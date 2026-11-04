# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from kdcube_cli import installer as installer_mod
from kdcube_cli.host_vault import HOST_VAULT_ACTIVATION_MARKER
from kdcube_cli.secrets_migration import (
    FileSecretInventory,
    HostVaultDestination,
    HostVaultStageError,
    load_file_secret_inventory,
    stage_file_secrets,
)

CONSUMER_SERVICES = ("chat-ingress", "chat-proc")
_ACTIVE_PROVIDER = "secrets-service"
_FILE_PROVIDER = "secrets-file"
_BROKER_URL = "http://kdcube-secrets:7777"


class HostVaultActivationError(RuntimeError):
    def __init__(self, message: str, *, code: str, rollback: str = "not_needed") -> None:
        super().__init__(message)
        self.code = code
        self.rollback = rollback


@dataclass(frozen=True)
class HostVaultActivationResult:
    dry_run: bool
    activated: bool
    provider_before: str
    provider_after: str
    discovered: int
    verified_consumers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "kdcube_cli.host_vault_activation.v1",
            "status": "ready" if self.dry_run else "ok",
            "dry_run": self.dry_run,
            "activated": self.activated,
            "provider_before": self.provider_before,
            "provider_after": self.provider_after,
            "discovered": self.discovered,
            "verified_consumers": list(self.verified_consumers),
            "plaintext_source_retained": True,
            "source_deleted": False,
        }


@dataclass(frozen=True)
class HostVaultRecoveryResult:
    recovered: bool
    provider_after: str
    verified_consumers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "kdcube_cli.host_vault_recovery.v1",
            "status": "ok",
            "recovered": self.recovered,
            "provider_after": self.provider_after,
            "verified_consumers": list(self.verified_consumers),
            "pending_activation": False,
            "plaintext_source_retained": True,
        }


class HostVaultActivationRuntime(Protocol):
    def require_running(self) -> None: ...

    def quiesce_consumers(self) -> None: ...

    def recreate_secret_path(self) -> None: ...

    def verify_consumer(self, service: str, *, key: str, digest: str) -> None: ...


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    payload: bytes
    mode: int

    @classmethod
    def capture(cls, path: Path) -> _FileSnapshot:
        try:
            stat = path.stat()
        except OSError as exc:
            raise HostVaultActivationError(
                "Host-vault activation requires complete runtime configuration files.",
                code="invalid_runtime_configuration",
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise HostVaultActivationError(
                "Host-vault activation requires regular runtime configuration files.",
                code="invalid_runtime_configuration",
            )
        return cls(path=path, payload=path.read_bytes(), mode=stat.st_mode & 0o777)

    def restore(self) -> None:
        _atomic_write(self.path, self.payload, mode=self.mode)


def _atomic_write(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_mode = mode
    if current_mode is None and path.exists():
        current_mode = path.stat().st_mode & 0o777
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        if current_mode is not None and os.name == "posix":
            os.fchmod(fd, current_mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _activation_marker(config_dir: Path) -> Path:
    return config_dir / HOST_VAULT_ACTIVATION_MARKER


def _marker_payload(phase: str) -> bytes:
    return (
        json.dumps(
            {
                "schema": "kdcube_cli.host_vault_activation_transaction.v1",
                "operation": "activate",
                "phase": phase,
                "recovery": "secrets-file-ephemeral",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _create_activation_marker(config_dir: Path) -> None:
    marker = _activation_marker(config_dir)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(marker, flags, 0o600)
    except FileExistsError as exc:
        raise HostVaultActivationError(
            "A host-vault activation recovery is already pending; run "
            "`kdcube secrets backend host-vault recover --yes`.",
            code="activation_recovery_required",
        ) from exc
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not create its recovery marker.",
            code="activation_marker_failed",
        ) from exc
    try:
        payload = _marker_payload("prepared")
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(config_dir)
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not persist its recovery marker.",
            code="activation_marker_failed",
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _set_activation_phase(config_dir: Path, phase: str) -> None:
    marker = _activation_marker(config_dir)
    try:
        metadata = marker.lstat()
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation lost its recovery marker.",
            code="activation_marker_failed",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise HostVaultActivationError(
            "Host-vault activation recovery marker is not a regular file.",
            code="activation_marker_invalid",
        )
    _atomic_write(marker, _marker_payload(phase), mode=0o600)


def _clear_activation_marker(config_dir: Path) -> None:
    marker = _activation_marker(config_dir)
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not inspect its recovery marker.",
            code="activation_marker_failed",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise HostVaultActivationError(
            "Host-vault activation recovery marker is not a regular file.",
            code="activation_marker_invalid",
        )
    try:
        marker.unlink()
        _sync_directory(config_dir)
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not clear its recovery marker.",
            code="activation_marker_failed",
        ) from exc


def _assembly(config_dir: Path) -> dict[str, object]:
    path = config_dir / "assembly.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not read assembly.yaml.",
            code="invalid_runtime_configuration",
        ) from exc
    if not isinstance(value, dict):
        raise HostVaultActivationError(
            "Host-vault activation requires assembly.yaml to contain a mapping.",
            code="invalid_runtime_configuration",
        )
    return value


def _provider(assembly: Mapping[str, object]) -> str:
    secrets = assembly.get("secrets")
    if not isinstance(secrets, Mapping):
        return ""
    return installer_mod.normalize_secrets_provider(
        secrets.get("provider"),
        default="",
    )


def _set_assembly_provider(config_dir: Path, provider: str) -> None:
    path = config_dir / "assembly.yaml"
    value = _assembly(config_dir)
    secrets = value.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        raise HostVaultActivationError(
            "Host-vault activation requires assembly.secrets to contain a mapping.",
            code="invalid_runtime_configuration",
        )
    secrets["provider"] = provider
    from kdcube_cli.host_vault import materialize_service_url

    materialize_service_url(value)
    payload = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")
    _atomic_write(path, payload)


def _set_host_vault_backend(config_dir: Path, backend: str) -> None:
    path = config_dir / "assembly.yaml"
    value = _assembly(config_dir)
    secrets = value.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        raise HostVaultActivationError(
            "Host-vault recovery requires assembly.secrets to contain a mapping.",
            code="invalid_runtime_configuration",
        )
    service = secrets.setdefault("service", {})
    if not isinstance(service, dict):
        raise HostVaultActivationError(
            "Host-vault recovery requires assembly.secrets.service to contain a mapping.",
            code="invalid_runtime_configuration",
        )
    secrets["provider"] = _FILE_PROVIDER
    service["backend"] = backend
    payload = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")
    _atomic_write(path, payload)


def _update_env(path: Path, updates: Mapping[str, str], *, remove_keys: tuple[str, ...] = ()) -> None:
    try:
        env = installer_mod.load_env_file(path)
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not read a generated runtime environment file.",
            code="invalid_runtime_configuration",
        ) from exc
    for key, value in updates.items():
        installer_mod.update_env_value(env, key, value)
    for key in remove_keys:
        installer_mod.remove_env_key(env, key)
    payload = ("\n".join(env.lines).rstrip() + "\n").encode("utf-8")
    _atomic_write(path, payload)


def _apply_active_configuration(config_dir: Path) -> None:
    _set_assembly_provider(config_dir, _ACTIVE_PROVIDER)
    for name in (".env.ingress", ".env.proc"):
        _update_env(
            config_dir / name,
            {},
            remove_keys=("SECRETS_PROVIDER", "SECRETS_URL"),
        )


def _apply_ephemeral_file_recovery(config_dir: Path) -> None:
    _set_host_vault_backend(config_dir, "ephemeral")
    _update_env(
        config_dir / ".env",
        {"KDCUBE_SECRETS_SERVICE_BACKEND": "ephemeral"},
    )
    for name in (".env.ingress", ".env.proc"):
        _update_env(config_dir / name, {}, remove_keys=("SECRETS_PROVIDER", "SECRETS_URL"))


def _load_inventory(config_dir: Path) -> FileSecretInventory:
    try:
        return load_file_secret_inventory(
            config_dir,
            is_placeholder=installer_mod.is_placeholder,
        )
    except HostVaultStageError as exc:
        raise HostVaultActivationError(
            "Host-vault activation could not build the file-backed secret inventory.",
            code="invalid_source_inventory",
        ) from exc


def _require_destination_parity(
    inventory: FileSecretInventory,
    destination: HostVaultDestination,
) -> None:
    try:
        result = stage_file_secrets(inventory, destination, dry_run=True)
    except HostVaultStageError as exc:
        raise HostVaultActivationError(
            "Host-vault activation found a destination conflict; no runtime was changed.",
            code="destination_conflict",
        ) from exc
    if result.would_create:
        raise HostVaultActivationError(
            "Host-vault activation requires a complete shadow stage; run "
            "`kdcube secrets backend host-vault stage` first.",
            code="destination_incomplete",
        )


def _probe(inventory: FileSecretInventory) -> tuple[str, str]:
    candidates = [
        (key, value)
        for key, value in inventory.values.items()
        if not key.endswith(".__keys")
    ]
    if not candidates:
        raise HostVaultActivationError(
            "Host-vault activation requires at least one non-placeholder secret for "
            "end-to-end read verification.",
            code="empty_source_inventory",
        )
    key, value = min(candidates)
    return key, hashlib.sha256(value.encode("utf-8")).hexdigest()


def _restore_snapshots(snapshots: tuple[_FileSnapshot, ...]) -> None:
    for snapshot in snapshots:
        snapshot.restore()


def _verify_consumers(
    runtime: HostVaultActivationRuntime,
    inventory: FileSecretInventory,
) -> tuple[str, ...]:
    key, digest = _probe(inventory)
    verified: list[str] = []
    for service in CONSUMER_SERVICES:
        runtime.verify_consumer(service, key=key, digest=digest)
        verified.append(service)
    return tuple(verified)


def activate_host_vault(
    *,
    config_dir: Path,
    destination: HostVaultDestination,
    runtime: HostVaultActivationRuntime,
    dry_run: bool,
) -> HostVaultActivationResult:
    config_dir = Path(config_dir)
    assembly = _assembly(config_dir)
    provider = _provider(assembly)
    if provider == _ACTIVE_PROVIDER:
        return HostVaultActivationResult(
            dry_run=dry_run,
            activated=False,
            provider_before=_ACTIVE_PROVIDER,
            provider_after=_ACTIVE_PROVIDER,
            discovered=0,
            verified_consumers=(),
        )
    if provider != _FILE_PROVIDER:
        raise HostVaultActivationError(
            "Host-vault activation requires secrets.provider 'secrets-file'.",
            code="unsupported_source_provider",
        )

    runtime.require_running()
    initial_inventory = _load_inventory(config_dir)
    _probe(initial_inventory)
    _require_destination_parity(initial_inventory, destination)
    if dry_run:
        return HostVaultActivationResult(
            dry_run=True,
            activated=False,
            provider_before=_FILE_PROVIDER,
            provider_after=_FILE_PROVIDER,
            discovered=len(initial_inventory.values),
            verified_consumers=(),
        )

    snapshot_paths = (
        config_dir / "assembly.yaml",
        config_dir / ".env",
        config_dir / ".env.ingress",
        config_dir / ".env.proc",
    )
    snapshots = tuple(_FileSnapshot.capture(path) for path in snapshot_paths)
    quiesced = False
    frozen_inventory = initial_inventory
    _create_activation_marker(config_dir)
    try:
        quiesced = True
        runtime.quiesce_consumers()
        _set_activation_phase(config_dir, "quiesced")
        frozen_inventory = _load_inventory(config_dir)
        _require_destination_parity(frozen_inventory, destination)
        _apply_active_configuration(config_dir)
        _set_activation_phase(config_dir, "configured")
        runtime.recreate_secret_path()
        _set_activation_phase(config_dir, "runtime_recreated")
        verified = _verify_consumers(runtime, frozen_inventory)
        if _load_inventory(config_dir).values != frozen_inventory.values:
            raise HostVaultActivationError(
                "The file-backed inventory changed during activation.",
                code="source_changed",
            )
        _clear_activation_marker(config_dir)
    except Exception as exc:
        if not quiesced:
            if isinstance(exc, HostVaultActivationError):
                raise
            raise HostVaultActivationError(
                "Host-vault activation failed before the runtime was changed.",
                code="activation_failed",
            ) from exc

        try:
            _restore_snapshots(snapshots)
            runtime.recreate_secret_path()
            _verify_consumers(runtime, _load_inventory(config_dir))
            _clear_activation_marker(config_dir)
        except Exception as exact_rollback_exc:
            try:
                _apply_ephemeral_file_recovery(config_dir)
                runtime.recreate_secret_path()
                _verify_consumers(runtime, _load_inventory(config_dir))
                _clear_activation_marker(config_dir)
            except Exception as recovery_exc:
                raise HostVaultActivationError(
                    "Host-vault activation failed and automatic runtime recovery did not complete. "
                    "The plaintext source remains present; inspect the runtime before retrying.",
                    code="activation_and_recovery_failed",
                    rollback="failed",
                ) from recovery_exc
            raise HostVaultActivationError(
                "Host-vault activation failed. The runtime recovered with secrets-file and "
                "the ephemeral sidecar; host-vault shadow mode must be configured again.",
                code="activation_failed",
                rollback="secrets-file-ephemeral",
            ) from exact_rollback_exc
        raise HostVaultActivationError(
            "Host-vault activation failed. The prior secrets-file shadow configuration "
            "was restored and verified.",
            code="activation_failed",
            rollback="restored",
        ) from exc

    return HostVaultActivationResult(
        dry_run=False,
        activated=True,
        provider_before=_FILE_PROVIDER,
        provider_after=_ACTIVE_PROVIDER,
        discovered=len(frozen_inventory.values),
        verified_consumers=verified,
    )


def recover_host_vault_activation(
    *,
    config_dir: Path,
    runtime: HostVaultActivationRuntime,
) -> HostVaultRecoveryResult:
    config_dir = Path(config_dir)
    marker = _activation_marker(config_dir)
    if not marker.exists() and not marker.is_symlink():
        return HostVaultRecoveryResult(
            recovered=False,
            provider_after=_provider(_assembly(config_dir)),
            verified_consumers=(),
        )

    try:
        metadata = marker.lstat()
    except OSError as exc:
        raise HostVaultActivationError(
            "Host-vault recovery could not inspect the pending activation marker.",
            code="activation_marker_failed",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise HostVaultActivationError(
            "Host-vault recovery requires a regular pending activation marker.",
            code="activation_marker_invalid",
        )

    provider = _provider(_assembly(config_dir))
    if provider not in {_FILE_PROVIDER, _ACTIVE_PROVIDER}:
        raise HostVaultActivationError(
            "Host-vault recovery found an unsupported provider and changed nothing.",
            code="unsupported_recovery_provider",
        )
    inventory = _load_inventory(config_dir)
    _probe(inventory)
    try:
        _set_activation_phase(config_dir, "recovering")
        _apply_ephemeral_file_recovery(config_dir)
        runtime.recreate_secret_path()
        verified = _verify_consumers(runtime, inventory)
        _clear_activation_marker(config_dir)
    except Exception as exc:
        raise HostVaultActivationError(
            "Host-vault recovery did not complete. The plaintext source and pending "
            "marker remain; correct the runtime failure and retry recovery.",
            code="activation_recovery_failed",
            rollback="failed",
        ) from exc

    return HostVaultRecoveryResult(
        recovered=True,
        provider_after=_FILE_PROVIDER,
        verified_consumers=verified,
    )


class ComposeHostVaultActivationRuntime:
    def __init__(
        self,
        *,
        docker_dir: Path,
        env_file: Path,
        timeout_seconds: float = 120.0,
        poll_seconds: float = 1.0,
    ) -> None:
        self._docker_dir = Path(docker_dir)
        self._env_file = Path(env_file)
        self._timeout = timeout_seconds
        self._poll = poll_seconds

    def _base(self, env_file: Path | None = None) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(env_file or self._env_file),
        ]

    def _run(
        self,
        command: list[str],
        *,
        env_file: Path,
        stdin: str = "",
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=self._docker_dir,
                env=installer_mod.compose_env(env_file),
                input=stdin,
                text=True,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostVaultActivationError(
                "A Docker Compose operation required by host-vault activation could not run.",
                code="runtime_operation_failed",
            ) from exc

    def require_running(self) -> None:
        result = self._run(
            [*self._base(), "ps", "--services", "--status", "running"],
            env_file=self._env_file,
        )
        if result.returncode != 0:
            raise HostVaultActivationError(
                "Host-vault activation could not inspect the running deployment.",
                code="runtime_status_failed",
            )
        running = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        required = {"kdcube-secrets", *CONSUMER_SERVICES}
        if not required.issubset(running):
            raise HostVaultActivationError(
                "Host-vault activation requires kdcube-secrets, chat-ingress, and chat-proc "
                "to be running.",
                code="runtime_not_running",
            )

    def quiesce_consumers(self) -> None:
        result = self._run(
            [*self._base(), "stop", *CONSUMER_SERVICES],
            env_file=self._env_file,
        )
        if result.returncode != 0:
            raise HostVaultActivationError(
                "Host-vault activation could not quiesce the secret-consuming services.",
                code="runtime_quiesce_failed",
            )

    def _wait_for_broker(self, runtime_env: Path) -> None:
        deadline = time.monotonic() + self._timeout
        command = [
            *self._base(runtime_env),
            "exec",
            "-T",
            "kdcube-secrets",
            "python",
            "-c",
            (
                "import sys,urllib.request; "
                "r=urllib.request.urlopen('http://127.0.0.1:7777/health',timeout=2); "
                "sys.exit(0 if r.status==200 else 1)"
            ),
        ]
        while time.monotonic() < deadline:
            result = self._run(command, env_file=runtime_env)
            if result.returncode == 0:
                return
            time.sleep(self._poll)
        raise HostVaultActivationError(
            "The secrets broker did not become healthy during host-vault activation.",
            code="broker_not_ready",
        )

    def recreate_secret_path(self) -> None:
        runtime_env = installer_mod.write_env_overlay(
            self._env_file,
            installer_mod.generate_runtime_tokens(),
        )
        try:
            broker = self._run(
                [
                    *self._base(runtime_env),
                    "up",
                    "-d",
                    "--force-recreate",
                    "--no-deps",
                    "kdcube-secrets",
                ],
                env_file=runtime_env,
            )
            if broker.returncode != 0:
                raise HostVaultActivationError(
                    "The secrets broker could not be recreated during host-vault activation.",
                    code="broker_recreate_failed",
                )
            self._wait_for_broker(runtime_env)
            consumers = self._run(
                [
                    *self._base(runtime_env),
                    "up",
                    "-d",
                    "--force-recreate",
                    "--no-deps",
                    *CONSUMER_SERVICES,
                ],
                env_file=runtime_env,
            )
            if consumers.returncode != 0:
                raise HostVaultActivationError(
                    "The secret-consuming services could not be recreated during activation.",
                    code="consumer_recreate_failed",
                )
        finally:
            runtime_env.unlink(missing_ok=True)

    def verify_consumer(self, service: str, *, key: str, digest: str) -> None:
        if service not in CONSUMER_SERVICES:
            raise HostVaultActivationError(
                "Host-vault activation received an unsupported consumer probe.",
                code="invalid_consumer_probe",
            )
        script = (
            "import asyncio,hashlib,hmac,json,sys; "
            "from kdcube_ai_app.infra.secrets.manager import get_secrets_manager; "
            "p=json.loads(sys.stdin.read()); "
            "v=asyncio.run(get_secrets_manager().get_secret(p['key'])); "
            "d=hashlib.sha256(v.encode('utf-8')).hexdigest() if v is not None else ''; "
            "sys.exit(0 if hmac.compare_digest(d,p['digest']) else 3)"
        )
        payload = json.dumps({"key": key, "digest": digest}, separators=(",", ":"))
        deadline = time.monotonic() + self._timeout
        command = [
            *self._base(),
            "exec",
            "-T",
            service,
            "python",
            "-c",
            script,
        ]
        while time.monotonic() < deadline:
            result = self._run(command, env_file=self._env_file, stdin=payload)
            if result.returncode == 0:
                return
            time.sleep(self._poll)
        raise HostVaultActivationError(
            "A secret-consuming service could not verify the activated provider.",
            code="consumer_verification_failed",
        )


__all__ = [
    "CONSUMER_SERVICES",
    "ComposeHostVaultActivationRuntime",
    "HostVaultActivationError",
    "HostVaultActivationResult",
    "HostVaultRecoveryResult",
    "activate_host_vault",
    "recover_host_vault_activation",
]

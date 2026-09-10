# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Set

from kdcube_cli import installer as installer_mod
from kdcube_cli.compose_profiles import (
    PROXYLOGIN_PROFILE,
    PROXYLOGIN_SERVICE,
    ComposeProfileConfigurationError,
    compose_profile_args,
    proxy_login_enabled,
)
from kdcube_cli.control.errors import DockerUnavailableError, OperationFailedError
from kdcube_cli.control.execution import CommandResult, CommandRunner
from kdcube_cli.control.initialization import EventSink
from kdcube_cli.control.local_runtime import (
    DOCKER_STATUS_TIMEOUT_SECONDS,
    LocalRuntimeContext,
    compose_environment,
    local_ui_url,
    logs_dir,
    namespace_key,
    read_json,
    same_path,
)
from kdcube_cli.control.models import (
    ControlEvent,
    ControlEventKind,
    DeploymentTargetRef,
    LocalStartRequest,
    LocalStopRequest,
    OperationResult,
)
from kdcube_cli.host_vault import (
    HostVaultConfigurationError,
    validate_assembly_for_start as validate_host_vault_assembly_for_start,
)


class LocalLifecycleController:
    def __init__(
        self,
        reference: DeploymentTargetRef,
        context: LocalRuntimeContext,
        *,
        runner: CommandRunner,
        lock_file: Path,
        stream_process_output: bool,
    ) -> None:
        self._reference = reference
        self._context = context
        self._runner = runner
        self._lock_file = Path(lock_file).expanduser().resolve()
        self._stream_process_output = bool(stream_process_output)

    def start(
        self,
        request: LocalStartRequest,
        *,
        event_sink: Optional[EventSink],
    ) -> OperationResult:
        env_file = self._context.config_dir / ".env"
        if not env_file.exists():
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                f"Compose env file not found: {env_file}.\n"
                "Initialize the workdir first:\n  kdcube init",
            )
        self.ensure_docker_responsive()
        self._check_before_start()
        assembly = installer_mod.load_release_descriptor_soft(
            self._context.config_dir / "assembly.yaml"
        )
        try:
            profile_args = compose_profile_args(assembly)
            use_proxy_login = proxy_login_enabled(assembly)
        except ComposeProfileConfigurationError as exc:
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                f"Compose profile configuration is invalid: {exc}",
            ) from exc
        try:
            validate_host_vault_assembly_for_start(
                assembly,
                workdir=self._context.workdir,
            )
        except HostVaultConfigurationError as exc:
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                f"Host-vault startup preflight failed: {exc}",
            ) from exc
        env_main = installer_mod.load_env_file(env_file)
        installer_mod.ensure_compose_log_dirs(logs_dir(env_main, self._context.workdir))
        runtime_env = installer_mod.write_env_overlay(
            env_file,
            installer_mod.generate_runtime_tokens(),
        )
        base_command = [
            "docker",
            "compose",
            "--env-file",
            str(runtime_env),
        ]
        command = [*base_command, *profile_args, "up", "-d"]
        if request.build:
            command.append("--build")
        try:
            self._write_lock(env_file)
            if not use_proxy_login:
                self._remove_running_proxy_login(
                    base_command,
                    runtime_env,
                    event_sink=event_sink,
                )
            self._emit_command(event_sink, command)
            result = self._run_command(
                command,
                cwd=self._context.docker_dir,
                env=compose_environment(runtime_env),
                capture_output=not self._stream_process_output,
            )
            if result.returncode != 0:
                raise OperationFailedError(
                    "start",
                    self._reference.target_id,
                    f"Command failed with exit code {result.returncode}.",
                    returncode=result.returncode,
                )
        finally:
            runtime_env.unlink(missing_ok=True)
        return OperationResult(
            target=self._reference,
            operation="start",
            changed=True,
            running=True,
            url=local_ui_url(env_main),
        )

    def stop(
        self,
        request: LocalStopRequest,
        *,
        event_sink: Optional[EventSink],
    ) -> OperationResult:
        env_file = self._context.config_dir / ".env"
        if not env_file.exists():
            raise OperationFailedError(
                "stop",
                self._reference.target_id,
                f"Compose env file not found: {env_file}. "
                "Pass --workdir for the runtime you want to stop or re-run "
                "the installer first.",
            )
        self.ensure_docker_responsive()
        self._check_before_stop()
        command = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "--profile",
            PROXYLOGIN_PROFILE,
            "down",
            "--remove-orphans",
        ]
        if request.remove_volumes:
            command.append("-v")
        self._emit_command(event_sink, command)
        result = self._run_command(
            command,
            cwd=self._context.docker_dir,
            env=compose_environment(env_file),
            capture_output=not self._stream_process_output,
        )
        if result.returncode != 0:
            raise OperationFailedError(
                "stop",
                self._reference.target_id,
                f"Command failed with exit code {result.returncode}.",
                returncode=result.returncode,
            )
        self._clear_lock()
        return OperationResult(
            target=self._reference,
            operation="stop",
            changed=True,
            running=False,
        )

    def _remove_running_proxy_login(
        self,
        base_command: Sequence[str],
        runtime_env: Path,
        *,
        event_sink: Optional[EventSink],
    ) -> None:
        status_command = [
            *base_command,
            "--profile",
            PROXYLOGIN_PROFILE,
            "ps",
            "--services",
            "--filter",
            "status=running",
        ]
        status = self._run_command(
            status_command,
            cwd=self._context.docker_dir,
            env=compose_environment(runtime_env),
            capture_output=True,
            timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
        )
        if status.returncode != 0:
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                "Docker compose could not inspect optional services.",
                returncode=status.returncode,
            )
        running = {line.strip() for line in status.stdout.splitlines() if line.strip()}
        if PROXYLOGIN_SERVICE not in running:
            return

        remove_command = [
            *base_command,
            "--profile",
            PROXYLOGIN_PROFILE,
            "rm",
            "--stop",
            "--force",
            PROXYLOGIN_SERVICE,
        ]
        self._emit_command(event_sink, remove_command)
        removed = self._run_command(
            remove_command,
            cwd=self._context.docker_dir,
            env=compose_environment(runtime_env),
            capture_output=not self._stream_process_output,
        )
        if removed.returncode != 0:
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                "Docker compose could not stop the disabled proxy-login service.",
                returncode=removed.returncode,
            )

    def running_services(self) -> Set[str]:
        env_file = self._context.config_dir / ".env"
        command = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "ps",
            "--services",
            "--filter",
            "status=running",
        ]
        result = self._run_command(
            command,
            cwd=self._context.docker_dir,
            env=compose_environment(env_file),
            capture_output=True,
            timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise OperationFailedError(
                "status",
                self._reference.target_id,
                "Docker compose status failed.",
                returncode=result.returncode,
            )
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def ensure_docker_responsive(self) -> None:
        command = ["docker", "info", "--format", "{{.ServerVersion}}"]
        try:
            result = self._runner.run(
                command,
                timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise DockerUnavailableError(
                "Docker not found. Please install Docker and retry."
            ) from exc
        except OSError as exc:
            raise DockerUnavailableError(
                "Docker could not be executed. Verify that it is installed and accessible."
            ) from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise DockerUnavailableError(
                "Docker Desktop or the Docker daemon is not responding."
            ) from exc
        if result.returncode != 0:
            raise DockerUnavailableError("Docker Desktop or the Docker daemon is not responding.")

    def _run_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        capture_output: bool,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        try:
            return self._runner.run(
                command,
                cwd=cwd,
                env=env,
                timeout=timeout,
                capture_output=capture_output,
            )
        except FileNotFoundError as exc:
            raise DockerUnavailableError(
                "Docker not found. Please install Docker and retry."
            ) from exc
        except OSError as exc:
            raise DockerUnavailableError(
                "Docker could not be executed. Verify that it is installed and accessible."
            ) from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise DockerUnavailableError("Docker timed out and is not responding.") from exc

    def _read_lock(self) -> Optional[Dict[str, object]]:
        return read_json(self._lock_file)

    def _write_lock(self, env_file: Path) -> None:
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file.write_text(
            json.dumps(
                {
                    "tenant": self._reference.tenant or "",
                    "project": self._reference.project or "",
                    "workdir": str(self._context.workdir),
                    "docker_dir": str(self._context.docker_dir),
                    "env_file": str(env_file),
                },
                indent=2,
            )
        )

    def _clear_lock(self) -> None:
        self._lock_file.unlink(missing_ok=True)

    def _lock_matches_target(self, lock: Mapping[str, object]) -> bool:
        if same_path(lock.get("workdir"), self._reference.workdir):
            return True
        return (
            namespace_key(lock.get("tenant")) == namespace_key(self._reference.tenant)
            and namespace_key(lock.get("project")) == namespace_key(self._reference.project)
        )

    def _lock_running_services(self, lock: Mapping[str, object]) -> Set[str]:
        docker_dir = Path(str(lock.get("docker_dir") or "")).expanduser()
        env_file = Path(str(lock.get("env_file") or "")).expanduser()
        if not docker_dir.exists() or not env_file.exists():
            return set()
        command = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "ps",
            "--services",
            "--filter",
            "status=running",
        ]
        try:
            result = self._runner.run(
                command,
                cwd=docker_dir,
                env=compose_environment(env_file),
                timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
                capture_output=True,
            )
        except Exception:
            return set()
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _check_before_start(self) -> None:
        lock = self._read_lock()
        if lock is None or self._lock_matches_target(lock):
            return
        running = self._lock_running_services(lock)
        if running:
            lock_tenant = str(lock.get("tenant") or "").strip()
            lock_project = str(lock.get("project") or "").strip()
            lock_workdir = str(lock.get("workdir") or "?")
            raise OperationFailedError(
                "start",
                self._reference.target_id,
                "Another local KDCube deployment is already running.\n"
                f"  Tenant  : {lock_tenant}\n"
                f"  Project : {lock_project}\n"
                f"  Workdir : {lock_workdir}\n"
                f"  Services: {', '.join(sorted(running))}\n\n"
                "Stop it first, then retry:\n"
                f"  kdcube stop --workdir {lock_workdir}",
            )
        self._clear_lock()

    def _check_before_stop(self) -> None:
        running = self.running_services()
        if not running:
            raise OperationFailedError(
                "stop",
                self._reference.target_id,
                f"Deployment is not running.\n  Workdir: {self._context.workdir}",
            )
        lock = self._read_lock()
        if lock is None or self._lock_matches_target(lock):
            return
        lock_tenant = str(lock.get("tenant") or "").strip()
        lock_project = str(lock.get("project") or "").strip()
        lock_workdir = str(lock.get("workdir") or "?")
        raise OperationFailedError(
            "stop",
            self._reference.target_id,
            "Cannot stop: a different deployment is currently running.\n"
            f"  Running  : {lock_tenant} / {lock_project}  ({lock_workdir})\n"
            f"  Requested: {self._reference.tenant or ''} / "
            f"{self._reference.project or ''}  ({self._context.workdir})\n\n"
            "Stop the running deployment first:\n"
            f"  kdcube stop --workdir {lock_workdir}",
        )

    @staticmethod
    def _emit_command(event_sink: Optional[EventSink], command: Sequence[str]) -> None:
        if event_sink is not None:
            event_sink(
                ControlEvent(
                    kind=ControlEventKind.COMMAND,
                    message="$ " + " ".join(command),
                )
            )

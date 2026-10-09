# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

_RUNTIME_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_CREDENTIAL_DIGEST = re.compile(r"[0-9a-f]{64}")
# W673: the platform's own runtime-record purposes, enrolled when secrets.runtime is absent. kdcube-cli is a
# standalone distribution, so this mirrors kdcube_ai_app.infra.secrets.runtime_contract.DEFAULT_RUNTIME_NAMESPACES;
# a consumer-contract test keeps the two equal.
DEFAULT_RUNTIME_NAMESPACES = ("login-attempts", "card-credentials", "oauth-refresh-tokens")
RUNTIME_SCOPE_SCHEMA = "kdcube.runtime_secret_scopes.v1"

EPHEMERAL_BACKEND = "ephemeral"
HOST_VAULT_BACKEND = "host-vault"
DEPLOYMENT_SECRET_APPLICATION = "kdcube-runtime"
HOST_VAULT_ACTIVATION_MARKER = ".host-vault-activation.pending.json"
IDENTITY_FILENAMES = (
    "host-vault-client.crt",
    "host-vault-client.key",
    "host-vault-ca.crt",
)


class HostVaultConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HostVaultRuntimeConfig:
    provider: str
    backend: str
    tenant: str
    project: str
    address: str = ""
    server_name: str = ""
    identity_dir: Path | None = None
    exec_network_mode: str = ""
    runtime_root: str = ""
    runtime_namespaces: tuple[str, ...] = ()
    runtime_scope_policy: str = ""
    # W673: True when secrets.runtime is absent; the per-run token overlay then grants the default
    # namespaces to this deployment's own proc reader/writer credentials only (default_runtime_scope_policy).
    runtime_defaulted: bool = False

    @property
    def enabled(self) -> bool:
        return self.backend == HOST_VAULT_BACKEND

    @property
    def identity_paths(self) -> tuple[Path, Path, Path]:
        if self.identity_dir is None:
            raise HostVaultConfigurationError(
                "secrets.service.host_vault.identity_dir is required"
            )
        return (
            self.identity_dir / IDENTITY_FILENAMES[0],
            self.identity_dir / IDENTITY_FILENAMES[1],
            self.identity_dir / IDENTITY_FILENAMES[2],
        )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _provider_name(value: object) -> str:
    provider = _text(value).lower().replace("_", "-")
    if provider in {"local", "service", "sidecar", "secrets-service"}:
        return "secrets-service"
    if provider in {"file", "yaml", "yaml-file", "secrets-file"}:
        return "secrets-file"
    if provider in {"aws", "aws-sm", "awssm"}:
        return "aws-sm"
    if provider in {"memory", "in-memory", "inmemory"}:
        return "in-memory"
    return provider


def _backend_name(value: object) -> str:
    backend = _text(value).lower().replace("_", "-") or EPHEMERAL_BACKEND
    if backend in {"memory", "transient", "ephemeral-memory"}:
        return EPHEMERAL_BACKEND
    return backend


def _validate_address(address: str) -> None:
    if "://" in address:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.address must be host:port without a URL scheme"
        )
    try:
        parsed = urlsplit(f"//{address}")
        port = parsed.port
    except ValueError as exc:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.address must contain a valid port"
        ) from exc
    if not parsed.hostname or port is None or parsed.username or parsed.password:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.address must be host:port; bracket IPv6 literals"
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.address must not contain a path, query, or fragment"
        )


def _runtime_configuration(secrets: Mapping[str, object]) -> dict[str, object]:
    # kdcube-cli is a standalone distribution. Validate the producer's closed
    # projection shape here, without importing the runtime SDK or authorizing
    # credentials. Consumer-contract tests keep the two boundaries aligned.
    runtime = secrets.get("runtime")
    if runtime is None:
        # W673: no runtime configuration enrolls the platform's own purposes, granted per run.
        return {"runtime_namespaces": DEFAULT_RUNTIME_NAMESPACES, "runtime_defaulted": True}
    if not isinstance(runtime, Mapping) or set(runtime) - {"root", "namespaces", "scope_policy"}:
        raise HostVaultConfigurationError("secrets.runtime must contain only root, namespaces and scope_policy")
    root = runtime.get("root", "")
    try:
        invalid_root = type(root) is not str or len(root.encode("utf-8")) > 4096
    except UnicodeError:
        invalid_root = True
    if (invalid_root or (root and (
            not Path(root).is_absolute() or Path(root) == Path("/")
            or root != root.strip() or any(value in root for value in ("\n", "\r", "\0", "$"))
            or ".." in Path(root).parts))):
        raise HostVaultConfigurationError("secrets.runtime.root must be a dedicated absolute path")
    # W673 (Main, 2026-10-09): omitted (or null) namespaces enroll the platform's own purposes, as the SDK
    # does (runtime_contract.runtime_section_namespaces); an explicit list, [] included, is exact.
    namespaces_defaulted = runtime.get("namespaces") is None
    namespaces = list(DEFAULT_RUNTIME_NAMESPACES) if namespaces_defaulted else runtime.get("namespaces")
    if (type(namespaces) is not list or len(namespaces) > 64
            or any(type(value) is not str or _RUNTIME_NAMESPACE.fullmatch(value) is None
                   for value in namespaces)
            or len(set(namespaces)) != len(namespaces)):
        raise HostVaultConfigurationError("secrets.runtime.namespaces must be unique exact namespace names")
    raw_policy = ""
    if "scope_policy" in runtime:
        try:
            raw_policy = json.dumps(runtime["scope_policy"], sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise HostVaultConfigurationError("secrets.runtime.scope_policy is invalid") from None
        policy = runtime["scope_policy"]
        if not _valid_runtime_policy(policy, namespaces=namespaces, encoded=raw_policy):
            raise HostVaultConfigurationError("secrets.runtime.scope_policy must grant only declared namespaces")
    # The per-run own-credential grant applies only to defaulted namespaces without a declared policy;
    # declared namespaces without a policy stay closed, and a declared policy is projected as it is.
    return {"runtime_root": root, "runtime_namespaces": tuple(namespaces),
            "runtime_scope_policy": raw_policy,
            "runtime_defaulted": namespaces_defaulted and "scope_policy" not in runtime}


def _valid_runtime_policy(policy: object, *, namespaces: list[str], encoded: str) -> bool:
    if (type(policy) is not dict or set(policy) != {"schema", "read", "write"}
            or policy["schema"] != "kdcube.runtime_secret_scopes.v1"
            or len(encoded.encode("utf-8")) > 65536):
        return False
    for kind in ("read", "write"):
        grants = policy[kind]
        if type(grants) is not dict or len(grants) > 64:
            return False
        for digest, values in grants.items():
            if (type(digest) is not str or _CREDENTIAL_DIGEST.fullmatch(digest) is None
                    or type(values) is not list or len(values) > 64
                    or any(type(value) is not str or value not in namespaces for value in values)):
                return False
    return True


def config_from_assembly(assembly: Mapping[str, object]) -> HostVaultRuntimeConfig:
    secrets = _mapping(assembly.get("secrets"))
    service = _mapping(secrets.get("service"))
    vault = _mapping(service.get("host_vault"))
    context = _mapping(assembly.get("context"))
    platform = _mapping(assembly.get("platform"))
    services = _mapping(platform.get("services"))
    proc = _mapping(services.get("proc"))
    exec_config = _mapping(proc.get("exec"))

    provider = _provider_name(secrets.get("provider"))
    backend = _backend_name(service.get("backend"))
    identity_text = _text(vault.get("identity_dir"))
    identity_dir = Path(identity_text).expanduser() if identity_text else None
    if identity_dir is not None and identity_dir.is_absolute():
        identity_dir = identity_dir.resolve()
    config = HostVaultRuntimeConfig(
        provider=provider,
        backend=backend,
        tenant=_text(context.get("tenant")),
        project=_text(context.get("project")),
        address=_text(vault.get("address")),
        server_name=_text(vault.get("server_name")),
        identity_dir=identity_dir,
        exec_network_mode=_text(exec_config.get("py_code_exec_network_mode")),
        **_runtime_configuration(secrets),
    )
    validate_configuration(config, check_identity=False)
    return config


def validate_configuration(
    config: HostVaultRuntimeConfig,
    *,
    check_identity: bool,
    workdir: Path | None = None,
) -> None:
    if config.backend not in {EPHEMERAL_BACKEND, HOST_VAULT_BACKEND}:
        raise HostVaultConfigurationError(
            "secrets.service.backend must be 'ephemeral' or 'host-vault'"
        )
    if not config.enabled:
        return
    if config.provider not in {"secrets-file", "secrets-service"}:
        raise HostVaultConfigurationError(
            "secrets.service.backend 'host-vault' requires secrets.provider "
            "'secrets-file' for shadow staging or 'secrets-service' for active use"
        )
    if not config.tenant or not config.project:
        raise HostVaultConfigurationError(
            "context.tenant and context.project are required for the host-vault backend"
        )
    if not config.address:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.address is required"
        )
    _validate_address(config.address)
    if not config.server_name:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.server_name is required"
        )
    if config.identity_dir is None or not config.identity_dir.is_absolute():
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.identity_dir must be an absolute host path"
        )
    if config.exec_network_mode.lower() != "auto":
        raise HostVaultConfigurationError(
            "the local host-vault backend requires "
            "platform.services.proc.exec.py_code_exec_network_mode 'auto'"
        )
    if workdir is not None:
        runtime_root = Path(workdir).expanduser().resolve()
        if (
            config.identity_dir == runtime_root
            or runtime_root in config.identity_dir.parents
        ):
            raise HostVaultConfigurationError(
                "the host-vault deployment identity must be stored outside the KDCube workdir"
            )
    if not check_identity:
        return
    for path in config.identity_paths:
        if path.is_symlink() or not path.is_file():
            raise HostVaultConfigurationError(
                f"host-vault deployment identity is incomplete: {path.name} is missing"
            )
    key_path = config.identity_paths[1]
    if os.name == "posix" and key_path.stat().st_mode & 0o077:
        raise HostVaultConfigurationError(
            "host-vault-client.key must not be accessible by group or other users"
        )


def compose_environment(config: HostVaultRuntimeConfig) -> dict[str, str]:
    values = {
        "KDCUBE_SECRETS_SERVICE_BACKEND": config.backend,
        "KDCUBE_HOST_VAULT_ADDR": "",
        "KDCUBE_HOST_VAULT_SERVER_NAME": "",
        "KDCUBE_SECRETS_TENANT": "",
        "KDCUBE_SECRETS_PROJECT": "",
        "HOST_KDCUBE_HOST_VAULT_CLIENT_CERT_PATH": "",
        "HOST_KDCUBE_HOST_VAULT_CLIENT_KEY_PATH": "",
        "HOST_KDCUBE_HOST_VAULT_CA_PATH": "",
        "KDCUBE_SECRETS_RUNTIME_ROOT": config.runtime_root,
        "HOST_KDCUBE_RUNTIME_SECRETS_ROOT": config.runtime_root,
        "KDCUBE_SECRETS_RUNTIME_NAMESPACES": json.dumps(list(config.runtime_namespaces), separators=(",", ":")),
        "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY": config.runtime_scope_policy,
        "KDCUBE_SECRETS_RUNTIME_DEFAULTED": "1" if config.runtime_defaulted else "",
    }
    if not config.enabled:
        return values
    cert_path, key_path, ca_path = config.identity_paths
    values.update(
        {
            "KDCUBE_HOST_VAULT_ADDR": config.address,
            "KDCUBE_HOST_VAULT_SERVER_NAME": config.server_name,
            "KDCUBE_SECRETS_TENANT": config.tenant,
            "KDCUBE_SECRETS_PROJECT": config.project,
            "HOST_KDCUBE_HOST_VAULT_CLIENT_CERT_PATH": str(cert_path),
            "HOST_KDCUBE_HOST_VAULT_CLIENT_KEY_PATH": str(key_path),
            "HOST_KDCUBE_HOST_VAULT_CA_PATH": str(ca_path),
        }
    )
    return values


CONTAINER_CONFIG_DIR = PurePosixPath("/config")
DEFAULT_CONTAINER_RUNTIME_ROOT = CONTAINER_CONFIG_DIR / "secrets"


def runtime_compose_environment(assembly: Mapping[str, object], *, host_config_dir: str | Path) -> dict[str, str]:
    """W670 K2: the runtime-secrets folder for Compose, on every provider.

    Operator, 2026-10-09: the folder lives "in config folder. make the folder secrets". The container root is
    ``secrets.runtime.root`` or, when absent, ``/config/secrets``; it must lie inside the container config
    folder ``/config``. The host root is the matching folder under the host config folder (the descriptor's
    location), never the container path. Processors already mount ``/config``; only the secrets service
    binds the host root, at the container root.
    """
    runtime = _runtime_configuration(_mapping(assembly.get("secrets")))
    container_root = PurePosixPath(str(runtime.get("runtime_root") or DEFAULT_CONTAINER_RUNTIME_ROOT))
    try:
        relative = container_root.relative_to(CONTAINER_CONFIG_DIR)
    except ValueError:
        relative = None
    if relative is None or not relative.parts:
        raise HostVaultConfigurationError("secrets.runtime.root must be a folder inside the config folder /config")
    host_config = Path(host_config_dir).expanduser()
    if not host_config.is_absolute():
        raise HostVaultConfigurationError("the host config folder must be an absolute path")
    return {
        "KDCUBE_SECRETS_RUNTIME_ROOT": str(container_root),
        "HOST_KDCUBE_RUNTIME_SECRETS_ROOT": str(host_config.joinpath(*relative.parts)),
        "KDCUBE_SECRETS_RUNTIME_NAMESPACES": json.dumps(list(runtime.get("runtime_namespaces") or ()),
                                                        separators=(",", ":")),
        "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY": str(runtime.get("runtime_scope_policy") or ""),
    }


def validate_assembly_for_start(
    assembly: Mapping[str, object],
    *,
    workdir: Path,
) -> HostVaultRuntimeConfig:
    marker = Path(workdir).expanduser().resolve() / "config" / HOST_VAULT_ACTIVATION_MARKER
    if marker.exists() or marker.is_symlink():
        raise HostVaultConfigurationError(
            "an interrupted host-vault activation is pending; run "
            "`kdcube secrets backend host-vault recover --yes` before starting "
            "the runtime"
        )
    config = config_from_assembly(assembly)
    validate_configuration(config, check_identity=True, workdir=workdir)
    return config


__all__ = [
    "DEPLOYMENT_SECRET_APPLICATION",
    "EPHEMERAL_BACKEND",
    "HOST_VAULT_ACTIVATION_MARKER",
    "HOST_VAULT_BACKEND",
    "HostVaultConfigurationError",
    "HostVaultRuntimeConfig",
    "compose_environment",
    "config_from_assembly",
    "validate_assembly_for_start",
    "validate_configuration",
]


def default_runtime_scope_policy(*, read_token: str, write_token: str, namespaces: tuple[str, ...]) -> str:
    """W673: the scope policy for a deployment without secrets.runtime (Infra contract, 2026-10-09).

    Exactly the given namespaces, read for this deployment's own proc read credential and write for its own
    writer credential, each identified by SHA-256 only. No wildcard, no other credential (ingress gets
    nothing). An empty or unusable token or namespace list grants nothing.
    """
    if (type(read_token) is not str or not read_token or type(write_token) is not str or not write_token
            or not namespaces or any(type(value) is not str or _RUNTIME_NAMESPACE.fullmatch(value) is None
                                     for value in namespaces)):
        return ""
    import hashlib
    granted = list(namespaces)
    policy = {"schema": RUNTIME_SCOPE_SCHEMA,
              "read": {hashlib.sha256(read_token.encode("utf-8")).hexdigest(): granted},
              "write": {hashlib.sha256(write_token.encode("utf-8")).hexdigest(): granted}}
    return json.dumps(policy, sort_keys=True, separators=(",", ":"))

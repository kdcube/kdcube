# SPDX-License-Identifier: MIT
"""Keep a configured host vault reachable before the runtime starts.

``secrets.service.backend: host-vault`` makes the vault a startup dependency:
the ``kdcube-secrets`` broker reports unhealthy while the vault is unreachable,
and Compose then leaves every service that waits on the broker in ``Created``
with no error. A configured dependency is therefore ensured here, before
Compose runs, by every command that starts the runtime.

Two cases, decided by the descriptor:

- ``secrets.service.host_vault.local_service`` is declared. The vault is a
  source-operated process on this host and the CLI owns keeping it up: start it
  when nothing listens, leave it alone when something does.
- It is not declared. The vault belongs to another owner (a service account, a
  second machine). The CLI only probes it and fails with the named cause.

The vault is left running by ``kdcube stop``. It is durable storage that
outlives the Compose stack, and a second local deployment may share it.

Nothing here runs unless the descriptor selects the ``host-vault`` backend, and
no process is started unless it also declares ``local_service``.

Operating systems. Starting the vault is a POSIX feature:

- macOS: supported, ``bind`` defaults to loopback. Verified with Docker Desktop.
- Linux: supported, ``bind`` must be written out. Compose maps
  ``host.docker.internal`` to ``host-gateway``. Under Docker Engine that is the
  bridge gateway, which a loopback listener does not answer, so a silent
  loopback default would report "started" and leave the stack in ``Created``.
- Windows: refused. The vault's root-key custody check reads POSIX file modes,
  so the vault service does not run natively there. The reachability check
  still applies to a vault that runs elsewhere (WSL, another machine).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kdcube_cli.host_vault import HostVaultConfigurationError, HostVaultRuntimeConfig

# Names that route from a container to the Docker host. Seen from the host
# itself, the same listener is on loopback.
DOCKER_HOST_ALIASES = frozenset(
    {"host.docker.internal", "gateway.docker.internal", "host-gateway", "localhost"}
)
DEFAULT_BIND = "127.0.0.1"
VAULT_SERVER_RELATIVE = Path(
    "deployment/docker/all_in_one_kdcube/secrets/host_vault/vault_server.py"
)
VAULT_SOURCE_RELATIVE = Path("src/kdcube-ai-app")
START_TIMEOUT_SECONDS = 20.0
PROBE_TIMEOUT_SECONDS = 2.0

Probe = Callable[[str, int], bool]
Spawn = Callable[[list[str], Mapping[str, str], Path, Path], int]


@dataclass(frozen=True)
class HostVaultLocalService:
    """The ``local_service`` declaration: this host runs the vault process."""

    home: Path
    python: Path
    bind: str = DEFAULT_BIND


@dataclass(frozen=True)
class HostVaultEnsureResult:
    state: str  # "disabled" | "reachable" | "started" | "unverified"
    probe_host: str = ""
    port: int = 0
    pid: int = 0

    def describe(self) -> str:
        if self.state == "started":
            return (
                f"Host vault was not running. Started it on "
                f"{self.probe_host}:{self.port} (pid {self.pid})."
            )
        if self.state == "reachable":
            return f"Host vault is reachable on {self.probe_host}:{self.port}."
        if self.state == "unverified":
            return (
                f"Host vault at {self.probe_host}:{self.port} did not answer from this "
                "host. It is on another machine, so the containers may still reach it. "
                "If chat-ingress and chat-proc stay in 'Created', the vault is the cause."
            )
        return ""


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_windows(platform: str) -> bool:
    return platform.startswith("win") or platform == "cygwin"


def _is_linux(platform: str) -> bool:
    return platform.startswith("linux")


def local_service_from_assembly(
    assembly: Mapping[str, object],
    *,
    platform: str = sys.platform,
) -> HostVaultLocalService | None:
    secrets = _mapping(assembly.get("secrets"))
    service = _mapping(secrets.get("service"))
    vault = _mapping(service.get("host_vault"))
    # Absent and null both mean "not declared": a portable export nulls this
    # machine-local block the same way it nulls identity_dir.
    if vault.get("local_service") is None:
        return None
    if _is_windows(platform):
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.local_service is not supported on Windows. "
            "The vault's root-key custody check reads POSIX file modes, so the vault "
            "service does not run natively there. Run the vault in WSL or on another "
            "host and remove local_service. kdcube then only checks that it answers"
        )
    raw = _mapping(vault.get("local_service"))
    home_text = _text(raw.get("home"))
    python_text = _text(raw.get("python"))
    if not home_text or not python_text:
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.local_service requires 'home' (the vault home "
            "directory) and 'python' (the interpreter of the vault environment)"
        )
    home = Path(home_text).expanduser()
    python = Path(python_text).expanduser()
    if not home.is_absolute() or not python.is_absolute():
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.local_service.home and .python must be "
            "absolute host paths"
        )
    bind = _text(raw.get("bind"))
    if not bind and _is_linux(platform):
        raise HostVaultConfigurationError(
            "secrets.service.host_vault.local_service.bind is required on Linux. "
            "Containers reach the host through host.docker.internal, which Compose maps "
            "to host-gateway. Under Docker Engine that is the bridge gateway (commonly "
            "172.17.0.1), which a loopback listener does not answer: use that address, "
            "or 0.0.0.0 with port access restricted at the firewall. Under Docker "
            "Desktop for Linux use 127.0.0.1"
        )
    return HostVaultLocalService(home=home, python=python, bind=bind or DEFAULT_BIND)


def _address_parts(address: str) -> tuple[str, int]:
    parsed = urlsplit(f"//{address}")
    return str(parsed.hostname or ""), int(parsed.port or 0)


def is_docker_host_address(address: str) -> bool:
    host, _port = _address_parts(address)
    return host.lower() in DOCKER_HOST_ALIASES or host in {"127.0.0.1", "::1"}


def probe_target(address: str, local: HostVaultLocalService | None) -> tuple[str, int]:
    """Where the vault listener is, seen from the host that runs the CLI."""
    host, port = _address_parts(address)
    if local is not None:
        bind = local.bind
        return (DEFAULT_BIND if bind in {"0.0.0.0", "::", ""} else bind), port
    if is_docker_host_address(address):
        return DEFAULT_BIND, port
    return host, port


def tcp_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _spawn_detached(
    command: list[str], env: Mapping[str, str], cwd: Path, log_path: Path
) -> int:
    # POSIX only: a new session detaches the vault from the CLI's terminal, so
    # it outlives the command. Windows never reaches this (local_service is
    # refused there before anything is started).
    with open(log_path, "ab", buffering=0) as log:
        process = subprocess.Popen(  # noqa: S603 - arguments come from the descriptor
            command,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return int(process.pid)


def _log_tail(log_path: Path, lines: int = 5) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def _start_local_service(
    local: HostVaultLocalService,
    *,
    ai_app_root: Path,
    port: int,
    probe_host: str,
    probe: Probe,
    spawn: Spawn,
    timeout_seconds: float,
) -> int:
    server = Path(ai_app_root) / VAULT_SERVER_RELATIVE
    if not server.is_file():
        raise HostVaultConfigurationError(
            f"the host vault entrypoint is missing: {server}. A local_service vault "
            "runs from a KDCube source checkout. Pass the checkout with --path"
        )
    if not local.python.is_file():
        raise HostVaultConfigurationError(
            f"secrets.service.host_vault.local_service.python does not exist: {local.python}"
        )
    if not (local.home / "tls" / "server.crt").is_file():
        raise HostVaultConfigurationError(
            f"secrets.service.host_vault.local_service.home is not an initialized "
            f"vault home (no tls/server.crt): {local.home}. Run `hostvaultctl.py init` first"
        )
    env = dict(os.environ)
    env.update(
        {
            "KDCUBE_HOST_VAULT_HOME": str(local.home),
            "KDCUBE_HOST_VAULT_BIND": local.bind,
            "KDCUBE_HOST_VAULT_PORT": str(port),
            "PYTHONPATH": str(Path(ai_app_root) / VAULT_SOURCE_RELATIVE),
        }
    )
    log_path = local.home / "service.log"
    pid = spawn([str(local.python), str(server)], env, local.home, log_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if probe(probe_host, port):
            (local.home / "service.pid").write_text(f"{pid}\n", encoding="utf-8")
            return pid
        time.sleep(0.25)
    tail = _log_tail(log_path)
    raise HostVaultConfigurationError(
        f"the host vault was started (pid {pid}) and did not listen on "
        f"{probe_host}:{port} within {timeout_seconds:.0f}s. Last lines of {log_path}:\n{tail}"
    )


def ensure_host_vault_running(
    config: HostVaultRuntimeConfig,
    assembly: Mapping[str, object],
    *,
    ai_app_root: Path,
    probe: Probe = tcp_probe,
    spawn: Spawn = _spawn_detached,
    timeout_seconds: float = START_TIMEOUT_SECONDS,
    platform: str = sys.platform,
) -> HostVaultEnsureResult:
    # Gate 1: the descriptor did not select the host-vault backend. Nothing is
    # read, probed, or started.
    if not config.enabled:
        return HostVaultEnsureResult(state="disabled")
    local = local_service_from_assembly(assembly, platform=platform)
    probe_host, port = probe_target(config.address, local)
    if probe(probe_host, port):
        return HostVaultEnsureResult(state="reachable", probe_host=probe_host, port=port)
    # Gate 2: no local_service. The CLI never starts a process it was not told
    # to own. A listener that must be on this host and is not is a certain
    # failure. A vault on another machine is judged from a different network
    # position than the containers, so that case is a warning.
    if local is None and not is_docker_host_address(config.address):
        return HostVaultEnsureResult(state="unverified", probe_host=probe_host, port=port)
    if local is None:
        raise HostVaultConfigurationError(
            f"the host vault at {config.address} is not reachable (nothing answers on "
            f"{probe_host}:{port} from this host). secrets.service.backend is 'host-vault', "
            "so kdcube-secrets stays unhealthy and chat-ingress, chat-proc, web-ui and "
            "web-proxy would wait in 'Created'. Start the vault service, or declare "
            "secrets.service.host_vault.local_service (home, python) so kdcube starts it"
        )
    pid = _start_local_service(
        local,
        ai_app_root=ai_app_root,
        port=port,
        probe_host=probe_host,
        probe=probe,
        spawn=spawn,
        timeout_seconds=timeout_seconds,
    )
    return HostVaultEnsureResult(state="started", probe_host=probe_host, port=port, pid=pid)


__all__ = [
    "DEFAULT_BIND",
    "HostVaultEnsureResult",
    "HostVaultLocalService",
    "ensure_host_vault_running",
    "is_docker_host_address",
    "local_service_from_assembly",
    "probe_target",
    "tcp_probe",
]

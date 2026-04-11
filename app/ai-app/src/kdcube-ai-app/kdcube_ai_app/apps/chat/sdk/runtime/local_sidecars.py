# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

logger = logging.getLogger("Bundle.LocalSidecars")


@dataclass(frozen=True)
class LocalSidecarHandle:
    bundle_id: str
    tenant: str
    project: str
    name: str
    host: str
    port: Optional[int]
    pid: int
    base_url: Optional[str]
    cwd: Optional[str]
    started_at: float


@dataclass
class _LocalSidecarRecord:
    key: str
    process: subprocess.Popen
    handle: LocalSidecarHandle


_registry: dict[str, _LocalSidecarRecord] = {}
_lock = threading.RLock()


def _sidecar_key(*, bundle_id: str, tenant: str, project: str, name: str) -> str:
    return f"{bundle_id}:{tenant}:{project}:{name}"


def _allocate_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _is_running(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def _wait_for_tcp(host: str, port: int, *, deadline: float, proc: subprocess.Popen) -> None:
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Sidecar exited before tcp readiness: {proc.returncode}")
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for sidecar tcp {host}:{port}")


def _wait_for_http(url: str, *, deadline: float, proc: subprocess.Popen) -> None:
    request = urllib.request.Request(url, method="GET")
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Sidecar exited before http readiness: {proc.returncode}")
        try:
            with urllib.request.urlopen(request, timeout=0.75):
                return
        except urllib.error.HTTPError:
            # Any HTTP response means the server is up.
            return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for sidecar http {url}")


def _terminate_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return


def _kill_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        return


def ensure_local_sidecar(
    *,
    bundle_id: str,
    tenant: str,
    project: str,
    name: str,
    command: Sequence[str],
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    host: str = "127.0.0.1",
    port: Optional[int] = 0,
    ready_path: Optional[str] = None,
    ready_timeout_sec: float = 30.0,
) -> LocalSidecarHandle:
    """
    Ensure a per-process local sidecar is running for the bundle scope.

    The returned handle is process-local. Each proc worker maintains its own
    sidecar instance and tears it down on proc lifespan shutdown.
    """
    if not command:
        raise ValueError("Sidecar command is required")

    key = _sidecar_key(bundle_id=bundle_id, tenant=tenant, project=project, name=name)

    with _lock:
        existing = _registry.get(key)
        if existing and _is_running(existing.process):
            return existing.handle
        if existing:
            _registry.pop(key, None)

        resolved_cwd = str(Path(cwd).resolve()) if cwd else None
        chosen_port = _allocate_port(host) if port == 0 else port
        merged_env = os.environ.copy()
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})
        merged_env.setdefault("HOST", host)
        if chosen_port is not None:
            merged_env.setdefault("PORT", str(chosen_port))

        proc = subprocess.Popen(
            list(command),
            cwd=resolved_cwd,
            env=merged_env,
            start_new_session=(os.name != "nt"),
        )

        base_url = f"http://{host}:{chosen_port}" if chosen_port is not None else None
        handle = LocalSidecarHandle(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            host=host,
            port=chosen_port,
            pid=proc.pid,
            base_url=base_url,
            cwd=resolved_cwd,
            started_at=time.time(),
        )
        _registry[key] = _LocalSidecarRecord(key=key, process=proc, handle=handle)

    deadline = time.monotonic() + max(0.1, float(ready_timeout_sec))
    try:
        if chosen_port is not None and ready_path:
            _wait_for_http(f"{base_url}{ready_path}", deadline=deadline, proc=proc)
        elif chosen_port is not None:
            _wait_for_tcp(host, chosen_port, deadline=deadline, proc=proc)
        elif ready_path:
            raise ValueError("ready_path requires a port-enabled sidecar")
    except Exception:
        stop_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            terminate_timeout_sec=2.0,
            kill_timeout_sec=1.0,
        )
        raise

    if proc.poll() is not None:
        stop_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            terminate_timeout_sec=0.1,
            kill_timeout_sec=0.1,
        )
        raise RuntimeError(f"Local sidecar exited during startup: {name}")

    logger.info(
        "Started local sidecar: bundle=%s tenant=%s project=%s name=%s pid=%s base_url=%s",
        bundle_id,
        tenant,
        project,
        name,
        proc.pid,
        base_url,
    )
    return handle


def get_local_sidecar(
    *,
    bundle_id: str,
    tenant: str,
    project: str,
    name: str,
) -> Optional[LocalSidecarHandle]:
    key = _sidecar_key(bundle_id=bundle_id, tenant=tenant, project=project, name=name)
    with _lock:
        record = _registry.get(key)
        if not record:
            return None
        if not _is_running(record.process):
            _registry.pop(key, None)
            return None
        return record.handle


def stop_local_sidecar(
    *,
    bundle_id: str,
    tenant: str,
    project: str,
    name: str,
    terminate_timeout_sec: float = 10.0,
    kill_timeout_sec: float = 3.0,
) -> None:
    key = _sidecar_key(bundle_id=bundle_id, tenant=tenant, project=project, name=name)
    with _lock:
        record = _registry.pop(key, None)
    if not record:
        return

    proc = record.process
    _terminate_process_group(proc)
    try:
        proc.wait(timeout=max(0.1, terminate_timeout_sec))
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            proc.wait(timeout=max(0.1, kill_timeout_sec))
        except subprocess.TimeoutExpired:
            logger.warning("Local sidecar did not exit after SIGKILL: %s", key)


def stop_local_sidecars_for_scope(
    *,
    bundle_id: str,
    tenant: str,
    project: str,
    terminate_timeout_sec: float = 10.0,
    kill_timeout_sec: float = 3.0,
) -> int:
    with _lock:
        keys = [
            key
            for key in _registry.keys()
            if key.startswith(f"{bundle_id}:{tenant}:{project}:")
        ]
    for key in keys:
        scope_bundle_id, scope_tenant, scope_project, name = key.split(":", 3)
        stop_local_sidecar(
            bundle_id=scope_bundle_id,
            tenant=scope_tenant,
            project=scope_project,
            name=name,
            terminate_timeout_sec=terminate_timeout_sec,
            kill_timeout_sec=kill_timeout_sec,
        )
    return len(keys)


def stop_local_sidecars_for_bundle_ids(
    *,
    bundle_ids: set[str],
    tenant: str,
    project: str,
    terminate_timeout_sec: float = 10.0,
    kill_timeout_sec: float = 3.0,
) -> int:
    if not bundle_ids:
        return 0
    stopped = 0
    for bundle_id in bundle_ids:
        stopped += stop_local_sidecars_for_scope(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            terminate_timeout_sec=terminate_timeout_sec,
            kill_timeout_sec=kill_timeout_sec,
        )
    return stopped


def stop_inactive_local_sidecars(
    *,
    active_bundle_ids: set[str],
    tenant: str,
    project: str,
    terminate_timeout_sec: float = 10.0,
    kill_timeout_sec: float = 3.0,
) -> int:
    with _lock:
        scoped_bundle_ids = set()
        for key in _registry.keys():
            bundle_id_key, tenant_key, project_key, _name = key.split(":", 3)
            if tenant_key == tenant and project_key == project:
                scoped_bundle_ids.add(bundle_id_key)
    target_bundle_ids = scoped_bundle_ids - active_bundle_ids
    return stop_local_sidecars_for_bundle_ids(
        bundle_ids=target_bundle_ids,
        tenant=tenant,
        project=project,
        terminate_timeout_sec=terminate_timeout_sec,
        kill_timeout_sec=kill_timeout_sec,
    )


async def shutdown_all_local_sidecars(
    *,
    terminate_timeout_sec: float = 10.0,
    kill_timeout_sec: float = 3.0,
) -> None:
    def _shutdown_sync() -> None:
        with _lock:
            keys = list(_registry.keys())
        for key in keys:
            bundle_id, tenant, project, name = key.split(":", 3)
            stop_local_sidecar(
                bundle_id=bundle_id,
                tenant=tenant,
                project=project,
                name=name,
                terminate_timeout_sec=terminate_timeout_sec,
                kill_timeout_sec=kill_timeout_sec,
            )

    await asyncio.to_thread(_shutdown_sync)


def clear_local_sidecars_for_tests() -> None:
    with _lock:
        keys = list(_registry.keys())
    for key in keys:
        bundle_id, tenant, project, name = key.split(":", 3)
        stop_local_sidecar(
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            name=name,
            terminate_timeout_sec=0.2,
            kill_timeout_sec=0.2,
        )

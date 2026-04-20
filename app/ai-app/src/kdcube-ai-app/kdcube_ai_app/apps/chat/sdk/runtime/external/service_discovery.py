# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/docker/discovery.py

import pathlib, os
from dataclasses import dataclass
from typing import Mapping, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

CONTAINER_BUNDLES_ROOT = "/bundles"
CONTAINER_MANAGED_BUNDLES_ROOT = "/managed-bundles"


@dataclass(frozen=True)
class HostMountPaths:
    """
    Proc-runtime host path hints used for Docker-in-Docker sibling mounts.

    These are deployment topology hints. In descriptors-first deployments they
    come from ``assembly.yaml -> paths.*`` and are promoted into the managed
    settings contract; env vars remain the compatibility fallback.
    """

    kdcube_storage: str | None
    bundles: str | None
    managed_bundles: str | None
    bundle_storage: str | None
    exec_workspace: str | None

    @property
    def effective_managed_bundles(self) -> str:
        return self.managed_bundles or CONTAINER_MANAGED_BUNDLES_ROOT

    @property
    def effective_bundles(self) -> str:
        return self.bundles or CONTAINER_BUNDLES_ROOT

    @property
    def effective_bundle_storage(self) -> str:
        return self.bundle_storage or "/bundle-storage"

    @property
    def effective_kdcube_storage(self) -> str:
        return self.kdcube_storage or "/kdcube-storage"

    @property
    def effective_exec_workspace(self) -> str:
        return self.exec_workspace or "/exec-workspace"


def get_host_mount_paths(env: Mapping[str, str] | None = None) -> HostMountPaths:
    environ = env or os.environ

    settings = None
    if env is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None

    def _pick(env_key: str, settings_attr: str) -> str | None:
        raw = (environ.get(env_key) or "").strip()
        if raw:
            return raw
        if settings is None:
            return None
        raw = str(getattr(settings, settings_attr, None) or "").strip()
        return raw or None

    return HostMountPaths(
        kdcube_storage=_pick("HOST_KDCUBE_STORAGE_PATH", "HOST_KDCUBE_STORAGE_PATH"),
        bundles=_pick("HOST_BUNDLES_PATH", "HOST_BUNDLES_PATH"),
        managed_bundles=_pick("HOST_MANAGED_BUNDLES_PATH", "HOST_MANAGED_BUNDLES_PATH"),
        bundle_storage=_pick("HOST_BUNDLE_STORAGE_PATH", "HOST_BUNDLE_STORAGE_PATH"),
        exec_workspace=_pick("HOST_EXEC_WORKSPACE_PATH", "HOST_EXEC_WORKSPACE_PATH"),
    )

def _path(p: pathlib.Path | str) -> str:
    return str(p if isinstance(p, pathlib.Path) else pathlib.Path(p))

def _is_running_in_docker() -> bool:
    """
    Detect if we're running inside a Docker container.
    """
    # Method 1: Check for .dockerenv file
    if os.path.exists("/.dockerenv"):
        return True

    # Method 2: Check cgroup for docker
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except Exception:
        pass

    # Method 3: Check environment variable
    if os.environ.get("DOCKER_CONTAINER") == "true":
        return True

    return False

def _translate_container_path_to_host(container_path: pathlib.Path) -> pathlib.Path:
    """
    Translate container paths to host paths for Docker-in-Docker.

    When running inside a container, paths we see (like /tmp/codegen_xxx or /kdcube-storage/temp/abc)
    need to be translated to host paths for sibling containers to access them.

    Architecture:
    -------------
    Host filesystem:
      ├── kdcube-storage/              # Knowledge base data (persistent)
      ├── bundles/                 # Agentic bundles (persistent)
      └── exec-workspace/          # Temporary code execution (ephemeral, can be cleaned)
          └── codegen_xxx/         # Auto-created, auto-cleaned
              ├── pkg/
              └── out/

    Inside chat-chat container:
      /kdcube-storage/        → Host: {HOST_KDCUBE_STORAGE_PATH}
      /bundles/           → Host: {HOST_BUNDLES_PATH}
      /managed-bundles/   → Host: {HOST_MANAGED_BUNDLES_PATH}
      /exec-workspace/    → Host: {HOST_EXEC_WORKSPACE_PATH}
      /tmp/codegen_xxx/   → Redirected to /exec-workspace/codegen_xxx/

    py-code-exec sibling container mounts:
      Host: {HOST_EXEC_WORKSPACE_PATH}/codegen_xxx/pkg → Container: /workspace/work
      Host: {HOST_EXEC_WORKSPACE_PATH}/codegen_xxx/out → Container: /workspace/out
    """
    container_path = container_path.resolve()
    path_str = str(container_path)
    host_mounts = get_host_mount_paths()

    running_in_docker = _is_running_in_docker()

    # /kdcube-storage → host path from env
    if path_str.startswith("/kdcube-storage"):
        rel = os.path.relpath(path_str, "/kdcube-storage")
        return pathlib.Path(host_mounts.effective_kdcube_storage) / rel

    # /managed-bundles → host path from env/settings
    if path_str.startswith(CONTAINER_MANAGED_BUNDLES_ROOT):
        rel = os.path.relpath(path_str, CONTAINER_MANAGED_BUNDLES_ROOT)
        return pathlib.Path(host_mounts.effective_managed_bundles) / rel

    # /bundles → host path from env
    if path_str.startswith(CONTAINER_BUNDLES_ROOT):
        rel = os.path.relpath(path_str, CONTAINER_BUNDLES_ROOT)
        return pathlib.Path(host_mounts.effective_bundles) / rel

    # /bundle-storage → host path from env
    if path_str.startswith("/bundle-storage"):
        rel = os.path.relpath(path_str, "/bundle-storage")
        return pathlib.Path(host_mounts.effective_bundle_storage) / rel

    # /exec-workspace → host path from env (NEW)
    # This handles paths that were created directly in /exec-workspace
    if path_str.startswith("/exec-workspace") and running_in_docker:
        rel = os.path.relpath(path_str, "/exec-workspace")
        return pathlib.Path(host_mounts.effective_exec_workspace) / rel

    # /tmp → Redirect to /exec-workspace (Docker-in-Docker)
    # This handles paths that were mistakenly created in /tmp
    if path_str.startswith("/tmp") and running_in_docker:
        rel = os.path.relpath(path_str, "/tmp")
        shared_path = pathlib.Path("/exec-workspace") / rel
        shared_path.mkdir(parents=True, exist_ok=True)
        return pathlib.Path(host_mounts.effective_exec_workspace) / rel

    # If no translation needed, return as-is
    return container_path


def _should_resolve_redis_host(hostname: str) -> bool:
    """
    Determine if a Redis hostname needs DNS resolution for Docker-in-Docker.

    Returns True if:
    - Single-word hostname (Docker service name like "redis")
    - localhost/127.0.0.1 (won't work in DinD anyway)

    Returns False if:
    - Already an IP address
    - FQDN with domain (external service)
    """
    import ipaddress

    # Already an IP? Don't resolve
    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        pass

    # localhost variations? Resolve (though they won't work in DinD)
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True

    # Single-word hostname (Docker service name)? Resolve
    if "." not in hostname:
        return True

    # FQDN with domain? Don't resolve (probably external)
    return False


def _resolve_redis_url_for_container(redis_url: str, *, logger: Optional[AgentLogger] = None) -> str:
    """
    Resolve Redis hostname to IP if needed for Docker-in-Docker networking.
    """
    import socket
    from urllib.parse import urlparse, urlunparse, ParseResult

    log = logger or AgentLogger("docker.redis_resolve")

    if not _is_running_in_docker():
        return redis_url

    try:
        parsed: ParseResult = urlparse(redis_url)
        hostname = parsed.hostname

        if not hostname or not _should_resolve_redis_host(hostname):
            return redis_url

        # Resolve hostname to IP
        try:
            redis_ip = socket.gethostbyname(hostname)
            log.log(f"[redis_resolve] Resolved {hostname} → {redis_ip}", level="INFO")

            # EXPLICIT: Rebuild netloc preserving auth and port
            port_str = f":{parsed.port}" if parsed.port else ""
            auth_str = f"{parsed.username or ''}"
            if parsed.password:
                auth_str += f":{parsed.password}"
            if auth_str:
                auth_str += "@"

            new_netloc = f"{auth_str}{redis_ip}{port_str}"

            # Construct tuple with explicit strings
            new_url = urlunparse((
                parsed.scheme or "",
                new_netloc,
                parsed.path or "",
                parsed.params or "",
                parsed.query or "",
                parsed.fragment or ""
            ))

            return new_url

        except socket.gaierror as e:
            log.log(f"[redis_resolve] Failed to resolve {hostname}: {e}", level="WARNING")
            return redis_url

    except Exception as e:
        log.log(f"[redis_resolve] Error processing Redis URL: {e}", level="ERROR")
        return redis_url

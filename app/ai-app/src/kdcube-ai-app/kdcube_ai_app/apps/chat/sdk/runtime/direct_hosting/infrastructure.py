"""Standard descriptor bootstrap for directly hosted agents."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlparse


DESCRIPTOR_FILENAMES = (
    "assembly.yaml",
    "secrets.yaml",
    "economics.yaml",
    "gateway.yaml",
)


def activate_platform_descriptors(descriptors_dir: Path):
    """Point the SDK at one ordinary platform descriptor directory."""
    root = descriptors_dir.expanduser().resolve()
    missing = [name for name in DESCRIPTOR_FILENAMES if not (root / name).is_file()]
    if missing:
        raise ValueError(
            f"descriptor directory {root} is missing: {', '.join(missing)}"
        )
    os.environ["PLATFORM_DESCRIPTORS_DIR"] = str(root)
    os.environ["ASSEMBLY_YAML_DESCRIPTOR_PATH"] = str(root / "assembly.yaml")
    os.environ["GLOBAL_SECRETS_YAML"] = str(root / "secrets.yaml")
    os.environ["ECONOMICS_YAML_DESCRIPTOR_PATH"] = str(root / "economics.yaml")
    os.environ["GATEWAY_YAML_PATH"] = str(root / "gateway.yaml")
    os.environ["GATEWAY_COMPONENT"] = "proc"

    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    from kdcube_ai_app.apps.chat.sdk.config_cache import clear_config_cache
    from kdcube_ai_app.apps.chat.sdk.config_scopes import _load_plain_yaml_cached
    from kdcube_ai_app.infra.secrets import reset_secrets_manager_cache

    clear_config_cache()
    _load_plain_yaml_cached.cache_clear()
    get_settings.cache_clear()
    reset_secrets_manager_cache()
    settings = get_settings()
    raw_storage = str(getattr(settings, "STORAGE_PATH", "") or "").strip()
    if raw_storage and not urlparse(raw_storage).scheme:
        settings.STORAGE_PATH = str((root / raw_storage).expanduser().resolve())
    raw_bundle_storage = str(
        getattr(settings.PLATFORM.APPLICATIONS, "BUNDLE_STORAGE_ROOT", "") or ""
    ).strip()
    if raw_bundle_storage and not urlparse(raw_bundle_storage).scheme:
        bundle_storage = Path(raw_bundle_storage).expanduser()
        if not bundle_storage.is_absolute():
            bundle_storage = root / bundle_storage
        settings.PLATFORM.APPLICATIONS.BUNDLE_STORAGE_ROOT = str(
            bundle_storage.resolve()
        )
    raw_host_bundle_storage = str(
        getattr(settings, "HOST_BUNDLE_STORAGE_PATH", "") or ""
    ).strip()
    if raw_host_bundle_storage and not urlparse(raw_host_bundle_storage).scheme:
        host_bundle_storage = Path(raw_host_bundle_storage).expanduser()
        if not host_bundle_storage.is_absolute():
            host_bundle_storage = root / host_bundle_storage
        settings.HOST_BUNDLE_STORAGE_PATH = str(host_bundle_storage.resolve())
    return settings


def redis_url(settings, *, check_only: bool = False) -> str:
    password = str(getattr(settings, "REDIS_PASSWORD", "") or "")
    if not password and not check_only:
        raise RuntimeError("platform.infra.redis.password is not set in secrets.yaml")
    password = quote(password or "check-only", safe="")
    host = str(getattr(settings, "REDIS_HOST", "") or "127.0.0.1")
    port = int(getattr(settings, "REDIS_PORT", 56379) or 56379)
    database = int(getattr(settings, "REDIS_DB", 0) or 0)
    return f"redis://:{password}@{host}:{port}/{database}"


def postgres_url(settings, *, check_only: bool = False) -> str:
    password_raw = str(getattr(settings, "PGPASSWORD", "") or "")
    if not password_raw and not check_only:
        raise RuntimeError("platform.infra.postgres.password is not set in secrets.yaml")
    password = quote(password_raw or "check-only", safe="")
    user = quote(str(getattr(settings, "PGUSER", "") or "kdcube_agents"), safe="")
    host = str(getattr(settings, "PGHOST", "") or "127.0.0.1")
    port = int(getattr(settings, "PGPORT", 55432) or 55432)
    database = quote(str(getattr(settings, "PGDATABASE", "") or "kdcube_agents"), safe="")
    sslmode = "require" if bool(getattr(settings, "PGSSL", False)) else "disable"
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode={sslmode}"


def postgres_label(settings) -> str:
    return (
        f"{getattr(settings, 'PGHOST', None) or '127.0.0.1'}:"
        f"{int(getattr(settings, 'PGPORT', None) or 55432)}/"
        f"{getattr(settings, 'PGDATABASE', None) or 'kdcube_agents'}"
    )


def storage_uri(settings, *, descriptors_dir: Path) -> str:
    raw = str(getattr(settings, "STORAGE_PATH", "") or "").strip()
    if not raw:
        raise ValueError("storage.kdcube is required in assembly.yaml")
    parsed = urlparse(raw)
    if parsed.scheme:
        return raw
    return (descriptors_dir / raw).expanduser().resolve().as_uri()


def platform_exec_profile(settings) -> dict[str, object]:
    py = settings.PLATFORM.EXEC.PY
    return {
        "mode": "docker",
        "image": str(py.PY_CODE_EXEC_IMAGE),
        "timeout": int(py.PY_CODE_EXEC_TIMEOUT),
        "container_strategy": str(py.PY_CODE_EXEC_CONTAINER_STRATEGY),
        "network_mode": str(py.PY_CODE_EXEC_NETWORK_MODE),
        "max_file_bytes": str(py.EXEC_MAX_FILE_BYTES),
        "max_exec_workspace_delta_bytes": str(py.EXEC_MAX_WORKSPACE_DELTA_BYTES),
        "max_workspace_bytes": py.EXEC_MAX_WORKSPACE_BYTES,
        "workspace_monitor_interval_s": float(py.EXEC_WORKSPACE_MONITOR_INTERVAL_S),
    }


def direct_harness_config(
    *,
    settings,
    descriptors_dir: Path,
    bundle_id: str,
    agent_id: str,
    user_id: str,
    user_type: str,
    session_id: str,
    check_only: bool = False,
):
    """Project standard descriptors into the SDK's direct-host contract."""
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_harness import (
        DirectAgentHarnessConfig,
    )

    return DirectAgentHarnessConfig(
        tenant=str(getattr(settings, "TENANT", "") or "standalone"),
        project=str(getattr(settings, "PROJECT", "") or "agent-harness-demo"),
        user_id=user_id,
        user_type=user_type,
        session_id=session_id,
        bundle_id=bundle_id,
        agent_id=agent_id,
        postgres_url=postgres_url(settings, check_only=check_only),
        redis_url=redis_url(settings, check_only=check_only),
        storage_uri=storage_uri(settings, descriptors_dir=descriptors_dir),
        turn_cache_ttl_seconds=3600,
    )


__all__ = [
    "DESCRIPTOR_FILENAMES",
    "activate_platform_descriptors",
    "direct_harness_config",
    "platform_exec_profile",
    "postgres_label",
    "postgres_url",
    "redis_url",
    "storage_uri",
]

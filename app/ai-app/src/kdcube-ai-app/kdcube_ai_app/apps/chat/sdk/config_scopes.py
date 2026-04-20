import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from kdcube_ai_app.infra.secrets import get_secrets_manager

def _descriptors_dir() -> Path | None:
    raw = str(os.getenv("PLATFORM_DESCRIPTORS_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _descriptor_path(*, env_name: str, filename: str, default: str) -> Path:
    explicit = str(os.getenv(env_name) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    descriptors_dir = _descriptors_dir()
    if descriptors_dir is not None:
        return descriptors_dir / filename
    return Path(default)


# ─── YAML helpers ─────────────────────────────────────────────────────────────

def _descriptor_cache_token(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return str(path), stat.st_mtime_ns, stat.st_size


def _load_plain_yaml(path: Path) -> Any:
    token = _descriptor_cache_token(path)
    if token is None:
        return None
    return _load_plain_yaml_cached(*token)


@lru_cache(maxsize=8)
def _load_plain_yaml_cached(path_str: str, _mtime_ns: int, _size: int) -> Any:
    path = Path(path_str)
    try:
        return yaml.safe_load(path.read_text()) if path.exists() else None
    except Exception:
        return None


def _resolve_dotted_value(data: Any, dotted_path: str) -> Any:
    if not dotted_path:
        return data
    cur: Any = data
    segments = [part for part in dotted_path.split(".") if part]
    idx = 0
    while idx < len(segments):
        segment = segments[idx]
        if isinstance(cur, dict):
            if segment in cur:
                cur = cur.get(segment)
                idx += 1
                continue
            matched = False
            for end in range(len(segments), idx, -1):
                compound = ".".join(segments[idx:end])
                if compound in cur:
                    cur = cur.get(compound)
                    idx = end
                    matched = True
                    break
            if not matched:
                return None
            continue
        if isinstance(cur, list):
            if not segment.isdigit():
                return None
            list_idx = int(segment)
            if list_idx < 0 or list_idx >= len(cur):
                return None
            cur = cur[list_idx]
            idx += 1
            continue
        return None
    return cur


def _load_assembly_plain(dotted_path: str) -> Any:
    return _resolve_dotted_value(
        _load_plain_yaml(
            _descriptor_path(
                env_name="ASSEMBLY_YAML_DESCRIPTOR_PATH",
                filename="assembly.yaml",
                default="/config/assembly.yaml",
            )
        ),
        dotted_path,
    )


def _parse_plain_key(key: str) -> tuple[Path, str]:
    raw = str(key or "").strip()
    assembly_path = _descriptor_path(
        env_name="ASSEMBLY_YAML_DESCRIPTOR_PATH",
        filename="assembly.yaml",
        default="/config/assembly.yaml",
    )
    bundles_path = _descriptor_path(
        env_name="BUNDLES_YAML_DESCRIPTOR_PATH",
        filename="bundles.yaml",
        default="/config/bundles.yaml",
    )
    if not raw:
        return assembly_path, ""
    for prefix, path in {
        "a:": assembly_path,
        "assembly:": assembly_path,
        "b:": bundles_path,
        "bundles:": bundles_path,
    }.items():
        if raw.startswith(prefix):
            return path, raw[len(prefix):]
    return assembly_path, raw


# ─── PLATFORM_CONFIG — base class with assembly + env helpers ─────────────────
# Inherits BaseSettings to preserve backward compat: for cloud deployments env
# vars are injected directly and BaseSettings picks them up automatically for
# all flat attrs on Settings.  The _resolve_* helpers below implement the
# canonical priority order:  assembly.yaml  >  env var  >  hard-coded default.

class PLATFORM_CONFIG(BaseSettings):
    GATEWAY_COMPONENT: str | None = None

    # ── secret fetching ───────────────────────────────────────────────────────

    def _fetch_secret(self, key: str) -> str | None:
        try:
            return get_secrets_manager(self).get_secret(key)
        except Exception:
            return None

    # ── low-level env readers (typed, None when absent / unparseable) ─────────

    def _env_present(self, name: str) -> bool:
        return bool(str(os.getenv(name) or "").strip())

    def _env_str(self, name: str) -> str | None:
        val = os.getenv(name)
        return val.strip() if val and val.strip() else None

    def _env_int(self, name: str) -> int | None:
        val = os.getenv(name)
        if not val or not val.strip():
            return None
        try:
            return int(val.strip())
        except (ValueError, TypeError):
            return None

    def _env_float(self, name: str) -> float | None:
        val = os.getenv(name)
        if not val or not val.strip():
            return None
        try:
            return float(val.strip())
        except (ValueError, TypeError):
            return None

    def _env_bool(self, name: str) -> bool | None:
        val = os.getenv(name)
        if not val or not val.strip():
            return None
        return val.strip().lower() in {"1", "true", "yes", "y", "on"}

    # ── low-level assembly readers ────────────────────────────────────────────

    def _assembly_str(self, path: str) -> str | None:
        val = _load_assembly_plain(path)
        s = str(val).strip() if val is not None else ""
        return s or None

    def _assembly_int(self, path: str) -> int | None:
        val = _load_assembly_plain(path)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _assembly_float(self, path: str) -> float | None:
        val = _load_assembly_plain(path)
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _assembly_bool(self, path: str) -> bool | None:
        val = _load_assembly_plain(path)
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

    # ── high-level resolvers: assembly > env > default ────────────────────────
    # Assembly descriptor wins when present; env var is the fallback for cloud
    # deployments that inject env vars without a mounted descriptor; hard-coded
    # default is the last resort.  This mirrors the old BaseSettings behaviour
    # where model_post_init would override the env-read value with the assembly
    # value whenever the key was present in the descriptor.

    def _resolve_str(self, env_name: str, assembly_path: str, default: str | None = None) -> str | None:
        v = self._assembly_str(assembly_path)
        if v is not None:
            return v
        v = self._env_str(env_name)
        return v if v is not None else default

    def _resolve_int(self, env_name: str, assembly_path: str, default: int) -> int:
        v = self._assembly_int(assembly_path)
        if v is not None:
            return v
        v = self._env_int(env_name)
        return v if v is not None else default

    def _resolve_float(self, env_name: str, assembly_path: str, default: float) -> float:
        v = self._assembly_float(assembly_path)
        if v is not None:
            return v
        v = self._env_float(env_name)
        return v if v is not None else default

    def _resolve_bool(self, env_name: str, assembly_path: str, default: bool) -> bool:
        # Check assembly first; only fall back to env when no assembly value is set.
        # _env_present guard is required so that False from env is not skipped.
        v = self._assembly_bool(assembly_path)
        if v is not None:
            return v
        if self._env_present(env_name):
            ev = self._env_bool(env_name)
            return ev if ev is not None else default
        return default


# ─── PLATFORM.LOG ─────────────────────────────────────────────────────────────

class LOGConfig(BaseModel):
    LOG_LEVEL: str = "INFO"
    LOG_MAX_MB: int = 20
    LOG_BACKUP_COUNT: int = 10
    LOG_DIR: str | None = None
    LOG_FILE_PREFIX: str | None = None


# ─── PLATFORM.SERVICE ─────────────────────────────────────────────────────────
# Per-service runtime knobs (read from platform.services.<component>.service.*)

class ServiceConfig(BaseModel):
    UVICORN_RELOAD: bool = False
    HEARTBEAT_INTERVAL: int = 5
    CB_RELAY_IDENTITY: str | None = None
    # Proc scheduler backend selector (legacy_lists | kafka | …)
    CHAT_SCHEDULER_BACKEND: str = "legacy_lists"
    # Task timeout knobs (proc)
    CHAT_TASK_TIMEOUT_SEC: int = 600
    CHAT_TASK_IDLE_TIMEOUT_SEC: int = 600
    CHAT_TASK_MAX_WALL_TIME_SEC: int = 2400
    CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC: float = 1.0


# ─── PLATFORM.HOSTED_SERVICES ─────────────────────────────────────────────────
# Component-hosted sidecar services (ingress: AV scanner, …)

class AVConfig(BaseModel):
    APP_AV_SCAN: bool = True
    APP_AV_TIMEOUT_S: float = 3.0
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310


class HostedServicesConfig(BaseModel):
    AV: AVConfig = Field(default_factory=AVConfig)


# ─── PLATFORM.MONITORING ──────────────────────────────────────────────────────

class MonitoringConfig(BaseModel):
    MONITORING_BURST_ENABLE: bool = True


# ─── PLATFORM.EXEC ────────────────────────────────────────────────────────────

class PyExecConfig(BaseModel):
    """Python sandboxed code execution settings (get_settings().PLATFORM.EXEC.PY)."""
    PY_CODE_EXEC_IMAGE: str = "py-code-exec:latest"
    PY_CODE_EXEC_TIMEOUT: int = 600
    PY_CODE_EXEC_NETWORK_MODE: str = "host"


class ExecConfig(BaseModel):
    EXEC_WORKSPACE_ROOT: str | None = None
    PY: PyExecConfig = Field(default_factory=PyExecConfig)


# ─── PLATFORM.ACCOUNTING ──────────────────────────────────────────────────────

class AccountingConfig(BaseModel):
    # JSON string: per-tool tier metadata, e.g. {"web_search": {"brave": {"tier": "free"}}}
    ACCOUNTING_SERVICES: str | None = None


# ─── PLATFORM.APPLICATIONS ────────────────────────────────────────────────────

class GitBundlesConfig(BaseModel):
    BUNDLE_GIT_RESOLUTION_ENABLED: bool = True
    BUNDLE_GIT_ATOMIC: bool = True
    BUNDLE_GIT_ALWAYS_PULL: bool = False
    BUNDLE_GIT_REDIS_LOCK: bool = True
    BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS: int = 300
    BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS: int = 60
    BUNDLE_GIT_PREFETCH_ENABLED: bool = True
    BUNDLE_GIT_PREFETCH_INTERVAL_SECONDS: int = 15
    BUNDLE_GIT_FAIL_BACKOFF_SECONDS: int = 60
    BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS: int = 300
    BUNDLE_GIT_KEEP: int = 3
    BUNDLE_GIT_TTL_HOURS: int = 0
    GIT_SSH_KEY_PATH: str | None = None
    GIT_SSH_KNOWN_HOSTS: str | None = None
    GIT_SSH_STRICT_HOST_KEY_CHECKING: str = "yes"


class ApplicationsConfig(BaseModel):
    BUNDLES_ROOT: str = "/bundles"
    MANAGED_BUNDLES_ROOT: str = "/managed-bundles"
    BUNDLE_STORAGE_ROOT: str | None = None
    BUNDLES_INCLUDE_EXAMPLES: bool = True
    BUNDLE_CLEANUP_ENABLED: bool = True
    BUNDLE_CLEANUP_INTERVAL_SECONDS: int = 3600
    BUNDLE_CLEANUP_LOCK_TTL_SECONDS: int = 900
    BUNDLE_REF_TTL_SECONDS: int = 3600
    BUNDLES_FORCE_ENV_ON_STARTUP: bool = False
    BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS: int = 60
    BUNDLES_PRELOAD_ON_START: bool = False
    BUNDLES_PRELOAD_LOCK_TTL_SECONDS: int = 900
    GIT: GitBundlesConfig = Field(default_factory=GitBundlesConfig)


# ─── PLATFORM (top-level nested config) ───────────────────────────────────────
# Access via get_settings().PLATFORM.<sub>.<attr>

class PlatformConfig(BaseModel):
    LOG: LOGConfig = Field(default_factory=LOGConfig)
    SERVICE: ServiceConfig = Field(default_factory=ServiceConfig)
    HOSTED_SERVICES: HostedServicesConfig = Field(default_factory=HostedServicesConfig)
    MONITORING: MonitoringConfig = Field(default_factory=MonitoringConfig)
    EXEC: ExecConfig = Field(default_factory=ExecConfig)
    ACCOUNTING: AccountingConfig = Field(default_factory=AccountingConfig)
    APPLICATIONS: ApplicationsConfig = Field(default_factory=ApplicationsConfig)


# ─── AUTH ─────────────────────────────────────────────────────────────────────

class IDPLocalConfig(BaseModel):
    """Local (simple-auth) identity provider settings."""
    IDP_DB_PATH: str | None = None
    IDP_IMPORT_ENABLED: bool = False
    IDP_IMPORT_RUN_AT: str | None = None
    IDP_IMPORT_SCRIPT_PATH: str | None = None


class IDPConfig(BaseModel):
    local: IDPLocalConfig = Field(default_factory=IDPLocalConfig)


class AuthConfig(BaseModel):
    """Auth settings.  Access via get_settings().AUTH.<attr>."""
    COGNITO_REGION: str | None = None
    COGNITO_USER_POOL_ID: str | None = None
    COGNITO_APP_CLIENT_ID: str | None = None
    COGNITO_SERVICE_CLIENT_ID: str | None = None
    ID_TOKEN_HEADER_NAME: str = "X-ID-Token"
    AUTH_TOKEN_COOKIE_NAME: str = "__Secure-LATC"
    ID_TOKEN_COOKIE_NAME: str = "__Secure-LITC"
    JWKS_CACHE_TTL_SECONDS: int = 86400
    OIDC_SERVICE_USER_EMAIL: str | None = None
    OIDC_SERVICE_ADMIN_USERNAME: str | None = None
    OIDC_SERVICE_ADMIN_PASSWORD: str | None = None
    IDP: IDPConfig = Field(default_factory=IDPConfig)


# ─── SERVICES ─────────────────────────────────────────────────────────────────

class ServicesConfig(BaseModel):
    """External-service / model settings.  Access via get_settings().SERVICES.<attr>.
    Gemini cache knobs are plain config — use get_plain("services.llm.gemini.<attr>").
    """
    DEFAULT_EMBEDDING_MODEL_ID: str | None = None


# ─── RUNTIME_CONFIG (request-context headers — misc runtime wiring) ──────────
# Access via get_settings().RUNTIME_CONFIG.<attr>.
# Inherits PLATFORM_CONFIG (BaseSettings) so env vars are picked up automatically
# as backward compat for cloud deployments.

class RUNTIME_CONFIG(PLATFORM_CONFIG):
    STREAM_ID_HEADER_NAME: str | None = Field(default="KDC-Stream-ID", alias="STREAM_ID_HEADER_NAME")
    USER_TIMEZONE_HEADER_NAME: str | None = Field(default="X-User-Timezone", alias="USER_TIMEZONE_HEADER_NAME")
    USER_UTC_OFFSET_MIN_HEADER_NAME: str | None = Field(default="X-User-UTC-Offset", alias="USER_UTC_OFFSET_MIN_HEADER_NAME")

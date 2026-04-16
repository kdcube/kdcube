# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chatbot/sdk/config.py
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Any
from pydantic import Field
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import yaml

from kdcube_ai_app.infra.props import get_props_manager
from kdcube_ai_app.infra.secrets import get_secrets_manager

_SECRET_LOG = logging.getLogger("kdcube.settings.secrets")
_SECRET_LOGGED: set[str] = set()

_SECRET_ALIASES: dict[str, list[str]] = {
    "services.openai.api_key": ["OPENAI_API_KEY"],
    "services.anthropic.api_key": ["ANTHROPIC_API_KEY"],
    "services.anthropic.claude_code_key": ["CLAUDE_CODE_KEY"],
    "services.brave.api_key": ["BRAVE_API_KEY"],
    "services.google.api_key": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "services.git.http_token": ["GIT_HTTP_TOKEN"],
    "services.git.http_user": ["GIT_HTTP_USER"],
    "services.openrouter.api_key": ["OPENROUTER_API_KEY"],
    "services.stripe.secret_key": ["STRIPE_SECRET_KEY", "STRIPE_API_KEY"],
    "services.stripe.webhook_secret": ["STRIPE_WEBHOOK_SECRET"],
    "services.huggingface.api_key": ["HUGGING_FACE_KEY", "HUGGINGFACE_API_KEY"],
    "services.firecrawl.api_key": ["FIRECRAWL_API_KEY"],
    "services.email.password": ["EMAIL_PASSWORD"],
}
_LEGACY_SECRET_TO_CANON: dict[str, str] = {
    legacy: canon for canon, aliases in _SECRET_ALIASES.items() for legacy in aliases
}
_PG_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_ASSEMBLY_YAML_PATH = Path(os.getenv("ASSEMBLY_YAML_DESCRIPTOR_PATH") or "/config/assembly.yaml")
_BUNDLES_YAML_PATH = Path(os.getenv("BUNDLES_YAML_DESCRIPTOR_PATH") or "/config/bundles.yaml")


def _secret_candidates(key: str) -> list[str]:
    if key in _SECRET_ALIASES:
        return [key, *_SECRET_ALIASES[key]]
    if key in _LEGACY_SECRET_TO_CANON:
        canon = _LEGACY_SECRET_TO_CANON[key]
        return [canon, *_SECRET_ALIASES.get(canon, [])]
    return [key]


def _log_secret_status(key: str, value: str | None, source: str | None) -> None:
    if key in _SECRET_LOGGED:
        return
    _SECRET_LOGGED.add(key)
    if value:
        _SECRET_LOG.info("Secret %s loaded (%s)", key, source or "unknown")
    else:
        _SECRET_LOG.warning("Secret %s not set", key)


def _resolve_current_bundle_id() -> str | None:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_current_bundle_id,
            get_current_request_context,
        )

        ctx = get_current_request_context()
    except Exception:
        ctx = None
        get_current_bundle_id = lambda: None  # type: ignore[assignment]
    if ctx is not None:
        bundle_id = str(getattr(getattr(ctx, "routing", None), "bundle_id", None) or "").strip()
        if bundle_id:
            return bundle_id
    bundle_id = str(get_current_bundle_id() or "").strip()
    if bundle_id:
        return bundle_id
    for env_key in ("KDCUBE_BUNDLE_ID", "AGENTIC_BUNDLE_ID", "BUNDLE_ID"):
        env_val = str(os.getenv(env_key) or "").strip()
        if env_val:
            return env_val
    return None


def _normalize_secret_lookup_key(key: str) -> str:
    raw = str(key or "").strip()
    if not raw:
        return raw
    if raw.startswith("a:") or raw.startswith("assembly:"):
        return raw.split(":", 1)[1].strip()
    if raw.startswith("b:") or raw.startswith("bundles:"):
        tail = raw.split(":", 1)[1].strip().strip(".")
        if not tail:
            return raw
        bundle_id = _resolve_current_bundle_id()
        if not bundle_id:
            _SECRET_LOG.warning("Bundle-scoped secret %s requested without bundle context", raw)
            return raw
        return f"bundles.{bundle_id}.secrets.{tail}"
    return raw


def get_secret(key: str, default: str | None = None) -> str | None:
    settings = get_settings()
    normalized_key = _normalize_secret_lookup_key(key)
    for candidate in _secret_candidates(normalized_key):
        env_val = os.getenv(candidate)
        if env_val:
            return env_val
        if hasattr(settings, candidate):
            value = getattr(settings, candidate)
            if value:
                return value
        value = settings.secret(candidate, default=None)
        if value:
            return value
    return default


def read_secret(key: str, default: str | None = None) -> str | None:
    return get_secret(key, default=default)


def _parse_plain_key(key: str) -> tuple[Path, str]:
    raw = str(key or "").strip()
    if not raw:
        return _ASSEMBLY_YAML_PATH, ""
    for prefix, path in {
        "a:": _ASSEMBLY_YAML_PATH,
        "assembly:": _ASSEMBLY_YAML_PATH,
        "b:": _BUNDLES_YAML_PATH,
        "bundles:": _BUNDLES_YAML_PATH,
    }.items():
        if raw.startswith(prefix):
            return path, raw[len(prefix):]
    return _ASSEMBLY_YAML_PATH, raw


def _descriptor_cache_token(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return str(path), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=8)
def _load_plain_yaml_cached(path_str: str, _mtime_ns: int, _size: int) -> Any:
    path = Path(path_str)
    try:
        return yaml.safe_load(path.read_text()) if path.exists() else None
    except Exception:
        return None


def _load_plain_yaml(path: Path) -> Any:
    token = _descriptor_cache_token(path)
    if token is None:
        return None
    return _load_plain_yaml_cached(*token)


def _load_assembly_plain(dotted_path: str) -> Any:
    return _resolve_dotted_value(_load_plain_yaml(_ASSEMBLY_YAML_PATH), dotted_path)


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


def get_plain(key: str, default: Any = None) -> Any:
    return get_settings().plain(key, default=default)


def read_plain(key: str, default: Any = None) -> Any:
    return get_plain(key, default=default)


def _resolve_current_user_bundle_scope(
    *,
    user_id: str | None = None,
    bundle_id: str | None = None,
) -> tuple[str | None, str | None]:
    resolved_user_id = str(user_id or "").strip() or None
    resolved_bundle_id = str(bundle_id or "").strip() or None
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

    ctx = get_current_request_context()
    if ctx is not None:
        if resolved_user_id is None:
            resolved_user_id = str(getattr(getattr(ctx, "user", None), "user_id", None) or "").strip() or None
        if resolved_bundle_id is None:
            resolved_bundle_id = str(getattr(getattr(ctx, "routing", None), "bundle_id", None) or "").strip() or None
    if resolved_bundle_id is None:
        resolved_bundle_id = _resolve_current_bundle_id()
    return resolved_user_id, resolved_bundle_id


def get_user_secret(
    key: str,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
    default: str | None = None,
) -> str | None:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id:
        return default
    settings = get_settings()
    try:
        value = get_secrets_manager(settings).get_user_secret(
            user_id=resolved_user_id,
            bundle_id=resolved_bundle_id,
            key=key,
        )
    except Exception:
        value = None
    return value or default


def set_user_secret(
    key: str,
    value: str,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
) -> None:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id:
        raise RuntimeError("Current user id is unavailable for user-scoped secret write")
    get_secrets_manager(get_settings()).set_user_secret(
        user_id=resolved_user_id,
        bundle_id=resolved_bundle_id,
        key=key,
        value=value,
    )


def delete_user_secret(
    key: str,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
) -> None:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id:
        raise RuntimeError("Current user id is unavailable for user-scoped secret delete")
    get_secrets_manager(get_settings()).delete_user_secret(
        user_id=resolved_user_id,
        bundle_id=resolved_bundle_id,
        key=key,
    )


def get_user_prop(
    key: str,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
    default: Any = None,
) -> Any:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id or not resolved_bundle_id:
        return default
    try:
        value = get_props_manager().get_user_prop(
            user_id=resolved_user_id,
            bundle_id=resolved_bundle_id,
            key=key,
        )
    except Exception:
        value = None
    return default if value is None else value


def get_user_props(
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id or not resolved_bundle_id:
        return {}
    try:
        return get_props_manager().list_user_props(
            user_id=resolved_user_id,
            bundle_id=resolved_bundle_id,
        )
    except Exception:
        return {}


def set_user_prop(
    key: str,
    value: Any,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
) -> None:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id:
        raise RuntimeError("Current user id is unavailable for user-scoped prop write")
    if not resolved_bundle_id:
        raise RuntimeError("Current bundle id is unavailable for user-scoped prop write")
    get_props_manager().set_user_prop(
        user_id=resolved_user_id,
        bundle_id=resolved_bundle_id,
        key=key,
        value=value,
    )


def delete_user_prop(
    key: str,
    *,
    bundle_id: str | None = None,
    user_id: str | None = None,
) -> None:
    resolved_user_id, resolved_bundle_id = _resolve_current_user_bundle_scope(
        user_id=user_id,
        bundle_id=bundle_id,
    )
    if not resolved_user_id:
        raise RuntimeError("Current user id is unavailable for user-scoped prop delete")
    if not resolved_bundle_id:
        raise RuntimeError("Current bundle id is unavailable for user-scoped prop delete")
    get_props_manager().delete_user_prop(
        user_id=resolved_user_id,
        bundle_id=resolved_bundle_id,
        key=key,
    )


def log_secret_statuses(force: bool = False) -> None:
    if force:
        _SECRET_LOGGED.clear()
    settings = get_settings()
    env_openai = os.getenv("OPENAI_API_KEY")
    env_anthropic = os.getenv("ANTHROPIC_API_KEY")
    env_gemini = os.getenv("GEMINI_API_KEY")
    env_brave = os.getenv("BRAVE_API_KEY")
    env_git_token = os.getenv("GIT_HTTP_TOKEN")
    env_git_user = os.getenv("GIT_HTTP_USER")
    env_openrouter = os.getenv("OPENROUTER_API_KEY")
    _log_secret_status("services.openai.api_key", settings.OPENAI_API_KEY, "env" if env_openai else "secrets")
    _log_secret_status("services.anthropic.api_key", settings.ANTHROPIC_API_KEY, "env" if env_anthropic else "secrets")
    _log_secret_status("services.google.api_key", settings.GOOGLE_API_KEY, "env" if env_gemini else "secrets")
    _log_secret_status("services.brave.api_key", settings.BRAVE_API_KEY, "env" if env_brave else "secrets")
    _log_secret_status("services.git.http_token", settings.GIT_HTTP_TOKEN, "env" if env_git_token else "secrets")
    _log_secret_status("services.git.http_user", settings.GIT_HTTP_USER, "env" if env_git_user else "secrets")
    _log_secret_status("services.openrouter.api_key", settings.OPENROUTER_API_KEY, "env" if env_openrouter else "secrets")

class CorsConfig(BaseModel):
    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = True

    @field_validator("allow_origins", "allow_methods", "allow_headers", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return ["*"]
        if isinstance(v, str):
            if v.strip() == "*":
                return ["*"]
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return v
        return [str(v)]


class Settings(BaseSettings):
    # API
    PORT: int = 8011
    CORS_CONFIG: str | None = None
    CORS_CONFIG_OBJ: CorsConfig | None = None

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = Field(default=None, alias="GEMINI_API_KEY")
    BRAVE_API_KEY: str | None = None
    GIT_HTTP_TOKEN: str | None = None
    GIT_HTTP_USER: str | None = None
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str | None = None
    CLAUDE_CODE_KEY: str | None = None
    SECRETS_PROVIDER: str | None = None
    SECRETS_URL: str | None = None
    SECRETS_TOKEN: str | None = None
    SECRETS_ADMIN_TOKEN: str | None = None
    SECRETS_AWS_SM_PREFIX: str | None = None
    SECRETS_SM_PREFIX: str | None = None
    GLOBAL_SECRETS_YAML: str | None = None
    BUNDLE_SECRETS_YAML: str | None = None
    LINK_PREVIEW_ENABLED: bool = Field(default=True)

    # Postgres
    PGHOST: str = Field(default="localhost", alias="POSTGRES_HOST")
    PGPORT: int = Field(default=5434, alias="POSTGRES_PORT")
    PGDATABASE: str = Field(default="postgres", alias="POSTGRES_DATABASE")
    PGUSER: str = Field(default="postgres", alias="POSTGRES_USER")
    PGPASSWORD: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    PGSSL: bool = Field(default=False, alias="POSTGRES_SSL")
    PGSSL_MODE: str | None = Field(default=None, alias="POSTGRES_SSL_MODE")
    PGSSL_ROOT_CERT: str | None = Field(default=None, alias="POSTGRES_SSL_ROOT_CERT")

    # Neo4j
    NEO4J_URI: str = Field(default="bolt://neo4j:7687", alias="APP_NEO4J_URI")
    NEO4J_USER: str = Field(default="neo4j", alias="APP_NEO4J_USERNAME")
    NEO4J_PASSWORD: str = Field(default="neo4j", alias="APP_NEO4J_PASSWORD")
    APP_GRAPH_ENABLED: bool = Field(default=False, alias="APP_GRAPH_ENABLED")

    # S3
    AWS_REGION: str = "us-east-1"
    AWS_S3_BUCKET: str = "your-conv-bucket"
    AWS_PROFILE: str | None = None
    AWS_SHARED_CREDENTIALS_FILE: str | None = None
    AWS_CONFIG_FILE: str | None = None

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_PASSWORD: str | None = Field(default=None)
    REDIS_DB: int = Field(default=0)

    STORAGE_PATH: str | None = Field(default=None, alias="KDCUBE_STORAGE_PATH")
    BUNDLE_STORAGE_URL: str | None = Field(default=None, alias="CB_BUNDLE_STORAGE_URL")
    REACT_WORKSPACE_IMPLEMENTATION: str = Field(default="custom")
    REACT_WORKSPACE_GIT_REPO: str | None = None
    AI_REACT_AGENT_VERSION: str = Field(default="v2")
    CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION: str = Field(default="local")
    CLAUDE_CODE_SESSION_GIT_REPO: str | None = None

    TENANT: str = Field(default="home", alias="TENANT_ID")
    PROJECT: str = Field(default="default-project", alias="PROJECT_ID")
    INSTANCE_ID: str = Field(default="home-instance-1", alias="INSTANCE_ID")

    DEFAULT_MODEL_LLM: str | None = "claude-3-7-sonnet-20250219"

    # OPEX aggregation scheduler
    OPEX_AGG_CRON: str = Field(default="0 3 * * *")

    # Subscription rollover scheduler
    SUBSCRIPTION_ROLLOVER_ENABLED: bool = Field(default=True)
    SUBSCRIPTION_ROLLOVER_CRON: str = Field(default="15 * * * *")
    SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS: int = Field(default=900)
    SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT: int = Field(default=500)

    # Stripe reconcile scheduler
    STRIPE_RECONCILE_ENABLED: bool = Field(default=True)
    STRIPE_RECONCILE_CRON: str = Field(default="45 * * * *")
    STRIPE_RECONCILE_LOCK_TTL_SECONDS: int = Field(default=900)

    # Bundle cleanup + ref tracking
    BUNDLE_CLEANUP_ENABLED: bool = Field(default=True)
    BUNDLE_CLEANUP_INTERVAL_SECONDS: int = Field(default=3600)
    BUNDLE_CLEANUP_LOCK_TTL_SECONDS: int = Field(default=900)
    BUNDLE_REF_TTL_SECONDS: int = Field(default=3600)
    BUNDLES_PRELOAD_LOCK_TTL_SECONDS: int = Field(default=900)
    # Include built-in example bundles from sdk/examples/bundles
    BUNDLES_INCLUDE_EXAMPLES: bool = Field(default=True)
    # Force bundles registry to be overwritten from AGENTIC_BUNDLES_JSON at startup (processor only).
    BUNDLES_FORCE_ENV_ON_STARTUP: bool = Field(default=False)
    BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS: int = Field(default=60)
    # Eagerly load all configured bundles at proc startup.
    BUNDLES_PRELOAD_ON_START: bool = Field(default=False)

    # Email notifications (admin alerts for Stripe events)
    EMAIL_ENABLED: bool = Field(default=True)
    EMAIL_HOST: str | None = None
    EMAIL_PORT: int = Field(default=587)
    EMAIL_USER: str | None = None
    EMAIL_FROM: str | None = None
    EMAIL_TO: str = Field(default="ops@example.com")
    EMAIL_USE_TLS: bool = Field(default=True)

    # Solution workspace retention — set True to keep workdir/outdir after turn completes (debug).
    SOLUTION_RETAIN_TURN_WORKSPACE: bool = Field(default=False)

    def model_post_init(self, __context) -> None:
        def _fetch_secret(key: str) -> str | None:
            try:
                return get_secrets_manager(self).get_secret(key)
            except Exception:
                return None

        def _env_present(name: str) -> bool:
            return bool(str(os.getenv(name) or "").strip())

        # Override tenant/project from GATEWAY_CONFIG_JSON if present.
        cfg_json = os.getenv("GATEWAY_CONFIG_JSON")
        if isinstance(cfg_json, str) and cfg_json.strip():
            try:
                import json
                cfg = json.loads(cfg_json)
                tenant = cfg.get("tenant_id") or cfg.get("tenant")
                project = cfg.get("project_id") or cfg.get("project")
                if tenant:
                    self.TENANT = tenant
                if project:
                    self.PROJECT = project
            except Exception:
                # Keep env-derived values on parse failure
                pass

        # Parse CORS_CONFIG JSON (if provided)
        if isinstance(self.CORS_CONFIG, str) and self.CORS_CONFIG.strip():
            try:
                import json
                self.CORS_CONFIG_OBJ = CorsConfig.model_validate(json.loads(self.CORS_CONFIG))
            except Exception:
                # Leave None on parse failure
                self.CORS_CONFIG_OBJ = None

        # If REDIS_URL is not explicitly set, build it from host/port/password/db.
        if not os.getenv("REDIS_URL"):
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

        # Populate non-secret storage/runtime settings from assembly.yaml when
        # they are not explicitly provided via env. This lets runtime code use
        # get_settings() while keeping assembly.yaml as the source of truth.
        if not _env_present("SECRETS_PROVIDER") and not self.SECRETS_PROVIDER:
            self.SECRETS_PROVIDER = _load_assembly_plain("secrets.provider")
        if not _env_present("KDCUBE_STORAGE_PATH") and not self.STORAGE_PATH:
            self.STORAGE_PATH = _load_assembly_plain("storage.kdcube")
        if not _env_present("CB_BUNDLE_STORAGE_URL") and not self.BUNDLE_STORAGE_URL:
            self.BUNDLE_STORAGE_URL = _load_assembly_plain("storage.bundles")
        if not _env_present("REACT_WORKSPACE_IMPLEMENTATION"):
            self.REACT_WORKSPACE_IMPLEMENTATION = str(
                _load_assembly_plain("storage.workspace.type") or self.REACT_WORKSPACE_IMPLEMENTATION
            )
        if not _env_present("REACT_WORKSPACE_GIT_REPO") and not self.REACT_WORKSPACE_GIT_REPO:
            self.REACT_WORKSPACE_GIT_REPO = _load_assembly_plain("storage.workspace.repo")
        self.AI_REACT_AGENT_VERSION = (
            str(self.AI_REACT_AGENT_VERSION or "v2").strip().lower() or "v2"
        )
        if self.AI_REACT_AGENT_VERSION not in {"v2", "v3"}:
            self.AI_REACT_AGENT_VERSION = "v2"
        if not _env_present("CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION"):
            self.CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION = str(
                _load_assembly_plain("storage.claude_code_session.type")
                or self.CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION
            )
        if not _env_present("CLAUDE_CODE_SESSION_GIT_REPO") and not self.CLAUDE_CODE_SESSION_GIT_REPO:
            self.CLAUDE_CODE_SESSION_GIT_REPO = _load_assembly_plain("storage.claude_code_session.repo")

        # Populate secrets from provider if not set in env.
        env_openai = os.getenv("OPENAI_API_KEY")
        env_anthropic = os.getenv("ANTHROPIC_API_KEY")
        env_gemini = os.getenv("GEMINI_API_KEY")
        env_brave = os.getenv("BRAVE_API_KEY")
        env_git_token = os.getenv("GIT_HTTP_TOKEN")
        env_git_user = os.getenv("GIT_HTTP_USER")
        env_openrouter = os.getenv("OPENROUTER_API_KEY")

        if not self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = _fetch_secret("services.openai.api_key") or _fetch_secret("OPENAI_API_KEY")
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = _fetch_secret("services.anthropic.api_key") or _fetch_secret("ANTHROPIC_API_KEY")
        if not self.GOOGLE_API_KEY:
            self.GOOGLE_API_KEY = (
                _fetch_secret("services.google.api_key")
                or _fetch_secret("GOOGLE_API_KEY")
                or _fetch_secret("GEMINI_API_KEY")
            )
        if not self.BRAVE_API_KEY:
            self.BRAVE_API_KEY = _fetch_secret("services.brave.api_key") or _fetch_secret("BRAVE_API_KEY")
        if not self.GIT_HTTP_TOKEN:
            self.GIT_HTTP_TOKEN = _fetch_secret("services.git.http_token") or _fetch_secret("GIT_HTTP_TOKEN")
        if not self.GIT_HTTP_USER and self.GIT_HTTP_TOKEN:
            self.GIT_HTTP_USER = _fetch_secret("services.git.http_user") or "x-access-token"
        if not self.OPENROUTER_API_KEY:
            self.OPENROUTER_API_KEY = _fetch_secret("services.openrouter.api_key") or _fetch_secret("OPENROUTER_API_KEY")
        if not self.OPENROUTER_BASE_URL:
            self.OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
        if not self.CLAUDE_CODE_KEY:
            self.CLAUDE_CODE_KEY = _fetch_secret("services.anthropic.claude_code_key") or _fetch_secret("CLAUDE_CODE_KEY")

        _log_secret_status("services.openai.api_key", self.OPENAI_API_KEY, "env" if env_openai else "secrets")
        _log_secret_status("services.anthropic.api_key", self.ANTHROPIC_API_KEY, "env" if env_anthropic else "secrets")
        _log_secret_status("services.google.api_key", self.GOOGLE_API_KEY, "env" if env_gemini else "secrets")
        _log_secret_status("services.brave.api_key", self.BRAVE_API_KEY, "env" if env_brave else "secrets")
        _log_secret_status("services.git.http_token", self.GIT_HTTP_TOKEN, "env" if env_git_token else "secrets")
        _log_secret_status("services.git.http_user", self.GIT_HTTP_USER, "env" if env_git_user else "secrets")
        _log_secret_status("services.openrouter.api_key", self.OPENROUTER_API_KEY, "env" if env_openrouter else "secrets")

    def secret(self, key: str, default: str | None = None) -> str | None:
        normalized_key = _normalize_secret_lookup_key(key)
        env_val = os.getenv(normalized_key)
        if env_val:
            return env_val
        try:
            value = get_secrets_manager(self).get_secret(normalized_key)
        except Exception:
            value = None
        return value or default

    def plain(self, key: str, default: Any = None) -> Any:
        path, dotted_path = _parse_plain_key(key)
        value = _resolve_dotted_value(_load_plain_yaml(path), dotted_path)
        return default if value is None else value

@lru_cache()
def get_settings() -> Settings:
    return Settings()


def resolve_asyncpg_ssl(settings: Settings | None = None) -> bool | str:
    settings = settings or get_settings()
    if not settings.PGSSL:
        return False

    mode = (settings.PGSSL_MODE or "").strip().lower().replace("_", "-") or "require"
    if mode not in _PG_SSL_MODES:
        raise ValueError(
            f"Unsupported POSTGRES_SSL_MODE={settings.PGSSL_MODE!r}; "
            f"expected one of {sorted(_PG_SSL_MODES)}"
        )

    # Let asyncpg honor libpq-compatible sslmode semantics. If an explicit CA
    # path is configured under our POSTGRES_* namespace, bridge it to the
    # environment name asyncpg already understands.
    if settings.PGSSL_ROOT_CERT and not os.getenv("PGSSLROOTCERT"):
        os.environ["PGSSLROOTCERT"] = settings.PGSSL_ROOT_CERT

    return mode

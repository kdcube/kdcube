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

from kdcube_ai_app.apps.chat.sdk.config_scopes import (
    PLATFORM_CONFIG, RUNTIME_CONFIG,
    _load_assembly_plain, _parse_plain_key, _load_plain_yaml, _resolve_dotted_value,
    LOGConfig, ServiceConfig, AVConfig, HostedServicesConfig, MonitoringConfig,
    PyExecConfig, ExecConfig, AccountingConfig, GitBundlesConfig, ApplicationsConfig,
    PlatformConfig, IDPLocalConfig, IDPConfig, AuthConfig, ServicesConfig,
)
from kdcube_ai_app.infra.props import get_props_manager
from kdcube_ai_app.infra.secrets import get_secrets_manager

_SECRET_LOG = logging.getLogger("kdcube.settings.secrets")
_SECRET_LOGGED: set[str] = set()

_SECRET_ALIASES: dict[str, list[str]] = {
    "services.openai.api_key": ["OPENAI_API_KEY"],
    "services.anthropic.api_key": ["ANTHROPIC_API_KEY"],
    "services.anthropic.claude_code_key": ["CLAUDE_CODE_KEY"],
    "services.brave.api_key": ["BRAVE_API_KEY"],
    "services.brave.api_comm_mid_key": ["BRAVE_API_COMM_MID_KEY"],
    "services.google.api_key": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "services.git.http_token": ["GIT_HTTP_TOKEN"],
    "services.git.http_user": ["GIT_HTTP_USER"],
    "services.openrouter.api_key": ["OPENROUTER_API_KEY"],
    "services.serpapi.api_key": ["SERPAPI_API_KEY"],
    "services.stripe.secret_key": ["STRIPE_SECRET_KEY", "STRIPE_API_KEY"],
    "services.stripe.webhook_secret": ["STRIPE_WEBHOOK_SECRET"],
    "services.huggingface.api_key": ["HUGGING_FACE_KEY", "HUGGINGFACE_API_KEY", "HUGGING_FACE_API_TOKEN"],
    "services.firecrawl.api_key": ["FIRECRAWL_API_KEY"],
    "services.email.password": ["EMAIL_PASSWORD"],
    "auth.oidc.admin_email": ["OIDC_SERVICE_USER_EMAIL"],
    "auth.oidc.admin_username": ["OIDC_SERVICE_ADMIN_USERNAME"],
    "auth.oidc.admin_password": ["OIDC_SERVICE_ADMIN_PASSWORD"],
}
_LEGACY_SECRET_TO_CANON: dict[str, str] = {
    legacy: canon for canon, aliases in _SECRET_ALIASES.items() for legacy in aliases
}
_PG_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}



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


def _resolve_current_bundle_scope(
    *,
    bundle_id: str | None = None,
    tenant: str | None = None,
    project: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    resolved_bundle_id = str(bundle_id or "").strip() or None
    resolved_tenant = str(tenant or "").strip() or None
    resolved_project = str(project or "").strip() or None

    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

    ctx = get_current_request_context()
    if ctx is not None:
        actor = getattr(ctx, "actor", None)
        if resolved_tenant is None:
            resolved_tenant = str(getattr(actor, "tenant_id", None) or "").strip() or None
        if resolved_project is None:
            resolved_project = str(getattr(actor, "project_id", None) or "").strip() or None
        if resolved_bundle_id is None:
            resolved_bundle_id = str(getattr(getattr(ctx, "routing", None), "bundle_id", None) or "").strip() or None

    if resolved_bundle_id is None:
        resolved_bundle_id = _resolve_current_bundle_id()

    settings = get_settings()
    if resolved_tenant is None:
        resolved_tenant = str(getattr(settings, "TENANT", None) or "").strip() or None
    if resolved_project is None:
        resolved_project = str(getattr(settings, "PROJECT", None) or "").strip() or None

    return resolved_bundle_id, resolved_tenant, resolved_project


def _set_nested_value(root: dict[str, Any], path: str, value: Any) -> None:
    parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not parts:
        raise ValueError("Bundle prop key path is empty")
    cursor = root
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


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


async def set_bundle_secret(
    key: str,
    value: str,
    *,
    bundle_id: str | None = None,
) -> None:
    resolved_bundle_id, _tenant, _project = _resolve_current_bundle_scope(bundle_id=bundle_id)
    if not resolved_bundle_id:
        raise RuntimeError("Current bundle id is unavailable for bundle-scoped secret write")
    tail = str(key or "").strip().strip(".")
    if not tail:
        raise ValueError("Bundle secret key path is empty")
    get_secrets_manager(get_settings()).set_secret(
        f"bundles.{resolved_bundle_id}.secrets.{tail}",
        value,
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


async def set_bundle_prop(
    key: str,
    value: Any,
    *,
    bundle_id: str | None = None,
    tenant: str | None = None,
    project: str | None = None,
) -> None:
    resolved_bundle_id, resolved_tenant, resolved_project = _resolve_current_bundle_scope(
        bundle_id=bundle_id,
        tenant=tenant,
        project=project,
    )
    if not resolved_bundle_id:
        raise RuntimeError("Current bundle id is unavailable for bundle-scoped prop write")
    if not resolved_tenant:
        raise RuntimeError("Current tenant is unavailable for bundle-scoped prop write")
    if not resolved_project:
        raise RuntimeError("Current project is unavailable for bundle-scoped prop write")

    from kdcube_ai_app.infra.redis.client import get_async_redis_client
    from kdcube_ai_app.infra.plugin.bundle_store import (
        get_bundle_props as _store_get_bundle_props,
        put_bundle_props as _store_put_bundle_props,
    )

    tail = str(key or "").strip().strip(".")
    if not tail:
        raise ValueError("Bundle prop key path is empty")

    redis = get_async_redis_client(get_settings().REDIS_URL)
    current = await _store_get_bundle_props(
        redis,
        tenant=resolved_tenant,
        project=resolved_project,
        bundle_id=resolved_bundle_id,
    )
    props = dict(current or {})
    _set_nested_value(props, tail, value)
    await _store_put_bundle_props(
        redis,
        tenant=resolved_tenant,
        project=resolved_project,
        bundle_id=resolved_bundle_id,
        props=props,
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


class Settings(PLATFORM_CONFIG):
    # API
    PORT: int = 8011
    CHAT_APP_PORT: int = Field(default=8010)
    CHAT_PROCESSOR_PORT: int = Field(default=8020)
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

    # Nested config objects — populated in model_post_init.
    # Primary access: get_settings().PLATFORM.<sub>.<attr>
    #                 get_settings().AUTH.<attr>
    #                 get_settings().SERVICES.<attr>
    PLATFORM: Any = None
    AUTH: Any = None
    SERVICES: Any = None
    RUNTIME_CONFIG: Any = None

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
    PLATFORM_DESCRIPTORS_DIR: str | None = None
    REACT_WORKSPACE_IMPLEMENTATION: str = Field(default="custom")
    REACT_WORKSPACE_GIT_REPO: str | None = None
    AI_REACT_AGENT_VERSION: str = Field(default="v2")
    AI_REACT_AGENT_MULTI_ACTION: str = Field(default="off")
    CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION: str = Field(default="local")
    CLAUDE_CODE_SESSION_GIT_REPO: str | None = None

    TENANT: str = Field(default="home", alias="TENANT_ID")
    PROJECT: str = Field(default="default-project", alias="PROJECT_ID")
    INSTANCE_ID: str = Field(default="home-instance-1", alias="INSTANCE_ID")
    AUTH_PROVIDER: str | None = Field(default=None, alias="AUTH_PROVIDER")

    DEFAULT_MODEL_LLM_ID: str | None = Field(default="claude-3-7-sonnet-20250219", alias="DEFAULT_LLM_MODEL_ID")
    DEFAULT_EMBEDDER: str | None = "openai-text-embedding-3-small"

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

    # Bundle lifecycle settings — primary access via get_settings().PLATFORM.APPLICATIONS.*
    # Flat fields below are kept for backward-compat env-var reads on cloud deployments
    # where PLATFORM is not yet initialised (e.g. early startup checks).
    BUNDLE_CLEANUP_ENABLED: bool = Field(default=True)
    BUNDLE_CLEANUP_INTERVAL_SECONDS: int = Field(default=3600)
    BUNDLE_CLEANUP_LOCK_TTL_SECONDS: int = Field(default=900)
    BUNDLE_REF_TTL_SECONDS: int = Field(default=3600)
    BUNDLES_PRELOAD_LOCK_TTL_SECONDS: int = Field(default=900)
    BUNDLES_INCLUDE_EXAMPLES: bool = Field(default=True)
    BUNDLES_FORCE_ENV_ON_STARTUP: bool = Field(default=False)
    BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS: int = Field(default=60)
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
        descriptors_dir = str(getattr(self, "PLATFORM_DESCRIPTORS_DIR", None) or os.getenv("PLATFORM_DESCRIPTORS_DIR") or "").strip()
        descriptors_root = Path(descriptors_dir).expanduser() if descriptors_dir else None

        def _descriptor_file_uri(filename: str) -> str | None:
            if descriptors_root is None:
                return None
            return (descriptors_root / filename).resolve().as_uri()


        # 1. Override tenant/project from GATEWAY_CONFIG_JSON if present (backward compat).
        #    Track whether GATEWAY_CONFIG_JSON supplied them so assembly.yaml does not override.
        _gateway_json_set_tenant = False
        _gateway_json_set_project = False
        cfg_json = os.getenv("GATEWAY_CONFIG_JSON")
        if isinstance(cfg_json, str) and cfg_json.strip():
            try:
                import json
                cfg = json.loads(cfg_json)
                tenant = cfg.get("tenant_id") or cfg.get("tenant")
                project = cfg.get("project_id") or cfg.get("project")
                if tenant:
                    self.TENANT = tenant
                    _gateway_json_set_tenant = True
                if project:
                    self.PROJECT = project
                    _gateway_json_set_project = True
            except Exception:
                pass

        # 2. Parse CORS_CONFIG JSON (if provided); fall back to assembly cors.* section.
        if isinstance(self.CORS_CONFIG, str) and self.CORS_CONFIG.strip():
            try:
                import json
                self.CORS_CONFIG_OBJ = CorsConfig.model_validate(json.loads(self.CORS_CONFIG))
            except Exception:
                self.CORS_CONFIG_OBJ = None
        if self.CORS_CONFIG_OBJ is None:
            cors_data = _load_assembly_plain("cors")
            if cors_data and isinstance(cors_data, dict):
                try:
                    self.CORS_CONFIG_OBJ = CorsConfig.model_validate(cors_data)
                except Exception:
                    pass

        # 3. Read infra settings from assembly.yaml before building REDIS_URL,
        #    so the assembled values feed into the URL construction below.
        if not self._env_present("SECRETS_PROVIDER") and not self.SECRETS_PROVIDER:
            self.SECRETS_PROVIDER = self._assembly_str("secrets.provider")

        if not self._env_present("GLOBAL_SECRETS_YAML") and not self.GLOBAL_SECRETS_YAML:
            self.GLOBAL_SECRETS_YAML = _descriptor_file_uri("secrets.yaml")
        if not self._env_present("BUNDLE_SECRETS_YAML") and not self.BUNDLE_SECRETS_YAML:
            self.BUNDLE_SECRETS_YAML = _descriptor_file_uri("bundles.secrets.yaml")

        if not self._env_present("POSTGRES_HOST"):
            val = self._assembly_str("infra.postgres.host")
            if val:
                self.PGHOST = val
        if not self._env_present("POSTGRES_PORT"):
            val = self._assembly_int("infra.postgres.port")
            if val is not None:
                self.PGPORT = val
        if not self._env_present("POSTGRES_USER"):
            val = self._assembly_str("infra.postgres.user")
            if val:
                self.PGUSER = val
        if not self._env_present("POSTGRES_PASSWORD"):
            raw = _load_assembly_plain("infra.postgres.password")
            if raw is not None:
                self.PGPASSWORD = str(raw)
        if not self._env_present("POSTGRES_DATABASE"):
            val = self._assembly_str("infra.postgres.database")
            if val:
                self.PGDATABASE = val
        if not self._env_present("POSTGRES_SSL"):
            val = self._assembly_bool("infra.postgres.postgres_ssl")
            if val is not None:
                self.PGSSL = val

        if not self._env_present("REDIS_HOST"):
            val = self._assembly_str("infra.redis.host")
            if val:
                self.REDIS_HOST = val
        if not self._env_present("REDIS_PORT"):
            val = self._assembly_int("infra.redis.port")
            if val is not None:
                self.REDIS_PORT = val
        if not self._env_present("REDIS_PASSWORD"):
            raw = _load_assembly_plain("infra.redis.password")
            if raw is not None:
                self.REDIS_PASSWORD = str(raw) if str(raw).strip() else None

        # 4. Build REDIS_URL from components if not explicitly set in env.
        #    Must happen after infra reads so assembly.yaml host/port/password are reflected.
        if not os.getenv("REDIS_URL"):
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

        # 5. context: tenant/project from assembly.yaml when neither env var nor
        #    GATEWAY_CONFIG_JSON supplied them.
        if not self._env_present("TENANT_ID") and not _gateway_json_set_tenant:
            val = self._assembly_str("context.tenant")
            if val:
                self.TENANT = val
        if not self._env_present("PROJECT_ID") and not _gateway_json_set_project:
            val = self._assembly_str("context.project")
            if val:
                self.PROJECT = val

        # 6. auth provider
        if not self._env_present("AUTH_PROVIDER") and not self.AUTH_PROVIDER:
            self.AUTH_PROVIDER = self._assembly_str("auth.type")

        # 7. AWS settings
        if not self._env_present("AWS_REGION"):
            val = self._assembly_str("aws.aws_region")
            if val:
                self.AWS_REGION = val
        if not self._env_present("AWS_PROFILE") and not self.AWS_PROFILE:
            self.AWS_PROFILE = self._assembly_str("aws.aws_profile")

        # 8. Build PLATFORM nested config (per-service reads from assembly.yaml).
        component = (self.GATEWAY_COMPONENT or "").strip().lower()
        svc = f"platform.services.{component}"
        log_p = f"{svc}.log"
        svc_p = f"{svc}.service"
        av_p = f"{svc}.av"
        idp_p = f"{svc}.idp"
        mon_p = f"{svc}.monitoring"
        exec_p = f"{svc}.exec"
        bundles_p = f"{svc}.bundles"
        git_p = f"{bundles_p}.git"

        self.PLATFORM = PlatformConfig(
            LOG=LOGConfig(
                LOG_LEVEL=self._resolve_str("LOG_LEVEL", f"{log_p}.log_level", "INFO"),
                LOG_MAX_MB=self._resolve_int("LOG_MAX_MB", f"{log_p}.log_max_mb", 20),
                LOG_BACKUP_COUNT=self._resolve_int("LOG_BACKUP_COUNT", f"{log_p}.log_backup_count", 10),
                LOG_DIR=self._resolve_str("LOG_DIR", f"{log_p}.log_dir"),
                LOG_FILE_PREFIX=self._resolve_str("LOG_FILE_PREFIX", f"{log_p}.log_file_prefix"),
            ),
            SERVICE=ServiceConfig(
                UVICORN_RELOAD=self._resolve_bool("UVICORN_RELOAD", f"{svc_p}.uvicorn_reload", False),
                HEARTBEAT_INTERVAL=self._resolve_int("HEARTBEAT_INTERVAL", f"{svc_p}.heartbeat_interval", 5),
                CB_RELAY_IDENTITY=self._resolve_str("CB_RELAY_IDENTITY", f"{svc_p}.cb_relay_identity"),
                CHAT_SCHEDULER_BACKEND=self._resolve_str("CHAT_SCHEDULER_BACKEND", f"{svc_p}.chat_scheduler_backend", "legacy_lists"),
                CHAT_TASK_TIMEOUT_SEC=self._resolve_int("CHAT_TASK_TIMEOUT_SEC", f"{svc_p}.chat_task_timeout_sec", 600),
                CHAT_TASK_IDLE_TIMEOUT_SEC=self._resolve_int("CHAT_TASK_IDLE_TIMEOUT_SEC", f"{svc_p}.chat_task_idle_timeout_sec", 600),
                CHAT_TASK_MAX_WALL_TIME_SEC=self._resolve_int("CHAT_TASK_MAX_WALL_TIME_SEC", f"{svc_p}.chat_task_max_wall_time_sec", 2400),
                CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC=self._resolve_float("CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC", f"{svc_p}.chat_task_watchdog_poll_interval_sec", 1.0),
            ),
            HOSTED_SERVICES=HostedServicesConfig(
                AV=AVConfig(
                    APP_AV_SCAN=self._resolve_bool("APP_AV_SCAN", f"{av_p}.app_av_scan", True),
                    APP_AV_TIMEOUT_S=self._resolve_float("APP_AV_TIMEOUT_S", f"{av_p}.app_av_timeout_s", 3.0),
                    CLAMAV_HOST=self._resolve_str("CLAMAV_HOST", f"{av_p}.clamav_host", "localhost"),
                    CLAMAV_PORT=self._resolve_int("CLAMAV_PORT", f"{av_p}.clamav_port", 3310),
                ),
            ),
            MONITORING=MonitoringConfig(
                MONITORING_BURST_ENABLE=self._resolve_bool("MONITORING_BURST_ENABLE", f"{mon_p}.monitoring_burst_enable", True),
            ),
            EXEC=ExecConfig(
                EXEC_WORKSPACE_ROOT=self._resolve_str("EXEC_WORKSPACE_ROOT", f"{exec_p}.exec_workspace_root"),
                PY=PyExecConfig(
                    PY_CODE_EXEC_IMAGE=self._resolve_str("PY_CODE_EXEC_IMAGE", f"{exec_p}.py_code_exec_image", "py-code-exec:latest"),
                    PY_CODE_EXEC_TIMEOUT=self._resolve_int("PY_CODE_EXEC_TIMEOUT", f"{exec_p}.py_code_exec_timeout", 600),
                    PY_CODE_EXEC_NETWORK_MODE=self._resolve_str("PY_CODE_EXEC_NETWORK_MODE", f"{exec_p}.py_code_exec_network_mode", "host"),
                ),
            ),
            ACCOUNTING=AccountingConfig(
                ACCOUNTING_SERVICES=self._resolve_str("ACCOUNTING_SERVICES", f"{svc}.tools.accounting_services"),
            ),
            APPLICATIONS=ApplicationsConfig(
                AGENTIC_BUNDLES_ROOT=self._resolve_str("AGENTIC_BUNDLES_ROOT", f"{bundles_p}.agentic_bundles_root", "/bundles"),
                BUNDLE_STORAGE_ROOT=self._resolve_str("BUNDLE_STORAGE_ROOT", f"{bundles_p}.bundle_storage_root"),
                BUNDLES_INCLUDE_EXAMPLES=self._resolve_bool("BUNDLES_INCLUDE_EXAMPLES", f"{bundles_p}.bundles_include_examples", True),
                BUNDLE_CLEANUP_ENABLED=self._resolve_bool("BUNDLE_CLEANUP_ENABLED", f"{bundles_p}.bundle_cleanup_enabled", True),
                BUNDLE_CLEANUP_INTERVAL_SECONDS=self._resolve_int("BUNDLE_CLEANUP_INTERVAL_SECONDS", f"{bundles_p}.bundle_cleanup_interval_seconds", 3600),
                BUNDLE_CLEANUP_LOCK_TTL_SECONDS=self._resolve_int("BUNDLE_CLEANUP_LOCK_TTL_SECONDS", f"{bundles_p}.bundle_cleanup_lock_ttl_seconds", 900),
                BUNDLE_REF_TTL_SECONDS=self._resolve_int("BUNDLE_REF_TTL_SECONDS", f"{bundles_p}.bundle_ref_ttl_seconds", 3600),
                BUNDLES_FORCE_ENV_ON_STARTUP=self._resolve_bool("BUNDLES_FORCE_ENV_ON_STARTUP", f"{bundles_p}.bundles_force_env_on_startup", False),
                BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS=self._resolve_int("BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS", f"{bundles_p}.bundles_force_env_lock_ttl_seconds", 60),
                BUNDLES_PRELOAD_ON_START=self._resolve_bool("BUNDLES_PRELOAD_ON_START", f"{bundles_p}.bundles_preload_on_start", False),
                BUNDLES_PRELOAD_LOCK_TTL_SECONDS=self._resolve_int("BUNDLES_PRELOAD_LOCK_TTL_SECONDS", f"{bundles_p}.bundles_preload_lock_ttl_seconds", 900),
                GIT=GitBundlesConfig(
                    BUNDLE_GIT_RESOLUTION_ENABLED=self._resolve_bool("BUNDLE_GIT_RESOLUTION_ENABLED", f"{git_p}.bundle_git_resolution_enabled", True),
                    BUNDLE_GIT_ATOMIC=self._resolve_bool("BUNDLE_GIT_ATOMIC", f"{git_p}.bundle_git_atomic", True),
                    BUNDLE_GIT_ALWAYS_PULL=self._resolve_bool("BUNDLE_GIT_ALWAYS_PULL", f"{git_p}.bundle_git_always_pull", False),
                    BUNDLE_GIT_REDIS_LOCK=self._resolve_bool("BUNDLE_GIT_REDIS_LOCK", f"{git_p}.bundle_git_redis_lock", True),
                    BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS=self._resolve_int("BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS", f"{git_p}.bundle_git_redis_lock_ttl_seconds", 300),
                    BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS=self._resolve_int("BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS", f"{git_p}.bundle_git_redis_lock_wait_seconds", 60),
                    BUNDLE_GIT_PREFETCH_ENABLED=self._resolve_bool("BUNDLE_GIT_PREFETCH_ENABLED", f"{git_p}.bundle_git_prefetch_enabled", True),
                    BUNDLE_GIT_PREFETCH_INTERVAL_SECONDS=self._resolve_int("BUNDLE_GIT_PREFETCH_INTERVAL_SECONDS", f"{git_p}.bundle_git_prefetch_interval_seconds", 15),
                    BUNDLE_GIT_FAIL_BACKOFF_SECONDS=self._resolve_int("BUNDLE_GIT_FAIL_BACKOFF_SECONDS", f"{git_p}.bundle_git_fail_backoff_seconds", 60),
                    BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS=self._resolve_int("BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS", f"{git_p}.bundle_git_fail_max_backoff_seconds", 300),
                    BUNDLE_GIT_KEEP=self._resolve_int("BUNDLE_GIT_KEEP", f"{git_p}.bundle_git_keep", 3),
                    BUNDLE_GIT_TTL_HOURS=self._resolve_int("BUNDLE_GIT_TTL_HOURS", f"{git_p}.bundle_git_ttl_hours", 0),
                    GIT_SSH_KEY_PATH=self._resolve_str("GIT_SSH_KEY_PATH", f"{git_p}.git_ssh_key_path"),
                    GIT_SSH_KNOWN_HOSTS=self._resolve_str("GIT_SSH_KNOWN_HOSTS", f"{git_p}.git_ssh_known_hosts"),
                    GIT_SSH_STRICT_HOST_KEY_CHECKING=self._resolve_str("GIT_SSH_STRICT_HOST_KEY_CHECKING", f"{git_p}.git_ssh_strict_host_key_checking", "yes"),
                ),
            ),
        )

        # Service port from assembly.yaml based on GATEWAY_COMPONENT.
        _port_key = {"ingress": "ports.ingress", "proc": "ports.proc",
                     "processor": "ports.proc", "metrics": "ports.metrics"}.get(component)
        if _port_key and not self._env_present("CHAT_APP_PORT") and not self._env_present("CHAT_PROCESSOR_PORT") \
                and not self._env_present("METRICS_PORT"):
            val = self._assembly_int(_port_key)
            if val is not None:
                self.PORT = val

        # CHAT_APP_PORT / CHAT_PROCESSOR_PORT — explicit per-service port fields (always from assembly).
        if not self._env_present("CHAT_APP_PORT"):
            val = self._assembly_int("ports.ingress")
            if val is not None:
                self.CHAT_APP_PORT = val
        if not self._env_present("CHAT_PROCESSOR_PORT"):
            val = self._assembly_int("ports.proc")
            if val is not None:
                self.CHAT_PROCESSOR_PORT = val

        # Default LLM model (env: DEFAULT_LLM_MODEL_ID, assembly: models.default_llm_model_id).
        if not self._env_present("DEFAULT_LLM_MODEL_ID"):
            val = self._assembly_str("models.default_llm_model_id")
            if val:
                self.DEFAULT_MODEL_LLM_ID = val

        # 9. Storage / workspace settings from assembly.yaml.
        if not self._env_present("KDCUBE_STORAGE_PATH") and not self.STORAGE_PATH:
            self.STORAGE_PATH = self._assembly_str("storage.kdcube")
        if not self._env_present("CB_BUNDLE_STORAGE_URL") and not self.BUNDLE_STORAGE_URL:
            self.BUNDLE_STORAGE_URL = self._assembly_str("storage.bundles")
        if not self._env_present("REACT_WORKSPACE_IMPLEMENTATION"):
            self.REACT_WORKSPACE_IMPLEMENTATION = str(
                self._assembly_str("storage.workspace.type") or self.REACT_WORKSPACE_IMPLEMENTATION
            )
        self.AI_REACT_AGENT_VERSION = (
            str(self._resolve_str("AI_REACT_AGENT_VERSION", "ai.react.react_agent_version", "v2") or "v2").strip().lower() or "v2"
        )
        if self.AI_REACT_AGENT_VERSION not in {"v2", "v3"}:
            self.AI_REACT_AGENT_VERSION = "v2"
        self.AI_REACT_AGENT_MULTI_ACTION = (
            self._resolve_str("AI_REACT_AGENT_MULTI_ACTION", "ai.react.react_agent_multiaction", "off") or "off"
        )
        if not self._env_present("REACT_WORKSPACE_GIT_REPO") and not self.REACT_WORKSPACE_GIT_REPO:
            self.REACT_WORKSPACE_GIT_REPO = self._assembly_str("storage.workspace.repo")
        if not self._env_present("CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION"):
            self.CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION = str(
                self._assembly_str("storage.claude_code_session.type")
                or self.CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION
            )
        if not self._env_present("CLAUDE_CODE_SESSION_GIT_REPO") and not self.CLAUDE_CODE_SESSION_GIT_REPO:
            self.CLAUDE_CODE_SESSION_GIT_REPO = self._assembly_str("storage.claude_code_session.repo")

        # 10. Routines / scheduler settings from assembly.yaml.
        if not self._env_present("OPEX_AGG_CRON"):
            val = self._assembly_str("routines.opex.agg_cron")
            if val:
                self.OPEX_AGG_CRON = val
        if not self._env_present("SUBSCRIPTION_ROLLOVER_ENABLED"):
            val = self._assembly_bool("routines.economics.subscription_rollover_enabled")
            if val is not None:
                self.SUBSCRIPTION_ROLLOVER_ENABLED = val
        if not self._env_present("SUBSCRIPTION_ROLLOVER_CRON"):
            val = self._assembly_str("routines.economics.subscription_rollover_cron")
            if val:
                self.SUBSCRIPTION_ROLLOVER_CRON = val
        if not self._env_present("SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS"):
            val = self._assembly_int("routines.economics.subscription_rollover_lock_ttl_seconds")
            if val is not None:
                self.SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS = val
        if not self._env_present("SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT"):
            val = self._assembly_int("routines.economics.subscription_rollover_sweep_limit")
            if val is not None:
                self.SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT = val
        if not self._env_present("STRIPE_RECONCILE_ENABLED"):
            val = self._assembly_bool("routines.stripe.reconcile_enabled")
            if val is not None:
                self.STRIPE_RECONCILE_ENABLED = val
        if not self._env_present("STRIPE_RECONCILE_CRON"):
            val = self._assembly_str("routines.stripe.reconcile_cron")
            if val:
                self.STRIPE_RECONCILE_CRON = val
        if not self._env_present("STRIPE_RECONCILE_LOCK_TTL_SECONDS"):
            val = self._assembly_int("routines.stripe.reconcile_lock_ttl_seconds")
            if val is not None:
                self.STRIPE_RECONCILE_LOCK_TTL_SECONDS = val

        # 11. Email notification settings from assembly.yaml.
        if not self._env_present("EMAIL_ENABLED"):
            val = self._assembly_bool("notifications.email.enabled")
            if val is not None:
                self.EMAIL_ENABLED = val
        if not self._env_present("EMAIL_HOST") and not self.EMAIL_HOST:
            self.EMAIL_HOST = self._assembly_str("notifications.email.host")
        if not self._env_present("EMAIL_PORT"):
            val = self._assembly_int("notifications.email.port")
            if val is not None:
                self.EMAIL_PORT = val
        if not self._env_present("EMAIL_USER") and not self.EMAIL_USER:
            self.EMAIL_USER = self._assembly_str("notifications.email.user")
        if not self._env_present("EMAIL_FROM") and not self.EMAIL_FROM:
            self.EMAIL_FROM = self._assembly_str("notifications.email.from")
        if not self._env_present("EMAIL_TO"):
            val = self._assembly_str("notifications.email.to")
            if val:
                self.EMAIL_TO = val
        if not self._env_present("EMAIL_USE_TLS"):
            val = self._assembly_bool("notifications.email.use_tls")
            if val is not None:
                self.EMAIL_USE_TLS = val

        # 12. Populate secrets from provider if not set in env.
        env_openai = os.getenv("OPENAI_API_KEY")
        env_anthropic = os.getenv("ANTHROPIC_API_KEY")
        env_gemini = os.getenv("GEMINI_API_KEY")
        env_brave = os.getenv("BRAVE_API_KEY")
        env_git_token = os.getenv("GIT_HTTP_TOKEN")
        env_git_user = os.getenv("GIT_HTTP_USER")
        env_openrouter = os.getenv("OPENROUTER_API_KEY")

        if not self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = self._fetch_secret("services.openai.api_key") or self._fetch_secret("OPENAI_API_KEY")
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = self._fetch_secret("services.anthropic.api_key") or self._fetch_secret("ANTHROPIC_API_KEY")
        if not self.GOOGLE_API_KEY:
            self.GOOGLE_API_KEY = (
                self._fetch_secret("services.google.api_key")
                or self._fetch_secret("GOOGLE_API_KEY")
                or self._fetch_secret("GEMINI_API_KEY")
            )
        if not self.BRAVE_API_KEY:
            self.BRAVE_API_KEY = self._fetch_secret("services.brave.api_key") or self._fetch_secret("BRAVE_API_KEY")
        if not self.GIT_HTTP_TOKEN:
            self.GIT_HTTP_TOKEN = self._fetch_secret("services.git.http_token") or self._fetch_secret("GIT_HTTP_TOKEN")
        if not self.GIT_HTTP_USER and self.GIT_HTTP_TOKEN:
            self.GIT_HTTP_USER = self._fetch_secret("services.git.http_user") or "x-access-token"
        if not self.OPENROUTER_API_KEY:
            self.OPENROUTER_API_KEY = self._fetch_secret("services.openrouter.api_key") or self._fetch_secret("OPENROUTER_API_KEY")
        if not self.OPENROUTER_BASE_URL:
            self.OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
        if not self.CLAUDE_CODE_KEY:
            self.CLAUDE_CODE_KEY = self._fetch_secret("services.anthropic.claude_code_key") or self._fetch_secret("CLAUDE_CODE_KEY")

        _log_secret_status("services.openai.api_key", self.OPENAI_API_KEY, "env" if env_openai else "secrets")
        _log_secret_status("services.anthropic.api_key", self.ANTHROPIC_API_KEY, "env" if env_anthropic else "secrets")
        _log_secret_status("services.google.api_key", self.GOOGLE_API_KEY, "env" if env_gemini else "secrets")
        _log_secret_status("services.brave.api_key", self.BRAVE_API_KEY, "env" if env_brave else "secrets")
        _log_secret_status("services.git.http_token", self.GIT_HTTP_TOKEN, "env" if env_git_token else "secrets")
        _log_secret_status("services.git.http_user", self.GIT_HTTP_USER, "env" if env_git_user else "secrets")
        _log_secret_status("services.openrouter.api_key", self.OPENROUTER_API_KEY, "env" if env_openrouter else "secrets")

        # 13. Build AUTH config (env > assembly.yaml > default).
        self.AUTH = AuthConfig(
            COGNITO_REGION=self._resolve_str("COGNITO_REGION", "auth.cognito.region"),
            COGNITO_USER_POOL_ID=self._resolve_str("COGNITO_USER_POOL_ID", "auth.cognito.user_pool_id"),
            COGNITO_APP_CLIENT_ID=self._resolve_str("COGNITO_APP_CLIENT_ID", "auth.cognito.app_client_id"),
            COGNITO_SERVICE_CLIENT_ID=self._resolve_str("COGNITO_SERVICE_CLIENT_ID", "auth.cognito.service_client_id"),
            ID_TOKEN_HEADER_NAME=self._resolve_str("ID_TOKEN_HEADER_NAME", "auth.id_token_header_name", "X-ID-Token"),
            AUTH_TOKEN_COOKIE_NAME=self._resolve_str("AUTH_TOKEN_COOKIE_NAME", "auth.auth_token_cookie_name", "__Secure-LATC"),
            ID_TOKEN_COOKIE_NAME=self._resolve_str("ID_TOKEN_COOKIE_NAME", "auth.id_token_cookie_name", "__Secure-LITC"),
            JWKS_CACHE_TTL_SECONDS=self._resolve_int("JWKS_CACHE_TTL_SECONDS", "auth.jwks_cache_ttl_seconds", 86400),
            OIDC_SERVICE_USER_EMAIL=self._fetch_secret("auth.oidc.admin_email") or self._env_str("OIDC_SERVICE_USER_EMAIL"),
            OIDC_SERVICE_ADMIN_USERNAME=self._fetch_secret("auth.oidc.admin_username") or self._env_str("OIDC_SERVICE_ADMIN_USERNAME"),
            OIDC_SERVICE_ADMIN_PASSWORD=self._fetch_secret("auth.oidc.admin_password") or self._env_str("OIDC_SERVICE_ADMIN_PASSWORD"),
            IDP=IDPConfig(
                local=IDPLocalConfig(
                    IDP_DB_PATH=self._resolve_str("IDP_DB_PATH", f"{svc}.idp.idp_db_path"),
                    IDP_IMPORT_ENABLED=self._resolve_bool("IDP_IMPORT_ENABLED", f"{svc}.idp.idp_import_enabled", False),
                    IDP_IMPORT_RUN_AT=self._resolve_str("IDP_IMPORT_RUN_AT", f"{svc}.idp.idp_import_run_at"),
                    IDP_IMPORT_SCRIPT_PATH=self._resolve_str("IDP_IMPORT_SCRIPT_PATH", f"{svc}.idp.idp_import_script_path"),
                ),
            ),
        )

        # 14. Build SERVICES config.
        self.SERVICES = ServicesConfig(
            DEFAULT_EMBEDDING_MODEL_ID=self._resolve_str("DEFAULT_EMBEDDING_MODEL_ID", "models.default_embedding_model_id"),
        )

        # 15. Build RUNTIME_CONFIG (request-context header names).
        # RUNTIME_CONFIG inherits PLATFORM_CONFIG (BaseSettings) so env vars are
        # picked up automatically by Pydantic — no manual resolution needed here.
        self.RUNTIME_CONFIG = RUNTIME_CONFIG()

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

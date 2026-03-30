# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chatbot/sdk/config.py
from __future__ import annotations
import os
import logging
from pydantic import Field
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

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


def get_secret(key: str, default: str | None = None) -> str | None:
    settings = get_settings()
    for candidate in _secret_candidates(key):
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
            self.GIT_HTTP_USER = _fetch_secret("services.git.http_user") or _fetch_secret("GIT_HTTP_USER") or "x-access-token"
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
        env_val = os.getenv(key)
        if env_val:
            return env_val
        try:
            value = get_secrets_manager(self).get_secret(key)
        except Exception:
            value = None
        return value or default

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

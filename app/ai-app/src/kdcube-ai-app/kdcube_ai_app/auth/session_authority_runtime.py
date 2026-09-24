# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Process binding for descriptor-selected durable session authorities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from connection_hub.delegated_credentials.authority_config import (
    AUTHORITY_BACKEND_POSTGRESQL,
    AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE,
    DurableAuthorityConfig,
)
from connection_hub.delegated_credentials.authority_cutover import (
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_USERS,
    FAMILY_PLATFORM_SESSIONS,
    PostgresAuthorityCutoverStore,
)
if TYPE_CHECKING:
    from kdcube_ai_app.auth.bundle.session_store import BundleSessionStore
    from kdcube_ai_app.auth.platform_session_store import PlatformSessionStore

KDCUBE_SESSION_AUTHORITY_FAMILIES = (
    FAMILY_BUNDLE_USERS,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_PLATFORM_SESSIONS,
)


class SessionAuthorityUnavailable(RuntimeError):
    """The selected authority generation is not ready to serve requests."""


@dataclass(frozen=True)
class SessionAuthoritySnapshot:
    tenant: str
    project: str
    config: DurableAuthorityConfig
    ready: bool


@dataclass
class _SessionAuthorityBinding:
    config: DurableAuthorityConfig
    bundle_store: BundleSessionStore | None = None
    platform_store: PlatformSessionStore | None = None
    ready: bool = False


_Scope = tuple[str, str]
_BINDINGS: dict[_Scope, _SessionAuthorityBinding] = {}
_DEFAULT_SCOPE: _Scope | None = None
_DEFAULT_SCOPE_AMBIGUOUS = False


def _scope(tenant: str | None, project: str | None) -> _Scope:
    return (
        str(tenant or "").strip() or "default",
        str(project or "").strip() or "default",
    )


def _record_default_scope(scope: _Scope) -> None:
    global _DEFAULT_SCOPE, _DEFAULT_SCOPE_AMBIGUOUS
    if _DEFAULT_SCOPE is None and not _DEFAULT_SCOPE_AMBIGUOUS:
        _DEFAULT_SCOPE = scope
    elif _DEFAULT_SCOPE != scope:
        _DEFAULT_SCOPE = None
        _DEFAULT_SCOPE_AMBIGUOUS = True


def _configured_scope(
    tenant: str | None,
    project: str | None,
) -> _Scope | None:
    if str(tenant or "").strip() or str(project or "").strip():
        default = _DEFAULT_SCOPE
        return _scope(
            tenant or (default[0] if default is not None else None),
            project or (default[1] if default is not None else None),
        )
    if _DEFAULT_SCOPE_AMBIGUOUS:
        raise SessionAuthorityUnavailable(
            "session_authority_scope_is_ambiguous"
        )
    return _DEFAULT_SCOPE


def configure_session_authority(
    *,
    tenant: str,
    project: str,
    config: DurableAuthorityConfig,
) -> SessionAuthoritySnapshot:
    """Declare the selected generation before any asynchronous preparation."""

    scope = _scope(tenant, project)
    existing = _BINDINGS.get(scope)
    if existing is not None and existing.config != config:
        raise SessionAuthorityUnavailable(
            "session_authority_configuration_changed_in_process"
        )
    if existing is None:
        existing = _SessionAuthorityBinding(config=config)
        _BINDINGS[scope] = existing
    if config.backend == AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE:
        existing.bundle_store = None
        existing.platform_store = None
        existing.ready = True
    _record_default_scope(scope)
    return SessionAuthoritySnapshot(
        tenant=scope[0],
        project=scope[1],
        config=existing.config,
        ready=existing.ready,
    )


def activate_session_authority_stores(
    *,
    tenant: str,
    project: str,
    config: DurableAuthorityConfig,
    bundle_store: BundleSessionStore,
    platform_store: PlatformSessionStore,
) -> SessionAuthoritySnapshot:
    """Publish both prepared stores together for all existing consumers."""

    snapshot = configure_session_authority(
        tenant=tenant,
        project=project,
        config=config,
    )
    if config.backend != AUTHORITY_BACKEND_POSTGRESQL:
        raise SessionAuthorityUnavailable(
            "session_authority_postgresql_not_selected"
        )
    scope = (snapshot.tenant, snapshot.project)
    binding = _BINDINGS[scope]
    binding.bundle_store = bundle_store
    binding.platform_store = platform_store
    binding.ready = True
    return SessionAuthoritySnapshot(
        tenant=scope[0],
        project=scope[1],
        config=binding.config,
        ready=True,
    )


def _resolved_binding(
    tenant: str | None,
    project: str | None,
) -> _SessionAuthorityBinding | None:
    scope = _configured_scope(tenant, project)
    binding = _BINDINGS.get(scope) if scope is not None else None
    if binding is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings

            selected = session_authority_config_from_settings(get_settings())
        except SessionAuthorityUnavailable:
            raise
        except Exception as exc:
            raise SessionAuthorityUnavailable(
                "session_authority_configuration_unavailable"
            ) from exc
        if selected.backend == AUTHORITY_BACKEND_POSTGRESQL:
            raise SessionAuthorityUnavailable(
                "session_authority_postgresql_store_not_bound"
            )
        return None
    if not binding.ready:
        raise SessionAuthorityUnavailable(
            "session_authority_generation_not_prepared"
        )
    return binding


def bundle_session_store_for(
    *,
    tenant: str | None,
    project: str | None,
) -> BundleSessionStore | None:
    binding = _resolved_binding(tenant, project)
    return binding.bundle_store if binding is not None else None


def platform_session_store_for(
    *,
    tenant: str | None,
    project: str | None,
) -> PlatformSessionStore | None:
    binding = _resolved_binding(tenant, project)
    return binding.platform_store if binding is not None else None


def session_authority_config_from_settings(settings: Any) -> DurableAuthorityConfig:
    auth = getattr(settings, "AUTH", None)
    sessions = getattr(auth, "SESSIONS", None)
    authority = getattr(sessions, "AUTHORITY", None)
    return DurableAuthorityConfig.from_mapping(
        {
            "backend": getattr(authority, "BACKEND", ""),
            "generation_id": getattr(authority, "GENERATION_ID", ""),
        },
        field_path="auth.sessions.authority",
    )


async def prepare_configured_session_authority(
    *,
    pg_pool: Any,
    settings: Any,
    redis: Any | None = None,
) -> SessionAuthoritySnapshot:
    """Prepare and atomically expose durable stores plus Redis projections."""

    config = session_authority_config_from_settings(settings)
    tenant = str(getattr(settings, "TENANT", "") or "").strip() or "default"
    project = str(getattr(settings, "PROJECT", "") or "").strip() or "default"
    snapshot = configure_session_authority(
        tenant=tenant,
        project=project,
        config=config,
    )
    if config.backend == AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE:
        return snapshot
    if pg_pool is None:
        raise SessionAuthorityUnavailable(
            "postgresql_session_authority_requires_pg_pool"
        )
    projection_redis = redis
    if projection_redis is None:
        redis_url = str(getattr(settings, "REDIS_URL", "") or "").strip()
        if not redis_url:
            raise SessionAuthorityUnavailable(
                "durable_session_authority_requires_redis_projection"
            )
        from kdcube_ai_app.infra.redis.client import get_async_redis_client

        projection_redis = get_async_redis_client(redis_url)

    from kdcube_ai_app.auth.bundle.session_projection import (
        ProjectedBundleSessionStore,
        RedisBundleSessionProjection,
    )
    from kdcube_ai_app.auth.bundle.session_store import (
        PostgresBundleSessionStore,
    )
    from kdcube_ai_app.auth.platform_session_projection import (
        ProjectedPlatformSessionStore,
        RedisPlatformSessionProjection,
    )
    from kdcube_ai_app.auth.platform_session_store import (
        PostgresPlatformSessionStore,
    )

    scope = (snapshot.tenant, snapshot.project)
    binding = _BINDINGS[scope]
    if binding.ready:
        return snapshot

    bundle_authority = PostgresBundleSessionStore(
        pg_pool=pg_pool,
        tenant=scope[0],
        project=scope[1],
    )
    platform_authority = PostgresPlatformSessionStore(
        pg_pool=pg_pool,
        tenant=scope[0],
        project=scope[1],
    )
    cutovers = PostgresAuthorityCutoverStore(
        pg_pool=pg_pool,
        tenant=scope[0],
        project=scope[1],
    )
    await bundle_authority.ensure_schema()
    await platform_authority.ensure_schema()
    await cutovers.ensure_schema()
    await cutovers.require_activated(
        config.generation_id,
        required_families=KDCUBE_SESSION_AUTHORITY_FAMILIES,
    )
    bundle_store = ProjectedBundleSessionStore(
        authority=bundle_authority,
        projection=RedisBundleSessionProjection(
            projection_redis,
            tenant=scope[0],
            project=scope[1],
            generation_id=config.generation_id,
        ),
    )
    platform_store = ProjectedPlatformSessionStore(
        authority=platform_authority,
        projection=RedisPlatformSessionProjection(
            projection_redis,
            tenant=scope[0],
            project=scope[1],
            generation_id=config.generation_id,
        ),
    )
    return activate_session_authority_stores(
        tenant=scope[0],
        project=scope[1],
        config=config,
        bundle_store=bundle_store,
        platform_store=platform_store,
    )


async def open_configured_session_authority(
    *,
    settings: Any,
) -> tuple[SessionAuthoritySnapshot, Any | None]:
    """Open a small owned PostgreSQL pool for a standalone service."""

    config = session_authority_config_from_settings(settings)
    if config.backend == AUTHORITY_BACKEND_REDIS_MIGRATION_SOURCE:
        snapshot = await prepare_configured_session_authority(
            pg_pool=None,
            settings=settings,
            redis=None,
        )
        return snapshot, None

    import asyncpg

    from kdcube_ai_app.apps.chat.sdk.config import resolve_asyncpg_ssl

    async def _init_connection(connection: Any) -> None:
        await connection.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await connection.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    pool = await asyncpg.create_pool(
        host=settings.PGHOST,
        port=settings.PGPORT,
        user=settings.PGUSER,
        password=settings.PGPASSWORD,
        database=settings.PGDATABASE,
        ssl=resolve_asyncpg_ssl(settings),
        init=_init_connection,
        min_size=0,
        max_size=2,
    )
    try:
        snapshot = await prepare_configured_session_authority(
            pg_pool=pool,
            settings=settings,
        )
    except Exception:
        await pool.close()
        raise
    return snapshot, pool


def reset_session_authority_runtime_for_tests() -> None:
    global _DEFAULT_SCOPE, _DEFAULT_SCOPE_AMBIGUOUS
    _BINDINGS.clear()
    _DEFAULT_SCOPE = None
    _DEFAULT_SCOPE_AMBIGUOUS = False


__all__ = [
    "KDCUBE_SESSION_AUTHORITY_FAMILIES",
    "SessionAuthoritySnapshot",
    "SessionAuthorityUnavailable",
    "activate_session_authority_stores",
    "bundle_session_store_for",
    "configure_session_authority",
    "platform_session_store_for",
    "open_configured_session_authority",
    "prepare_configured_session_authority",
    "reset_session_authority_runtime_for_tests",
    "session_authority_config_from_settings",
]

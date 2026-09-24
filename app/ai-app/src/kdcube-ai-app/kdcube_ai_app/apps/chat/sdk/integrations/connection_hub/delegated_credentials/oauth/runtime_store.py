# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Bind Connection Hub's selected OAuth authority to a KDCube request."""

from __future__ import annotations

from typing import Any, Mapping

from connection_hub.delegated_credentials.authority_config import (
    AUTHORITY_BACKEND_POSTGRESQL,
    DurableAuthorityConfig,
)
from connection_hub.delegated_credentials.oauth.runtime_store import (
    OAuthGrantStoreProvider,
)
from connection_hub.delegated_credentials.oauth.store import (
    GrantStore,
    GrantStoreUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
    oauth_delegated_config,
)
from kdcube_ai_app.infra.plugin.bundle_store import (
    _get_bundle_props_from_authority as get_bundle_props_from_authority,
)

DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"


def _states(request: Any) -> tuple[Any, ...]:
    return (
        getattr(request, "state", None),
        getattr(getattr(request, "app", None), "state", None),
    )


def _scope(
    request: Any,
    *,
    tenant: str | None,
    project: str | None,
) -> tuple[str, str]:
    if tenant and project:
        return str(tenant).strip(), str(project).strip()
    config = oauth_delegated_config(request)
    return (
        str(tenant or config.tenant or "default").strip(),
        str(project or config.project or "default").strip(),
    )


def _connections(
    request: Any,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    connections: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if connections is not None:
        return connections
    for state in _states(request):
        candidate = getattr(state, "oauth_authority_connections", None)
        if isinstance(candidate, Mapping):
            return candidate
    props = get_bundle_props_from_authority(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    if props is None:
        raise GrantStoreUnavailable(
            "selected_authority.configuration_unavailable"
        )
    if not isinstance(props, Mapping):
        return {}
    candidate = props.get("connections")
    return candidate if isinstance(candidate, Mapping) else {}


def selected_oauth_authority_config(
    request: Any,
    *,
    tenant: str | None = None,
    project: str | None = None,
    bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    connections: Mapping[str, Any] | None = None,
) -> tuple[DurableAuthorityConfig, str, str, Mapping[str, Any]]:
    """Return the process-selected authority and its descriptor scope."""

    resolved_tenant, resolved_project = _scope(
        request,
        tenant=tenant,
        project=project,
    )
    for state in _states(request):
        backend = str(getattr(state, "oauth_authority_backend", "") or "").strip()
        if not backend:
            continue
        config = DurableAuthorityConfig.from_mapping(
            {
                "backend": backend,
                "generation_id": getattr(
                    state,
                    "oauth_authority_generation_id",
                    "",
                ),
            },
            field_path="request.oauth_authority",
        )
        return config, resolved_tenant, resolved_project, {}

    resolved_connections = _connections(
        request,
        tenant=resolved_tenant,
        project=resolved_project,
        bundle_id=bundle_id,
        connections=connections,
    )
    return (
        DurableAuthorityConfig.from_connections(resolved_connections),
        resolved_tenant,
        resolved_project,
        resolved_connections,
    )


def _bound_store(request: Any) -> GrantStore | None:
    for state in _states(request):
        store = getattr(state, "oauth_grant_store", None)
        if store is not None:
            return store
    return None


def _bind_selection(
    request: Any,
    *,
    config: DurableAuthorityConfig,
    store: GrantStore | None = None,
    unavailable_reason: str = "",
) -> None:
    state = getattr(request, "state", None)
    if state is None:
        return
    state.oauth_grant_store_required = True
    state.oauth_authority_backend = config.backend
    state.oauth_authority_generation_id = config.generation_id
    state.oauth_grant_store_unavailable_reason = unavailable_reason
    if store is not None:
        state.oauth_grant_store = store


def _redis(request: Any, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    app_state = getattr(getattr(request, "app", None), "state", None)
    value = getattr(app_state, "redis_async", None)
    if value is not None:
        return value
    from kdcube_ai_app.infra.redis.client import get_async_redis_client

    try:
        return get_async_redis_client(get_settings().REDIS_URL)
    except Exception as exc:
        raise GrantStoreUnavailable(
            "selected_authority.redis_projection_unavailable"
        ) from exc


async def resolve_request_oauth_grant_store(
    request: Any,
    *,
    redis: Any = None,
    pg_pool: Any = None,
    tenant: str | None = None,
    project: str | None = None,
    bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    connections: Mapping[str, Any] | None = None,
) -> GrantStore:
    """Resolve and bind the selected store, checking PostgreSQL readiness."""

    existing = _bound_store(request)
    if existing is not None:
        return existing
    try:
        config, resolved_tenant, resolved_project, _ = selected_oauth_authority_config(
            request,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            connections=connections,
        )
        app_state = getattr(getattr(request, "app", None), "state", None)
        provider = OAuthGrantStoreProvider.from_config(
            config=config,
            redis=_redis(request, redis),
            pg_pool=(
                pg_pool
                if pg_pool is not None
                else getattr(app_state, "pg_pool", None)
            ),
            tenant=resolved_tenant,
            project=resolved_project,
        )
        _bind_selection(request, config=config)
        store = await provider.resolve()
    except GrantStoreUnavailable as exc:
        if "config" in locals():
            _bind_selection(
                request,
                config=config,
                unavailable_reason=exc.operation,
            )
        raise
    except Exception as exc:
        reason = "selected_authority.configuration_unavailable"
        if "config" in locals():
            _bind_selection(
                request,
                config=config,
                unavailable_reason=reason,
            )
        raise GrantStoreUnavailable(reason) from exc
    _bind_selection(request, config=config, store=store)
    return store


def resolve_bound_or_migration_source_grant_store(request: Any) -> GrantStore:
    """Resolve a synchronous OAuth dependency without hiding PostgreSQL."""

    existing = _bound_store(request)
    if existing is not None:
        return existing
    try:
        config, tenant, project, _ = selected_oauth_authority_config(request)
        if config.backend == AUTHORITY_BACKEND_POSTGRESQL:
            reason = "selected_authority.postgresql_store_not_bound"
            _bind_selection(request, config=config, unavailable_reason=reason)
            raise GrantStoreUnavailable(reason)
        store = GrantStore(_redis(request, None), tenant, project)
    except GrantStoreUnavailable:
        raise
    except Exception as exc:
        raise GrantStoreUnavailable(
            "selected_authority.configuration_unavailable"
        ) from exc
    _bind_selection(request, config=config, store=store)
    return store


__all__ = [
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "resolve_bound_or_migration_source_grant_store",
    "resolve_request_oauth_grant_store",
    "selected_oauth_authority_config",
]

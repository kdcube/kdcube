# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK client for Connection Hub authority-registry operations.

The authority registry is SDK-owned configuration. This client resolves it from
the shared bundle-props authority/Redis cache or from an explicitly supplied
registry. It must not call Connection Hub bundle operations; the bundle is only
a UI/API facade over the same SDK resolver.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    connection_hub_bundle_id_from_entrypoint,
    request_origin,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config import (
    authority_registry_config,
    resolve_authority_provider_instance,
)


def _str(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _prop(entrypoint: Any, path: str, default: Any = None) -> Any:
    getter = getattr(entrypoint, "bundle_prop", None)
    if callable(getter):
        return getter(path, default)
    props = getattr(entrypoint, "bundle_props", None)
    if not isinstance(props, Mapping):
        return default
    current: Any = props
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return default
        current = current.get(part)
    return default if current is None else current


def _runtime_tenant_project(entrypoint: Any) -> tuple[str, str]:
    runtime_identity = getattr(entrypoint, "runtime_identity", None)
    if callable(runtime_identity):
        try:
            ident = runtime_identity()
        except Exception:
            ident = {}
        if isinstance(ident, Mapping):
            return _str(ident.get("tenant")), _str(ident.get("project"))
    return "", ""


def _bundle_operation_public_path(
    *,
    tenant: str,
    project: str,
    endpoint: Mapping[str, Any],
) -> str:
    bundle_id = _str(endpoint.get("bundle_id") or endpoint.get("bundle") or endpoint.get("app_id"))
    route = _str(endpoint.get("route") or "public") or "public"
    operation = _str(endpoint.get("operation") or endpoint.get("alias"))
    if not tenant or not project or not bundle_id or not route or not operation:
        return ""
    return (
        "/api/integrations/bundles/"
        f"{quote(tenant, safe='')}/{quote(project, safe='')}/"
        f"{quote(bundle_id, safe='')}/{quote(route, safe='')}/{quote(operation, safe='')}"
    )


def _public_url(
    *,
    origin: str = "",
    path: str = "",
) -> str:
    if not path:
        return ""
    clean_origin = _str(origin).rstrip("/")
    return f"{clean_origin}{path}" if clean_origin else path


class AuthorityRegistryClient:
    """Typed SDK client for Connection Hub authority registry lookups."""

    def __init__(
        self,
        entrypoint: Any = None,
        *,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        redis: Any = None,
        registry: Mapping[str, Any] | None = None,
        bundle_props: Mapping[str, Any] | None = None,
    ) -> None:
        self.entrypoint = entrypoint
        self.bundle_id = (
            _str(connection_hub_bundle_id)
            or connection_hub_bundle_id_from_entrypoint(entrypoint)
            or DEFAULT_CONNECTION_HUB_BUNDLE_ID
        )
        runtime_tenant, runtime_project = _runtime_tenant_project(entrypoint)
        self.tenant = _str(tenant) or runtime_tenant or None
        self.project = _str(project) or runtime_project or None
        self.redis = redis if redis is not None else getattr(entrypoint, "redis", None)
        self._registry = _dict(registry)
        self._bundle_props = _dict(bundle_props)

    async def _load_connection_hub_bundle_props(self) -> dict[str, Any]:
        if self._bundle_props:
            return dict(self._bundle_props)

        own_props = getattr(self.entrypoint, "bundle_props", None)
        if isinstance(own_props, Mapping) and authority_registry_config(own_props):
            return dict(own_props)

        configured_props = _prop(self.entrypoint, "authority_registry", None)
        if isinstance(configured_props, Mapping):
            return {"authority_registry": dict(configured_props)}

        if self.redis is not None and self.tenant and self.project:
            from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

            props = await get_bundle_props(
                self.redis,
                tenant=self.tenant,
                project=self.project,
                bundle_id=self.bundle_id,
            )
            return dict(props or {})

        return {}

    async def registry(self) -> dict[str, Any]:
        if self._registry:
            return dict(self._registry)
        props = await self._load_connection_hub_bundle_props()
        return authority_registry_config(props)

    async def resolve_provider(
        self,
        *,
        authority_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        host_bundle_id: str = "",
        host_route: str = "",
        host_operation: str = "",
    ) -> dict[str, Any]:
        return resolve_authority_provider_instance(
            await self.registry(),
            authority_id=_str(authority_id),
            provider_id=_str(provider_id),
            provider_type=_str(provider_type),
            host_bundle_id=_str(host_bundle_id),
            host_route=_str(host_route),
            host_operation=_str(host_operation),
        )

    async def resolve_provider_entrypoint(
        self,
        *,
        authority_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        entrypoint: str = "login",
        request: Any = None,
        public_origin: str = "",
    ) -> dict[str, Any]:
        entrypoint_name = _str(entrypoint) or "login"
        result = await self.resolve_provider(
            authority_id=authority_id,
            provider_id=provider_id,
            provider_type=provider_type,
        )
        if not result.get("ok"):
            return result

        entrypoints = _dict(result.get("entrypoints"))
        endpoint = _dict(entrypoints.get(entrypoint_name))
        if not endpoint:
            return {
                "ok": False,
                "error": "authority_provider_entrypoint_not_found",
                "authority_id": result.get("authority_id"),
                "provider_id": result.get("provider_id"),
                "entrypoint": entrypoint_name,
            }

        path = _bundle_operation_public_path(
            tenant=_str(self.tenant),
            project=_str(self.project),
            endpoint=endpoint,
        )
        if not path:
            return {
                "ok": False,
                "error": "authority_provider_entrypoint_url_unavailable",
                "authority_id": result.get("authority_id"),
                "provider_id": result.get("provider_id"),
                "entrypoint": entrypoint_name,
                "endpoint": endpoint,
            }

        origin = _str(public_origin) or request_origin(request)
        return {
            "ok": True,
            "authority_id": result.get("authority_id"),
            "provider_id": result.get("provider_id"),
            "provider_type": result.get("provider_type"),
            "platform": bool(result.get("platform")),
            "entrypoint": entrypoint_name,
            "endpoint": endpoint,
            "url": _public_url(origin=origin, path=path),
        }


__all__ = ["AuthorityRegistryClient"]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK client for external identity links owned by Connection Hub.

This is the app-to-app boundary for identity linking. A surface app such as a
Telegram Mini App should expose only its own narrow HTTP operation to the
browser, then call this client server-side. The client invokes Connection Hub
over the request-bound bundle operation bridge, preserving the current
tenant/project/user context without making the browser know Connection Hub's
route shape.
"""

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation


DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"


def _str(value: Any) -> str:
    return str(value or "").strip()


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


def connection_hub_bundle_id(entrypoint: Any, *, default: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID) -> str:
    """Resolve the Connection Hub app id from app config.

    `integrations.connection_hub.bundle_id` is the preferred location for
    callers. The fallback keys keep existing local descriptors working while the
    config shape converges.
    """

    for path in (
        "integrations.connection_hub.bundle_id",
        "connections.connection_hub.bundle_id",
        "identity.connection_hub.bundle_id",
        "identity.telegram.connection_hub_bundle_id",
    ):
        value = _str(_prop(entrypoint, path, ""))
        if value:
            return value
    return _str(default) or DEFAULT_CONNECTION_HUB_BUNDLE_ID


def request_origin(request: Any) -> str:
    """Best-effort public origin for links returned to a browser.

    The origin is passed explicitly through the SDK payload because the
    request-bound bundle operation bridge carries user/session context, not the
    original HTTP request object.
    """

    if request is None:
        return ""
    try:
        headers = request.headers
        host = _str(headers.get("x-forwarded-host") or headers.get("host"))
        proto = _str(headers.get("x-forwarded-proto")).split(",", 1)[0].strip()
        if host:
            return f"{proto or 'https'}://{host}"
    except Exception:
        pass
    try:
        url = request.url
        return f"{url.scheme}://{url.netloc}"
    except Exception:
        return ""


class IdentityLinksClient:
    """Typed app-to-app client for Connection Hub identity-link operations."""

    def __init__(
        self,
        entrypoint: Any,
        *,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
    ) -> None:
        self.entrypoint = entrypoint
        self.bundle_id = _str(connection_hub_bundle_id) or connection_hub_bundle_id_from_entrypoint(entrypoint)
        self.tenant = _str(tenant) or None
        self.project = _str(project) or None

    async def telegram_link_start(
        self,
        *,
        telegram_init_data: str = "",
        public_origin: str = "",
    ) -> dict[str, Any]:
        return await self._public_call(
            "telegram_identity_link_start",
            {
                "telegram_init_data": _str(telegram_init_data),
                "request_origin": _str(public_origin),
            },
        )

    async def telegram_link_complete(
        self,
        *,
        challenge_id: str,
        telegram_init_data: str = "",
        public_origin: str = "",
    ) -> dict[str, Any]:
        return await self._public_call(
            "telegram_identity_link_complete",
            {
                "challenge_id": _str(challenge_id),
                "telegram_init_data": _str(telegram_init_data),
                "request_origin": _str(public_origin),
            },
        )

    async def resolve_identity(self, *, provider: str, provider_subject: str) -> dict[str, Any]:
        return await self._operation_call(
            "identity_resolve",
            {
                "provider": _str(provider),
                "provider_subject": _str(provider_subject),
            },
        )

    async def _public_call(self, operation: str, data: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call(operation, data=data, route="public")

    async def _operation_call(self, operation: str, data: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call(operation, data=data, route="operations")

    async def _call(self, operation: str, *, data: Mapping[str, Any], route: str) -> dict[str, Any]:
        result = await call_bundle_operation(
            bundle_id=self.bundle_id,
            operation=operation,
            data=dict(data),
            tenant=self.tenant,
            project=self.project,
            route=route,
        )
        return dict(result)


def connection_hub_bundle_id_from_entrypoint(entrypoint: Any) -> str:
    return connection_hub_bundle_id(entrypoint)


__all__ = [
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "IdentityLinksClient",
    "connection_hub_bundle_id",
    "connection_hub_bundle_id_from_entrypoint",
    "request_origin",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Consumer SDK for the `connections` named service.

``ConnectionsClient`` wraps a ``NamedServiceClient`` and exposes typed
convenience methods. It works identically over the local (in-process) and API
(HTTP) transports — the transport is a property of the wrapped client.
"""

from __future__ import annotations

from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceClient,
    NamedServiceRegistry,
    NamedServiceRequest,
    NamedServiceResponse,
    TRANSPORT_LOCAL,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_client import AuthorityRegistryClient
from kdcube_ai_app.apps.chat.sdk.solutions.connections.contract import (
    NAMESPACE,
    CONNECTION_CATALOG,
    CONNECTION_DISCONNECT,
    CONNECTION_GET_TOKEN,
    CONNECTION_STATUS,
    OAUTH_START,
    CatalogEntry,
    Connection,
    ConnectionToken,
)


class ConnectionsError(RuntimeError):
    """Raised when a connections named-service call returns an error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConnectionsClient:
    """Typed client for the `connections` named service.

    Construct either from a registry (+ optional transport / auth) or from an
    already-built ``NamedServiceClient``::

        ConnectionsClient(registry, transport=TRANSPORT_API)
        ConnectionsClient(client=named_service_client)
    """

    def __init__(
        self,
        registry: NamedServiceRegistry | None = None,
        *,
        client: NamedServiceClient | None = None,
        transport: str = TRANSPORT_LOCAL,
        **client_kwargs: Any,
    ) -> None:
        if client is not None:
            self._client = client
        elif registry is not None:
            self._client = NamedServiceClient(registry, transport=transport, **client_kwargs)
        else:
            raise ValueError("ConnectionsClient requires a registry or a NamedServiceClient")

    # ── typed operations ────────────────────────────────────────────────────

    async def catalog(self) -> list[CatalogEntry]:
        response = await self._call(CONNECTION_CATALOG)
        return [CatalogEntry.coerce(item) for item in response.items]

    async def status(self, provider: str) -> dict[str, Any]:
        response = await self._call(CONNECTION_STATUS, provider=provider)
        return dict(response.object)

    async def get_token(self, provider: str, account_id: str | None = None) -> ConnectionToken | None:
        payload: dict[str, Any] = {"provider": provider}
        if account_id:
            payload["account_id"] = account_id
        response = await self._call(CONNECTION_GET_TOKEN, **payload)
        if not response.attrs.get("has_token"):
            return None
        obj = dict(response.object)
        if not obj.get("access_token"):
            return None
        return ConnectionToken.coerce(obj)

    async def disconnect(self, provider: str, account_id: str) -> dict[str, Any]:
        response = await self._call(CONNECTION_DISCONNECT, provider=provider, account_id=account_id)
        return dict(response.object)

    async def start_oauth(
        self,
        provider: str,
        app_id: str | None = None,
        scopes: list[str] | None = None,
        return_hint: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": provider}
        if app_id:
            payload["app_id"] = app_id
        if scopes:
            # per-connect subset of the client app's configured scopes (admin ceiling)
            payload["scopes"] = list(scopes)
        if return_hint:
            payload["return_hint"] = return_hint
        response = await self._call(OAUTH_START, **payload)
        return dict(response.object)

    # ── internals ────────────────────────────────────────────────────────────

    async def _call(self, operation: str, **payload: Any) -> NamedServiceResponse:
        request = NamedServiceRequest(
            operation=operation,
            namespace=NAMESPACE,
            payload=dict(payload),
        )
        response = await self._client.call(request)
        if not response.ok:
            error = response.error
            code = error.code if error else "connections_error"
            message = error.message if error else "connections request failed"
            raise ConnectionsError(code, message)
        return response


class ConnectionHubClient:
    """Facade for SDK-owned Connection Hub runtime capabilities.

    This is the boundary general platform code should use when it needs a
    Connection Hub answer. The implementation may use descriptor-backed bundle
    props, Redis, or an explicit in-memory registry, but callers should not
    inspect Connection Hub descriptors directly and should not call the
    Connection Hub bundle just to resolve SDK-owned registry metadata.
    """

    def __init__(
        self,
        entrypoint: Any = None,
        *,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        redis: Any = None,
        registry: dict[str, Any] | None = None,
        bundle_props: dict[str, Any] | None = None,
    ) -> None:
        self.authority_registry = AuthorityRegistryClient(
            entrypoint,
            connection_hub_bundle_id=connection_hub_bundle_id,
            tenant=tenant,
            project=project,
            redis=redis,
            registry=registry,
            bundle_props=bundle_props,
        )

    async def resolve_authority_provider(
        self,
        *,
        authority_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        host_bundle_id: str = "",
        host_route: str = "",
        host_operation: str = "",
    ) -> dict[str, Any]:
        return await self.authority_registry.resolve_provider(
            authority_id=authority_id,
            provider_id=provider_id,
            provider_type=provider_type,
            host_bundle_id=host_bundle_id,
            host_route=host_route,
            host_operation=host_operation,
        )

    async def resolve_authority_provider_entrypoint(
        self,
        *,
        authority_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        entrypoint: str = "login",
        request: Any = None,
        public_origin: str = "",
    ) -> dict[str, Any]:
        return await self.authority_registry.resolve_provider_entrypoint(
            authority_id=authority_id,
            provider_id=provider_id,
            provider_type=provider_type,
            entrypoint=entrypoint,
            request=request,
            public_origin=public_origin,
        )


__all__ = ["ConnectionsClient", "ConnectionsError", "Connection", "ConnectionHubClient"]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext

from .registry import NamedServiceRegistry, RegisteredNamedServiceProvider
from .types import (
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_RESOLVE,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    BLOCK_PRODUCE,
    BLOCK_RENDER,
    EVENT_ACTION,
    EVENT_RESOLVE,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
    PROVIDER_OPERATION,
    RELATION_LIST,
    RELATION_SEARCH,
    TRANSPORT_DATA_BUS,
    TRANSPORT_LOCAL,
    NamedServiceContext,
    NamedServiceRequest,
    NamedServiceResponse,
    namespace_for_ref,
)


class NamedServiceClient:
    """Async client for local named service provider dispatch."""

    def __init__(
        self,
        registry: NamedServiceRegistry,
        *,
        context: NamedServiceContext | None = None,
        auth_context: AuthContext | None = None,
        transport: str = TRANSPORT_LOCAL,
    ) -> None:
        self.registry = registry
        if context is None and auth_context is None:
            auth_context = AuthContext.from_current_request_context(source=f"named_service.{transport}")
        if context is not None:
            self.context = context
        elif auth_context is not None:
            self.context = NamedServiceContext.from_auth_context(auth_context)
        else:
            self.context = NamedServiceContext()
        self.transport = transport

    @classmethod
    def from_current_request(
        cls,
        registry: NamedServiceRegistry,
        *,
        transport: str = TRANSPORT_LOCAL,
        source: str | None = None,
    ) -> "NamedServiceClient":
        auth = AuthContext.from_current_request_context(source=source or f"named_service.{transport}")
        return cls(registry, auth_context=auth, transport=transport)

    @classmethod
    def from_data_bus_context(
        cls,
        registry: NamedServiceRegistry,
        data_bus_context: Any,
        *,
        transport: str = TRANSPORT_DATA_BUS,
        source: str | None = "named_service.data_bus",
    ) -> "NamedServiceClient":
        auth = AuthContext.from_data_bus_context(data_bus_context, source=source)
        return cls(registry, auth_context=auth, transport=transport)

    @classmethod
    def for_bundle_job(
        cls,
        registry: NamedServiceRegistry,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        job_alias: str | None = None,
        on_behalf_of: AuthContext | None = None,
        transport: str = TRANSPORT_LOCAL,
    ) -> "NamedServiceClient":
        auth = AuthContext.for_bundle_job(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            job_alias=job_alias,
            on_behalf_of=on_behalf_of,
            source=f"named_service.{transport}.bundle_job",
        )
        return cls(registry, auth_context=auth, transport=transport)

    async def call(self, request: NamedServiceRequest | Mapping[str, Any]) -> NamedServiceResponse:
        req = request if isinstance(request, NamedServiceRequest) else NamedServiceRequest.from_dict(request)
        entry = self._resolve(req)
        if entry is None:
            return NamedServiceResponse.error_response(
                code="named_service_provider_not_found",
                message="No named service provider matched the request",
                status=404,
                namespace=req.namespace or namespace_for_ref(req.object_ref),
                object_ref=req.object_ref,
            )
        if not entry.spec.supports_operation(req.operation):
            return NamedServiceResponse.error_response(
                code="named_service_operation_not_supported",
                message=f"Provider {entry.spec.provider_id} does not support {req.operation}",
                status=404,
                provider=self._provider_identity(entry),
                namespace=req.namespace,
                object_ref=req.object_ref,
            )
        if not entry.spec.supports_transport(req.operation, self.transport):
            return NamedServiceResponse.error_response(
                code="named_service_transport_not_supported",
                message=f"Provider {entry.spec.provider_id} does not expose {req.operation} over {self.transport}",
                status=400,
                provider=self._provider_identity(entry),
                namespace=req.namespace,
                object_ref=req.object_ref,
            )
        result = entry.provider.dispatch(self.context, req)
        if not hasattr(result, "__await__"):
            raise TypeError("Named service provider dispatch must be async")
        raw = await result
        response = self._coerce_response(raw, entry=entry, request=req)
        return response

    async def about(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(PROVIDER_ABOUT, **kwargs))

    async def capabilities(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(PROVIDER_CAPABILITIES, **kwargs))

    async def provider_operation(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(PROVIDER_OPERATION, **kwargs))

    async def list(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_LIST, **kwargs))

    async def search(self, **kwargs: Any) -> NamedServiceResponse:
        kwargs.setdefault("search_mode", "hybrid")
        return await self.call(self._request(OBJECT_SEARCH, **kwargs))

    async def get(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_GET, **kwargs))

    async def upsert(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_UPSERT, **kwargs))

    async def delete(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_DELETE, **kwargs))

    async def action(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_ACTION, **kwargs))

    async def resolve(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(OBJECT_RESOLVE, **kwargs))

    async def relation_list(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(RELATION_LIST, **kwargs))

    async def relation_search(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(RELATION_SEARCH, **kwargs))

    async def event_resolve(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(EVENT_RESOLVE, **kwargs))

    async def event_action(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(EVENT_ACTION, **kwargs))

    async def block_produce(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(BLOCK_PRODUCE, **kwargs))

    async def block_render(self, **kwargs: Any) -> NamedServiceResponse:
        return await self.call(self._request(BLOCK_RENDER, **kwargs))

    def _request(self, operation: str, **kwargs: Any) -> NamedServiceRequest:
        payload = dict(kwargs)
        payload["operation"] = operation
        return NamedServiceRequest.from_dict(payload)

    def _resolve(self, request: NamedServiceRequest) -> RegisteredNamedServiceProvider | None:
        return self.registry.resolve(
            provider_id=request.provider,
            namespace=request.namespace,
            object_ref=request.object_ref,
        )

    def _coerce_response(
        self,
        raw: Any,
        *,
        entry: RegisteredNamedServiceProvider,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        if isinstance(raw, NamedServiceResponse):
            return raw
        if raw is None:
            raw = {"ok": True}
        if not isinstance(raw, Mapping):
            raw = {"ok": True, "data": {"result": raw}}
        payload = dict(raw)
        payload.setdefault("ok", True)
        payload.setdefault("provider", self._provider_identity(entry))
        payload.setdefault("namespace", request.namespace or entry.spec.namespace)
        payload.setdefault("object_ref", request.object_ref)
        return NamedServiceResponse.from_dict(payload)

    @staticmethod
    def _provider_identity(entry: RegisteredNamedServiceProvider) -> dict[str, Any]:
        return {
            "provider_id": entry.spec.provider_id,
            "bundle_id": entry.spec.bundle_id,
        }

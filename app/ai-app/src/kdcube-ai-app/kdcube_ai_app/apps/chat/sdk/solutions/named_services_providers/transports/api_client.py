# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    BundleOperationStreamResult,
    call_bundle_named_service,
    call_bundle_operation,
    call_bundle_operation_stream,
)
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import (
    BundleBinaryResponse,
    BundleFileResponse,
    BundleStreamResponse,
)

from ..client import NamedServiceClient
from ..discovery import ConfiguredNamedServiceDiscovery, get_current_named_service_discovery
from ..provider import NamedServiceProvider
from ..registry import NamedServiceRegistry
from ..types import NamedServiceRequest, NamedServiceResponse

LOGGER = logging.getLogger("kdcube.sdk.named_services.endpoint")

ENDPOINT_TRANSPORT_BUNDLE_REGISTRY = "bundle_registry"
ENDPOINT_TRANSPORT_BUNDLE_OPERATION = "bundle_operation"
ENDPOINT_TRANSPORT_MODULE = "module"


@dataclass(frozen=True)
class NamedServiceEndpoint:
    """Configured provider endpoint for a named-service namespace."""

    transport: str = ENDPOINT_TRANSPORT_BUNDLE_REGISTRY
    bundle_id: str | None = None
    operation: str = "named_service"
    route: str = "operations"
    tenant: str | None = None
    project: str | None = None
    provider: str | None = None
    namespace: str | None = None
    module: str | None = None
    factory: str | None = None
    registry_method: str = "named_services"
    provider_configs: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def from_provider_config(
        cls,
        provider_config: Mapping[str, Any],
        *,
        namespace: str = "",
        tenant: str = "",
        project: str = "",
    ) -> "NamedServiceEndpoint":
        cfg = dict(provider_config or {})
        transport = str(cfg.get("transport") or "").strip() or (
            ENDPOINT_TRANSPORT_MODULE if str(cfg.get("module") or "").strip() else ENDPOINT_TRANSPORT_BUNDLE_REGISTRY
        )
        return cls(
            transport=transport,
            bundle_id=str(cfg.get("bundle_id") or "").strip() or None,
            operation=str(cfg.get("operation") or "named_service").strip() or "named_service",
            route=str(cfg.get("route") or "operations").strip() or "operations",
            tenant=str(cfg.get("tenant") or tenant or "").strip() or None,
            project=str(cfg.get("project") or project or "").strip() or None,
            provider=str(cfg.get("provider") or "").strip() or None,
            namespace=str(namespace or cfg.get("namespace") or "").strip().lower().rstrip(":") or None,
            module=str(cfg.get("module") or "").strip() or None,
            factory=str(cfg.get("factory") or "").strip() or None,
            registry_method=str(cfg.get("registry_method") or "named_services").strip() or "named_services",
        )

    @classmethod
    def from_provider_configs(
        cls,
        provider_configs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        namespace: str = "",
        tenant: str = "",
        project: str = "",
    ) -> "NamedServiceEndpoint":
        return cls(
            namespace=str(namespace or "").strip().lower().rstrip(":") or None,
            tenant=str(tenant or "").strip() or None,
            project=str(project or "").strip() or None,
            provider_configs=tuple(dict(item) for item in provider_configs or () if isinstance(item, Mapping)),
        )

    @property
    def provider_label(self) -> str:
        return self.provider or ""

    @property
    def namespace_label(self) -> str:
        return self.namespace or ""


def _unwrap_operation_response(raw: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    if operation in raw and isinstance(raw.get(operation), Mapping):
        return raw[operation]  # type: ignore[index]
    return raw


def _load_module_registry(endpoint: NamedServiceEndpoint) -> NamedServiceRegistry:
    module_name = str(endpoint.module or "").strip()
    if not module_name:
        raise RuntimeError("module named-service endpoint requires module")
    factory_name = str(endpoint.factory or "named_services").strip() or "named_services"
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        raise RuntimeError(f"Named-service module {module_name!r} does not expose callable {factory_name!r}")
    value = factory()
    if isinstance(value, NamedServiceRegistry):
        return value
    if isinstance(value, NamedServiceProvider):
        registry = NamedServiceRegistry()
        registry.register(value)
        return registry
    raise RuntimeError(
        f"Named-service module factory {module_name}:{factory_name} must return NamedServiceRegistry or NamedServiceProvider"
    )


async def _call_module_endpoint(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest,
) -> NamedServiceResponse:
    raw = await _call_module_endpoint_raw(endpoint, request)
    if isinstance(raw, Mapping):
        return NamedServiceResponse.from_dict(raw)
    try:
        return NamedServiceResponse.coerce(raw)
    except TypeError:
        pass
    raise RuntimeError(f"Named-service module endpoint returned non-JSON response type {type(raw).__name__}")


async def _call_module_endpoint_raw(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest,
) -> Any:
    registry = _load_module_registry(endpoint)
    auth = AuthContext.from_current_request_context(source="named_service.module")
    client = NamedServiceClient(registry, auth_context=auth)
    raw, entry, req = await client.call_raw(request)
    if entry is None or isinstance(raw, NamedServiceResponse):
        return raw
    if isinstance(raw, (BundleStreamResponse, BundleFileResponse, BundleBinaryResponse)):
        return raw
    return client._coerce_response(raw, entry=entry, request=req)


async def _call_bundle_registry_endpoint(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest,
) -> NamedServiceResponse | BundleStreamResponse | BundleFileResponse | BundleBinaryResponse:
    if not endpoint.bundle_id:
        raise RuntimeError("bundle_registry named-service endpoint requires bundle_id")
    result = await call_bundle_named_service(
        tenant=endpoint.tenant,
        project=endpoint.project,
        bundle_id=endpoint.bundle_id,
        request=request,
        registry_method=endpoint.registry_method,
    )
    if isinstance(result, BundleNamedServiceResult):
        return result.value
    if hasattr(result, "value"):
        return result.value
    return result


async def _resolve_endpoint_from_discovery(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest,
) -> NamedServiceEndpoint:
    if endpoint.bundle_id or endpoint.transport == ENDPOINT_TRANSPORT_MODULE:
        LOGGER.info(
            "Named-service endpoint resolution:\n"
            "  source: explicit_endpoint\n"
            "  namespace: %s\n"
            "  request_operation: %s\n"
            "  endpoint:\n"
            "    transport: %s\n"
            "    bundle: %s\n"
            "    provider: %s\n"
            "    route: %s\n"
            "    registry_method: %s",
            endpoint.namespace or request.namespace or "",
            request.operation or "",
            endpoint.transport,
            endpoint.bundle_id or endpoint.module or "",
            endpoint.provider or request.provider or "",
            endpoint.route,
            endpoint.registry_method,
        )
        return endpoint
    if endpoint.provider_configs:
        configured = ConfiguredNamedServiceDiscovery(
            list(endpoint.provider_configs),
            namespace=endpoint.namespace or request.namespace or "",
        )
        entry = await configured.resolve(
            request,
            namespace=endpoint.namespace or request.namespace or "",
            provider_id=endpoint.provider or request.provider or "",
        )
        if entry is not None:
            discovered = dict(entry.endpoint or {})
            LOGGER.info(
                "Named-service endpoint resolution:\n"
                "  source: configured_providers\n"
                "  namespace: %s\n"
                "  request_operation: %s\n"
                "  selected_provider: %s\n"
                "  selected_bundle: %s\n"
                "  endpoint_transport: %s",
                endpoint.namespace or request.namespace or entry.spec.namespace or "",
                request.operation or "",
                entry.spec.provider_id,
                entry.spec.bundle_id or "",
                discovered.get("transport") or "",
            )
            return NamedServiceEndpoint.from_provider_config(
                {
                    **discovered,
                    "provider": discovered.get("provider") or entry.spec.provider_id,
                    "namespace": endpoint.namespace or request.namespace or entry.spec.namespace,
                },
                namespace=endpoint.namespace or request.namespace or entry.spec.namespace or "",
                tenant=endpoint.tenant or "",
                project=endpoint.project or "",
            )
        LOGGER.info(
            "Named-service endpoint resolution:\n"
            "  source: configured_providers\n"
            "  namespace: %s\n"
            "  request_operation: %s\n"
            "  selected_provider: <none>\n"
            "  configured_provider_count: %s",
            endpoint.namespace or request.namespace or "",
            request.operation or "",
            len(endpoint.provider_configs),
        )
    discovery = get_current_named_service_discovery()
    if discovery is None:
        LOGGER.warning(
            "Named-service endpoint resolution:\n"
            "  source: redis_discovery\n"
            "  namespace: %s\n"
            "  request_operation: %s\n"
            "  selected_provider: <none>\n"
            "  reason: no discovery context bound",
            endpoint.namespace or request.namespace or "",
            request.operation or "",
        )
        return endpoint
    entry = await discovery.resolve(
        request,
        namespace=endpoint.namespace or request.namespace or "",
        provider_id=endpoint.provider or request.provider or "",
    )
    if entry is None:
        LOGGER.warning(
            "Named-service endpoint resolution:\n"
            "  source: redis_discovery\n"
            "  namespace: %s\n"
            "  request_operation: %s\n"
            "  selected_provider: <none>\n"
            "  reason: no matching provider",
            endpoint.namespace or request.namespace or "",
            request.operation or "",
        )
        return endpoint
    discovered = dict(entry.endpoint or {})
    LOGGER.info(
        "Named-service endpoint resolution:\n"
        "  source: redis_discovery\n"
        "  namespace: %s\n"
        "  request_operation: %s\n"
        "  selected_provider: %s\n"
        "  selected_bundle: %s\n"
        "  endpoint_transport: %s",
        endpoint.namespace or request.namespace or entry.spec.namespace or "",
        request.operation or "",
        entry.spec.provider_id,
        entry.spec.bundle_id or "",
        discovered.get("transport") or "",
    )
    return NamedServiceEndpoint.from_provider_config(
        {
            **discovered,
            "provider": discovered.get("provider") or entry.spec.provider_id,
            "namespace": endpoint.namespace or request.namespace or entry.spec.namespace,
        },
        namespace=endpoint.namespace or request.namespace or entry.spec.namespace or "",
        tenant=endpoint.tenant or "",
        project=endpoint.project or "",
    )


async def call_named_service_endpoint(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest | Mapping[str, Any],
) -> NamedServiceResponse:
    """Call a named-service provider endpoint through its configured transport.

    Runtime-local transports keep the current user/session context while
    avoiding browser cookie replay or public HTTP callbacks.
    """

    req = NamedServiceRequest.coerce(request)
    payload = req.to_dict()
    if endpoint.provider and not payload.get("provider"):
        payload["provider"] = endpoint.provider
    if endpoint.namespace and not payload.get("namespace"):
        payload["namespace"] = endpoint.namespace
    req = NamedServiceRequest.from_dict(payload)
    endpoint = await _resolve_endpoint_from_discovery(endpoint, req)
    payload = req.to_dict()
    if endpoint.provider and not payload.get("provider"):
        payload["provider"] = endpoint.provider
    if endpoint.namespace and not payload.get("namespace"):
        payload["namespace"] = endpoint.namespace
    req = NamedServiceRequest.from_dict(payload)
    if endpoint.transport == ENDPOINT_TRANSPORT_BUNDLE_REGISTRY and not endpoint.bundle_id:
        return NamedServiceResponse.error_response(
            code="named_service_provider_not_found",
            message="No named-service provider is configured or discovered for this request",
            status=404,
            namespace=req.namespace or endpoint.namespace,
            object_ref=req.object_ref,
        )
    if endpoint.transport == ENDPOINT_TRANSPORT_MODULE:
        return await _call_module_endpoint(endpoint, req)
    if endpoint.transport == ENDPOINT_TRANSPORT_BUNDLE_REGISTRY:
        try:
            raw = await _call_bundle_registry_endpoint(endpoint, req)
            if isinstance(raw, Mapping):
                return NamedServiceResponse.from_dict(_unwrap_operation_response(raw, endpoint.operation))
            try:
                return NamedServiceResponse.coerce(raw)
            except TypeError:
                pass
            raise RuntimeError(f"Named-service endpoint returned non-JSON response type {type(raw).__name__}")
        except RuntimeError as exc:
            if "No request-bound bundle named-service caller" not in str(exc):
                raise
            LOGGER.debug(
                "Named-service bundle_registry caller unavailable; falling back to bundle_operation: bundle=%s provider=%s namespace=%s",
                endpoint.bundle_id or "",
                endpoint.provider or "",
                endpoint.namespace or "",
            )
    try:
        LOGGER.info(
            "Named-service endpoint call start: transport=%s bundle=%s operation=%s provider=%s namespace=%s request_operation=%s",
            endpoint.transport,
            endpoint.bundle_id or "",
            endpoint.operation,
            endpoint.provider or "",
            endpoint.namespace or req.namespace or "",
            req.operation,
        )
        raw = await call_bundle_operation(
            tenant=endpoint.tenant,
            project=endpoint.project,
            bundle_id=endpoint.bundle_id,
            operation=endpoint.operation,
            route=endpoint.route,
            data=payload,
        )
    except Exception as exc:
        LOGGER.warning(
            "Named-service endpoint call failed: transport=%s bundle=%s operation=%s provider=%s namespace=%s request_operation=%s error=%s",
            endpoint.transport,
            endpoint.bundle_id or "",
            endpoint.operation,
            endpoint.provider or "",
            endpoint.namespace or req.namespace or "",
            req.operation,
            exc,
        )
        return NamedServiceResponse.error_response(
            code="named_service_api_endpoint_unavailable",
            message=str(exc),
            status=503,
            namespace=req.namespace or endpoint.namespace,
            object_ref=req.object_ref,
        )
    response = NamedServiceResponse.from_dict(_unwrap_operation_response(raw, endpoint.operation))
    LOGGER.info(
        "Named-service endpoint call complete: transport=%s bundle=%s operation=%s provider=%s namespace=%s request_operation=%s ok=%s",
        endpoint.transport,
        endpoint.bundle_id or "",
        endpoint.operation,
        endpoint.provider or "",
        endpoint.namespace or req.namespace or "",
        req.operation,
        response.ok,
    )
    return response


async def call_named_service_endpoint_stream(
    endpoint: NamedServiceEndpoint,
    request: NamedServiceRequest | Mapping[str, Any],
    *,
    chunk_size: int = 1024 * 1024,
) -> BundleOperationStreamResult:
    """Call a named-service provider endpoint that returns bytes as a stream."""

    req = NamedServiceRequest.coerce(request)
    payload = req.to_dict()
    if endpoint.provider and not payload.get("provider"):
        payload["provider"] = endpoint.provider
    if endpoint.namespace and not payload.get("namespace"):
        payload["namespace"] = endpoint.namespace
    req = NamedServiceRequest.from_dict(payload)
    endpoint = await _resolve_endpoint_from_discovery(endpoint, req)
    payload = req.to_dict()
    if endpoint.provider and not payload.get("provider"):
        payload["provider"] = endpoint.provider
    if endpoint.namespace and not payload.get("namespace"):
        payload["namespace"] = endpoint.namespace
    req = NamedServiceRequest.from_dict(payload)
    if endpoint.transport == ENDPOINT_TRANSPORT_BUNDLE_REGISTRY and not endpoint.bundle_id:
        raise RuntimeError("No named-service stream provider is configured or discovered for this request")
    if endpoint.transport == ENDPOINT_TRANSPORT_MODULE:
        raw = await _call_module_endpoint_raw(endpoint, req)
        if isinstance(raw, BundleStreamResponse):
            return BundleOperationStreamResult(
                chunks=raw.chunks,
                filename=raw.filename,
                media_type=raw.media_type,
                headers=dict(raw.headers or {}),
                status_code=raw.status_code,
            )
        if isinstance(raw, BundleFileResponse):
            return BundleOperationStreamResult(
                chunks=_file_response_chunks(raw, chunk_size=chunk_size),
                filename=raw.filename,
                media_type=raw.media_type or "application/octet-stream",
                headers=dict(raw.headers or {}),
                status_code=raw.status_code,
            )
        if isinstance(raw, BundleBinaryResponse):
            return BundleOperationStreamResult(
                chunks=_single_chunk(bytes(raw.content or b"")),
                filename=raw.filename,
                media_type=raw.media_type or "application/octet-stream",
                headers=dict(raw.headers or {}),
                status_code=raw.status_code,
            )
        try:
            response = NamedServiceResponse.coerce(raw)
        except TypeError:
            response = None
        if response is not None:
            message = response.error.message if response.error else "Named-service module stream endpoint returned an error"
            raise RuntimeError(message)
        raise RuntimeError(f"Named-service module endpoint returned unsupported stream response type {type(raw).__name__}")
    if endpoint.transport == ENDPOINT_TRANSPORT_BUNDLE_REGISTRY:
        try:
            raw = await _call_bundle_registry_endpoint(endpoint, req)
            if isinstance(raw, BundleStreamResponse):
                return BundleOperationStreamResult(
                    chunks=raw.chunks,
                    filename=raw.filename,
                    media_type=raw.media_type,
                    headers=dict(raw.headers or {}),
                    status_code=raw.status_code,
                )
            if isinstance(raw, BundleFileResponse):
                return BundleOperationStreamResult(
                    chunks=raw.iter_bytes(chunk_size=chunk_size) if hasattr(raw, "iter_bytes") else _file_response_chunks(raw, chunk_size=chunk_size),
                    filename=raw.filename,
                    media_type=raw.media_type or "application/octet-stream",
                    headers=dict(raw.headers or {}),
                    status_code=raw.status_code,
                )
            if isinstance(raw, BundleBinaryResponse):
                return BundleOperationStreamResult(
                    chunks=_single_chunk(bytes(raw.content or b"")),
                    filename=raw.filename,
                    media_type=raw.media_type or "application/octet-stream",
                    headers=dict(raw.headers or {}),
                    status_code=raw.status_code,
                )
            try:
                response = NamedServiceResponse.coerce(raw)
            except TypeError:
                response = None
            if response is not None:
                message = response.error.message if response.error else "Named-service stream endpoint returned an error"
                raise RuntimeError(message)
            if isinstance(raw, Mapping):
                raise RuntimeError(str(raw.get("error") or raw.get("message") or "Named-service stream endpoint returned JSON"))
            raise RuntimeError(f"Named-service endpoint returned unsupported stream response type {type(raw).__name__}")
        except RuntimeError as exc:
            if "No request-bound bundle named-service caller" not in str(exc):
                raise
            LOGGER.debug(
                "Named-service bundle_registry stream caller unavailable; falling back to bundle_operation: bundle=%s provider=%s namespace=%s",
                endpoint.bundle_id or "",
                endpoint.provider or "",
                endpoint.namespace or "",
            )
    LOGGER.info(
        "Named-service stream call start: transport=%s bundle=%s operation=%s provider=%s namespace=%s request_operation=%s action=%s",
        endpoint.transport,
        endpoint.bundle_id or "",
        endpoint.operation,
        endpoint.provider or "",
        endpoint.namespace or req.namespace or "",
        req.operation,
        req.action or "",
    )
    result = await call_bundle_operation_stream(
        tenant=endpoint.tenant,
        project=endpoint.project,
        bundle_id=endpoint.bundle_id,
        operation=endpoint.operation,
        route=endpoint.route,
        data=payload,
        chunk_size=chunk_size,
    )
    LOGGER.info(
        "Named-service stream call accepted: transport=%s bundle=%s operation=%s provider=%s namespace=%s request_operation=%s filename=%s media_type=%s",
        endpoint.transport,
        endpoint.bundle_id or "",
        endpoint.operation,
        endpoint.provider or "",
        endpoint.namespace or req.namespace or "",
        req.operation,
        result.filename or "",
        result.media_type or "",
    )
    return result


async def _single_chunk(data: bytes):
    if data:
        yield data


async def _file_response_chunks(response: BundleFileResponse, *, chunk_size: int):
    from pathlib import Path
    import asyncio

    size = max(1, int(chunk_size or 1024 * 1024))
    with Path(response.path).open("rb") as fh:
        while True:
            chunk = await asyncio.to_thread(fh.read, size)
            if not chunk:
                break
            yield chunk


__all__ = [
    "ENDPOINT_TRANSPORT_BUNDLE_OPERATION",
    "ENDPOINT_TRANSPORT_BUNDLE_REGISTRY",
    "ENDPOINT_TRANSPORT_MODULE",
    "NamedServiceEndpoint",
    "call_named_service_endpoint",
    "call_named_service_endpoint_stream",
]

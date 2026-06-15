# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from .types import (
    NamedServiceContext,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    build_default_operations,
)

LOGGER = logging.getLogger("kdcube.sdk.named_services.provider")


def named_service_provider(
    *,
    provider_id: str,
    bundle_id: str | None = None,
    namespace: str | None = None,
    namespaces: Sequence[str] = (),
    refs: Sequence[str] = (),
    object_kinds: Sequence[str] = (),
    search_scopes: Sequence[Any] = (),
    operations: Mapping[str, Any] | None = None,
    label: str | None = None,
    description: str | None = None,
    metadata: Mapping[str, Any] | None = None,
):
    """Attach named service provider metadata to a class or factory.

    Runtime loader integration is intentionally separate. This decorator only
    provides stable metadata that a bundle or registry can inspect.
    """

    spec = NamedServiceProviderSpec(
        provider_id=provider_id,
        bundle_id=bundle_id,
        namespace=namespace,
        namespaces=tuple(namespaces or ()),
        refs=tuple(refs or ()),
        object_kinds=tuple(object_kinds or ()),
        search_scopes=tuple(search_scopes or ()),
        operations=dict(operations or build_default_operations()),
        label=label,
        description=description,
        metadata=dict(metadata or {}),
    )

    def decorate(target):
        setattr(target, "__kdcube_named_service_provider__", spec)
        return target

    return decorate


class NamedServiceProvider:
    """Base class for async named service providers.

    Providers may override ``dispatch`` directly, or implement async methods
    named after operations, for example ``object_search`` or ``object_action``.
    """

    spec: NamedServiceProviderSpec

    def __init__(self, spec: NamedServiceProviderSpec | None = None) -> None:
        inferred = spec or getattr(self, "__kdcube_named_service_provider__", None)
        if inferred is None:
            inferred = NamedServiceProviderSpec(
                provider_id=self.__class__.__name__,
                operations=build_default_operations(),
            )
        self.spec = inferred

    async def dispatch(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse | Mapping[str, Any]:
        method_name = request.operation.replace(".", "_")
        method = getattr(self, method_name, None)
        if method is None:
            LOGGER.warning(
                "Named-service provider operation missing: provider=%s namespace=%s operation=%s object_ref=%s",
                self.spec.provider_id,
                request.namespace,
                request.operation,
                request.object_ref or "",
            )
            return NamedServiceResponse.error_response(
                code="named_service_operation_not_supported",
                message=f"Provider does not implement {request.operation}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace,
                object_ref=request.object_ref,
            )
        LOGGER.info(
            "Named-service provider dispatch start: provider=%s namespace=%s operation=%s object_ref=%s user_type=%s user_id=%s",
            self.spec.provider_id,
            request.namespace,
            request.operation,
            request.object_ref or "",
            ctx.user_type or "",
            ctx.user_id or "",
        )
        result = method(ctx, request)
        if not hasattr(result, "__await__"):
            raise TypeError(f"Named service provider method {method_name} must be async")
        response = await result
        ok = response.ok if isinstance(response, NamedServiceResponse) else bool(response.get("ok")) if isinstance(response, Mapping) else True
        LOGGER.info(
            "Named-service provider dispatch complete: provider=%s namespace=%s operation=%s object_ref=%s ok=%s",
            self.spec.provider_id,
            request.namespace,
            request.operation,
            request.object_ref or "",
            ok,
        )
        return response

    def provider_identity(self) -> dict[str, Any]:
        return {
            "provider_id": self.spec.provider_id,
            "bundle_id": self.spec.bundle_id,
        }

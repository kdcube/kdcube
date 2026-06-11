# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation

from ..types import NamedServiceRequest, NamedServiceResponse

LOGGER = logging.getLogger("kdcube.sdk.named_services.api_client")


@dataclass(frozen=True)
class NamedServiceApiEndpoint:
    """Configured API endpoint for a remote named service provider."""

    bundle_id: str
    operation: str = "named_service"
    route: str = "operations"
    tenant: str | None = None
    project: str | None = None
    provider: str | None = None
    namespace: str | None = None


def _unwrap_operation_response(raw: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    if operation in raw and isinstance(raw.get(operation), Mapping):
        return raw[operation]  # type: ignore[index]
    return raw


async def call_named_service_api_endpoint(
    endpoint: NamedServiceApiEndpoint,
    request: NamedServiceRequest | Mapping[str, Any],
) -> NamedServiceResponse:
    """Call a named-service provider endpoint through the local API bridge.

    The caller is request-bound by platform ingress. This preserves the current
    user/session visibility checks without requiring the bundle to replay
    browser cookies or issue HTTP callbacks into KDCube.
    """

    req = request if isinstance(request, NamedServiceRequest) else NamedServiceRequest.from_dict(request)
    payload = req.to_dict()
    if endpoint.provider and not payload.get("provider"):
        payload["provider"] = endpoint.provider
    if endpoint.namespace and not payload.get("namespace"):
        payload["namespace"] = endpoint.namespace
    try:
        LOGGER.info(
            "Named-service API endpoint call start: bundle=%s operation=%s provider=%s namespace=%s request_operation=%s",
            endpoint.bundle_id,
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
            "Named-service API endpoint call failed: bundle=%s operation=%s provider=%s namespace=%s request_operation=%s error=%s",
            endpoint.bundle_id,
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
        "Named-service API endpoint call complete: bundle=%s operation=%s provider=%s namespace=%s request_operation=%s ok=%s",
        endpoint.bundle_id,
        endpoint.operation,
        endpoint.provider or "",
        endpoint.namespace or req.namespace or "",
        req.operation,
        response.ok,
    )
    return response


__all__ = [
    "NamedServiceApiEndpoint",
    "call_named_service_api_endpoint",
]

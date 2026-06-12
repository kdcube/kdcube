# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext

from ..client import NamedServiceClient
from ..registry import NamedServiceRegistry
from ..types import TRANSPORT_API, NamedServiceRequest, NamedServiceResponse, ensure_json_object

LOGGER = logging.getLogger("kdcube.sdk.named_services.api")


def _unwrap_api_payload(payload: Mapping[str, Any] | None, request_fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = ensure_json_object(payload, field_name="named service api payload")
    fields = ensure_json_object(request_fields, field_name="named service api fields")
    if "operation" not in data:
        for wrapper_key in ("request", "data"):
            nested = data.get(wrapper_key)
            if isinstance(nested, Mapping):
                data = dict(nested)
                break
    data.update(fields)
    return data


def _coerce_api_request(
    payload: Mapping[str, Any] | NamedServiceRequest | None,
    request_fields: Mapping[str, Any],
) -> NamedServiceRequest:
    if payload is not None and not isinstance(payload, Mapping) and not request_fields:
        return NamedServiceRequest.coerce(payload)
    return NamedServiceRequest.from_dict(_unwrap_api_payload(payload, request_fields))


class NamedServiceApiTransport:
    """API transport adapter that dispatches through the local provider loop."""

    def __init__(
        self,
        registry: NamedServiceRegistry,
        *,
        auth_context: AuthContext | None = None,
        client: NamedServiceClient | None = None,
    ) -> None:
        self.registry = registry
        self.auth_context = auth_context
        self.client = client

    async def dispatch(
        self,
        payload: Mapping[str, Any] | NamedServiceRequest | None = None,
        **request_fields: Any,
    ) -> dict[str, Any]:
        try:
            request = _coerce_api_request(payload, request_fields)
        except Exception as exc:
            LOGGER.warning("Named-service API request invalid: error=%s", exc)
            return NamedServiceResponse.error_response(
                code="named_service_api_request_invalid",
                message=str(exc),
                status=400,
            ).to_dict()
        client = self.client
        if client is None:
            client = NamedServiceClient.from_current_request(
                self.registry,
                transport=TRANSPORT_API,
                source="named_service.api",
            ) if self.auth_context is None else NamedServiceClient(
                self.registry,
                auth_context=self.auth_context,
                transport=TRANSPORT_API,
            )
        LOGGER.info(
            "Named-service API dispatch start: provider=%s namespace=%s operation=%s object_ref=%s",
            request.provider or "",
            request.namespace or "",
            request.operation,
            request.object_ref or "",
        )
        response = await client.call(request)
        LOGGER.info(
            "Named-service API dispatch complete: provider=%s namespace=%s operation=%s object_ref=%s ok=%s status=%s",
            request.provider or "",
            request.namespace or "",
            request.operation,
            request.object_ref or "",
            response.ok,
            response.status,
        )
        return response.to_dict()


async def dispatch_named_service_api_request(
    registry: NamedServiceRegistry,
    payload: Mapping[str, Any] | NamedServiceRequest | None = None,
    *,
    auth_context: AuthContext | None = None,
    client: NamedServiceClient | None = None,
    **request_fields: Any,
) -> dict[str, Any]:
    """Dispatch one API request to an in-process named service provider.

    Bundle `@api(...)` methods should call this helper after platform ingress
    authenticates the API request. The helper uses the bound request context or
    the explicit `auth_context`, then dispatches through the local registry.
    """

    transport = NamedServiceApiTransport(registry, auth_context=auth_context, client=client)
    return await transport.dispatch(payload, **request_fields)


async def dispatch_named_service_api_stream_request(
    registry: NamedServiceRegistry,
    payload: Mapping[str, Any] | NamedServiceRequest | None = None,
    *,
    auth_context: AuthContext | None = None,
    client: NamedServiceClient | None = None,
    **request_fields: Any,
) -> Any:
    """Dispatch one API request expected to return a stream-capable response."""

    try:
        request = _coerce_api_request(payload, request_fields)
    except Exception as exc:
        LOGGER.warning("Named-service API stream request invalid: error=%s", exc)
        return NamedServiceResponse.error_response(
            code="named_service_api_request_invalid",
            message=str(exc),
            status=400,
        ).to_dict()
    resolved_client = client
    if resolved_client is None:
        resolved_client = NamedServiceClient.from_current_request(
            registry,
            transport=TRANSPORT_API,
            source="named_service.api.stream",
        ) if auth_context is None else NamedServiceClient(
            registry,
            auth_context=auth_context,
            transport=TRANSPORT_API,
        )
    LOGGER.info(
        "Named-service API stream dispatch start: provider=%s namespace=%s operation=%s action=%s object_ref=%s",
        request.provider or "",
        request.namespace or "",
        request.operation,
        request.action or "",
        request.object_ref or "",
    )
    raw, entry, req = await resolved_client.call_raw(request)
    if entry is None:
        response = NamedServiceResponse.coerce(raw)
        LOGGER.warning(
            "Named-service API stream dispatch failed before provider: namespace=%s operation=%s status=%s",
            req.namespace or "",
            req.operation,
            response.status,
        )
        return response.to_dict()
    try:
        response = NamedServiceResponse.coerce(raw)
    except TypeError:
        response = None
    if response is not None:
        return response.to_dict()
    LOGGER.info(
        "Named-service API stream dispatch complete: provider=%s namespace=%s operation=%s action=%s object_ref=%s result_type=%s",
        entry.spec.provider_id,
        req.namespace or "",
        req.operation,
        req.action or "",
        req.object_ref or "",
        type(raw).__name__,
    )
    return raw

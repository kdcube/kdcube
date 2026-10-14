# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named-service relay over the Data Bus.

Detached runtimes (the exec supervisor, subprocesses) hold no live
bundle-registry caller — that object exists only in the host proc. The relay
lets them run a named-service request anyway: publish the request as a durable
Data Bus message addressed to the provider's bundle, let that bundle's Data
Bus worker execute it in the proc under the carried request identity, and wait
for the result on the bus's results stream.

The relay reuses the bus end to end: transport (Redis Streams, consumer
groups, retries, DLQ), identity binding (the worker builds the request context
from ``message.actor`` — the same identity the portable room carries), and the
result convention (``publish_and_wait``). Because bus delivery is
at-least-once, the server side is idempotent per message id: a redelivered
relay message returns the recorded response instead of re-running the action.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
    get_current_user_identity,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import DataBusPublisher
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusResultBodyUnavailable
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import timestamp_message_id
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
)

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceError,
    NamedServiceRequest,
    NamedServiceResponse,
)

LOGGER = logging.getLogger("kdcube.sdk.named_services.relay")

NAMED_SERVICE_RELAY_SUBJECT = "kdcube.named_service.relay.v1"

NAMED_SERVICE_RELAY_TIMEOUT_MS = max(
    1000, int(os.getenv("NAMED_SERVICE_RELAY_TIMEOUT_MS", "90000") or "90000")
)
NAMED_SERVICE_RELAY_RESULT_TTL_SECONDS = max(
    60, int(os.getenv("NAMED_SERVICE_RELAY_RESULT_TTL_SECONDS", "900") or "900")
)


def _relay_actor() -> dict[str, Any]:
    """The carried request identity, in the bus's actor vocabulary."""
    ctx = get_current_request_context()
    user = getattr(ctx, "user", None)
    routing = getattr(ctx, "routing", None)
    event = getattr(ctx, "event", None)
    identity = get_current_user_identity() or {}
    if user is None:
        return {}
    return {
        "user_id": getattr(user, "user_id", None),
        "user_type": getattr(user, "user_type", None),
        "username": getattr(user, "username", None),
        "email": getattr(user, "email", None),
        "fingerprint": getattr(user, "fingerprint", None),
        "roles": list(getattr(user, "roles", None) or []),
        "permissions": list(getattr(user, "permissions", None) or []),
        "identity_authority": dict(getattr(user, "identity_authority", None) or {}),
        "session_id": getattr(routing, "session_id", None),
        "source_bundle_id": str(identity.get("bundle_id") or "").strip(),
        "source_agent_id": str(getattr(event, "agent_id", "") or "").strip(),
    }


def _scope_from_context() -> tuple[str, str]:
    ctx = get_current_request_context()
    actor = getattr(ctx, "actor", None)
    return (
        str(getattr(actor, "tenant_id", "") or ""),
        str(getattr(actor, "project_id", "") or ""),
    )


def _error_response(code: str, message: str, *, status: int = 503, details: Mapping[str, Any] | None = None) -> NamedServiceResponse:
    return NamedServiceResponse(
        ok=False,
        status=status,
        ret={},
        error=NamedServiceError(code=code, message=message, details=dict(details or {})),
    )


async def relay_named_service_call(
    *,
    bundle_id: str,
    request: NamedServiceRequest,
    admission: NamedServiceAdmission,
    tenant: str = "",
    project: str = "",
    timeout_ms: int = NAMED_SERVICE_RELAY_TIMEOUT_MS,
) -> NamedServiceResponse:
    """Run one named-service request through the provider bundle's Data Bus.

    Used where no live bundle-registry caller is bound (detached runtimes).
    The message actor is the carried request identity; the provider authorizes
    against it exactly as it would for a direct call.
    """
    ctx_tenant, ctx_project = _scope_from_context()
    tenant_value = str(tenant or ctx_tenant or "").strip()
    project_value = str(project or ctx_project or "").strip()
    bundle_value = str(bundle_id or "").strip()
    admission.validate(request)
    if not (tenant_value and project_value and bundle_value):
        return _error_response(
            "named_service_relay_scope_missing",
            "Relay requires tenant, project, and the provider bundle id.",
            status=500,
        )
    actor = _relay_actor()
    if not actor.get("user_id") and not actor.get("fingerprint"):
        return _error_response(
            "named_service_relay_identity_missing",
            "Relay requires a bound request identity; this runtime carries none.",
            status=401,
        )
    message_id = timestamp_message_id("nsrelay")
    publisher = DataBusPublisher(
        tenant=tenant_value,
        project=project_value,
        bundle_id=bundle_value,
    )
    LOGGER.info(
        "[named_service.relay] publish bundle=%s namespace=%s operation=%s action=%s message=%s",
        bundle_value,
        request.namespace or "",
        request.operation or "",
        request.action or "",
        message_id,
    )
    try:
        result = await publisher.publish_and_wait(
            subject=NAMED_SERVICE_RELAY_SUBJECT,
            payload={
                "request": request.to_dict(),
                "admission": admission.relay_selector(),
            },
            actor=actor,
            idempotency_key=message_id,
            message_id=message_id,
            timeout_ms=max(1000, int(timeout_ms or NAMED_SERVICE_RELAY_TIMEOUT_MS)),
        )
    except DataBusResultBodyUnavailable:
        # W827 S2d: the provider answered but its large result body is gone or altered: outcome unknown, retryable.
        return _error_response(
            DataBusResultBodyUnavailable.code,
            "The relayed call's result body is no longer available; its outcome is unknown. Retry the read.",
            status=504,
            details={"bundle_id": bundle_value, "message_id": message_id, "outcome_unknown": True},
        )
    except TimeoutError:
        return _error_response(
            "named_service_relay_timeout",
            (
                "The provider bundle did not answer the relayed call in time. "
                "Its Data Bus worker may be busy or the proc may be restarting; retry."
            ),
            status=504,
            details={"bundle_id": bundle_value, "message_id": message_id},
        )
    status = str(result.get("status") or "")
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    response_payload = data.get("response") if isinstance(data.get("response"), Mapping) else None
    if response_payload is not None:
        return NamedServiceResponse.from_dict(response_payload)
    error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
    return _error_response(
        str(error.get("code") or f"named_service_relay_{status or 'failed'}"),
        str(error.get("message") or "Relayed named-service call failed."),
        status=502,
        details={"bundle_id": bundle_value, "message_id": message_id, **dict(error.get("details") or {})},
    )


def _result_cache_key(message: Any) -> str:
    return (
        f"kdcube:data-bus:{message.tenant}:{message.project}:{message.bundle_id}"
        f":relay-done:{message.message_id}"
    )


async def _record_relay_result(redis: Any, cache_key: str, result: Mapping[str, Any]) -> None:
    if redis is None:
        return
    try:
        await redis.setex(
            cache_key,
            NAMED_SERVICE_RELAY_RESULT_TTL_SECONDS,
            json.dumps(dict(result), ensure_ascii=False),
        )
    except Exception:
        LOGGER.debug("[named_service.relay] result cache write failed", exc_info=True)


async def handle_named_service_relay(ctx: Any, message: Any) -> dict[str, Any]:
    """Server side of the relay: runs inside the provider bundle's Data Bus
    handler, in the proc.

    Dispatches the carried request through the bundle's own named-service
    registry with the request context the worker bound from ``message.actor``.
    Idempotent per message id: bus redelivery returns the recorded response.
    """
    from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
        NamedServiceAdmissionResolutionError,
        admission_from_relay_selector,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client import (
        NamedServiceClient,
    )

    redis = getattr(ctx.bundle, "redis", None)
    cache_key = _result_cache_key(message)
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if isinstance(cached, (bytes, bytearray)):
                cached = cached.decode("utf-8")
            if cached:
                LOGGER.info(
                    "[named_service.relay] replaying recorded result message=%s",
                    message.message_id,
                )
                return json.loads(cached)
        except Exception:
            LOGGER.debug("[named_service.relay] result cache read failed", exc_info=True)

    raw_request = (message.payload or {}).get("request")
    if not isinstance(raw_request, Mapping):
        return {
            "status": "rejected",
            "error": {
                "code": "named_service_relay_request_invalid",
                "message": "Relay message carries no named-service request object.",
            },
        }
    request = NamedServiceRequest.from_dict(raw_request)
    raw_admission = (message.payload or {}).get("admission")
    if not isinstance(raw_admission, Mapping):
        return {
            "status": "rejected",
            "error": {
                "code": "named_service_relay_admission_missing",
                "message": "Relay message carries no typed admission selector.",
            },
        }
    try:
        admission = admission_from_relay_selector(
            raw_admission,
            actor=message.actor if isinstance(message.actor, Mapping) else {},
        )
    except (NamedServiceAdmissionResolutionError, TypeError, ValueError) as exc:
        return {
            "status": "rejected",
            "error": {
                "code": "named_service_relay_admission_invalid",
                "message": str(exc),
            },
        }
    decision = await admission.authorize(request)
    if not decision.allowed:
        denial = decision.denial or _error_response(
            "named_service_admission_denied",
            "Named-service admission was denied.",
            status=403,
        )
        result = {
            "status": "ok",
            "data": {"response": denial.to_dict()},
        }
        await _record_relay_result(redis, cache_key, result)
        return result

    registry = ctx.bundle.named_services()
    auth = AuthContext.from_current_request_context(source="named_service.relay")
    client = NamedServiceClient(registry, auth_context=auth)
    with decision.bind():
        response = await client.call(request)
    response = NamedServiceResponse.coerce(response)

    result: dict[str, Any] = {
        "status": "ok",
        "data": {"response": response.to_dict()},
    }
    await _record_relay_result(redis, cache_key, result)
    return result


__all__ = [
    "NAMED_SERVICE_RELAY_SUBJECT",
    "NAMED_SERVICE_RELAY_TIMEOUT_MS",
    "handle_named_service_relay",
    "relay_named_service_call",
]

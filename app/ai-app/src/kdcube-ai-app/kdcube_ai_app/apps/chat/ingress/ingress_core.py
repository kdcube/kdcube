# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/ingress/ingress_core.py

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from fastapi import HTTPException, Request  # only if you put this in a place where FastAPI is available

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.util import _iso
from kdcube_ai_app.auth.sessions import RequestContext, UserType, UserSession
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator, ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventPayload, ExternalEventMeta, ExternalEventRouting, ExternalEventActor, ExternalEventUser,
    ExternalEventRequest, ExternalEventConfig, ExternalEventAccounting, ExternalEventContinuation, ExternalEvent,
    ExternalEventLaneRef, ExternalEventLaneWakeup, ServiceCtx, ConversationCtx, external_events_text,
    external_event_attachment_payloads, hosted_external_event_attachments,
)
from kdcube_ai_app.apps.chat.sdk.event_identity import (
    DEFAULT_REACT_AGENT_ID,
    build_event_logical_path,
    normalize_agent_id,
    safe_event_object_path,
)
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError
from kdcube_ai_app.infra.gateway.backpressure import BackpressureError
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerError
from kdcube_ai_app.apps.middleware.token_extract import (
    resolve_auth_from_headers_and_cookies,
    resolve_socket_auth_tokens,
)
from kdcube_ai_app.apps.chat.ids import new_turn_id
from kdcube_ai_app.apps.chat.ingress.resolvers import get_auth_manager
from kdcube_ai_app.infra.plugin.bundle_registry import load_persisted_registry_from_runtime_ctx

from kdcube_ai_app.auth.AuthManager import AuthenticationError, PRIVILEGED_ROLES

logger = logging.getLogger(__name__)

async def _load_active_registry(app, tenant: str, project: str):
    """
    Ingress must not mutate bundle descriptors. It reads the active registry
    via bundle_store; file-backed deployments reread descriptor authority and
    refresh Redis only as a runtime cache.
    """
    return await load_persisted_registry_from_runtime_ctx(app.state, tenant, project)


TransportKind = Literal["sse", "socket", "telegram"]

# Hard limit for input text length.
# Chosen so that we can embed it with OpenAI embeddings without chunking.
try:
    MAX_EMBED_TEXT_CHARS: int = int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "32000"))
except Exception:
    MAX_EMBED_TEXT_CHARS = 32000

# -----------------------------
# Gateway checks (shared)
# -----------------------------

@dataclass
class GatewayCheckResult:
    kind: Literal["ok", "rate_limit", "backpressure", "circuit_breaker", "error"]
    exc: Optional[Exception] = None


async def run_gateway_checks(
        gateway_adapter,
        session: UserSession,
        context: RequestContext,
        endpoint: str,
) -> GatewayCheckResult:
    """
    Shared gateway checks for chat ingestion.
    Transport layer decides how to map errors to HTTP / WS semantics.
    """
    try:
        await gateway_adapter.gateway.rate_limiter.check_and_record(session, context, endpoint)
        await gateway_adapter.gateway.backpressure_manager.check_capacity(
            session.user_type, session, context, endpoint
        )
        return GatewayCheckResult(kind="ok")
    except RateLimitError as e:
        return GatewayCheckResult(kind="rate_limit", exc=e)
    except BackpressureError as e:
        return GatewayCheckResult(kind="backpressure", exc=e)
    except CircuitBreakerError as e:
        return GatewayCheckResult(kind="circuit_breaker", exc=e)
    except Exception as e:
        logger.exception("Gateway checks failed for endpoint %s: %s", endpoint, e)
        return GatewayCheckResult(kind="error", exc=e)


def map_gateway_error(result: GatewayCheckResult) -> Dict[str, Any]:
    """
    Map GatewayCheckResult → generic error payload for transport layer.
    """
    if result.kind == "rate_limit":
        e: RateLimitError = result.exc  # type: ignore[assignment]
        msg = f"Rate limit exceeded: {getattr(e, 'message', str(e))}"
        return {
            "error_type": "rate_limit",
            "status": 429,
            "message": msg,
            "retry_after": getattr(e, "retry_after", None),
        }
    if result.kind == "backpressure":
        e: BackpressureError = result.exc  # type: ignore[assignment]
        msg = f"System under pressure: {getattr(e, 'message', str(e))}"
        return {
            "error_type": "backpressure",
            "status": 503,
            "message": msg,
            "retry_after": getattr(e, "retry_after", None),
        }
    if result.kind == "circuit_breaker":
        e: CircuitBreakerError = result.exc  # type: ignore[assignment]
        msg = f"Service temporarily unavailable: {getattr(e, 'message', str(e))}"
        return {
            "error_type": "circuit_breaker",
            "status": 503,
            "message": msg,
            "retry_after": getattr(e, "retry_after", None),
        }

    # generic
    msg = "System check failed"
    return {
        "error_type": "gateway_error",
        "status": 503,
        "message": msg,
        "retry_after": None,
    }


# -----------------------------
# Attachments (shared)
# -----------------------------

@dataclass
class RawAttachment:
    """
    Transport-agnostic representation of a raw uploaded file.
    """
    content: bytes
    name: str
    mime: str
    meta: Optional[Dict[str, Any]] = None


# -----------------------------
# Chat ingestion (shared)
# -----------------------------

@dataclass
class IngressConfig:
    transport: TransportKind                # "sse", "socket", or proc-local transports such as "telegram"
    entrypoint: str                         # "/sse/chat", "/socket.io/chat", "/telegram/webhook", ...
    component: str                          # "chat.sse", "chat.socket", "chat.telegram", ...
    instance_id: str
    stream_id: Optional[str] = None         # SSE stream_id or Socket.IO sid
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class IngressResult:
    ok: bool
    error_type: Optional[str] = None
    error: Optional[str] = None
    http_status: Optional[int] = None
    retry_after: Optional[int] = None
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    session_id: Optional[str] = None
    user_type: Optional[str] = None
    queue_stats: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    is_continuation: Optional[bool] = None
    active_turn_id: Optional[str] = None
    target_turn_id: Optional[str] = None
    queued_turn_id: Optional[str] = None
    event_id: Optional[str] = None
    external_event_sequence: Optional[int] = None
    live_owner_detected: Optional[bool] = None


def _message_payload(message_data: Dict[str, Any]) -> Dict[str, Any]:
    payload = message_data.get("payload")
    return payload if isinstance(payload, dict) else {}


def _resolve_target_turn_id(message_data: Dict[str, Any]) -> Optional[str]:
    payload = _message_payload(message_data)
    raw = (
        message_data.get("target_turn_id")
        or message_data.get("active_turn_id")
        or payload.get("target_turn_id")
        or payload.get("active_turn_id")
    )
    value = str(raw or "").strip()
    return value or None


def _external_events_from_message(message_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = message_data.get("external_events")
    if events is None:
        return []
    if not isinstance(events, list):
        return []
    return [dict(event) for event in events if isinstance(event, dict)]


def _bool_from_any(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _external_event_is_reactive(event: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(event, dict):
        return False
    return _bool_from_any(event.get("reactive"), False)


def _external_event_source_id(event: Optional[Dict[str, Any]]) -> str:
    if not isinstance(event, dict):
        return "react.external_event"
    value = str(event.get("event_source_id") or "").strip()
    return value or "react.external_event"


def _target_agent_id(message_data: Dict[str, Any]) -> str:
    payload = _message_payload(message_data)
    target = message_data.get("target") if isinstance(message_data.get("target"), dict) else {}
    if not target:
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    value = (
        target.get("agent_id")
        or target.get("agent")
        or payload.get("agent_id")
        or message_data.get("agent_id")
        or message_data.get("agent")
    )
    return normalize_agent_id(value, default=DEFAULT_REACT_AGENT_ID)


def _first_reactive_or_first_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not events:
        return None
    return next((event for event in events if _external_event_is_reactive(event)), events[0])


def _external_event_object_path(event: Dict[str, Any], *, event_id: str) -> str:
    logical_path = str(event.get("logical_path") or "").strip()
    marker = ".events/"
    if logical_path.startswith("ev:") and marker in logical_path:
        return safe_event_object_path(logical_path.split(marker, 1)[1], default=event_id)
    hosted_uri = str(event.get("hosted_uri") or "").strip()
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_ref = str(payload.get("event_ref") or "").strip()
    external_ref = hosted_uri or event_ref
    if ":" in external_ref:
        return safe_event_object_path(external_ref.split(":", 1)[1], default=event_id)
    source_id = _external_event_source_id(event)
    return safe_event_object_path(f"{source_id.replace('.', '/')}/{event_id}", default=event_id)


def _accept_external_events(
    message_data: Dict[str, Any],
    *,
    turn_id: str,
    target_agent_id: str,
) -> List[Dict[str, Any]]:
    accepted: List[Dict[str, Any]] = []
    for raw in _external_events_from_message(message_data):
        event = dict(raw)
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            event_id = f"evt_{uuid.uuid4().hex}"
        event["event_id"] = event_id
        event["type"] = str(event.get("type") or "event.external").strip() or "event.external"
        event["event_source_id"] = _external_event_source_id(event)
        timestamp = str(event.get("timestamp") or event.get("ts") or "").strip()
        event["timestamp"] = timestamp or _iso()
        event["reactive"] = _external_event_is_reactive(event)
        event["agent_id"] = normalize_agent_id(event.get("agent_id") or target_agent_id, default=target_agent_id)
        if event.get("story_id") is not None:
            event["story_id"] = str(event.get("story_id") or "").strip() or None
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload = dict(payload)
        if "mime" not in payload and event.get("mime"):
            payload["mime"] = str(event.get("mime") or "").strip()
        payload.setdefault("mime", "application/json")
        event["payload"] = payload
        if not event.get("hosted_uri") and isinstance(payload.get("event_ref"), str):
            event["hosted_uri"] = str(payload.get("event_ref") or "").strip() or None
        logical_path = str(event.get("logical_path") or "").strip()
        if not logical_path:
            event["logical_path"] = build_event_logical_path(
                turn_id=turn_id,
                event_path=_external_event_object_path(event, event_id=event_id),
            )
        accepted.append(event)
    if accepted:
        message_data["external_events"] = accepted
    return accepted


def _external_event_envelope(
    *,
    message_data: Dict[str, Any],
    text: str,
    event: Dict[str, Any],
    is_continuation: bool = False,
) -> Dict[str, Any]:
    payload = _message_payload(message_data)
    target = message_data.get("target") if isinstance(message_data.get("target"), dict) else {}
    if not target:
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    envelope: Dict[str, Any] = {
        "text": text or "",
        "event": dict(event),
        "is_continuation": bool(is_continuation),
    }
    if target:
        envelope["target"] = dict(target)
    return envelope


def _task_payload_for_external_event(
    *,
    payload: ExternalEventPayload,
    event: Dict[str, Any],
    kind: str,
    source: str,
) -> Dict[str, Any]:
    task_payload = payload.model_dump()
    request = task_payload.get("request")
    if isinstance(request, dict):
        request["external_events"] = [dict(event)]
        task_payload["request"] = request
    continuation = task_payload.get("continuation") if isinstance(task_payload.get("continuation"), dict) else {}
    is_continuation = bool(continuation.get("is_continuation"))
    event_meta = task_payload.get("event")
    if not isinstance(event_meta, dict):
        event_meta = {}
    event_meta.update({
        "kind": str(kind or "external_event"),
        "type": str(event.get("type") or "event.external"),
        "agent_id": normalize_agent_id(event.get("agent_id"), default=DEFAULT_REACT_AGENT_ID),
        "event_source_id": _external_event_source_id(event),
        "event_id": str(event.get("event_id") or ""),
        "logical_path": str(event.get("logical_path") or ""),
        "story_id": event.get("story_id"),
        "reactive": _external_event_is_reactive(event),
        "is_continuation": is_continuation,
        "source": source,
    })
    task_payload["event"] = event_meta
    return task_payload


async def _publish_external_event_batch(
    *,
    external_event_source: Any,
    message_data: Dict[str, Any],
    text: str,
    events: List[Dict[str, Any]],
    payload: ExternalEventPayload,
    kind: str = "external_event",
    explicit: bool = True,
    target_turn_id: Optional[str] = None,
    active_turn_id_at_ingress: Optional[str] = None,
    owner_turn_id: Optional[str] = None,
    source: str = "",
) -> List[Any]:
    published: List[Any] = []
    is_continuation = bool(active_turn_id_at_ingress or owner_turn_id)
    for event in events:
        event_text = external_events_text([event]) or ""
        env = await external_event_source.publish(
            kind=kind,
            event_id=str(event.get("event_id") or "") or None,
            explicit=explicit,
            is_continuation=is_continuation,
            target_turn_id=target_turn_id,
            active_turn_id_at_ingress=active_turn_id_at_ingress,
            owner_turn_id=owner_turn_id,
            source=source,
            event_source_id=_external_event_source_id(event),
            text=event_text,
            payload=_external_event_envelope(
                message_data=message_data,
                text=event_text,
                event=event,
                is_continuation=is_continuation,
            ),
            task_payload=_task_payload_for_external_event(
                payload=payload,
                event=event,
                kind=kind,
                source=source,
            ),
        )
        published.append(env)
    return published


def _event_lane_ref_from_envelope(
    *,
    tenant: Optional[str],
    project: Optional[str],
    user_id: Optional[str],
    conversation_id: str,
    agent_id: str,
    event: Any,
) -> ExternalEventLaneRef:
    return ExternalEventLaneRef(
        tenant=tenant,
        project=project,
        user_id=user_id,
        conversation_id=conversation_id,
        agent_id=normalize_agent_id(agent_id, default=DEFAULT_REACT_AGENT_ID),
        event_id=str(getattr(event, "message_id", "") or "") or None,
        sequence=int(getattr(event, "sequence", 0) or 0) or None,
        stream_id=str(getattr(event, "stream_id", "") or "") or None,
    )


def _event_lane_wakeup_from_payload(
    *,
    payload: ExternalEventPayload,
    event: Any,
    tenant: Optional[str],
    project: Optional[str],
    user_id: Optional[str],
    conversation_id: str,
    agent_id: str,
    reason: str,
) -> ExternalEventLaneWakeup:
    return ExternalEventLaneWakeup(
        meta=payload.meta,
        routing=payload.routing,
        actor=payload.actor,
        user=payload.user,
        config=payload.config,
        accounting=payload.accounting,
        continuation=payload.continuation,
        event=payload.event,
        bundle_call_context=dict(getattr(payload, "bundle_call_context", {}) or {}),
        event_lane=_event_lane_ref_from_envelope(
            tenant=tenant,
            project=project,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            event=event,
        ),
        reason=reason,
    )


def _resolve_conversation_owner_id(session: UserSession) -> Optional[str]:
    owner_id = getattr(session, "user_id", None) or getattr(session, "fingerprint", None)
    if owner_id is None:
        return None
    owner_id = str(owner_id).strip()
    return owner_id or None


async def _conversation_state_row_exists(
        conversation_browser,
        *,
        user_id: str,
        conversation_id: str,
) -> bool:
    idx = getattr(conversation_browser, "idx", None)
    if idx is None:
        return False
    get_row = getattr(idx, "get_conversation_state_row", None)
    if get_row is None:
        return False
    try:
        row = await get_row(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return bool(row)
    except Exception as e:
        logger.warning(
            "Conversation state-row fallback failed user=%s conversation_id=%s: %s",
            user_id,
            conversation_id,
            e,
        )
        return False


async def resolve_ingress_conversation_id(
        *,
        app,
        session: UserSession,
        message_data: Dict[str, Any],
) -> tuple[str, bool]:
    raw_conversation_id = message_data.get("conversation_id")
    conversation_id = str(raw_conversation_id or "").strip()
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        message_data["conversation_id"] = conversation_id
        return conversation_id, True

    owner_id = _resolve_conversation_owner_id(session)
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authenticated user identity is missing")

    conversation_browser = getattr(getattr(app, "state", None), "conversation_browser", None)
    if conversation_browser is None:
        logger.error("Conversation lookup is unavailable: app.state.conversation_browser is not configured")
        raise HTTPException(status_code=503, detail="Conversation lookup unavailable")

    exists = await conversation_browser.conversation_exists(
        user_id=owner_id,
        conversation_id=conversation_id,
    )
    if not exists:
        # A newly created conversation writes its conversation.state row before
        # the HTTP ack, while searchable turn artifacts may arrive later in the
        # processor. Treat that state row as sufficient ownership evidence so a
        # rapid follow-up does not 404 during the indexing gap.
        exists = await _conversation_state_row_exists(
            conversation_browser,
            user_id=owner_id,
            conversation_id=conversation_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message_data["conversation_id"] = conversation_id
    return conversation_id, False


async def process_chat_message(
        *,
        app,
        chat_queue_manager,
        chat_comm: ChatRelayCommunicator,
        session: UserSession,
        request_context: RequestContext,
        message_data: Dict[str, Any],
        message_text: str,
        ingress: IngressConfig,
        raw_attachments: Optional[List[RawAttachment]] = None,
) -> IngressResult:
    """
    Core chat ingestion:
      - validate message
      - resolve bundle
      - build payload + accounting envelope
      - conversation state (lock, created/in_progress)
      - enqueue
      - emit conv_status + start + errors

    Transport layer already ran gateway checks. It just passes its
    RequestContext + IngressConfig.
    """
    text = (message_text or "").strip()
    has_raw_attachments = bool(raw_attachments)
    target_turn_id = _resolve_target_turn_id(message_data)
    target_agent_id = _target_agent_id(message_data)
    task_id = str(uuid.uuid4())
    turn_id = message_data.get("turn_id") or new_turn_id()
    external_events = _accept_external_events(
        message_data,
        turn_id=turn_id,
        target_agent_id=target_agent_id,
    )
    has_external_events = bool(external_events)
    if not has_external_events:
        return IngressResult(
            ok=False,
            error_type="missing_external_events",
            error='Missing "external_events"',
            http_status=400,
        )
    first_reactive_or_first_event = _first_reactive_or_first_event(external_events)
    external_event_reactive = any(_external_event_is_reactive(event) for event in external_events)
    conversation_id = str(message_data.get("conversation_id") or "").strip()
    if not conversation_id:
        return IngressResult(
            ok=False,
            error_type="missing_conversation_id",
            error="Missing conversation_id",
            http_status=400,
        )
    message_data["conversation_id"] = conversation_id
    # Tenant / project
    settings = get_settings()
    tenant_id = (
            message_data.get("tenant")
            or message_data.get("tenant_id")
            or settings.TENANT
    )
    project_id = message_data.get("project") or settings.PROJECT

    request_id = str(uuid.uuid4())
    provided_bundle_id = message_data.get("bundle_id")

    # Ingress only reads the active registry; it does not mutate descriptors.
    reg = await _load_active_registry(app, tenant_id, project_id)

    svc = ServiceCtx(request_id=request_id, user=session.user_id, project=project_id, tenant=tenant_id)
    conv = ConversationCtx(
        session_id=session.session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    comm = ChatCommunicator(
        emitter=chat_comm,
        tenant=tenant_id or "",
        project=project_id or "",
        user_id=session.user_id,
        user_type=session.user_type.value,
        service=svc.model_dump(),
        conversation=conv.model_dump(),
        room=session.session_id,
        target_sid=ingress.stream_id,
    )

    # If the active registry is not available, fail early to avoid stale defaults.
    if not reg:
        err = "Bundle registry unavailable"
        await chat_comm.emit_error(
            svc,
            conv,
            error=err,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="bundle_registry_unavailable",
            error=err,
            http_status=503,
        )

    # External events may intentionally carry blank text.
    if not text and not has_raw_attachments and not has_external_events:
        await chat_comm.emit_error(
            svc,
            conv,
            error='Missing "message"',
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="missing_message",
            error='Missing "message"',
            http_status=400,
        )

    from kdcube_ai_app.infra.service_hub.multimodality import (
        MESSAGE_MAX_BYTES,
        MODALITY_DOC_MIME,
        MODALITY_IMAGE_MIME,
        MODALITY_MAX_DOC_BYTES,
        MODALITY_MAX_IMAGE_BYTES,
    )

    text_bytes = len(text.encode("utf-8"))
    if text_bytes > MESSAGE_MAX_BYTES:
        total_limit_mb = int(MESSAGE_MAX_BYTES / (1024 * 1024))
        await chat_comm.emit_error(
            svc,
            conv,
            error=f"Message exceeds the {total_limit_mb} MB total limit (text + attachments).",
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="message_size_limit",
            error="message exceeds total size limit",
            http_status=413,
        )

    bundle_id_val = provided_bundle_id or (reg.default_bundle_id if reg else None)
    if not reg or not bundle_id_val or bundle_id_val not in (reg.bundles or {}):
        err = f"Unknown bundle_id '{bundle_id_val or provided_bundle_id}'"
        await chat_comm.emit_error(
            svc,
            conv,
            error=err,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="unknown_bundle",
            error=err,
            http_status=400,
        )
    spec_resolved = reg.bundles.get(bundle_id_val)
    if not spec_resolved:
        err = f"Bundle spec missing for bundle_id '{bundle_id_val}'"
        await chat_comm.emit_error(
            svc,
            conv,
            error=err,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="unknown_bundle",
            error=err,
            http_status=400,
        )

    # Input too long → emit error and do NOT enqueue
    if len(text) > MAX_EMBED_TEXT_CHARS:
        err = (
            f"Input is too long ({len(text)} characters). "
            f"Maximum allowed is {MAX_EMBED_TEXT_CHARS} characters."
        )
        await chat_comm.emit_error(
            svc,
            conv,
            error=err,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        return IngressResult(
            ok=False,
            error_type="input_too_long",
            error=err,
            http_status=400,
            reason="input_too_long",
        )

    bundle_id = spec_resolved.id
    metadata = dict(ingress.metadata or {})
    acct_env = build_envelope_from_session(
        session=session,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=request_id,
        component=ingress.component,
        app_bundle_id=bundle_id,
        metadata=metadata,
    ).to_dict()

    ext_config = (message_data.get("config") or {}).copy()
    if "tenant" not in ext_config:
        ext_config["tenant"] = tenant_id
    if "project" not in ext_config and project_id:
        ext_config["project"] = project_id

    routing = ExternalEventRouting(
        session_id=session.session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        socket_id=ingress.stream_id,
        bundle_id=bundle_id,
    )

    payload = ExternalEventPayload(
        meta=ExternalEventMeta(
            task_id=task_id,
            created_at=time.time(),
            instance_id=ingress.instance_id,
        ),
        routing=routing,
        actor=ExternalEventActor(
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        user=ExternalEventUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            email=session.email,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.timezone,
            utc_offset_min=getattr(request_context, "user_utc_offset_min", None),
        ),
        request=ExternalEventRequest(
            external_events=external_events,
            chat_history=message_data.get("chat_history") or [],
            operation=message_data.get("operation") or message_data.get("command"),
            invocation=message_data.get("invocation"),
            payload=message_data.get("payload") or {},
            request_id=request_id,
        ),
        config=ExternalEventConfig(values=ext_config),
        accounting=ExternalEventAccounting(envelope=acct_env),
        continuation=ExternalEventContinuation(
            is_continuation=False,
            target_turn_id=target_turn_id,
        ),
        event=None,
    )
    async def _host_message_attachments(
        *,
        storage_turn_id: str,
        rollback_conversation_state: bool,
    ) -> Optional[IngressResult]:
        if not raw_attachments:
            return None

        store = getattr(app.state, "conversation_store", None)
        _av = get_settings().PLATFORM.HOSTED_SERVICES.AV
        enable_av = _av.APP_AV_SCAN
        av_timeout = _av.APP_AV_TIMEOUT_S
        from kdcube_ai_app.infra.gateway.safe_preflight import PreflightConfig, preflight_async
        cfg = PreflightConfig(av_scan=enable_av, av_timeout_s=av_timeout)

        attachment_errors: List[str] = []
        preflight_ok: List[RawAttachment] = []

        async def _safe_attachment_event(reason: str, message: str, att: RawAttachment, extra: Optional[Dict[str, Any]] = None) -> None:
            data = {
                "reason": reason,
                "message": message,
                "filename": att.name or "file",
                "mime": att.mime or "application/octet-stream",
                "show_in_timeline": True,
            }
            if extra:
                data.update(extra)
            try:
                await comm.service_event(
                    type="rate_limit.attachment_failure",
                    step="rate_limit",
                    status="error",
                    title="Attachment rejected",
                    agent="ingress.attachments",
                    data=data,
                )
            except Exception:
                logger.exception("failed to emit attachment failure event")

        total_bytes = text_bytes + sum(len(a.content or b"") for a in raw_attachments)
        total_limit_mb = int(MESSAGE_MAX_BYTES / (1024 * 1024))
        if total_bytes > MESSAGE_MAX_BYTES:
            attachment_errors.append("message_size_limit")
            await _safe_attachment_event(
                "message_size_limit",
                (
                    f"Total message size exceeds {total_limit_mb} MB (includes message text + attachments; "
                    f"per-file caps: images {int(MODALITY_MAX_IMAGE_BYTES / (1024 * 1024))} MB, "
                    f"PDFs {int(MODALITY_MAX_DOC_BYTES / (1024 * 1024))} MB)."
                ),
                raw_attachments[0],
                {"size_bytes": total_bytes, "max_bytes": MESSAGE_MAX_BYTES, "max_mb": total_limit_mb, "text_bytes": text_bytes},
            )

        for a in raw_attachments:
            if not a.content:
                attachment_errors.append("empty")
                await _safe_attachment_event("empty", "Attachment is empty.", a)
                continue
            mime = (a.mime or "").strip().lower()
            per_file_cap = MESSAGE_MAX_BYTES
            if mime in MODALITY_IMAGE_MIME:
                per_file_cap = MODALITY_MAX_IMAGE_BYTES
            elif mime in MODALITY_DOC_MIME:
                per_file_cap = MODALITY_MAX_DOC_BYTES
            if len(a.content) > per_file_cap:
                attachment_errors.append("size_limit")
                per_file_mb = int(per_file_cap / (1024 * 1024))
                await _safe_attachment_event(
                    "size_limit",
                    f"Attachment '{a.name or 'file'}' exceeds the per-file size limit ({per_file_mb} MB).",
                    a,
                    {"size_bytes": len(a.content), "max_bytes": per_file_cap},
                )
                continue
            if not store:
                attachment_errors.append("store_missing")
                await _safe_attachment_event(
                    "store_missing",
                    "Attachment store is unavailable.",
                    a,
                    {"size_bytes": len(a.content)},
                )
                continue
            if enable_av:
                try:
                    pf = await preflight_async(
                        a.content,
                        a.name or "file",
                        a.mime or "application/octet-stream",
                        cfg,
                    )
                except Exception:
                    attachment_errors.append("preflight_error")
                    await _safe_attachment_event("preflight_error", "Attachment preflight failed.", a)
                    continue
                if not pf.allowed:
                    attachment_errors.append("preflight_rejected")
                    await _safe_attachment_event(
                        "preflight_rejected",
                        "Attachment failed security checks.",
                        a,
                        {"reasons": pf.reasons},
                    )
                    continue
            preflight_ok.append(a)

        attachments: List[Dict[str, Any]] = []
        if not attachment_errors and preflight_ok:
            for a in preflight_ok:
                try:
                    uri, key, rn_f = await store.put_attachment(
                        tenant=tenant_id or "",
                        project=project_id or "",
                        user=session.user_id,
                        fingerprint=session.fingerprint,
                        conversation_id=conversation_id,
                        turn_id=storage_turn_id,
                        role="user",
                        filename=a.name or "file",
                        data=a.content,
                        mime=a.mime or "application/octet-stream",
                        user_type=session.user_type.value,
                        origin="user",
                    )
                except Exception:
                    attachment_errors.append("store_error")
                    await _safe_attachment_event("store_error", "Attachment hosting failed.", a)
                    break
                attachments.append({
                    "filename": a.name or "file",
                    "mime": a.mime or "application/octet-stream",
                    "size": len(a.content),
                    "meta": a.meta or {},
                    "hosted_uri": uri,
                    "key": key,
                    "rn": rn_f,
                    "role": "user",
                    "origin": "user",
                })

        if attachment_errors:
            if store:
                try:
                    _, user_or_fp = store._who_and_id(session.user_id, session.fingerprint)
                    await store.delete_turn(
                        tenant=tenant_id or "",
                        project=project_id or "",
                        user_type=session.user_type.value,
                        user_or_fp=user_or_fp,
                        conversation_id=conversation_id,
                        turn_id=storage_turn_id,
                    )
                except Exception:
                    logger.exception("failed to cleanup attachments after failure")
            if rollback_conversation_state:
                try:
                    res_reset = await app.state.conversation_browser.set_conversation_state(
                        tenant=tenant_id,
                        project=project_id,
                        user_id=session.user_id,
                        conversation_id=conversation_id,
                        new_state="idle",
                        by_instance=ingress.instance_id,
                        request_id=request_id,
                        last_turn_id=turn_id,
                        require_not_in_progress=False,
                        user_type=session.user_type.value,
                        bundle_id=bundle_id,
                    )
                    await chat_comm.emit_conv_status(
                        svc,
                        conv,
                        routing=routing,
                        state="idle",
                        updated_at=res_reset.get("updated_at", _iso()),
                        current_turn_id=res_reset.get("current_turn_id"),
                        completion="rollback",
                        target_sid=ingress.stream_id,
                    )
                except Exception:
                    logger.exception("failed to reset conv state after attachment failure")
            return IngressResult(
                ok=False,
                error_type="attachment_rejected",
                error="Attachment rejected; message not processed.",
                http_status=400,
            )

        if attachments:
            attachment_idx = 0
            for event in external_events:
                if not isinstance(event, dict):
                    continue
                if not str(event.get("type") or "").startswith("event.user.attachment"):
                    continue
                if attachment_idx >= len(attachments):
                    break
                hosted = dict(attachments[attachment_idx])
                event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                event_body = event_payload.get("event") if isinstance(event_payload.get("event"), dict) else {}
                event_body.update(hosted)
                event_payload["event"] = event_body
                event_payload.setdefault("mime", hosted.get("mime") or "application/octet-stream")
                event["payload"] = event_payload
                if not event.get("hosted_uri") and hosted.get("hosted_uri"):
                    event["hosted_uri"] = hosted.get("hosted_uri")
                attachment_idx += 1
            message_data["external_events"] = external_events
            if payload.request:
                payload.request.external_events = external_events
            try:
                await comm.event(
                    agent="tooling",
                    type="chat.attachments",
                    title=f"Attachments Ready ({len(attachments)})",
                    step="attachments",
                    status="completed",
                    data={"count": len(attachments), "items": attachments},
                )
            except Exception:
                logger.exception("failed to emit attachments step")

        return None

    if has_external_events and not external_event_reactive:
        try:
            conv_exists = await app.state.conversation_browser.conversation_exists(
                user_id=payload.user.user_id,
                conversation_id=conversation_id,
                bundle_id=payload.routing.bundle_id,
            )
            idle_res = await app.state.conversation_browser.set_conversation_state(
                tenant=payload.actor.tenant_id,
                project=payload.actor.project_id,
                user_id=payload.user.user_id,
                conversation_id=payload.routing.conversation_id,
                new_state="idle",
                by_instance=ingress.instance_id,
                request_id=request_id,
                last_turn_id=payload.routing.turn_id,
                require_not_in_progress=True,
                user_type=payload.user.user_type,
                bundle_id=payload.routing.bundle_id,
            )
        except Exception as e:
            logger.error("external event idle state update failed: %s", e)
            conv_exists = True
            idle_res = {
                "ok": False,
                "error": f"conversation state update failed: {e}",
                "error_type": "conversation_state_update_error",
                "updated_at": _iso(),
                "current_turn_id": turn_id,
            }
        if idle_res.get("ok", True):
            redis_async = getattr(app.state, "redis_async", None)
            if redis_async is None:
                err = "External event source unavailable"
                await chat_comm.emit_error(
                    svc,
                    conv,
                    error=err,
                    target_sid=ingress.stream_id,
                    session_id=session.session_id,
                )
                return IngressResult(
                    ok=False,
                    error_type="external_event_source_unavailable",
                    error=err,
                    http_status=503,
                )
            attachment_result = await _host_message_attachments(
                storage_turn_id=turn_id,
                rollback_conversation_state=False,
            )
            if attachment_result is not None:
                return attachment_result
            hosted_attachments = []
            try:
                hosted_attachments = hosted_external_event_attachments(external_events)
            except Exception:
                hosted_attachments = []
            external_event_source = build_conversation_external_event_source(
                redis=redis_async,
                tenant=tenant_id,
                project=project_id,
                conversation_id=conversation_id,
                user_id=session.user_id or session.fingerprint or "",
                agent_id=target_agent_id,
            )
            published_events = await _publish_external_event_batch(
                external_event_source=external_event_source,
                message_data=message_data,
                text=text,
                events=external_events,
                payload=payload,
                kind="external_event",
                explicit=True,
                target_turn_id=target_turn_id,
                active_turn_id_at_ingress=None,
                owner_turn_id=None,
                source=f"ingress.{ingress.transport}",
            )
            env = published_events[0]
            last_env = published_events[-1]
            logger.info(
                "[ingress.external] recorded non-reactive external events conversation=%s count=%s first_event_source_id=%s first_event_id=%s last_seq=%s target_turn=%s text=%r",
                conversation_id,
                len(published_events),
                _external_event_source_id(first_reactive_or_first_event),
                env.message_id,
                last_env.sequence,
                target_turn_id,
                (text or "")[:160],
            )
            try:
                if not conv_exists:
                    await chat_comm.emit_conv_status(
                        svc,
                        conv,
                        routing,
                        state="created",
                        updated_at=idle_res.get("updated_at", _iso()),
                        current_turn_id=payload.routing.turn_id,
                    )
                await chat_comm.emit_conv_status(
                    svc,
                    conv,
                    routing,
                    state="idle",
                    updated_at=idle_res.get("updated_at", _iso()),
                    current_turn_id=payload.routing.turn_id,
                )
                await comm.service_event(
                    type="event.external.recorded",
                    step="event.external",
                    status="completed",
                    title="External event recorded",
                    agent="ingress",
                    data={
                        "event_type": str((first_reactive_or_first_event or {}).get("type") or "event.external"),
                        "event_source_id": _external_event_source_id(first_reactive_or_first_event),
                        "reactive": False,
                        "is_continuation": False,
                        "message_len": len(text or ""),
                        "attachment_count": len(hosted_attachments),
                        "event_id": env.message_id,
                        "event_ids": [item.message_id for item in published_events],
                        "event_count": len(published_events),
                        "event_sequence": last_env.sequence,
                        "target_turn_id": target_turn_id,
                    },
                )
            except Exception:
                logger.debug("Failed to emit non-reactive external event service status", exc_info=True)
            return IngressResult(
                ok=True,
                task_id=task_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                session_id=session.session_id,
                user_type=session.user_type.value,
                queue_stats={
                    "external_event_sequence": last_env.sequence,
                    "external_event_count": len(published_events),
                    "live_owner_detected": False,
                },
                reason="external_event_recorded",
                is_continuation=False,
                active_turn_id=None,
                target_turn_id=target_turn_id,
                queued_turn_id=None,
                event_id=env.message_id,
                external_event_sequence=int(last_env.sequence or 0),
                live_owner_detected=False,
            )
        idle_error_type = idle_res.get("error_type") or ""
        if idle_error_type != "conversation_busy":
            error = idle_res.get("error") or "Conversation state update failed"
            await chat_comm.emit_error(
                svc,
                conv,
                error=error,
                target_sid=ingress.stream_id,
                session_id=session.session_id,
            )
            return IngressResult(
                ok=False,
                error_type=idle_error_type or "conversation_state_update_error",
                error=error,
                http_status=409,
            )

    # --- Conversation lock + state ---
    try:
        conv_exists = await app.state.conversation_browser.conversation_exists(
            user_id=payload.user.user_id,
            conversation_id=conversation_id,
            bundle_id=payload.routing.bundle_id,
        )

        set_res = await app.state.conversation_browser.set_conversation_state(
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            user_id=payload.user.user_id,
            conversation_id=payload.routing.conversation_id,
            new_state="in_progress",
            by_instance=ingress.instance_id,
            request_id=request_id,
            last_turn_id=payload.routing.turn_id,
            require_not_in_progress=True,
            user_type=payload.user.user_type,
            bundle_id=payload.routing.bundle_id,
        )
    except Exception as e:
        conv_state_update_error = f"conversation state update failed: {e}"
        logger.error(conv_state_update_error)
        conv_exists = True
        set_res = {"ok": False,
                   "error": conv_state_update_error,
                   "updated_at": _iso(),
                   "current_turn_id": turn_id,
                   "error_type": "conversation_state_update_error"
                   }

    if not set_res.get("ok", True):
        active_turn = set_res.get("current_turn_id")
        error = set_res.get("error") or "Conversation is busy (another tab/process is answering)."
        error_type = set_res.get("error_type") or "conversation_busy"
        if error_type == "conversation_busy":
            try:
                payload.continuation = ExternalEventContinuation(
                    is_continuation=True,
                    target_turn_id=target_turn_id,
                    active_turn_id=active_turn,
                )
                if payload.event is not None:
                    payload.event.kind = "external_event"
                    payload.event.agent_id = target_agent_id
                    payload.event.event_source_id = _external_event_source_id(first_reactive_or_first_event)
                    payload.event.event_id = str((first_reactive_or_first_event or {}).get("event_id") or "") or None
                    payload.event.logical_path = str((first_reactive_or_first_event or {}).get("logical_path") or "") or None
                    payload.event.type = str((first_reactive_or_first_event or {}).get("type") or "event.external")
                    payload.event.story_id = (first_reactive_or_first_event or {}).get("story_id")
                    payload.event.reactive = external_event_reactive
                external_event_source = None
                redis_async = getattr(app.state, "redis_async", None)
                if redis_async is not None:
                    external_event_source = build_conversation_external_event_source(
                        redis=redis_async,
                        tenant=tenant_id,
                        project=project_id,
                        conversation_id=conversation_id,
                        user_id=session.user_id or session.fingerprint or "",
                        agent_id=target_agent_id,
                    )
                owner = await external_event_source.get_owner() if external_event_source is not None else None
                owner_turn_id = str(owner.turn_id or "").strip() if owner else ""
                if external_event_source is not None:
                    storage_turn_id = str(active_turn or target_turn_id or turn_id or "").strip() or turn_id
                    attachment_result = await _host_message_attachments(
                        storage_turn_id=storage_turn_id,
                        rollback_conversation_state=False,
                    )
                    if attachment_result is not None:
                        return attachment_result
                    hosted_attachments = []
                    try:
                        hosted_attachments = hosted_external_event_attachments(external_events)
                    except Exception:
                        hosted_attachments = []
                    published_events = await _publish_external_event_batch(
                        external_event_source=external_event_source,
                        message_data=message_data,
                        text=text,
                        events=external_events,
                        payload=payload,
                        kind="external_event",
                        explicit=True,
                        target_turn_id=target_turn_id,
                        active_turn_id_at_ingress=active_turn,
                        owner_turn_id=owner_turn_id,
                        source=f"ingress.{ingress.transport}",
                    )
                    selected_event_id = str((first_reactive_or_first_event or {}).get("event_id") or "")
                    env = next((item for item in published_events if item.message_id == selected_event_id), published_events[0])
                    last_env = published_events[-1]
                    live_owner_detected = bool(owner_turn_id and active_turn and owner_turn_id == str(active_turn))
                    no_owner_wakeup_queued = False
                    no_owner_wakeup_stats: Dict[str, Any] = {}
                    no_owner_wakeup_error: Optional[IngressResult] = None
                    if external_event_reactive and not live_owner_detected:
                        try:
                            try:
                                wakeup_payload = env.task_payload_model()
                            except Exception:
                                wakeup_payload = payload
                            wakeup = _event_lane_wakeup_from_payload(
                                payload=wakeup_payload,
                                event=env,
                                tenant=tenant_id,
                                project=project_id,
                                user_id=session.user_id or session.fingerprint or "",
                                conversation_id=conversation_id,
                                agent_id=target_agent_id,
                                reason="reactive_continuation_no_live_owner",
                            )
                            queued, queue_reason, queued_stats = await chat_queue_manager.enqueue_chat_task_atomic(
                                session.user_type,
                                wakeup.model_dump(),
                                session,
                                request_context,
                                ingress.entrypoint,
                            )
                            no_owner_wakeup_stats = dict(queued_stats or {})
                            no_owner_wakeup_stats["queue_reason"] = queue_reason
                            if queued:
                                no_owner_wakeup_queued = True
                                for item in published_events:
                                    await external_event_source.mark_promoted(
                                        message_id=item.message_id,
                                        claimant_id="ingress.continuation_no_owner_wakeup",
                                        task_id=task_id,
                                    )
                            else:
                                logger.warning(
                                    "No-owner reactive continuation wakeup enqueue was rejected: conversation=%s task_id=%s reason=%s stats=%s",
                                    conversation_id,
                                    task_id,
                                    queue_reason,
                                    queued_stats,
                                )
                                for item in published_events:
                                    try:
                                        await external_event_source.mark_failed(
                                            message_id=item.message_id,
                                            claimant_id="ingress.continuation_no_owner_wakeup",
                                            reason=f"wakeup_enqueue_rejected:{queue_reason}",
                                        )
                                    except Exception:
                                        logger.debug(
                                            "Failed to mark rejected no-owner continuation event failed: event_id=%s",
                                            item.message_id,
                                            exc_info=True,
                                        )
                                no_owner_wakeup_error = IngressResult(
                                    ok=False,
                                    error_type="queue.enqueue_rejected",
                                    error=f"System under pressure - continuation rejected ({queue_reason})",
                                    http_status=503,
                                    retry_after=30,
                                    reason=queue_reason,
                                )
                        except Exception:
                            logger.exception(
                                "Failed to enqueue no-owner reactive continuation wakeup conversation=%s task_id=%s",
                                conversation_id,
                                task_id,
                            )
                            for item in published_events:
                                try:
                                    await external_event_source.mark_failed(
                                        message_id=item.message_id,
                                        claimant_id="ingress.continuation_no_owner_wakeup",
                                        reason="wakeup_enqueue_error",
                                    )
                                except Exception:
                                    logger.debug(
                                        "Failed to mark errored no-owner continuation event failed: event_id=%s",
                                        item.message_id,
                                        exc_info=True,
                                    )
                            no_owner_wakeup_error = IngressResult(
                                ok=False,
                                error_type="queue.enqueue_error",
                                error="Continuation could not be queued.",
                                http_status=500,
                            )
                    if no_owner_wakeup_error is not None:
                        return no_owner_wakeup_error
                    logger.info(
                        "[ingress.external] published open-turn event batch conversation=%s event_source_id=%s event_id=%s last_seq=%s count=%s active_turn=%s owner_turn=%s target_turn=%s live_owner=%s text=%r",
                        conversation_id,
                        _external_event_source_id(first_reactive_or_first_event),
                        env.message_id,
                        last_env.sequence,
                        len(published_events),
                        active_turn,
                        owner_turn_id,
                        target_turn_id,
                        live_owner_detected,
                        (text or "")[:160],
                    )
                    try:
                        if live_owner_detected:
                            await comm.service_event(
                                type="event.continuation.accepted",
                                step="event.continuation",
                                status="completed",
                                title="Continuation accepted",
                                agent="ingress",
                                data={
                                    "event_type": str((first_reactive_or_first_event or {}).get("type") or "event.external"),
                                    "event_source_id": _external_event_source_id(first_reactive_or_first_event),
                                    "reactive": external_event_reactive,
                                    "is_continuation": True,
                                    "message_len": len(text or ""),
                                    "attachment_count": len(hosted_attachments),
                                    "active_turn_id": active_turn,
                                    "event_id": env.message_id,
                                    "event_ids": [item.message_id for item in published_events],
                                    "event_count": len(published_events),
                                    "event_sequence": last_env.sequence,
                                    "target_turn_id": target_turn_id,
                                    "live_owner_detected": True,
                                },
                            )
                        else:
                            await comm.service_event(
                                type="event.continuation.accepted",
                                step="event.continuation",
                                status="completed",
                                title="Continuation accepted",
                                agent="ingress",
                                data={
                                    "event_type": str((first_reactive_or_first_event or {}).get("type") or "event.external"),
                                    "event_source_id": _external_event_source_id(first_reactive_or_first_event),
                                    "reactive": external_event_reactive,
                                    "is_continuation": True,
                                    "message_len": len(text or ""),
                                    "attachment_count": len(hosted_attachments),
                                    "active_turn_id": active_turn,
                                    "queued_turn_id": payload.routing.turn_id,
                                    "task_id": payload.meta.task_id,
                                    "event_id": env.message_id,
                                    "event_ids": [item.message_id for item in published_events],
                                    "event_count": len(published_events),
                                    "event_sequence": last_env.sequence,
                                    "live_owner_detected": False,
                                    "queued_wakeup": no_owner_wakeup_queued,
                                },
                            )
                    except Exception:
                        logger.debug("Failed to emit continuation accepted", exc_info=True)
                    return IngressResult(
                        ok=True,
                        task_id=task_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        session_id=session.session_id,
                        user_type=session.user_type.value,
                        queue_stats={
                            "external_event_sequence": last_env.sequence,
                            "external_event_count": len(published_events),
                            "live_owner_detected": live_owner_detected,
                            "queued_wakeup": no_owner_wakeup_queued,
                            **no_owner_wakeup_stats,
                        },
                        reason="external_event_accepted",
                        is_continuation=True,
                        active_turn_id=str(active_turn or "") or None,
                        target_turn_id=target_turn_id,
                        queued_turn_id=payload.routing.turn_id,
                        event_id=env.message_id,
                        external_event_sequence=int(last_env.sequence or 0),
                        live_owner_detected=live_owner_detected,
                    )
                err = "External event source unavailable"
                await chat_comm.emit_error(
                    svc,
                    conv,
                    error=err,
                    target_sid=ingress.stream_id,
                    session_id=session.session_id,
                )
                return IngressResult(
                    ok=False,
                    error_type="external_event_source_unavailable",
                    error=err,
                    http_status=503,
                )
            except Exception:
                logger.exception("Failed to store continuation message for conversation %s", conversation_id)
        try:
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing,
                state="in_progress",
                updated_at=set_res.get("updated_at", _iso()),
                current_turn_id=active_turn,
                target_sid=ingress.stream_id,
            )
            await chat_comm.emit_error(
                svc,
                conv,
                error=error,
                target_sid=ingress.stream_id,
                session_id=payload.routing.session_id,
            )
        except Exception:
            pass

        return IngressResult(
            ok=False,
            error_type=error_type,
            error=error,
            http_status=409,
        )

    # Emit conv_status created / in_progress
    try:
        if not conv_exists:
            # broadcast. client can update the conversation list
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing,
                state="created",
                updated_at=set_res["updated_at"],
                current_turn_id=payload.routing.turn_id,
            )
        # broadcast
        await chat_comm.emit_conv_status(
            svc,
            conv,
            routing,
            state="in_progress",
            updated_at=set_res["updated_at"],
            current_turn_id=payload.routing.turn_id,
        )
    except Exception:
        pass

    # --- Attachments (host after lock; reject entire message on any failure) ---
    attachment_result = await _host_message_attachments(
        storage_turn_id=turn_id,
        rollback_conversation_state=True,
    )
    if attachment_result is not None:
        return attachment_result

    redis_async = getattr(app.state, "redis_async", None)
    if redis_async is None:
        err = "External event source unavailable"
        await chat_comm.emit_error(
            svc,
            conv,
            error=err,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
        try:
            res_reset = await app.state.conversation_browser.set_conversation_state(
                tenant=payload.actor.tenant_id,
                project=payload.actor.project_id,
                user_id=payload.user.user_id,
                conversation_id=payload.routing.conversation_id,
                new_state="idle",
                by_instance=ingress.instance_id,
                request_id=request_id,
                last_turn_id=payload.routing.turn_id,
                require_not_in_progress=False,
                user_type=payload.user.user_type,
                bundle_id=payload.routing.bundle_id,
            )
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing,
                state="idle",
                updated_at=res_reset.get("updated_at", _iso()),
                current_turn_id=res_reset.get("current_turn_id"),
                completion="rollback",
                target_sid=ingress.stream_id,
            )
        except Exception:
            logger.exception("failed to reset conv state after missing external event source")
        return IngressResult(
            ok=False,
            error_type="external_event_source_unavailable",
            error=err,
            http_status=503,
        )

    event_kind = "external_event"
    event_source_id = _external_event_source_id(first_reactive_or_first_event)
    if payload.event is not None:
        payload.event.kind = event_kind
        payload.event.agent_id = target_agent_id
        payload.event.event_source_id = event_source_id
        payload.event.event_id = str((first_reactive_or_first_event or {}).get("event_id") or "") or None
        payload.event.logical_path = str((first_reactive_or_first_event or {}).get("logical_path") or "") or None
        payload.event.type = str((first_reactive_or_first_event or {}).get("type") or "event.external")
        payload.event.story_id = (first_reactive_or_first_event or {}).get("story_id")
        payload.event.reactive = external_event_reactive
        payload.event.source = f"ingress.{ingress.transport}"

    external_event_source = build_conversation_external_event_source(
        redis=redis_async,
        tenant=tenant_id,
        project=project_id,
        conversation_id=conversation_id,
        user_id=session.user_id or session.fingerprint or "",
        agent_id=target_agent_id,
    )
    published_events = await _publish_external_event_batch(
        external_event_source=external_event_source,
        message_data=message_data,
        text=text,
        events=external_events,
        payload=payload,
        kind=event_kind,
        explicit=True,
        target_turn_id=target_turn_id,
        active_turn_id_at_ingress=None,
        owner_turn_id=None,
        source=f"ingress.{ingress.transport}",
    )
    selected_event_id = str((first_reactive_or_first_event or {}).get("event_id") or "")
    env = next((item for item in published_events if item.message_id == selected_event_id), published_events[0])
    last_env = published_events[-1]
    try:
        wakeup_payload = env.task_payload_model()
    except Exception:
        wakeup_payload = payload
    wakeup = _event_lane_wakeup_from_payload(
        payload=wakeup_payload,
        event=env,
        tenant=tenant_id,
        project=project_id,
        user_id=session.user_id or session.fingerprint or "",
        conversation_id=conversation_id,
        agent_id=target_agent_id,
        reason="reactive_event",
    )

    # --- Enqueue wakeup ---
    enqueue_started_at = time.monotonic()
    try:
        success, reason, stats = await chat_queue_manager.enqueue_chat_task_atomic(
            session.user_type,
            wakeup.model_dump(),
            session,
            request_context,
            ingress.entrypoint,
        )
    except Exception as e:
        logger.exception("enqueue_chat_task_atomic failed: %s", e)
        success, reason, stats = False, "internal_error", {}
    enqueue_ms = int((time.monotonic() - enqueue_started_at) * 1000)
    try:
        created_at = float(payload.meta.created_at) if payload.meta else None
    except Exception:
        created_at = None
    ingress_to_enqueue_ms = None
    if created_at:
        try:
            ingress_to_enqueue_ms = int((time.time() - created_at) * 1000)
        except Exception:
            ingress_to_enqueue_ms = None

    logger.info(
        "enqueue_chat_task_atomic wakeup result task_id=%s event_id=%s event_seq=%s user_type=%s success=%s reason=%s enqueue_ms=%s ingress_to_enqueue_ms=%s queue_stats=%s",
        task_id,
        env.message_id,
        last_env.sequence,
        session.user_type.value,
        success,
        reason,
        enqueue_ms,
        ingress_to_enqueue_ms,
        stats,
    )

    if not success:
        try:
            await external_event_source.mark_failed(
                message_id=env.message_id,
                claimant_id="ingress.enqueue_failure",
                reason=f"wakeup_enqueue_failed:{reason}",
            )
        except Exception:
            logger.exception(
                "failed to mark lane event failed after wakeup enqueue failure event_id=%s",
                env.message_id,
            )
        # rollback state since nothing will process this turn
        try:
            res_reset = await app.state.conversation_browser.set_conversation_state(
                tenant=payload.actor.tenant_id,
                project=payload.actor.project_id,
                user_id=payload.user.user_id,
                conversation_id=payload.routing.conversation_id,
                new_state="idle",
                by_instance=ingress.instance_id,
                request_id=request_id,
                last_turn_id=payload.routing.turn_id,
                require_not_in_progress=False,
                user_type=payload.user.user_type,
                bundle_id=payload.routing.bundle_id,
            )
        except Exception as e:
            logger.error("Failed to reset conv state after enqueue failure: %s", e)
            res_reset = {"updated_at": _iso(), "current_turn_id": payload.routing.turn_id}

        retry_after = (
            30
            if session.user_type == UserType.ANONYMOUS
            else 45
            if session.user_type == UserType.REGISTERED
            else 60
        )
        try: # broadcast conv state rollback
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing=routing,
                state="idle",
                updated_at=res_reset.get("updated_at", _iso()),
                current_turn_id=res_reset.get("current_turn_id"),
                completion="rollback",
            )
            # legacy error for compat
            await chat_comm.emit_error(
                svc,
                conv,
                error=f"System under pressure - request rejected ({reason})",
                target_sid=ingress.stream_id,
                session_id=payload.routing.session_id,
            )
            # chat_service event (minimal inline env)
            env = {
                "type": "queue.enqueue_rejected",   # logical type
                "timestamp": _iso(),
                "ts": int(time.time() * 1000),
                "service": svc.model_dump(),
                "conversation": conv.model_dump(),
                "event": {
                    "step": "enqueue",
                    "status": "error",
                    "title": "Request rejected by queue",
                    "agent": "queue",
                },
                "data": {
                    "message": f"System under pressure - request rejected ({reason})",
                    "error_type": "queue.enqueue_rejected",
                    "http_status": 503,
                    "retry_after": retry_after,
                    "reason": reason,
                    "queue_stats": stats,
                },
                "route": "chat_service",
            }

            await chat_comm.emit(
                event="chat_service",
                data=env,
                tenant=svc.tenant,
                project=svc.project,
                session_id=payload.routing.session_id,
                target_sid=ingress.stream_id,
            )
        except Exception:
            pass

        return IngressResult(
            ok=False,
            error_type="queue.enqueue_rejected",
            error=f"System under pressure - request rejected ({reason})",
            http_status=503,
            retry_after=retry_after,
            reason=reason,
        )

    try:
        for item in published_events:
            await external_event_source.mark_promoted(
                message_id=item.message_id,
                claimant_id="ingress.initial_wakeup",
                task_id=task_id,
            )
    except Exception:
        logger.warning(
            "Failed to mark initially queued external event wakeup as promoted conversation=%s task_id=%s",
            conversation_id,
            task_id,
            exc_info=True,
        )

    # --- Success: emit start + ack payload ---
    try:
        await chat_comm.emit_start(
            svc,
            conv,
            message=(text[:100] + "..." if len(text) > 100 else text),
            queue_stats=stats,
            target_sid=ingress.stream_id,
            session_id=session.session_id,
        )
    except Exception:
        pass

    return IngressResult(
        ok=True,
        task_id=task_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        session_id=session.session_id,
        user_type=session.user_type.value,
        queue_stats={
            **(stats or {}),
            "external_event_sequence": last_env.sequence,
            "event_id": env.message_id,
            "external_event_count": len(published_events),
            "queue_payload_kind": "external_event_lane_wakeup",
        },
        event_id=env.message_id,
        external_event_sequence=int(last_env.sequence or 0),
    )


# -----------------------------
# Conversation status (shared)
# -----------------------------

async def get_conversation_status(
        *,
        app,
        chat_comm: ChatRelayCommunicator,
        session: UserSession,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
        conversation_id: Optional[str],
        stream_id: Optional[str],
        publish: bool = True,
) -> Dict[str, Any]:
    """
    Shared implementation for conv_status.get for SSE + WS.
    """
    reg = await _load_active_registry(app, tenant, project)
    allowed_bundle_ids = list((getattr(reg, "bundles", None) or {}).keys())
    if not allowed_bundle_ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if bundle_id and bundle_id not in allowed_bundle_ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv_id = conversation_id or session.session_id
    row = None
    try:
        row = await app.state.conversation_browser.idx.get_conversation_state_row(
            user_id=session.user_id,
            conversation_id=conv_id,
            bundle_ids=[bundle_id] if bundle_id else allowed_bundle_ids,
        )
    except Exception as e:
        logger.error("conv_status lookup failed user=%s conv=%s: %s", session.user_id, conv_id, e)

    if not row:
        exists = False
        try:
            exists = await app.state.conversation_browser.conversation_exists(
                user_id=session.user_id,
                conversation_id=conv_id,
                bundle_ids=[bundle_id] if bundle_id else allowed_bundle_ids,
            )
        except Exception as e:
            logger.error("conv_status existence fallback failed user=%s conv=%s: %s", session.user_id, conv_id, e)
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation not found")
        row = {}
    tags = row.get("tags", [])
    if "conv.state:in_progress" in tags:
        state = "in_progress"
    elif "conv.state:error" in tags:
        state = "error"
    else:
        state = "idle"
    ts = row.get("ts")
    updated_at = ts.isoformat() + "Z" if ts else _iso()
    payload_row = row.get("payload") or {}
    current_turn_id = payload_row.get("last_turn_id")
    if publish:
        if not reg:
            logger.warning(
                "conv_status.get: bundle registry unavailable; falling back to provided bundle_id/default placeholder "
                "(tenant=%s project=%s session=%s)",
                tenant,
                project,
                session.session_id,
            )
        bundle_id_val = bundle_id or (reg.default_bundle_id if reg else "unknown")
        if not bundle_id_val:
            bundle_id_val = "unknown"
            logger.warning(
                "conv_status.get: bundle_id missing; using placeholder (tenant=%s project=%s session=%s)",
                tenant,
                project,
                session.session_id,
            )

        routing = ExternalEventRouting(
            session_id=session.session_id,
            conversation_id=conv_id,
            turn_id=current_turn_id,
            socket_id=stream_id,
            bundle_id=bundle_id_val,
        )
        svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id, tenant=tenant, project=project)
        conv = ConversationCtx(
            session_id=session.session_id,
            conversation_id=conv_id,
            turn_id=current_turn_id,
        )

        try:
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing=routing,
                state=state,
                updated_at=updated_at,
                current_turn_id=current_turn_id,
                target_sid=stream_id,
            )
        except Exception as e:
            logger.error("emit_conv_status failed: %s", e)

    return {
        "conversation_id": conv_id,
        "state": state,
        "updated_at": updated_at,
        "current_turn_id": current_turn_id,
    }

def build_sse_request_context(
        request: Request,
        session: UserSession,
        bearer_token: Optional[str] = None,
        id_token: Optional[str] = None,
        *,
        client_ip_fallback: str = "sse",
) -> RequestContext:
    """
    Build RequestContext for SSE endpoints.

    Rule:
      - Use session.request_context if present.
      - If auth/id_token missing there, fill from explicit args.
      - If still missing, fall back to request headers.
    """

    base = getattr(session, "request_context", None)

    # client ip
    client_ip = client_ip_fallback
    try:
        if isinstance(base, RequestContext) and base.client_ip:
            client_ip = base.client_ip
        elif request.client and request.client.host:
            client_ip = request.client.host
    except Exception:
        pass

    # user agent
    user_agent = ""
    if isinstance(base, RequestContext) and base.user_agent:
        user_agent = base.user_agent
    else:
        user_agent = request.headers.get("user-agent", "")

    # authorization header
    auth_header = None
    if isinstance(base, RequestContext) and base.authorization_header:
        auth_header = base.authorization_header
    elif bearer_token:
        auth_header = f"Bearer {bearer_token}"
    else:
        auth_header, _ = resolve_auth_from_headers_and_cookies(
            request.headers.get("authorization"),
            None,
            request.cookies,
        )

    # id token
    resolved_id_token = None
    if isinstance(base, RequestContext) and base.id_token:
        resolved_id_token = base.id_token
    elif id_token:
        resolved_id_token = id_token
    else:
        _, resolved_id_token = resolve_auth_from_headers_and_cookies(
            None,
            request.headers.get(get_settings().AUTH.ID_TOKEN_HEADER_NAME)
            or request.headers.get(get_settings().AUTH.ID_TOKEN_HEADER_NAME.lower()),
            request.cookies,
        )

    return RequestContext(
        client_ip=client_ip,
        user_agent=user_agent,
        authorization_header=auth_header,
        id_token=resolved_id_token,
    )

def build_ws_connect_request_context(
        environ: dict,
        auth: Optional[dict],
) -> RequestContext:

    xff = environ.get("HTTP_X_FORWARDED_FOR")
    client_ip = (xff.split(",")[0].strip() if xff else None) or environ.get("REMOTE_ADDR") or "unknown"
    user_agent = environ.get("HTTP_USER_AGENT", "")
    bearer, id_token = resolve_socket_auth_tokens(auth, environ)
    auth_header = f"Bearer {bearer}" if bearer else None

    return RequestContext(
        client_ip=client_ip,
        user_agent=user_agent,
        authorization_header=auth_header,
        id_token=id_token
    )


def build_ws_chat_request_context() -> RequestContext:
    # For chat_message we often don’t have the raw environ;
    # you already used synthetic values.
    return RequestContext(
        client_ip="socket.io",
        user_agent="socket.io-client",
        authorization_header=None,
    )

async def upgrade_session_from_tokens(
        *,
        session: UserSession,
        ctx,
        bearer_token: Optional[str],
        id_token: Optional[str],
        gateway_adapter,
        chat_comm: ChatRelayCommunicator,
        stream_id: Optional[str],
) -> UserSession:
    # No tokens → nothing to do
    if not bearer_token and not id_token:
        return session

    # Already non-anonymous with a real user → keep as-is
    if session.user_type != UserType.ANONYMOUS and session.user_id:
        return session

    auth_manager = get_auth_manager()

    try:
        user = await auth_manager.authenticate_with_both(
            access_token=bearer_token or "",
            id_token=id_token,
        )
    except AuthenticationError as e:
        svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id or session.fingerprint)
        conv = ConversationCtx(
            session_id=session.session_id,
            conversation_id=session.session_id,
            turn_id=new_turn_id(),
        )
        # This won't work if this is the connection flow because the relay is not connected.
        await chat_comm.emit_error(
            svc,
            conv,
            error=f"Authentication failed: {e.message}",
            target_sid=stream_id,
            session_id=session.session_id,
        )
        raise e

    roles = user.roles or []
    if any(r in PRIVILEGED_ROLES for r in roles):
        user_type = UserType.PRIVILEGED
    else:
        user_type = UserType.REGISTERED

    user_data = {
        "user_id": user.id,
        "username": user.username,
        "roles": roles,
        "permissions": user.permissions or [],
        "email": user.email,
    }

    new_session = await gateway_adapter.gateway.get_or_create_session_with_econ_role(
        ctx,
        user_type=user_type,
        user_data=user_data,
    )

    # Persist context on the session if you like
    new_session.request_context = ctx
    return new_session

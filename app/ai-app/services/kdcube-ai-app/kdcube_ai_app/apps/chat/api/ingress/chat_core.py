# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/ingress/chat_core.py

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from fastapi import Request  # only if you put this in a place where FastAPI is available

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.sessions import RequestContext, UserType, UserSession
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskPayload, ChatTaskMeta, ChatTaskRouting, ChatTaskActor, ChatTaskUser,
    ChatTaskRequest, ChatTaskConfig, ChatTaskAccounting, ServiceCtx, ConversationCtx
)
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError
from kdcube_ai_app.infra.gateway.backpressure import BackpressureError
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerError
from kdcube_ai_app.infra.gateway.safe_preflight import PreflightConfig, preflight_async
from kdcube_ai_app.infra.namespaces import CONFIG
from kdcube_ai_app.tools.file_text_extractor import DocumentTextExtractor
from kdcube_ai_app.apps.chat.api.resolvers import get_auth_manager
from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle

from kdcube_ai_app.auth.AuthManager import AuthenticationError, PRIVILEGED_ROLES

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


TransportKind = Literal["sse", "socket"]

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


async def extract_attachments_text(
        raw_attachments: List[RawAttachment],
        *,
        max_mb: int,
) -> List[Dict[str, Any]]:
    """
    Shared attachment pipeline:
      - size limit
      - AV preflight
      - text extraction
    """
    if not raw_attachments:
        return []

    max_bytes = max_mb * 1024 * 1024
    enable_av = os.getenv("APP_AV_SCAN", "1") == "1"
    av_timeout = float(os.getenv("APP_AV_TIMEOUT_S", "3.0"))
    cfg = PreflightConfig(av_scan=enable_av, av_timeout_s=av_timeout)
    extractor = DocumentTextExtractor()

    out: List[Dict[str, Any]] = []
    for a in raw_attachments:
        if not a.content:
            continue

        if len(a.content) > max_bytes:
            logger.warning("attachment '%s' rejected: %d > max %d", a.name, len(a.content), max_bytes)
            continue

        mime = a.mime or "application/octet-stream"
        name = a.name or "file"

        pf = await preflight_async(a.content, name, mime, cfg)
        if not pf.allowed:
            logger.warning("attachment '%s' rejected by preflight: %s", name, pf.reasons)
            continue

        try:
            text, info = extractor.extract(a.content, name, mime)
        except Exception as ex:
            logger.error("extract failed for '%s': %s", name, ex)
            continue

        merged_meta = {**(info.meta or {}), **(a.meta or {})}
        out.append(
            {
                "name": name,
                "mime": info.mime,
                "ext": info.ext,
                "size": len(a.content),
                "meta": merged_meta,
                "warnings": info.warnings,
                "text": text,
            }
        )

    return out


def merge_attachments_into_message(
        message: str,
        attachments_text: List[Dict[str, Any]],
) -> str:
    """
    Produce the final message text seen by the worker (same semantics for SSE & WS).
    """
    base = (message or "").strip()
    if not attachments_text:
        return base

    lines: List[str] = [base, "ATTACHMENTS:"]
    for idx, a in enumerate(attachments_text, start=1):
        lines.append(f"{idx}. Name: {a['name']}; Mime: {a['mime']}")
        lines.append(a["text"])
        lines.append("...")
    return "\n".join(lines)


# -----------------------------
# Chat ingestion (shared)
# -----------------------------

@dataclass
class IngressConfig:
    transport: TransportKind                # "sse" or "socket"
    entrypoint: str                         # "/sse/chat" or "/socket.io/chat"
    component: str                          # "chat.sse" or "chat.socket"
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
    task_id = str(uuid.uuid4())
    turn_id = message_data.get("turn_id") or f"turn_{uuid.uuid4().hex[:8]}"
    conversation_id = message_data.get("conversation_id") or session.session_id
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

    svc = ServiceCtx(request_id=request_id, user=session.user_id, project=project_id, tenant=tenant_id)
    conv = ConversationCtx(
        session_id=session.session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    # Empty message → emit error via relay + let transport map to HTTP/WS
    if not text:
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

    spec_resolved = resolve_bundle(provided_bundle_id, override=None)
    if not spec_resolved:
        err = f"Unknown bundle_id '{provided_bundle_id}'"
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

    routing = ChatTaskRouting(
        session_id=session.session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        socket_id=ingress.stream_id,
        bundle_id=bundle_id,
    )

    payload = ChatTaskPayload(
        meta=ChatTaskMeta(
            task_id=task_id,
            created_at=time.time(),
            instance_id=ingress.instance_id,
        ),
        routing=routing,
        actor=ChatTaskActor(
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        user=ChatTaskUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.timezone,
            utc_offset_min=getattr(request_context, "user_utc_offset_min", None),
        ),
        request=ChatTaskRequest(
            message=text,
            chat_history=message_data.get("chat_history") or [],
            operation=message_data.get("operation") or message_data.get("command"),
            invocation=message_data.get("invocation"),
            payload=message_data.get("payload") or {},
            request_id=request_id,
        ),
        config=ChatTaskConfig(values=ext_config),
        accounting=ChatTaskAccounting(envelope=acct_env),
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

    # --- Enqueue ---
    try:
        success, reason, stats = await chat_queue_manager.enqueue_chat_task_atomic(
            session.user_type,
            payload.model_dump(),
            session,
            request_context,
            ingress.entrypoint,
        )
    except Exception as e:
        logger.exception("enqueue_chat_task_atomic failed: %s", e)
        success, reason, stats = False, "internal_error", {}

    if not success:
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
        try: # broadcast
            await chat_comm.emit_conv_status(
                svc,
                conv,
                routing=routing,
                state="idle",
                updated_at=res_reset.get("updated_at", _iso()),
                current_turn_id=res_reset.get("current_turn_id"),
                completion="rollback",
            )
            await chat_comm.emit_error(
                svc,
                conv,
                error=f"System under pressure - request rejected ({reason})",
                target_sid=ingress.stream_id,
                session_id=payload.routing.session_id,
            )
        except Exception:
            pass

        return IngressResult(
            ok=False,
            error_type="enqueue_rejected",
            error=f"System under pressure - request rejected ({reason})",
            http_status=503,
            retry_after=retry_after,
            reason=reason,
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
        queue_stats=stats,
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
    conv_id = conversation_id or session.session_id
    row = None
    try:
        row = await app.state.conversation_browser.idx.get_conversation_state_row(
            user_id=session.user_id,
            conversation_id=conv_id,
        )
    except Exception as e:
        logger.error("conv_status lookup failed user=%s conv=%s: %s", session.user_id, conv_id, e)

    if not row:
        state = "idle"
        updated_at = _iso()
        current_turn_id = None
    else:
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
        spec_resolved = resolve_bundle(bundle_id, override=None)

        routing = ChatTaskRouting(
            session_id=session.session_id,
            conversation_id=conv_id,
            turn_id=current_turn_id,
            socket_id=stream_id,
            bundle_id=spec_resolved.id if spec_resolved else None,
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
        auth_header = request.headers.get("authorization")

    # id token
    resolved_id_token = None
    if isinstance(base, RequestContext) and base.id_token:
        resolved_id_token = base.id_token
    elif id_token:
        resolved_id_token = id_token
    else:
        resolved_id_token = request.headers.get(CONFIG.ID_TOKEN_HEADER_NAME)

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
    bearer = (auth or {}).get("bearer_token")
    id_token = (auth or {}).get("id_token")
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
            turn_id=f"turn_{uuid.uuid4().hex[:8]}",
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

    new_session = await gateway_adapter.gateway.session_manager.get_or_create_session(
        ctx,
        user_type=user_type,
        user_data=user_data,
    )

    # Persist context on the session if you like
    new_session.request_context = ctx
    return new_session
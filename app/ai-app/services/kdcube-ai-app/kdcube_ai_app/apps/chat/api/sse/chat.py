# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/sse/chat.py

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from kdcube_ai_app.apps.chat.api.resolvers import get_user_session_dependency
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.util import _iso
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.sessions import UserSession, UserType

from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ServiceCtx, ConversationCtx,
)
from kdcube_ai_app.apps.chat.api.ingress.chat_core import (
    IngressConfig,
    RawAttachment,
    run_gateway_checks,
    map_gateway_error,
    extract_attachments_text,
    merge_attachments_into_message,
    process_chat_message,
    get_conversation_status, build_sse_request_context, upgrade_session_from_tokens,
)

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 3

def _sse_frame(event: str, data: Dict[str, Any], *, event_id: Optional[str] = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    lines: List[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    for line in payload.splitlines():
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"

async def _reject_anonymous(
        *,
        endpoint: str,
        session: UserSession,
        chat_comm,
        stream_id: Optional[str],
):
    if session.user_type != UserType.ANONYMOUS:
        return

    svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id or session.fingerprint)
    conv = ConversationCtx(
        session_id=session.session_id,
        conversation_id=session.session_id,
        turn_id=f"turn_{uuid.uuid4().hex[:8]}",
    )
    err_detail = f"Anonymous sessions are not allowed for {endpoint}"

    # keep error payload consistent with your other emit_error usage
    await chat_comm.emit_error(
        svc,
        conv,
        error=err_detail,
        target_sid=stream_id,
        session_id=session.session_id,
    )
    raise HTTPException(status_code=401, detail=err_detail)

# -----------------------------
# In-process SSE Hub (fan-out)
# -----------------------------

@dataclass(frozen=True)
class Client:
    tenant: Optional[str]
    project: Optional[str]
    session_id: str
    stream_id: Optional[str]
    queue: asyncio.Queue[str]      # queue of SSE frames (strings)

class SSEHub:
    """
    One Redis relay subscription per process -> fan out to all connected SSE clients.
    """
    def __init__(self, chat_comm: ChatRelayCommunicator):
        self.chat_comm = chat_comm
        self._by_session: Dict[str, List[Client]] = {}
        self._lock = asyncio.Lock()
        self._relay_started = False

    async def start(self):
        # Just mark that the hub is ready. We'll start the Redis listener
        # lazily on the first real subscription.
        if self._relay_started:
            logger.info("[SSEHub] start() called but already started")
            return

        logger.info(
            "[SSEHub] start() – hub initialized; listener will start on first session subscription"
        )
        self._relay_started = True

    async def stop(self):
        if not self._relay_started:
            return


        # Unblock all generators by pushing a sentinel into their queues
        async with self._lock:
            for clients in self._by_session.values():
                for c in clients:
                    try:
                        c.queue.put_nowait(": shutdown\n\n")
                    except Exception:
                        pass

        await self.chat_comm.unsubscribe()
        self._relay_started = False
        logger.info("[SSEHub] unsubscribed from relay channel(s)")

    async def register(self, client: Client):
        async with self._lock:
            lst = self._by_session.setdefault(client.session_id, [])
            lst.append(client)

        # Acquire per-session channel via central refcounting
        await self.chat_comm.acquire_session_channel(
            client.session_id,
            callback=self._on_relay,
            tenant=client.tenant,
            project=client.project,
        )
        logger.info(
            "[SSEHub] register session=%s stream_id=%s tenant=%s project=%s total_now=%s hub_id=%s relay_id=%s",
            client.session_id, client.stream_id, client.tenant, client.project,
            len(self._by_session.get(client.session_id, [])),
            id(self), id(self.chat_comm)
        )

    async def unregister(self, client: Client):
        async with self._lock:
            lst = self._by_session.get(client.session_id, [])
            self._by_session[client.session_id] = [c for c in lst if c is not client]
            if not self._by_session[client.session_id]:
                self._by_session.pop(client.session_id, None)

        # IMPORTANT: release per client (not only last)
        await self.chat_comm.release_session_channel(client.session_id,
                                                     tenant=client.tenant,
                                                     project=client.project,
                                                     )

        logger.info(
            "[SSEHub] unregister session=%s stream_id=%s tenant=%s project=%s total_now=%s hub_id=%s relay_id=%s",
            client.session_id, client.stream_id, client.tenant, client.project,
            len(self._by_session.get(client.session_id, [])),
            id(self), id(self.chat_comm)
        )


    # Relay callback invoked by ChatRelayCommunicator
    async def _on_relay(self, message: dict):
        """
        message = { event, data, target_sid?, session_id? }
        """
        try:
            event = message.get("event")
            data = message.get("data") or {}
            target_sid = message.get("target_sid")
            room = message.get("session_id")

            if not event or not room:
                logger.warning(f"[SSEHub._on_relay] unrouted message {message}")
                return  # we only fan-out messages scoped to a session room. ignore malformed / global messages

            logger.debug("[SSEHub._on_relay] received message for session session=%s stream_id=%s", room, target_sid)

            # First check if we even have listeners for this session
            async with self._lock:
                recipients = list(self._by_session.get(room, []))

            if not recipients:
                # Nothing to do on this worker
                logger.warning("[SSEHub._on_relay] no recipients found for message for session session=%s stream_id=%s", room, target_sid)
                return

            # Only now build the SSE frame
            frame = _sse_frame(event, data, event_id=str(uuid.uuid4()))

            if target_sid:
                # DM: only client with matching stream_id
                for c in recipients:
                    if c.stream_id and c.stream_id == target_sid:
                        logger.debug("[SSEHub._on_relay] DIRECT SEND the message for session session=%s stream_id=%s to recipient session=%s stream_id=%s", room, target_sid, c.session_id, c.stream_id)
                        self._enqueue(c, frame)
                    else:
                        # logger.debug("[SSEHub._on_relay] SKIP DIRECT SEND the message for session session=%s stream_id=%s to recipient session=%s stream_id=%s", room, target_sid, c.session_id, c.stream_id)
                        pass
            else:
                # Broadcast to all clients in the same session
                for c in recipients:
                    logger.debug("[SSEHub._on_relay] BROADCAST the message for session session=%s stream_id=%s to recipient session=%s stream_id=%s", room, target_sid, c.session_id, c.stream_id)
                    self._enqueue(c, frame)

        except Exception as e:
            logger.error("[SSEHub] relay fan-out failed: %s", e)

    def _enqueue(self, client: Client, frame: str):
        # Small bounded queues per client; drop oldest on overflow
        q = client.queue
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(frame)
            except Exception:
                pass


def create_sse_router(
    *,
    app,
    gateway_adapter,
    chat_queue_manager,
    instance_id: str,
    redis_url: str,
) -> APIRouter:
    """
    Mount with:
        app.state.sse_hub = SSEHub(chat_comm)
        app.add_event_handler("startup", app.state.sse_hub.start)
        app.add_event_handler("shutdown", app.state.sse_hub.stop)
        app.include_router(create_sse_router(...), prefix="/sse", tags=["SSE"])
    """
    router = APIRouter()
    app.state.sse_enabled = True

    chat_comm = ChatRelayCommunicator(
        redis_url=redis_url,
        channel="chat.events",
        orchestrator_identity=os.getenv("CB_RELAY_IDENTITY"),
    )

    # Ensure hub exists on app
    if not hasattr(app.state, "sse_hub"):
        app.state.sse_hub = SSEHub(chat_comm)

    settings = get_settings()

    # ---------- STREAM ----------
    @router.get("/stream")
    async def sse_stream(
        request: Request,
        stream_id: str,
        session: UserSession = Depends(get_user_session_dependency()),
        # direct-message id for this connection
        # Query auth (parity with Socket.IO connect)
        user_session_id: Optional[str] = None,
        bearer_token: Optional[str] = None,
        id_token: Optional[str] = None,
        project: Optional[str] = None,
        tenant: Optional[str] = None,
    ):

        # --- Resolve session exactly like WS connect ---
        if user_session_id:
            try:
                sess = await gateway_adapter.gateway.session_manager.get_session_by_id(user_session_id)
                if not sess:
                    raise HTTPException(status_code=401, detail="Unknown session")
                session = sess  # override the cookie-derived session
            except HTTPException:
                raise
            except Exception as e:
                logger.error("SSE load session failed for id=%s: %s", user_session_id, e)
                raise HTTPException(status_code=401, detail="Invalid session")
        logger.info(f"[sse_stream]. user_session_id={user_session_id}; session.session_id={session.session_id}; stream_id={stream_id}")

        # Gateway protections for opening a stream
        ctx = build_sse_request_context(request, bearer_token=bearer_token, id_token=id_token, session=session)
        try:
            session = await upgrade_session_from_tokens(
                session=session,
                ctx=ctx,
                bearer_token=bearer_token,
                id_token=id_token,
                gateway_adapter=gateway_adapter,
                chat_comm=chat_comm,
                stream_id=stream_id,
            )
        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail="Invalid token") from e
        logger.info(f"[sse_stream]. After upgrade: user_session_id={user_session_id}; session.session_id={session.session_id}; stream_id={stream_id};")
        if os.environ.get("CHAT_SSE_REJECT_ANONYMOUS", "1") == "1":
            await _reject_anonymous(
                endpoint="/sse/stream",
                session=session,
                stream_id=stream_id,
                chat_comm=chat_comm,
            )
        # gw_res = await run_gateway_checks(
        #     gateway_adapter=gateway_adapter,
        #     session=session,
        #     context=ctx,
        #     endpoint="/sse/stream",
        # )
        # if gw_res.kind != "ok":
        #     mapped = map_gateway_error(gw_res)
        #     svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
        #     conv = ConversationCtx(
        #         session_id=session.session_id,
        #         conversation_id=session.session_id,
        #         turn_id=f"turn_{uuid.uuid4().hex[:8]}",
        #     )
        #     await chat_comm.emit_error(
        #         svc,
        #         conv,
        #         error=mapped["message"],
        #         target_sid=stream_id,
        #         session_id=session.session_id,
        #     )
        #     detail = {"error": mapped["message"]}
        #     if mapped.get("retry_after") is not None:
        #         detail["retry_after"] = mapped["retry_after"]
        #     raise HTTPException(status_code=mapped["status"], detail=detail)

        # Prepare per-connection queue (bounded)
        max_q = int(os.getenv("SSE_CLIENT_QUEUE", "1000"))
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=max_q)

        tenant = tenant or settings.TENANT
        project = project or settings.PROJECT
        client = Client(session_id=session.session_id,
                        stream_id=stream_id, queue=q,
                        tenant=tenant,
                        project=project,)

        # Register client
        await app.state.sse_hub.register(client)

        async def gen():

            # Initial ready
            hello = {
                "timestamp": _iso(),
                "session_id": session.session_id,
                "user_type": session.user_type.value,
                **({"stream_id": stream_id} if stream_id else {}),
                **({"tenant": tenant} if tenant else {}),
                **({"project": project} if project else {}),
            }
            yield _sse_frame("ready", hello, event_id=str(uuid.uuid4()))

            try:
                while True:
                    # 1) if server is shutting down, exit
                    if getattr(app.state, "shutting_down", False):
                        logger.info(f"[sse_stream] Server shutting down; closing SSE for session={session.session_id} stream_id={stream_id}")
                        break

                    # 2) if client disconnected, exit
                    try:
                        if await request.is_disconnected():
                            logger.info(f"[sse_stream] Client disconnected; closing SSE for session={session.session_id} stream_id={stream_id}")
                            break
                    except RuntimeError:
                        # request might be finalized already in some edge cases
                        break

                    try:
                        # Wait up to KEEPALIVE_SECONDS for the next frame
                        frame = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                        yield frame
                    except asyncio.TimeoutError:
                        # On timeout, if shutting down, don't send keepalive, just break
                        if getattr(app.state, "shutting_down", False):
                            logger.info(f"[sse_stream] Timeout during shutdown; closing SSE for session={session.session_id} stream_id={stream_id}")
                            break
                        # No frames in this window → send keepalive
                        yield ": keepalive\n\n"

            except asyncio.CancelledError:
                # Uvicorn/gunicorn is cancelling us as part of shutdown
                logger.info(f"[sse_stream] Cancelled; closing SSE for session={session.session_id} stream_id={stream_id}")
                # Re-raise so Uvicorn/Starlette know this request is done
                raise
            finally:
                await app.state.sse_hub.unregister(client)
                logger.info(f"[sse_stream] Cleaned up: for session={session.session_id} stream_id={stream_id}")

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",      # nginx
                # "Connection": "keep-alive",
            },
        )

    # ---------- CHAT (enqueue) ----------
    @router.post("/chat")
    async def sse_chat(
            request: Request,
            stream_id: str,
            session: UserSession = Depends(get_user_session_dependency()),
            # multipart support (attachments)
            message: Optional[str] = Form(None),
            attachment_meta: Optional[str] = Form(None),
            files: List[UploadFile] = File(default=[]),
    ):

        ctx = build_sse_request_context(request, session=session)
        if os.environ.get("CHAT_SSE_REJECT_ANONYMOUS", "1") == "1":
            await _reject_anonymous(
                endpoint="/sse/chat",
                session=session,
                stream_id=stream_id,
                chat_comm=chat_comm,
            )
        # Check conversation status
        gw_res = await run_gateway_checks(
            gateway_adapter=gateway_adapter,
            session=session,
            context=ctx,
            endpoint="/sse/chat",
        )
        hub = router.state.sse_hub
        client = next(iter(hub._by_session.get(session.session_id) or []), None) or {}
        logger.info(f"[/conv_status.get] Received request for session={session.session_id} stream_id={stream_id}")

        settings = get_settings()
        tenant = client.tenant if client else settings.TENANT
        project = client.project if client else settings.PROJECT
        if gw_res.kind != "ok":
            mapped = map_gateway_error(gw_res)
            svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id, tenant=tenant, project=project)
            conv = ConversationCtx(
                session_id=session.session_id,
                conversation_id=session.session_id,
                turn_id=f"turn_{uuid.uuid4().hex[:8]}",
            )
            # 1) New chat_service event
            env = {
                "type": f"gateway.{gw_res.kind}",  # e.g. "gateway.rate_limit"
                "timestamp": _iso(),
                "ts": int(time.time() * 1000),
                "service": svc.model_dump(),
                "conversation": conv.model_dump(),
                "event": {
                    "step": "gateway",
                    "status": "error",
                    "title": "Gateway rejected request",
                    "agent": "gateway",
                },
                "data": {
                    "message": mapped["message"],
                    "error_type": mapped["error_type"],
                    "http_status": mapped["status"],
                    "retry_after": mapped.get("retry_after"),
                    "endpoint": "/sse/chat",
                },
                "route": "chat_service",
            }

            await chat_comm.emit(
                event="chat_service",
                data=env,
                tenant=svc.tenant,
                project=svc.project,
                session_id=session.session_id,
                target_sid=stream_id,
            )

            # 2) Legacy chat_error. We can remove this after clients migrate to chat_service event
            await chat_comm.emit_error(
                svc,
                conv,
                error=mapped["message"],
                target_sid=stream_id,
                session_id=session.session_id,
            )
            # 3) HTTP error response
            detail = {"error": mapped["message"]}
            if mapped.get("retry_after") is not None:
                detail["retry_after"] = mapped["retry_after"]
            raise HTTPException(status_code=mapped["status"], detail=detail)

        # ---------- parse body (JSON or multipart) ----------
        if message is not None:
            try:
                body = json.loads(message)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid 'message' JSON in multipart form")
            body_attachment_meta_json = attachment_meta
        else:
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
            body_attachment_meta_json = (body or {}).get("attachment_meta")

        message_data = (body or {}).get("message") or {}

        # Accept either:
        #  - { message: "hello" }
        #  - { text: "hello" }
        #  - { message: { msg: "hello" } }
        raw_msg = message_data.get("text")
        if raw_msg is None:
            raw_msg = message_data.get("message")

        if isinstance(raw_msg, dict):
            base_text = (
                    raw_msg.get("text")
                    or raw_msg.get("message")
                    or raw_msg.get("msg")
                    or ""
            )
        else:
            base_text = raw_msg or ""

        base_text = str(base_text).strip()

        # ---------- attachments (transport → RawAttachment) ----------
        raw_attachments: List[RawAttachment] = []
        max_mb = 20
        try:
            max_mb = int(os.environ.get("CHAT_MAX_UPLOAD_MB", "20"))
        except Exception:
            pass

        attachment_meta_list: List[Dict[str, Any]] = []
        if files:
            if body_attachment_meta_json and isinstance(body_attachment_meta_json, str):
                try:
                    attachment_meta_list = json.loads(body_attachment_meta_json) or []
                except Exception:
                    attachment_meta_list = []

            by_name = {m.get("filename") or m.get("name"): m for m in (attachment_meta_list or [])}

            for f in files:
                try:
                    raw = await f.read()
                except Exception:
                    continue
                if not raw:
                    continue

                meta_in = by_name.get(f.filename) or {}
                cleaned_meta = {
                    k: v for k, v in meta_in.items() if k not in ("filename", "name")
                }

                raw_attachments.append(
                    RawAttachment(
                        content=raw,
                        name=f.filename or "file",
                        mime=f.content_type or "application/octet-stream",
                        meta=cleaned_meta,
                    )
                )

        attachments_text: List[Dict[str, Any]] = []
        if raw_attachments:
            attachments_text = await extract_attachments_text(
                raw_attachments,
                max_mb=max_mb,
            )

        # Merge into final message text
        text = merge_attachments_into_message(base_text, attachments_text)

        # ---------- delegate to core business logic ----------
        ingress_cfg = IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id=instance_id,
            stream_id=stream_id,
            metadata={"stream_id": stream_id, "entrypoint": "/sse/chat"},
        )

        result = await process_chat_message(
            app=app,
            chat_queue_manager=chat_queue_manager,
            chat_comm=chat_comm,
            session=session,
            request_context=ctx,
            message_data=message_data,
            message_text=text,
            ingress=ingress_cfg,
        )

        if not result.ok:
            # process_chat_message already emitted error via chat_comm
            error_type = result.error_type or "bad_request"
            status = result.http_status or 400

            # keep retry_after semantics for pressure errors
            detail: Any
            if error_type in ("enqueue_rejected",):
                detail = {
                    "error": "System under pressure",
                    "reason": result.reason,
                    "retry_after": result.retry_after,
                }
            elif error_type == "missing_message":
                detail = result.error or 'Missing "message"'
            elif error_type == "unknown_bundle":
                detail = result.error or "Unknown bundle_id"
            elif error_type == "conversation_busy":
                detail = result.error or "Conversation is busy"
            elif error_type == "input_too_long":
                detail = result.error or "Input is too long"
            else:
                detail = result.error or "Chat request failed"

            raise HTTPException(status_code=status, detail=detail)

        # HTTP ack – everything else goes over SSE stream
        return {
            "status": "processing_started",
            "task_id": result.task_id,
            "session_id": result.session_id,
            "user_type": result.user_type,
            "message": "Queued; streaming via SSE",
        }

    # ---------- conv_status.get (parity, now via core) ----------
    @router.post("/conv_status.get")
    async def sse_conv_status_get(
            data: Dict[str, Any],
            session: UserSession = Depends(get_user_session_dependency()),
    ):
        conv_id = (data or {}).get("conversation_id") or session.session_id
        bundle_id = data.get("bundle_id")
        stream_id = data.get("stream_id")
        hub = router.state.sse_hub
        client = next(iter(hub._by_session.get(session.session_id) or []), None) or {}
        logger.info(f"[/conv_status.get] Received request for session={session.session_id} stream_id={stream_id}")

        settings = get_settings()
        tenant = client.tenant if client else settings.TENANT
        project = client.project if client else settings.PROJECT

        status = await get_conversation_status(
            app=app,
            chat_comm=app.state.chat_comm if hasattr(app.state, "chat_comm") else chat_comm,
            session=session,
            bundle_id=bundle_id,
            conversation_id=conv_id,
            stream_id=stream_id,
            tenant=tenant,
            project=project,
        )
        logger.info(f"[/conv_status.get] Completed request for session={session.session_id} stream_id={stream_id}")
        return status

    return router

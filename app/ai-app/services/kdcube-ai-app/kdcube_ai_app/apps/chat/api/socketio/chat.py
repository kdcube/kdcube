# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/socketio/chat.py
"""
Modular Socket.IO chat handler with gateway integration and Redis relay.
Redis pub/sub listener relays chat events (chat.events) to clients
uses a standardized ChatTaskPayload schema (chat/sdk/protocol.py).
"""

from __future__ import annotations
import os
import uuid
import time
import logging
from datetime import datetime
from typing import Any, Dict
import hashlib
import re

import socketio

from kdcube_ai_app.apps.chat.api.resolvers import get_tenant
from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError
from kdcube_ai_app.infra.gateway.backpressure import BackpressureError
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerError

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskPayload, ChatTaskMeta, ChatTaskRouting, ChatTaskActor, ChatTaskUser,
    ChatTaskRequest, ChatTaskConfig, ChatTaskAccounting,
    ServiceCtx, ConversationCtx,
)
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.infra.gateway.safe_preflight import PreflightConfig, preflight_async
from kdcube_ai_app.tools.file_text_extractor import DocumentTextExtractor

logger = logging.getLogger(__name__)


class SocketIOChatHandler:
    """
    Socket.IO chat handler with FULL gateway gating + Redis relay.
    Emits to clients only via ChatRelayCommunicator (same one the processor uses).
    """

    def __init__(
        self,
        app,
        gateway_adapter,
        chat_queue_manager,
        allowed_origins,
        instance_id: str,
        redis_url: str,
        chat_comm: ChatRelayCommunicator,   # ← SAME communicator used by processor
    ):
        self.app = app
        self.gateway_adapter = gateway_adapter
        self.chat_queue_manager = chat_queue_manager
        self.allowed_origins = allowed_origins
        self.instance_id = instance_id
        self.redis_url = redis_url

        self._comm = chat_comm
        self._listener_started = False

        from pathlib import Path

        # self.upload_dir = Path(os.environ.get("CHAT_UPLOAD_DIR", "./var/chat_uploads"))
        # self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.max_upload_mb = int(os.environ.get("CHAT_MAX_UPLOAD_MB", "20"))

        self.sio = self._create_socketio_server()
        self._setup_event_handlers()

    # ---------- Socket.IO core ----------

    def _create_socketio_server(self):
        try:
            mgr = socketio.AsyncRedisManager(self.redis_url)
            max_mb = getattr(self, "max_upload_mb", 20)
            max_bytes = int(max_mb) * 1024 * 1024

            sio = socketio.AsyncServer(
                cors_allowed_origins=self.allowed_origins,
                async_mode="asgi",
                client_manager=mgr,
                logger=True,
                engineio_logger=True,
                max_http_buffer_size=max_bytes
            )
            return sio
        except Exception as e:
            logger.exception("Socket.IO init failed: %s", e)
            return None

    def _setup_event_handlers(self):
        if not self.sio:
            return

        @self.sio.on("connect")
        async def _on_connect(sid, environ, auth):
            return await self._handle_connect(sid, environ, auth)

        @self.sio.on("disconnect")
        async def _on_disconnect(sid):
            return await self._handle_disconnect(sid)

        @self.sio.on("chat_message")
        async def _on_chat_message(sid, *args):
            return await self._handle_chat_message(sid,  *args)

        @self.sio.on("ping")
        async def _on_ping(sid, data):
            await self.sio.emit("pong", {"timestamp": datetime.utcnow().isoformat() + "Z"}, to=sid)

        @self.sio.on("conv_status.get")
        async def _on_conv_status_subscribe(sid, data):
            return await self._handle_conv_status_subscribe(data, sid)

    # ---------- Relay (pub/sub -> socket) ----------

    async def _on_pubsub_message(self, message: dict):
        """
        Relay events published by workers/processors to connected sockets.
        { event, data, target_sid?, session_id? }
        """
        try:
            event = message.get("event")
            data = message.get("data") or {}
            target_sid = message.get("target_sid")
            session_id = message.get("session_id")
            if not event:
                return
            if target_sid:
                await self.sio.emit(event, data, room=target_sid)
            elif session_id:
                await self.sio.emit(event, data, room=session_id)
        except Exception as e:
            logger.error("[chat relay] emit failed: %s", e)

    async def start(self):
        if self._listener_started or not self.sio:
            return
        await self._comm.subscribe(self._on_pubsub_message)
        self._listener_started = True
        logger.info("Socket.IO chat handler subscribed to relay channel.")

    async def stop(self):
        if not self._listener_started:
            return
        await self._comm.unsubscribe()
        self._listener_started = False

    # ---------- CONNECT with GATING (restored) ----------

    async def _handle_connect(self, sid, environ, auth):
        logger.info("WS connect attempt sid=%s", sid)

        # origin allowlist
        origin = environ.get("HTTP_ORIGIN")
        if self.allowed_origins not in (None, [], ["*"]):
            if not origin or (origin not in self.allowed_origins and "*" not in self.allowed_origins):
                logger.warning("WS connect rejected: origin '%s' not allowed", origin)
                return False

        user_session_id = (auth or {}).get("user_session_id")
        if not user_session_id:
            logger.warning("WS connect rejected: missing user_session_id")
            return False

        # load session
        try:
            session = await self.gateway_adapter.gateway.session_manager.get_session_by_id(user_session_id)
            if not session:
                logger.warning("WS connect rejected: unknown session_id=%s", user_session_id)
                return False
        except Exception as e:
            logger.error("WS connect failed to load session %s: %s", user_session_id, e)
            return False

        # optional bearer validation (registered/privileged)
        try:
            bearer_token = (auth or {}).get("bearer_token")
            id_token = (auth or {}).get("id_token")
            if bearer_token and session.user_type.value != "anonymous":
                user = await self.gateway_adapter.gateway.auth_manager.authenticate_with_both(bearer_token, id_token)
                claimed_user_id = getattr(user, "sub", None) or user.username
                if session.user_id and claimed_user_id and session.user_id != claimed_user_id:
                    logger.warning(
                        "WS connect rejected: bearer user_id '%s' != session user_id '%s'",
                        claimed_user_id, session.user_id
                    )
                    return False
        except Exception as e:
            logger.error("WS bearer validation failed: %s", e)
            return False

        # gateway protections (rate-limit + backpressure) — tracked on this endpoint
        try:
            client_ip = environ.get("REMOTE_ADDR", environ.get("HTTP_X_FORWARDED_FOR", "unknown"))
            user_agent = environ.get("HTTP_USER_AGENT", "")
            auth_header = f"Bearer {(auth or {}).get('bearer_token')}" if (auth or {}).get("bearer_token") else None

            context = RequestContext(client_ip=client_ip, user_agent=user_agent, authorization_header=auth_header)
            endpoint = "/socket.io/connect"

            await self.gateway_adapter.gateway.rate_limiter.check_and_record(session, context, endpoint)
            await self.gateway_adapter.gateway.backpressure_manager.check_capacity(
                session.user_type, session, context, endpoint
            )
            logger.info("WS connect gateway checks passed: sid=%s session=%s type=%s",
                        sid, session.session_id, session.user_type.value)

        except RateLimitError as e:
            logger.warning("WS connect rate-limited: %s", e.message)
            try:
                await self.sio.emit("chat_error", {
                    "error": f"Rate limit exceeded: {e.message}",
                    "retry_after": e.retry_after,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }, to=sid)
            finally:
                return False

        except BackpressureError as e:
            logger.warning("WS connect backpressure: %s", e.message)
            try:
                await self.sio.emit("chat_error", {
                    "error": f"System under pressure: {e.message}",
                    "retry_after": e.retry_after,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }, to=sid)
            finally:
                return False

        except CircuitBreakerError as e:
            logger.warning("WS connect circuit breaker '%s'", e.circuit_name)
            try:
                await self.sio.emit("chat_error", {
                    "error": f"Service temporarily unavailable: {e.message}",
                    "retry_after": e.retry_after,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }, to=sid)
            finally:
                return False

        except Exception as e:
            logger.error("WS connect gateway failure: %s", e)
            try:
                await self.sio.emit("chat_error", {
                    "error": "System check failed",
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }, to=sid)
            finally:
                return False

        # save socket session & join per-session room
        try:
            socket_meta = {
                "user_session": session.serialize_to_dict(),
                "authenticated": session.user_type.value != "anonymous",
                "project": (auth or {}).get("project"),
                "tenant": (auth or {}).get("tenant"),
            }
            await self.sio.save_session(sid, socket_meta)
            await self.sio.enter_room(sid, session.session_id)

            await self.sio.emit("session_info", {
                "session_id": session.session_id,
                "user_type": session.user_type.value,
                "user_id": session.user_id,
                "username": session.username,
                "project": socket_meta.get("project"),
                "tenant": socket_meta.get("tenant"),
                "connected_at": datetime.utcnow().isoformat() + "Z"
            }, to=sid)

            logger.info("WS connected: sid=%s -> room=%s", sid, session.session_id)
            return True

        except Exception as e:
            logger.error("WS connect finalization failed: %s", e)
            return False

    async def _handle_disconnect(self, sid):
        logger.info("Chat client disconnected: %s", sid)

    # ---------- CHAT MESSAGE with GATING (restored) ----------

    async def _save_attachment(self, raw: bytes, orig_name: str, mime: str) -> Dict[str, Any]:
        # sanitise name
        name = (orig_name or "file.pdf").strip()
        name = re.sub(r"[^A-Za-z0-9._ -]+", "", name) or "file.pdf"

        data = bytes(raw)  # handle memoryview/bytearray
        sha = hashlib.sha256(data).hexdigest()
        fname = f"{uuid.uuid4().hex}_{name}"
        fpath = self.upload_dir / fname

        # lazy import to avoid hard dep if not used
        aiofiles = __import__("aiofiles")
        async with await aiofiles.open(fpath, "wb") as f:
            await f.write(data)

        # If you serve static files elsewhere, make this a real URL
        return {
            "id": fname,
            "name": name,
            "mime": mime or "application/pdf",
            "size": len(data),
            "sha256": sha,
            "storage": "local",
            "path": str(fpath),     # workers on same host can read this
            "url": None,            # optionally set e.g. "/uploads/{fname}"
        }

    async def _handle_chat_message(self, sid, *args):
        if not args:
            logger.info("chat_message with no args")
            return {"ok": False, "error": "No data provided"}

        data = args[0]

        # Defer heavy work (attachment extraction) until AFTER gateway checks.
        # ------- collect only cheap info up-front -------
        message_data = data.get("message", {})
        # message_data["conversation_id"] = "565fbaea-c54c-4e0e-a73a-0ddb3aed158f"
        # message_data["conversation_id"] = "cbd6155a-8714-4d49-8a77-c7a802bfe848"
        # message_data["conversation_id"] = "927b6edd-e664-4b2d-87ef-a041995b6e18"
        # message_data["conversation_id"] = "f0fec000-8dd2-4d85-acda-b8781f210d04" # BROKEN
        # message_data["conversation_id"] = "ae42599e-2c1e-4334-be43-667a957d61e2"
        logger.info("chat_message sid=%s '%s'...", sid, (message_data or {}).get("message", "")[:100])

        try:
            socket_session = await self.sio.get_session(sid)
            user_session_data = (socket_session or {}).get("user_session", {})

            # rebuild lightweight UserSession
            session = UserSession(
                session_id=user_session_data.get("session_id", "unknown"),
                user_type=UserType(user_session_data.get("user_type", "anonymous")),
                fingerprint=user_session_data.get("fingerprint", "unknown"),
                user_id=user_session_data.get("user_id"),
                username=user_session_data.get("username"),
                roles=user_session_data.get("roles", []),
                permissions=user_session_data.get("permissions", []),
            )

            # gateway protections for /socket.io/chat (tracked)
            context = RequestContext(client_ip="socket.io", user_agent="socket.io-client", authorization_header=None)
            try:
                endpoint = "/socket.io/chat"
                await self.gateway_adapter.gateway.rate_limiter.check_and_record(session, context, endpoint)
                await self.gateway_adapter.gateway.backpressure_manager.check_capacity(
                    session.user_type, session, context, endpoint
                )
                logger.info("gateway ok sid=%s session=%s", sid, session.session_id)

            except RateLimitError as e:
                svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
                conv = ConversationCtx(session_id=session.session_id, conversation_id=data.get("conversation_id") or session.session_id, turn_id=f"turn_{uuid.uuid4().hex[:8]}")
                self._comm.emit_error(svc, conv, error=f"Rate limit exceeded: {e.message}", target_sid=sid, session_id=session.session_id)
                return

            except BackpressureError as e:
                svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
                conv = ConversationCtx(session_id=session.session_id, conversation_id=data.get("conversation_id") or session.session_id, turn_id=f"turn_{uuid.uuid4().hex[:8]}")
                self._comm.emit_error(svc, conv, error=f"System under pressure: {e.message}", target_sid=sid, session_id=session.session_id)
                return

            except CircuitBreakerError as e:
                svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
                conv = ConversationCtx(session_id=session.session_id, conversation_id=data.get("conversation_id") or session.session_id, turn_id=f"turn_{uuid.uuid4().hex[:8]}")
                self._comm.emit_error(svc, conv, error=f"Service temporarily unavailable: {e.message}", target_sid=sid, session_id=session.session_id)
                return

            except Exception as e:
                svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
                conv = ConversationCtx(session_id=session.session_id, conversation_id=message_data.get("conversation_id") or session.session_id, turn_id=f"turn_{uuid.uuid4().hex[:8]}")
                self._comm.emit_error(svc, conv, error="System check failed", target_sid=sid, session_id=session.session_id)
                logger.error("gateway check failed: %s", e)
                return

            enable_av = os.getenv("APP_AV_SCAN", "1") == "1"   # default ON
            av_timeout = float(os.getenv("APP_AV_TIMEOUT_S", "3.0"))

            cfg = PreflightConfig(av_scan=enable_av, av_timeout_s=av_timeout)
            # ------- ONLY NOW: parse attachments & extract text -------
            max_bytes = int(getattr(self, "max_upload_mb", 20)) * 1024 * 1024
            attachments_meta = data.get("attachment_meta", [])
            attachments = []
            for idx, f in enumerate(attachments_meta):
                mime = f.get("mime", "application/pdf")
                name = f.get("filename")
                raw = args[1 + idx] if len(args) > 1 + idx else None
                if raw and isinstance(raw, (bytes, bytearray, memoryview)):
                    raw_bytes = bytes(raw)
                    # quick size guard (cheap)
                    if len(raw_bytes) > max_bytes:
                        logger.warning("attachment '%s' rejected: %d > max %d", name, len(raw_bytes), max_bytes)
                        continue
                    pf = await preflight_async(raw_bytes, name, mime, cfg)
                    if not pf.allowed:
                        logger.warning(f"attachment '{name}' rejected. reasons={pf.reasons}, {pf.meta}.")
                        continue
                    else:
                        logger.info(f"attachment '{name}' accepted.")
                    attachments.append({"raw": raw_bytes, "name": name, "mime": mime})

            extractor = DocumentTextExtractor()
            attachments_text = []
            for a in attachments:
                try:
                    text, info = extractor.extract(a["raw"], a.get("name") or "file", a.get("mime"))
                    attachments_text.append({
                        "name": a.get("name"),
                        "mime": info.mime,
                        "ext": info.ext,
                        "size": len(a["raw"]),
                        "meta": info.meta,
                        "warnings": info.warnings,
                        "text": text,
                    })
                except Exception as ex:
                    logger.error("extract failed for '%s': %s", a.get("name"), ex)

            # ------- build message (same as before) -------
            message = (message_data or {}).get("text") or (message_data or {}).get("message") or ""

            if attachments_text:
                message += "\nATTACHMENTS:\n"
                for idx, a in enumerate(attachments_text):
                    message += f"{idx + 1}. Name: {a['name']}; Mime: {a['mime']}\n{a['text']}\n...\n"
            if not message:
                svc = ServiceCtx(request_id=str(uuid.uuid4()))
                conv = ConversationCtx(
                    session_id=user_session_data.get("session_id", "unknown"),
                    conversation_id=message_data.get("conversation_id") or user_session_data.get("session_id", "unknown"),
                    turn_id=f"turn_{uuid.uuid4().hex[:8]}",
                )
                self._comm.emit_error(svc, conv, error='Missing "message"', target_sid=sid, session_id=conv.session_id)
                return


            tenant_id = message_data.get("tenant_id") or get_tenant()
            project_id = message_data.get("project")

            # bundle resolution,accounting envelope, payload assembly, enqueue, and ack...
            request_id = str(uuid.uuid4())

            agentic_bundle_id = message_data.get("bundle_id")
            from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
            spec_resolved = resolve_bundle(agentic_bundle_id, override=None)
            agentic_bundle_id = spec_resolved.id if spec_resolved else None

            acct_env = build_envelope_from_session(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                request_id=request_id,
                component="chat.socket",
                app_bundle_id=agentic_bundle_id,
                metadata={"socket_id": sid, "entrypoint": "/socket.io/chat"},
            ).to_dict()

            # assemble task payload (protocol model)
            task_id = str(uuid.uuid4())
            turn_id = message_data.get("turn_id") or f"turn_{uuid.uuid4().hex[:8]}"
            conversation_id = message_data.get("conversation_id") or session.session_id

            ext_config = message_data.get("config") or {}
            if "tenant" not in ext_config:
                ext_config["tenant"] = tenant_id
            if "project" not in ext_config and project_id:
                ext_config["project"] = project_id

            svc = ServiceCtx(request_id=request_id, user=session.user_id, project=project_id, tenant=tenant_id)
            conv = ConversationCtx(session_id=session.session_id, conversation_id=conversation_id, turn_id=turn_id)

            if not spec_resolved:
                self._comm.emit_error(svc, conv, error=f"Unknown bundle_id '{agentic_bundle_id}'", target_sid=sid, session_id=session.session_id)
                return
            routing = ChatTaskRouting(
                session_id=session.session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                socket_id=sid,
                bundle_id=spec_resolved.id,
            )
            payload = ChatTaskPayload(
                meta=ChatTaskMeta(task_id=task_id, created_at=time.time(), instance_id=self.instance_id),
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
                ),
                request=ChatTaskRequest(
                    message=message,
                    chat_history=message_data.get("chat_history") or [],
                    operation=message_data.get("operation") or message_data.get("command"),
                    invocation=message_data.get("invocation"),
                    payload=message_data.get("payload") or {},       # ← generic Any pass-through
                ),
                config=ChatTaskConfig(values=ext_config),
                accounting=ChatTaskAccounting(envelope=acct_env),
            )

            set_res = await self.app.state.conversation_browser.set_conversation_state(
                tenant=payload.actor.tenant_id,
                project=payload.actor.project_id,
                user_id=payload.user.user_id,
                conversation_id=payload.routing.conversation_id,
                new_state="in_progress",
                by_instance=self.instance_id,
                request_id=request_id,
                last_turn_id=payload.routing.turn_id,
                require_not_in_progress=True,
                user_type=payload.user.user_type,
                bundle_id=payload.routing.bundle_id,
            )

            if not set_res["ok"]:
                # Did NOT acquire; someone else is active
                active_turn = set_res.get("current_turn_id")
                try:
                    self._comm.emit_conv_status(
                        svc, conv, routing,
                        state="in_progress",
                        updated_at=set_res["updated_at"],
                        current_turn_id=active_turn,
                        target_sid=sid  # DM this requester tab only
                    )
                    self._comm.emit_error(
                        svc, conv,
                        error="Conversation is busy (another tab/process is answering).",
                        target_sid=sid,
                        session_id=payload.routing.session_id,
                    )
                except Exception:
                    pass
                return

            # We DID acquire the lock → proceed and announce with our turn_id
            try:
                self._comm.emit_conv_status(
                    svc, conv, routing,
                    state="in_progress",
                    updated_at=set_res["updated_at"],
                    current_turn_id=payload.routing.turn_id,
                )
            except Exception:
                pass

            # atomic enqueue with backpressure accounting
            success, reason, stats = await self.chat_queue_manager.enqueue_chat_task_atomic(
                session.user_type,
                payload.model_dump(),   # ← serialize protocol model
                session,
                context,
                "/socket.io/chat",
            )
            if not success:
                # rollback state, since nothing will process this turn
                res_reset = await self.app.state.conversation_browser.set_conversation_state(
                    tenant=payload.actor.tenant_id,
                    project=payload.actor.project_id,
                    user_id=payload.user.user_id,
                    conversation_id=payload.routing.conversation_id,
                    new_state="idle",
                    by_instance=self.instance_id,
                    request_id=request_id,
                    last_turn_id=payload.routing.turn_id,
                    require_not_in_progress=False,
                    user_type=payload.user.user_type,
                    bundle_id=payload.routing.bundle_id,
                )
                # let the requester tab know
                self._comm.emit_conv_status(
                    svc, conv,
                    routing=routing,
                    state="idle",
                    updated_at=res_reset["updated_at"],
                    current_turn_id=res_reset.get("current_turn_id"),
                    target_sid=sid,  # DM the requester tab; or omit target_sid to session-broadcast
                )
                self._comm.emit_error(
                    svc, conv,
                    error=f"System under pressure - request rejected ({reason})",
                    target_sid=sid,
                    session_id=payload.routing.session_id,
                )
                return

            # ack to client via communicator (same envelope as processor emits)
            self._comm.emit_start(svc, conv, message=(message[:100] + "..." if len(message) > 100 else message), queue_stats=stats, target_sid=sid, session_id=session.session_id)

        except Exception as e:
            logger.exception("chat_message error: %s", e)
            try:
                svc = ServiceCtx(request_id=str(uuid.uuid4()))
                conv = ConversationCtx(session_id="unknown", conversation_id="unknown", turn_id=f"turn_{uuid.uuid4().hex[:8]}")
                self._comm.emit_error(svc, conv, error=str(e), target_sid=sid)
            except Exception:
                pass

    # ---------- subscr ----------
    async def _handle_conv_status_subscribe(self,
                                            data,
                                            sid):
        conv_id = (data or {}).get("conversation_id")
        socket_session = await self.sio.get_session(sid)
        user_session = (socket_session or {}).get("user_session", {})
        conv_id = conv_id or user_session.get("session_id")
        # lookup current row
        row = await self.app.state.conversation_browser.idx.get_conversation_state_row(
            user_id=user_session.get("user_id"),
            conversation_id=conv_id
        )
        # translate row → state + current_turn_id + updated_at
        state = "idle" if not row else ("in_progress" if "conv.state:in_progress" in row["tags"] else "error" if "conv.state:error" in row["tags"] else "idle")
        updated_at = (row["ts"].isoformat() + "Z") if row else datetime.utcnow().isoformat() + "Z"
        current_turn_id = row.get("payload", {}).get("last_turn_id") if row else None

        agentic_bundle_id = data.get("bundle_id")
        from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
        spec_resolved = resolve_bundle(agentic_bundle_id, override=None)

        # just EMIT `conv_status` back to the requester sid
        svc = ServiceCtx(request_id=str(uuid.uuid4()), user=user_session.get("user_id"))
        conv = ConversationCtx(session_id=user_session.get("session_id"),
                               conversation_id=conv_id,
                               turn_id=current_turn_id or f"turn_{uuid.uuid4().hex[:8]}")
        routing = ChatTaskRouting(
            session_id=user_session.get("session_id"),
            conversation_id=conv_id,
            turn_id=current_turn_id,
            socket_id=sid,
            bundle_id=spec_resolved.id,
        )
        self._comm.emit_conv_status(svc, conv,
                                    routing=routing,
                                    state=state,
                                    updated_at=updated_at,
                                    current_turn_id=current_turn_id,
                                    target_sid=sid)

    # ---------- ASGI app ----------

    def get_asgi_app(self):
        return socketio.ASGIApp(self.sio) if self.sio else None


def create_socketio_chat_handler(
    app,
    gateway_adapter,
    chat_queue_manager,
    allowed_origins,
    instance_id,
    redis_url,
    chat_comm: ChatRelayCommunicator,
):
    return SocketIOChatHandler(
        app=app,
        gateway_adapter=gateway_adapter,
        chat_queue_manager=chat_queue_manager,
        allowed_origins=allowed_origins,
        instance_id=instance_id,
        redis_url=redis_url,
        chat_comm=chat_comm,
    )

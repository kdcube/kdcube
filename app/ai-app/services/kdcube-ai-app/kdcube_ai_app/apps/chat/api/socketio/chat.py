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
from pathlib import Path
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib
import re

import socketio

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ServiceCtx, ConversationCtx,
)
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator

from kdcube_ai_app.apps.chat.api.ingress.chat_core import (
    IngressConfig,
    RawAttachment,
    run_gateway_checks,
    map_gateway_error,
    extract_attachments_text,
    merge_attachments_into_message,
    process_chat_message,
    get_conversation_status, build_ws_connect_request_context, upgrade_session_from_tokens,
    build_ws_chat_request_context,
)

logger = logging.getLogger(__name__)

async def _reject_anonymous(
        *,
        endpoint: str,
        session: UserSession,
        chat_comm,
        sid: Optional[str],
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
        target_sid=sid,
        session_id=session.session_id,
    )
    return False


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
    ):
        self.app = app
        self.gateway_adapter = gateway_adapter
        self.chat_queue_manager = chat_queue_manager
        self.allowed_origins = allowed_origins
        self.instance_id = instance_id
        self.redis_url = redis_url

        self._comm = ChatRelayCommunicator(
            redis_url=redis_url,
            channel="chat.events",
            orchestrator_identity=os.getenv("CB_RELAY_IDENTITY"),
        )
        self._listener_started = False

        self.max_upload_mb = int(os.environ.get("CHAT_MAX_UPLOAD_MB", "20"))

        self.sio = self._create_socketio_server()
        self._setup_event_handlers()

        self._session_refcounts: Dict[str, int] = {}
        self._sid_to_session_id: Dict[str, str] = {}
        self._sid_to_tenant_project: Dict[str, dict] = {}

        # TODO: remove!
        self.upload_dir = Path(os.environ.get("CHAT_UPLOAD_DIR", "/tmp/chat_uploads"))
        self.upload_dir.mkdir(parents=True, exist_ok=True)

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
        """
        Initialize WS relay. We no longer subscribe to a global channel here.
        Subscriptions are done lazily per-session in _ensure_session_subscription.
        """
        if self._listener_started or not self.sio:
            return

        # Nothing to subscribe yet; just mark initialized.
        self._listener_started = True
        logger.info("Socket.IO chat handler initialized (dynamic session subscriptions enabled).")

    async def stop(self):
        """
        WS shutdown hook. We don't stop the shared Redis listener here, because
        SSE may still be using it. Global stop is handled by SSEHub.stop() / app shutdown.
        """
        self._listener_started = False
        logger.info("Socket.IO chat handler stopped")

    # ---------- CONNECT with GATING (restored) ----------

    async def _handle_connect(self, sid, environ, auth):
        logger.info("WS connect attempt sid=%s", sid)

        # origin allowlist
        origin = environ.get("HTTP_ORIGIN")
        if self.allowed_origins not in (None, [], ["*"]):
            if not origin or (origin not in self.allowed_origins and "*" not in self.allowed_origins):
                logger.warning("WS connect rejected: origin '%s' not allowed", origin)
                return False

        auth = auth or {}
        user_session_id = auth.get("user_session_id")
        if not user_session_id:
            logger.warning("WS connect rejected: missing user_session_id")
            return False

        # 1) load base session
        try:
            session = await self.gateway_adapter.gateway.session_manager.get_session_by_id(user_session_id)
            if not session:
                logger.warning("WS connect rejected: unknown session_id=%s", user_session_id)
                return False
        except Exception as e:
            logger.error("WS connect failed to load session %s: %s", user_session_id, e)
            return False

        # 2) extract tokens from Socket.IO auth payload
        bearer_token = auth.get("bearer_token")
        id_token = auth.get("id_token")

        # 3) build connect RequestContext from transport
        ctx = build_ws_connect_request_context(environ, auth)
        # 4) upgrade session if tokens present (mirrors SSE)
        try:
            session = await upgrade_session_from_tokens(
                session=session,
                ctx=ctx,
                bearer_token=bearer_token,
                id_token=id_token,
                gateway_adapter=self.gateway_adapter,
                chat_comm=self._comm,
                stream_id=sid,
            )
        except AuthenticationError:
            # upgrade_session_from_tokens already emitted an error
            return False
        except Exception as e:
            logger.error("WS session upgrade failed: %s", e)
            return False

        # 5) optional policy gate for anonymous
        if os.environ.get("CHAT_WS_REJECT_ANONYMOUS", "0") == "1":
            ok = await _reject_anonymous(endpoint="/socket.io/connect", session=session, sid=sid, chat_comm=self._comm)
            if ok is False:
                return False

        # # 6) gateway protections
        # gw_res = await run_gateway_checks(
        #     gateway_adapter=self.gateway_adapter,
        #     session=session,
        #     context=ctx,
        #     endpoint="/socket.io/connect",
        # )
        # if gw_res.kind != "ok":
        #     mapped = map_gateway_error(gw_res)
        #     try:
        #         await self.sio.emit("chat_error", {
        #             "error": mapped["message"],
        #             "retry_after": mapped.get("retry_after"),
        #             "timestamp": datetime.utcnow().isoformat() + "Z",
        #         }, to=sid)
        #     finally:
        #         return False

        # 7) save socket session metadata (store ctx too!)
        try:
            tenant = auth.get("tenant")
            project = auth.get("project")
            socket_meta = {
                "user_session": session.serialize_to_dict(),
                "request_context": ctx.model_dump() if hasattr(ctx, "model_dump") else ctx.__dict__,  # safe bridge
                "authenticated": session.user_type.value != "anonymous",
                "project": project,
                "tenant": tenant,
            }

            await self.sio.save_session(sid, socket_meta)

            # join room for *current* session (could be upgraded)
            await self.sio.enter_room(sid, session.session_id)

            # track sid → session_id and ensure Redis subscription
            self._sid_to_session_id[sid] = session.session_id
            self._sid_to_tenant_project[sid] = {"tenant": tenant, "project": project}
            await self._comm.acquire_session_channel(
                session.session_id,
                callback=self._on_pubsub_message,
                tenant=tenant,
                project=project,
            )

            await self.sio.emit("session_info", {
                "session_id": session.session_id,
                "user_type": session.user_type.value,
                "user_id": session.user_id,
                "username": session.username,
                "project": project,
                "tenant": tenant,
                "connected_at": datetime.utcnow().isoformat() + "Z"
            }, to=sid)

            logger.info("WS connected: sid=%s -> room=%s", sid, session.session_id)
            return True

        except Exception as e:
            logger.error("WS connect finalization failed: %s", e)
            return False

    async def _handle_disconnect(self, sid):
        logger.info("Chat client disconnected: %s", sid)
        session_id = self._sid_to_session_id.pop(sid, None)
        if session_id:
            tenant_project = self._sid_to_tenant_project.get(sid) or {}
            await self._comm.release_session_channel(session_id,
                                                     tenant=tenant_project["tenant"],
                                                     project=tenant_project["project"])


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
        message_data = data.get("message", {}) or {}
        logger.info(
            "chat_message sid=%s '%s'...",
            sid,
            (message_data or {}).get("message", "")[:100],
        )

        try:
            socket_session = await self.sio.get_session(sid)
            ctx_data = (socket_session or {}).get("request_context")

            user_session_data = (socket_session or {}).get("user_session", {})

            # Rebuild lightweight UserSession
            session = UserSession(
                session_id=user_session_data.get("session_id", "unknown"),
                user_type=UserType(user_session_data.get("user_type", "anonymous")),
                fingerprint=user_session_data.get("fingerprint", "unknown"),
                user_id=user_session_data.get("user_id"),
                username=user_session_data.get("username"),
                roles=user_session_data.get("roles", []),
                permissions=user_session_data.get("permissions", []),
                timezone=user_session_data.get("timezone", "unknown"),
            )

            # rebuild ctx if we stored it on connect; else fallback
            if ctx_data:
                context = RequestContext(**ctx_data)
            else:
                context = build_ws_chat_request_context()

            # optional anonymous gate for message-level parity
            if os.environ.get("CHAT_WS_REJECT_ANONYMOUS", "0") == "1":
                ok = await _reject_anonymous(endpoint="/socket.io/chat", session=session, sid=sid, chat_comm=self._comm)
                if ok is False:
                    return
            gw_res = await run_gateway_checks(
                gateway_adapter=self.gateway_adapter,
                session=session,
                context=context,
                endpoint="/socket.io/chat",
            )
            if gw_res.kind != "ok":
                mapped = map_gateway_error(gw_res)
                svc = ServiceCtx(request_id=str(uuid.uuid4()), user=session.user_id)
                conv = ConversationCtx(
                    session_id=session.session_id,
                    conversation_id=message_data.get("conversation_id") or session.session_id,
                    turn_id=f"turn_{uuid.uuid4().hex[:8]}",
                )
                await self._comm.emit_error(
                    svc,
                    conv,
                    error=mapped["message"],
                    target_sid=sid,
                    session_id=session.session_id,
                )
                # No explicit WS ack – just error event
                return

            # ---------- attachments (Socket.IO → RawAttachment) ----------
            max_mb = getattr(self, "max_upload_mb", 20)
            attachments_meta = data.get("attachment_meta") or []
            raw_attachments: List[RawAttachment] = []

            for idx, f in enumerate(attachments_meta):
                mime = f.get("mime", "application/octet-stream")
                name = f.get("filename") or f.get("name") or "file"
                raw = args[1 + idx] if len(args) > 1 + idx else None
                if raw and isinstance(raw, (bytes, bytearray, memoryview)):
                    raw_bytes = bytes(raw)
                    raw_attachments.append(
                        RawAttachment(
                            content=raw_bytes,
                            name=name,
                            mime=mime,
                            meta={},  # you can pass f itself here if you want to merge custom meta
                        )
                    )

            attachments_text: List[Dict[str, Any]] = []
            if raw_attachments:
                attachments_text = await extract_attachments_text(
                    raw_attachments,
                    max_mb=max_mb,
                )

            # ---------- build final message text ----------
            base_message = (
                    (message_data or {}).get("text")
                    or (message_data or {}).get("message")
                    or ""
            )
            text = merge_attachments_into_message(base_message, attachments_text)

            # ---------- delegate to core business logic ----------
            ingress_cfg = IngressConfig(
                transport="socket",
                entrypoint="/socket.io/chat",
                component="chat.socket",
                instance_id=self.instance_id,
                stream_id=sid,
                metadata={"socket_id": sid, "entrypoint": "/socket.io/chat"},
            )

            result = await process_chat_message(
                app=self.app,
                chat_queue_manager=self.chat_queue_manager,
                chat_comm=self._comm,
                session=session,
                request_context=context,
                message_data=message_data,
                message_text=text,
                ingress=ingress_cfg,
            )

            if not result.ok:
                # process_chat_message already emitted proper conv_status + error
                # For WS we don't need to raise; just return.
                logger.warning(
                    "chat_message rejected sid=%s error_type=%s error=%s",
                    sid,
                    result.error_type,
                    result.error,
                )
                return

            # On success, everything (conv_status + start) is already emitted.
            # You *could* emit a lightweight ack event here if desired.
            return

        except Exception as e:
            logger.exception("chat_message error: %s", e)
            try:
                svc = ServiceCtx(request_id=str(uuid.uuid4()))
                conv = ConversationCtx(
                    session_id="unknown",
                    conversation_id="unknown",
                    turn_id=f"turn_{uuid.uuid4().hex[:8]}",
                )
                await self._comm.emit_error(
                    svc,
                    conv,
                    error=str(e),
                    target_sid=sid,
                )
            except Exception:
                pass

    # ---------- subscr ----------
    async def _handle_conv_status_subscribe(self, data, sid):
        socket_session = await self.sio.get_session(sid)
        user_session = (socket_session or {}).get("user_session", {})

        session = UserSession(
            session_id=user_session.get("session_id", "unknown"),
            user_type=UserType(user_session.get("user_type", "anonymous")),
            fingerprint=user_session.get("fingerprint", "unknown"),
            user_id=user_session.get("user_id"),
            username=user_session.get("username"),
            roles=user_session.get("roles", []),
            permissions=user_session.get("permissions", []),
            timezone=user_session.get("timezone", "Europe/Berlin"),
        )

        conv_id = (data or {}).get("conversation_id") or session.session_id
        bundle_id = (data or {}).get("bundle_id")

        tenant_project = self._sid_to_tenant_project.get(sid) or {}
        status = await get_conversation_status(
            app=self.app,
            chat_comm=self._comm,
            session=session,
            bundle_id=bundle_id,
            conversation_id=conv_id,
            stream_id=sid,
            tenant=tenant_project.get("tenant"),
            project=tenant_project.get("project"),
        )
        # We don't send a separate WS ACK; conv_status event itself is enough.
        return status


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
    )

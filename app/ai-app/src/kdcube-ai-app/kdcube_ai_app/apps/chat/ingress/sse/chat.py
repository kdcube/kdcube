# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/ingress/sse/chat.py

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from kdcube_ai_app.apps.chat.ingress.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.util import _iso
from kdcube_ai_app.auth.AuthManager import AuthenticationError, RequireUser
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator, PROJECT_BROADCAST_ROOM
from kdcube_ai_app.apps.chat.ids import new_turn_id
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ServiceCtx, ConversationCtx,
)
from kdcube_ai_app.apps.chat.ingress.ingress_core import (
    IngressConfig,
    RawAttachment,
    run_gateway_checks,
    map_gateway_error,
    process_chat_message,
    resolve_ingress_conversation_id,
    get_conversation_status, build_sse_request_context, upgrade_session_from_tokens,
)
from kdcube_ai_app.apps.chat.ingress.socketio.data_bus.publish import DataBusSocketIOIngress
from kdcube_ai_app.apps.chat.sdk.protocol import external_event_attachment_payloads, external_events_text

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
        turn_id=new_turn_id(),
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
    project_events: bool = False

class SSECapacityError(Exception):
    """Raised when SSE instance capacity is exhausted."""
    pass

class SSEHub:
    """
    One Redis relay subscription per process -> fan out to all connected SSE clients.
    """
    def __init__(
        self,
        chat_comm: ChatRelayCommunicator,
        *,
        redis=None,
        instance_id: Optional[str] = None,
        process_id: Optional[int] = None,
        stats_ttl_sec: Optional[int] = None,
    ):
        self.chat_comm = chat_comm
        self.redis = redis
        self.instance_id = instance_id or get_settings().INSTANCE_ID
        self.process_id = process_id or os.getpid()
        self._stats_ttl_sec = stats_ttl_sec
        self._by_session: Dict[str, List[Client]] = {}
        self._counts_by_tp: Dict[Tuple[str, str], int] = {}
        self._sessions_by_tp: Dict[Tuple[str, str], int] = {}
        self._session_tp: Dict[str, Tuple[str, str]] = {}
        self._lock = asyncio.Lock()
        self._relay_started = False

    def _total_connections_locked(self) -> int:
        if self._counts_by_tp:
            return sum(self._counts_by_tp.values())
        return sum(len(v) for v in self._by_session.values())

    def _tp_key(self, client: Client) -> Tuple[str, str]:
        tenant = client.tenant or "unknown"
        project = client.project or "unknown"
        return tenant, project

    def _redis_stats_key(self, tenant: str, project: str) -> str:
        base = f"{REDIS.CHAT.SSE_CONNECTIONS_PREFIX}:{self.instance_id}:{self.process_id}"
        return ns_key(base, tenant=tenant, project=project)

    def _resolve_stats_ttl(self) -> int:
        if self._stats_ttl_sec:
            return int(self._stats_ttl_sec)
        try:
            from kdcube_ai_app.infra.gateway.config import get_gateway_config
            cfg = get_gateway_config()
            ttl = getattr(getattr(cfg, "redis", None), "sse_stats_ttl_seconds", None)
            if ttl is not None:
                return int(ttl)
        except Exception:
            pass
        # fallback to gateway defaults if config is unavailable
        return 60

    async def _publish_stats(self, tenant: str, project: str, count: int, sessions: int) -> None:
        if not self.redis:
            return
        payload = {
            "count": int(count),
            "sessions": int(sessions),
            "max": self._resolve_max_connections(),
            "ts": time.time(),
            "instance_id": self.instance_id,
            "process_id": self.process_id,
        }
        try:
            await self.redis.setex(
                self._redis_stats_key(tenant, project),
                self._resolve_stats_ttl(),
                json.dumps(payload),
            )
        except Exception as e:
            logger.warning("[SSEHub] Failed to publish SSE stats: %s", e)

    def _resolve_max_connections(self) -> int:
        # Prefer gateway config limits (component-aware), fallback to env.
        try:
            from kdcube_ai_app.infra.gateway.config import get_gateway_config
            cfg = get_gateway_config()
            limits_cfg = getattr(cfg, "limits", None)
            if limits_cfg and limits_cfg.max_sse_connections_per_instance is not None:
                return int(limits_cfg.max_sse_connections_per_instance)
        except Exception:
            pass
        return 0

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


        tp_payloads: List[Tuple[str, str, int, int]] = []
        # Unblock all generators by pushing a sentinel into their queues
        async with self._lock:
            for tp_key, count in self._counts_by_tp.items():
                sessions = self._sessions_by_tp.get(tp_key, 0)
                tp_payloads.append((tp_key[0], tp_key[1], count, sessions))
            for clients in self._by_session.values():
                for c in clients:
                    try:
                        c.queue.put_nowait(": shutdown\n\n")
                    except Exception:
                        pass
            self._counts_by_tp.clear()
            self._sessions_by_tp.clear()
            self._session_tp.clear()

        await self.chat_comm.unsubscribe()
        self._relay_started = False
        logger.info("[SSEHub] unsubscribed from relay channel(s)")
        for tenant, project, _count, _sessions in tp_payloads:
            await self._publish_stats(tenant, project, 0, 0)

    async def register(self, client: Client):
        tp_key = self._tp_key(client)
        async with self._lock:
            max_conn = self._resolve_max_connections()
            if max_conn > 0:
                total_now = self._total_connections_locked()
                if total_now >= max_conn:
                    raise SSECapacityError(
                        f"SSE instance capacity exceeded: {total_now}/{max_conn}"
                    )
            lst = self._by_session.setdefault(client.session_id, [])
            lst.append(client)
            self._counts_by_tp[tp_key] = self._counts_by_tp.get(tp_key, 0) + 1
            if client.session_id not in self._session_tp:
                self._session_tp[client.session_id] = tp_key
                self._sessions_by_tp[tp_key] = self._sessions_by_tp.get(tp_key, 0) + 1
            elif self._session_tp.get(client.session_id) != tp_key:
                logger.warning(
                    "[SSEHub] session mapped to different tenant/project: session=%s current=%s new=%s",
                    client.session_id,
                    self._session_tp.get(client.session_id),
                    tp_key,
                )

        # Acquire per-session channel via central refcounting
        await self.chat_comm.acquire_session_channel(
            client.session_id,
            callback=self._on_relay,
            tenant=client.tenant,
            project=client.project,
        )
        if client.project_events:
            await self.chat_comm.acquire_project_channel(
                tenant=client.tenant,
                project=client.project,
                callback=self._on_relay,
            )
        await self._publish_stats(
            tp_key[0],
            tp_key[1],
            self._counts_by_tp.get(tp_key, 0),
            self._sessions_by_tp.get(tp_key, 0),
        )
        logger.info(
            "[SSEHub] register session=%s stream_id=%s tenant=%s project=%s total_now=%s hub_id=%s relay_id=%s",
            client.session_id, client.stream_id, client.tenant, client.project,
            len(self._by_session.get(client.session_id, [])),
            id(self), id(self.chat_comm)
        )

    async def get_stats(self) -> Dict[str, int]:
        async with self._lock:
            total = self._total_connections_locked()
            sessions = len(self._by_session)
        return {
            "total_connections": total,
            "sessions": sessions,
            "max_connections": self._resolve_max_connections(),
        }

    async def unregister(self, client: Client):
        tp_key = self._tp_key(client)
        async with self._lock:
            lst = self._by_session.get(client.session_id, [])
            self._by_session[client.session_id] = [c for c in lst if c is not client]
            if not self._by_session[client.session_id]:
                self._by_session.pop(client.session_id, None)
                prev_tp = self._session_tp.pop(client.session_id, None)
                if prev_tp:
                    self._sessions_by_tp[prev_tp] = max(0, self._sessions_by_tp.get(prev_tp, 0) - 1)
                    if self._sessions_by_tp.get(prev_tp, 0) == 0:
                        self._sessions_by_tp.pop(prev_tp, None)

            if tp_key in self._counts_by_tp:
                self._counts_by_tp[tp_key] = max(0, self._counts_by_tp.get(tp_key, 0) - 1)
                if self._counts_by_tp.get(tp_key, 0) == 0:
                    self._counts_by_tp.pop(tp_key, None)

        # IMPORTANT: release per client (not only last)
        await self.chat_comm.release_session_channel(client.session_id,
                                                     tenant=client.tenant,
                                                     project=client.project,
                                                     )
        if client.project_events:
            await self.chat_comm.release_project_channel(
                tenant=client.tenant,
                project=client.project,
            )
        await self._publish_stats(
            tp_key[0],
            tp_key[1],
            self._counts_by_tp.get(tp_key, 0),
            self._sessions_by_tp.get(tp_key, 0),
        )

        logger.info(
            "[SSEHub] unregister session=%s stream_id=%s tenant=%s project=%s total_now=%s hub_id=%s relay_id=%s",
            client.session_id, client.stream_id, client.tenant, client.project,
            len(self._by_session.get(client.session_id, [])),
            id(self), id(self.chat_comm)
        )

    async def resync_relay(self, *, reason: str = "manual"):
        """
        Reconcile relay subscriptions/refcounts with current SSE hub state.
        Useful after Redis reconnect or suspected drift.
        """
        async with self._lock:
            snapshot: Dict[str, tuple[str | None, str | None, int]] = {}
            project_snapshot: Dict[Tuple[str, str], int] = {}
            for session_id, clients in self._by_session.items():
                if not clients:
                    continue
                tenant = clients[0].tenant
                project = clients[0].project
                snapshot[session_id] = (tenant, project, len(clients))
                for client in clients:
                    if client.project_events:
                        key = self._tp_key(client)
                        project_snapshot[key] = project_snapshot.get(key, 0) + 1

        await self.chat_comm.reconcile_sessions(snapshot, reason=reason)
        reconcile_project = getattr(self.chat_comm, "reconcile_project_channels", None)
        if reconcile_project:
            await reconcile_project(project_snapshot, reason=reason)
        logger.info(
            "[SSEHub] resync relay reason=%s sessions=%s project_channels=%s hub_id=%s relay_id=%s",
            reason, len(snapshot), len(project_snapshot), id(self), id(self.chat_comm),
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

            if room == PROJECT_BROADCAST_ROOM:
                service = (data or {}).get("service") or {}
                tenant = service.get("tenant")
                project = service.get("project")
                if not tenant or not project:
                    logger.warning("[SSEHub._on_relay] project event missing tenant/project: %s", message)
                    return
                async with self._lock:
                    recipients = [
                        client
                        for clients in self._by_session.values()
                        for client in clients
                        if client.project_events and client.tenant == tenant and client.project == project
                    ]
                if not recipients:
                    return
                frame = _sse_frame(event, data, event_id=str(uuid.uuid4()))
                for client in recipients:
                    self._enqueue(client, frame)
                return

            logger.debug(
                "[SSEHub._on_relay] RECEIVED event=%s session=%s target_sid=%s "
                "known_sessions=%s hub_id=%s",
                event, room, target_sid,
                list(self._by_session.keys()),
                id(self),
            )

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
                # DM: only client with matching stream_id.
                # Client reuses stream_id across reconnects, so this should
                # always match as long as the session is the same.
                delivered = False
                for c in recipients:
                    if c.stream_id and c.stream_id == target_sid:
                        logger.debug("[SSEHub._on_relay] DIRECT SEND the message for session session=%s stream_id=%s to recipient session=%s stream_id=%s", room, target_sid, c.session_id, c.stream_id)
                        self._enqueue(c, frame)
                        delivered = True
                if not delivered:
                    log_method = logger.debug if (
                        event == "chat_service"
                        and str(data.get("type") or "").startswith("kdcube.data_bus.")
                    ) else logger.warning
                    log_method(
                        "[SSEHub._on_relay] target stream_id=%s not found among %d session client(s) for session=%s; "
                        "message dropped (connected stream_ids: %s)",
                        target_sid, len(recipients), room,
                        [c.stream_id for c in recipients],
                    )
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
        orchestrator_identity=get_settings().PLATFORM.SERVICE.CB_RELAY_IDENTITY,
    )

    # Ensure hub exists on app
    if not hasattr(app.state, "sse_hub"):
        app.state.sse_hub = SSEHub(
            chat_comm,
            redis=getattr(app.state, "redis_async", None),
            instance_id=instance_id,
        )

    settings = get_settings()

    def _session_dict(session_obj: UserSession) -> dict[str, Any]:
        serializer = getattr(session_obj, "serialize_to_dict", None)
        if callable(serializer):
            try:
                value = serializer()
                if isinstance(value, dict):
                    return value
            except Exception:
                pass
        return {
            "session_id": session_obj.session_id,
            "user_type": getattr(session_obj.user_type, "value", str(session_obj.user_type)),
            "fingerprint": session_obj.fingerprint,
            "user_id": session_obj.user_id,
            "username": session_obj.username,
            "email": session_obj.email,
            "roles": list(session_obj.roles or []),
            "permissions": list(session_obj.permissions or []),
            "timezone": session_obj.timezone,
        }

    def _resolve_sse_client(session_id: str, stream_id: str | None) -> Client | None:
        hub = router.state.sse_hub
        clients = list(hub._by_session.get(session_id) or [])
        if stream_id:
            for client in clients:
                if client.stream_id == stream_id:
                    return client
        return next(iter(clients), None)

    # ---------- STREAM ----------
    @router.get("/stream")
    async def sse_stream(
        request: Request,
        stream_id: str,
        session: UserSession = Depends(require_auth(RequireUser())),
        # direct-message id for this connection
        # Query auth (parity with Socket.IO connect)
        user_session_id: Optional[str] = None,
        bearer_token: Optional[str] = None,
        id_token: Optional[str] = None,
        project: Optional[str] = None,
        tenant: Optional[str] = None,
        project_events: bool = False,
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
        logger.info(f"[sse_stream]. user_session_id={user_session_id}; session.session_id={session.session_id}; stream_id={stream_id}; user_id={session.user_id}; user_type={session.user_type}")

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
        logger.info(f"[sse_stream]. After upgrade: user_session_id={user_session_id}; session.session_id={session.session_id}; stream_id={stream_id}; user_id={session.user_id}; user_type={session.user_type}")
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
        #         turn_id=new_turn_id(),
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
                        project=project,
                        project_events=bool(project_events),)

        # Register client
        try:
            await app.state.sse_hub.register(client)
        except SSECapacityError as e:
            logger.warning("[sse_stream] SSE capacity reached: %s", e)
            raise HTTPException(status_code=429, detail="SSE instance capacity reached. Try again later.")

        # Diagnostic: verify relay wiring after register
        relay_comm = app.state.sse_hub.chat_comm
        comm_obj = getattr(relay_comm, "_comm", None)
        logger.info(
            "[sse_stream] relay diagnostic session=%s stream_id=%s "
            "relay_id=%s comm_id=%s listener_started=%s "
            "listener_alive=%s subscribed_channels=%s refcounts=%s",
            session.session_id, stream_id,
            id(relay_comm), id(comm_obj) if comm_obj else None,
            getattr(relay_comm, "_listener_started", "?"),
            comm_obj.listener_alive() if comm_obj and hasattr(comm_obj, "listener_alive") else "?",
            getattr(comm_obj, "_subscribed_channels", []) if comm_obj else [],
            dict(getattr(relay_comm, "_session_refcounts", {})),
        )

        async def gen():
            shutdown_notified = False

            # Initial ready
            hello = {
                "timestamp": _iso(),
                "session_id": session.session_id,
                "user_type": session.user_type.value,
                **({"stream_id": stream_id} if stream_id else {}),
                **({"tenant": tenant} if tenant else {}),
                **({"project": project} if project else {}),
                "project_events": bool(project_events),
            }
            yield _sse_frame("ready", hello, event_id=str(uuid.uuid4()))

            try:
                while True:
                    # 1) if server is shutting down, exit
                    if getattr(app.state, "shutting_down", False):
                        if not shutdown_notified:
                            shutdown_notified = True
                            yield _sse_frame(
                                "server_shutdown",
                                {
                                    "timestamp": _iso(),
                                    "reason": "draining",
                                    "session_id": session.session_id,
                                    **({"stream_id": stream_id} if stream_id else {}),
                                },
                                event_id=str(uuid.uuid4()),
                            )
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
                            if not shutdown_notified:
                                shutdown_notified = True
                                yield _sse_frame(
                                    "server_shutdown",
                                    {
                                        "timestamp": _iso(),
                                        "reason": "draining",
                                        "session_id": session.session_id,
                                        **({"stream_id": stream_id} if stream_id else {}),
                                    },
                                    event_id=str(uuid.uuid4()),
                                )
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
            session: UserSession = Depends(require_auth(RequireUser())),
            # multipart support (attachments)
            event_submission: Optional[str] = Form(None),
            files: List[UploadFile] = File(default=[]),
    ):
        logger.info(f"[sse_chat] received request session={session.session_id} stream_id={stream_id}")

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
                turn_id=new_turn_id(),
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

        # ---------- parse event submission (JSON or multipart) ----------
        if event_submission is not None:
            try:
                message_data = json.loads(event_submission)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid 'event_submission' JSON in multipart form")
        else:
            try:
                message_data = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(message_data, dict):
            raise HTTPException(status_code=400, detail="Invalid event submission")

        base_text = external_events_text(message_data.get("external_events") or [])

        turn_id = new_turn_id()
        message_data["turn_id"] = turn_id
        conversation_id, conversation_created = await resolve_ingress_conversation_id(
            app=app,
            session=session,
            message_data=message_data,
        )
        logger.info(
            "[sse_chat] parsed request session=%s stream_id=%s conversation_id=%s turn_id=%s text_len=%s",
            session.session_id,
            stream_id,
            conversation_id,
            turn_id,
            len(base_text),
        )

        # ---------- attachments (transport → RawAttachment) ----------
        raw_attachments: List[RawAttachment] = []

        attachment_events = external_event_attachment_payloads(message_data.get("external_events") or [])
        if files:
            for idx, f in enumerate(files):
                try:
                    raw = await f.read()
                except Exception:
                    continue
                if not raw:
                    continue

                meta_in = attachment_events[idx] if idx < len(attachment_events) else {}
                cleaned_meta = {
                    k: v for k, v in meta_in.items() if k not in ("filename", "name", "file_index")
                }

                raw_attachments.append(
                    RawAttachment(
                        content=raw,
                        name=f.filename or "file",
                        mime=f.content_type or "application/octet-stream",
                        meta=cleaned_meta,
                    )
                )

        payload = message_data.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        message_data["payload"] = payload

        # Final message text (attachments handled downstream)
        text = base_text

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
            raw_attachments=raw_attachments,
            ingress=ingress_cfg,
        )

        if not result.ok:
            # process_chat_message already emitted error via chat_comm
            error_type = result.error_type or "bad_request"
            status = result.http_status or 400

            # keep retry_after semantics for pressure errors
            detail: Any
            if error_type in ("queue.enqueue_rejected",):
                detail = {
                    "error": "System under pressure",
                    "error_type": result.error_type,
                    "reason": result.reason,
                    "retry_after": result.retry_after,
                    "queue_stats": result.queue_stats,
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
            "status": result.reason or "processing_started",
            "task_id": result.task_id,
            "session_id": result.session_id,
            "conversation_id": result.conversation_id,
            "turn_id": result.turn_id,
            "active_turn_id": result.active_turn_id,
            "target_turn_id": result.target_turn_id,
            "queued_turn_id": result.queued_turn_id,
            "event_id": result.event_id,
            "external_event_sequence": result.external_event_sequence,
            "live_owner_detected": result.live_owner_detected,
            "conversation_created": conversation_created,
            "user_type": result.user_type,
            "is_continuation": bool(result.is_continuation),
            "message": (
                "Continuation accepted; available to the active conversation owner"
                if result.is_continuation
                else "External event recorded"
                if result.reason == "external_event_recorded"
                else "Queued; streaming via SSE"
            ),
        }

    # ---------- conv_status.get (parity, now via core) ----------
    @router.post("/data_bus.publish")
    async def sse_data_bus_publish(
            request: Request,
            data: Dict[str, Any] = Body(...),
            stream_id: Optional[str] = None,
            session: UserSession = Depends(require_auth(RequireUser())),
    ):
        logger.info(
            "[sse_data_bus.publish] received request session=%s stream_id=%s",
            session.session_id,
            stream_id,
        )
        ctx = build_sse_request_context(request, session=session)
        if os.environ.get("CHAT_SSE_REJECT_ANONYMOUS", "1") == "1":
            await _reject_anonymous(
                endpoint="/sse/data_bus.publish",
                session=session,
                stream_id=stream_id,
                chat_comm=chat_comm,
            )
        gw_res = await run_gateway_checks(
            gateway_adapter=gateway_adapter,
            session=session,
            context=ctx,
            endpoint="/sse/data_bus.publish",
        )
        if gw_res.kind != "ok":
            mapped = map_gateway_error(gw_res)
            raise HTTPException(status_code=mapped["status"], detail={"error": mapped["message"]})

        client = _resolve_sse_client(session.session_id, stream_id)
        tenant = client.tenant if client else settings.TENANT
        project = client.project if client else settings.PROJECT
        socket_meta = {
            "user_session": _session_dict(session),
            "tenant": tenant,
            "project": project,
        }
        ingress = DataBusSocketIOIngress(app=app)
        ack = await ingress.handle_publish(
            sid=str(stream_id or ""),
            socket_session=socket_meta,
            data=data,
            reply_transport="sse",
        )
        logger.info(
            "[sse_data_bus.publish] completed session=%s stream_id=%s status=%s accepted=%s rejected=%s",
            session.session_id,
            stream_id,
            ack.get("status"),
            len(ack.get("accepted") or []),
            len(ack.get("rejected") or []),
        )
        return ack

    @router.post("/conv_status.get")
    async def sse_conv_status_get(
            data: Dict[str, Any],
            session: UserSession = Depends(require_auth(RequireUser())),
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

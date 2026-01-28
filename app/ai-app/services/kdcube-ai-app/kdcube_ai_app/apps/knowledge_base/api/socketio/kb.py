# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/knowledge_base/api/socketio/kb.py
"""
Modular Socket.IO handler for KB progress events
- Creates a Socket.IO server with Redis manager
- Listens to orchestrator pubsub channel and relays events to clients
- Clean start/stop lifecycle
"""
import os
import asyncio
import logging

import socketio

from typing import Optional

from kdcube_ai_app.apps.knowledge_base.api.resolvers import require_kb_read
from kdcube_ai_app.apps.middleware.accounting import MiddlewareAuthWithAccounting
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.auth.AuthManager import RequireRoles, RequirePermissions, AuthenticationError
from kdcube_ai_app.apps.middleware.token_extract import resolve_socket_auth_tokens

logger = logging.getLogger("KB.SocketIO")

class SocketIOKBHandler:
    def __init__(
            self,
            allowed_origins,
            redis_url: str,
            orchestrator_identity: str,
            instance_id: Optional[str] = None,
            *,
            auth_with_acct: MiddlewareAuthWithAccounting,  # << single dependency
            component_name: str = "kb-socket",
    ):
        self.allowed_origins = allowed_origins
        self.redis_url = redis_url
        self.orchestrator_identity = orchestrator_identity
        self.instance_id = instance_id
        self.auth_with_acct = auth_with_acct
        self.component_name = component_name

        self.sio = self._create_socketio_server()
        self._setup_event_handlers()

        self._redis = None
        self._listener_task: asyncio.Task | None = None

        from kdcube_ai_app.infra.orchestration.app.communicator import ServiceCommunicator
        self.comm = ServiceCommunicator(redis_url, orchestrator_identity)
        self.relay_channel = "kb.process_resource_out"

    def _create_socketio_server(self):
        mgr = socketio.AsyncRedisManager(self.redis_url)
        return socketio.AsyncServer(
            cors_allowed_origins=self.allowed_origins,
            async_mode="asgi",
            client_manager=mgr,
            logger=False,
            engineio_logger=False,
        )

    def _setup_event_handlers(self):
        if not self.sio:
            return

        @self.sio.on("connect")
        async def handle_connect(sid, environ, auth):
            return await self._handle_connect(sid, environ, auth)

        @self.sio.on("disconnect")
        async def handle_disconnect(sid):
            logger.info(f"KB socket disconnected: {sid}")

        # === KB operations ===

        @self.sio.on("kb_search")
        async def kb_search(sid, payload):
            try:
                # Resolve context with per-message override
                session, project, tenant = await self._resolve_event_context(sid, payload)

                # Enforce KB read on the *effective* user
                self.auth_with_acct.authorize_session_user(
                    session,
                    require_kb_read(),           # <- RequireKBRead() under the hood
                    require_all=True
                )
                # Bind accounting to this event
                self.auth_with_acct.apply_event_accounting(
                    session=session,
                    component=f"{self.component_name}.event",
                    tenant_id=tenant,
                    project_id=project,
                    extra={"socket_event": "kb_search"}
                )

                # --- do the search (or dispatch) ---
                query = (payload or {}).get("query") or ""
                top_k = int((payload or {}).get("top_k") or 5)
                resource_id = (payload or {}).get("resource_id")
                request_id = (payload or {}).get("request_id")

                from kdcube_ai_app.apps.knowledge_base.api.resolvers import get_kb_for_project, DEFAULT_PROJECT
                kb = get_kb_for_project(project or DEFAULT_PROJECT)
                results = kb.hybrid_search(query=query, resource_id=resource_id, top_k=top_k)

                await self.sio.emit(
                    "kb_search_result",
                    {
                        "request_id": request_id,
                        "query": query,
                        "results": [r.model_dump() if hasattr(r, "model_dump") else r.__dict__ for r in results],
                        "total_results": len(results),
                        "project": project,
                        "session_id": session.session_id if session else None,
                    },
                    # Emit to the user's room so UIs & the service client both see it
                    room=session.session_id if session else sid,
                )

            except Exception as e:
                await self.sio.emit("socket_error", {"error": str(e)}, to=sid)

    async def _handle_connect(self, sid, environ, auth):
        origin = environ.get("HTTP_ORIGIN")
        logger.info(f"KB socket connect from {origin} (sid={sid})")

        # 1) Accept access token; ID token optional (warn if missing).
        bearer, idt = resolve_socket_auth_tokens(auth, environ)

        if not bearer:
            # Send a reason the client can read via `connect_error`
            raise ConnectionRefusedError("missing bearer_token")

        try:
            # 2) Do NOT enforce end-user KB perms here for service users.
            session = await self.auth_with_acct.process_socket_connect(
                auth, environ,
                # Allow service OR UI; no hard KB permission check at connect
                RequireRoles("kdcube:role:super-admin", "kdcube:role:service", require_all=False),
                require_all=False,
                component=self.component_name,
                require_existing_session=False,
                verify_token_session_match=False,
            )
        except AuthenticationError as e:
            raise ConnectionRefusedError(f"auth error: {e.message}")  # propagate
        except Exception as e:
            logger.exception("KB socket connect error")
            raise ConnectionRefusedError("internal error")

        is_service = session is None
        sock_state = {
            "authenticated": True,
            "internal": False,
            "project": (auth or {}).get("project"),
            "tenant": (auth or {}).get("tenant"),
            "user_session": (session.__dict__ if session else None),
            "service": is_service,
        }
        await self.sio.save_session(sid, sock_state)

        if session:
            await self.sio.enter_room(sid, session.session_id)

        await self.sio.emit("session_info", {
            "connected_as_service": is_service,
            "session_id": getattr(session, "session_id", None),
            "user_type": getattr(session, "user_type", None).value if session else None,
            "username": getattr(session, "username", None),
            "project": sock_state.get("project"),
            "tenant": sock_state.get("tenant"),
        }, to=sid)

        logger.info(f"KB socket connected sid={sid}, service={is_service}, session={getattr(session,'session_id',None)}")

    async def _resolve_event_context(self, sid: str, payload: dict):
        """
        Session resolution:
        1) payload['on_behalf_session_id'] (highest priority)
        2) socket_state['user_session'] (UI user from connect)
        """
        sock = await self.sio.get_session(sid) or {}
        project = (payload or {}).get("project") or sock.get("project")
        tenant = (payload or {}).get("tenant") or sock.get("tenant")

        target_id = (payload or {}).get("on_behalf_session_id")
        session = None
        if target_id:
            session = await self.auth_with_acct.get_session_by_id(target_id)
            if session:
                await self.sio.enter_room(sid, session.session_id)  # deliver events to that user
        elif sock.get("user_session"):
            from kdcube_ai_app.auth.sessions import UserSession as US
            session = US(**sock["user_session"])

        return session, project, tenant

    async def _on_pubsub_message(self, message: dict):
        try:
            target_sid = message.get("target_sid")
            session_id = message.get("session_id")
            event = message["event"]
            data = message["data"]
            resource_id = data.get("resource_id")
            channel = f"resource_processing_progress:{resource_id}" if resource_id else event
            unified = {"event": event, **data}
            logger.info(f"KB socket pubsub: {channel}, to sid={target_sid}, session={session_id}")

            if target_sid:
                await self.sio.emit(channel, unified, room=target_sid)
            if session_id:
                await self.sio.emit(channel, unified, room=session_id)
        except Exception as e:
            logger.error(f"Relay error: {e}")

    def get_asgi_app(self):
        return socketio.ASGIApp(self.sio) if self.sio else None

    async def start(self):
        if getattr(self, "_listener_started", False):
            return
        await self.comm.subscribe(self.relay_channel)
        await self.comm.start_listener(self._on_pubsub_message)
        self._listener_started = True
        logger.info("Socket.IO KB handler subscribed & listening.")

    async def stop(self):
        await self.comm.stop_listener()
        self._listener_started = False
        logger.info("Socket.IO KB handler stopped.")

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Renew routing leases from the owning Socket.IO transport's liveness."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_session_lease import (
    transport_lease_seconds,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import (
    DataBusLiveSession,
    DataBusLiveSessionRegistry,
)

logger = logging.getLogger(__name__)


class DataBusSocketLeases:
    """Process-local transport tasks; Redis carries cluster-visible routing."""

    def __init__(self, sio: Any) -> None:
        self.sio = sio
        self._tasks: dict[str, asyncio.Task] = {}
        self._sessions: dict[str, DataBusLiveSession] = {}

    def registration_id(self, sid: str) -> str | None:
        session = self._sessions.get(sid)
        return session.registration_id if session is not None else None

    @property
    def lease_seconds(self) -> int:
        return transport_lease_seconds(
            ping_interval=self.sio.eio.ping_interval,
            ping_timeout=self.sio.eio.ping_timeout,
        )

    async def start(
        self, *, registry: DataBusLiveSessionRegistry, session: DataBusLiveSession,
        tenant: str, project: str, bundle_id: str,
    ) -> None:
        await self.stop(session.socket_id)
        self._sessions[session.socket_id] = session
        self._tasks[session.socket_id] = asyncio.create_task(self._renew(
            registry=registry, session=session, tenant=tenant,
            project=project, bundle_id=bundle_id,
        ))

    async def _transport_refusal_reason(self, sid: str) -> str | None:
        manager = self.sio.manager
        if not manager.is_connected(sid, "/"):
            return "namespace_disconnected"
        eio_sid = manager.eio_sid_from_sid(sid, "/")
        socket = self.sio.eio.sockets.get(eio_sid)
        if socket is None:
            return "socket_missing"
        if not socket.connected or socket.closed or socket.closing:
            return "socket_closed"
        # Engine.IO's own timeout test covers missing pong even when business
        # traffic is idle, and closes half-open transports through its handler.
        try:
            return None if await socket.check_ping_timeout() else "ping_timeout"
        except Exception as exc:
            logger.warning("Data Bus transport liveness unavailable: %s", type(exc).__name__[:64])
            return "liveness_unavailable"

    async def _healthy(self, sid: str) -> bool:
        return await self._transport_refusal_reason(sid) is None

    async def _renew(self, *, registry, session, tenant, project, bundle_id) -> None:
        sid = session.socket_id
        current = asyncio.current_task()
        interval = max(1.0, min(self.sio.eio.ping_interval, self.sio.eio.ping_timeout))
        next_lag_warning = next_error_warning = float("-inf")
        try:
            while True:
                # The connect callback must return before the first liveness
                # check; Socket.IO may still be finalizing the namespace.
                scheduled = monotonic() + interval
                await asyncio.sleep(interval)
                now = monotonic()
                # Saturate diagnostic values at one day; no identity or payload.
                lag_ms = min(86_400_000.0, max(0.0, now - scheduled) * 1000)
                if self._tasks.get(sid) is not current:
                    break
                if lag_ms >= interval * 1000 and now >= next_lag_warning:
                    logger.warning("Data Bus transport lease check delayed: reason=scheduling_lag lag_ms=%.0f lease_seconds=%s",
                                   lag_ms, self.lease_seconds)
                    next_lag_warning = now + 60
                reason = await self._transport_refusal_reason(sid)
                if reason is not None:
                    logger.info("Data Bus transport lease stopped: reason=%s lag_ms=%.0f lease_seconds=%s",
                                reason, lag_ms, self.lease_seconds)
                    break
                try:
                    renewed = await registry.renew(
                        tenant=tenant, project=project, bundle_id=bundle_id, session=session,
                    )
                except Exception as exc:
                    # An outage cannot extend the old lease. Try again while
                    # the transport is healthy; the Redis TTL remains bounded.
                    if now >= next_error_warning:
                        logger.warning("Data Bus transport lease renewal unavailable: reason=renewal_unavailable lag_ms=%.0f lease_seconds=%s error_type=%s",
                                       lag_ms, self.lease_seconds, type(exc).__name__[:64])
                        next_error_warning = now + 60
                    continue
                if not renewed:
                    # Never recreate a removed registration from an old task.
                    # A new connect re-admits credentials and creates a new SID.
                    if self._tasks.get(sid) is current:
                        logger.warning("Data Bus transport lease renewal refused: reason=registration_refused lag_ms=%.0f lease_seconds=%s",
                                       lag_ms, self.lease_seconds)
                        await self.sio.disconnect(sid)
                    break
        finally:
            if self._tasks.get(sid) is current:
                self._tasks.pop(sid, None)
                self._sessions.pop(sid, None)
            try:
                await registry.unregister(socket_id=sid, registration_id=session.registration_id)
            except Exception as exc:
                logger.warning("Data Bus transport lease cleanup unavailable: %s", type(exc).__name__[:64])

    async def stop(self, sid: str) -> None:
        task = self._tasks.pop(sid, None)
        self._sessions.pop(sid, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop_all(self) -> None:
        for sid in tuple(self._tasks):
            await self.stop(sid)

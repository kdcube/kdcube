# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Live Data Bus session routing for bundle-scoped federated clients."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

LIVE_SESSION_SCHEMA = "kdcube.data_bus.live_session.v1"
LIVE_SESSION_INDEX_TTL_SECONDS = 3600


def _principal_digest(principal: str) -> str:
    return hashlib.sha256(str(principal).encode("utf-8")).hexdigest()


def _scope_prefix(*, tenant: str, project: str, bundle_id: str) -> str:
    return f"kdcube:data-bus-live:{tenant}:{project}:{bundle_id}"


@dataclass(frozen=True, slots=True)
class DataBusLiveSession:
    principal: str
    session_id: str
    socket_id: str
    expires_at: int


class DataBusLiveSessionRegistry:
    """Redis-backed routing index for currently connected federated sockets."""

    def __init__(self, redis: Any) -> None:
        if redis is None:
            raise ValueError("A Redis client is required for live Data Bus sessions.")
        self.redis = redis

    @staticmethod
    def _index_key(*, tenant: str, project: str, bundle_id: str, principal: str) -> str:
        return (
            f"{_scope_prefix(tenant=tenant, project=project, bundle_id=bundle_id)}:"
            f"principal:{_principal_digest(principal)}"
        )

    @staticmethod
    def _socket_key(socket_id: str) -> str:
        return f"kdcube:data-bus-live:socket:{socket_id}"

    async def register(
        self,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        principal: str,
        session_id: str,
        socket_id: str,
        expires_at: int,
    ) -> DataBusLiveSession:
        now = int(time.time())
        expiry = max(now + 1, int(expires_at))
        ttl = max(1, expiry - now)
        record = {
            "schema": LIVE_SESSION_SCHEMA,
            "tenant": str(tenant),
            "project": str(project),
            "bundle_id": str(bundle_id),
            "principal": str(principal),
            "session_id": str(session_id),
            "socket_id": str(socket_id),
            "expires_at": expiry,
        }
        member = json.dumps(
            {"session_id": str(session_id), "socket_id": str(socket_id)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        index_key = self._index_key(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            principal=principal,
        )
        await self.redis.zadd(index_key, {member: expiry})
        # Every member carries its own expiry score. Keep the shared index long
        # enough for the maximum federated-token lifetime so registering a
        # near-expiry socket cannot delete another live socket's index early.
        await self.redis.expire(index_key, max(ttl, LIVE_SESSION_INDEX_TTL_SECONDS))
        await self.redis.setex(
            self._socket_key(socket_id),
            ttl,
            json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        return DataBusLiveSession(
            principal=str(principal),
            session_id=str(session_id),
            socket_id=str(socket_id),
            expires_at=expiry,
        )

    async def unregister(self, *, socket_id: str) -> None:
        socket_key = self._socket_key(socket_id)
        raw = await self.redis.get(socket_key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if not raw:
            return
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            await self.redis.delete(socket_key)
            return
        if not isinstance(record, Mapping):
            await self.redis.delete(socket_key)
            return
        member = json.dumps(
            {
                "session_id": str(record.get("session_id") or ""),
                "socket_id": str(record.get("socket_id") or socket_id),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        index_key = self._index_key(
            tenant=str(record.get("tenant") or ""),
            project=str(record.get("project") or ""),
            bundle_id=str(record.get("bundle_id") or ""),
            principal=str(record.get("principal") or ""),
        )
        await self.redis.zrem(index_key, member)
        await self.redis.delete(socket_key)

    async def sessions(
        self,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        principal: str,
        now: int | None = None,
    ) -> tuple[str, ...]:
        current = int(time.time() if now is None else now)
        index_key = self._index_key(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            principal=principal,
        )
        await self.redis.zremrangebyscore(index_key, "-inf", current - 1)
        raw_members = await self.redis.zrangebyscore(index_key, current, "+inf")
        session_ids: set[str] = set()
        for raw in raw_members or ():
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            try:
                member = json.loads(str(raw))
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(member, Mapping) and member.get("session_id"):
                session_ids.add(str(member["session_id"]))
        return tuple(sorted(session_ids))

    async def connected(self, **scope: Any) -> bool:
        return bool(await self.sessions(**scope))


class DataBusLiveSessionPublisher:
    """Send a compact service event to every live session for one principal."""

    def __init__(
        self,
        *,
        redis: Any,
        tenant: str,
        project: str,
        bundle_id: str,
        relay: Any | None = None,
        communicator_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = DataBusLiveSessionRegistry(redis)
        self.tenant = str(tenant)
        self.project = str(project)
        self.bundle_id = str(bundle_id)
        if relay is None:
            # Import lazily: chat.emitters imports the Data Bus publisher while
            # the data_bus package itself exports this helper.
            from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator

            relay = ChatRelayCommunicator()
        self.relay = relay
        self.communicator_factory = communicator_factory

    async def connected(self, principal: str) -> bool:
        return await self.registry.connected(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            principal=principal,
        )

    async def publish(
        self,
        *,
        principal: str,
        event_type: str,
        data: Mapping[str, Any],
        status: str = "running",
    ) -> int:
        sessions = await self.registry.sessions(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            principal=principal,
        )
        for session_id in sessions:
            communicator_factory = self.communicator_factory
            if communicator_factory is None:
                from kdcube_ai_app.apps.chat.emitters import ChatCommunicator

                communicator_factory = ChatCommunicator

            comm = communicator_factory(
                emitter=self.relay,
                tenant=self.tenant,
                project=self.project,
                user_id=principal,
                user_type="external",
                service={
                    "request_id": str(uuid.uuid4()),
                    "tenant": self.tenant,
                    "project": self.project,
                    "user": principal,
                    "bundle_id": self.bundle_id,
                },
                conversation={
                    "session_id": session_id,
                    "conversation_id": session_id,
                    "turn_id": None,
                },
                room=session_id,
            )
            await comm.service_event(
                type=event_type,
                step="data_bus.push",
                status=status,
                data=dict(data),
                agent="data_bus",
                auto_markdown=False,
                broadcast=True,
            )
        return len(sessions)


__all__ = [
    "DataBusLiveSession",
    "DataBusLiveSessionPublisher",
    "DataBusLiveSessionRegistry",
    "LIVE_SESSION_SCHEMA",
    "LIVE_SESSION_INDEX_TTL_SECONDS",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Live Data Bus session routing for bundle-scoped federated clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from connection_hub.delegated_credentials.live_grant import (
    LiveGrantCardError,
    live_grants_for_resource,
    resolve_live_grant_card,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.serving import (
    delegated_card_store,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_session_lease import (
    DEFAULT_TRANSPORT_LEASE_SECONDS,
    prune_mismatched_member,
    prune_missing_member,
    register_lease,
    release_lease,
    renew_lease,
    routing_member,
)

LIVE_SESSION_SCHEMA = "kdcube.data_bus.live_session.v1"
LIVE_SESSION_INDEX_TTL_SECONDS = 3600
# Bounds for sessions_many: socket authority checks running at once, and
# principals or socket records per Redis pipeline.
LIVE_SESSION_BATCH_CONCURRENCY = 16
LIVE_SESSION_BATCH_CHUNK = 64


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
    registration_id: str = ""


class DataBusLiveSessionRegistry:
    """Redis-backed routing index for currently connected federated sockets."""

    def __init__(self, redis: Any, *, lease_seconds: int = DEFAULT_TRANSPORT_LEASE_SECONDS) -> None:
        if redis is None:
            raise ValueError("A Redis client is required for live Data Bus sessions.")
        self.redis = redis
        if lease_seconds <= 0:
            raise ValueError("A live transport lease must have a positive duration.")
        self.lease_seconds = int(lease_seconds)

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
        authorization_scope: Mapping[str, Any] | None = None,
    ) -> DataBusLiveSession:
        now = int(time.time())
        expiry = int(expires_at)
        registration_id = str(uuid.uuid4())
        record = {
            "schema": LIVE_SESSION_SCHEMA,
            "tenant": str(tenant),
            "project": str(project),
            "bundle_id": str(bundle_id),
            "principal": str(principal),
            "session_id": str(session_id),
            "socket_id": str(socket_id),
            "expires_at": expiry,
            "registration_id": registration_id,
        }
        authority = dict(authorization_scope or {})
        if str(authority.get("credential_kind") or "") == "delegated_card":
            record["authorization"] = {
                "credential_kind": "delegated_card",
                "access_id": str(authority.get("access_id") or ""),
                "resource": str(authority.get("resource") or ""),
                "client_id": str(authority.get("client_id") or ""),
                "grantor_user_id": str(authority.get("grantor_user_id") or ""),
                "delegate_identity": str(
                    authority.get("delegate_identity") or ""
                ),
            }
        member = routing_member(session_id=session_id, socket_id=socket_id)
        index_key = self._index_key(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            principal=principal,
        )
        await register_lease(
            self.redis, socket_key=self._socket_key(socket_id), index_key=index_key,
            member=member, record=record, now=now, lease_seconds=self.lease_seconds,
            index_ttl=LIVE_SESSION_INDEX_TTL_SECONDS,
        )
        return DataBusLiveSession(
            principal=str(principal),
            session_id=str(session_id),
            socket_id=str(socket_id),
            expires_at=expiry,
            registration_id=registration_id,
        )

    async def renew(
        self, *, tenant: str, project: str, bundle_id: str, session: DataBusLiveSession,
    ) -> bool:
        return await renew_lease(
            self.redis, socket_key=self._socket_key(session.socket_id),
            index_key=self._index_key(tenant=tenant, project=project,
                                      bundle_id=bundle_id, principal=session.principal),
            member=routing_member(session_id=session.session_id, socket_id=session.socket_id),
            registration_id=session.registration_id, now=int(time.time()),
            lease_seconds=self.lease_seconds, index_ttl=LIVE_SESSION_INDEX_TTL_SECONDS,
        )

    async def unregister(self, *, socket_id: str, registration_id: str | None = None) -> None:
        socket_key = self._socket_key(socket_id)
        raw = await self.redis.get(socket_key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if not raw:
            return
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(record, Mapping):
            return
        if registration_id is not None and record.get("registration_id") != registration_id:
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
        await release_lease(self.redis, socket_key=socket_key, index_key=index_key,
                            member=member, raw_record=raw)

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
        await self.redis.zremrangebyscore(index_key, "-inf", current)
        raw_members = await self.redis.zrangebyscore(index_key, current, "+inf")
        session_ids: set[str] = set()
        for raw in raw_members or ():
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            try:
                member = json.loads(str(raw))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(member, Mapping) or not member.get("session_id"):
                continue
            socket_id = str(member.get("socket_id") or "").strip()
            if not socket_id:
                continue
            if socket_id:
                socket_key = self._socket_key(socket_id)
                raw_record = await self.redis.get(socket_key)
                if not raw_record:
                    await prune_missing_member(self.redis, socket_key=socket_key,
                                               index_key=index_key, member=raw)
                    continue
                try:
                    record = json.loads(raw_record)
                except (TypeError, ValueError):
                    record = None
                expected = {"tenant": str(tenant), "project": str(project),
                            "bundle_id": str(bundle_id), "principal": str(principal),
                            "session_id": str(member["session_id"]), "socket_id": socket_id}
                if not isinstance(record, Mapping) or any(record.get(k) != v for k, v in expected.items()):
                    # A stale index must never route through a socket now bound
                    # to another principal or scope. Keep that other socket.
                    await prune_mismatched_member(self.redis, socket_key=socket_key,
                                                 index_key=index_key, member=raw,
                                                 raw_record=raw_record)
                    continue
                lease_expiry = record.get("lease_expires_at")
                credential_expiry = record.get("expires_at")
                if (record.get("schema") != LIVE_SESSION_SCHEMA
                        or not record.get("registration_id")
                        or type(lease_expiry) is not int
                        or type(credential_expiry) is not int
                        or lease_expiry <= current
                        or lease_expiry > credential_expiry):
                    # A pre-lease record cannot prove recent transport liveness.
                    # Exact-value removal preserves a concurrently renewed SID;
                    # successful reconnect installs a bounded registration.
                    await release_lease(self.redis, socket_key=socket_key, index_key=index_key,
                                        member=raw, raw_record=raw_record)
                    continue
                live = await self._live_authority(socket_id=socket_id, raw_record=raw_record)
                if live is False:
                    await release_lease(self.redis, socket_key=socket_key, index_key=index_key,
                                        member=raw, raw_record=raw_record)
                    continue
                if live is None:
                    # Authority lookup is unavailable. Keep the record for a
                    # later retry, but fail closed for this delivery.
                    continue
            session_ids.add(str(member["session_id"]))
        return tuple(sorted(session_ids))

    async def sessions_many(
        self,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        principals: Any,
        now: int | None = None,
        concurrency: int = LIVE_SESSION_BATCH_CONCURRENCY,
        chunk: int = LIVE_SESSION_BATCH_CHUNK,
    ) -> dict[str, tuple[str, ...]]:
        """``sessions()`` for several principals with fewer sequential round trips.

        The result equals calling ``sessions()`` per principal at one ``now``:
        the same prune and range per index, the same per-record branches and
        cleanup helpers, and each surviving socket's own live authority. The
        routing reads of up to ``chunk`` principals share one non-transactional
        pipeline; socket records are read ``chunk`` at a time with pipelined
        GETs (a wrong-type key raises, as GET does in ``sessions()``); at most
        ``concurrency`` authority checks run at once. Nothing is kept after the
        call. A routing or record read error propagates as it arises; among
        authority-phase failures the earliest (principal, socket) one is raised,
        unwrapped. No atomic snapshot across principals or Cards is implied.
        """

        if type(concurrency) is not int or concurrency <= 0:
            raise ValueError("concurrency must be a positive integer")
        if type(chunk) is not int or chunk <= 0:
            raise ValueError("chunk must be a positive integer")
        unique = list(dict.fromkeys(str(principal) for principal in principals))
        if not unique:
            return {}
        if not callable(getattr(self.redis, "pipeline", None)):
            # A client without pipelines takes the serial path, unchanged.
            return {
                principal: await self.sessions(
                    tenant=tenant, project=project, bundle_id=bundle_id,
                    principal=principal, now=now,
                )
                for principal in unique
            }
        current = int(time.time() if now is None else now)
        found: dict[str, set[str]] = {principal: set() for principal in unique}
        for start in range(0, len(unique), chunk):
            group = unique[start:start + chunk]
            index_keys = [
                self._index_key(tenant=tenant, project=project, bundle_id=bundle_id, principal=principal)
                for principal in group
            ]
            pipe = self.redis.pipeline(transaction=False)
            for index_key in index_keys:
                pipe.zremrangebyscore(index_key, "-inf", current)
                pipe.zrangebyscore(index_key, current, "+inf")
            replies = await pipe.execute(raise_on_error=True)
            routes: list[tuple[str, str, Any, dict[str, Any], str]] = []
            for position, principal in enumerate(group):
                for raw in replies[2 * position + 1] or ():
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8")
                    try:
                        member = json.loads(str(raw))
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(member, Mapping) or not member.get("session_id"):
                        continue
                    socket_id = str(member.get("socket_id") or "").strip()
                    if not socket_id:
                        continue
                    routes.append((principal, index_keys[position], raw, dict(member), socket_id))
            for record_start in range(0, len(routes), chunk):
                part = routes[record_start:record_start + chunk]
                pipe = self.redis.pipeline(transaction=False)
                for _principal, _index_key, _raw, _member, socket_id in part:
                    pipe.get(self._socket_key(socket_id))
                records = await pipe.execute(raise_on_error=True)
                qualifying = []
                for (principal, index_key, raw, member, socket_id), raw_record in zip(part, records):
                    socket_key = self._socket_key(socket_id)
                    if not raw_record:
                        await prune_missing_member(self.redis, socket_key=socket_key,
                                                   index_key=index_key, member=raw)
                        continue
                    try:
                        record = json.loads(raw_record)
                    except (TypeError, ValueError):
                        record = None
                    expected = {"tenant": str(tenant), "project": str(project),
                                "bundle_id": str(bundle_id), "principal": str(principal),
                                "session_id": str(member["session_id"]), "socket_id": socket_id}
                    if not isinstance(record, Mapping) or any(record.get(k) != v for k, v in expected.items()):
                        await prune_mismatched_member(self.redis, socket_key=socket_key,
                                                     index_key=index_key, member=raw,
                                                     raw_record=raw_record)
                        continue
                    lease_expiry = record.get("lease_expires_at")
                    credential_expiry = record.get("expires_at")
                    if (record.get("schema") != LIVE_SESSION_SCHEMA
                            or not record.get("registration_id")
                            or type(lease_expiry) is not int
                            or type(credential_expiry) is not int
                            or lease_expiry <= current
                            or lease_expiry > credential_expiry):
                        await release_lease(self.redis, socket_key=socket_key, index_key=index_key,
                                            member=raw, raw_record=raw_record)
                        continue
                    qualifying.append((principal, index_key, raw, str(member["session_id"]),
                                       socket_id, raw_record))
                await self._authorize_routes(qualifying, found, concurrency=concurrency)
        return {principal: tuple(sorted(found[principal])) for principal in unique}

    async def _authorize_routes(self, routes: list, found: dict[str, set[str]], *, concurrency: int) -> None:
        """Each route's own live authority, at most ``concurrency`` at once, owned and drained."""

        if not routes:
            return
        queue = deque(enumerate(routes))
        failures: list[tuple[int, BaseException]] = []

        async def work() -> None:
            while queue and not failures:
                position, (principal, index_key, raw, session_id, socket_id, raw_record) = queue.popleft()
                try:
                    live = await self._live_authority(socket_id=socket_id, raw_record=raw_record)
                    if live is False:
                        await release_lease(self.redis, socket_key=self._socket_key(socket_id),
                                            index_key=index_key, member=raw, raw_record=raw_record)
                        continue
                    if live is None:
                        # Authority unavailable: keep the route for a retry, exclude it now.
                        continue
                    found[principal].add(session_id)
                except Exception as exc:  # noqa: BLE001 - re-raised below, unwrapped
                    failures.append((position, exc))

        async with asyncio.TaskGroup() as group:
            for _ in range(min(concurrency, len(routes))):
                group.create_task(work())
        if failures:
            raise min(failures, key=lambda failure: failure[0])[1]

    async def _live_authority(self, *, socket_id: str, raw_record: str | bytes | None = None) -> bool | None:
        raw = raw_record if raw_record is not None else await self.redis.get(self._socket_key(socket_id))
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if not raw:
            return False
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(record, Mapping):
            return False
        authorization = record.get("authorization")
        if not isinstance(authorization, Mapping):
            return True
        if str(authorization.get("credential_kind") or "") != "delegated_card":
            return True
        access_id = str(authorization.get("access_id") or "").strip()
        resource = str(authorization.get("resource") or "").strip()
        if not access_id or not resource:
            return False
        tenant = str(record.get("tenant") or "")
        project = str(record.get("project") or "")
        try:
            # A record written before the grantor was kept resolves without
            # the store; a missing projection then reads as unknown, not
            # revoked.
            card = await resolve_live_grant_card(
                self.redis,
                tenant=tenant,
                project=project,
                access_id=access_id,
                expected_client_id=str(
                    authorization.get("client_id") or ""
                ).strip(),
                expected_grantor_subject=str(
                    authorization.get("grantor_user_id") or ""
                ).strip(),
                expected_delegate_subject=str(
                    authorization.get("delegate_identity") or ""
                ).strip(),
                card_store=delegated_card_store(tenant=tenant, project=project),
            )
        except LiveGrantCardError:
            return None
        except Exception:
            return None
        if card is None:
            return False
        return live_grants_for_resource(card, resource) is not None

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

    async def connected_many(self, principals: Any) -> dict[str, bool]:
        """``connected()`` for several principals through one ``sessions_many`` call."""

        sessions = await self.registry.sessions_many(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
            principals=principals,
        )
        return {principal: bool(found) for principal, found in sessions.items()}

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

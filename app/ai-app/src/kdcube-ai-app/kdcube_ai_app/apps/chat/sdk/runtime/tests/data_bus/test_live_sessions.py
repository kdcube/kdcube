from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions as live_sessions
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import (
    DataBusLiveSessionPublisher,
    DataBusLiveSessionRegistry,
    LIVE_SESSION_INDEX_TTL_SECONDS,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tests.data_bus.live_lease_fake import eval_transport_lease


@pytest.fixture(autouse=True)
def fixed_registration_clock(monkeypatch):
    # A routing-lease test needs registration and observation in one clock.
    monkeypatch.setattr(live_sessions.time, "time", lambda: 1000)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted: dict[str, dict[str, float]] = {}
        self.expiries: dict[str, int] = {}

    async def eval(self, script, key_count, *args):
        return await eval_transport_lease(self, script, key_count, *args)

    async def zadd(self, key, values):
        self.sorted.setdefault(key, {}).update(values)

    async def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.expiries[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def zrem(self, key, member):
        self.sorted.get(key, {}).pop(member, None)

    async def zremrangebyscore(self, key, low, high):
        del low
        cutoff = float(high)
        rows = self.sorted.get(key, {})
        for member, score in list(rows.items()):
            if score <= cutoff:
                rows.pop(member, None)

    async def zrangebyscore(self, key, low, high):
        minimum = float(low)
        maximum = float("inf") if high == "+inf" else float(high)
        return [
            member
            for member, score in self.sorted.get(key, {}).items()
            if minimum <= score <= maximum
        ]


class _Communicator:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = []
        self.instances.append(self)

    async def service_event(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_registry_is_partitioned_by_bundle_and_principal() -> None:
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        session_id="session-alpha",
        socket_id="socket-alpha",
        expires_at=1200,
    )
    await registry.register(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:beta",
        session_id="session-beta",
        socket_id="socket-beta",
        expires_at=1300,
    )

    assert await registry.sessions(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        now=1000,
    ) == ("session-alpha",)
    assert max(redis.expiries.values()) >= LIVE_SESSION_INDEX_TTL_SECONDS

    await registry.unregister(socket_id="socket-alpha")
    assert not await registry.connected(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        now=1000,
    )


@pytest.mark.asyncio
async def test_publisher_targets_only_addressed_principal_sessions() -> None:
    _Communicator.instances.clear()
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    for principal, session in (
        ("card:alpha", "session-a1"),
        ("card:alpha", "session-a2"),
        ("card:beta", "session-b"),
    ):
        await registry.register(
            tenant="tenant-a",
            project="project-a",
            bundle_id="problem-board@1-0",
            principal=principal,
            session_id=session,
            socket_id=f"socket-{session}",
            expires_at=2_000_000_000,
        )
    publisher = DataBusLiveSessionPublisher(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        relay=object(),
        communicator_factory=_Communicator,
    )

    delivered = await publisher.publish(
        principal="card:alpha",
        event_type="problem_board.worker.event.v1",
        data={"kind": "control.available", "control_ref": "work:control:1"},
    )

    assert delivered == 2
    assert {row.kwargs["room"] for row in _Communicator.instances} == {
        "session-a1",
        "session-a2",
    }
    assert all(
        row.events[0]["type"] == "problem_board.worker.event.v1"
        for row in _Communicator.instances
    )


@pytest.mark.asyncio
async def test_revoked_card_is_removed_before_outbound_delivery(monkeypatch) -> None:
    async def resolve(redis, **kwargs):
        del redis, kwargs
        return None

    monkeypatch.setattr(live_sessions, "resolve_live_grant_card", resolve)
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        session_id="session-alpha",
        socket_id="socket-alpha",
        expires_at=2_000_000_000,
        authorization_scope={
            "credential_kind": "delegated_card",
            "access_id": "alpha",
            "resource": "https://runtime.example/problem-board",
            "client_id": "worker-alpha",
            "grantor_user_id": "grantor-alpha",
            "delegate_identity": "worker:alpha",
            "delegated_bearer_token": "must-not-be-stored",
        },
    )

    stored = json.loads(redis.values[registry._socket_key("socket-alpha")])
    assert stored["authorization"] == {
        "credential_kind": "delegated_card",
        "access_id": "alpha",
        "resource": "https://runtime.example/problem-board",
        "client_id": "worker-alpha",
        "grantor_user_id": "grantor-alpha",
        "delegate_identity": "worker:alpha",
    }
    assert "must-not-be-stored" not in json.dumps(stored)

    assert await registry.sessions(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        now=1000,
    ) == ()
    assert registry._socket_key("socket-alpha") not in redis.values


@pytest.mark.asyncio
async def test_active_card_remains_in_outbound_delivery_index(monkeypatch) -> None:
    resource = "https://runtime.example/problem-board"

    async def resolve(redis, **kwargs):
        del redis, kwargs
        return SimpleNamespace(resource_grants={resource: ("work:relay",)})

    monkeypatch.setattr(live_sessions, "resolve_live_grant_card", resolve)
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        session_id="session-alpha",
        socket_id="socket-alpha",
        expires_at=2_000_000_000,
        authorization_scope={
            "credential_kind": "delegated_card",
            "access_id": "alpha",
            "resource": resource,
            "client_id": "worker-alpha",
            "delegate_identity": "worker:alpha",
        },
    )

    assert await registry.sessions(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        now=1000,
    ) == ("session-alpha",)



@pytest.mark.asyncio
async def test_live_session_card_lookup_reads_through_the_durable_store(monkeypatch) -> None:
    # A missing projection must resolve from the durable Card, not read as
    # revoked, so the lookup carries the store and the grantor it needs.
    seen: dict = {}
    store = object()

    async def resolve(redis, **kwargs):
        del redis
        seen.update(kwargs)
        return None

    monkeypatch.setattr(live_sessions, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(
        live_sessions, "delegated_card_store", lambda **scope: store
    )
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        session_id="session-alpha",
        socket_id="socket-alpha",
        expires_at=2_000_000_000,
        authorization_scope={
            "credential_kind": "delegated_card",
            "access_id": "alpha",
            "resource": "https://runtime.example/problem-board",
            "client_id": "worker-alpha",
            "grantor_user_id": "grantor-alpha",
            "delegate_identity": "worker:alpha",
        },
    )

    await registry.sessions(
        tenant="tenant-a",
        project="project-a",
        bundle_id="problem-board@1-0",
        principal="card:alpha",
        now=1000,
    )

    assert seen["card_store"] is store
    assert seen["expected_grantor_subject"] == "grantor-alpha"


@pytest.mark.asyncio
async def test_crashed_socket_expires_without_card_reads(monkeypatch):
    from unittest.mock import AsyncMock
    resolve = AsyncMock()
    monkeypatch.setattr(live_sessions, "resolve_live_grant_card", resolve)
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(tenant="tenant", project="project", bundle_id="bundle",
                            principal="principal", session_id="session", socket_id="sid",
                            expires_at=2_000_000_000)
    assert redis.expiries[registry._socket_key("sid")] == 75
    assert await registry.sessions(tenant="tenant", project="project", bundle_id="bundle",
                                   principal="principal", now=1075) == ()
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_socket_prunes_its_exact_member():
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    await registry.register(tenant="tenant", project="project", bundle_id="bundle",
                            principal="principal", session_id="session", socket_id="sid",
                            expires_at=2_000_000_000)
    redis.values.pop(registry._socket_key("sid"))
    assert await registry.sessions(tenant="tenant", project="project", bundle_id="bundle",
                                   principal="principal", now=1000) == ()
    assert not any(redis.sorted.values())


@pytest.mark.asyncio
async def test_old_connection_cannot_renew_or_remove_successor():
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    scope = dict(tenant="tenant", project="project", bundle_id="bundle")
    old = await registry.register(**scope, principal="principal", session_id="session",
                                  socket_id="sid", expires_at=2_000_000_000)
    new = await registry.register(**scope, principal="principal", session_id="session",
                                  socket_id="sid", expires_at=2_000_000_000)
    assert not await registry.renew(**scope, session=old)
    await registry.unregister(socket_id="sid", registration_id=old.registration_id)
    assert json.loads(redis.values[registry._socket_key("sid")])["registration_id"] == new.registration_id
    assert await registry.renew(**scope, session=new)


@pytest.mark.asyncio
async def test_stale_principal_index_never_routes_a_rebound_socket():
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    scope = dict(tenant="tenant", project="project", bundle_id="bundle")
    await registry.register(**scope, principal="old", session_id="old-session",
                            socket_id="sid", expires_at=2_000_000_000)
    await registry.register(**scope, principal="new", session_id="new-session",
                            socket_id="sid", expires_at=2_000_000_000)
    assert await registry.sessions(**scope, principal="old", now=1000) == ()
    assert await registry.sessions(**scope, principal="new", now=1000) == ("new-session",)


@pytest.mark.asyncio
async def test_legacy_long_lived_record_does_not_prove_current_transport():
    redis = _Redis()
    registry = DataBusLiveSessionRegistry(redis)
    scope = dict(tenant="tenant", project="project", bundle_id="bundle")
    await registry.register(**scope, principal="principal", session_id="session",
                            socket_id="sid", expires_at=2_000_000_000)
    key = registry._socket_key("sid")
    record = json.loads(redis.values[key])
    record.pop("registration_id")
    record.pop("lease_expires_at")
    redis.values[key] = json.dumps(record)
    assert await registry.sessions(**scope, principal="principal", now=1000) == ()
    assert key not in redis.values

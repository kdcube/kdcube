from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import (
    DataBusLiveSessionPublisher,
    DataBusLiveSessionRegistry,
    LIVE_SESSION_INDEX_TTL_SECONDS,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted: dict[str, dict[str, float]] = {}
        self.expiries: dict[str, int] = {}

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

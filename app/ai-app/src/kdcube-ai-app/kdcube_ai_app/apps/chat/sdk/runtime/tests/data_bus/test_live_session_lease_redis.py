from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from redis.asyncio import Redis

import kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions as live_sessions
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_session_lease import (
    register_lease, renew_lease, release_lease, prune_missing_member,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import DataBusLiveSessionRegistry


@pytest_asyncio.fixture
async def routing_redis():
    port = os.getenv("W812_LEASE_TEST_REDIS_PORT")
    if not port:
        pytest.skip("Owned disposable Redis port W812_LEASE_TEST_REDIS_PORT is required")
    redis = Redis(host="127.0.0.1", port=int(port), db=15, decode_responses=False)
    tag = uuid.uuid4().hex
    keys = set()
    def pair():
        number = len(keys)
        socket, index = f"test:lease:{tag}:socket:{number}", f"test:lease:{tag}:index:{number}"
        keys.update((socket, index))
        return socket, index
    try:
        await redis.ping()
        yield redis, pair, keys, tag
    finally:
        if keys:
            await redis.delete(*sorted(keys))
        await redis.aclose()


async def register(redis, socket, index, *, generation="one", expiry=None):
    now = int(time.time())
    credential_expiry = now + 20000 if expiry is None else expiry
    await register_lease(redis, socket_key=socket, index_key=index, member="member",
                         record={"registration_id": generation, "expires_at": credential_expiry},
                         now=now, lease_seconds=75, index_ttl=3600)
    return now, credential_expiry


@pytest.mark.asyncio
async def test_actual_redis_stores_short_ttl_and_matching_index_score(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    now, expiry = await register(redis, socket, index)
    assert 0 < await redis.ttl(socket) <= 75
    assert await redis.zscore(index, "member") == now + 75
    assert json.loads(await redis.get(socket))["expires_at"] == expiry


@pytest.mark.asyncio
async def test_actual_redis_renewal_is_capped_at_credential_expiry(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    now = int(time.time())
    await register(redis, socket, index, expiry=now + 5)
    assert await renew_lease(redis, socket_key=socket, index_key=index, member="member",
                             registration_id="one", now=now + 2, lease_seconds=75, index_ttl=3600)
    assert await redis.zscore(index, "member") == now + 5
    assert 0 < await redis.ttl(socket) <= 3


@pytest.mark.asyncio
async def test_actual_redis_rejects_old_generation_and_expired_credential(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    now, expiry = await register(redis, socket, index, generation="new")
    before = await redis.get(socket)
    for generation, current in (("old", now), ("new", expiry)):
        assert not await renew_lease(redis, socket_key=socket, index_key=index, member="member",
                                     registration_id=generation, now=current, lease_seconds=75, index_ttl=3600)
    assert await redis.get(socket) == before


@pytest.mark.asyncio
async def test_actual_redis_release_is_exact_record_fenced(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    await register(redis, socket, index)
    old = await redis.get(socket)
    await register(redis, socket, index, generation="new")
    assert not await release_lease(redis, socket_key=socket, index_key=index, member="member", raw_record=old)
    assert await redis.zcard(index) == 1
    assert await release_lease(redis, socket_key=socket, index_key=index, member="member",
                               raw_record=await redis.get(socket))
    assert not await redis.exists(socket)
    assert not await redis.exists(index)


@pytest.mark.asyncio
async def test_actual_redis_wrong_index_type_refuses_before_socket_write(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    await redis.set(index, "unrelated")
    with pytest.raises(ValueError):
        await register(redis, socket, index)
    assert not await redis.exists(socket)
    assert await redis.get(index) == b"unrelated"


@pytest.mark.asyncio
async def test_actual_redis_prunes_only_a_missing_socket_member(routing_redis):
    redis, pair, _, _ = routing_redis
    socket, index = pair()
    await register(redis, socket, index)
    assert not await prune_missing_member(redis, socket_key=socket, index_key=index, member="member")
    assert await redis.zcard(index) == 1
    await redis.delete(socket)
    assert await prune_missing_member(redis, socket_key=socket, index_key=index, member="member")
    assert not await redis.exists(index)


@pytest.mark.asyncio
async def test_actual_redis_crash_expires_then_prunes_index(routing_redis):
    redis, _, keys, tag = routing_redis
    registry = DataBusLiveSessionRegistry(redis, lease_seconds=1)
    scope = dict(tenant="test-" + tag, project="project", bundle_id="bundle")
    session = await registry.register(**scope, principal="principal", session_id="session",
                                      socket_id=tag, expires_at=int(time.time()) + 20000)
    socket = registry._socket_key(session.socket_id)
    index = registry._index_key(**scope, principal="principal")
    keys.update((socket, index))
    await asyncio.sleep(1.1)
    assert not await redis.exists(socket)
    assert await registry.sessions(**scope, principal="principal") == ()
    assert not await redis.exists(index)


@pytest.mark.asyncio
async def test_actual_redis_delivery_rechecks_authority_and_revocation(routing_redis, monkeypatch):
    redis, _, keys, tag = routing_redis
    resource = "https://resource.example/data-bus"
    resolver = AsyncMock(side_effect=[SimpleNamespace(resource_grants={resource: ("work:relay",)}), None])
    monkeypatch.setattr(live_sessions, "resolve_live_grant_card", resolver)
    registry = DataBusLiveSessionRegistry(redis)
    scope = dict(tenant="test-" + tag, project="project", bundle_id="bundle")
    await registry.register(**scope, principal="principal", session_id="session", socket_id=tag,
                            expires_at=int(time.time()) + 20000,
                            authorization_scope={"credential_kind": "delegated_card", "access_id": "card",
                                                 "resource": resource, "client_id": "client"})
    socket = registry._socket_key(tag)
    index = registry._index_key(**scope, principal="principal")
    keys.update((socket, index))
    assert await registry.sessions(**scope, principal="principal") == ("session",)
    assert await registry.sessions(**scope, principal="principal") == ()
    assert resolver.await_count == 2
    assert not await redis.exists(socket)

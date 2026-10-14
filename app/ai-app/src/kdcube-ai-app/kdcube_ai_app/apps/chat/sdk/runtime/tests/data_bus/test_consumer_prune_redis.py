"""W827: idle consumers with nothing pending are pruned from the handler group; pending work never is.

Set DATA_BUS_TEST_REDIS_URL to a disposable Redis (each test flushes its database).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import stream as stream_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage

REDIS_URL = os.getenv("DATA_BUS_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="requires a disposable Redis (DATA_BUS_TEST_REDIS_URL)")

MESSAGE = DataBusMessage(message_id="m", tenant="t", project="p", bundle_id="b@1", subject="s", payload={})


@pytest.fixture
async def redis():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL)
    await client.flushdb()
    stream_module._LAST_PRUNE.clear()
    stream_module._GROUP_READY.clear()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def _stream(redis, **kwargs):
    return RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1", consumer_idle_prune_seconds=1,
                              consumer_prune_interval_seconds=0, **kwargs)


async def _consumers(redis, stream):
    return {c["name"].decode(): c["pending"] for c in await redis.xinfo_consumers(stream.messages_key, stream.group_name)}


async def _read(redis, stream, consumer, count=1):
    await redis.xreadgroup(stream.group_name, consumer, {stream.messages_key: ">"}, count=count)


@pytest.mark.asyncio
async def test_an_idle_consumer_with_nothing_pending_is_pruned_and_pending_work_is_kept(redis):
    stream = _stream(redis)
    await stream.publish(MESSAGE)
    await _read(redis, stream, "old-worker-with-pending")
    await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, "old-worker-idle")
    await asyncio.sleep(1.2)

    removed = await stream.prune_idle_consumers(keep="current-worker", force=True)

    assert removed == 1
    assert await _consumers(redis, stream) == {"old-worker-with-pending": 1}
    assert (await redis.xpending(stream.messages_key, stream.group_name))["pending"] == 1


@pytest.mark.asyncio
async def test_the_callers_own_consumer_and_recently_active_consumers_stay(redis):
    stream = _stream(redis)
    await stream.ensure_group()
    await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, "current-worker")
    await asyncio.sleep(1.2)
    await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, "just-started")

    removed = await stream.prune_idle_consumers(keep="current-worker", force=True)

    assert removed == 0
    assert set(await _consumers(redis, stream)) == {"current-worker", "just-started"}


@pytest.mark.asyncio
async def test_one_prune_deletes_at_most_its_limit(redis):
    stream = _stream(redis, consumer_prune_limit=3)
    await stream.ensure_group()
    for n in range(8):
        await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, f"leaked-{n}")
    await asyncio.sleep(1.2)

    assert await stream.prune_idle_consumers(force=True) == 3
    assert len(await _consumers(redis, stream)) == 5


@pytest.mark.asyncio
async def test_a_missing_stream_or_group_prunes_nothing(redis):
    stream = _stream(redis)
    assert await stream.prune_idle_consumers(force=True) == 0
    await redis.xadd(stream.messages_key, {"json": MESSAGE.to_json()})
    assert await stream.prune_idle_consumers(force=True) == 0


@pytest.mark.asyncio
async def test_claim_next_prunes_leaked_consumers_but_not_itself_and_is_throttled(redis):
    stream = RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1", consumer_idle_prune_seconds=1,
                                consumer_prune_interval_seconds=3600)
    await stream.ensure_group()
    await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, "leaked-a")
    await asyncio.sleep(1.2)

    await stream.claim_next(consumer_name="worker-now", block_ms=1)
    assert set(await _consumers(redis, stream)) == {"worker-now"}

    await redis.xgroup_createconsumer(stream.messages_key, stream.group_name, "leaked-b")
    await asyncio.sleep(1.2)
    await stream.claim_next(consumer_name="worker-now", block_ms=1)
    assert "leaked-b" in await _consumers(redis, stream)  # within the interval: no second prune

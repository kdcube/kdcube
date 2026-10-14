"""W827: Data Bus retention never drops unread or pending work, on a real disposable Redis.

Only the message stream changed: results and DLQ keep their count caps. Set DATA_BUS_TEST_REDIS_URL to a
disposable Redis (never a running KDCube Redis: each test flushes its own
database). The script computes the acknowledged boundary on every version; run it on Redis 7, 8.4 and the live
8.x (XTRIM's own ACKED option removed nothing with "~ ... LIMIT" on 8.4.0). Old entries get explicit stream IDs far in the past, so no test sleeps. The fixture sets
stream-node-max-entries to 1 so the approximate (``~``) trim the stream uses removes exactly the eligible
entries; the default node size only makes it trim less, never more.
"""
from __future__ import annotations

import os

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import stream as stream_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage

REDIS_URL = os.getenv("DATA_BUS_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="requires a disposable Redis (DATA_BUS_TEST_REDIS_URL)")

OLD = 1_000_000  # 1970: always outside any retention window


@pytest.fixture
async def redis():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL)
    await client.flushdb()
    await client.config_set("stream-node-max-entries", 1)
    stream_module._LAST_TRIM.clear()
    stream_module._GROUP_READY.clear()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.config_set("stream-node-max-entries", 100)
        await client.aclose()


def _stream(redis, **kwargs):
    return RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1", trim_interval_seconds=0, **kwargs)


MESSAGE = DataBusMessage(message_id="m", tenant="t", project="p", bundle_id="b@1", subject="s", payload={})


async def _old(redis, key, count, *, start=1):
    return [await redis.xadd(key, {"json": MESSAGE.to_json()}, id=f"{OLD}-{n}") for n in range(start, start + count)]


async def _ids(redis, key):
    return [entry_id.decode() for entry_id, _ in await redis.xrange(key)]


async def _read(redis, stream, group, count):
    response = await redis.xreadgroup(group, "c1", {stream.messages_key: ">"}, count=count)
    return [entry_id.decode() for entry_id, _ in response[0][1]] if response else []


async def _trim(stream):
    return await stream.trim(force=True)


@pytest.mark.asyncio
async def test_a_pending_entry_past_the_window_is_kept_and_acknowledged_ones_before_it_go(redis):
    stream = _stream(redis)
    await stream.ensure_group()
    old = await _old(redis, stream.messages_key, 5)
    assert await _read(redis, stream, stream.group_name, 5) == [i.decode() for i in old]
    for entry_id in old[:3]:
        await redis.xack(stream.messages_key, stream.group_name, entry_id)

    await _trim(stream)

    assert await _ids(redis, stream.messages_key) == [f"{OLD}-4", f"{OLD}-5"]
    pending = await redis.xpending(stream.messages_key, stream.group_name)
    assert pending["pending"] == 2


@pytest.mark.asyncio
async def test_unread_entries_with_no_pending_are_never_trimmed(redis):
    stream = _stream(redis)
    await stream.ensure_group()
    await _old(redis, stream.messages_key, 4)

    await _trim(stream)

    assert len(await _ids(redis, stream.messages_key)) == 4


@pytest.mark.asyncio
async def test_a_lagging_second_group_keeps_what_the_first_group_acknowledged(redis):
    stream = _stream(redis)
    await stream.ensure_group()
    await redis.xgroup_create(stream.messages_key, "lagging", id="0-0")
    old = await _old(redis, stream.messages_key, 3)
    await _read(redis, stream, stream.group_name, 3)
    for entry_id in old:
        await redis.xack(stream.messages_key, stream.group_name, entry_id)

    await _trim(stream)

    assert len(await _ids(redis, stream.messages_key)) == 3


@pytest.mark.asyncio
async def test_a_stream_without_any_group_is_kept(redis):
    stream = _stream(redis)
    await _old(redis, stream.messages_key, 3)

    assert await _trim(stream) == -1
    assert len(await _ids(redis, stream.messages_key)) == 3


@pytest.mark.asyncio
async def test_acknowledged_entries_inside_the_window_stay(redis):
    stream = _stream(redis)
    await stream.ensure_group()
    old = await _old(redis, stream.messages_key, 2)
    young = [await redis.xadd(stream.messages_key, {"json": "{}"}) for _ in range(2)]
    await _read(redis, stream, stream.group_name, 4)
    for entry_id in old + young:
        await redis.xack(stream.messages_key, stream.group_name, entry_id)

    await _trim(stream)

    assert await _ids(redis, stream.messages_key) == [i.decode() for i in young]


@pytest.mark.asyncio
async def test_the_terminal_ack_path_trims_and_keeps_what_is_still_pending(redis):
    """Through the public API: publish, claim two, ack one; the ack's trim removes only the acked old entry."""
    stream = _stream(redis)
    await stream.ensure_group()
    await _old(redis, stream.messages_key, 2)
    first = await stream.claim_next(consumer_name="c1", block_ms=1)
    second = await stream.claim_next(consumer_name="c1", block_ms=1)
    assert (first.stream_id, second.stream_id) == (f"{OLD}-1", f"{OLD}-2")

    await stream.ack(first)

    assert await _ids(redis, stream.messages_key) == [f"{OLD}-2"]


@pytest.mark.asyncio
async def test_publish_creates_the_handler_group_before_the_first_entry(redis):
    stream = _stream(redis)
    await stream.publish(MESSAGE)

    groups = await redis.xinfo_groups(stream.messages_key)
    assert [g["name"].decode() for g in groups] == [stream.group_name]
    assert len(await _ids(redis, stream.messages_key)) == 1


@pytest.mark.asyncio
async def test_publish_never_trims_by_count(redis):
    stream = _stream(redis)
    for _ in range(300):
        await stream.publish(MESSAGE)

    assert await redis.xlen(stream.messages_key) == 300


@pytest.mark.asyncio
async def test_results_and_dlq_keep_their_existing_count_caps(redis):
    """Results and DLQ have no consumer group; their documented count caps are unchanged by W827."""
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusResult

    stream = _stream(redis, result_stream_maxlen=128, dlq_stream_maxlen=128)
    for _ in range(400):
        await stream.write_result(DataBusResult(message_id="m", status="ok", subject="s"))
        await stream.write_dlq(MESSAGE, reason="r")
    assert 128 <= await redis.xlen(stream.results_key) < 400
    assert 128 <= await redis.xlen(stream.dlq_key) < 400


@pytest.mark.asyncio
async def test_one_trim_removes_at_most_its_limit(redis):
    stream = _stream(redis, trim_limit=10)
    await stream.ensure_group()
    old = await _old(redis, stream.messages_key, 50)
    await _read(redis, stream, stream.group_name, 50)
    await redis.xack(stream.messages_key, stream.group_name, *old)

    removed = await _trim(stream)

    assert 0 < removed <= 10
    assert await redis.xlen(stream.messages_key) == 50 - removed

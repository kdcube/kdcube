"""W827 S2d: a large result's data is stored by reference and every polling caller gets it back whole.

Set DATA_BUS_TEST_REDIS_URL to a disposable Redis (each test flushes its database).
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import stream as stream_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import DataBusPublisher
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import (
    RESULT_WAIT_MAX_MS,
    DataBusResultBodyUnavailable,
    DataBusResultWaitTooLong,
    RedisDataBusStream,
    result_body_key,
    result_body_ttl_seconds,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage, DataBusResult

REDIS_URL = os.getenv("DATA_BUS_TEST_REDIS_URL", "")
needs_redis = pytest.mark.skipif(not REDIS_URL, reason="requires a disposable Redis (DATA_BUS_TEST_REDIS_URL)")

LARGE = {"team": [{"worker_name": f"w{n}", "store_reads": ["x" * 400] * 4, "ü": "€"} for n in range(60)]}


@pytest.fixture
async def redis():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def _stream(redis):
    return RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1")


def _publisher(redis):
    return DataBusPublisher(redis=redis, tenant="t", project="p", bundle_id="b@1")


async def _entry(redis, stream):
    (_, fields), = await redis.xrevrange(stream.results_key, count=1)
    return json.loads(fields[b"json"])


def test_the_body_ttl_covers_the_callers_declared_wait_and_is_capped():
    base = stream_module.DATA_BUS_RESULT_BODY_TTL_SECONDS
    assert result_body_ttl_seconds(None) == base
    assert result_body_ttl_seconds(90_000) == base
    assert result_body_ttl_seconds(7_200_000) == 7_200 + stream_module.RESULT_BODY_WAIT_MARGIN_SECONDS
    assert result_body_ttl_seconds(10**12) == stream_module.RESULT_BODY_TTL_CAP_SECONDS
    assert result_body_ttl_seconds("junk") == base


def test_the_body_key_sits_in_its_own_results_streams_scope():
    assert result_body_key("kdcube:data-bus:t:p:b@1:results", "m1:abc") == "kdcube:data-bus:t:p:b@1:result-body:m1:abc"


@needs_redis
@pytest.mark.asyncio
async def test_a_small_result_stays_inline(redis):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="m1", subject="s", data={"ok": True}))
    entry = await _entry(redis, stream)
    assert entry["data"] == {"ok": True} and "data_ref" not in entry


@needs_redis
@pytest.mark.asyncio
async def test_a_large_result_is_stored_by_reference_and_read_back_whole(redis):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="m2", subject="s", data=LARGE), wait_ms=7_200_000)

    entry = await _entry(redis, stream)
    assert entry["data"] == {} and entry["data_ref"]["bytes"] > stream.result_inline_bytes
    assert len(json.dumps(entry)) < 1024
    key = result_body_key(stream.results_key, entry["data_ref"]["key_suffix"])
    assert 7_200 < await redis.ttl(key) <= 7_200 + stream_module.RESULT_BODY_WAIT_MARGIN_SECONDS

    found = await _publisher(redis)._find_result(stream.results_key, "m2", count=10)

    assert found["data"] == LARGE and "data_ref" not in found and found["status"] == "ok"


@needs_redis
@pytest.mark.asyncio
async def test_a_retried_write_reuses_the_body_and_never_shortens_its_lifetime(redis):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="m3", subject="s", data=LARGE))
    [key] = await redis.keys("*result-body:m3:*")
    await redis.expire(key, 10)

    await stream.write_result(DataBusResult(message_id="m3", subject="s", data=LARGE), wait_ms=7_200_000)

    assert await redis.keys("*result-body:m3:*") == [key]
    assert await redis.ttl(key) > 7_200


@needs_redis
@pytest.mark.asyncio
async def test_a_replay_over_different_stored_bytes_never_points_at_them(redis):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="m7", subject="s", data=LARGE))
    [key] = await redis.keys("*result-body:m7:*")
    await redis.set(key, b"tampered")

    await stream.write_result(DataBusResult(message_id="m7", subject="s", data=LARGE))

    assert "data_ref" not in await _entry(redis, stream)


@needs_redis
@pytest.mark.asyncio
async def test_a_wait_longer_than_any_body_lives_is_refused_before_publishing(redis):
    with pytest.raises(DataBusResultWaitTooLong):
        await _publisher(redis).publish_and_wait(subject="s", payload={}, timeout_ms=RESULT_WAIT_MAX_MS + 1)
    assert await redis.keys("*:messages") == []


@needs_redis
@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["expired", "altered"])
async def test_a_missing_or_altered_body_is_outcome_unknown_never_empty(redis, damage):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="m4", subject="s", data=LARGE))
    key = result_body_key(stream.results_key, (await _entry(redis, stream))["data_ref"]["key_suffix"])
    if damage == "expired":
        await redis.delete(key)
    else:
        await redis.set(key, json.dumps({"team": []}))

    with pytest.raises(DataBusResultBodyUnavailable) as raised:
        await _publisher(redis)._find_result(stream.results_key, "m4", count=10)
    assert raised.value.code == "data_bus_result_body_unavailable" and raised.value.outcome_unknown


@needs_redis
@pytest.mark.asyncio
async def test_a_reference_naming_another_message_is_refused(redis):
    stream = _stream(redis)
    await stream.write_result(DataBusResult(message_id="victim", subject="s", data=LARGE))
    victim = await _entry(redis, stream)
    forged = {**victim, "message_id": "attacker"}
    await redis.xadd(stream.results_key, {"json": json.dumps(forged)})

    with pytest.raises(DataBusResultBodyUnavailable):
        await _publisher(redis)._find_result(stream.results_key, "attacker", count=10)


@needs_redis
@pytest.mark.asyncio
async def test_publish_and_wait_declares_its_wait_and_gets_the_whole_large_result(redis):
    """End to end: the caller's timeout reaches the worker as trace.result_wait_ms; the body outlives it."""
    publisher = _publisher(redis)
    stream = _stream(redis)

    async def worker():
        claim = None
        while claim is None:
            claim = await stream.claim_next(consumer_name="w", block_ms=50)
        message = claim.message
        assert message.trace["result_wait_ms"] == 5_400_000
        await stream.write_result(DataBusResult.ok(message, LARGE), stream_id=claim.stream_id,
                                  wait_ms=message.trace.get("result_wait_ms"))
        await stream.ack(claim)

    task = asyncio.create_task(worker())
    result = await publisher.publish_and_wait(subject="s", payload={}, timeout_ms=5_400_000,
                                              poll_interval_ms=25, message_id="m5")
    await task

    assert result["data"] == LARGE
    key = result_body_key(stream.results_key, f"m5:{(await _entry(redis, stream))['data_ref']['sha256'][:16]}")
    assert await redis.ttl(key) > 5_400


class _SetRefused:
    def __init__(self):
        self.added = []

    async def set(self, *args, **kwargs):
        raise ConnectionError("refused")

    async def xadd(self, key, fields, **kwargs):
        self.added.append(json.loads(fields["json"]))
        return "1-1"


@pytest.mark.asyncio
async def test_a_failed_body_write_keeps_the_result_inline():
    redis = _SetRefused()
    await RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1").write_result(
        DataBusResult(message_id="m6", subject="s", data=LARGE))
    assert redis.added[0]["data"] == LARGE and "data_ref" not in redis.added[0]

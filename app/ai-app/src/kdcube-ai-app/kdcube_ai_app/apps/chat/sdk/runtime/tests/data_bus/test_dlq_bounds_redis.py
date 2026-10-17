"""W888: a Data Bus DLQ entry is bounded in size and age, on a real disposable Redis.

The operator's policy decision (2026-10-16, option c): each entry keeps its envelope and at most 16 KB of the
failed body, with the body's size and SHA-256, and entries older than seven days are removed; the 10,000-entry
count cap stays. The trim is exact, so it holds with Redis's default 100-entry stream nodes, not only with the
node size of 1 other retention tests use. Set DATA_BUS_TEST_REDIS_URL to a disposable Redis (never a running KDCube Redis: each test flushes
its own database); run it on Redis 7.4 and 8.x. Old entries get explicit stream IDs, so no test sleeps.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import stream as stream_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import (
    DATA_BUS_DLQ_INLINE_BYTES, DATA_BUS_DLQ_RETENTION_SECONDS, DLQ_BODY_SCHEMA, RedisDataBusStream, _bounded_body,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage

REDIS_URL = os.getenv("DATA_BUS_TEST_REDIS_URL", "")
redis_only = pytest.mark.skipif(not REDIS_URL, reason="requires a disposable Redis (DATA_BUS_TEST_REDIS_URL)")
DAY_MS = 86_400_000


def test_defaults_are_the_operator_decision_and_not_environment_knobs():
    import inspect

    assert DATA_BUS_DLQ_INLINE_BYTES == 16384
    assert DATA_BUS_DLQ_RETENTION_SECONDS == 7 * 86400
    # A fixed policy: no environment variable reads them (configuration is descriptor-owned).
    source = inspect.getsource(stream_module)
    assert 'getenv("DATA_BUS_DLQ_INLINE_BYTES"' not in source
    assert 'getenv("DATA_BUS_DLQ_RETENTION_SECONDS"' not in source


def test_a_body_within_the_bound_is_kept_as_it_is():
    body = {"operations": [{"op": "set", "value": "x" * 100}]}
    assert _bounded_body(body, 16384) is body
    assert _bounded_body("short", 16384) == "short"


def test_a_large_body_keeps_its_size_digest_and_a_valid_utf8_head():
    body = {"text": "é" * 20000}  # two bytes per character: the cut lands inside one
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    bounded = _bounded_body(body, 16384)
    assert bounded["schema"] == DLQ_BODY_SCHEMA and bounded["truncated"] is True
    assert bounded["bytes"] == len(raw)
    assert bounded["sha256"] == hashlib.sha256(raw).hexdigest()
    head = bounded["head"].encode("utf-8")
    assert len(head) <= 16384 and raw.startswith(head) and len(head) >= 16383


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


def _message(payload):
    return DataBusMessage(message_id="m-1", tenant="t", project="p", bundle_id="b@1", subject="worker.heartbeat",
                          object_ref="work:worker:self", idempotency_key="k-1",
                          actor={"principal_key": "card:one"}, payload=payload)


async def _entries(redis, key):
    return [json.loads(fields[b"json"]) for _, fields in await redis.xrange(key)]


@redis_only
@pytest.mark.asyncio
async def test_a_large_failed_message_keeps_its_envelope_and_a_bounded_body(redis):
    stream = _stream(redis)
    payload = {"operation": "worker.heartbeat", "payload": {"blob": "x" * 600_000}}
    await stream.write_dlq(_message(payload), reason="handler_error", details={"error": "e" * 50_000})
    [entry] = await _entries(redis, stream.dlq_key)
    message = entry["message"]
    # The envelope: who, what, which object and why, whole.
    assert (message["message_id"], message["subject"], message["object_ref"], message["idempotency_key"]) == (
        "m-1", "worker.heartbeat", "work:worker:self", "k-1")
    assert message["actor"] == {"principal_key": "card:one"} and entry["reason"] == "handler_error"
    body = message["payload"]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert body["truncated"] is True and body["bytes"] == len(raw)
    assert body["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["details"]["truncated"] is True
    stored = await redis.xrange(stream.dlq_key)
    assert len(stored[0][1][b"json"]) < 2 * 16384 + 2048


@redis_only
@pytest.mark.asyncio
async def test_a_small_failed_message_is_kept_exactly(redis):
    stream = _stream(redis)
    payload = {"operation": "worker.estimate", "payload": {"note": "small"}}
    await stream.write_dlq(_message(payload), reason="handler_error", details={"retry_count": 3})
    [entry] = await _entries(redis, stream.dlq_key)
    assert entry["message"]["payload"] == payload and entry["details"] == {"retry_count": 3}


@redis_only
@pytest.mark.asyncio
async def test_an_unreadable_record_is_bounded_per_field(redis):
    stream = _stream(redis)
    await stream.write_dlq_raw(stream_id="1-1", fields={b"json": b"{" + b"y" * 40_000, b"note": b"ok"},
                               reason="invalid_payload")
    [entry] = await _entries(redis, stream.dlq_key)
    assert entry["fields"]["note"] == "ok"
    assert entry["fields"]["json"]["truncated"] is True and entry["fields"]["json"]["bytes"] == 40_001


@redis_only
@pytest.mark.asyncio
async def test_entries_older_than_seven_days_go_and_newer_stay(redis):
    stream = _stream(redis)
    seconds, micros = await redis.time()
    now_ms = int(seconds) * 1000 + int(micros) // 1000
    old = [await redis.xadd(stream.dlq_key, {"json": "{}"}, id=f"{now_ms - days * DAY_MS}-0") for days in (38, 9, 8)]
    kept = [await redis.xadd(stream.dlq_key, {"json": "{}"}, id=f"{now_ms - days * DAY_MS}-0") for days in (6, 1)]
    assert len(old) == 3
    await stream.write_dlq(_message({}), reason="r")
    ids = [entry_id.decode() for entry_id, _ in await redis.xrange(stream.dlq_key)]
    assert ids[:2] == [k.decode() for k in kept] and len(ids) == 3


@redis_only
@pytest.mark.asyncio
@pytest.mark.parametrize("node_entries", [100, 1])
async def test_with_default_stream_nodes_an_old_entry_beside_a_fresh_one_still_goes(redis, node_entries):
    """Redis's default node holds 100 entries: an approximate trim would keep this whole small stream (W888 review)."""
    await redis.config_set("stream-node-max-entries", node_entries)
    stream = _stream(redis)
    seconds, micros = await redis.time()
    now_ms = int(seconds) * 1000 + int(micros) // 1000
    old = await redis.xadd(stream.dlq_key, {"json": "{}"}, id=f"{now_ms - 8 * DAY_MS}-0")
    fresh = await redis.xadd(stream.dlq_key, {"json": "{}"}, id=f"{now_ms - DAY_MS}-0")
    await stream.write_dlq(_message({}), reason="r")
    ids = [entry_id for entry_id, _ in await redis.xrange(stream.dlq_key)]
    assert old not in ids and ids[0] == fresh and len(ids) == 2


@redis_only
@pytest.mark.asyncio
async def test_one_dlq_trim_removes_every_entry_past_the_window_and_the_count_cap_stays(redis):
    await redis.config_set("stream-node-max-entries", 100)
    stream = _stream(redis, dlq_stream_maxlen=128)
    for n in range(1, 251):
        await redis.xadd(stream.dlq_key, {"json": "{}"}, id=f"1000000-{n}")
    assert await stream.trim_dlq(force=True) == 250
    assert await redis.xlen(stream.dlq_key) == 0
    for _ in range(400):
        await stream.write_dlq(_message({}), reason="r")
    assert 128 <= await redis.xlen(stream.dlq_key) < 400


@pytest.mark.asyncio
async def test_a_refused_dlq_trim_never_fails_the_dlq_write():
    class Refusing:
        def __init__(self):
            self.added = []

        async def xadd(self, key, fields, **kwargs):
            self.added.append(key)
            return b"1-1"

        async def time(self):
            raise ConnectionError("synthetic")

    redis = Refusing()
    stream = _stream(redis)
    assert await stream.write_dlq(_message({}), reason="r") == "1-1"
    assert await stream.trim_dlq(force=True) is None
    assert redis.added == [stream.dlq_key]

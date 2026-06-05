from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.locks import RedisDataBusPartitionLocker
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.streams = {}
        self.groups = set()
        self.acks = []
        self._next_id = 1

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        return key in self.values

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0

    async def xadd(self, key, fields, maxlen=None, approximate=True):
        stream_id = f"1-{self._next_id}"
        self._next_id += 1
        self.streams.setdefault(key, []).append((stream_id, dict(fields)))
        return stream_id

    async def xgroup_create(self, key, group, id="0-0", mkstream=True):
        marker = (key, group)
        if marker in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(marker)
        self.streams.setdefault(key, [])

    async def xreadgroup(self, group, consumer, streams, count=1, block=0):
        key = next(iter(streams.keys()))
        items = self.streams.get(key, [])[:count]
        self.streams[key] = self.streams.get(key, [])[count:]
        if not items:
            return []
        return [(key, items)]

    async def xack(self, key, group, stream_id):
        self.acks.append((key, group, stream_id))
        return 1

    async def xautoclaim(self, *args, **kwargs):
        return ("0-0", [])


@pytest.mark.asyncio
async def test_data_bus_stream_publishes_json_and_claims_message():
    redis = FakeRedis()
    stream = RedisDataBusStream(redis, tenant="t", project="p", bundle_id="bundle@1")
    message = DataBusMessage(
        message_id="m1",
        tenant="t",
        project="p",
        bundle_id="bundle@1",
        subject="task.patch",
        payload={"x": 1},
    )

    published = await stream.publish(message)
    claim = await stream.claim_next(consumer_name="worker-1")

    assert published.stream_key.endswith(":messages")
    assert claim is not None
    assert claim.message.message_id == "m1"
    assert claim.message.payload == {"x": 1}
    await stream.ack(claim)
    assert redis.acks == [(stream.messages_key, stream.group_name, claim.stream_id)]


@pytest.mark.asyncio
async def test_partition_lock_uses_token_for_release():
    redis = FakeRedis()
    locker = RedisDataBusPartitionLocker(redis, ttl_seconds=30)

    lock = await locker.acquire("tenant:project:bundle:subject:object")
    assert lock is not None
    assert await locker.acquire("tenant:project:bundle:subject:object") is None

    assert await locker.release(lock) is True
    assert await locker.acquire("tenant:project:bundle:subject:object") is not None


@pytest.mark.asyncio
async def test_partition_lock_refuses_stale_token_release():
    redis = FakeRedis()
    locker = RedisDataBusPartitionLocker(redis, ttl_seconds=30)

    lock = await locker.acquire("partition")
    assert lock is not None
    redis.values[lock.key] = "different-token"

    assert await locker.release(lock) is False
    assert redis.values[lock.key] == "different-token"

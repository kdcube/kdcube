import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor


class _FakePool:
    def __init__(self):
        self.disconnect_calls = []

    async def disconnect(self, inuse_connections=True):
        self.disconnect_calls.append(inuse_connections)


class _HangingRedis:
    def __init__(self):
        self.connection_pool = _FakePool()

    async def brpop(self, queue_key, timeout):
        await asyncio.sleep(60)


async def _noop_handler(_payload):
    return {}


def _build_processor(redis_client):
    middleware = SimpleNamespace(
        redis_url="redis://example",
        instance_id="proc-test",
        QUEUE_PREFIX="queue",
        LOCK_PREFIX="lock",
        redis=None,
    )
    processor = EnhancedChatRequestProcessor(
        middleware,
        _noop_handler,
        conversation_ctx=SimpleNamespace(),
        redis=redis_client,
    )
    processor.queue_block_timeout_sec = 0.01
    processor.queue_call_timeout_sec = 0.02
    return processor


@pytest.mark.asyncio
async def test_queue_brpop_timeout_disconnects_shared_pool():
    hanging = _HangingRedis()
    processor = _build_processor(hanging)

    result = await processor._queue_brpop("queue:anonymous")

    assert result is None
    assert hanging.connection_pool.disconnect_calls == [True]
    assert "Queue BRPOP exceeded" in (processor.get_runtime_metadata()["last_queue_error"] or "")

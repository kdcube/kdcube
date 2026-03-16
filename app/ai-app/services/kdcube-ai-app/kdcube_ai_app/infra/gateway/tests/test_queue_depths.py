from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.sessions import UserType
from kdcube_ai_app.infra.gateway.backpressure import AtomicBackpressureManager, AtomicChatQueueManager


class _FakeRedis:
    def __init__(self, sizes):
        self.sizes = dict(sizes)

    async def llen(self, key):
        return self.sizes.get(key, 0)

    async def get(self, key):
        return self.sizes.get(key, 0)


class _EvalRedis:
    def __init__(self):
        self.last_eval = None

    async def eval(self, script, numkeys, *args):
        self.last_eval = (script, numkeys, args)
        return [1, "admitted", 3, 12, 2]


def _gateway_config():
    return SimpleNamespace(
        tenant_id="tenant-a",
        project_id="project-a",
        backpressure_config_obj=SimpleNamespace(),
        monitoring=SimpleNamespace(queue_analytics_enabled=False, heartbeat_timeout_seconds=30),
        redis=SimpleNamespace(analytics_ttl=60),
        profile=SimpleNamespace(value="test"),
        instance_id="instance-1",
        total_capacity_per_instance=8,
        backpressure=SimpleNamespace(
            anonymous_pressure_threshold=0.6,
            registered_pressure_threshold=0.9,
            paid_pressure_threshold=0.9,
            hard_limit_threshold=0.98,
            capacity_buffer=0.1,
            queue_depth_multiplier=3.0,
        ),
        capacity_source_selector=lambda: ("chat", "proc"),
        limits=SimpleNamespace(max_queue_size=100),
        get_thresholds_for_actual_capacity=lambda actual: {
            "anonymous_threshold": int(actual * 0.6),
            "registered_threshold": int(actual * 0.9),
            "paid_threshold": int(actual * 0.9),
            "hard_limit": int(actual * 0.98),
        },
    )


@pytest.mark.asyncio
async def test_atomic_backpressure_queue_sizes_include_inflight_lists():
    manager = AtomicBackpressureManager("redis://example", _gateway_config(), monitor=None)
    manager.redis = _FakeRedis(
        {
            "tenant-a:project-a:kdcube:chat:prompt:queue:anonymous": 2,
            "tenant-a:project-a:kdcube:chat:prompt:queue:registered": 3,
            "tenant-a:project-a:kdcube:chat:prompt:queue:privileged": 1,
            "tenant-a:project-a:kdcube:chat:prompt:queue:paid": 4,
            "tenant-a:project-a:kdcube:chat:prompt:queue:inflight:anonymous": 5,
            "tenant-a:project-a:kdcube:chat:prompt:queue:inflight:registered": 6,
            "tenant-a:project-a:kdcube:chat:prompt:queue:inflight:privileged": 7,
            "tenant-a:project-a:kdcube:chat:prompt:queue:inflight:paid": 8,
        }
    )

    sizes = await manager.get_individual_queue_sizes()

    assert sizes == {
        "anonymous": 7,
        "registered": 9,
        "privileged": 8,
        "paid": 12,
    }


@pytest.mark.asyncio
async def test_atomic_chat_queue_manager_passes_continuation_counter_keys():
    manager = AtomicChatQueueManager("redis://example", _gateway_config(), monitor=None)
    manager.redis = _EvalRedis()

    success, reason, stats = await manager.enqueue_chat_task_atomic(
        UserType.PRIVILEGED,
        {"task_id": "task-1"},
        session=None,
        context=None,
        endpoint="/api/chat",
    )

    assert success is True
    assert reason == "admitted"
    assert stats["task_id"] == "task-1"

    _, numkeys, args = manager.redis.last_eval
    keys = args[:numkeys]
    assert "tenant-a:project-a:kdcube:chat:conversation:mailbox:count:anonymous" in keys
    assert "tenant-a:project-a:kdcube:chat:conversation:mailbox:count:registered" in keys
    assert "tenant-a:project-a:kdcube:chat:conversation:mailbox:count:privileged" in keys
    assert "tenant-a:project-a:kdcube:chat:conversation:mailbox:count:paid" in keys

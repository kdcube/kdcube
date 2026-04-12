import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.processor as processor_mod
from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_comm,
    get_current_request_context,
)


class _FakePool:
    def __init__(self):
        self.disconnect_calls = []

    async def disconnect(self, inuse_connections=True):
        self.disconnect_calls.append(inuse_connections)


class _HangingRedis:
    def __init__(self):
        self.connection_pool = _FakePool()

    async def brpoplpush(self, source, destination, timeout):
        del source, destination, timeout
        await asyncio.sleep(60)


async def _noop_handler(_payload):
    return {}


class _MinimalRedis:
    def __init__(self):
        self.connection_pool = _FakePool()
        self.expire_calls = []
        self.lpush_calls = []
        self.rpush_calls = []
        self.delete_calls = []
        self.lrem_calls = []
        self.set_calls = []
        self.lists = {}
        self.lock_ttls = {}
        self.values = {}

    def seed_list(self, key, values):
        self.lists[key] = list(values)

    async def brpoplpush(self, source, destination, timeout):
        del timeout
        items = self.lists.get(source) or []
        if not items:
            return None
        value = items.pop()
        self.lists[source] = items
        self.lists.setdefault(destination, []).insert(0, value)
        return value

    async def ttl(self, key):
        return self.lock_ttls.get(key, -2)

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        if key in self.lock_ttls:
            self.lock_ttls[key] = ttl
        return True

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.lock_ttls and self.lock_ttls[key] >= 0:
            return False
        self.lock_ttls[key] = ex if ex is not None else 300
        return True

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            self.delete_calls.append(key)
            if key in self.lock_ttls:
                deleted += 1
                del self.lock_ttls[key]
            elif key in self.lists:
                deleted += 1
                del self.lists[key]
            elif key in self.values:
                deleted += 1
                del self.values[key]
        return deleted

    async def lpush(self, key, value):
        self.lpush_calls.append((key, value))
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def rpush(self, key, value):
        self.rpush_calls.append((key, value))
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lrem(self, key, count, value):
        self.lrem_calls.append((key, count, value))
        items = list(self.lists.get(key) or [])
        removed = 0
        new_items = []
        for item in items:
            if item == value and (count <= 0 or removed < count):
                removed += 1
                continue
            new_items.append(item)
        self.lists[key] = new_items
        return removed

    async def lrange(self, key, start, end):
        items = list(self.lists.get(key) or [])
        if end == -1:
            return items[start:]
        return items[start:end + 1]

    async def llen(self, key):
        return len(self.lists.get(key) or [])

    async def rpop(self, key):
        items = list(self.lists.get(key) or [])
        if not items:
            return None
        value = items.pop()
        self.lists[key] = items
        return value

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def decr(self, key):
        value = int(self.values.get(key, 0)) - 1
        self.values[key] = value
        return value

    async def get(self, key):
        return self.values.get(key)


class _NoopConversationCtx:
    def __init__(self):
        self.calls = []

    async def set_conversation_state(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "updated_at": "2026-03-16T00:00:00Z",
            "current_turn_id": kwargs.get("last_turn_id"),
        }


class _NoopRelay:
    def __init__(self):
        self.conv_status_calls = []

    async def emit_conv_status(self, *args, **kwargs):
        self.conv_status_calls.append({"args": args, "kwargs": kwargs})


class _DummyChatCommunicator:
    error_calls = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def start(self, **kwargs):
        del kwargs

    async def step(self, **kwargs):
        del kwargs

    async def complete(self, **kwargs):
        del kwargs

    async def error(self, **kwargs):
        type(self).error_calls.append(kwargs)


class _DummyEnvelope:
    @staticmethod
    def from_dict(data):
        return data


class _DummyHostDrainDetector:
    def __init__(self, *, enabled=True, draining=False):
        self.enabled = enabled
        self.draining = draining
        self.check_calls = 0

    async def is_host_draining(self):
        self.check_calls += 1
        return self.draining

    def snapshot(self):
        return {
            "enabled": self.enabled,
            "container_instance_status": "DRAINING" if self.draining else "ACTIVE",
        }


@asynccontextmanager
async def _noop_async_context(*args, **kwargs):
    del args, kwargs
    yield


def _build_task_payload(task_id="task-1", *, user_type="registered"):
    return {
        "meta": {"task_id": task_id, "created_at": 1.0, "instance_id": "ingress-1"},
        "routing": {
            "bundle_id": "bundle.demo",
            "session_id": "session-1",
            "conversation_id": "conv-1",
            "turn_id": "turn-1",
            "socket_id": "socket-1",
        },
        "actor": {"tenant_id": "tenant-a", "project_id": "project-a"},
        "user": {"user_type": user_type, "user_id": "user-1", "fingerprint": "fp-1"},
        "request": {"message": "hello", "operation": "chat", "payload": None},
        "config": {"values": {}},
        "accounting": {"envelope": {}},
    }


def _build_processor(redis_client, *, max_concurrent=5, handler=_noop_handler, conversation_ctx=None, relay=None):
    middleware = SimpleNamespace(
        redis_url="redis://example",
        instance_id="proc-test",
        QUEUE_PREFIX="queue",
        LOCK_PREFIX="lock",
        redis=None,
    )
    processor = EnhancedChatRequestProcessor(
        middleware,
        handler,
        conversation_ctx=conversation_ctx or _NoopConversationCtx(),
        relay=relay or _NoopRelay(),
        redis=redis_client,
        max_concurrent=max_concurrent,
        host_drain_detector=_DummyHostDrainDetector(enabled=False),
    )
    processor.queue_block_timeout_sec = 0.01
    processor.queue_call_timeout_sec = 0.02
    processor.inflight_reaper_interval_sec = 0.01
    return processor


@pytest.fixture
def _patch_processor_dependencies(monkeypatch):
    _DummyChatCommunicator.error_calls = []
    monkeypatch.setattr(processor_mod, "get_settings", lambda: SimpleNamespace(STORAGE_PATH="/tmp"))
    monkeypatch.setattr(processor_mod, "create_storage_backend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(processor_mod, "record_metric", _noop_handler)
    monkeypatch.setattr(processor_mod, "ChatCommunicator", _DummyChatCommunicator)

    import kdcube_ai_app.infra.accounting.envelope as envelope_mod
    import kdcube_ai_app.infra.accounting as accounting_mod

    monkeypatch.setattr(envelope_mod, "AccountingEnvelope", _DummyEnvelope)
    monkeypatch.setattr(envelope_mod, "bind_accounting", _noop_async_context)
    monkeypatch.setattr(accounting_mod, "with_accounting", _noop_async_context)
    return _DummyChatCommunicator


@pytest.mark.asyncio
async def test_queue_claim_timeout_disconnects_shared_pool():
    hanging = _HangingRedis()
    processor = _build_processor(hanging)

    result = await processor._queue_claim("queue:anonymous", "queue:inflight:anonymous")

    assert result is None
    assert hanging.connection_pool.disconnect_calls == [True]
    assert "Queue claim exceeded" in (processor.get_runtime_metadata()["last_queue_error"] or "")


@pytest.mark.asyncio
async def test_stop_processing_waits_for_inflight_task_without_cancelling():
    redis = _MinimalRedis()
    processor = _build_processor(redis, max_concurrent=1)
    task_started = asyncio.Event()
    task_finish = asyncio.Event()
    task_cancelled = False

    async def _processing_loop():
        while not processor._stop_event.is_set():
            await asyncio.sleep(0.01)

    async def _config_loop():
        while not processor._stop_event.is_set():
            await asyncio.sleep(0.01)

    async def _reaper_loop():
        while not processor._stop_event.is_set():
            await asyncio.sleep(0.01)

    async def _inflight_task():
        nonlocal task_cancelled
        task_started.set()
        try:
            await task_finish.wait()
        except asyncio.CancelledError:
            task_cancelled = True
            raise
        finally:
            processor._current_load = 0

    processor._current_load = 1
    inflight = asyncio.create_task(_inflight_task(), name="chat-task:drain-test")
    processor._active_tasks.add(inflight)
    processor._active_task_details[inflight] = {"task_id": "drain-test", "queue_key": "queue:registered"}
    inflight.add_done_callback(lambda t: (processor._active_tasks.discard(t), processor._active_task_details.pop(t, None)))
    processor._processor_task = asyncio.create_task(_processing_loop(), name="chat-processing-loop")
    processor._config_task = asyncio.create_task(_config_loop(), name="config-bundles-listener")
    processor._reaper_task = asyncio.create_task(_reaper_loop(), name="chat-inflight-recovery-loop")

    await task_started.wait()
    stop_task = asyncio.create_task(processor.stop_processing())

    await asyncio.sleep(0.05)
    assert stop_task.done() is False
    assert task_cancelled is False
    assert processor.get_runtime_metadata()["draining"] is True

    task_finish.set()
    await stop_task

    assert task_cancelled is False
    assert processor.get_current_load() == 0
    assert processor.get_runtime_metadata()["active_tasks"] == 0


@pytest.mark.asyncio
async def test_prefetch_git_bundles_skips_existing_paths(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle-ready"
    bundle_root.mkdir()
    ensure_calls = []

    monkeypatch.setattr(
        processor_mod,
        "_get_bundle_registry",
        lambda: {
            "bundle.ready": {
                "repo": "https://example.invalid/repo.git",
                "path": str(bundle_root),
            }
        },
    )

    async def _ensure_git_bundle_async(**kwargs):
        ensure_calls.append(kwargs)

    monkeypatch.setattr(processor_mod, "ensure_git_bundle_async", _ensure_git_bundle_async)

    errors = await processor_mod.prefetch_git_bundles()

    assert errors == {}
    assert ensure_calls == []


@pytest.mark.asyncio
async def test_prefetch_git_bundles_resolves_missing_paths_and_collects_errors(monkeypatch):
    ensure_calls = []

    monkeypatch.setattr(
        processor_mod,
        "_get_bundle_registry",
        lambda: {
            "bundle.ok": {
                "repo": "https://example.invalid/ok.git",
                "ref": "main",
                "subdir": "bundle",
            },
            "bundle.cooldown": {
                "repo": "https://example.invalid/cooldown.git",
            },
            "bundle.fail": {
                "repo": "https://example.invalid/fail.git",
            },
        },
    )

    monkeypatch.setattr(
        processor_mod,
        "compute_git_bundle_paths",
        lambda **kwargs: SimpleNamespace(bundle_root=Path(f"/tmp/{kwargs['bundle_id']}")),
    )

    async def _ensure_git_bundle_async(**kwargs):
        ensure_calls.append(kwargs)
        if kwargs["bundle_id"] == "bundle.cooldown":
            raise processor_mod.GitBundleCooldown("cooldown")
        if kwargs["bundle_id"] == "bundle.fail":
            raise RuntimeError("boom")

    monkeypatch.setattr(processor_mod, "ensure_git_bundle_async", _ensure_git_bundle_async)

    old_atomic = os.environ.get("BUNDLE_GIT_ATOMIC")
    try:
        os.environ["BUNDLE_GIT_ATOMIC"] = "1"
        errors = await processor_mod.prefetch_git_bundles()
    finally:
        if old_atomic is None:
            os.environ.pop("BUNDLE_GIT_ATOMIC", None)
        else:
            os.environ["BUNDLE_GIT_ATOMIC"] = old_atomic

    assert errors == {
        "bundle.cooldown": "cooldown",
        "bundle.fail": "boom",
    }
    assert [call["bundle_id"] for call in ensure_calls] == [
        "bundle.ok",
        "bundle.cooldown",
        "bundle.fail",
    ]


@pytest.mark.asyncio
async def test_pop_any_queue_fair_requeues_item_if_drain_starts_after_claim():
    redis = _MinimalRedis()
    processor = _build_processor(redis)
    payload = {"meta": {"task_id": "task-1"}}
    raw_payload = json.dumps(payload).encode("utf-8")

    async def _queue_claim(_ready_key, inflight_key):
        redis.seed_list(inflight_key, [raw_payload])
        processor._stop_event.set()
        return raw_payload

    processor._queue_claim = _queue_claim

    result = await processor._pop_any_queue_fair()

    assert result is None
    assert redis.lrem_calls == [("queue:inflight:privileged", 1, raw_payload)]
    assert redis.rpush_calls == [("queue:privileged", raw_payload)]
    assert processor.get_current_load() == 0


@pytest.mark.asyncio
async def test_pop_any_queue_fair_skips_queue_claims_when_host_is_draining():
    redis = _MinimalRedis()
    processor = _build_processor(redis)
    processor._host_draining = True

    async def _unexpected_queue_claim(*args, **kwargs):
        raise AssertionError("queue claim should not run while host is draining")

    processor._queue_claim = _unexpected_queue_claim

    result = await processor._pop_any_queue_fair()

    assert result is None
    metadata = processor.get_runtime_metadata()
    assert metadata["host_draining"] is True
    assert metadata["accepting_new_tasks"] is False


@pytest.mark.asyncio
async def test_lock_renewer_keeps_extending_lock_during_drain():
    redis = _MinimalRedis()
    redis.lock_ttls["lock:test"] = 300
    processor = _build_processor(redis)
    processor.lock_renew_sec = 0.01

    async with processor._lock_renewer("lock:test"):
        processor._stop_event.set()
        await asyncio.sleep(0.03)

    assert redis.expire_calls


@pytest.mark.asyncio
async def test_lock_renewer_uses_longer_started_marker_ttl_for_extra_keys():
    redis = _MinimalRedis()
    redis.lock_ttls["lock:test"] = 300
    redis.lock_ttls["lock:started:test"] = 960
    processor = _build_processor(redis)
    processor.lock_renew_sec = 0.01
    processor.started_marker_ttl_sec = 960

    async with processor._lock_renewer(
            "lock:test",
            extra_keys=["lock:started:test"],
            extra_ttl_sec=processor.started_marker_ttl_sec,
    ):
        await asyncio.sleep(0.03)

    assert ("lock:test", 300) in redis.expire_calls
    assert ("lock:started:test", 960) in redis.expire_calls


@pytest.mark.asyncio
async def test_requeue_stale_inflight_task_returns_item_to_ready_queue():
    redis = _MinimalRedis()
    processor = _build_processor(redis)
    raw_payload = json.dumps(_build_task_payload("stale-task")).encode("utf-8")
    ready_key = "queue:privileged"
    inflight_key = "queue:inflight:privileged"
    redis.seed_list(inflight_key, [raw_payload])

    reclaimed = await processor._requeue_stale_inflight_tasks()

    assert reclaimed == 1
    assert redis.lists[inflight_key] == []
    assert redis.lists[ready_key] == [raw_payload]


@pytest.mark.asyncio
async def test_started_stale_inflight_task_is_marked_interrupted_and_signalled(_patch_processor_dependencies):
    redis = _MinimalRedis()
    conversation_ctx = _NoopConversationCtx()
    relay = _NoopRelay()
    processor = _build_processor(redis, conversation_ctx=conversation_ctx, relay=relay)
    raw_payload = json.dumps(_build_task_payload("started-task")).encode("utf-8")
    inflight_key = "queue:inflight:privileged"
    started_key = "lock:started:started-task"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[started_key] = 300

    reclaimed = await processor._requeue_stale_inflight_tasks()

    assert reclaimed == 0
    assert redis.lists[inflight_key] == []
    assert redis.lists.get("queue:privileged", []) == []
    assert started_key in redis.delete_calls
    assert processor.get_runtime_metadata()["stale_interrupted_count"] == 1
    assert conversation_ctx.calls[-1]["new_state"] == "error"
    assert relay.conv_status_calls[-1]["kwargs"]["completion"] == "interrupted"
    assert _patch_processor_dependencies.error_calls[-1]["data"]["error_type"] == "turn_interrupted"


@pytest.mark.asyncio
async def test_process_task_cancellation_keeps_started_inflight_claim(_patch_processor_dependencies):
    redis = _MinimalRedis()
    handler_started = asyncio.Event()

    async def _blocking_handler(_payload):
        handler_started.set()
        await asyncio.Event().wait()

    processor = _build_processor(redis, handler=_blocking_handler)
    task_payload = _build_task_payload("cancel-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    lock_key = "lock:cancel-task"
    started_key = "lock:started:cancel-task"
    inflight_key = "queue:inflight:registered"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = dict(task_payload)
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    task = asyncio.create_task(processor._process_task(task_data))
    await handler_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert redis.lists[inflight_key] == [raw_payload]
    assert lock_key in redis.lock_ttls
    assert started_key in redis.lock_ttls
    assert redis.lock_ttls[started_key] == processor.started_marker_ttl_sec
    assert redis.lock_ttls[started_key] > redis.lock_ttls[lock_key]
    assert lock_key not in redis.delete_calls
    assert started_key not in redis.delete_calls
    assert processor.get_current_load() == 0


@pytest.mark.asyncio
async def test_process_task_promotes_next_continuation_to_ready_queue(_patch_processor_dependencies):
    redis = _MinimalRedis()
    conversation_ctx = _NoopConversationCtx()
    relay = _NoopRelay()
    processor = _build_processor(redis, conversation_ctx=conversation_ctx, relay=relay)

    current_payload = _build_task_payload("task-current", user_type="registered")
    next_payload = _build_task_payload("task-next", user_type="registered")
    next_payload["routing"]["turn_id"] = "turn-next"
    next_payload["request"]["message"] = "follow up"
    next_payload["continuation"] = {"kind": "followup", "explicit": False, "active_turn_id": "turn-1"}

    mailbox_key = "tenant-a:project-a:kdcube:chat:conversation:mailbox:conv-1"
    redis.seed_list(
        mailbox_key,
        [
            json.dumps(
                {
                    "message_id": "cont-1",
                    "kind": "followup",
                    "created_at": 1.0,
                    "sequence": 1,
                    "payload": next_payload,
                }
            )
        ],
    )

    raw_payload = json.dumps(current_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:task-current"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = dict(current_payload)
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    await processor._process_task(task_data)

    assert redis.lists[inflight_key] == []
    promoted = redis.lists["queue:registered"][0]
    promoted_payload = json.loads(promoted)
    assert promoted_payload["meta"]["task_id"] == "task-next"
    assert conversation_ctx.calls[-1]["new_state"] == "in_progress"
    assert conversation_ctx.calls[-1]["last_turn_id"] == "turn-next"
    assert relay.conv_status_calls[-1]["kwargs"]["completion"] == "queued_next"


@pytest.mark.asyncio
async def test_process_task_binds_runtime_request_context(_patch_processor_dependencies):
    redis = _MinimalRedis()
    captured = {}

    async def _handler(_payload):
        current = get_current_request_context()
        current_comm = get_current_comm()
        captured["before"] = (
            getattr(getattr(current, "user", None), "user_id", None),
            getattr(getattr(current, "routing", None), "socket_id", None),
            current_comm is not None,
        )
        await asyncio.sleep(0)
        current = get_current_request_context()
        current_comm = get_current_comm()
        captured["after"] = (
            getattr(getattr(current, "user", None), "user_id", None),
            getattr(getattr(current, "routing", None), "socket_id", None),
            current_comm is not None,
        )
        return {}

    processor = _build_processor(redis, handler=_handler)
    task_payload = _build_task_payload("ctx-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:ctx-task"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = dict(task_payload)
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    await processor._process_task(task_data)

    assert captured["before"] == ("user-1", "socket-1", True)
    assert captured["after"] == ("user-1", "socket-1", True)

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.processor as processor_mod
from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
from kdcube_ai_app.apps.chat.processor_scheduler_backend import (
    SCHEDULER_BACKEND_CONVERSATION_STREAMS,
    SCHEDULER_BACKEND_LEGACY_LISTS,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus import build_event_lane_wakeup
from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import ConversationEventBusOrchestrator
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_call_context_patch,
    bind_current_request_context,
    get_current_bundle_call_context,
    get_current_comm,
    get_current_request_context,
    restore_ctxvars as restore_comm_ctxvars,
    set_current_bundle_call_context,
    set_current_request_context,
    snapshot_ctxvars as snapshot_comm_ctxvars,
    touch_current_task_activity,
    update_current_bundle_call_context,
)
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.infra.jobs.stream import BackgroundJob, BackgroundJobClaim
from kdcube_ai_app.infra.plugin import bundle_store


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


class _TimeoutPubSub:
    def __init__(self, stop_event: asyncio.Event):
        self.stop_event = stop_event
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        self.closed = False

    async def subscribe(self, *channels):
        self.subscribe_calls.append(channels)

    async def get_message(self, **_kwargs):
        self.stop_event.set()
        raise asyncio.TimeoutError

    async def unsubscribe(self, *channels):
        self.unsubscribe_calls.append(channels)

    async def close(self):
        self.closed = True


class _RedisWithTimeoutPubSub:
    def __init__(self, stop_event: asyncio.Event):
        self.connection_pool = _FakePool()
        self.pubsub_instance = _TimeoutPubSub(stop_event)

    def pubsub(self):
        return self.pubsub_instance


class _MessagePubSub:
    def __init__(self, stop_event: asyncio.Event, messages):
        self.stop_event = stop_event
        self.messages = list(messages)
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        self.closed = False

    async def subscribe(self, *channels):
        self.subscribe_calls.append(channels)

    async def get_message(self, **_kwargs):
        if self.messages:
            return self.messages.pop(0)
        self.stop_event.set()
        return None

    async def unsubscribe(self, *channels):
        self.unsubscribe_calls.append(channels)

    async def close(self):
        self.closed = True


class _RedisWithMessagePubSub:
    def __init__(self, stop_event: asyncio.Event, messages):
        self.connection_pool = _FakePool()
        self.pubsub_instance = _MessagePubSub(stop_event, messages)

    def pubsub(self):
        return self.pubsub_instance


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
        self.streams = {}
        self.stream_seq = {}
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
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.lock_ttls[key] = ex if ex is not None else 300
        return True

    async def setex(self, key, ttl, value):
        del ttl
        self.values[key] = value
        return True

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            self.delete_calls.append(key)
            removed = False
            if key in self.lock_ttls:
                del self.lock_ttls[key]
                removed = True
            if key in self.lists:
                del self.lists[key]
                removed = True
            if key in self.values:
                del self.values[key]
                removed = True
            if removed:
                deleted += 1
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

    async def xadd(self, key, fields):
        seq = int(self.stream_seq.get(key, 0)) + 1
        self.stream_seq[key] = seq
        stream_id = f"{seq}-0"
        self.streams.setdefault(key, []).append((stream_id, dict(fields or {})))
        return stream_id

    async def xrange(self, key, min="-", max="+", count=None):
        items = list(self.streams.get(key) or [])
        out = []
        for stream_id, fields in items:
            if min not in ("-", None, ""):
                exclusive = str(min).startswith("(")
                floor = str(min)[1:] if exclusive else str(min)
                if exclusive:
                    if stream_id <= floor:
                        continue
                elif stream_id < floor:
                    continue
            if max not in ("+", None, "") and stream_id > str(max):
                continue
            out.append((stream_id, dict(fields)))
            if count is not None and len(out) >= int(count):
                break
        return out

    async def xack(self, *_args, **_kwargs):
        return 1

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

    def _touch(self, kind):
        touch_current_task_activity(kind)

    async def start(self, **kwargs):
        del kwargs
        self._touch("dummy.chat.start")

    async def step(self, **kwargs):
        del kwargs
        self._touch("dummy.chat.step")

    async def complete(self, **kwargs):
        del kwargs
        self._touch("dummy.chat.complete")

    async def error(self, **kwargs):
        type(self).error_calls.append(kwargs)
        self._touch("dummy.chat.error")

    async def delta(self, **kwargs):
        del kwargs
        self._touch("dummy.chat.delta")

    async def event(self, **kwargs):
        del kwargs
        self._touch("dummy.chat.event")

    async def emit(self, *args, **kwargs):
        del args, kwargs
        self._touch("dummy.chat.emit")

    async def emit_enveloped(self, *args, **kwargs):
        del args, kwargs
        self._touch("dummy.chat.emit_enveloped")


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
        "request": {
            "external_events": [
                {
                    "type": "event.user.prompt",
                    "event_source_id": "event.user.prompt",
                    "reactive": True,
                    "payload": {"mime": "text/plain", "event": {"text": "hello"}},
                }
            ],
            "operation": "chat",
            "payload": None,
        },
        "config": {"values": {}},
        "accounting": {"envelope": {}},
    }


def test_comm_ctx_snapshots_bundle_call_context():
    payload = ExternalEventPayload.model_validate(_build_task_payload("bundle-call-context"))
    payload.bundle_call_context = {"automation_id": "task-a", "execution_id": "exec-a"}
    with bind_current_request_context(payload):
        snapshot = snapshot_comm_ctxvars()

    try:
        restore_comm_ctxvars(snapshot)
        assert get_current_bundle_call_context() == {"automation_id": "task-a", "execution_id": "exec-a"}
        assert get_current_request_context().bundle_call_context["execution_id"] == "exec-a"
    finally:
        set_current_request_context(None)
        set_current_bundle_call_context({})


def test_comm_ctx_update_syncs_request_context_and_snapshots():
    payload = ExternalEventPayload.model_validate(_build_task_payload("bundle-call-context-update"))
    payload.bundle_call_context = {"automation_id": "task-a"}
    with bind_current_request_context(payload):
        update_current_bundle_call_context({"execution_id": "exec-b"})
        assert get_current_request_context().bundle_call_context == {
            "automation_id": "task-a",
            "execution_id": "exec-b",
        }
        snapshot = snapshot_comm_ctxvars()

    try:
        restore_comm_ctxvars(snapshot)
        assert get_current_bundle_call_context() == {"automation_id": "task-a", "execution_id": "exec-b"}
        assert get_current_request_context().bundle_call_context["execution_id"] == "exec-b"
    finally:
        set_current_request_context(None)
        set_current_bundle_call_context({})


def test_comm_ctx_patch_binding_restores_request_context():
    payload = ExternalEventPayload.model_validate(_build_task_payload("bundle-call-context-patch"))
    payload.bundle_call_context = {"scope": "base"}
    with bind_current_request_context(payload):
        with bind_current_bundle_call_context_patch({"role_models": {"agent": "haiku"}}):
            assert get_current_request_context().bundle_call_context["role_models"]["agent"] == "haiku"
        assert get_current_request_context().bundle_call_context == {"scope": "base"}


@pytest.mark.asyncio
async def test_cleanup_turn_browser_sessions_for_payload_uses_payload_context(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.tools.backends import browser_backend

    calls = []

    async def _fake_cleanup(*, bound_context=None, reason="turn_cleanup", session_id=None):
        del session_id
        calls.append((bound_context, reason))
        return {"closed_count": 1}

    monkeypatch.setattr(browser_backend, "close_browser_sessions_for_current_context", _fake_cleanup)
    payload_dict = _build_task_payload("browser-cleanup-task")
    payload_dict["request"]["request_id"] = "req-1"
    payload = ExternalEventPayload.model_validate(payload_dict)

    await processor_mod._cleanup_turn_browser_sessions_for_payload(payload, reason="task_cancelled")

    assert calls
    bound_context, reason = calls[0]
    assert reason == "task_cancelled"
    assert bound_context.tenant == "tenant-a"
    assert bound_context.project == "project-a"
    assert bound_context.user_id == "user-1"
    assert bound_context.conversation_id == "conv-1"
    assert bound_context.turn_id == "turn-1"
    assert bound_context.request_id == "req-1"
    assert bound_context.bundle_id == "bundle.demo"


def _build_processor(
        redis_client,
        *,
        max_concurrent=5,
        handler=_noop_handler,
        conversation_ctx=None,
        relay=None,
        task_timeout_sec=None,
        task_idle_timeout_sec=None,
        task_max_wall_time_sec=None,
        scheduler_backend=None,
        pg_pool=None,
):
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
        task_timeout_sec=task_timeout_sec,
        task_idle_timeout_sec=task_idle_timeout_sec,
        task_max_wall_time_sec=task_max_wall_time_sec,
        scheduler_backend=scheduler_backend,
        pg_pool=pg_pool,
        host_drain_detector=_DummyHostDrainDetector(enabled=False),
    )
    processor.queue_block_timeout_sec = 0.01
    processor.queue_call_timeout_sec = 0.02
    processor.inflight_reaper_interval_sec = 0.01
    return processor


@pytest.fixture
def _patch_processor_dependencies(monkeypatch):
    _DummyChatCommunicator.error_calls = []
    service_settings = SimpleNamespace(
        CHAT_SCHEDULER_BACKEND=SCHEDULER_BACKEND_LEGACY_LISTS,
        CHAT_TASK_TIMEOUT_SEC=30,
        CHAT_TASK_IDLE_TIMEOUT_SEC=30,
        CHAT_TASK_MAX_WALL_TIME_SEC=120,
        CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC=0.01,
    )
    settings = SimpleNamespace(
        STORAGE_PATH="/tmp",
        TENANT="tenant-a",
        PROJECT="project-a",
        PLATFORM=SimpleNamespace(SERVICE=service_settings),
    )
    monkeypatch.setattr(processor_mod, "get_settings", lambda: settings)
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
async def test_queue_claim_timeout_leaves_shared_pool_connected():
    hanging = _HangingRedis()
    processor = _build_processor(hanging)

    result = await processor._queue_claim("queue:anonymous", "queue:inflight:anonymous")

    assert result is None
    assert hanging.connection_pool.disconnect_calls == []
    assert "Queue claim exceeded" in (processor.get_runtime_metadata()["last_queue_error"] or "")


@pytest.mark.asyncio
async def test_config_listener_timeout_leaves_shared_pool_connected(monkeypatch):
    settings = SimpleNamespace(TENANT="tenant-a", PROJECT="project-a")
    import kdcube_ai_app.apps.chat.sdk.config as sdk_config_mod
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry_mod
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store_mod

    async def _fake_load_registry(*_args, **_kwargs):
        return SimpleNamespace(bundles={}, default_bundle_id=None)

    async def _fake_set_registry_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sdk_config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(bundle_store_mod, "load_registry", _fake_load_registry)
    monkeypatch.setattr(bundle_registry_mod, "set_registry_async", _fake_set_registry_async)

    redis = _RedisWithTimeoutPubSub(asyncio.Event())
    processor = _build_processor(redis)
    redis.pubsub_instance.stop_event = processor._stop_event
    processor.config_call_timeout_sec = 0.01
    processor.config_get_message_timeout_sec = 0.01

    await processor._config_listener_loop()

    assert redis.connection_pool.disconnect_calls == []
    assert redis.pubsub_instance.closed is True
    assert "Config listener get_message exceeded" in (
        processor.get_runtime_metadata()["last_config_error"] or ""
    )


@pytest.mark.asyncio
async def test_config_listener_secrets_update_invalidates_config_secret_cache(monkeypatch):
    settings = SimpleNamespace(TENANT="tenant-a", PROJECT="project-a")
    import kdcube_ai_app.apps.chat.sdk.config as sdk_config_mod
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry_mod
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store_mod
    import kdcube_ai_app.apps.chat.sdk.config_cache as config_cache_mod

    async def _fake_load_registry(*_args, **_kwargs):
        return SimpleNamespace(bundles={}, default_bundle_id=None)

    async def _fake_set_registry_async(*_args, **_kwargs):
        return None

    invalidations = []

    def _fake_clear_secret_cache(**kwargs):
        invalidations.append(kwargs)
        return 3

    monkeypatch.setattr(sdk_config_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(bundle_store_mod, "load_registry", _fake_load_registry)
    monkeypatch.setattr(bundle_registry_mod, "set_registry_async", _fake_set_registry_async)
    monkeypatch.setattr(config_cache_mod, "clear_secret_cache", _fake_clear_secret_cache)

    channel = "kdcube:config:bundles:secrets:update:tenant-a:project-a"
    event = {
        "type": "bundles.secrets.update",
        "tenant": "tenant-a",
        "project": "project-a",
        "bundle_id": "bundle@1",
        "scope": "bundle",
        "mode": "set",
        "keys": ["bundles.bundle@1.secrets.openai.api_key"],
    }
    redis = _RedisWithMessagePubSub(
        asyncio.Event(),
        [{"type": "message", "channel": channel, "data": json.dumps(event)}],
    )
    processor = _build_processor(redis)
    redis.pubsub_instance.stop_event = processor._stop_event

    await processor._config_listener_loop()

    assert invalidations == [
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "bundle_id": "bundle@1",
            "user_id": None,
            "keys": ["bundles.bundle@1.secrets.openai.api_key"],
        }
    ]
    assert redis.connection_pool.disconnect_calls == []
    assert redis.pubsub_instance.closed is True


def test_processor_defaults_to_legacy_lists_scheduler_backend():
    processor = _build_processor(_MinimalRedis())
    assert processor.scheduler_backend_name == SCHEDULER_BACKEND_LEGACY_LISTS
    assert processor.get_runtime_metadata()["scheduler_backend"] == SCHEDULER_BACKEND_LEGACY_LISTS


def test_processor_accepts_legacy_scheduler_alias_via_constructor_override():
    processor = _build_processor(_MinimalRedis(), scheduler_backend="legacy")
    assert processor.scheduler_backend_name == SCHEDULER_BACKEND_LEGACY_LISTS


@pytest.mark.asyncio
async def test_bundle_scheduler_reconcile_from_authority_reads_active_registry(monkeypatch, _patch_processor_dependencies):
    redis = _MinimalRedis()
    processor = _build_processor(redis)
    reg = bundle_store.BundlesRegistry(
        default_bundle_id="bundle.demo",
        bundles={
            "bundle.demo": bundle_store.BundleEntry(
                id="bundle.demo",
                path="/bundles/demo",
                module="entrypoint",
            )
        },
    )
    load_calls = []
    reconcile_calls = []

    async def _fake_load_registry(redis_arg, tenant, project):
        load_calls.append((redis_arg, tenant, project))
        return reg

    class _FakeScheduler:
        async def reconcile(self, registry):
            reconcile_calls.append(registry)

    monkeypatch.setattr(bundle_store, "load_registry", _fake_load_registry)
    processor._scheduler = _FakeScheduler()

    await processor._reconcile_bundle_scheduler_from_authority("unit-test")

    assert load_calls == [(redis, "tenant-a", "project-a")]
    assert reconcile_calls == [reg]


def test_background_job_chat_task_carries_accounting_context(_patch_processor_dependencies):
    processor = _build_processor(_MinimalRedis())
    claim = BackgroundJobClaim(
        stream_key="jobs:registered",
        stream_id="1-0",
        consumer_name="proc-test",
        fields={},
        job=BackgroundJob(
            job_id="job_exec_1",
            work_kind="task.execution.due",
            tenant="demo-tenant",
            project="demo-project",
            queue="registered",
            bundle_id="task-and-memo-app@1-0",
            user_id="user-123",
            user_type="registered",
            metadata={
                "conversation_id": "automation_job_abc",
                "turn_id": "turn_exec_1",
                "request_id": "req-job-1",
                "timezone": "Europe/Berlin",
            },
            payload={"automation_id": "task-1", "execution_id": "exec-1"},
        ),
    )

    task_data = processor._background_job_to_chat_task(claim)
    envelope = task_data["accounting"]["envelope"]

    assert task_data["actor"]["tenant_id"] == "demo-tenant"
    assert task_data["actor"]["project_id"] == "demo-project"
    assert task_data["routing"]["bundle_id"] == "task-and-memo-app@1-0"
    assert task_data["user"]["user_id"] == "user-123"
    assert envelope["tenant_id"] == "demo-tenant"
    assert envelope["project_id"] == "demo-project"
    assert envelope["user_id"] == "user-123"
    assert envelope["component"] == "task-and-memo-app@1-0"
    assert envelope["app_bundle_id"] == "task-and-memo-app@1-0"
    assert envelope["metadata"]["conversation_id"] == "automation_job_abc"
    assert envelope["metadata"]["turn_id"] == "turn_exec_1"


@pytest.mark.asyncio
async def test_start_processing_fails_fast_for_unimplemented_streams_backend():
    processor = _build_processor(_MinimalRedis(), scheduler_backend=SCHEDULER_BACKEND_CONVERSATION_STREAMS)
    with pytest.raises(RuntimeError, match="conversation_streams"):
        await processor.start_processing()
    assert processor.scheduler_backend_name == SCHEDULER_BACKEND_CONVERSATION_STREAMS


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

    async def _ensure_git_bundle(**kwargs):
        ensure_calls.append(kwargs)

    async def _cache_status(**kwargs):
        del kwargs
        return SimpleNamespace(current=True, reason="current")

    monkeypatch.setattr(processor_mod, "ensure_git_bundle", _ensure_git_bundle)
    monkeypatch.setattr(processor_mod, "git_bundle_cache_status", _cache_status)

    errors = await processor_mod.prefetch_git_bundles({
        "bundle.ready": {
            "repo": "https://example.invalid/repo.git",
            "path": str(bundle_root),
        }
    })

    assert errors == {}
    assert ensure_calls == []


@pytest.mark.asyncio
async def test_prefetch_git_bundles_materializes_existing_path_when_cache_not_current(monkeypatch, tmp_path):
    bundle_root = tmp_path / "bundle-stale"
    bundle_root.mkdir()
    ensure_calls = []

    async def _ensure_git_bundle(**kwargs):
        ensure_calls.append(kwargs)

    async def _cache_status(**kwargs):
        del kwargs
        return SimpleNamespace(current=False, reason="missing_marker")

    monkeypatch.setattr(processor_mod, "ensure_git_bundle", _ensure_git_bundle)
    monkeypatch.setattr(processor_mod, "git_bundle_cache_status", _cache_status)

    errors = await processor_mod.prefetch_git_bundles({
        "bundle.stale": {
            "repo": "https://example.invalid/repo.git",
            "path": str(bundle_root),
        }
    })

    assert errors == {}
    assert [call["bundle_id"] for call in ensure_calls] == ["bundle.stale"]


@pytest.mark.asyncio
async def test_prefetch_git_bundles_resolves_missing_paths_and_collects_errors(monkeypatch):
    ensure_calls = []

    monkeypatch.setattr(
        processor_mod,
        "compute_git_bundle_paths",
        lambda **kwargs: SimpleNamespace(bundle_root=Path(f"/tmp/{kwargs['bundle_id']}")),
    )

    async def _ensure_git_bundle(**kwargs):
        ensure_calls.append(kwargs)
        if kwargs["bundle_id"] == "bundle.cooldown":
            raise processor_mod.GitBundleCooldown("cooldown")
        if kwargs["bundle_id"] == "bundle.fail":
            raise RuntimeError("boom")

    async def _cache_status(**kwargs):
        del kwargs
        return SimpleNamespace(current=False, reason="repo_missing")

    monkeypatch.setattr(processor_mod, "ensure_git_bundle", _ensure_git_bundle)
    monkeypatch.setattr(processor_mod, "git_bundle_cache_status", _cache_status)

    monkeypatch.setattr(
        processor_mod,
        "get_settings",
        lambda: SimpleNamespace(
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    GIT=SimpleNamespace(
                        BUNDLE_GIT_ALWAYS_PULL=False,
                        BUNDLE_GIT_ATOMIC=True,
                    ),
                ),
            ),
        ),
    )
    errors = await processor_mod.prefetch_git_bundles({
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
    })

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
async def test_processor_lock_release_does_not_delete_mismatched_token():
    redis = _MinimalRedis()
    processor = _build_processor(redis)
    await redis.set("lock:test", "owner-a", ex=300)

    released = await processor._release_redis_lock("lock:test", "owner-b")

    assert released is False
    assert await redis.get("lock:test") == "owner-a"


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
async def test_process_task_does_not_scan_lane_after_completion(_patch_processor_dependencies):
    redis = _MinimalRedis()
    conversation_ctx = _NoopConversationCtx()
    relay = _NoopRelay()
    processor = _build_processor(redis, conversation_ctx=conversation_ctx, relay=relay)

    current_payload = _build_task_payload("task-current", user_type="registered")
    payload = ExternalEventPayload.model_validate(current_payload)
    source = processor._external_event_source_for(payload)
    await source.publish(
        kind="external_event",
        event_id="evt-next",
        source="test",
        event_source_id="event.user.followup",
        text="follow up",
        payload={
            "event": {
                "type": "event.user.followup",
                "event_source_id": "event.user.followup",
                "reactive": True,
                "timestamp": "2026-06-10T10:00:01Z",
                "payload": {"mime": "text/plain", "event": {"text": "follow up"}},
            }
        },
        task_payload=current_payload,
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
    assert redis.lists.get("queue:registered", []) == []
    assert conversation_ctx.calls[-1]["new_state"] == "idle"
    assert conversation_ctx.calls[-1]["last_turn_id"] == "turn-1"
    assert relay.conv_status_calls[-1]["kwargs"]["completion"] == "success"


@pytest.mark.asyncio
async def test_process_task_acks_stale_external_event_lane_wakeup_without_running_handler(_patch_processor_dependencies):
    redis = _MinimalRedis()
    handler_calls = []

    async def _handler(payload):
        handler_calls.append(payload)
        return {}

    processor = _build_processor(redis, handler=_handler)
    event_payload = _build_task_payload("task-event", user_type="registered")
    event_payload["routing"]["turn_id"] = "turn-event"
    event_payload["event"] = {
        "kind": "external_event",
        "event_source_id": "event.user.followup",
        "reactive": True,
    }
    event_payload["request"]["external_events"][0]["timestamp"] = "2026-06-10T10:00:00Z"
    event_payload["request"]["external_events"][0]["ts"] = "2026-06-10T10:00:00Z"

    payload_model = ExternalEventPayload.model_validate(event_payload)
    source = processor._external_event_source_for(payload_model)
    event = await source.publish(
        kind="external_event",
        event_id="evt-stale",
        source="test",
        event_source_id="event.user.followup",
        text="follow up",
        payload={"event": event_payload["request"]["external_events"][0]},
        task_payload=event_payload,
    )
    await ConversationEventBusOrchestrator.for_source(source).open_handler(turn_id="turn-event")
    await ConversationEventBusOrchestrator.for_source(source).record_processed_events([event], turn_id="turn-event")

    wakeup = build_event_lane_wakeup(
        payload=payload_model,
        event=event,
        tenant=payload_model.actor.tenant_id,
        project=payload_model.actor.project_id,
        user_id=payload_model.user.user_id or payload_model.user.fingerprint or "",
        conversation_id=payload_model.routing.conversation_id or payload_model.routing.session_id,
        agent_id=getattr(payload_model.event, "agent_id", None),
        reason="test",
    )
    raw_payload = json.dumps(wakeup.model_dump()).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:task-event-wakeup"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = wakeup.model_dump()
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    await processor._process_task(task_data)

    assert handler_calls == []
    assert redis.lists[inflight_key] == []
    assert lock_key in redis.delete_calls
    assert processor.get_current_load() == 0


@pytest.mark.asyncio
async def test_process_task_schedules_fresh_external_event_lane_wakeup_before_handler(_patch_processor_dependencies):
    redis = _MinimalRedis()
    captured = {}
    source_holder = {}

    async def _handler(payload):
        source = source_holder["source"]
        state = await ConversationEventBusOrchestrator.for_source(source).state()
        captured["task_id"] = payload.meta.task_id
        captured["event_id"] = payload.event.event_id
        captured["consumer_status"] = state.consumer_status
        captured["consumer_status_at"] = state.consumer_status_at
        await source.mark_consumed_up_to(max_sequence=int(payload.event.sequence or 0), turn_id=payload.routing.turn_id)
        return {}

    processor = _build_processor(redis, handler=_handler)
    event_payload = _build_task_payload("task-event", user_type="registered")
    event_payload["routing"]["turn_id"] = "turn-event"
    event_payload["event"] = {
        "kind": "external_event",
        "event_source_id": "event.user.followup",
        "reactive": True,
    }
    event_payload["request"]["external_events"][0]["timestamp"] = "2026-06-10T10:00:01Z"
    event_payload["request"]["external_events"][0]["ts"] = "2026-06-10T10:00:01Z"

    payload_model = ExternalEventPayload.model_validate(event_payload)
    source = processor._external_event_source_for(payload_model)
    source_holder["source"] = source
    event = await source.publish(
        kind="external_event",
        event_id="evt-fresh",
        source="test",
        event_source_id="event.user.followup",
        text="follow up",
        payload={"event": event_payload["request"]["external_events"][0]},
        task_payload=event_payload,
    )

    wakeup = build_event_lane_wakeup(
        payload=payload_model,
        event=event,
        tenant=payload_model.actor.tenant_id,
        project=payload_model.actor.project_id,
        user_id=payload_model.user.user_id or payload_model.user.fingerprint or "",
        conversation_id=payload_model.routing.conversation_id or payload_model.routing.session_id,
        agent_id=getattr(payload_model.event, "agent_id", None),
        reason="test",
    )
    raw_payload = json.dumps(wakeup.model_dump()).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:task-event-wakeup"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = wakeup.model_dump()
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    await processor._process_task(task_data)

    assert captured["task_id"] == "task-event"
    assert captured["event_id"] == "evt-fresh"
    assert captured["consumer_status"] == "scheduled"
    assert captured["consumer_status_at"]
    assert redis.lists[inflight_key] == []


@pytest.mark.asyncio
async def test_process_task_does_not_schedule_invalid_external_event_lane_wakeup(_patch_processor_dependencies):
    redis = _MinimalRedis()
    handler_calls = []

    async def _handler(payload):
        handler_calls.append(payload)
        return {}

    processor = _build_processor(redis, handler=_handler)
    event_payload = _build_task_payload("task-invalid", user_type="registered")
    event_payload["routing"]["turn_id"] = "turn-invalid"
    event_payload["event"] = {
        "kind": "external_event",
        "event_source_id": "event.user.followup",
        "reactive": True,
    }
    event_payload["request"]["external_events"][0]["timestamp"] = "2026-06-10T10:00:02Z"
    event_payload["request"]["external_events"][0]["ts"] = "2026-06-10T10:00:02Z"

    payload_model = ExternalEventPayload.model_validate(event_payload)
    source = processor._external_event_source_for(payload_model)
    invalid_payload = dict(event_payload)
    invalid_payload["request"] = dict(event_payload["request"])
    invalid_payload["request"]["chat_history"] = {"bad": "shape"}
    event = await source.publish(
        kind="external_event",
        event_id="evt-invalid",
        source="test",
        event_source_id="event.user.followup",
        text="follow up",
        payload={"event": event_payload["request"]["external_events"][0]},
        task_payload=invalid_payload,
    )

    wakeup = build_event_lane_wakeup(
        payload=payload_model,
        event=event,
        tenant=payload_model.actor.tenant_id,
        project=payload_model.actor.project_id,
        user_id=payload_model.user.user_id or payload_model.user.fingerprint or "",
        conversation_id=payload_model.routing.conversation_id or payload_model.routing.session_id,
        agent_id=getattr(payload_model.event, "agent_id", None),
        reason="test",
    )
    raw_payload = json.dumps(wakeup.model_dump()).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:task-invalid-wakeup"
    redis.seed_list(inflight_key, [raw_payload])
    redis.lock_ttls[lock_key] = 300
    processor._current_load = 1

    task_data = wakeup.model_dump()
    task_data["_lock_key"] = lock_key
    task_data["_raw_payload"] = raw_payload
    task_data["_ready_queue_key"] = "queue:registered"
    task_data["_inflight_queue_key"] = inflight_key
    task_data["_queue_wait_ms"] = 10

    await processor._process_task(task_data)

    state = await ConversationEventBusOrchestrator.for_source(source).state()
    assert state.consumer_status not in {"scheduled", "active"}
    assert handler_calls == []
    assert redis.lists[inflight_key] == []
    assert lock_key in redis.delete_calls
    assert processor.get_current_load() == 0
    assert redis.lists.get("queue:registered", []) == []


@pytest.mark.asyncio
async def test_legacy_queue_requeues_when_conversation_lock_is_held(_patch_processor_dependencies):
    redis = _MinimalRedis()
    processor = _build_processor(redis)

    task_payload = _build_task_payload("task-locked", user_type="registered")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    ready_key = "queue:registered"
    inflight_key = "queue:inflight:registered"
    redis.seed_list(ready_key, [raw_payload])

    conversation_lock_key = processor._task_conversation_lock_key(task_payload)
    assert conversation_lock_key is not None
    redis.values[conversation_lock_key] = "other-processor"

    result = await processor._legacy_pop_any_queue_fair()

    assert result is None
    assert redis.lists[ready_key] == [raw_payload]
    assert redis.lists[inflight_key] == []
    assert redis.values[conversation_lock_key] == "other-processor"
    assert "lock:task-locked" in redis.delete_calls


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
            get_current_bundle_call_context().get("purpose"),
        )
        await asyncio.sleep(0)
        current = get_current_request_context()
        current_comm = get_current_comm()
        captured["after"] = (
            getattr(getattr(current, "user", None), "user_id", None),
            getattr(getattr(current, "routing", None), "socket_id", None),
            current_comm is not None,
            get_current_bundle_call_context().get("purpose"),
        )
        return {}

    processor = _build_processor(redis, handler=_handler)
    task_payload = _build_task_payload("ctx-task")
    task_payload["bundle_call_context"] = {"purpose": "test-context"}
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

    assert captured["before"] == ("user-1", "socket-1", True, "test-context")
    assert captured["after"] == ("user-1", "socket-1", True, "test-context")


@pytest.mark.asyncio
async def test_process_task_binds_peer_operation_caller_with_processor_pg_pool(
        monkeypatch,
        _patch_processor_dependencies,
):
    redis = _MinimalRedis()
    pg_pool = object()
    captured = {}

    def _fake_make_local_bundle_operation_caller(*, redis, pg_pool, comm_context):
        captured["redis"] = redis
        captured["pg_pool"] = pg_pool
        captured["bundle_id"] = comm_context.routing.bundle_id

        async def _caller(call):
            captured["call"] = call
            return {"ok": True, "bundle_id": call.bundle_id, "operation": call.operation}

        return _caller

    monkeypatch.setattr(
        processor_mod,
        "make_local_bundle_operation_caller",
        _fake_make_local_bundle_operation_caller,
    )

    async def _handler(_payload):
        result = await call_bundle_operation(
            bundle_id="bundle.provider",
            operation="named_service",
            data={"operation": "object.get"},
        )
        captured["result"] = result
        return {}

    processor = _build_processor(redis, handler=_handler, pg_pool=pg_pool)
    task_payload = _build_task_payload("peer-pool-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:peer-pool-task"
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

    assert captured["redis"] is redis
    assert captured["pg_pool"] is pg_pool
    assert captured["bundle_id"] == "bundle.demo"
    assert captured["result"] == {
        "ok": True,
        "bundle_id": "bundle.provider",
        "operation": "named_service",
    }


@pytest.mark.asyncio
async def test_process_task_idle_watchdog_times_out_silent_handler(_patch_processor_dependencies):
    redis = _MinimalRedis()

    async def _silent_handler(_payload):
        await asyncio.Event().wait()

    processor = _build_processor(
        redis,
        handler=_silent_handler,
        task_idle_timeout_sec=1,
        task_max_wall_time_sec=5,
    )
    processor.task_idle_timeout_sec = 0.05
    processor.task_max_wall_time_sec = 1.0
    processor._task_watchdog_poll_interval_sec = 0.01

    task_payload = _build_task_payload("idle-timeout-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:idle-timeout-task"
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

    assert redis.lists[inflight_key] == []
    assert processor.get_current_load() == 0
    assert _patch_processor_dependencies.error_calls[-1]["data"]["error_type"] == "task_watchdog_timeout"
    assert _patch_processor_dependencies.error_calls[-1]["data"]["timeout_kind"] == "idle"


@pytest.mark.asyncio
async def test_process_task_internal_activity_touch_prevents_idle_timeout(_patch_processor_dependencies):
    redis = _MinimalRedis()

    async def _internally_active_handler(_payload):
        for _ in range(5):
            await asyncio.sleep(0.02)
            assert touch_current_task_activity("test.internal_activity")
        return {"ok": True}

    processor = _build_processor(
        redis,
        handler=_internally_active_handler,
        task_idle_timeout_sec=1,
        task_max_wall_time_sec=5,
    )
    processor.task_idle_timeout_sec = 0.05
    processor.task_max_wall_time_sec = 1.0
    processor._task_watchdog_poll_interval_sec = 0.01

    task_payload = _build_task_payload("internal-activity-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:internal-activity-task"
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

    assert redis.lists[inflight_key] == []
    assert processor.get_current_load() == 0
    assert not _patch_processor_dependencies.error_calls


@pytest.mark.asyncio
async def test_process_task_raw_communicator_emit_updates_idle_activity(_patch_processor_dependencies):
    redis = _MinimalRedis()

    async def _raw_comm_active_handler(_payload):
        comm = get_current_comm()
        raw_comm = getattr(comm, "_inner", comm)
        assert raw_comm is not None
        for idx in range(5):
            await asyncio.sleep(0.02)
            await raw_comm.delta(text=f"chunk-{idx}", index=idx, marker="thinking")
        return {"ok": True}

    processor = _build_processor(
        redis,
        handler=_raw_comm_active_handler,
        task_idle_timeout_sec=1,
        task_max_wall_time_sec=5,
    )
    processor.task_idle_timeout_sec = 0.05
    processor.task_max_wall_time_sec = 1.0
    processor._task_watchdog_poll_interval_sec = 0.01

    task_payload = _build_task_payload("raw-comm-activity-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:raw-comm-activity-task"
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

    assert redis.lists[inflight_key] == []
    assert processor.get_current_load() == 0
    assert not _patch_processor_dependencies.error_calls


@pytest.mark.asyncio
async def test_process_task_hard_cap_wins_even_with_ongoing_activity(_patch_processor_dependencies):
    redis = _MinimalRedis()

    async def _chatty_handler(_payload):
        comm = get_current_comm()
        assert comm is not None
        while True:
            await asyncio.sleep(0.01)
            await comm.step(step="heartbeat", status="running")

    processor = _build_processor(
        redis,
        handler=_chatty_handler,
        task_idle_timeout_sec=1,
        task_max_wall_time_sec=5,
    )
    processor.task_idle_timeout_sec = 0.25
    processor.task_max_wall_time_sec = 0.06
    processor._task_watchdog_poll_interval_sec = 0.01

    task_payload = _build_task_payload("wall-timeout-task")
    raw_payload = json.dumps(task_payload).encode("utf-8")
    inflight_key = "queue:inflight:registered"
    lock_key = "lock:wall-timeout-task"
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

    metadata = processor.get_runtime_metadata()
    assert metadata["task_idle_timeout_sec"] == pytest.approx(0.25, rel=0, abs=1e-6)
    assert metadata["task_max_wall_time_sec"] == pytest.approx(0.06, rel=0, abs=1e-6)
    assert _patch_processor_dependencies.error_calls[-1]["data"]["error_type"] == "task_watchdog_timeout"
    assert _patch_processor_dependencies.error_calls[-1]["data"]["timeout_kind"] == "wall"


@pytest.mark.asyncio
async def test_runtime_metadata_includes_active_task_activity_details(_patch_processor_dependencies):
    redis = _MinimalRedis()
    processor = _build_processor(redis)

    async def _never_done():
        await asyncio.Event().wait()

    task = asyncio.create_task(_never_done())
    now_iso = "2026-05-08T16:00:00Z"
    try:
        processor._active_tasks.add(task)
        processor._active_task_details[task] = {
            "task_id": "task-active",
            "queue_key": "queue:registered",
            "inflight_queue_key": "queue:inflight:registered",
            "claimed_at": now_iso,
            "claimed_monotonic": time.monotonic() - 4,
            "started_at": now_iso,
            "started_monotonic": time.monotonic() - 3,
            "started_execution": True,
            "last_activity_at": now_iso,
            "last_activity_monotonic": time.monotonic() - 1,
            "last_activity_kind": "comm.emit:chat.delta",
            "activity_count": 7,
        }

        metadata = processor.get_runtime_metadata()

        assert metadata["active_tasks"] == 1
        assert metadata["active_task_details"][0]["task_id"] == "task-active"
        assert metadata["active_task_details"][0]["last_activity_kind"] == "comm.emit:chat.delta"
        assert metadata["active_task_details"][0]["activity_count"] == 7
        assert metadata["active_task_details"][0]["idle_age_sec"] >= 0
    finally:
        task.cancel()

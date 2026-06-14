import json
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.ingress.ingress_core as ingress_core
from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressConfig, process_chat_message
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.auth.sessions import UserType


def _scoped_external_source(redis):
    return build_conversation_external_event_source(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        user_id="user-1",
        agent_id="default.react.agent",
    )


def _text_event(event_type: str, text: str, *, reactive: bool = True) -> dict:
    return {
        "type": event_type,
        "event_source_id": event_type,
        "reactive": reactive,
        "payload": {
            "mime": "text/plain",
            "event": {"text": text},
        },
    }


def _attachment_event(*, filename: str, mime: str = "application/octet-stream", reactive: bool = True) -> dict:
    return {
        "type": "event.user.attachment",
        "event_source_id": "event.user.attachment",
        "reactive": reactive,
        "payload": {
            "mime": mime,
            "event": {"filename": filename, "mime": mime},
        },
    }


def _domain_event(event_type: str, body: dict, *, reactive: bool = False) -> dict:
    return {
        "type": event_type,
        "event_source_id": event_type,
        "reactive": reactive,
        "payload": {
            "mime": "application/json",
            "event": dict(body),
        },
    }


class _MiniRedis:
    def __init__(self):
        self.lists = {}
        self.streams = {}
        self.stream_seq = {}
        self.values = {}

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

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

    async def rpop(self, key):
        items = list(self.lists.get(key) or [])
        if not items:
            return None
        value = items.pop()
        self.lists[key] = items
        return value

    async def llen(self, key):
        return len(self.lists.get(key) or [])

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

    async def setex(self, key, ttl, value):
        del ttl
        self.values[key] = value

    async def set(self, key, value, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)


class _BusyConversationBrowser:
    async def conversation_exists(self, **kwargs):
        del kwargs
        return True

    async def set_conversation_state(self, **kwargs):
        del kwargs
        return {
            "ok": False,
            "error_type": "conversation_busy",
            "error": "busy",
            "updated_at": "2026-03-16T00:00:00Z",
            "current_turn_id": "turn-active",
        }


class _IdleConversationBrowser:
    async def conversation_exists(self, **kwargs):
        del kwargs
        return False

    async def set_conversation_state(self, **kwargs):
        del kwargs
        return {
            "ok": True,
            "updated_at": "2026-03-16T00:00:00Z",
            "current_turn_id": "turn-new",
        }


class _DummyEnvelope:
    def to_dict(self):
        return {"request_id": "req-1"}


class _DummyCommunicator:
    service_events = []

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def service_event(self, **kwargs):
        type(self).service_events.append(kwargs)

    async def event(self, **kwargs):
        del kwargs


class _DummyRelay:
    async def emit_error(self, *args, **kwargs):
        del args, kwargs

    async def emit_conv_status(self, *args, **kwargs):
        del args, kwargs


class _QueueManagerShouldNotRun:
    async def enqueue_chat_task_atomic(self, *args, **kwargs):
        raise AssertionError("main queue should not be used for busy continuations")


class _QueueManagerCaptures:
    def __init__(self, redis):
        self.redis = redis
        self.payloads = []
        self.fail_reason = ""

    async def enqueue_chat_task_atomic(self, _user_type, payload, *_args, **_kwargs):
        self.payloads.append(payload)
        return True, "ok", {"queued": 1}

    async def enqueue_chat_task_with_lane_events_atomic(
        self,
        _user_type,
        payload,
        *_args,
        lane_log_key,
        lane_events,
        **_kwargs,
    ):
        if self.fail_reason:
            return False, self.fail_reason, {"queued": 0, "lane_stream_ids": []}
        stream_ids = []
        for item in lane_events:
            event = dict(item["event"])
            stream_id = await self.redis.xadd(lane_log_key, {"message_id": event["message_id"]})
            await self.redis.set(item["event_key"], json.dumps(event))
            stream_ids.append(stream_id)
        self.payloads.append(payload)
        return True, "ok", {"queued": 1, "lane_stream_ids": stream_ids}


class _DummyConversationStore:
    async def put_attachment(
        self,
        *,
        tenant,
        project,
        user,
        fingerprint,
        conversation_id,
        turn_id,
        role,
        filename,
        data,
        mime,
        user_type,
        origin,
    ):
        del tenant, project, user, fingerprint, conversation_id, role, data, mime, user_type, origin
        hosted_uri = f"file:///tmp/{turn_id}/{filename}"
        key = f"k/{turn_id}/{filename}"
        rn = f"rn:{turn_id}:{filename}"
        return hosted_uri, key, rn

    def _who_and_id(self, user_id, fingerprint):
        return user_id, fingerprint or user_id

    async def delete_turn(self, **kwargs):
        del kwargs


@pytest.fixture
def _patch_ingress_dependencies(monkeypatch):
    _DummyCommunicator.service_events = []
    async def _load_registry(*_args, **_kwargs):
        return SimpleNamespace(
            default_bundle_id="bundle.demo",
            bundles={"bundle.demo": SimpleNamespace(id="bundle.demo")},
        )
    monkeypatch.setattr(
        ingress_core,
        "get_settings",
        lambda: SimpleNamespace(
            TENANT="tenant-a",
            PROJECT="project-a",
            PLATFORM=SimpleNamespace(
                HOSTED_SERVICES=SimpleNamespace(
                    AV=SimpleNamespace(
                        APP_AV_SCAN=False,
                        APP_AV_TIMEOUT_S=5,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(ingress_core, "_load_active_registry", _load_registry)
    monkeypatch.setattr(ingress_core, "build_envelope_from_session", lambda **_kwargs: _DummyEnvelope())
    monkeypatch.setattr(ingress_core, "ChatCommunicator", _DummyCommunicator)


@pytest.mark.asyncio
async def test_regular_attachment_only_message_is_enqueued(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_IdleConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    queue = _QueueManagerCaptures(redis)
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [_attachment_event(filename="notes.txt", mime="text/plain")],
        },
        message_text="",
        raw_attachments=[
            ingress_core.RawAttachment(
                content=b"hello",
                name="notes.txt",
                mime="text/plain",
                meta={"source": "test"},
            )
        ],
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert result.event_id
    assert result.external_event_sequence == 1
    assert len(queue.payloads) == 1
    wakeup = queue.payloads[0]
    assert wakeup["kind"] == "external_event_lane_wakeup"
    assert wakeup["event_lane"]["event_id"] == result.event_id
    assert wakeup["event_lane"]["sequence"] == result.external_event_sequence
    assert "request" not in wakeup

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "external_event"
    stored = await source.get_event(result.event_id)
    assert stored is not None
    assert stored.promoted_task_id is None
    assert stored.promoted_at is None
    assert stored.consumed_at is None
    payload = events[0].task_payload
    accepted_events = payload["request"]["external_events"]
    assert accepted_events[0]["type"] == "event.user.attachment"
    model = events[0].task_payload_model()
    assert model.user.permissions == []
    assert model.user.roles == []
    assert model.request.chat_history == []
    attachments = [accepted_events[0]["payload"]["event"]]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "notes.txt"
    assert attachments[0]["hosted_uri"].endswith("/notes.txt")
    assert "base64" not in attachments[0]


@pytest.mark.asyncio
async def test_idle_non_reactive_external_event_is_recorded_without_wakeup(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_IdleConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    queue = _QueueManagerCaptures(redis)
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [
                _domain_event("task_tracker.wizard.snapshot", {"draft_id": "draft-1"}, reactive=False)
            ],
        },
        message_text="",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_recorded"
    assert result.is_continuation is False
    assert queue.payloads == []

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "external_event"
    assert events[0].is_continuation is False
    assert events[0].active_turn_id_at_ingress is None
    accepted_event = events[0].task_payload["request"]["external_events"][0]
    assert accepted_event["type"] == "task_tracker.wizard.snapshot"
    assert accepted_event["reactive"] is False


@pytest.mark.asyncio
async def test_busy_non_reactive_external_event_is_accepted_by_open_turn(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [
                _domain_event("task_tracker.wizard.snapshot", {"draft_id": "draft-1"}, reactive=False)
            ],
        },
        message_text="",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_accepted"
    assert result.is_continuation is True
    assert result.active_turn_id == "turn-active"

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "external_event"
    assert events[0].is_continuation is True
    assert events[0].active_turn_id_at_ingress == "turn-active"
    assert events[0].payload["is_continuation"] is True
    accepted_event = events[0].task_payload["request"]["external_events"][0]
    assert accepted_event["type"] == "task_tracker.wizard.snapshot"
    assert accepted_event["reactive"] is False


@pytest.mark.asyncio
async def test_busy_message_is_stored_as_followup(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    queue = _QueueManagerCaptures(redis)
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [_text_event("event.user.followup", "followup text")],
        },
        message_text="followup text",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_accepted"
    assert result.is_continuation is True
    assert result.active_turn_id == "turn-active"
    assert result.target_turn_id is None
    assert result.queued_turn_id == result.turn_id
    assert result.live_owner_detected is None
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "external_event"
    assert events[0].is_continuation is True
    assert events[0].active_turn_id_at_ingress == "turn-active"
    assert not events[0].owner_turn_id
    assert result.event_id == events[0].message_id
    assert result.external_event_sequence == events[0].sequence
    assert events[0].task_payload["event"]["kind"] == "external_event"
    assert events[0].task_payload["request"]["external_events"][0]["type"] == "event.user.followup"
    assert events[0].task_payload["continuation"]["is_continuation"] is True


@pytest.mark.asyncio
async def test_idle_reactive_queue_rejection_does_not_write_lane_event(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_IdleConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    queue = _QueueManagerCaptures(redis)
    queue.fail_reason = "hard_limit_exceeded"
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [_text_event("event.user.prompt", "hello")],
        },
        message_text="hello",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is False
    assert result.error_type == "queue.enqueue_rejected"
    assert queue.payloads == []

    source = _scoped_external_source(redis)
    assert await source.read_since(0) == []


@pytest.mark.asyncio
async def test_busy_followup_batch_stamps_context_and_followup_events(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    memory_event = _domain_event(
        "event.external",
        {
            "context_role": "context",
            "object_ref": "mem:mem_803986c10e324a16b05a3ba109237c7c",
            "label": "Family facts about Elena and Timur",
        },
        reactive=False,
    )
    memory_event["event_source_id"] = "memory.context"
    memory_event["hosted_uri"] = "mem:mem_803986c10e324a16b05a3ba109237c7c"

    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [
                memory_event,
                _text_event("event.user.followup", "and this?"),
            ],
        },
        message_text="and this?",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"
    assert result.queue_stats["queue_payload_kind"] == "external_event_lane_wakeup"

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 2
    batch_ids = {event.batch_id for event in events}
    assert len(batch_ids) == 1
    batch_id = next(iter(batch_ids))
    assert batch_id.startswith("batch_")
    assert [event.task_payload["request"]["external_events"][0]["batch_id"] for event in events] == [batch_id, batch_id]
    assert [event.payload["event"]["batch_id"] for event in events] == [batch_id, batch_id]
    assert events[0].task_payload["request"]["external_events"][0]["event_source_id"] == "memory.context"
    assert events[1].task_payload["request"]["external_events"][0]["type"] == "event.user.followup"


@pytest.mark.asyncio
async def test_context_only_send_with_empty_prompt_enqueues_reactive_wakeup(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_IdleConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    memory_event = _domain_event(
        "event.external",
        {
            "context_role": "context",
            "object_ref": "mem:mem_803986c10e324a16b05a3ba109237c7c",
            "label": "Family facts about Elena and Timur",
        },
        reactive=False,
    )
    memory_event["event_source_id"] = "memory.context"
    memory_event["hosted_uri"] = "mem:mem_803986c10e324a16b05a3ba109237c7c"

    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [
                memory_event,
                _text_event("event.user.prompt", ""),
            ],
        },
        message_text="",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"
    assert result.queue_stats["queue_payload_kind"] == "external_event_lane_wakeup"

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert [event.task_payload["request"]["external_events"][0]["event_source_id"] for event in events] == [
        "memory.context",
        "event.user.prompt",
    ]
    assert events[1].task_payload["request"]["external_events"][0]["payload"]["event"]["text"] == ""


@pytest.mark.asyncio
async def test_busy_followup_ack_preserves_target_and_server_active_owner(_patch_ingress_dependencies):
    redis = _MiniRedis()
    source = _scoped_external_source(redis)
    await redis.set(source.owner_key, json.dumps({
        "turn_id": "turn-active",
        "bundle_id": "bundle.demo",
        "instance_id": "proc-1",
        "process_id": 11,
        "listener_id": "listener-1",
        "lease_token": "lease-1",
        "lease_epoch": 1,
        "started_at": "2026-03-16T00:00:00Z",
        "updated_at": "2026-03-16T00:00:01Z",
    }))
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "target_turn_id": "turn-client-visible",
            "external_events": [_text_event("event.user.followup", "followup text")],
        },
        message_text="followup text",
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_accepted"
    assert result.is_continuation is True
    assert result.active_turn_id == "turn-active"
    assert result.target_turn_id == "turn-client-visible"
    assert result.queued_turn_id == result.turn_id
    assert result.live_owner_detected is None
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"

    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].is_continuation is True
    assert events[0].target_turn_id == "turn-client-visible"
    assert events[0].active_turn_id_at_ingress == "turn-active"
    assert not events[0].owner_turn_id
    assert events[0].task_payload["continuation"]["is_continuation"] is True
    assert events[0].task_payload["continuation"]["target_turn_id"] == "turn-client-visible"
    assert events[0].task_payload["continuation"]["active_turn_id"] == "turn-active"
    assert result.event_id == events[0].message_id
    assert result.external_event_sequence == events[0].sequence


@pytest.mark.asyncio
async def test_busy_followup_attachment_is_persisted_into_external_event_payload(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [
                _text_event("event.user.followup", "followup text"),
                _attachment_event(filename="notes.txt", mime="text/plain", reactive=False),
            ],
        },
        message_text="followup text",
        raw_attachments=[
            ingress_core.RawAttachment(
                content=b"hello",
                name="notes.txt",
                mime="text/plain",
                meta={"source": "test"},
            )
        ],
        ingress=IngressConfig(
            transport="sse",
            entrypoint="/sse/chat",
            component="chat.sse",
            instance_id="ingress-1",
            stream_id="stream-1",
        ),
    )

    assert result.ok is True
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"
    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 2
    accepted_events = []
    for lane_event in events:
        accepted_events.extend(lane_event.task_payload["request"]["external_events"])
    attachments = [
        item["payload"]["event"]
        for item in accepted_events
        if item.get("type") == "event.user.attachment"
    ]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "notes.txt"
    assert attachments[0]["hosted_uri"] == "file:///tmp/turn-active/notes.txt"
    assert attachments[0]["rn"] == "rn:turn-active:notes.txt"
    assert "base64" not in attachments[0]
    assert "text" not in attachments[0]


@pytest.mark.asyncio
async def test_busy_attachment_only_followup_is_persisted_into_external_event_payload(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [_attachment_event(filename="followup-notes.txt", mime="text/plain")],
        },
        message_text="",
        raw_attachments=[
            ingress_core.RawAttachment(
                content=b"hello",
                name="followup-notes.txt",
                mime="text/plain",
                meta={"source": "test"},
            )
        ],
        ingress=IngressConfig(
            transport="socket",
            entrypoint="/socket.io/chat",
            component="chat.socket",
            instance_id="ingress-1",
            stream_id="socket-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_accepted"
    assert result.is_continuation is True
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"
    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].text == ""
    accepted_events = events[0].task_payload["request"]["external_events"]
    attachments = [
        item["payload"]["event"]
        for item in accepted_events
        if item.get("type") == "event.user.attachment"
    ]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "followup-notes.txt"
    assert attachments[0]["hosted_uri"] == "file:///tmp/turn-active/followup-notes.txt"
    assert attachments[0]["rn"] == "rn:turn-active:followup-notes.txt"


@pytest.mark.asyncio
async def test_busy_blank_explicit_steer_is_stored(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
        conversation_store=_DummyConversationStore(),
    ))
    session = SimpleNamespace(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        username="user",
        email="user@example.com",
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    queue = _QueueManagerCaptures(redis)
    result = await process_chat_message(
        app=app,
        chat_queue_manager=queue,
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "external_events": [_text_event("event.user.steer", "")],
        },
        message_text="",
        ingress=IngressConfig(
            transport="socket",
            entrypoint="/socket.io/chat",
            component="chat.socket",
            instance_id="ingress-1",
            stream_id="socket-1",
        ),
    )

    assert result.ok is True
    assert result.reason == "external_event_accepted"
    assert result.is_continuation is True
    assert len(queue.payloads) == 1
    assert queue.payloads[0]["kind"] == "external_event_lane_wakeup"

    source = _scoped_external_source(redis)
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "external_event"
    assert events[0].is_continuation is True
    assert events[0].task_payload["request"]["external_events"][0]["type"] == "event.user.steer"
    assert events[0].task_payload["continuation"]["is_continuation"] is True
    assert events[0].task_payload["continuation"]["target_turn_id"] is None

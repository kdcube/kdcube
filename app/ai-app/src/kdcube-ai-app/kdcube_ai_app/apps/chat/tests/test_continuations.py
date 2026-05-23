import json
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.ingress.chat_core as chat_core
from kdcube_ai_app.apps.chat.ingress.chat_core import IngressConfig, process_chat_message
from kdcube_ai_app.apps.chat.continuations import RedisConversationContinuationSource
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.auth.sessions import UserType


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


def _task_payload(*, task_id: str, turn_id: str, text: str, kind: str = "followup"):
    return {
        "meta": {"task_id": task_id, "created_at": 1.0, "instance_id": "ingress-1"},
        "routing": {
            "bundle_id": "bundle.demo",
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "turn_id": turn_id,
            "socket_id": "stream-1",
        },
        "actor": {"tenant_id": "tenant-a", "project_id": "project-a"},
        "user": {"user_type": "registered", "user_id": "user-1", "fingerprint": "fp-1"},
        "request": {"message": text, "operation": "chat", "payload": {}},
        "config": {"values": {}},
        "accounting": {"envelope": {"request_id": f"req-{task_id}"}},
        "continuation": {"kind": kind, "explicit": kind != "followup"},
    }


@pytest.mark.asyncio
async def test_redis_continuation_source_preserves_order_and_count():
    redis = _MiniRedis()
    source = RedisConversationContinuationSource(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
    )

    await source.publish(_task_payload(task_id="task-1", turn_id="turn-1", text="first"), kind="followup")
    await source.publish(_task_payload(task_id="task-2", turn_id="turn-2", text="second"), kind="steer", explicit=True)

    assert await source.pending_count() == 2

    first = await source.peek_next()
    assert first.payload["meta"]["task_id"] == "task-1"

    taken_first = await source.take_next()
    taken_second = await source.take_next()

    assert taken_first.payload["meta"]["task_id"] == "task-1"
    assert taken_second.payload["meta"]["task_id"] == "task-2"
    assert await source.pending_count() == 0

    await source.restore_taken(taken_second)
    assert await source.pending_count() == 1


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
        chat_core,
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
    monkeypatch.setattr(chat_core, "_load_active_registry", _load_registry)
    monkeypatch.setattr(chat_core, "build_envelope_from_session", lambda **_kwargs: _DummyEnvelope())
    monkeypatch.setattr(chat_core, "ChatCommunicator", _DummyCommunicator)


@pytest.mark.asyncio
async def test_busy_message_is_stored_as_followup(_patch_ingress_dependencies):
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
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    result = await process_chat_message(
        app=app,
        chat_queue_manager=_QueueManagerShouldNotRun(),
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={"conversation_id": "conv-1", "payload": {}},
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
    assert result.reason == "followup_accepted"
    assert result.continuation_kind == "followup"
    assert result.active_turn_id == "turn-active"
    assert result.target_turn_id is None
    assert result.queued_turn_id == result.turn_id
    assert result.live_owner_detected is False

    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
    )
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "followup"
    assert events[0].active_turn_id_at_ingress == "turn-active"
    assert not events[0].owner_turn_id
    assert result.event_id == events[0].message_id
    assert result.external_event_sequence == events[0].sequence
    assert events[0].task_payload["continuation"]["kind"] == "followup"


@pytest.mark.asyncio
async def test_busy_followup_ack_preserves_target_and_server_active_owner(_patch_ingress_dependencies):
    redis = _MiniRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
    )
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
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )

    result = await process_chat_message(
        app=app,
        chat_queue_manager=_QueueManagerShouldNotRun(),
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={
            "conversation_id": "conv-1",
            "target_turn_id": "turn-client-visible",
            "payload": {},
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
    assert result.reason == "followup_accepted"
    assert result.active_turn_id == "turn-active"
    assert result.target_turn_id == "turn-client-visible"
    assert result.queued_turn_id == result.turn_id
    assert result.live_owner_detected is True

    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].target_turn_id == "turn-client-visible"
    assert events[0].active_turn_id_at_ingress == "turn-active"
    assert events[0].owner_turn_id == "turn-active"
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
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    result = await process_chat_message(
        app=app,
        chat_queue_manager=_QueueManagerShouldNotRun(),
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={"conversation_id": "conv-1", "payload": {}},
        message_text="followup text",
        raw_attachments=[
            chat_core.RawAttachment(
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
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
    )
    events = await source.read_since(0)
    assert len(events) == 1
    payload = events[0].task_payload["request"]["payload"]
    attachments = payload.get("attachments") or []
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "notes.txt"
    assert attachments[0]["hosted_uri"] == "file:///tmp/turn-active/notes.txt"
    assert attachments[0]["rn"] == "rn:turn-active:notes.txt"
    assert "base64" not in attachments[0]
    assert "text" not in attachments[0]


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
        fingerprint="fp-1",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    result = await process_chat_message(
        app=app,
        chat_queue_manager=_QueueManagerShouldNotRun(),
        chat_comm=_DummyRelay(),
        session=session,
        request_context=SimpleNamespace(user_utc_offset_min=None),
        message_data={"conversation_id": "conv-1", "payload": {}, "steer": True},
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
    assert result.reason == "steer_accepted"
    assert result.continuation_kind == "steer"

    source = build_conversation_external_event_source(
        redis=redis,
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
    )
    events = await source.read_since(0)
    assert len(events) == 1
    assert events[0].kind == "steer"
    assert events[0].task_payload["request"]["message"] == ""
    assert events[0].task_payload["continuation"]["target_turn_id"] is None

import json
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.api.ingress.chat_core as chat_core
from kdcube_ai_app.apps.chat.api.ingress.chat_core import IngressConfig, process_chat_message
from kdcube_ai_app.apps.chat.continuations import RedisConversationContinuationSource
from kdcube_ai_app.auth.sessions import UserType


class _MiniRedis:
    def __init__(self):
        self.lists = {}
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


@pytest.fixture
def _patch_ingress_dependencies(monkeypatch):
    _DummyCommunicator.service_events = []
    async def _load_registry(*_args, **_kwargs):
        return SimpleNamespace(
            default_bundle_id="bundle.demo",
            bundles={"bundle.demo": SimpleNamespace(id="bundle.demo")},
        )
    monkeypatch.setattr(chat_core, "get_settings", lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"))
    monkeypatch.setattr(chat_core, "_load_registry_from_redis", _load_registry)
    monkeypatch.setattr(chat_core, "build_envelope_from_session", lambda **_kwargs: _DummyEnvelope())
    monkeypatch.setattr(chat_core, "ChatCommunicator", _DummyCommunicator)


@pytest.mark.asyncio
async def test_busy_message_is_stored_as_followup(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
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

    mailbox_key = "tenant-a:project-a:kdcube:chat:conversation:mailbox:conv-1"
    assert len(redis.lists[mailbox_key]) == 1
    envelope = json.loads(redis.lists[mailbox_key][0])
    assert envelope["kind"] == "followup"
    assert envelope["active_turn_id"] == "turn-active"
    assert envelope["payload"]["continuation"]["kind"] == "followup"


@pytest.mark.asyncio
async def test_busy_blank_explicit_steer_is_stored(_patch_ingress_dependencies):
    redis = _MiniRedis()
    app = SimpleNamespace(state=SimpleNamespace(
        redis_async=redis,
        conversation_browser=_BusyConversationBrowser(),
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

    mailbox_key = "tenant-a:project-a:kdcube:chat:conversation:mailbox:conv-1"
    envelope = json.loads(redis.lists[mailbox_key][0])
    assert envelope["kind"] == "steer"
    assert envelope["payload"]["request"]["message"] == ""
    assert envelope["payload"]["continuation"]["target_turn_id"] is None

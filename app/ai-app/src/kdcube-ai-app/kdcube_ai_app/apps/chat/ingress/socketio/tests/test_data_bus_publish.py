from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.federated import issue_federated_data_bus_token
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.apps.chat.ingress.ingress_core import GatewayCheckResult, IngressResult
from kdcube_ai_app.apps.chat.ingress.socketio import chat as socket_chat
from kdcube_ai_app.apps.chat.ingress.socketio.data_bus import publish as pub
from kdcube_ai_app.apps.chat.ingress.socketio.data_bus.publish import DataBusSocketIOIngress
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusHandlerSpec


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.values = {}
        self._next_id = 1

    async def xadd(self, key, fields, maxlen=None, approximate=True):
        stream_id = f"1-{self._next_id}"
        self._next_id += 1
        self.streams.setdefault(key, []).append((stream_id, dict(fields)))
        return stream_id

    async def setex(self, key, ttl, value):
        del ttl
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}

    async def get_or_create_session(self, context, user_type, user_data):
        session = UserSession(
            session_id=f"session-{user_data['user_id']}",
            user_type=user_type,
            fingerprint=context.get_fingerprint(),
            user_id=user_data["user_id"],
            username=user_data.get("username"),
            email=user_data.get("email"),
            roles=list(user_data.get("roles") or []),
            permissions=list(user_data.get("permissions") or []),
            timezone="UTC",
            request_context=context,
        )
        self.sessions[session.session_id] = session
        return session

    async def get_session_by_id(self, session_id):
        return self.sessions.get(session_id)


class FakeSocketServer:
    def __init__(self):
        self.saved_sessions = {}
        self.rooms = []
        self.emitted = []

    async def save_session(self, sid, data):
        self.saved_sessions[sid] = data

    async def enter_room(self, sid, room):
        self.rooms.append((sid, room))

    async def emit(self, event, data, to=None, room=None):
        self.emitted.append((event, data, to, room))


class FakeComm:
    def __init__(self):
        self.acquired = []

    async def acquire_session_channel(self, session_id, callback, tenant, project):
        del callback
        self.acquired.append((session_id, tenant, project))


def _socket_session():
    return {
        "tenant": "tenant-a",
        "project": "project-a",
        "user_session": {
            "session_id": "session-1",
            "user_type": "registered",
            "fingerprint": "fp",
            "user_id": "user-1",
            "username": "user",
            "roles": [],
            "permissions": [],
            "timezone": "UTC",
        },
    }


def _prompt_event(text: str = "hello") -> dict:
    return {
        "type": "event.user.prompt",
        "event_source_id": "event.user.prompt",
        "reactive": True,
        "payload": {
            "mime": "text/plain",
            "event": {"text": text},
        },
    }


def _manifest():
    return SimpleNamespace(
        allowed_roles=(),
        allowed_roles_config=None,
        data_bus_handlers=(
            DataBusHandlerSpec(
                method_name="handle_patch",
                subject="task_tracker.canvas.patch",
                partition_by="object_ref",
                ordering="serial_per_partition",
                idempotency="required",
                user_types=("registered",),
            ),
        ),
    )


def _patch_data_bus_contract(monkeypatch, manifest):
    del manifest

    async def fake_load_registry(_redis, tenant, project):
        return SimpleNamespace(
            bundles={
                "task-tracker@1-0": SimpleNamespace(
                    path="/bundles/task-tracker@1-0",
                    module="entrypoint",
                    singleton=True,
                )
            }
        )

    async def fake_get_bundle_props(_redis, tenant, project, bundle_id):
        return {}

    monkeypatch.setattr(pub, "load_registry", fake_load_registry)
    monkeypatch.setattr(pub, "get_bundle_props", fake_get_bundle_props)
    monkeypatch.setattr(pub, "get_settings", lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"))


def _async_return(value):
    async def _inner(*args, **kwargs):
        del args, kwargs
        return value

    return _inner


def _async_noop():
    async def _inner(*args, **kwargs):
        del args, kwargs
        return None

    return _inner


@pytest.mark.asyncio
async def test_data_bus_publish_accepts_messages_into_bundle_stream(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    _patch_data_bus_contract(monkeypatch, _manifest())

    ingress = DataBusSocketIOIngress(app=app)
    ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=_socket_session(),
        data={
            "schema": "kdcube.data_bus.ingress.v1",
            "bundle_id": "task-tracker@1-0",
            "messages": [
                {
                    "message_id": "m1",
                    "subject": "task_tracker.canvas.patch",
                    "object_ref": "canvas:main",
                    "idempotency_key": "op-1",
                    "payload": {"base_revision": 1, "operations": []},
                }
            ],
        },
    )

    assert ack["status"] == "accepted"
    assert ack["accepted"] == [{"message_id": "m1", "stream_id": "1-1"}]
    assert ack["rejected"] == []
    stream_key = "kdcube:data-bus:tenant-a:project-a:task-tracker@1-0:messages"
    assert stream_key in redis.streams
    record = json.loads(redis.streams[stream_key][0][1]["json"])
    assert record["schema"] == "kdcube.data_bus.message.v1"
    assert record["subject"] == "task_tracker.canvas.patch"
    assert record["reply"] == {
        "transport": "socketio",
        "session_id": "session-1",
        "socket_id": "socket-1",
    }
    assert "external_events" not in record


@pytest.mark.asyncio
async def test_data_bus_publish_anonymous_threshold_handler_accepts_platform_registered_session(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    manifest = SimpleNamespace(
        allowed_roles=(),
        allowed_roles_config=None,
        data_bus_handlers=(
            DataBusHandlerSpec(
                method_name="handle_echo",
                subject="versatile.echo",
                idempotency="required",
                user_types=("anonymous",),
            ),
        ),
    )
    _patch_data_bus_contract(monkeypatch, manifest)

    ingress = DataBusSocketIOIngress(app=app)
    ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=_socket_session(),
        data={
            "schema": "kdcube.data_bus.ingress.v1",
            "bundle_id": "task-tracker@1-0",
            "messages": [
                {
                    "message_id": "m1",
                    "subject": "versatile.echo",
                    "object_ref": "probe:memory",
                    "idempotency_key": "echo-1",
                    "payload": {"source": "platform-widget"},
                }
            ],
        },
    )

    assert ack["status"] == "accepted"
    assert ack["rejected"] == []


@pytest.mark.asyncio
async def test_data_bus_publish_queues_unknown_subject_for_proc_side_rejection(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    _patch_data_bus_contract(monkeypatch, _manifest())

    ingress = DataBusSocketIOIngress(app=app)
    ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=_socket_session(),
        data={
            "bundle_id": "task-tracker@1-0",
            "messages": [
                {
                    "message_id": "m1",
                    "subject": "unknown.subject",
                    "payload": {},
                }
            ],
        },
    )

    assert ack["status"] == "accepted"
    assert ack["accepted"] == [{"message_id": "m1", "stream_id": "1-1"}]
    assert ack["rejected"] == []
    stream_key = "kdcube:data-bus:tenant-a:project-a:task-tracker@1-0:messages"
    record = json.loads(redis.streams[stream_key][0][1]["json"])
    assert record["subject"] == "unknown.subject"


@pytest.mark.asyncio
async def test_data_bus_publish_rejects_subject_outside_federated_token_scope(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    manifest = SimpleNamespace(
        allowed_roles=(),
        allowed_roles_config=None,
        data_bus_handlers=(
            DataBusHandlerSpec(method_name="handle_known", subject="known.subject"),
            DataBusHandlerSpec(method_name="handle_other", subject="other.subject"),
        ),
    )
    _patch_data_bus_contract(monkeypatch, manifest)

    socket_session = _socket_session()
    socket_session["federated_claims"] = {"allowed_subjects": ["known.subject"]}

    ingress = DataBusSocketIOIngress(app=app)
    ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=socket_session,
        data={
            "bundle_id": "task-tracker@1-0",
            "messages": [
                {
                    "message_id": "m1",
                    "subject": "other.subject",
                    "payload": {},
                }
            ],
        },
    )

    assert ack["status"] == "rejected"
    assert ack["accepted"] == []
    assert "subject is not allowed by federated token" in ack["rejected"][0]["error"]
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_data_bus_publish_rejects_bundle_outside_federated_token_scope(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    manifest = SimpleNamespace(
        allowed_roles=(),
        allowed_roles_config=None,
        data_bus_handlers=(
            DataBusHandlerSpec(method_name="handle_known", subject="known.subject"),
        ),
    )
    _patch_data_bus_contract(monkeypatch, manifest)

    socket_session = _socket_session()
    socket_session["federated_claims"] = {
        "bundle_id": "task-tracker@1-0",
        "allowed_subjects": ["known.subject"],
    }

    ingress = DataBusSocketIOIngress(app=app)
    ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=socket_session,
        data={
            "bundle_id": "other-bundle@1-0",
            "messages": [
                {
                    "message_id": "m1",
                    "subject": "known.subject",
                    "payload": {},
                }
            ],
        },
    )

    assert ack["status"] == "rejected"
    assert ack["accepted"] == []
    assert ack["rejected"][0]["error"] == "bundle_id is not allowed by federated token"
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_socketio_chat_message_and_data_bus_publish_coexist_without_cross_routing(monkeypatch):
    redis = FakeRedis()
    app = SimpleNamespace(state=SimpleNamespace(redis_async=redis))
    _patch_data_bus_contract(monkeypatch, _manifest())

    captured_chat: dict[str, dict] = {}

    async def fake_process_chat_message(**kwargs):
        message_data = dict(kwargs["message_data"])
        captured_chat["message_data"] = message_data
        assert message_data.get("external_events")
        assert "messages" not in message_data
        assert "subject" not in message_data
        return IngressResult(
            ok=True,
            task_id="chat-task-1",
            conversation_id=message_data["conversation_id"],
            turn_id=message_data["turn_id"],
            session_id="session-1",
            user_type="registered",
        )

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = app
    handler.gateway_adapter = SimpleNamespace()
    handler.chat_queue_manager = SimpleNamespace()
    handler.instance_id = "ingress-1"
    handler._comm = SimpleNamespace(emit_error=_async_noop())
    handler.sio = SimpleNamespace(get_session=_async_return(_socket_session()))

    monkeypatch.setattr(socket_chat, "build_ws_chat_request_context", lambda: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(socket_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))
    monkeypatch.setattr(socket_chat, "process_chat_message", fake_process_chat_message)

    chat_ack = await handler._handle_chat_message(
        "socket-1",
        {"external_events": [_prompt_event()]},
    )

    assert chat_ack["ok"] is True
    assert captured_chat["message_data"]["external_events"][0]["type"] == "event.user.prompt"
    assert redis.streams == {}

    ingress = DataBusSocketIOIngress(app=app)
    data_ack = await ingress.handle_publish(
        sid="socket-1",
        socket_session=_socket_session(),
        data={
            "schema": "kdcube.data_bus.ingress.v1",
            "bundle_id": "task-tracker@1-0",
            "messages": [
                {
                    "message_id": "m2",
                    "subject": "task_tracker.canvas.patch",
                    "object_ref": "canvas:main",
                    "idempotency_key": "op-2",
                    "payload": {"base_revision": 2, "operations": []},
                }
            ],
        },
    )

    assert data_ack["status"] == "accepted"
    stream_key = "kdcube:data-bus:tenant-a:project-a:task-tracker@1-0:messages"
    record = json.loads(redis.streams[stream_key][0][1]["json"])
    assert record["subject"] == "task_tracker.canvas.patch"
    assert record["payload"] == {"base_revision": 2, "operations": []}
    assert "external_events" not in record


@pytest.mark.asyncio
async def test_socketio_connect_accepts_scoped_federated_data_bus_token(monkeypatch):
    monkeypatch.setenv("KDCUBE_FEDERATED_TOKEN_SECRET", "test-secret")
    redis = FakeRedis()
    session_manager = FakeSessionManager()
    app = SimpleNamespace(
        state=SimpleNamespace(
            redis_async=redis,
            gateway_adapter=SimpleNamespace(gateway=SimpleNamespace(session_manager=session_manager)),
        )
    )
    grant = await issue_federated_data_bus_token(
        request=SimpleNamespace(
            headers={"user-agent": "pytest"},
            client=SimpleNamespace(host="127.0.0.1"),
            app=app,
        ),
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        provider="telegram",
        provider_subject="42",
        user_id="telegram:42",
        user_type=UserType.REGISTERED,
        username="telegram-user",
    )

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = app
    handler.gateway_adapter = app.state.gateway_adapter
    handler.allowed_origins = ["https://app.example"]
    handler.sio = FakeSocketServer()
    handler._comm = FakeComm()
    handler._sid_to_session_id = {}
    handler._sid_to_tenant_project = {}
    handler._session_refcounts = {}

    ok = await handler._handle_connect(
        "socket-1",
        {
            "HTTP_ORIGIN": "https://app.example",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_USER_AGENT": "pytest",
        },
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "bundle_id": "task-tracker@1-0",
            "federated_token": grant.token,
        },
    )

    assert ok is True
    assert handler._sid_to_session_id["socket-1"] == grant.session.session_id
    assert handler.sio.rooms == [("socket-1", grant.session.session_id)]
    assert handler.sio.saved_sessions["socket-1"]["user_session"]["user_id"] == "telegram:42"
    assert handler.sio.saved_sessions["socket-1"]["bundle_id"] == "task-tracker@1-0"

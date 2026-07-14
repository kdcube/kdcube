# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.auth.sessions import UserType
from kdcube_ai_app.apps.chat.ingress import ingress_core
from kdcube_ai_app.apps.chat.ingress.ingress_core import GatewayCheckResult, IngressResult
from kdcube_ai_app.apps.chat.ingress.sse import chat as sse_chat
from kdcube_ai_app.apps.chat.ingress.socketio import chat as socket_chat


class _ConversationBrowserExists:
    async def conversation_exists(self, **kwargs):
        del kwargs
        return True


class _ConversationBrowserMissing:
    async def conversation_exists(self, **kwargs):
        del kwargs
        return False


class _ConversationStateIndex:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def get_conversation_state_row(self, **kwargs):
        self.calls.append(kwargs)
        return self.row


class _ConversationBrowserStateOnly:
    def __init__(self):
        self.idx = _ConversationStateIndex(row={"state": "in_progress"})

    async def conversation_exists(self, **kwargs):
        del kwargs
        return False


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


def test_resolve_ingress_conversation_id_generates_uuid_when_missing():
    app = SimpleNamespace(state=SimpleNamespace())
    session = SimpleNamespace(user_id="user-1", fingerprint="fp-1")
    message_data = {}

    conversation_id, created = asyncio.run(
        ingress_core.resolve_ingress_conversation_id(
            app=app,
            session=session,
            message_data=message_data,
        )
    )

    assert created is True
    assert message_data["conversation_id"] == conversation_id
    assert str(uuid.UUID(conversation_id)) == conversation_id


def test_resolve_ingress_conversation_id_creates_unknown_supplied_id():
    # A client may mint a stable conversation id (e.g. to anchor a
    # per-conversation model pick) before its first turn. Such an id does not
    # yet resolve in the caller's namespace; it is created with the supplied id
    # rather than rejected.
    app = SimpleNamespace(state=SimpleNamespace(conversation_browser=_ConversationBrowserMissing()))
    session = SimpleNamespace(user_id="user-1", fingerprint="fp-1")
    message_data = {"conversation_id": "client-minted-conv"}

    conversation_id, created = asyncio.run(
        ingress_core.resolve_ingress_conversation_id(
            app=app,
            session=session,
            message_data=message_data,
        )
    )

    assert conversation_id == "client-minted-conv"
    assert created is True
    assert message_data["conversation_id"] == "client-minted-conv"


def test_resolve_ingress_conversation_id_accepts_state_row_before_turn_artifacts():
    browser = _ConversationBrowserStateOnly()
    app = SimpleNamespace(state=SimpleNamespace(conversation_browser=browser))
    session = SimpleNamespace(user_id="user-1", fingerprint="fp-1")
    message_data = {"conversation_id": "conv-in-flight"}

    conversation_id, created = asyncio.run(
        ingress_core.resolve_ingress_conversation_id(
            app=app,
            session=session,
            message_data=message_data,
        )
    )

    assert conversation_id == "conv-in-flight"
    assert created is False
    assert message_data["conversation_id"] == "conv-in-flight"
    assert browser.idx.calls == [
        {"user_id": "user-1", "conversation_id": "conv-in-flight"}
    ]


def test_sse_chat_ack_includes_server_generated_conversation_id(monkeypatch):
    session = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        fingerprint="fp-1",
        user_type=UserType.REGISTERED,
        username="user",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    captured: dict[str, object] = {}

    async def _fake_auth():
        return session

    async def _fake_process_chat_message(**kwargs):
        captured["message_data"] = dict(kwargs["message_data"])
        return IngressResult(
            ok=True,
            task_id="task-1",
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id=session.session_id,
            user_type=session.user_type.value,
        )

    monkeypatch.setattr(sse_chat, "require_auth", lambda *_args, **_kwargs: _fake_auth)
    monkeypatch.setattr(sse_chat, "build_sse_request_context", lambda request, session: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(sse_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))
    monkeypatch.setattr(sse_chat, "process_chat_message", _fake_process_chat_message)

    app = FastAPI()
    app.state.sse_hub = SimpleNamespace(_by_session={})
    router = sse_chat.create_sse_router(
        app=app,
        gateway_adapter=SimpleNamespace(gateway=SimpleNamespace(session_manager=None)),
        chat_queue_manager=SimpleNamespace(),
        instance_id="ingress-1",
        redis_url="redis://unused",
    )
    router.state = app.state
    app.include_router(router, prefix="/sse")

    client = TestClient(app)
    response = client.post(
        "/sse/chat",
        params={"stream_id": "stream-1"},
        json={"external_events": [_prompt_event()]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == captured["message_data"]["conversation_id"]
    assert payload["conversation_created"] is True
    assert str(uuid.UUID(payload["conversation_id"])) == payload["conversation_id"]


def test_sse_chat_creates_client_minted_conversation_id(monkeypatch):
    # Fresh conversation ("No turns yet") whose id was minted client-side after a
    # model pick was saved against it. The first send must succeed and create the
    # conversation with that same id (not 404, not a server-minted replacement).
    session = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        fingerprint="fp-1",
        user_type=UserType.REGISTERED,
        username="user",
        roles=[],
        permissions=[],
        timezone="UTC",
    )
    captured: dict[str, object] = {}

    async def _fake_auth():
        return session

    async def _fake_process_chat_message(**kwargs):
        captured["message_data"] = dict(kwargs["message_data"])
        return IngressResult(
            ok=True,
            task_id="task-1",
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id=session.session_id,
            user_type=session.user_type.value,
        )

    monkeypatch.setattr(sse_chat, "require_auth", lambda *_args, **_kwargs: _fake_auth)
    monkeypatch.setattr(sse_chat, "build_sse_request_context", lambda request, session: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(sse_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))
    monkeypatch.setattr(sse_chat, "process_chat_message", _fake_process_chat_message)

    app = FastAPI()
    app.state.sse_hub = SimpleNamespace(_by_session={})
    app.state.conversation_browser = _ConversationBrowserMissing()
    router = sse_chat.create_sse_router(
        app=app,
        gateway_adapter=SimpleNamespace(gateway=SimpleNamespace(session_manager=None)),
        chat_queue_manager=SimpleNamespace(),
        instance_id="ingress-1",
        redis_url="redis://unused",
    )
    router.state = app.state
    app.include_router(router, prefix="/sse")

    client = TestClient(app)
    response = client.post(
        "/sse/chat",
        params={"stream_id": "stream-1"},
        json={
            "conversation_id": "client-minted-conv",
            "external_events": [_prompt_event()],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "client-minted-conv"
    assert captured["message_data"]["conversation_id"] == "client-minted-conv"
    assert payload["conversation_created"] is True


def test_socket_chat_ack_includes_server_generated_conversation_id(monkeypatch):
    session = {
        "user_session": {
            "session_id": "sess-1",
            "user_type": "registered",
            "fingerprint": "fp-1",
            "user_id": "user-1",
            "username": "user",
            "roles": [],
            "permissions": [],
            "timezone": "UTC",
        }
    }

    async def _fake_process_chat_message(**kwargs):
        return IngressResult(
            ok=True,
            task_id="task-1",
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id="sess-1",
            user_type="registered",
        )

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = SimpleNamespace(state=SimpleNamespace())
    handler.gateway_adapter = SimpleNamespace()
    handler.chat_queue_manager = SimpleNamespace()
    handler.instance_id = "ingress-1"
    handler._comm = SimpleNamespace(emit_error=_async_noop())
    handler.sio = SimpleNamespace(get_session=_async_return(session))

    monkeypatch.setattr(socket_chat, "build_ws_chat_request_context", lambda: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(socket_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))
    monkeypatch.setattr(socket_chat, "process_chat_message", _fake_process_chat_message)

    ack = asyncio.run(
        handler._handle_chat_message(
            "sid-1",
            {"external_events": [_prompt_event()]},
        )
    )

    assert ack["ok"] is True
    assert ack["conversation_created"] is True
    assert str(uuid.UUID(ack["conversation_id"])) == ack["conversation_id"]


def test_socket_chat_creates_unknown_supplied_conversation_id(monkeypatch):
    # A client-minted conversation id that has no turns yet is accepted and
    # created (not rejected), so a first send after a saved model pick succeeds.
    session = {
        "user_session": {
            "session_id": "sess-1",
            "user_type": "registered",
            "fingerprint": "fp-1",
            "user_id": "user-1",
            "username": "user",
            "roles": [],
            "permissions": [],
            "timezone": "UTC",
        }
    }

    browser = _ConversationBrowserMissing()
    emitted_errors: list[str] = []

    async def _emit_error(*args, **kwargs):
        del args
        emitted_errors.append(str(kwargs.get("error")))

    async def _fake_process_chat_message(**kwargs):
        return IngressResult(
            ok=True,
            task_id="task-1",
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id="sess-1",
            user_type="registered",
        )

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = SimpleNamespace(state=SimpleNamespace(conversation_browser=browser))
    handler.gateway_adapter = SimpleNamespace()
    handler.chat_queue_manager = SimpleNamespace()
    handler.instance_id = "ingress-1"
    handler._comm = SimpleNamespace(emit_error=_emit_error)
    handler.sio = SimpleNamespace(get_session=_async_return(session))

    monkeypatch.setattr(socket_chat, "build_ws_chat_request_context", lambda: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(socket_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))
    monkeypatch.setattr(socket_chat, "process_chat_message", _fake_process_chat_message)

    ack = asyncio.run(
        handler._handle_chat_message(
            "sid-1",
            {
                "conversation_id": "client-minted-conv",
                "external_events": [_prompt_event()],
            },
        )
    )

    assert ack["ok"] is True
    assert ack["conversation_id"] == "client-minted-conv"
    assert ack["conversation_created"] is True
    assert emitted_errors == []


def test_socket_chat_rejects_legacy_nested_message_payload(monkeypatch):
    session = {
        "user_session": {
            "session_id": "sess-1",
            "user_type": "registered",
            "fingerprint": "fp-1",
            "user_id": "user-1",
            "username": "user",
            "roles": [],
            "permissions": [],
            "timezone": "UTC",
        }
    }

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = SimpleNamespace(state=SimpleNamespace())
    handler.gateway_adapter = SimpleNamespace()
    handler.chat_queue_manager = SimpleNamespace()
    handler.instance_id = "ingress-1"
    handler._comm = SimpleNamespace(emit_error=_async_noop())
    handler.sio = SimpleNamespace(get_session=_async_return(session))

    ack = asyncio.run(
        handler._handle_chat_message(
            "sid-1",
            {"message": {"message": "hello", "conversation_id": "conv-missing"}},
        )
    )

    assert ack["ok"] is False
    assert ack["status"] == 400
    assert ack["error_type"] == "invalid_chat_message_payload"
    assert "top-level external_events[]" in ack["error"]


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

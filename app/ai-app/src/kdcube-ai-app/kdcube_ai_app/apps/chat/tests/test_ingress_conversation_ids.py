# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.auth.sessions import UserType
from kdcube_ai_app.apps.chat.ingress import chat_core
from kdcube_ai_app.apps.chat.ingress.chat_core import GatewayCheckResult, IngressResult
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


def test_resolve_ingress_conversation_id_generates_uuid_when_missing():
    app = SimpleNamespace(state=SimpleNamespace())
    session = SimpleNamespace(user_id="user-1", fingerprint="fp-1")
    message_data = {}

    conversation_id, created = asyncio.run(
        chat_core.resolve_ingress_conversation_id(
            app=app,
            session=session,
            message_data=message_data,
        )
    )

    assert created is True
    assert message_data["conversation_id"] == conversation_id
    assert str(uuid.UUID(conversation_id)) == conversation_id


def test_resolve_ingress_conversation_id_rejects_unknown_supplied_id():
    app = SimpleNamespace(state=SimpleNamespace(conversation_browser=_ConversationBrowserMissing()))
    session = SimpleNamespace(user_id="user-1", fingerprint="fp-1")
    message_data = {"conversation_id": "conv-does-not-exist"}

    with pytest.raises(chat_core.HTTPException) as exc:
        asyncio.run(
            chat_core.resolve_ingress_conversation_id(
                app=app,
                session=session,
                message_data=message_data,
            )
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Conversation not found"


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
        json={"message": {"message": "hello"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == captured["message_data"]["conversation_id"]
    assert payload["conversation_created"] is True
    assert str(uuid.UUID(payload["conversation_id"])) == payload["conversation_id"]


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
            {"message": {"message": "hello"}},
        )
    )

    assert ack["ok"] is True
    assert ack["conversation_created"] is True
    assert str(uuid.UUID(ack["conversation_id"])) == ack["conversation_id"]


def test_socket_chat_rejects_unknown_supplied_conversation_id(monkeypatch):
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

    handler = socket_chat.SocketIOChatHandler.__new__(socket_chat.SocketIOChatHandler)
    handler.app = SimpleNamespace(state=SimpleNamespace(conversation_browser=browser))
    handler.gateway_adapter = SimpleNamespace()
    handler.chat_queue_manager = SimpleNamespace()
    handler.instance_id = "ingress-1"
    handler._comm = SimpleNamespace(emit_error=_emit_error)
    handler.sio = SimpleNamespace(get_session=_async_return(session))

    monkeypatch.setattr(socket_chat, "build_ws_chat_request_context", lambda: SimpleNamespace(user_utc_offset_min=None))
    monkeypatch.setattr(socket_chat, "run_gateway_checks", _async_return(GatewayCheckResult(kind="ok")))

    ack = asyncio.run(
        handler._handle_chat_message(
            "sid-1",
            {"message": {"message": "hello", "conversation_id": "conv-missing"}},
        )
    )

    assert ack["ok"] is False
    assert ack["status"] == 404
    assert ack["error"] == "Conversation not found"
    assert emitted_errors == ["Conversation not found"]


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

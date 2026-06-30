from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest


def _signed_init_data(
    *,
    bot_token: str,
    telegram_user_id: int = 2002,
    username: str = "elena",
    auth_date: int | None = None,
) -> str:
    user = json.dumps(
        {"id": telegram_user_id, "username": username, "first_name": username.title()},
        separators=(",", ":"),
    )
    params = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "query-1",
        "user": user,
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_telegram_update_summary_reports_attachment_shape():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import summarize_telegram_update

    summary = summarize_telegram_update(
        {
            "update_id": 43,
            "message": {
                "message_id": 8,
                "caption": "context file",
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 2002},
                "document": {
                    "file_id": "doc-file-id",
                    "file_name": "brief.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert summary["text"] == "context file"
    assert summary["attachments"] == [
        {
            "type": "document",
            "file_id": "doc-file-id",
            "file_name": "brief.pdf",
            "mime_type": "application/pdf",
        }
    ]


def test_telegram_user_admin_storage_maps_roles_and_conversations(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage

    storage = TelegramUserAdminStorage(tmp_path)
    anonymous = storage.resolve_telegram_user(
        telegram_user_id="2002",
        telegram_username="elena",
    )

    assert anonymous["role"] == "anonymous"
    assert anonymous["conversation_id"] == "telegram_user_2002"

    updated_anonymous = storage.resolve_telegram_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="elenaviter",
    )

    assert updated_anonymous["role"] == "anonymous"
    assert updated_anonymous["telegram_chat_id"] == "1001"
    assert updated_anonymous["telegram_username"] == "elenaviter"
    assert updated_anonymous["conversation_id"] == "telegram_chat_1001"

    registered = storage.upsert_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="elena",
        kdcube_user_id="user-a",
        role="registered",
        conversation_id="conv-main",
    )

    assert registered["role"] == "registered"
    assert registered["conversation_id"] == "conv-main"

    conversations = storage.create_conversation(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        title="Side thread",
    )
    assert conversations["active_conversation_id"].startswith("telegram_chat_1001_")
    conversation_ids = {item["conversation_id"] for item in conversations["conversations"]}
    assert "telegram_chat_1001" in conversation_ids
    assert "conv-main" in conversation_ids

    claim = storage.claim_telegram_update(update_id="42")
    assert claim["claimed"] is True
    completed = storage.complete_telegram_update(update_id="42", result={"stage": "done"})
    assert completed["status"] == "completed"
    assert storage.claim_telegram_update(update_id="42")["claimed"] is False


@pytest.mark.asyncio
async def test_telegram_submit_react_turn_sends_external_events(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressResult
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    storage = TelegramUserAdminStorage(tmp_path)
    storage.upsert_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="elena",
        kdcube_user_id="user-a",
        role="registered",
        conversation_id="conv-main",
    )
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id="test.telegram-submit",
    )
    async def _authority(_entrypoint=None, **kwargs):
        return {
            "actor_user_id": kwargs["actor_user_id"],
            "storage_user_id": kwargs["actor_user_id"],
            "economics_user_id": "user-a",
            "platform_user_id": "user-a",
            "platform_roles": ["kdcube:role:chat-user"],
            "platform_permissions": ["chat:run"],
            "identity_provider": "telegram",
            "platform_authority_resolved": True,
        }

    monkeypatch.setattr(user_admin, "_telegram_platform_authority", _authority)

    captured: dict[str, object] = {}

    async def _submit(**kwargs):
        captured["message_data"] = dict(kwargs["message_data"])
        return IngressResult(
            ok=True,
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id="conv-main",
            user_type="registered",
        )

    entrypoint = SimpleNamespace(
        BUNDLE_ID="test.telegram-submit",
        chat_submitter=SimpleNamespace(submit=_submit),
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
            meta=SimpleNamespace(instance_id="telegram-test"),
        ),
    )

    result = await user_admin.submit_react_turn(
        entrypoint,
        summary={
            "text": "hello from telegram",
            "chat_id": "1001",
            "user_id": "2002",
            "username": "elena",
            "update_id": "upd-1",
            "message_id": 42,
            "attachments": [],
        },
    )

    assert result["accepted"] is True
    message_data = captured["message_data"]
    events = message_data["external_events"]
    assert len(events) == 1
    assert events[0]["type"] == "event.user.prompt"
    assert events[0]["event_source_id"] == "telegram.user.prompt"
    assert events[0]["reactive"] is True
    assert events[0]["payload"] == {
        "mime": "text/plain",
        "event": {"text": "hello from telegram"},
    }


@pytest.mark.asyncio
async def test_telegram_inline_run_react_turn_derives_external_event_type(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    streamer_kwargs: list[dict] = []

    class _FakeStreamer:
        def __init__(self, **kwargs):
            streamer_kwargs.append(dict(kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def delivered_file_keys(self):
            return set()

        def progress_message_id(self):
            return None

        def progress_summary(self):
            return ""

    monkeypatch.setattr(user_admin, "TelegramActivityStreamer", _FakeStreamer)
    monkeypatch.setattr(user_admin, "bot_token", lambda entrypoint=None: "telegram-token")
    async def _authority(_entrypoint=None, **kwargs):
        return {
            "actor_user_id": kwargs["actor_user_id"],
            "storage_user_id": kwargs["actor_user_id"],
            "economics_user_id": "user-a",
            "platform_user_id": "user-a",
            "platform_roles": ["kdcube:role:chat-user"],
            "platform_permissions": ["chat:run"],
            "identity_provider": "telegram",
            "platform_authority_resolved": True,
        }

    monkeypatch.setattr(user_admin, "_telegram_platform_authority", _authority)

    storage = TelegramUserAdminStorage(tmp_path)
    storage.upsert_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="elena",
        kdcube_user_id="user-a",
        role="registered",
        conversation_id="conv-main",
    )
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id="test.telegram-inline",
    )

    class _Ctx:
        def __init__(self):
            self.routing = SimpleNamespace(session_id="", conversation_id="", turn_id="")
            self.user = SimpleNamespace(user_id="web-user", username="", user_type="anonymous")
            self.request = SimpleNamespace(request_id="request-1")
            self.actor = SimpleNamespace(tenant_id="tenant-a", project_id="project-a")

        def model_copy(self, deep: bool = False):
            del deep
            clone = _Ctx()
            clone.routing = SimpleNamespace(**vars(self.routing))
            clone.user = SimpleNamespace(**vars(self.user))
            clone.request = SimpleNamespace(**vars(self.request))
            clone.actor = SimpleNamespace(**vars(self.actor))
            return clone

    class _Entrypoint:
        BUNDLE_ID = "test.telegram-inline"
        comm_context = _Ctx()

        def rebind_request_context(self, *, comm_context):
            self.bound_context = comm_context

        def create_initial_state(self, payload):
            self.initial_payload = dict(payload)
            return dict(payload)

        def set_state(self, state):
            self.state = dict(state)

        def bundle_prop(self, path, default=None):
            if path == "integrations":
                return {
                    "telegram.test": {
                        "provider": "telegram",
                        "enabled": True,
                        "definition": {"stream_activity_display": False},
                    }
                }
            return default

        async def run(self, **params):
            self.run_params = dict(params)
            return {"final_answer": "ok", "followups": [], "turn_log": {"blocks": []}, "timeline": {"blocks": []}}

    entrypoint = _Entrypoint()
    result = await user_admin.run_react_turn(
        entrypoint,
        summary={
            "text": "/followup and then?",
            "chat_id": "1001",
            "user_id": "2002",
            "username": "elena",
            "update_id": "upd-2",
            "attachments": [],
        },
    )

    assert result["answer"] == "ok"
    events = entrypoint.run_params["external_events"]
    assert events[0]["type"] == "event.user.followup"
    assert events[0]["event_source_id"] == "telegram.user.followup"
    assert events[0]["payload"]["event"]["text"] == "and then?"
    assert entrypoint.initial_payload["message_kind"] == "followup"
    assert streamer_kwargs[0]["show_progress"] is False


@pytest.mark.asyncio
async def test_queued_telegram_delivery_uses_processor_payload_telegram(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    delivered: dict[str, object] = {}
    streamer_kwargs: list[dict] = []

    async def _deliver(**kwargs):
        delivered.update(kwargs)
        return {
            "messages": [{"kind": "text", "text": "Delivered answer"}],
            "telegram_delivery": {"ok": True, "sent": 1},
            "sent_after_progress": 1,
            "progress_final_appended": False,
        }

    class _FakeStreamer:
        def __init__(self, **kwargs):
            streamer_kwargs.append(dict(kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def delivered_file_keys(self):
            return set()

        def progress_message_id(self):
            return None

        def progress_summary(self):
            return ""

    monkeypatch.setattr(user_admin, "deliver_react_turn_to_telegram", _deliver)
    monkeypatch.setattr(user_admin, "TelegramActivityStreamer", _FakeStreamer)
    monkeypatch.setattr(user_admin, "bot_token", lambda entrypoint=None: "telegram-token")

    class _Entrypoint:
        BUNDLE_ID = "test.telegram-queued"
        config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="test.telegram-queued"))
        comm_context = SimpleNamespace(
            request=SimpleNamespace(
                payload={
                    "telegram": {
                        "chat_id": "1001",
                        "update_id": "upd-queued",
                        "turn_id": "turn_queued",
                    }
                },
                external_events=[
                    {
                        "type": "event.user.prompt",
                        "payload": {"event": {"text": "hello"}},
                    }
                ],
            ),
            routing=SimpleNamespace(turn_id="turn_queued"),
        )

        def bundle_prop(self, path, default=None):
            if path == "integrations":
                return {
                    "telegram.test": {
                        "provider": "telegram",
                        "enabled": True,
                        "definition": {"stream_activity_display": False},
                    }
                }
            return default

    async def _runner():
        return {
            "answer": "Queued answer",
            "turn_log": {
                "turn_id": "turn_queued",
                "blocks": [
                    {
                        "type": "assistant.completion",
                        "turn_id": "turn_queued",
                        "path": "ar:turn_queued.assistant.completion",
                        "text": "Queued answer",
                    }
                ],
            },
            "timeline": {"blocks": []},
        }

    result = await user_admin.run_with_queued_telegram_delivery(
        _Entrypoint(),
        runner=_runner,
    )

    assert result["telegram"]["queued_delivery"] is True
    assert result["telegram"]["chat_id"] == "1001"
    assert delivered["chat_id"] == "1001"
    assert delivered["update_id"] == "upd-queued"
    assert delivered["react_turn"]["answer"] == "Queued answer"
    assert delivered["react_turn"]["turn_log"]["turn_id"] == "turn_queued"
    assert streamer_kwargs[0]["show_progress"] is False


def test_telegram_user_admin_uses_bound_comm_bundle_id(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin
    from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, ExternalEventRouting
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context

    storage = TelegramUserAdminStorage(tmp_path)
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id="kdcube.copilot",
    )
    entrypoint = SimpleNamespace(
        BUNDLE_ID="kdcube.copilot",
        config=SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="kdcube.copilot")),
    )
    comm_context = ExternalEventPayload(
        routing=ExternalEventRouting(
            bundle_id="kdcube.copilot@2026-04-03-19-05",
            session_id="session-1",
        ),
    )

    with bind_current_request_context(comm_context):
        assert user_admin.payload(entrypoint)["bundle_id"] == "kdcube.copilot@2026-04-03-19-05"


@pytest.mark.asyncio
async def test_telegram_profile_creates_pending_admin_row(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth, widget_ops

    bundle_id = "test.telegram-profile-create"
    bot_token = "123456:test-token"
    storage = TelegramUserAdminStorage(tmp_path)

    widget_auth.configure_telegram_widget_auth(
        storage_for=lambda entrypoint: storage,
        bot_token=lambda entrypoint=None: bot_token,
        bundle_id=bundle_id,
    )
    widget_ops.configure_telegram_widget_ops(
        automation_operations_module=SimpleNamespace(),
        telegram_user_admin_module=SimpleNamespace(storage=lambda entrypoint: storage),
        telegram_widget_auth_module=widget_auth,
        webapp_module=SimpleNamespace(),
        bundle_id=bundle_id,
    )

    entrypoint = SimpleNamespace(
        BUNDLE_ID=bundle_id,
        bundle_prop=lambda path, default=None: default,
    )
    result = await widget_ops.profile(
        entrypoint,
        telegram_init_data=_signed_init_data(
            bot_token=bot_token,
            telegram_user_id=9999,
            username="pending",
        ),
    )

    assert result["telegram"]["role"] == "anonymous"
    assert result["telegram"]["allowed"] is False
    users = storage.list_users()
    assert len(users) == 1
    assert users[0]["telegram_user_id"] == "9999"
    assert users[0]["telegram_username"] == "pending"
    assert users[0]["role"] == "anonymous"


@pytest.mark.asyncio
async def test_telegram_widget_auth_accepts_async_bot_token(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth, widget_ops

    bundle_id = "test.telegram-async-token"
    token = "123456:test-token"
    storage = TelegramUserAdminStorage(tmp_path)

    async def bot_token(entrypoint=None):
        del entrypoint
        return token

    widget_auth.configure_telegram_widget_auth(
        storage_for=lambda entrypoint: storage,
        bot_token=bot_token,
        bundle_id=bundle_id,
    )
    widget_ops.configure_telegram_widget_ops(
        automation_operations_module=SimpleNamespace(),
        telegram_user_admin_module=SimpleNamespace(storage=lambda entrypoint: storage),
        telegram_widget_auth_module=widget_auth,
        webapp_module=SimpleNamespace(),
        bundle_id=bundle_id,
    )

    entrypoint = SimpleNamespace(
        BUNDLE_ID=bundle_id,
        bundle_prop=lambda path, default=None: default,
    )
    result = await widget_ops.profile(
        entrypoint,
        telegram_init_data=_signed_init_data(
            bot_token=token,
            telegram_user_id=1111,
            username="async-token",
        ),
    )

    assert result["ok"] is True
    assert result["telegram"]["user_id"] == "1111"
    users = storage.list_users()
    assert len(users) == 1
    assert users[0]["telegram_user_id"] == "1111"
    assert users[0]["telegram_username"] == "async-token"


@pytest.mark.asyncio
async def test_telegram_admin_approval_preserves_chat_id_and_notifies(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    bundle_id = "test.telegram-admin-approval"
    storage = TelegramUserAdminStorage(tmp_path)
    storage.resolve_telegram_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="pending",
    )
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id=bundle_id,
    )
    entrypoint = SimpleNamespace(BUNDLE_ID=bundle_id)

    result = user_admin.upsert(
        entrypoint,
        telegram_user_id="2002",
        role="admin",
    )

    assert result["user"]["telegram_chat_id"] == "1001"
    assert result["user"]["telegram_username"] == "pending"
    assert result["approval_transition"] == {
        "from_role": "anonymous",
        "to_role": "admin",
        "approved": True,
    }

    sent = {}

    async def _fake_send(*, bot_token, chat_id, messages):
        sent["bot_token"] = bot_token
        sent["chat_id"] = chat_id
        sent["messages"] = messages
        return {"ok": True, "sent": len(messages)}

    monkeypatch.setattr(user_admin, "bot_token", lambda entrypoint=None: "token")
    monkeypatch.setattr(user_admin, "send_telegram_messages", _fake_send)

    notification = await user_admin.notify_access_change(entrypoint, result=result)

    assert notification["ok"] is True
    assert notification["sent"] == 1
    assert sent["chat_id"] == "1001"
    assert "admin permissions" in sent["messages"][0].text


@pytest.mark.asyncio
async def test_hydrate_telegram_attachments_downloads_file(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    def _fake_api_json(**kwargs):
        assert kwargs["method"] == "getFile"
        assert kwargs["data"] == {"file_id": "photo-file-id"}
        return {"ok": True, "result": {"file_path": "photos/file_1.jpg", "file_size": 8}}

    def _fake_download(**kwargs):
        assert kwargs["file_path"] == "photos/file_1.jpg"
        return b"JPEGDATA"

    monkeypatch.setattr(telegram, "_telegram_api_json", _fake_api_json)
    monkeypatch.setattr(telegram, "_download_telegram_file", _fake_download)

    result = await telegram.hydrate_telegram_attachments(
        attachments=[{"type": "photo", "file_id": "photo-file-id", "mime_type": "image/jpeg"}],
        bot_token="bot-token",
        message_id=8,
    )

    assert result[0]["filename"] == "file_1.jpg"
    assert result[0]["mime"] == "image/jpeg"
    assert result[0]["size_bytes"] == 8
    assert base64.b64decode(result[0]["base64"]) == b"JPEGDATA"


def test_telegram_renderer_prefers_timeline_final_answer():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    messages = render_telegram_messages_from_timeline(
        timeline={
            "blocks": [
                {"path": "tc:turn_1.notes", "text": "internal note"},
                {"path": "tc:turn_1.react.final_answer.0", "text": "Task created."},
            ]
        },
        react_turn={"answer": "fallback"},
    )

    assert [item.as_dict() for item in messages] == [
        {"kind": "text", "text": "Task created.", "files": [], "parse_mode": "HTML"}
    ]


def test_telegram_renderer_preserves_multiple_timeline_answers():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    messages = render_telegram_messages_from_timeline(
        timeline={
            "blocks": [
                {"path": "tc:turn_1.react.final_answer.0", "text": "First response."},
                {"path": "tc:turn_1.notes", "text": "internal note"},
                {"path": "tc:turn_1.react.final_answer.1", "text": "Follow-up response."},
            ]
        },
    )

    assert [item.text for item in messages] == ["First response.", "Follow-up response."]


def test_telegram_renderer_ignores_memsearch_answer_snippets_when_react_answer_is_available():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    messages = render_telegram_messages_from_timeline(
        timeline={
            "turn_id": "turn_current",
            "blocks": [
                {
                    "type": "react.tool.result",
                    "turn_id": "turn_current",
                    "path": "ar:turn_old.assistant.completion",
                    "text": "Old answer returned by react.memsearch.",
                    "meta": {"turn_id": "turn_current"},
                },
                {
                    "type": "assistant.completion",
                    "turn_id": "turn_current",
                    "path": "ar:turn_current.assistant.completion",
                    "text": "Current final answer.",
                },
            ],
        },
        react_turn={"answer": "Current final answer."},
        prefer_react_turn_answer=True,
    )

    assert [item.text for item in messages] == ["Current final answer."]
    assert "Old answer" not in "\n".join(item.text for item in messages)


def test_telegram_renderer_splits_long_final_answers_in_order():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    first = "A" * 3200
    second = "B" * 3200
    messages = render_telegram_messages_from_timeline(
        timeline={"turn_id": "turn_current", "blocks": []},
        react_turn={"answer": f"{first}\n\n{second}"},
        prefer_react_turn_answer=True,
    )

    assert len(messages) == 2
    assert messages[0].text == first
    assert messages[1].text == second


def test_telegram_progress_final_card_appends_final_text_and_keeps_files():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import TelegramMessage
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import progress_final_card

    edit_text, parse_mode, remaining = progress_final_card(
        progress_summary="<b>Thinking</b>\n<blockquote>Loaded skill.</blockquote>",
        telegram_messages=[
            TelegramMessage(kind="text", text="Final answer.", parse_mode="HTML"),
            TelegramMessage(
                kind="document",
                text="report.pdf",
                files=({"filename": "report.pdf", "url": "https://example.test/report.pdf"},),
            ),
        ],
    )

    assert edit_text == (
        "<b>Thinking</b>\n<blockquote>Loaded skill.</blockquote>\n\n"
        "<b>Final response</b>\nFinal answer."
    )
    assert parse_mode == "HTML"
    assert len(remaining) == 1
    assert remaining[0].kind == "document"


def test_telegram_renderer_includes_sources_and_hosted_artifacts():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    messages = render_telegram_messages_from_timeline(
        timeline={
            "blocks": [
                {"path": "tc:turn_1.react.final_answer.0", "text": "Found the report [1]."},
                {
                    "path": "fi:turn_1.outputs/report.pdf",
                    "text": (
                        '{"artifact_path":"fi:turn_1.outputs/report.pdf",'
                        '"hosted_uri":"https://example.test/report.pdf",'
                        '"mime":"application/pdf","filename":"report.pdf",'
                        '"visibility":"external","description":"Generated report"}'
                    ),
                },
            ],
            "sources_pool": [
                {"sid": 1, "title": "Science News", "url": "https://example.test/science"},
                {
                    "source_type": "file",
                    "artifact_path": "fi:turn_1.outputs/report.pdf",
                    "hosted_uri": "https://example.test/report.pdf",
                    "mime": "application/pdf",
                    "filename": "report.pdf",
                },
            ],
        },
    )

    assert messages[0].text == "Found the report [1]."
    assert messages[1].text == "Sources:\n1. Science News - https://example.test/science"
    assert messages[2].kind == "document"
    assert messages[2].files[0]["filename"] == "report.pdf"
    assert messages[2].files[0]["url"] == "https://example.test/report.pdf"


def test_telegram_renderer_skips_already_delivered_artifacts():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    messages = render_telegram_messages_from_timeline(
        timeline={
            "blocks": [
                {"path": "tc:turn_1.react.final_answer.0", "text": "Found the report."},
                {
                    "path": "fi:turn_1.outputs/report.pdf",
                    "text": (
                        '{"artifact_path":"fi:turn_1.outputs/report.pdf",'
                        '"hosted_uri":"https://example.test/report.pdf",'
                        '"mime":"application/pdf","filename":"report.pdf",'
                        '"visibility":"external","description":"Generated report"}'
                    ),
                },
            ],
            "sources_pool": [],
        },
        exclude_file_keys={"https://example.test/report.pdf"},
    )

    assert [message.kind for message in messages] == ["text"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_sends_timeline_notes():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ):
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "timeline_text", "text": "checking inbox", "index": 0, "completed": False},
                    "extra": {"artifact_name": "react.notes", "format": "markdown"},
                },
            }
        )
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "timeline_text", "text": "", "index": 1, "completed": True},
                    "extra": {"artifact_name": "react.notes", "format": "markdown"},
                },
            }
        )

    assert [message.text for message in sent] == ["<b>Notes</b>\n<blockquote>checking inbox</blockquote>"]
    assert sent[0].parse_mode == "HTML"


@pytest.mark.asyncio
async def test_telegram_activity_streamer_ignores_answer_deltas():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(comm=comm, bot_token="token", chat_id="chat", send_messages=_send):
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "answer", "text": "final answer", "index": 0, "completed": False},
                    "extra": {"artifact_name": "react.final_answer", "format": "markdown"},
                },
            }
        )

    assert sent == []


@pytest.mark.asyncio
async def test_telegram_activity_streamer_subscribes_to_relay_and_dedupes_local_echo():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeRelay:
        def __init__(self):
            self.callback = None
            self.released = False

        async def acquire_session_channel(self, session_id, tenant, project, *, callback=None):
            assert (session_id, tenant, project) == ("session-1", "tenant-1", "project-1")
            self.callback = callback

        def remove_listener(self, cb):
            if self.callback is cb:
                self.callback = None

        async def release_session_channel(self, session_id, tenant, project):
            assert (session_id, tenant, project) == ("session-1", "tenant-1", "project-1")
            self.released = True

    class _FakeComm:
        def __init__(self):
            self.listeners = []
            self.emitter = _FakeRelay()
            self.conversation = {"session_id": "session-1", "conversation_id": "conv-1", "turn_id": "turn-1"}
            self.service = {"tenant": "tenant-1", "project": "project-1"}
            self.tenant = "tenant-1"
            self.project = "project-1"

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit_local(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    envelope = {
        "type": "chat.delta",
        "conversation": {"session_id": "session-1", "conversation_id": "conv-1", "turn_id": "turn-1"},
        "event": {"agent": "react.decision"},
        "delta": {"marker": "timeline_text", "text": "same note", "index": 0, "completed": True},
        "extra": {"artifact_name": "react.notes", "format": "markdown"},
    }
    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ):
        await comm.emit_local({"event": "chat_delta", "data": envelope})
        await comm.emitter.callback({"event": "chat_delta", "data": envelope, "target_sid": None})

    assert [message.text for message in sent] == ["<b>Notes</b>\n<blockquote>same note</blockquote>"]
    assert sent[0].parse_mode == "HTML"
    assert comm.emitter.released is True


@pytest.mark.asyncio
async def test_telegram_activity_streamer_filters_by_turn_id():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent_a = []
    sent_b = []

    async def _send_a(messages):
        sent_a.extend(messages)
        return {"ok": True}

    async def _send_b(messages):
        sent_b.extend(messages)
        return {"ok": True}

    def _event(turn_id, text):
        return {
            "event": "chat_delta",
            "data": {
                "type": "chat.delta",
                "conversation": {"session_id": "conv-1", "conversation_id": "conv-1", "turn_id": turn_id},
                "event": {"agent": "react.decision"},
                "delta": {"marker": "timeline_text", "text": text, "index": 0, "completed": True},
                "extra": {"artifact_name": "react.notes", "format": "markdown"},
            },
        }

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        turn_id="turn-a",
        send_messages=_send_a,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ):
        async with TelegramActivityStreamer(
            comm=comm,
            bot_token="token",
            chat_id="chat",
            turn_id="turn-b",
            send_messages=_send_b,
            quiet_seconds=0.01,
            min_send_interval_seconds=0.01,
        ):
            await comm.emit(_event("turn-a", "note from A"))
            await comm.emit(_event("turn-b", "note from B"))

    assert [message.text for message in sent_a] == ["<b>Notes</b>\n<blockquote>note from A</blockquote>"]
    assert [message.text for message in sent_b] == ["<b>Notes</b>\n<blockquote>note from B</blockquote>"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_sends_files_event_immediately():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(comm=comm, bot_token="token", chat_id="chat", send_messages=_send) as streamer:
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.files",
                    "event": {"step": "files", "status": "completed", "title": "Files Ready (1)"},
                    "data": {
                        "count": 1,
                        "items": [
                            {
                                "filename": "report.pdf",
                                "mime": "application/pdf",
                                "description": "Generated report",
                                "hosted_uri": "https://example.test/report.pdf",
                                "physical_path": "turn_1/outputs/report.pdf",
                                "artifact_path": "fi:turn_1.outputs/report.pdf",
                            }
                        ],
                    },
                },
            }
        )

    assert len(sent) == 1
    assert sent[0].kind == "document"
    assert sent[0].files[0]["filename"] == "report.pdf"
    assert streamer.delivered_file_keys() == {"https://example.test/report.pdf"}


@pytest.mark.asyncio
async def test_telegram_activity_streamer_does_not_dedupe_different_file_events():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, filename, hosted_uri):
            for cb in list(self.listeners):
                await cb(
                    {
                        "event": "chat_step",
                        "data": {
                            "type": "chat.files",
                            "event": {"step": "files", "status": "completed", "title": "Files Ready (1)"},
                            "data": {
                                "count": 1,
                                "items": [
                                    {
                                        "filename": filename,
                                        "mime": "application/pdf",
                                        "hosted_uri": hosted_uri,
                                        "artifact_path": f"fi:turn_1.outputs/{filename}",
                                    }
                                ],
                            },
                        },
                    }
                )

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(comm=comm, bot_token="token", chat_id="chat", send_messages=_send):
        await comm.emit("one.pdf", "https://example.test/one.pdf")
        await comm.emit("two.pdf", "https://example.test/two.pdf")

    assert [message.files[0]["filename"] for message in sent] == ["one.pdf", "two.pdf"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_sends_citations_event():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(comm=comm, bot_token="token", chat_id="chat", send_messages=_send):
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.citations",
                    "event": {"step": "citations", "status": "completed", "title": "Citations (1)"},
                    "data": {"count": 1, "items": [{"sid": 3, "title": "Example source", "url": "https://example.test/src"}]},
                },
            }
        )

    assert [message.text for message in sent] == ["Sources ready:\n3. Example source - https://example.test/src"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_sends_compaction_events():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, status, data):
            for cb in list(self.listeners):
                await cb(
                    {
                        "event": "chat_compaction",
                        "data": {
                            "type": "chat.compaction",
                            "conversation": {"turn_id": "turn_1"},
                            "event": {"step": "context.compaction", "status": status, "title": "Context Compaction"},
                            "data": data,
                        },
                    }
                )

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 88}}]}

    async def _edit(message_id, text, parse_mode=""):
        sent.append(type("Edit", (), {"text": text, "parse_mode": parse_mode})())
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        turn_id="turn_1",
        send_messages=_send,
        edit_text_message=_edit,
    ):
        await comm.emit("started", {"status": "started", "compaction_id": "c1", "kind": "current_turn_prefix"})
        await comm.emit("completed", {"status": "completed", "compaction_id": "c1", "kind": "current_turn_prefix", "compacted_tokens": 1200})

    assert sent[0].text == "Context compaction started (current turn prefix)."
    assert sent[1].text == (
        "Context compaction started (current turn prefix).\n\n"
        "Context compaction completed (compacted ~1,200 tokens; current turn prefix)."
    )


@pytest.mark.asyncio
async def test_telegram_activity_streamer_edits_one_progress_message():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []
    edits = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    async def _edit(message_id, text, parse_mode=""):
        edits.append({"message_id": message_id, "text": text, "parse_mode": parse_mode})
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        edit_text_message=_edit,
    ) as streamer:
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.step",
                    "event": {"step": "web_search", "status": "started", "title": "Web Search"},
                    "data": {},
                },
            }
        )
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.citations",
                    "event": {"step": "citations", "status": "completed", "title": "Citations (1)"},
                    "data": {"count": 1, "items": [{"sid": 3, "title": "Example source", "url": "https://example.test/src"}]},
                },
            }
        )
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "thinking", "text": "checking source quality", "index": 0, "completed": True},
                    "extra": {"artifact_name": "react.thinking", "format": "markdown"},
                },
            }
        )

    assert [message.text for message in sent] == ["Web Search: started"]
    assert sent[0].parse_mode == "HTML"
    assert edits == [
        {
            "message_id": 77,
            "text": "Web Search: started\n\nSources ready:\n3. Example source - https://example.test/src",
            "parse_mode": "HTML",
        },
        {
            "message_id": 77,
            "text": (
                "Web Search: started\n\nSources ready:\n3. Example source - https://example.test/src\n\n"
                "<b>Thinking</b>\n<blockquote>checking source quality</blockquote>"
            ),
            "parse_mode": "HTML",
        }
    ]
    assert streamer.progress_message_id() == 77
    assert streamer.progress_summary() == (
        "Web Search: started\n\nSources ready:\n3. Example source - https://example.test/src\n\n"
        "<b>Thinking</b>\n<blockquote>checking source quality</blockquote>"
    )


@pytest.mark.asyncio
async def test_telegram_activity_streamer_ignores_trivial_thinking_completion_delta():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ):
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "thinking", "text": "meaningful thinking", "index": 0, "completed": False},
                    "extra": {"artifact_name": "react.thinking.0", "format": "markdown"},
                },
            }
        )
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "thinking", "text": ".", "index": 1, "completed": True},
                    "extra": {"artifact_name": "react.thinking.1", "format": "markdown"},
                },
            }
        )

    assert [message.text for message in sent] == ["<b>Thinking</b>\n<blockquote>meaningful thinking</blockquote>"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_ignores_quoted_punctuation_only_thinking_delta():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ):
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "thinking", "text": "> .", "index": 0, "completed": True},
                    "extra": {"artifact_name": "react.thinking.0", "format": "markdown"},
                },
            }
        )

    assert sent == []


@pytest.mark.asyncio
async def test_telegram_activity_streamer_suppresses_internal_status_noise():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit_status(self, *, step, title, status="completed"):
            for cb in list(self.listeners):
                await cb(
                    {
                        "event": "chat_step",
                        "data": {
                            "type": "chat.step",
                            "event": {"step": step, "status": status, "title": title},
                            "data": {},
                        },
                    }
                )

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    comm = _FakeComm()
    async with TelegramActivityStreamer(comm=comm, bot_token="token", chat_id="chat", send_messages=_send):
        await comm.emit_status(step="user_context", title="User↔Conversation Linked")
        await comm.emit_status(step="prepare", title="Prepare")
        await comm.emit_status(step="event", title="Assistant Delta")
        await comm.emit_status(step="persist", title="Assistant Messages Persisted")
        await comm.emit_status(step="web_search", title="Web Search", status="started")

    assert [message.text for message in sent] == ["Web Search: started"]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_dedupes_repeated_running_code_status():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit_code_status(self, artifact_name):
            for cb in list(self.listeners):
                await cb(
                    {
                        "event": "chat_delta",
                        "data": {
                            "type": "chat.delta",
                            "event": {"agent": "react.tooling"},
                            "delta": {"marker": "subsystem", "text": "", "index": 0, "completed": False},
                            "extra": {
                                "artifact_name": artifact_name,
                                "sub_type": "code_exec.run",
                            },
                        },
                    }
                )

    sent = []
    edits = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    async def _edit(message_id, text, parse_mode=""):
        edits.append({"message_id": message_id, "text": text, "parse_mode": parse_mode})
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        edit_text_message=_edit,
    ):
        await comm.emit_code_status("exec.one")
        await comm.emit_code_status("exec.two")
        await comm.emit_code_status("exec.three")

    assert [message.text for message in sent] == ["Running code..."]
    assert sent[0].parse_mode == "HTML"
    assert edits == []


@pytest.mark.asyncio
async def test_telegram_activity_streamer_describes_web_and_code_payloads():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit_subsystem(self, *, sub_type, text, title="Tool"):
            for cb in list(self.listeners):
                await cb(
                    {
                        "event": "chat_delta",
                        "data": {
                            "type": "chat.delta",
                            "event": {"agent": "react.tooling", "title": title},
                            "delta": {"marker": "subsystem", "text": text, "index": 0, "completed": True},
                            "extra": {
                                "artifact_name": f"artifact.{sub_type.replace('.', '_')}",
                                "sub_type": sub_type,
                            },
                        },
                    }
                )

    sent = []
    edits = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True, "responses": [{"ok": True, "result": {"message_id": 77}}]}

    async def _edit(message_id, text, parse_mode=""):
        edits.append({"message_id": message_id, "text": text, "parse_mode": parse_mode})
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        edit_text_message=_edit,
    ):
        await comm.emit_subsystem(
            sub_type="web_search.filtered_results",
            text='{"queries":["reinforcement learning concepts"],"results":[{"url":"https://example.test/a"},{"url":"https://example.test/b"}]}',
        )
        await comm.emit_subsystem(
            sub_type="web_fetch.results",
            text='{"url":"https://example.test/rl/tutorial","title":"RL Tutorial"}',
        )
        await comm.emit_subsystem(
            sub_type="code_exec.program.name",
            text="rl_dashboard.py",
        )

    assert sent[0].text == "Web search: reinforcement learning concepts (2 results)"
    assert edits[-1]["text"] == (
        "Web search: reinforcement learning concepts (2 results)\n\n"
        "Fetched: example.test/rl/tutorial\n\n"
        "Code program: rl_dashboard.py"
    )


def test_telegram_renderer_reads_file_metadata_from_block_meta(tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import render_telegram_messages_from_timeline

    report = tmp_path / "tech_news.xlsx"
    report.write_bytes(b"xlsx")

    messages = render_telegram_messages_from_timeline(
        timeline={
            "blocks": [
                {"path": "tc:turn_1.react.final_answer.0", "text": "Generated the file."},
                {
                    "path": "fi:turn_1.outputs/tech_news.xlsx",
                    "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "meta": {
                        "artifact_path": "fi:turn_1.outputs/tech_news.xlsx",
                        "hosted_uri": report.as_uri(),
                        "physical_path": "turn_1/outputs/tech_news.xlsx",
                        "filename": "tech_news.xlsx",
                        "visibility": "external",
                    },
                },
            ],
            "sources_pool": [],
        },
    )

    assert messages[1].kind == "document"
    assert messages[1].files[0]["hosted_uri"] == report.as_uri()
    assert messages[1].files[0]["physical_path"] == "turn_1/outputs/tech_news.xlsx"


@pytest.mark.asyncio
async def test_telegram_renderer_unwraps_stored_timeline_and_uploads_current_turn_file(tmp_path):
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    report = tmp_path / "tech_news.xlsx"
    report.write_bytes(b"excel-bytes")
    malformed_file_uri = "file://" + str(report).lstrip("/")

    stored_timeline_record = {
        "turn_id": "turn_current",
        "payload": {
            "turn_id": "turn_current",
            "blocks": [
                {
                    "type": "assistant.completion",
                    "turn_id": "turn_old",
                    "path": "ar:turn_old.assistant.completion",
                    "text": "Old answer.",
                },
                {
                    "type": "react.tool.result",
                    "turn_id": "turn_old",
                    "path": "fi:turn_old.outputs/old.xlsx",
                    "meta": {
                        "hosted_uri": "file:///does/not/exist/old.xlsx",
                        "physical_path": "turn_old/outputs/old.xlsx",
                    },
                },
                {
                    "type": "assistant.completion",
                    "turn_id": "turn_current",
                    "path": "ar:turn_current.assistant.completion",
                    "text": "Here is the spreadsheet.",
                },
                {
                    "type": "react.tool.result",
                    "turn_id": "turn_current",
                    "path": "fi:turn_current.outputs/tech_news.xlsx",
                    "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "meta": {
                        "digest": json.dumps(
                            {
                                "artifact_path": "fi:turn_current.outputs/tech_news.xlsx",
                                "physical_path": "turn_current/outputs/tech_news.xlsx",
                                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                "description": "Excel report",
                                "visibility": "external",
                                "size_bytes": len(b"excel-bytes"),
                            }
                        ),
                        "hosted_uri": malformed_file_uri,
                    },
                },
            ],
            "sources_pool": [],
        },
    }

    messages = telegram.render_telegram_messages_from_timeline(timeline=stored_timeline_record)

    assert [message.kind for message in messages] == ["text", "document"]
    assert messages[0].text == "Here is the spreadsheet."
    assert messages[1].text == "Excel report"
    assert messages[1].files[0]["logical_path"] == "fi:turn_current.outputs/tech_news.xlsx"
    assert await telegram._file_item_bytes(messages[1].files[0]) == b"excel-bytes"


def test_telegram_text_normalizes_markdown_for_plain_bot_messages():
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    text = """### Gmail status

**Gmail access:** Working
**MCP sub-processor:** Failed with `claude_code_mcp_result_not_recorded`

| # | Sender | Subject |
|---|---|---|
| 1 | Fireworks AI | April Recap |
| 2 | Lovable | Payments |

See [the report](https://example.test/report).
"""

    rendered = telegram._telegram_text(text)

    assert "###" not in rendered
    assert "**" not in rendered
    assert "`" not in rendered
    assert "|---" not in rendered
    assert "Gmail status" in rendered
    assert "Gmail access: Working" in rendered
    assert "MCP sub-processor: Failed with claude_code_mcp_result_not_recorded" in rendered
    assert "1. Fireworks AI - April Recap" in rendered
    assert "2. Lovable - Payments" in rendered
    assert "the report (https://example.test/report)" in rendered


def test_telegram_html_preserves_inline_code_with_underscores():
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    rendered = telegram._markdown_to_telegram_html(
        "Task ID: `task_email-inbox-summary-every-4-hours_22bdc854`\n"
        "Identifier: task_email-inbox-summary-every-4-hours_22bdc854\n"
        "Style: _italic_"
    )

    assert "<code>task_email-inbox-summary-every-4-hours_22bdc854</code>" in rendered
    assert "Identifier: task_email-inbox-summary-every-4-hours_22bdc854" in rendered
    assert "<i>email-inbox-summary-every-4-hours</i>" not in rendered
    assert "Style: <i>italic</i>" in rendered


@pytest.mark.asyncio
async def test_live_telegram_timeline_delivery_probe():
    timeline_path = (
        os.environ.get("TASK_MEMO_TELEGRAM_TURN_LOG_JSON", "").strip()
        or os.environ.get("TASK_MEMO_TELEGRAM_TIMELINE_JSON", "").strip()
    )
    if not timeline_path:
        pytest.skip("set TASK_MEMO_TELEGRAM_TURN_LOG_JSON to probe a stored Telegram turn log")
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    timeline = json.loads(Path(timeline_path).read_text(encoding="utf-8"))
    messages = telegram.render_telegram_messages_from_timeline(timeline=timeline)
    file_messages = [message for message in messages if message.files]

    assert messages, "timeline should render at least one Telegram message"
    assert file_messages, "timeline should render at least one Telegram file message"
    for message in file_messages:
        assert await telegram._file_item_bytes(message.files[0]), message.files[0]

    if os.environ.get("TASK_MEMO_TELEGRAM_SEND") == "1":
        bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["TASK_MEMO_TELEGRAM_CHAT_ID"]
        result = await telegram.send_telegram_messages(bot_token=bot_token, chat_id=chat_id, messages=messages)
        assert result["ok"] is True


@pytest.mark.asyncio
async def test_telegram_delivery_without_token_is_explicit():
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import TelegramMessage, send_telegram_messages

    result = await send_telegram_messages(
        bot_token="",
        chat_id=1001,
        messages=[TelegramMessage(kind="text", text="Task created.")],
    )

    assert result == {
        "ok": False,
        "error": "telegram bot token is not configured",
        "sent": 0,
    }


@pytest.mark.asyncio
async def test_telegram_delivery_sends_hosted_document(monkeypatch):
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    calls = []

    def fake_post(*, bot_token, method, data):
        calls.append({"bot_token": bot_token, "method": method, "data": data})
        return {"ok": True, "result": {"message_id": len(calls)}}

    monkeypatch.setattr(telegram, "_post_telegram_form", fake_post)

    result = await telegram.send_telegram_messages(
        bot_token="token",
        chat_id=1001,
        messages=[
            telegram.TelegramMessage(
                kind="document",
                text="Generated report",
                files=({"url": "https://example.test/report.pdf", "filename": "report.pdf"},),
            )
        ],
    )

    assert result["ok"] is True
    assert calls == [
        {
            "bot_token": "token",
            "method": "sendDocument",
            "data": {
                "chat_id": "1001",
                "caption": "Generated report",
                "document": "https://example.test/report.pdf",
            },
        }
    ]


@pytest.mark.asyncio
async def test_telegram_delivery_uploads_local_document(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot as telegram

    report = tmp_path / "tech_news.xlsx"
    report.write_bytes(b"excel-bytes")
    calls = []

    def fake_multipart(*, bot_token, method, data, file_field, filename, file_data, mime_type):
        calls.append(
            {
                "bot_token": bot_token,
                "method": method,
                "data": data,
                "file_field": file_field,
                "filename": filename,
                "file_data": file_data,
                "mime_type": mime_type,
            }
        )
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram, "_post_telegram_multipart", fake_multipart)

    result = await telegram.send_telegram_messages(
        bot_token="token",
        chat_id=1001,
        messages=[
            telegram.TelegramMessage(
                kind="document",
                text="Generated report",
                files=(
                    {
                        "hosted_uri": report.as_uri(),
                        "filename": "tech_news.xlsx",
                        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    },
                ),
            )
        ],
    )

    assert result["ok"] is True
    assert calls == [
        {
            "bot_token": "token",
            "method": "sendDocument",
            "data": {"chat_id": "1001", "caption": "Generated report"},
            "file_field": "document",
            "filename": "tech_news.xlsx",
            "file_data": b"excel-bytes",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ]


@pytest.mark.asyncio
async def test_telegram_activity_streamer_show_progress_false_delivers_files_only():
    """show_progress=False keeps chat.files delivery but suppresses progress
    (chat.step / chat.delta) display."""
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

    class _FakeComm:
        def __init__(self):
            self.listeners = []

        def add_activity_listener(self, cb):
            self.listeners.append(cb)

        def remove_activity_listener(self, cb):
            self.listeners.remove(cb)

        async def emit(self, activity):
            for cb in list(self.listeners):
                await cb(activity)

    sent = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=_send,
        show_progress=False,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ) as streamer:
        # Suppressed: step/status progress.
        for index in range(1105):
            await comm.emit(
                {
                    "event": "chat_step",
                    "data": {
                        "type": "chat.step",
                        "event": {"step": f"gate-{index}", "status": "completed", "title": "Gate Completed"},
                    },
                }
            )
        # Suppressed: thinking/timeline delta.
        await comm.emit(
            {
                "event": "chat_delta",
                "data": {
                    "type": "chat.delta",
                    "event": {"agent": "react.decision"},
                    "delta": {"marker": "timeline_text", "text": "checking inbox", "index": 0, "completed": True},
                    "extra": {"artifact_name": "react.notes", "format": "markdown"},
                },
            }
        )
        # Suppressed: citations are progress metadata for Telegram, not file delivery.
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.citations",
                    "event": {"step": "citations", "status": "completed", "title": "Citations (1)"},
                    "data": {"count": 1, "items": [{"sid": 1, "title": "Source", "url": "https://example.test/src"}]},
                },
            }
        )
        # KEPT: file delivery.
        await comm.emit(
            {
                "event": "chat_step",
                "data": {
                    "type": "chat.files",
                    "event": {"step": "files", "status": "completed", "title": "Files Ready (1)"},
                    "data": {
                        "count": 1,
                        "items": [
                            {
                                "filename": "report.pdf",
                                "mime": "application/pdf",
                                "description": "Generated report",
                                "hosted_uri": "https://example.test/report.pdf",
                                "physical_path": "turn_1/outputs/report.pdf",
                                "artifact_path": "fi:turn_1.outputs/report.pdf",
                            }
                        ],
                    },
                },
            }
        )

    assert len(sent) == 1
    assert sent[0].kind == "document"
    assert sent[0].files[0]["filename"] == "report.pdf"
    assert streamer.delivered_file_keys() == {"https://example.test/report.pdf"}

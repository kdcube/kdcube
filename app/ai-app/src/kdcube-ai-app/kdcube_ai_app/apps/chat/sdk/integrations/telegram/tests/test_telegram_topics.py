from __future__ import annotations

from types import SimpleNamespace

import pytest
from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressResult
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import bot, stream, topics
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
    TelegramMessage,
    send_telegram_messages,
    summarize_telegram_update,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.chat_submit import (
    telegram_ingress_config,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.user_storage import (
    TelegramUserAdminStorage,
)


def test_topic_update_summary_exposes_routing_and_lifecycle_fields() -> None:
    summary = summarize_telegram_update(
        {
            "update_id": 41,
            "message": {
                "message_id": 9,
                "message_thread_id": 73,
                "is_topic_message": True,
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 2002, "username": "elena"},
                "forum_topic_created": {"name": "Boat Aurora"},
            },
        }
    )

    assert summary["message_thread_id"] == "73"
    assert summary["is_topic_message"] is True
    assert summary["topic_event"] == "forum_topic_created"
    assert summary["topic_name"] == "Boat Aurora"


def test_topic_conversations_are_stable_distinct_and_do_not_change_active_chat(
    tmp_path,
) -> None:
    storage = TelegramUserAdminStorage(tmp_path)
    storage.upsert_user(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        telegram_username="elena",
        kdcube_user_id="user-a",
        role="registered",
        conversation_id="conv-main",
    )

    first = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id=73,
        topic_name="Boat Aurora",
    )
    repeated = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id="73",
    )
    renamed = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id="73",
        topic_name="Boat Aurora II",
    )
    second = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id=74,
        topic_name="Boat Borealis",
    )
    unthreaded = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
    )

    assert first["conversation_id"] == "telegram_chat_1001_topic_73"
    assert repeated["conversation_id"] == first["conversation_id"]
    assert renamed["conversation_id"] == first["conversation_id"]
    assert renamed["conversation"]["title"] == "Boat Aurora II"
    assert second["conversation_id"] == "telegram_chat_1001_topic_74"
    assert second["conversation_id"] != first["conversation_id"]
    assert unthreaded["conversation_id"] == "conv-main"
    assert unthreaded["active_conversation_id"] == "conv-main"


def test_topic_conversation_id_preserves_supergroup_chat_identity() -> None:
    assert topics.telegram_topic_conversation_id(
        chat_id=-100123456,
        message_thread_id=88,
    ) == "telegram_chat_-100123456_topic_88"


def test_same_topic_coordinates_are_isolated_between_bot_integrations(
    tmp_path,
) -> None:
    storage = TelegramUserAdminStorage(tmp_path)

    first = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id=73,
        integration_id="telegram.first",
    )
    second = storage.resolve_conversation_for_message(
        telegram_user_id="2002",
        telegram_chat_id="1001",
        message_thread_id=73,
        integration_id="telegram.second",
    )

    assert first["conversation_id"] == (
        "telegram_integration_telegram_first_chat_1001_topic_73"
    )
    assert second["conversation_id"] == (
        "telegram_integration_telegram_second_chat_1001_topic_73"
    )
    assert first["conversation_id"] != second["conversation_id"]


@pytest.mark.asyncio
async def test_topic_message_uses_bound_conversation_and_ingress_metadata(
    tmp_path,
    monkeypatch,
) -> None:
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
        bundle_id="test.telegram-topics",
    )

    async def _authority(_entrypoint=None, **kwargs):
        return {
            "actor_user_id": kwargs["actor_user_id"],
            "storage_user_id": kwargs["actor_user_id"],
            "economics_user_id": "user-a",
            "platform_user_id": "user-a",
            "platform_roles": ["kdcube:role:registered"],
            "platform_permissions": ["chat:run"],
            "identity_provider": "telegram",
            "platform_authority_resolved": True,
        }

    monkeypatch.setattr(user_admin, "_telegram_platform_authority", _authority)
    captured: dict[str, object] = {}

    async def _submit(**kwargs):
        captured.update(kwargs)
        return IngressResult(
            ok=True,
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id=kwargs["message_data"]["conversation_id"],
            user_type="registered",
        )

    entrypoint = SimpleNamespace(
        BUNDLE_ID="test.telegram-topics",
        chat_submitter=SimpleNamespace(submit=_submit),
        bundle_prop=lambda path, default=None: "react-agent"
        if path == "surfaces.as_consumer.default_agent"
        else default,
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
            meta=SimpleNamespace(instance_id="telegram-test"),
        ),
    )

    result = await user_admin.submit_telegram_turn(
        entrypoint,
        summary={
            "text": "show this boat",
            "chat_id": "1001",
            "user_id": "2002",
            "username": "elena",
            "update_id": "upd-topic",
            "message_id": 42,
            "message_thread_id": "73",
            "is_topic_message": True,
            "integration_id": "telegram.test",
            "attachments": [],
        },
    )

    assert result["accepted"] is True
    assert result["topic_bound"] is True
    assert result["conversation_id"] == (
        "telegram_integration_telegram_test_chat_1001_topic_73"
    )
    assert captured["message_data"]["conversation_id"] == result["conversation_id"]
    assert captured["message_data"]["payload"]["telegram"]["message_thread_id"] == "73"
    assert captured["ingress"].metadata["message_thread_id"] == "73"


@pytest.mark.asyncio
async def test_topic_lifecycle_event_binds_without_starting_an_agent_turn(
    tmp_path,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    storage = TelegramUserAdminStorage(tmp_path)
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id="test.telegram-topic-events",
    )

    result = await user_admin.submit_telegram_turn(
        SimpleNamespace(BUNDLE_ID="test.telegram-topic-events"),
        summary={
            "text": "",
            "chat_id": "1001",
            "user_id": "2002",
            "username": "elena",
            "update_id": "upd-created",
            "message_id": 43,
            "message_thread_id": "73",
            "is_topic_message": True,
            "topic_event": "forum_topic_created",
            "topic_name": "Boat Aurora",
            "attachments": [],
        },
    )

    assert result["mode"] == "topic_event"
    assert result["topic_bound"] is True
    assert result["conversation_id"] == "telegram_chat_1001_topic_73"
    listing = storage.list_conversations(telegram_user_id="2002")
    topic = next(
        item
        for item in listing["conversations"]
        if item.get("message_thread_id") == "73"
    )
    assert topic["title"] == "Boat Aurora"


@pytest.mark.asyncio
async def test_topic_lifecycle_webhook_is_acknowledged_as_a_binding(
    tmp_path,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin

    storage = TelegramUserAdminStorage(tmp_path)
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id="test.telegram-topic-webhook",
    )

    result = await user_admin.handle_webhook(
        SimpleNamespace(BUNDLE_ID="test.telegram-topic-webhook"),
        update_id=44,
        message={
            "message_id": 45,
            "message_thread_id": 73,
            "is_topic_message": True,
            "chat": {"id": 1001, "type": "private"},
            "from": {"id": 2002, "username": "elena"},
            "forum_topic_created": {"name": "Boat Aurora"},
        },
    )

    assert result["ok"] is True
    assert result["stage"] == "topic-bound"
    assert result["chat_ingress"]["conversation_id"] == (
        "telegram_chat_1001_topic_73"
    )


@pytest.mark.asyncio
async def test_text_and_document_delivery_target_the_originating_topic(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def _post(*, bot_token, method, data):
        calls.append({"bot_token": bot_token, "method": method, "data": data})
        return {"ok": True, "result": {"message_id": len(calls)}}

    monkeypatch.setattr(bot, "_post_telegram_form", _post)
    result = await send_telegram_messages(
        bot_token="token",
        chat_id=1001,
        message_thread_id=73,
        messages=[
            TelegramMessage(kind="text", text="Working"),
            TelegramMessage(
                kind="document",
                text="Report",
                files=(
                    {
                        "url": "https://example.test/report.pdf",
                        "filename": "report.pdf",
                    },
                ),
            ),
        ],
    )

    assert result["ok"] is True
    assert [call["method"] for call in calls] == ["sendMessage", "sendDocument"]
    assert all(call["data"]["message_thread_id"] == "73" for call in calls)


@pytest.mark.asyncio
async def test_invalid_delivery_topic_fails_before_telegram_dispatch(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _post(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(bot, "_post_telegram_form", _post)
    result = await send_telegram_messages(
        bot_token="token",
        chat_id=1001,
        message_thread_id="not-a-topic",
        messages=[TelegramMessage(kind="text", text="Must stay bound")],
    )

    assert result == {
        "ok": False,
        "error": "telegram message thread id is invalid",
        "sent": 0,
    }
    assert calls == []


@pytest.mark.asyncio
async def test_progress_and_final_delivery_keep_the_topic(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def _send(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "sent": len(kwargs["messages"]), "responses": []}

    monkeypatch.setattr(stream, "send_telegram_messages", _send)
    activity = stream.TelegramActivityStreamer(
        comm=None,
        bot_token="token",
        chat_id=1001,
        message_thread_id=73,
    )
    await activity._send_text("Searching", reason="test")
    await stream.deliver_messages_preserving_progress_card(
        bot_token="token",
        chat_id=1001,
        message_thread_id=73,
        telegram_messages=[TelegramMessage(kind="text", text="Done")],
    )

    assert len(calls) == 2
    assert all(call["message_thread_id"] == "73" for call in calls)


@pytest.mark.asyncio
async def test_topic_management_helpers_call_the_bound_bot_api(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _post(*, bot_token, method, data):
        calls.append({"bot_token": bot_token, "method": method, "data": data})
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot, "_post_telegram_form", _post)

    assert (
        await topics.create_telegram_topic(
            bot_token="token",
            chat_id=1001,
            name="Boat Aurora",
        )
    )["ok"] is True
    assert (
        await topics.edit_telegram_topic(
            bot_token="token",
            chat_id=1001,
            message_thread_id=73,
            name="Boat Aurora II",
        )
    )["ok"] is True
    assert (
        await topics.delete_telegram_topic(
            bot_token="token",
            chat_id=1001,
            message_thread_id=73,
        )
    )["ok"] is True

    assert [call["method"] for call in calls] == [
        "createForumTopic",
        "editForumTopic",
        "deleteForumTopic",
    ]
    assert calls[1]["data"]["message_thread_id"] == "73"
    before = len(calls)
    invalid = await topics.delete_telegram_topic(
        bot_token="token",
        chat_id=1001,
        message_thread_id=0,
    )
    assert invalid["ok"] is False
    assert len(calls) == before


@pytest.mark.asyncio
async def test_topic_management_transport_failure_does_not_expose_exception_text(
    monkeypatch,
    caplog,
) -> None:
    secret_marker = "bot-token-secret-marker"

    def _fail(**kwargs):
        del kwargs
        raise RuntimeError(secret_marker)

    monkeypatch.setattr(bot, "_post_telegram_form", _fail)
    result = await topics.create_telegram_topic(
        bot_token="token",
        chat_id=1001,
        name="Boat Aurora",
    )

    assert result == {"ok": False, "error": "Telegram topic request failed."}
    assert secret_marker not in caplog.text


def test_ingress_config_retains_message_thread_id() -> None:
    ingress = telegram_ingress_config(
        chat_id="1001",
        update_id="41",
        message_id=9,
        message_thread_id=73,
    )

    assert ingress.metadata["message_thread_id"] == 73

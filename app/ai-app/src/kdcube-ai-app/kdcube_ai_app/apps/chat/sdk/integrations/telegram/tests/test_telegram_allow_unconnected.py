"""`allow_unconnected` decides whether an unlinked Telegram account may chat.

Default (prop absent or false): the turn is not submitted and the user gets the
connect prompt. With the prop set: the turn is submitted as an `external` user
carrying no platform roles or permissions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressResult
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin


async def _unlinked_authority(_entrypoint=None, **kwargs):
    return {
        "actor_user_id": kwargs["actor_user_id"],
        "storage_user_id": kwargs["actor_user_id"],
        "economics_user_id": "",
        "platform_user_id": "",
        "platform_roles": [],
        "platform_permissions": [],
        "identity_provider": "telegram",
        "platform_authority_resolved": False,
    }


def _entrypoint(bundle_id: str, definition: dict | None, submit):
    integrations = {
        "telegram.default": {
            "provider": "telegram",
            "enabled": True,
            "definition": dict(definition or {}),
        }
    }

    def bundle_prop(path, default=None):
        return integrations if path == "integrations" else default

    return SimpleNamespace(
        BUNDLE_ID=bundle_id,
        bundle_prop=bundle_prop,
        chat_submitter=SimpleNamespace(submit=submit),
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
            meta=SimpleNamespace(instance_id="telegram-test"),
        ),
    )


def _summary(update_id: str) -> dict:
    return {
        "text": "What is the forecast?",
        "chat_id": "1001",
        "user_id": "2002",
        "username": "elena",
        "update_id": update_id,
        "message_id": 42,
        "attachments": [],
    }


@pytest.fixture
def unlinked_user(tmp_path, monkeypatch):
    storage = TelegramUserAdminStorage(tmp_path)
    storage.resolve_telegram_user(telegram_user_id="2002", telegram_chat_id="1001", telegram_username="elena")
    monkeypatch.setattr(user_admin, "_telegram_platform_authority", _unlinked_authority)

    def _configure(bundle_id: str) -> None:
        user_admin.configure_telegram_user_admin(
            storage_factory=lambda entrypoint: storage,
            storage_root_or_error=lambda entrypoint: tmp_path,
            bundle_id=bundle_id,
        )

    return _configure


def _recording_submit(captured: dict):
    async def _submit(**kwargs):
        captured["session"] = kwargs["session"]
        return IngressResult(
            ok=True,
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id=kwargs["message_data"]["conversation_id"],
            user_type="external",
        )

    return _submit


@pytest.mark.parametrize("definition", [None, {"allow_unconnected": False}, {"allow_unconnected": "false"}])
async def test_unlinked_user_is_walled_by_default(unlinked_user, definition):
    bundle_id = "test.telegram-walled"
    unlinked_user(bundle_id)
    captured: dict = {}

    result = await user_admin.submit_telegram_turn(
        _entrypoint(bundle_id, definition, _recording_submit(captured)),
        summary=_summary("upd-walled"),
    )

    assert result["mode"] == "connection_required"
    assert result["reason"] == "telegram_platform_identity_unresolved"
    assert "session" not in captured


@pytest.mark.parametrize("flag", [True, "true", "yes"])
async def test_allow_unconnected_submits_unlinked_user_as_external(unlinked_user, flag):
    bundle_id = "test.telegram-open"
    unlinked_user(bundle_id)
    captured: dict = {}

    result = await user_admin.submit_telegram_turn(
        _entrypoint(bundle_id, {"allow_unconnected": flag}, _recording_submit(captured)),
        summary=_summary("upd-open"),
    )

    assert result["mode"] == "submitted"
    assert result["accepted"] is True
    session = captured["session"]
    assert session.user_type.value == "external"
    assert session.roles == []
    assert session.permissions == []


async def test_allow_unconnected_is_read_from_the_webhook_block(unlinked_user):
    bundle_id = "test.telegram-open-webhook"
    unlinked_user(bundle_id)
    captured: dict = {}

    result = await user_admin.submit_telegram_turn(
        _entrypoint(bundle_id, {"webhook": {"allow_unconnected": True}}, _recording_submit(captured)),
        summary=_summary("upd-open-webhook"),
    )

    assert result["mode"] == "submitted"
    assert captured["session"].user_type.value == "external"

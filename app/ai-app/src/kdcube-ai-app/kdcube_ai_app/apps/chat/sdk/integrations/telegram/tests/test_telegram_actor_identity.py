"""`actor_id_template` picks the namespace of Telegram actor user ids.

The default must stay byte-identical to the historical `telegram_{id}` form so
existing histories, storage scopes, and accounting rows keep their keys. A
configured template must reach every SDK site that builds or matches the id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressResult
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.hub import authenticators
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import TelegramUserAdminStorage
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin, widget_auth
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.identity import (
    DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE,
    format_telegram_actor_user_id,
    telegram_actor_user_id,
    validate_telegram_actor_id_template,
)
from kdcube_ai_app.apps.chat.sdk.integrations import delivery

CANONICAL = "internal:telegram:{telegram_user_id}"


def _entrypoint(bundle_id: str, definition: dict | None = None, **extra):
    integrations = {
        "telegram.default": {
            "provider": "telegram",
            "enabled": True,
            "definition": dict(definition or {}),
        }
    }

    def bundle_prop(path, default=None):
        return integrations if path == "integrations" else default

    return SimpleNamespace(BUNDLE_ID=bundle_id, bundle_prop=bundle_prop, **extra)


def test_default_template_is_byte_identical_to_the_historical_form():
    assert DEFAULT_TELEGRAM_ACTOR_ID_TEMPLATE == "telegram_{telegram_user_id}"
    assert format_telegram_actor_user_id("2002") == "telegram_2002"
    assert telegram_actor_user_id(_entrypoint("test.identity-default"), "2002") == "telegram_2002"
    assert telegram_actor_user_id(_entrypoint("test.identity-default", {"actor_id_template": ""}), 2002) == "telegram_2002"


def test_configured_template_is_applied():
    entrypoint = _entrypoint("test.identity-canonical", {"actor_id_template": CANONICAL})
    assert telegram_actor_user_id(entrypoint, "2002") == "internal:telegram:2002"


def test_template_under_webhook_block_is_applied():
    entrypoint = _entrypoint("test.identity-webhook", {"webhook": {"actor_id_template": CANONICAL}})
    assert telegram_actor_user_id(entrypoint, "2002") == "internal:telegram:2002"


def test_empty_subject_yields_empty_id():
    assert format_telegram_actor_user_id("", CANONICAL) == ""


@pytest.mark.parametrize(
    "template",
    [
        "telegram",  # no placeholder: every user would collapse to one id
        "tg_{user}",  # unknown field
        "tg_{telegram_user_id}_{chat_id}",  # extra field
        "tg_{telegram_user_id!r}",  # conversion
        "tg_{telegram_user_id:>10}",  # format spec
        "tg_{",  # malformed
        42,
    ],
)
def test_unusable_templates_fail_loudly(template):
    with pytest.raises(ValueError):
        validate_telegram_actor_id_template(template)
    with pytest.raises(ValueError):
        telegram_actor_user_id(_entrypoint("test.identity-bad", {"actor_id_template": template}), "2002")


@pytest.mark.parametrize(
    ("definition", "expected"),
    [(None, "telegram_2002"), ({"actor_id_template": CANONICAL}, "internal:telegram:2002")],
)
async def test_submitted_turn_carries_the_templated_actor_id(tmp_path, monkeypatch, definition, expected):
    bundle_id = f"test.identity-submit-{bool(definition)}"
    storage = TelegramUserAdminStorage(tmp_path)
    user_admin.configure_telegram_user_admin(
        storage_factory=lambda entrypoint: storage,
        storage_root_or_error=lambda entrypoint: tmp_path,
        bundle_id=bundle_id,
    )
    authority_calls: list[dict] = []

    async def _authority(_entrypoint=None, **kwargs):
        authority_calls.append(dict(kwargs))
        return {
            "actor_user_id": kwargs["actor_user_id"],
            "storage_user_id": kwargs["actor_user_id"],
            "economics_user_id": "user-a",
            "platform_user_id": "user-a",
            "platform_roles": [],
            "platform_permissions": [],
            "identity_provider": "telegram",
            "platform_authority_resolved": True,
        }

    monkeypatch.setattr(user_admin, "_telegram_platform_authority", _authority)
    captured: dict = {}

    async def _submit(**kwargs):
        captured["session"] = kwargs["session"]
        return IngressResult(
            ok=True,
            conversation_id=kwargs["message_data"]["conversation_id"],
            turn_id=kwargs["message_data"]["turn_id"],
            session_id=kwargs["message_data"]["conversation_id"],
            user_type="registered",
        )

    entrypoint = _entrypoint(
        bundle_id,
        definition,
        chat_submitter=SimpleNamespace(submit=_submit),
        comm_context=SimpleNamespace(
            actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
            meta=SimpleNamespace(instance_id="telegram-test"),
        ),
    )
    result = await user_admin.submit_telegram_turn(
        entrypoint,
        summary={"text": "hello", "chat_id": "1001", "user_id": "2002", "update_id": "upd-identity", "attachments": []},
    )

    assert result["mode"] == "submitted"
    assert captured["session"].user_id == expected
    assert authority_calls[0]["actor_user_id"] == expected
    # The platform authority lookup keys on the Telegram subject, not the actor id.
    assert authority_calls[0]["telegram_user_id"] == "2002"


def _signed_init_data(*, bot_token: str, telegram_user_id: int) -> str:
    user = json.dumps({"id": telegram_user_id, "username": "elena"}, separators=(",", ":"))
    params = {"auth_date": str(int(time.time())), "query_id": "query-1", "user": user}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(params)


@pytest.mark.parametrize(
    ("definition", "expected"),
    [(None, "telegram_2002"), ({"actor_id_template": CANONICAL}, "internal:telegram:2002")],
)
async def test_mini_app_identity_of_an_unmapped_user_uses_the_template(tmp_path, definition, expected):
    bundle_id = f"test.identity-widget-{bool(definition)}"
    token = "123456:test-token"
    storage = TelegramUserAdminStorage(tmp_path)

    async def bot_token(entrypoint=None, **_kwargs):
        return token

    widget_auth.configure_telegram_widget_auth(storage_for=lambda entrypoint: storage, bot_token=bot_token, bundle_id=bundle_id)
    identity = await widget_auth.resolve_identity(
        _entrypoint(bundle_id, definition),
        telegram_init_data=_signed_init_data(bot_token=token, telegram_user_id=2002),
        allowed_roles=None,
        create_if_missing=True,
    )

    assert identity.user_id == expected


@pytest.mark.parametrize(
    ("definition", "user_id"),
    [(None, "telegram_2002"), ({"actor_id_template": CANONICAL}, "internal:telegram:2002")],
)
def test_report_delivery_finds_the_recipient_by_templated_id(tmp_path, definition, user_id):
    storage = TelegramUserAdminStorage(tmp_path)
    storage.upsert_user(telegram_user_id="2002", telegram_chat_id="1001", role="registered")
    entrypoint = _entrypoint("test.identity-delivery", definition)

    recipient = delivery._telegram_recipient_for_user(tmp_path, user_id=user_id, entrypoint=entrypoint)

    assert recipient is not None
    assert recipient["chat_id"] == "1001"
    other_form = "internal:telegram:2002" if user_id == "telegram_2002" else "telegram_2002"
    assert delivery._telegram_recipient_for_user(tmp_path, user_id=other_form, entrypoint=entrypoint) is None


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({}, "telegram_2002"),
        ({"properties": {"definition": {}}}, "telegram_2002"),
        ({"properties": {"definition": {"actor_id_template": CANONICAL}}}, "internal:telegram:2002"),
    ],
)
def test_connection_hub_authenticator_row_template(row, expected):
    assert format_telegram_actor_user_id("2002", authenticators._row_actor_id_template(row)) == expected

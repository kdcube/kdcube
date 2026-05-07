# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramMessage,
    make_signed_link_token,
    raw_attachments_from_telegram,
    render_telegram_messages_from_timeline,
    role_to_user_type,
    telegram_command_kind_and_text,
    validate_telegram_init_data,
    verify_signed_link_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth
from kdcube_ai_app.auth.sessions import UserType


def _telegram_init_data(*, bot_token: str, payload: dict[str, str]) -> str:
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    digest = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({**payload, "hash": digest})


def test_telegram_webapp_init_data_validates_signature():
    init_data = _telegram_init_data(
        bot_token="123:token",
        payload={
            "auth_date": "1000",
            "query_id": "query-1",
            "user": json.dumps({"id": 42, "username": "elena"}, separators=(",", ":")),
        },
    )

    verified = validate_telegram_init_data(
        init_data,
        bot_token="123:token",
        max_age_seconds=3600,
        now=1100,
    )

    assert verified.user["id"] == 42
    assert verified.params["query_id"] == "query-1"


def test_telegram_widget_auth_resolves_identity_through_configured_storage():
    class _Storage:
        def resolve_telegram_user(self, *, telegram_user_id, telegram_chat_id="", telegram_username="", create_if_missing=False):
            return {
                "telegram_user_id": telegram_user_id,
                "telegram_chat_id": telegram_chat_id,
                "telegram_username": telegram_username,
                "kdcube_user_id": "user-42",
                "role": "registered",
                "conversation_id": "telegram_chat_42",
            }

    class _Entry:
        def bundle_prop(self, path, default=None):
            if path == "integrations.telegram.web_app_auth_max_age_seconds":
                return 9999999999
            return default

    init_data = _telegram_init_data(
        bot_token="123:token",
        payload={
            "auth_date": "1000",
            "query_id": "query-1",
            "user": json.dumps({"id": 42, "username": "elena"}, separators=(",", ":")),
        },
    )
    widget_auth.configure_telegram_widget_auth(
        storage_for=lambda entrypoint: _Storage(),
        bot_token=lambda: "123:token",
    )

    identity = widget_auth.resolve_identity(_Entry(), telegram_init_data=init_data)

    assert identity.user_id == "user-42"
    assert identity.telegram_user_id == "42"
    assert identity.role == "registered"


def test_telegram_submit_helpers_normalize_commands_roles_and_attachments():
    kind, text = telegram_command_kind_and_text("/followup add this")
    assert (kind, text) == ("followup", "add this")
    assert role_to_user_type("admin") == UserType.PRIVILEGED
    assert role_to_user_type("registered") == UserType.REGISTERED

    raw = raw_attachments_from_telegram(
        [
            {
                "filename": "note.txt",
                "mime_type": "text/plain",
                "base64": base64.b64encode(b"hello").decode("ascii"),
                "file_id": "tg-file",
            }
        ]
    )

    assert len(raw) == 1
    assert raw[0].name == "note.txt"
    assert raw[0].content == b"hello"
    assert raw[0].meta["origin"] == "telegram"
    assert raw[0].meta["file_id"] == "tg-file"


def test_telegram_timeline_renderer_and_signed_links_are_importable():
    messages = render_telegram_messages_from_timeline(
        timeline={
            "turn_id": "turn_1",
            "blocks": [
                {
                    "type": "assistant.completion",
                    "turn_id": "turn_1",
                    "path": "ar:turn_1.assistant.completion",
                    "text": "Done.",
                }
            ],
            "sources_pool": [],
        }
    )

    assert messages == [TelegramMessage(kind="text", text="Done.", parse_mode="HTML")]

    signed = make_signed_link_token("secret", subject="exec:1:artifact:report.pdf", now=1000)
    payload = verify_signed_link_token("secret", signed.token, subject="exec:1:artifact:report.pdf", now=1001)
    assert payload["sub"] == "exec:1:artifact:report.pdf"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators import (
    AuthenticatedRequest,
    AuthenticatorRegistration,
    RequestEnvelope,
)


def test_request_envelope_normalizes_headers_and_json_body():
    envelope = RequestEnvelope.from_dict(
        {
            "method": "post",
            "path": "/hook",
            "headers": {"X-Telegram-Init-Data": "abc"},
            "query": {"tgWebAppData": "def"},
            "body_text": '{"telegram_init_data": "ghi"}',
        }
    )

    assert envelope.method == "POST"
    assert envelope.headers["x-telegram-init-data"] == "abc"
    assert envelope.query["tgwebappdata"] == "def"
    assert envelope.json_body()["telegram_init_data"] == "ghi"


def test_authenticator_registration_roundtrip():
    row = AuthenticatorRegistration.from_dict(
        {
            "id": "telegram.support",
            "provider": "telegram",
            "connection_id": "telegram.support",
            "role_providing": False,
            "secret": "identity.telegram.bot_token_support",
            "selector": {"header": "x-telegram-init-data"},
        }
    )

    assert row.authenticator_id == "telegram.support"
    assert row.connection_id == "telegram.support"
    assert row.secret_ref == "identity.telegram.bot_token_support"
    assert row.role_providing is False
    assert row.to_dict()["selector"] == {"header": "x-telegram-init-data"}


def test_authenticated_request_coerce():
    result = AuthenticatedRequest.coerce(
        {
            "ok": True,
            "authenticated": True,
            "provider": "telegram",
            "provider_subject": "42",
            "connection_id": "telegram.support",
            "identity_authority": {"actor_user_id": "telegram_42"},
        }
    )

    assert result.ok is True
    assert result.authenticated is True
    assert result.provider_subject == "42"
    assert result.connection_id == "telegram.support"
    assert result.identity_authority["actor_user_id"] == "telegram_42"

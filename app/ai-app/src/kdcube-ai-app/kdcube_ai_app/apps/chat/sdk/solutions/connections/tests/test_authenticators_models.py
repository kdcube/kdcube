# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators import (
    AuthRequestHints,
    AuthenticatedRequest,
    AuthenticatorRegistration,
    AuthorityIdentity,
    RequestEnvelope,
    SurfaceGuardRequirement,
    select_authenticator_candidates,
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
            "authority_id": "telegram.support",
            "connection_id": "telegram.support",
            "role_providing": False,
            "secret": "identity.authenticators.telegram_support.bot_token",
            "selector": {"header": "x-telegram-init-data"},
        }
    )

    assert row.authenticator_id == "telegram.support"
    assert row.authority_id == "telegram.support"
    assert row.connection_id == "telegram.support"
    assert row.secret_ref == "identity.authenticators.telegram_support.bot_token"
    assert row.role_providing is False
    assert row.to_dict()["selector"] == {"header": "x-telegram-init-data"}


def test_authenticator_registration_does_not_infer_authority_from_legacy_ids():
    row = AuthenticatorRegistration.from_dict(
        {
            "id": "telegram.support.init_data",
            "provider": "telegram",
            "integration_id": "telegram.support",
        }
    )

    assert row.authority_id == ""
    assert row.integration_id == "telegram.support"
    assert row.connection_id == "telegram.support"


def test_authenticated_request_coerce():
    result = AuthenticatedRequest.coerce(
        {
            "ok": True,
            "authenticated": True,
            "authority_id": "telegram.kdcube_ref",
            "identity_subject": "42",
            "provider": "telegram",
            "provider_subject": "42",
            "connection_id": "telegram.support",
            "identity_authority": {"actor_user_id": "telegram_42"},
        }
    )

    assert result.ok is True
    assert result.authenticated is True
    assert result.authority_id == "telegram.kdcube_ref"
    assert result.identity_subject == "42"
    assert result.provider_subject == "42"
    assert result.connection_id == "telegram.support"
    assert result.identity_authority["actor_user_id"] == "telegram_42"


def test_request_hints_from_headers_are_selector_hints_not_truth():
    envelope = RequestEnvelope.from_dict(
        {
            "headers": {
                "X-KDCube-Auth-Authority-ID": "custom.identity",
                "X-KDCube-Auth-Authenticator-ID": "custom.google_oidc",
                "X-KDCube-Auth-Provider": "oidc",
            }
        }
    )

    hints = AuthRequestHints.from_envelope(envelope)

    assert hints.authority_id == "custom.identity"
    assert hints.authenticator_id == "custom.google_oidc"
    assert hints.provider == "oidc"
    assert hints.has_explicit_selector is True


def test_request_hints_accept_authority_and_authenticator_from_query_and_body():
    envelope = RequestEnvelope.from_dict(
        {
            "query": {
                "auth_authority_id": "telegram.kdcube_ref",
                "auth_authenticator_id": "telegram.kdcube_ref",
                "auth_provider": "telegram",
            },
            "body_text": '{"authorityId": "ignored because query wins"}',
        }
    )

    hints = AuthRequestHints.from_envelope(envelope)

    assert hints.authority_id == "telegram.kdcube_ref"
    assert hints.authenticator_id == "telegram.kdcube_ref"
    assert hints.provider == "telegram"


def test_select_authenticator_candidates_prefers_exact_authenticator_hint():
    rows = [
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "custom.google_oidc",
                "authority_id": "custom.identity",
                "provider": "oidc",
            }
        ),
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "custom.api_key",
                "authority_id": "custom.identity",
                "provider": "api-key",
            }
        ),
    ]

    selected = select_authenticator_candidates(
        rows,
        {"authenticator_id": "custom.google_oidc", "authority_id": "custom.identity"},
    )

    assert [row.authenticator_id for row in selected] == ["custom.google_oidc"]


def test_select_authenticator_candidates_uses_authority_hint_as_narrowing_only():
    rows = [
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "kdcube.cognito",
                "authority_id": "kdcube.platform",
                "provider": "cognito",
            }
        ),
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "custom.google_oidc",
                "authority_id": "custom.identity",
                "provider": "oidc",
            }
        ),
    ]

    selected = select_authenticator_candidates(rows, {"authority_id": "custom.identity"})

    assert [row.authenticator_id for row in selected] == ["custom.google_oidc"]


def test_select_authenticator_candidates_combines_authority_and_legacy_hint():
    rows = [
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "telegram.ref.init_data",
                "authority_id": "telegram.ref",
                "integration_id": "telegram.ref",
                "provider": "telegram",
            }
        ),
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "telegram.other.init_data",
                "authority_id": "telegram.other",
                "integration_id": "telegram.ref",
                "provider": "telegram",
            }
        ),
    ]

    selected = select_authenticator_candidates(
        rows,
        {"authority_id": "telegram.ref", "integration_id": "telegram.ref"},
    )

    assert [row.authenticator_id for row in selected] == ["telegram.ref.init_data"]


def test_select_authenticator_candidates_respects_surface_accept_lists():
    rows = [
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "kdcube.cognito",
                "authority_id": "kdcube.platform",
                "provider": "cognito",
            }
        ),
        AuthenticatorRegistration.from_dict(
            {
                "authenticator_id": "custom.google_oidc",
                "authority_id": "custom.identity",
                "provider": "oidc",
            }
        ),
    ]
    surface = SurfaceGuardRequirement(
        required_authority="kdcube.platform",
        accepted_authorities=("kdcube.platform",),
    )

    selected = select_authenticator_candidates(rows, {"provider": "oidc"}, surface=surface)

    assert selected == []


def test_authority_identity_canonical_ref():
    identity = AuthorityIdentity(authority_id="custom.identity", subject="user:123")

    assert identity.canonical_ref == "custom.identity:user:123"
    assert identity.to_dict()["ref"] == "custom.identity:user:123"

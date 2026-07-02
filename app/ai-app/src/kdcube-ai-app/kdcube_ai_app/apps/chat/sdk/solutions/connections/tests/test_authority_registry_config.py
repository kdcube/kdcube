# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config import (
    authority_provider_instances,
    cognito_platform_auth_config,
    platform_authority_auth_config,
    resolve_authority_provider_instance,
    resolve_platform_authority_provider,
)


def _registry():
    return {
        "authorities": {
            "kdcube.platform": {
                "platform": True,
                "role_bindings": {
                    "emails": {
                        "owner@example.com": {"roles": ["kdcube:role:super-admin"]}
                    }
                },
                "providers": {
                    "cognito": {
                        "type": "multi_cognito",
                        "authenticator": {
                            "type": "cognito_id_token",
                            "id_token_header_name": "X-ID-Token",
                            "region": "eu-west-1",
                            "user_pool_id": "pool-a",
                            "app_client_id": "client-a",
                            "service_client_id": "service-a",
                            "trusted_providers": [
                                {
                                    "alias": "secondary",
                                    "kind": "cognito",
                                    "region": "eu-west-1",
                                    "user_pool_id": "pool-b",
                                    "app_client_id": "client-b",
                                }
                            ],
                            "cookie": {
                                "auth_token_cookie_name": "__Secure-AUTH",
                                "id_token_cookie_name": "__Secure-ID",
                            },
                        },
                    },
                    "versatile_google_session": {
                        "type": "bundle_session_login",
                        "entrypoints": {
                            "login": {
                                "bundle_id": "versatile@2026-03-31-13-36",
                                "route": "public",
                                "operation": "platform_login",
                            },
                            "session_issue": {
                                "bundle_id": "versatile@2026-03-31-13-36",
                                "route": "public",
                                "operation": "auth_google_session",
                            },
                            "consent": {
                                "bundle_id": "versatile@2026-03-31-13-36",
                                "route": "public",
                                "operation": "delegated_consent",
                            },
                        },
                        "input": {
                            "authenticator_ref": {
                                "authority_id": "google.accounts",
                                "provider_id": "google_oidc",
                            }
                        },
                        "issuer": {
                            "type": "kdcube_session_token",
                            "ttl_seconds": 43200,
                            "cookie": {
                                "auth_token_cookie_name": "__Secure-SESSION-AUTH",
                                "id_token_cookie_name": "__Secure-SESSION-ID",
                                "masqueraded_token_cookie_name": "__Secure-SESSION-MASK",
                            },
                        },
                        "grants": {"roles": ["kdcube:role:registered"]},
                    },
                    "disabled_provider": {
                        "type": "bundle_session_login",
                        "enabled": False,
                    },
                },
            },
            "telegram.kdcube_ref": {
                "platform": False,
                "providers": {
                    "telegram_bot_init_data": {
                        "type": "telegram_init_data",
                        "authenticator": {"secret_ref": "secret.ref"},
                    },
                },
            },
            "google.accounts": {
                "providers": {
                    "google_oidc": {
                        "type": "google_id_token",
                        "authenticator": {"client_id": "client.apps.googleusercontent.com"},
                    },
                },
            },
        },
    }


def test_authority_provider_instances_flatten_configured_instances():
    rows = authority_provider_instances(_registry())

    assert [
        (row["authority_id"], row["provider_id"], row["provider_type"], row["platform"])
        for row in rows
    ] == [
        ("kdcube.platform", "cognito", "multi-cognito", True),
        ("kdcube.platform", "versatile_google_session", "bundle_session_login", True),
        ("telegram.kdcube_ref", "telegram_bot_init_data", "telegram_init_data", False),
        ("google.accounts", "google_oidc", "google_id_token", False),
    ]


def test_resolve_authority_provider_instance_by_host_operation():
    result = resolve_authority_provider_instance(
        _registry(),
        provider_type="bundle_session_login",
        host_bundle_id="versatile@2026-03-31-13-36",
        host_route="public",
        host_operation="auth_google_session",
    )

    assert result["ok"] is True
    assert result["authority_id"] == "kdcube.platform"
    assert result["provider_id"] == "versatile_google_session"
    assert result["entrypoints"]["login"]["operation"] == "platform_login"
    assert result["entrypoints"]["consent"]["operation"] == "delegated_consent"
    assert result["provider"]["issuer"]["ttl_seconds"] == 43200
    assert result["authority"]["role_bindings"]["emails"]["owner@example.com"]["roles"] == ["kdcube:role:super-admin"]


def test_cognito_platform_auth_config_normalizes_registry_provider():
    resolved = resolve_platform_authority_provider(
        _registry(),
        authority_id="kdcube.platform",
        provider_id="cognito",
    )

    config = cognito_platform_auth_config(resolved)

    assert config["auth_provider"] == "multi-cognito"
    assert config["region"] == "eu-west-1"
    assert config["user_pool_id"] == "pool-a"
    assert config["app_client_id"] == "client-a"
    assert config["service_client_id"] == "service-a"
    assert config["auth_token_cookie_name"] == "__Secure-AUTH"
    assert config["id_token_cookie_name"] == "__Secure-ID"
    assert [row["alias"] for row in config["trusted_providers"]] == ["primary", "secondary"]


def test_platform_authority_auth_config_normalizes_bundle_session_provider():
    resolved = resolve_platform_authority_provider(
        _registry(),
        authority_id="kdcube.platform",
        provider_id="versatile_google_session",
    )

    config = platform_authority_auth_config(resolved)

    assert config["auth_provider"] == "session"
    assert config["id_token_header_name"] == "X-ID-Token"
    assert config["auth_token_cookie_name"] == "__Secure-SESSION-AUTH"
    assert config["id_token_cookie_name"] == "__Secure-SESSION-ID"
    assert config["masqueraded_token_cookie_name"] == "__Secure-SESSION-MASK"


def test_resolve_authority_provider_instance_by_consent_entrypoint():
    result = resolve_authority_provider_instance(
        _registry(),
        provider_type="bundle_session_login",
        host_bundle_id="versatile@2026-03-31-13-36",
        host_route="public",
        host_operation="delegated_consent",
    )

    assert result["ok"] is True
    assert result["authority_id"] == "kdcube.platform"
    assert result["provider_id"] == "versatile_google_session"


def test_resolve_authority_provider_instance_by_authority_and_provider_id():
    result = resolve_authority_provider_instance(
        _registry(),
        authority_id="google.accounts",
        provider_id="google_oidc",
    )

    assert result["ok"] is True
    assert result["provider_type"] == "google_id_token"
    assert result["provider"]["authenticator"]["client_id"] == "client.apps.googleusercontent.com"


def test_resolve_authority_provider_instance_fails_closed_when_missing():
    result = resolve_authority_provider_instance(
        _registry(),
        provider_type="bundle_session_login",
        host_bundle_id="versatile@2026-03-31-13-36",
        host_route="public",
        host_operation="missing",
    )

    assert result == {"ok": False, "error": "authority_provider_not_found"}

from __future__ import annotations

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login as bundle_session_login


def test_grants_can_assign_google_platform_subject_within_provider_bounds():
    roles, permissions, source = bundle_session_login.resolve_platform_grants(
        authority_cfg={
            "grants": {
                "subjects": {
                    "google:123": {
                        "label": "bootstrap_admin",
                        "roles": ["kdcube:role:super-admin"],
                        "permissions": ["kdcube:*:*:*"],
                    }
                }
            }
        },
        provider_cfg={
            "grants": {
                "default": {"roles": ["kdcube:role:registered"]},
                "assignable": {
                    "roles": ["kdcube:role:registered", "kdcube:role:super-admin"],
                    "permissions": ["kdcube:*:*:*"],
                },
            }
        },
        sub="google:123",
        provider="google",
        provider_subject="123",
    )

    assert roles == ["kdcube:role:super-admin"]
    assert permissions == ["kdcube:*:*:*"]
    assert source == "bootstrap_admin"


def test_role_binding_fails_closed_when_binding_exceeds_provider_bounds():
    with pytest.raises(Exception, match="non-assignable roles"):
        bundle_session_login.resolve_platform_grants(
            authority_cfg={
                "grants": {
                    "subjects": {
                        "google:123": {
                            "roles": ["kdcube:role:super-admin"],
                        },
                    }
                }
            },
            provider_cfg={
                "grants": {
                    "assignable": {"roles": ["kdcube:role:registered"]},
                }
            },
            sub="google:123",
            provider="google",
            provider_subject="123",
        )


def test_grants_fall_back_to_provider_default_grants():
    roles, permissions, source = bundle_session_login.resolve_platform_grants(
        authority_cfg={"grants": {}},
        provider_cfg={
            "grants": {
                "default": {
                    "roles": ["kdcube:role:registered"],
                    "permissions": ["kdcube:*:chat:*;read"],
                },
            }
        },
        sub="google:123",
        provider="google",
        provider_subject="123",
    )

    assert roles == ["kdcube:role:registered"]
    assert permissions == ["kdcube:*:chat:*;read"]
    assert source == "grants.default"


def test_grants_can_bootstrap_by_verified_google_email():
    roles, permissions, source = bundle_session_login.resolve_platform_grants(
        authority_cfg={
            "grants": {
                "subjects": {},
                "bootstrap_rules": [
                    {
                        "id": "bootstrap_admin_by_google_email",
                        "when": {
                            "provider": "google",
                            "claims": {
                                "email": "owner@example.com",
                                "email_verified": True,
                            },
                        },
                        "roles": ["kdcube:role:super-admin"],
                        "permissions": ["kdcube:*:*:*"],
                    }
                ]
            },
        },
        provider_cfg={
            "grants": {
                "assignable": {
                    "roles": ["kdcube:role:super-admin"],
                    "permissions": ["kdcube:*:*:*"],
                },
            }
        },
        sub="google:123",
        provider="google",
        provider_subject="123",
        verified_claims={
            "email": "owner@example.com",
            "email_verified": True,
        },
    )

    assert roles == ["kdcube:role:super-admin"]
    assert permissions == ["kdcube:*:*:*"]
    assert source == "bootstrap_admin_by_google_email"


def test_grants_reject_unverified_google_email():
    roles, permissions, source = bundle_session_login.resolve_platform_grants(
        authority_cfg={
            "grants": {
                "bootstrap_rules": [
                    {
                        "id": "bootstrap_admin_by_google_email",
                        "when": {
                            "provider": "google",
                            "claims": {"email": "owner@example.com"},
                        },
                        "roles": ["kdcube:role:super-admin"],
                    }
                ]
            }
        },
        provider_cfg={
            "grants": {
                "default": {"roles": ["kdcube:role:registered"]},
                "assignable": {
                    "roles": ["kdcube:role:registered", "kdcube:role:super-admin"],
                },
            }
        },
        sub="google:123",
        provider="google",
        provider_subject="123",
        verified_claims={
            "email": "owner@example.com",
            "email_verified": False,
        },
    )

    assert roles == ["kdcube:role:registered"]
    assert permissions == []
    assert source == "grants.default"

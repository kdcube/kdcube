from __future__ import annotations

import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

import kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authority_providers.bundle_login as bundle_login


def _load_platform_session_issuer():
    path = Path(__file__).resolve().parents[1] / "services" / "platform_session_issuer.py"
    spec = importlib.util.spec_from_file_location("workspace_platform_session_issuer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ResourceOperationInputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str | bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        values = dict(attrs)
        if values.get("name") != "resource_operations":
            return
        self.rows.append({
            "value": values.get("value") or "",
            "checked": "checked" in values,
        })


def test_grants_can_assign_google_platform_subject_within_provider_bounds():
    roles, permissions, source = bundle_login.resolve_platform_grants(
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


def test_custom_consent_renders_owner_connector_operations_and_existing_selection():
    renderer = _load_platform_session_issuer()
    connector = "urn:connection-hub:remote-mcp:mcp_0123456789abcdef01234567"
    result = renderer.delegated_consent_page(None, payload={
        "request": {
            "client_id": "openclaw",
            "redirect_uri": "http://127.0.0.1:9876/callback",
            "response_type": "code",
            "scope": "external_mcp:use",
            "resource": "https://runtime.example.test/public/mcp/remote_mcp_proxy",
            "state": "state-123",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
        },
        "consent_contract": {"version": "2026-09-02.1"},
        "resources": [
            {
                "resource": connector,
                "label": "Customer records <script>",
                "operations": [
                    {
                        "name": "records.search",
                        "label": "Search records",
                        "description": "Find matching customer records.",
                        "grants": ["external_mcp:use"],
                        "held": True,
                    },
                    {
                        "name": "records.delete",
                        "label": "Delete records",
                        "description": "Delete one customer record.",
                        "grants": ["external_mcp:use"],
                        "held": False,
                    },
                ],
            },
        ],
    })

    rendered = result["html"]
    parser = _ResourceOperationInputs()
    parser.feed(rendered)
    rows = {
        json.loads(str(row["value"]))["operation"]: row["checked"]
        for row in parser.rows
    }

    assert "Services and operations for this connection" in rendered
    assert "Customer records &lt;script&gt;" in rendered
    assert "Customer records <script>" not in rendered
    assert connector in rendered
    assert rows == {"records.search": True, "records.delete": False}


def test_role_binding_fails_closed_when_binding_exceeds_provider_bounds():
    with pytest.raises(Exception, match="non-assignable roles"):
        bundle_login.resolve_platform_grants(
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
    roles, permissions, source = bundle_login.resolve_platform_grants(
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
    roles, permissions, source = bundle_login.resolve_platform_grants(
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
    roles, permissions, source = bundle_login.resolve_platform_grants(
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

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The universal consent denial: hosted agents AND external delegated clients
each receive an actionable path — the same focused Connection Hub deep link,
plus the one-click grant action for hosted agents."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.consent_denial import (
    agent_grant_consent_denial,
    connection_hub_grant_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
    set_connection_hub_public_base_url,
)

RESOURCE = "*/kdcube-services@1-0/public/mcp/named_services*"


def _request(client_id: str):
    return SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {"attrs": {"grants": ["named_services:use"], "resource": RESOURCE}},
        "grant_record": {"client_id": client_id, "grants": []},
    }))


@pytest.fixture(autouse=True)
def _public_base():
    set_connection_hub_public_base_url("https://runtime.example")
    yield
    set_connection_hub_public_base_url("")


def _denial(client_id: str):
    return agent_grant_consent_denial(
        _request(client_id),
        namespace="slack", tool="search", operation="object.search",
        required=["slack:read"], missing=["slack:read"], available=["named_services:use"],
        tenant="demo-tenant", project="demo-project",
    )


def test_hub_grant_url_shape():
    url = connection_hub_grant_url(
        tenant="t", project="p", client_id="claude", resource=RESOURCE, claims=["mail:read"],
    )
    assert url.startswith("https://runtime.example/api/integrations/bundles/t/p/connection-hub%401-0/widgets/connections_settings?")
    assert "pending_agent_grant=1" in url
    assert "agent_client_id=claude" in url
    assert "claims=mail%3Aread" in url


def test_hosted_agent_gets_grant_action_and_link():
    denial = _denial("kdcube-agent:app:main")
    consent = denial["consent"]
    assert consent["grant"]["payload"]["claims"] == ["slack:read"]
    assert consent["connection_hub_url"].startswith("https://runtime.example/")
    assert "pending_agent_grant=1" in consent["connection_hub_url"]


def test_external_client_gets_focused_link_and_instructions():
    # An external app connected via OAuth (Claude Code): no one-click grant
    # action, but the SAME focused deep link — the user signs in and sees this
    # client's card with the missing claims pre-checked.
    denial = _denial("claude")
    consent = denial["consent"]
    assert consent["agent_client_id"] == "claude"
    assert "grant" not in consent
    url = consent["connection_hub_url"]
    assert "agent_client_id=claude" in url and "slack%3Aread" in url
    assert denial["connection_hub_url"] == url
    assert url in denial["instructions"]
    assert "sign in" in denial["next_step"]


def test_manual_automation_is_not_sent_to_the_pending_pane():
    """The pending pane's only save is delegated_agent_grant_create, which
    cannot serve a record keyed by a random access_id. Sending an automation
    client there produces a link whose save always fails, so it gets the
    open-the-hub guidance instead; its card is edited by hand."""
    denial = _denial("automation:aut_9xK2:google:1234")
    consent = denial["consent"]
    # The caller and its missing claims are still named, and the link still
    # lands on the exact editable card — only the pending create pane is withheld.
    assert consent["agent_client_id"] == "automation:aut_9xK2:google:1234"
    assert consent["claims"] == ["slack:read"]
    assert "grant" not in consent
    assert "tab=delegated_by_kdcube" in consent["connection_hub_url"]
    assert "manual_access_id=aut_9xK2" in consent["connection_hub_url"]
    assert "resource=" in consent["connection_hub_url"]
    assert "claims=slack%3Aread" in consent["connection_hub_url"]
    assert "pending_agent_grant" not in str(denial)


def test_the_per_account_binding_link_also_skips_the_pending_pane():
    """The binding denial builds its own URL, so the rule lives in the builder.

    This is the link that actually reaches an automation caller in practice.
    """
    url = connection_hub_grant_url(
        tenant="t",
        project="p",
        client_id="automation:aut_9xK2",
        resource=RESOURCE,
        claims=[],
        account_id="acc_1",
        account_claim="linkedin:post",
    )
    assert "tab=delegated_by_kdcube" in url
    assert "manual_access_id=aut_9xK2" in url
    assert "pending_agent_grant" not in url
    assert "account_id=acc_1" in url
    assert "account_claim=linkedin%3Apost" in url


def test_no_public_base_keeps_reconnect_guidance():
    set_connection_hub_public_base_url("")
    denial = _denial("claude")
    assert "connection_hub_url" not in denial
    assert "Reconnect" in denial["next_step"]


def _agent_request_credential_resource_grants():
    """The LIVE agent-bearer shape: no single attrs.resource; the door resource
    lives in credential.attrs.resource_grants (regression 2026-07-19 — the
    denial went out with resource='' so no banner rose, exactly what the
    [agent-consent-denial] 'consent block has NO resource' warning caught)."""
    door = "*/kdcube-services@1-0/public/mcp/named_services*"
    return SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {
            "sub": "integration:kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react:user-1",
            "attrs": {
                "grants": ["named_services:use", "slack:read"],
                "resource_grants": {door: ["named_services:use", "slack:read"]},
            },
        },
        "grant_record": {"grants": []},
    })), door


def test_agent_bearer_resource_comes_from_credential_resource_grants():
    request, door = _agent_request_credential_resource_grants()
    denial = agent_grant_consent_denial(
        request, namespace="mail", tool="search", operation="object.search",
        required=["mail:read"], missing=["mail:read"], available=["named_services:use", "slack:read"],
        tenant="demo-tenant", project="demo-project",
    )
    consent = denial["consent"]
    assert consent["agent_client_id"] == "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    assert consent["resource"] == door               # NOT '' — the banner can now rise
    assert consent["grant"]["payload"]["resource"] == door
    assert consent["connection_hub_url"]             # deep link built (resource present)

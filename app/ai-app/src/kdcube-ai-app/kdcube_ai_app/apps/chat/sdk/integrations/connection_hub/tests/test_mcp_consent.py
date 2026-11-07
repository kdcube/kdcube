# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The MCP-consent wrapper (`mcp_consent.py`) — turns a KDCube `@mcp` 403 into the
consent-demand exception, chat-bubbleable AND agent-explainable. The surface
keeps returning a plain 403; the client wrapper interprets it."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.mcp_consent import (
    MCPConsentRequired,
    is_kdcube_mcp_consent_denial,
    mcp_consent_from_denial,
    raise_for_mcp_consent,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.preflight import (
    CONSENT_NEEDED_CODE,
)

_RES = "https://h/api/integrations/bundles/t/p/user-memories@2026-06-26/public/mcp/memories"


def test_native_hosted_demand_builds_a_complete_card_review_url(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from connection_hub.delegated_to_kdcube import public_base
    from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx

    monkeypatch.setattr(comm_ctx, "get_current_user_identity", lambda: {"tenant_id": "t", "project_id": "p"})
    monkeypatch.setattr(public_base, "connection_hub_public_base_url", lambda: "https://hub.test")
    exc = mcp_consent_from_denial({"status": 403}, resource=_RES, claims=[],
        namespace="slides", operation="object.list", outer_operation="tools/call",
        agent_client_id="kdcube-agent:workspace@1-0:main")
    params = parse_qs(urlsplit(exc.chat_event_payload()["consent"]["url"]).query)
    assert params["agent_card_consent"] == ["1"]
    assert params["agent_client_id"] == ["kdcube-agent:workspace@1-0:main"]
    assert params["resource"] == [_RES]
    assert params["namespace"] == ["slides"] and params["operation"] == ["object.list"]
    assert params["outer_operation"] == ["tools/call"]
    assert "claims" not in params


def test_403_authority_mismatch_is_a_consent_denial():
    assert is_kdcube_mcp_consent_denial({"status": 403, "reason": "authority_mismatch"}) is True
    assert is_kdcube_mcp_consent_denial({"status": 403}) is True            # bare 403 -> surface consent
    assert is_kdcube_mcp_consent_denial({"status": 500, "reason": "boom"}) is False   # not a consent denial
    assert is_kdcube_mcp_consent_denial({"status": 403, "reason": "rate_limited"}) is False  # unrelated 403


def test_consent_payload_carries_the_chat_contract_and_claims():
    exc = mcp_consent_from_denial(
        {"status": 403, "reason": "authority_mismatch"},
        resource=_RES, claims=["memories:read"],
        connection_hub_url="https://h/hub", tool_name="memory_search",
    )
    assert exc.consent["code"] == CONSENT_NEEDED_CODE
    assert exc.consent["claims"] == ["memories:read"]
    assert exc.consent["resource"] == _RES
    assert exc.consent["connection_hub_url"] == "https://h/hub"


def test_hosted_agent_requires_card_review_before_grant():
    exc = mcp_consent_from_denial(
        {"status": 403, "reason": "authority_mismatch"},
        resource=_RES, claims=["memories:read"], tool_name="memory_search",
        agent_client_id="kdcube-agent:app@v1:lg-react",
    )
    assert exc.consent["kind"] == "delegated_agent_grant"
    assert exc.consent["agent_client_id"] == "kdcube-agent:app@v1:lg-react"
    assert exc.consent["agent_card_consent"] is True
    assert "grant" not in exc.consent


def test_chat_event_payload_is_the_nested_banner_shape_with_card_review():
    exc = mcp_consent_from_denial(
        {"status": 403, "reason": "authority_mismatch"},
        resource=_RES, claims=["memories:read"], tool_name="memory_search",
        connection_hub_url="https://h/hub", agent_client_id="kdcube-agent:app@v1:lg-react",
    )
    ev = exc.chat_event_payload()
    # Same shape the connected-account (Slack) banner consumes: code under error,
    # everything else under a nested consent block.
    assert ev["ok"] is False
    assert ev["error"]["code"] == CONSENT_NEEDED_CODE
    block = ev["consent"]
    assert block["claims"] == ["memories:read"]
    assert block["resource"] == _RES
    assert block["url"] == "https://h/hub?agent_card_consent=1"
    assert block["action_label"] == "Review agent access"
    assert block["agent_client_id"] == "kdcube-agent:app@v1:lg-react"
    assert block["agent_card_consent"] is True and "grant" not in block
    assert ev["tools"] == ["memory_search"]


def test_chat_event_payload_without_agent_is_open_hub():
    exc = mcp_consent_from_denial({"status": 403}, resource=_RES, claims=["memories:read"])
    block = exc.chat_event_payload()["consent"]
    assert block["action_label"] == "Open Connection Hub"
    assert "grant" not in block and "agent_client_id" not in block


def test_no_agent_client_id_leaves_a_plain_demand():
    exc = mcp_consent_from_denial(
        {"status": 403}, resource=_RES, claims=["memories:read"])
    # Without the agent identity there is no grant action / kind (unchanged shape).
    assert "grant" not in exc.consent and "kind" not in exc.consent


def test_exception_is_agent_explainable():
    exc = mcp_consent_from_denial(
        {"status": 403}, resource=_RES, claims=["memories:read"], tool_name="memory_search")
    # The message tells the model what's blocked, what to ask for, and not to retry.
    assert "consent" in str(exc).lower()
    assert "memories:read" in str(exc)
    assert "do not retry" in str(exc).lower()
    # The tool result form the model receives.
    res = exc.to_tool_result()
    assert res["ok"] is False and res["error"]["code"] == CONSENT_NEEDED_CODE
    assert res["consent"]["claims"] == ["memories:read"]


def test_raise_for_mcp_consent_raises_on_denial_and_is_silent_otherwise():
    with pytest.raises(MCPConsentRequired):
        raise_for_mcp_consent({"status": 403, "reason": "authority_mismatch"},
                              resource=_RES, claims=["memories:read"])
    # A non-consent error does not raise (the caller handles it as a normal failure).
    raise_for_mcp_consent({"status": 500}, resource=_RES, claims=["memories:read"])


def test_status_read_from_object_or_mapping():
    class _Err:
        status_code = 403
        reason = "missing_permission"
    with pytest.raises(MCPConsentRequired):
        raise_for_mcp_consent(_Err(), resource=_RES, claims=["memories:read"])

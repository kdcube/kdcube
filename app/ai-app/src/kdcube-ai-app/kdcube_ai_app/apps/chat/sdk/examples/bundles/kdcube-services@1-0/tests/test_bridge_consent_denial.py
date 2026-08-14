# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The named-services door's consent denial carries the per-agent grant path.

Regression: a hosted agent's op on an ungranted namespace (mail via a bearer
holding only slack grants) returned a bare `delegated_consent_required` error —
no agent identity, no resource, no grant action — so the caller's chat surface
could not raise the scoped consent banner and the Connection Hub landing showed
no pending claims. The denial now carries the full consent block for
`kdcube-agent:*` callers; other client families keep the reconnect guidance."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _bridge_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "named_services" / "bridge.py"
    )
    return module


class _Policy:
    namespace = "mail"

    def tool_configured(self, tool_name):
        return True

    def operation_configured(self, *, tool_name, operation):
        return True

    def grants_for(self, *, tool_name, operation):
        return ["mail:read"]

    def authority_for(self, *, tool_name, operation):
        return ""


RESOURCE_PATTERN = (
    "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
)


def _bind_catalog(state, namespaces):
    """A delegated call resolves the active catalog, so a test that reaches
    dispatch states the generation it runs under."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import (
        bind_delegated_catalog,
    )

    bind_delegated_catalog(
        SimpleNamespace(state=state),
        {
            "delegated_credentials": {
                "oauth": {
                    "enabled": True,
                    "resources": [
                        {
                            "resource": RESOURCE_PATTERN,
                            "grants": ["named_services:use"],
                            "named_services": {"namespaces": namespaces},
                        },
                    ],
                },
            },
        },
    )


def _request(
    client_id: str,
    grants=None,
    account_scope=None,
    *,
    catalog_namespaces=None,
):
    grants = list(grants or ["named_services:use", "slack:read"])
    request = SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {"attrs": {"grants": grants}},
        "grant_record": {
            "client_id": client_id,
            "grants": [],
            "resource_grants": {RESOURCE_PATTERN: [*grants]},
            "account_scope": dict(account_scope or {}),
        },
    }))
    if catalog_namespaces is not None:
        _bind_catalog(request.state, catalog_namespaces)
    return request


def _bridge(m, request):
    return m.NamedServicesMcpBridge(config={}, tenant="t", project="p", request=request)


async def test_agent_caller_denial_carries_the_consent_block():
    m = _bridge_module()
    client = "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    bridge = _bridge(m, _request(client))

    denial = await bridge._authorize(_Policy(), "object.search", tool_name="search")

    assert denial["error"] == "delegated_consent_required"
    assert denial["missing_grants"] == ["mail:read"]
    assert denial["code"] == "connections.consent_needed"
    consent = denial["consent"]
    assert consent["kind"] == "delegated_agent_grant"
    assert consent["agent_client_id"] == client
    assert consent["resource"].endswith("named_services*")
    assert consent["claims"] == ["mail:read"]
    assert consent["tool_name"] == "mail"
    assert consent["grant"]["operation"] == "delegated_agent_grant_create"
    assert consent["grant"]["payload"]["claims"] == ["mail:read"]
    assert "Connection Hub" in denial["next_step"]


async def test_external_caller_gets_identity_block_and_reconnect_fallback():
    # An external delegated client (Claude Code) is part of the SAME universal
    # contract: its denial carries the consent block naming the client and the
    # missing claims. Without a configured public base URL there is no hub deep
    # link, so the reconnect guidance stays as the fallback next step.
    m = _bridge_module()
    bridge = _bridge(m, _request("claude"))

    denial = await bridge._authorize(_Policy(), "object.search", tool_name="search")

    assert denial["error"] == "delegated_consent_required"
    consent = denial["consent"]
    assert consent["agent_client_id"] == "claude"
    assert consent["claims"] == ["mail:read"]
    assert "grant" not in consent          # one-click grant is hosted-agent only
    assert "Reconnect" in denial["next_step"]


def _request_via_subject():
    """The LIVE projection: grant_record without client_id; the delegate
    subject on the credential is the only agent identity, and the credential
    attrs carry the granted resource."""
    return SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {
            "sub": "integration:kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react:user-1",
            "attrs": {
                "grants": ["named_services:use"],
                "resource": "*/kdcube-services@1-0/public/mcp/named_services*",
            },
        },
        "grant_record": {"grants": []},
    }))


async def test_agent_identity_falls_back_to_the_delegate_subject():
    # Regression (live 2026-07-19): the projected grant_record carried no
    # client_id, so the denial went out bare (block={}) and no banner rose.
    m = _bridge_module()
    bridge = _bridge(m, _request_via_subject())

    denial = await bridge._authorize(_Policy(), "object.search", tool_name="search")

    assert denial["missing_grants"] == ["mail:read"]
    consent = denial["consent"]
    assert consent["agent_client_id"] == "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    assert consent["resource"] == "*/kdcube-services@1-0/public/mcp/named_services*"
    assert consent["grant"]["payload"]["claims"] == ["mail:read"]


async def test_get_forwards_provider_filters() -> None:
    m = _bridge_module()
    bridge = _bridge(m, _request("claude"))
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    bridge.call = fake_call
    result = await bridge.get(
        namespace="sheets",
        object_ref="sheets:google:account-1:spreadsheet:sheet-1",
        filters_json='{"ranges":["Plan!A1:D20"]}',
    )

    assert result == {"ok": True}
    assert captured["operation"] == "object.get"
    assert captured["filters"] == {"ranges": ["Plan!A1:D20"]}


async def test_search_forwards_provider_cursor() -> None:
    m = _bridge_module()
    bridge = _bridge(m, _request("claude"))
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    bridge.call = fake_call
    result = await bridge.search(
        namespace="mail",
        query="invoice",
        cursor="gmail-page-2",
    )

    assert result == {"ok": True}
    assert captured["cursor"] == "gmail-page-2"


async def test_schema_forwards_recursive_capability_search() -> None:
    m = _bridge_module()
    bridge = _bridge(m, _request("claude"))
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    bridge.call = fake_call
    result = await bridge.schema(
        namespace="docs",
        query="reply to a comment",
        search_mode="hybrid",
        limit=8,
    )

    assert result == {"ok": True}
    assert captured["operation"] == "object.schema"
    assert captured["query"] == "reply to a comment"
    assert captured["search_mode"] == "hybrid"
    assert captured["limit"] == 8


async def test_bounded_action_authorizes_the_exact_action_key(monkeypatch) -> None:
    m = _bridge_module()
    config = {
        "namespaces": {
            "slack": {
                "tools": {
                    "action": {
                        "operation": "object.action",
                        "operations": {
                            "object.action.post_message": {
                                "grants": ["named_services:use", "slack:post"]
                            },
                            "object.action.upload_file": {
                                "grants": [
                                    "named_services:use",
                                    "slack:files:write",
                                ]
                            },
                        },
                    }
                }
            }
        }
    }
    bridge = m.NamedServicesMcpBridge(
        config=config,
        tenant="t",
        project="p",
        request=_request(
            "claude",
            ["named_services:use", "slack:post"],
            catalog_namespaces=config["namespaces"],
        ),
    )
    captured = {}

    async def fake_endpoint(_endpoint, request):
        captured["request"] = request
        return m.NamedServiceResponse.ok_response(namespace="slack")

    monkeypatch.setattr(m, "call_named_service_endpoint", fake_endpoint)

    allowed = await bridge.object_action(
        namespace="slack",
        object_ref="slack:account:channel:C123",
        action="post_message",
        payload_json='{"text":"hello"}',
    )
    denied = await bridge.object_action(
        namespace="slack",
        object_ref="slack:account:channel:C123",
        action="upload_file",
        payload_json='{"staged_ref":"upload:1"}',
    )

    assert allowed["ok"] is True
    assert captured["request"].operation == "object.action"
    assert captured["request"].action == "post_message"
    assert denied["error"] == "delegated_consent_required"
    assert denied["missing_grants"] == ["slack:files:write"]


async def test_tool_call_rebinds_agent_account_scope_before_provider_call(monkeypatch) -> None:
    # Streamable MCP may invoke the tool after app construction. The bridge must
    # bind the delegated account_scope at the tool-call boundary too, otherwise
    # provider-backed Slack checks see an empty agent scope and ask for consent
    # even after the card shows the account claims.
    m = _bridge_module()
    config = {
        "namespaces": {
            "slack": {
                "tools": {
                    "action": {
                        "operation": "object.action",
                        "operations": {
                            "object.action.upload_file": {
                                "grants": ["named_services:use", "slack:files:write"]
                            },
                        },
                    }
                }
            }
        }
    }
    bridge = m.NamedServicesMcpBridge(
        config=config,
        tenant="t",
        project="p",
        request=_request(
            "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react",
            ["named_services:use"],
            account_scope={"slack": {"slack-1": ["slack:files:write"]}},
            catalog_namespaces=config["namespaces"],
        ),
    )

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
        account_claim_scope_for,
        clear_agent_account_scope,
    )

    clear_agent_account_scope()

    async def fake_endpoint(_endpoint, request):
        assert account_claim_scope_for("slack") == {
            "slack-1": ("slack:files:write",)
        }
        return m.NamedServiceResponse.ok_response(namespace="slack")

    monkeypatch.setattr(m, "call_named_service_endpoint", fake_endpoint)

    result = await bridge.object_action(
        namespace="slack",
        object_ref="slack:slack-1",
        action="upload_file",
        payload_json='{"staged_ref":"upload:1"}',
    )

    assert result["ok"] is True


async def test_account_scope_claim_satisfies_provider_backed_bridge_gate(monkeypatch) -> None:
    # Live regression (2026-08-17): the grant card carried slack:channels in
    # account_scope, but the bridge only checked bearer grants and denied
    # object.list before the provider broker could use the account binding.
    m = _bridge_module()
    config = {
        "namespaces": {
            "slack": {
                "tools": {
                    "call": {
                        "operation": "*",
                        "operations": {
                            "object.list": {
                                "grants": ["named_services:use", "slack:channels"]
                            },
                        },
                    }
                }
            }
        }
    }
    bridge = m.NamedServicesMcpBridge(
        config=config,
        tenant="t",
        project="p",
        request=_request(
            "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react",
            ["named_services:use"],
            account_scope={"slack": {"slack-1": ["slack:channels"]}},
            catalog_namespaces=config["namespaces"],
        ),
    )
    captured = {}

    async def fake_endpoint(_endpoint, request):
        captured["request"] = request
        return m.NamedServiceResponse.ok_response(namespace="slack")

    monkeypatch.setattr(m, "call_named_service_endpoint", fake_endpoint)

    result = await bridge.generic_call(
        namespace="slack",
        operation="object.list",
        filters_json="{}",
    )

    assert result["ok"] is True
    assert captured["request"].operation == "object.list"

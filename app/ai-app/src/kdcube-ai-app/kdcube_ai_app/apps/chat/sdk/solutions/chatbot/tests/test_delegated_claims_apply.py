# SPDX-License-Identifier: MIT

"""Connected-account claims narrow the turn's tool set — never block the turn.

Covers the live repro: an unmet claim on ONE provider's tools (e.g. Slack
unconnected) must drop exactly those tools, publish the provider + tool facts
to the ANNOUNCE `[INACTIVE TOOLS THIS TURN]` section via
`runtime_ctx.inactive_tools` (turn-local, so the cached instructions slice
stays byte-stable across claim-status changes), and let the turn proceed. A
user-disabled group's claims are pruned before any resolution, so they can
never prompt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube as delegated_pkg
from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import narrow_agent_tool_config
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
    connected_account_consent_payload,
    unavailable_tools_message,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ToolClaimPolicy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
    build_announce_inactive_tools_lines,
    build_announce_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


def _policy(tool_name: str, provider_id: str, claims: list[str]) -> ToolClaimPolicy:
    return ToolClaimPolicy.from_config(tool_name, {
        "connected_accounts": [
            {"provider_id": provider_id, "connector_app_id": "demo", "claims": claims},
        ],
    })


def _tool_cfg() -> AgentToolConfig:
    return AgentToolConfig(
        tool_specs=[
            {"alias": "web_tools", "module": "missing.web", "use_sk": True},
            {"alias": "gmail", "module": "missing.gmail", "use_sk": True},
            {"alias": "slack", "module": "missing.slack", "use_sk": True},
        ],
        allowed_plugins=["io_tools", "web_tools", "gmail", "slack"],
        allowed_tool_names_by_alias={
            "io_tools": ["tool_call"],
            "web_tools": ["web_search", "web_fetch"],
            "gmail": ["search_gmail", "send_gmail"],
            "slack": ["search_slack", "post_slack_message"],
        },
        tool_claim_policies=[
            _policy("gmail.search_gmail", "google", ["gmail:read"]),
            _policy("slack.search_slack", "slack", ["slack:search"]),
            _policy("slack.post_slack_message", "slack", ["slack:post"]),
        ],
    )


def _missing_slack() -> list[dict]:
    return [
        {
            "tool_name": "slack.search_slack",
            "failures": [{"provider_id": "slack", "connector_app_id": "demo", "claim": "slack:search"}],
        },
        {
            "tool_name": "slack.post_slack_message",
            "failures": [{"provider_id": "slack", "connector_app_id": "demo", "claim": "slack:post"}],
        },
    ]


class _Logger:
    def __init__(self):
        self.lines = []

    def log(self, message, level=None, **kwargs):
        self.lines.append((level, str(message)))


def _workflow_stub():
    stub = SimpleNamespace()
    stub.logger = _Logger()
    stub.runtime_ctx = SimpleNamespace(
        tenant="acme",
        project="demo",
        user_id="u1",
        inactive_tools=[],
    )
    stub.events = []

    async def _emit(evt):
        stub.events.append(evt)

    stub._emit = _emit
    return stub


def _patch_preflight(monkeypatch, result):
    calls = []

    async def fake_preflight(*, entrypoint, user_id, policies, tenant="", project="", **kwargs):
        calls.append(list(policies))
        return result

    monkeypatch.setattr(delegated_pkg, "preflight_tool_claim_policies", fake_preflight)
    return calls


@pytest.mark.asyncio
async def test_met_claims_keep_configured_set_and_reset_stale_notice(monkeypatch):
    stub = _workflow_stub()
    stub.runtime_ctx.inactive_tools = [{"provider_id": "stale"}]
    _patch_preflight(monkeypatch, {"ok": True, "checked": 3})
    cfg = _tool_cfg()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    assert out is cfg
    # Turn-local: a reused workflow never carries a stale announce forward.
    assert stub.runtime_ctx.inactive_tools == []
    assert stub.events == []


@pytest.mark.asyncio
async def test_unmet_claims_drop_tools_announce_and_proceed(monkeypatch):
    stub = _workflow_stub()
    _patch_preflight(monkeypatch, {
        "ok": False,
        "error": {"code": "needs_connected_account_consent", "message": "…"},
        "missing": _missing_slack(),
    })
    cfg = _tool_cfg()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)

    # Slack tools dropped (both were denied -> the whole group collapses)…
    assert "slack" not in out.allowed_plugins
    assert "slack" not in out.allowed_tool_names_by_alias
    # …everything else stays: the internet-search request keeps its tools.
    assert "web_tools" in out.allowed_plugins
    assert out.allowed_tool_names_by_alias["gmail"] == ["search_gmail", "send_gmail"]

    # The facts flow to the ANNOUNCE composer via runtime_ctx (turn-local).
    providers = stub.runtime_ctx.inactive_tools
    assert providers[0]["provider_id"] == "slack"
    assert set(providers[0]["tools"]) == {"slack.search_slack", "slack.post_slack_message"}

    # A notice event (not a blocker) went out, still carrying the consent
    # payload so the chat banner renders with the link.
    assert len(stub.events) == 1
    event = stub.events[0]
    assert event["step"] == "delegated_to_kdcube.claims"
    assert event["data"]["blocking"] is False
    assert event["data"]["providers"][0]["provider_id"] == "slack"


def test_announce_renders_inactive_tools_section():
    providers = [{
        "provider_id": "slack",
        "provider_label": "Slack",
        "connector_app_id": "demo",
        "claims": ["slack:search", "slack:post"],
        "tools": ["slack.search_slack", "slack.post_slack_message"],
    }]
    runtime = RuntimeCtx(inactive_tools=providers)
    lines = build_announce_inactive_tools_lines(runtime_ctx=runtime)
    assert lines[0] == "[INACTIVE TOOLS THIS TURN]"
    assert lines[1] == (
        "  - Slack tools (post_slack_message, search_slack): the user has no "
        "connected Slack account; they can connect one in Connection Hub."
    )
    assert "instead of attempting the call" in lines[2]

    announce = build_announce_text(
        iteration=0,
        max_iterations=8,
        started_at=None,
        timezone="UTC",
        timeline_blocks=[],
        runtime_ctx=runtime,
    )
    assert "[INACTIVE TOOLS THIS TURN]" in announce
    assert "Slack tools (post_slack_message, search_slack)" in announce

    # No inactive tools -> the section is absent entirely.
    clean = build_announce_text(
        iteration=0,
        max_iterations=8,
        started_at=None,
        timezone="UTC",
        timeline_blocks=[],
        runtime_ctx=RuntimeCtx(),
    )
    assert "[INACTIVE TOOLS THIS TURN]" not in clean


@pytest.mark.asyncio
async def test_partial_group_denial_keeps_other_tools(monkeypatch):
    stub = _workflow_stub()
    _patch_preflight(monkeypatch, {
        "ok": False,
        "missing": [
            {
                "tool_name": "slack.post_slack_message",
                "failures": [{"provider_id": "slack", "connector_app_id": "demo", "claim": "slack:post"}],
            },
        ],
    })
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, _tool_cfg())
    assert out.allowed_tool_names_by_alias["slack"] == ["search_slack"]
    assert stub.runtime_ctx.inactive_tools[0]["tools"] == ["slack.post_slack_message"]


@pytest.mark.asyncio
async def test_resolver_error_fails_open(monkeypatch):
    stub = _workflow_stub()

    async def boom(**kwargs):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(delegated_pkg, "preflight_tool_claim_policies", boom)
    cfg = _tool_cfg()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    assert out is cfg
    assert stub.runtime_ctx.inactive_tools == []


@pytest.mark.asyncio
async def test_no_policies_no_resolution():
    stub = _workflow_stub()
    cfg = AgentToolConfig(allowed_plugins=["io_tools"])
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    assert out is cfg
    assert stub.runtime_ctx.inactive_tools == []


@pytest.mark.asyncio
async def test_disabled_group_claims_never_resolve(monkeypatch):
    """Phase-1 guarantee, end to end: disabling a group prunes its claim
    policies BEFORE the claims check, so the resolver never sees them."""
    stub = _workflow_stub()
    seen = _patch_preflight(monkeypatch, {"ok": True, "checked": 1})

    cfg = narrow_agent_tool_config(_tool_cfg(), {"tools": {"slack": True}})
    assert all(not p.tool_name.startswith("slack") for p in cfg.tool_claim_policies)

    await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    assert len(seen) == 1
    assert [p.tool_name for p in seen[0]] == ["gmail.search_gmail"]


def test_notice_message_names_provider_and_tools():
    message = unavailable_tools_message(_missing_slack())
    assert message == (
        "Slack tools are inactive (search_slack, post_slack_message) — "
        "connect your Slack account in Connection Hub to use them."
    )

    payload = connected_account_consent_payload(
        tenant="acme",
        project="demo",
        connection_hub_bundle_id="connection-hub@1-0",
        missing=_missing_slack(),
    )
    assert payload["error"]["message"] == message
    assert payload["consent"]["url"].startswith("/api/integrations/bundles/acme/demo/connection-hub%401-0/")
    assert "widgets/connections_settings" in payload["consent"]["url"]

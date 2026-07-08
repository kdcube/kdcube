# SPDX-License-Identifier: MIT

"""Demand-driven consent: configured tools STAY in the turn's set.

Which tools a turn needs only becomes clear as the agent works, so the
turn-start hook keeps every claim-gated tool available and asks NOTHING —
consent raises at the tool ATTEMPT (see connected_accounts). The hook's one
job is the transition note: when an attempt-recorded consent demand is
satisfied (the user connected/approved mid-conversation), it publishes
`runtime_ctx.reactivated_tools` for the `[CONNECTED ACCOUNTS UPDATE]`
announce — checking ONLY the demanded tools, never sweeping the set.
User-disabled tools stay dropped by selection, which is a different door.
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
async def test_unmet_claims_keep_tools_available_and_stay_quiet(monkeypatch):
    """Demand-driven: the turn start neither drops claim-gated tools nor
    raises consent — the ask belongs to the attempt."""
    stub = _workflow_stub()
    calls = _patch_preflight(monkeypatch, {
        "ok": False,
        "error": {"code": "needs_connected_account_consent", "message": "…"},
        "missing": _missing_slack(),
    })
    cfg = _tool_cfg()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)

    # Every configured tool stays in the set…
    assert out is cfg
    assert "slack" in out.allowed_plugins
    assert out.allowed_tool_names_by_alias["slack"] == ["search_slack", "post_slack_message"]
    # …zero consent banners/events, zero inactive-tools announce…
    assert stub.events == []
    assert stub.runtime_ctx.inactive_tools == []
    # …and with no pending demand recorded, zero resolution work happens.
    assert calls == []


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
async def test_pending_demand_still_unmet_stays_pending_and_quiet(monkeypatch):
    """A recorded demand whose claims stay unmet keeps waiting: zero announce,
    zero events, the tool set untouched — the banner from the attempt is the
    single ask."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        record_consent_demand,
    )

    props = _install_fake_user_props(monkeypatch)
    stub = _conversation_stub()
    await record_consent_demand(
        user_id="u1", bundle_id="workspace@test", conversation_id="conv-1",
        provider_id="slack", connector_app_id="demo",
        claims=["slack:post"], tool_name="slack.post_slack_message",
    )
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
    assert out.allowed_tool_names_by_alias["slack"] == ["search_slack", "post_slack_message"]
    assert stub.runtime_ctx.inactive_tools == []
    assert stub.runtime_ctx.reactivated_tools == []
    assert stub.events == []
    assert props, "the demand stays pending for a later transition"


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
    """Selection stays a different door: disabling a group prunes its claim
    policies, so even a pending demand for a now-disabled tool resolves
    nothing and simply clears."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        record_consent_demand,
    )

    props = _install_fake_user_props(monkeypatch)
    stub = _conversation_stub()
    await record_consent_demand(
        user_id="u1", bundle_id="workspace@test", conversation_id="conv-1",
        provider_id="slack", connector_app_id="demo",
        claims=["slack:post"], tool_name="slack.post_slack_message",
    )
    seen = _patch_preflight(monkeypatch, {"ok": True, "checked": 1})

    cfg = narrow_agent_tool_config(_tool_cfg(), {"tools": {"slack": True}})
    assert all(not p.tool_name.startswith("slack") for p in cfg.tool_claim_policies)

    await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    # The demanded tool left the configured set (user turned it off): nothing
    # to resolve, no announce, and the conversation's pending snapshot clears —
    # no recurrence. (The hub-addressed registry entry may persist until a
    # matching grant consumes it; it drives event authoring, never announces.)
    assert seen == []
    assert stub.runtime_ctx.reactivated_tools == []
    snapshot_keys = [key for key in props if key[2] == "delegated_to_kdcube.blocked_snapshot"]
    assert not snapshot_keys, "the pending snapshot cleared with the tool deselected"


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
    # The payload message composition is owned by the connections stack and may
    # evolve; the stable contract asserted here: it NAMES the provider account
    # and points at Connection Hub (never the old anonymous wording).
    assert "Slack" in payload["error"]["message"]
    assert "Connection Hub" in payload["error"]["message"]
    assert payload["consent"]["url"].startswith("/api/integrations/bundles/acme/demo/connection-hub%401-0/")
    assert "widgets/connections_settings" in payload["consent"]["url"]


def _install_fake_user_props(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    props: dict[tuple[str, str, str], object] = {}

    def get_user_prop(key, *, user_id=None, bundle_id=None, default=None):
        return props.get((user_id or "", bundle_id or "", key), default)

    def set_user_prop(key, value, *, user_id=None, bundle_id=None):
        props[(user_id or "", bundle_id or "", key)] = value

    def delete_user_prop(key, *, user_id=None, bundle_id=None):
        props.pop((user_id or "", bundle_id or "", key), None)

    monkeypatch.setattr(sdk_config, "get_user_prop", get_user_prop)
    monkeypatch.setattr(sdk_config, "set_user_prop", set_user_prop)
    monkeypatch.setattr(sdk_config, "delete_user_prop", delete_user_prop)
    return props


def _conversation_stub():
    stub = _workflow_stub()
    stub.runtime_ctx.bundle_id = "workspace@test"
    stub.runtime_ctx.conversation_id = "conv-1"
    stub.runtime_ctx.reactivated_tools = []
    return stub


@pytest.mark.asyncio
async def test_consent_satisfied_mid_conversation_announces_the_transition(monkeypatch):
    """Surfaced live (log-verified): an attempt raised a consent demand, the
    user connected the account, and the next turn must carry the active-now
    signal — checking only the demanded tools — with no stale blocked note."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        record_consent_demand,
    )

    props = _install_fake_user_props(monkeypatch)
    stub = _conversation_stub()

    # A tool ATTEMPT recorded the demand (demand-driven; no turn-start block).
    assert await record_consent_demand(
        user_id="u1", bundle_id="workspace@test", conversation_id="conv-1",
        provider_id="slack", provider_label="Slack", connector_app_id="demo",
        claims=["slack:search"], tool_name="slack.search_slack",
    ) is True
    assert props, "the pending demand persists for the conversation"

    # Next turn: the user connected slack; the targeted check passes.
    calls = _patch_preflight(monkeypatch, {"ok": True, "checked": 1})
    cfg = _tool_cfg()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    assert out is cfg
    # Demand-scoped: ONLY the attempted tool's policy was resolved.
    assert [p.tool_name for p in calls[0]] == ["slack.search_slack"]
    # No stale blocked note…
    assert stub.runtime_ctx.inactive_tools == []
    # …and the transition is published for the ANNOUNCE composer.
    reactivated = stub.runtime_ctx.reactivated_tools
    assert reactivated and reactivated[0]["provider_id"] == "slack"
    assert reactivated[0]["tools"] == ["slack.search_slack"]

    # Steady state — the transition announces once, then stays quiet.
    _patch_preflight(monkeypatch, {"ok": True, "checked": 1})
    await BaseWorkflow.apply_delegated_tool_claims(stub, _tool_cfg())
    assert stub.runtime_ctx.reactivated_tools == []
    assert stub.runtime_ctx.inactive_tools == []


def test_announce_renders_connected_accounts_update():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import (
        build_announce_reactivated_tools_lines,
    )

    providers = [{
        "provider_id": "slack",
        "provider_label": "Slack",
        "connector_app_id": "demo",
        "claims": ["slack:search", "slack:post"],
        "tools": ["slack.search_slack", "slack.post_slack_message"],
    }]
    runtime = RuntimeCtx(reactivated_tools=providers)
    lines = build_announce_reactivated_tools_lines(runtime_ctx=runtime)
    assert lines[0] == "[CONNECTED ACCOUNTS UPDATE]"
    assert "Slack account is connected; tools (post_slack_message, search_slack) are active this turn." in lines[1]
    assert "supersedes earlier notes" in lines[2]

    announce = build_announce_text(
        iteration=0,
        max_iterations=8,
        started_at=None,
        timezone="UTC",
        timeline_blocks=[],
        runtime_ctx=runtime,
    )
    assert "[CONNECTED ACCOUNTS UPDATE]" in announce
    # The stale blocked section stays out when everything is active.
    assert "[INACTIVE TOOLS THIS TURN]" not in announce


@pytest.mark.asyncio
async def test_blocking_consent_bookkeeping_never_blocks_the_turn(monkeypatch):
    """Fail-open budget: a hanging snapshot store (DB stall, secrets stall —
    any of it) lets the turn proceed with bookkeeping skipped and a warning."""
    import time as _time
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    def hanging_get_user_prop(key, *, user_id=None, bundle_id=None, default=None):
        # Far beyond the 0.3s test budget; short enough that the leftover
        # worker thread drains quickly at loop close.
        _time.sleep(2)
        return default

    monkeypatch.setattr(sdk_config, "get_user_prop", hanging_get_user_prop)

    stub = _conversation_stub()
    stub.DELEGATED_CONSENT_TURN_BUDGET_SECONDS = 0.3
    cfg = _tool_cfg()
    import time as _clock
    started = _clock.monotonic()
    out = await BaseWorkflow.apply_delegated_tool_claims(stub, cfg)
    elapsed = _clock.monotonic() - started

    assert out is cfg
    assert elapsed < 2.0, "the budget bounded the turn-start hook"
    assert any("fail-open" in line for _lvl, line in stub.logger.lines), "the skip is named in a warning"
    assert stub.runtime_ctx.inactive_tools == []
    assert stub.runtime_ctx.reactivated_tools == []

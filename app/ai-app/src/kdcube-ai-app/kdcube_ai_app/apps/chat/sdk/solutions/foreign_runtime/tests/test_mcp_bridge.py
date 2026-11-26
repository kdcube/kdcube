# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The runtime-neutral delegated-MCP bridge (foreign_runtime/mcp_bridge.py).

Offline: `resolve_mcp_server_map` is stubbed via monkeypatch — no network, no
hub. What is proven: the agent's delegated-client id derivation, the drop_sink
pass-through, and the connect-required outcome shaping (consent_pending drops
produce the payload; operational drop reasons never do).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_mcp import (
    DROP_CONSENT_PENDING,
    DROP_MINT_ERROR,
    DROP_NO_USER,
    DROP_PROVIDER_ERROR,
    delegated_client_id_for_agent,
)
from kdcube_ai_app.apps.chat.sdk.solutions.foreign_runtime import mcp_bridge
from kdcube_ai_app.infra.accounting import clear_context


# ── connect_required_outcome (pure shaping) ──────────────────────────────────

def test_consent_pending_drops_shape_the_connect_required_payload() -> None:
    drop_sink = {
        "gmail": DROP_CONSENT_PENDING,
        "calendar": DROP_CONSENT_PENDING,
    }
    outcome = mcp_bridge.connect_required_outcome(
        drop_sink, connection_hub_url="https://hub.example/connections"
    )
    assert outcome == {
        "status": "connect_required",
        "connections": ["gmail", "calendar"],
        "connection_hub_url": "https://hub.example/connections",
    }


def test_operational_drop_reasons_are_excluded() -> None:
    drop_sink = {
        "gmail": DROP_CONSENT_PENDING,
        "broken": DROP_PROVIDER_ERROR,
        "anon": DROP_NO_USER,
        "mintless": DROP_MINT_ERROR,
    }
    outcome = mcp_bridge.connect_required_outcome(drop_sink)
    assert outcome is not None
    assert outcome["connections"] == ["gmail"]
    assert outcome["connection_hub_url"] is None


def test_no_consent_pending_drops_yield_none() -> None:
    assert mcp_bridge.connect_required_outcome({}) is None
    assert mcp_bridge.connect_required_outcome(None) is None
    assert mcp_bridge.connect_required_outcome({"broken": DROP_PROVIDER_ERROR}) is None


# ── resolve_turn_mcp (stubbed resolver) ──────────────────────────────────────

def test_resolve_turn_mcp_derives_the_agent_client_id_and_passes_the_drop_sink(monkeypatch) -> None:
    captured: dict = {}

    async def _stub_resolver(connections, *, user_sub=None, client_id="", bearer_provider=None, drop_sink=None, **kw):
        captured["connections"] = connections
        captured["user_sub"] = user_sub
        captured["client_id"] = client_id
        captured["bearer_provider"] = bearer_provider
        captured["drop_sink"] = drop_sink
        if drop_sink is not None:
            drop_sink["gmail"] = DROP_CONSENT_PENDING
        return {"tasks": {"url": "https://mcp.example/tasks", "transport": "streamable_http"}}

    monkeypatch.setattr(mcp_bridge, "resolve_mcp_server_map", _stub_resolver)

    conns = [{"kind": "mcp", "server_id": "tasks", "url": "https://mcp.example/tasks"}]
    drop_sink: dict = {}
    entrypoint = SimpleNamespace()  # untouched: user_sub given, resolver stubbed
    server_map = asyncio.run(mcp_bridge.resolve_turn_mcp(
        entrypoint, conns,
        agent_id="lg-react", application="my-app@1-0",
        user_sub="user-1", drop_sink=drop_sink,
    ))

    # The server map is the resolver's, untouched.
    assert set(server_map) == {"tasks"}
    # client_id derivation: the agent IS a delegated-client entity keyed by
    # application + agent_id.
    expected = delegated_client_id_for_agent("my-app@1-0", "lg-react")
    assert captured["client_id"] == expected == "kdcube-agent:my-app@1-0:lg-react"
    # The SAME drop_sink dict is passed through, so the caller reads the drops.
    assert captured["drop_sink"] is drop_sink
    assert drop_sink == {"gmail": DROP_CONSENT_PENDING}
    # Explicit user_sub is honored verbatim; a bearer provider is always wired.
    assert captured["user_sub"] == "user-1"
    assert callable(captured["bearer_provider"])
    assert captured["connections"] == conns
    # ...and the drops shape the structured outcome.
    assert mcp_bridge.connect_required_outcome(drop_sink) == {
        "status": "connect_required",
        "connections": ["gmail"],
        "connection_hub_url": None,
    }


def test_resolve_turn_mcp_resolves_the_user_from_the_turn_context_when_unset(monkeypatch) -> None:
    captured: dict = {}

    async def _stub_resolver(connections, *, user_sub=None, **kw):
        captured["user_sub"] = user_sub
        return {}

    monkeypatch.setattr(mcp_bridge, "resolve_mcp_server_map", _stub_resolver)
    monkeypatch.setattr(mcp_bridge, "current_turn_user_sub", lambda ep: "ctx-user")

    asyncio.run(mcp_bridge.resolve_turn_mcp(
        SimpleNamespace(), [], agent_id="a", application="app",
    ))
    assert captured["user_sub"] == "ctx-user"


# ── current_turn_user_sub ────────────────────────────────────────────────────

def test_current_turn_user_sub_is_empty_when_nothing_is_bound() -> None:
    class _NoTurn:
        @property
        def comm(self):
            raise RuntimeError("no turn task bound")

    # Establish the unbound state explicitly; test order must not supply identity.
    clear_context()
    try:
        assert mcp_bridge.current_turn_user_sub(_NoTurn()) == ""
    finally:
        clear_context()


def test_current_turn_user_sub_falls_back_to_the_comm_user() -> None:
    ep = SimpleNamespace(comm=SimpleNamespace(user_id="comm-user"))
    assert mcp_bridge.current_turn_user_sub(ep) == "comm-user"


# ── announce_connect_required (the house consent card) ───────────────────────

_CONN = {
    "name": "press",
    "kind": "mcp",
    "server_id": "press",
    "url": "https://host/mcp/press",
    "resource": "*/mcp/press*",
    "delegated": True,
    "scopes": ["press:read"],
}


def _announce_recorder(monkeypatch) -> list:
    """Capture what the seam hands the SDK's consent announcer."""
    captured: list = []

    async def _announce(consent):
        captured.append(consent)

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.mcp_consent.announce_agent_consent",
        _announce,
    )
    return captured


def test_consent_pending_drops_raise_one_demand_per_connection(monkeypatch) -> None:
    captured = _announce_recorder(monkeypatch)

    announced = asyncio.run(mcp_bridge.announce_connect_required(
        SimpleNamespace(), [dict(_CONN)], {"press": DROP_CONSENT_PENDING},
        agent_id="press", application="my-app@1-0", tenant="t1", project="p1",
    ))

    assert [item["server_id"] for item in announced] == ["press"]
    assert len(captured) == 1
    consent = captured[0]
    # The demand is the SAME MCPConsentRequired a KDCube-MCP 403 produces, so
    # the chat's one banner path serves both.
    payload = consent.chat_event_payload()
    assert payload["error"]["code"] == "needs_connected_account_consent"
    block = payload["consent"]
    assert block["agent_client_id"] == "kdcube-agent:my-app@1-0:press"
    assert block["claims"] == ["press:read"]
    assert block["resource"] == "*/mcp/press*"
    assert block["grant"]["operation"] == "delegated_agent_grant_create"


def test_operational_drops_raise_no_demand(monkeypatch) -> None:
    captured = _announce_recorder(monkeypatch)

    announced = asyncio.run(mcp_bridge.announce_connect_required(
        SimpleNamespace(), [dict(_CONN)], {"press": DROP_PROVIDER_ERROR},
        agent_id="press", application="my-app@1-0",
    ))

    assert announced == []
    assert captured == []
    assert asyncio.run(mcp_bridge.announce_connect_required(
        SimpleNamespace(), [dict(_CONN)], {}, agent_id="press", application="my-app@1-0",
    )) == []


def test_the_hub_link_is_the_platforms_and_absent_without_a_public_base(monkeypatch) -> None:
    """The seam never hand-writes a hub path: the link comes from the platform
    builder over the deployment's public base URL, so with none bound the demand
    simply carries no URL (the card still acts through its grant fields)."""
    captured = _announce_recorder(monkeypatch)
    calls: list = []

    def _hub_url(*, tenant, project, client_id, resource, claims, **kw):
        calls.append({"tenant": tenant, "project": project, "client_id": client_id,
                      "resource": resource, "claims": list(claims)})
        return ""

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials"
        ".consent_denial.connection_hub_grant_url",
        _hub_url,
    )

    asyncio.run(mcp_bridge.announce_connect_required(
        SimpleNamespace(), [dict(_CONN)], {"press": DROP_CONSENT_PENDING},
        agent_id="press", application="my-app@1-0", tenant="t1", project="p1",
    ))

    assert calls == [{
        "tenant": "t1", "project": "p1",
        "client_id": "kdcube-agent:my-app@1-0:press",
        "resource": "*/mcp/press*", "claims": ["press:read"],
    }]
    assert captured[0].chat_event_payload()["consent"]["url"] == ""


# ── claude_code_mcp_servers (the Claude Code binding step) ───────────────────

def test_neutral_map_binds_to_the_claude_code_workspace_shape() -> None:
    """The seam stops at the neutral map; this is the per-runtime binding the
    module header names. The SDK writes .mcp.json from the result, so an app
    hands it to ClaudeCodeWorkspaceConfig and writes no file itself."""
    bound = mcp_bridge.claude_code_mcp_servers({
        "press": {
            "url": "https://host/mcp/press",
            "transport": "streamable_http",
            "headers": {"Authorization": "Bearer turn-token"},
        },
        "sse_server": {"url": "https://host/sse", "transport": "sse"},
        "bare": {"url": "https://host/plain"},
    })
    assert bound == {
        "press": {
            "type": "http",
            "url": "https://host/mcp/press",
            "headers": {"Authorization": "Bearer turn-token"},
        },
        "sse_server": {"type": "sse", "url": "https://host/sse"},
        # No transport declared: the streamable-http default, and no headers key
        # when the resolution bound no bearer.
        "bare": {"type": "http", "url": "https://host/plain"},
    }


def test_half_formed_entries_are_dropped_not_written() -> None:
    assert mcp_bridge.claude_code_mcp_servers({
        "no_url": {"transport": "http"},
        "": {"url": "https://host/mcp"},
        "not_a_mapping": "nope",
    }) == {}
    assert mcp_bridge.claude_code_mcp_servers({}) == {}
    assert mcp_bridge.claude_code_mcp_servers(None) == {}


# ── narrow_mcp_connections (the pick, applied BEFORE any server contact) ─────

_PRESS = {
    "name": "press", "kind": "mcp", "server_id": "press", "alias": "press",
    "url": "https://host/mcp/press", "delegated": True, "scopes": ["press:read"],
    "allowed": ["search", "get_post", "commit_entry"],
}
_DOOR = {
    "name": "named_services", "kind": "mcp", "server_id": "named_services",
    "alias": "named_services", "url": "https://host/mcp/named_services",
}
_ROSTER = {
    "name": "roster", "kind": "named_service", "alias": "named_services",
    "namespaces": {"linkedin": {"allowed": ["object.search"]}},
}


def test_a_server_turned_off_whole_is_dropped_before_resolution() -> None:
    dropped: list[str] = []
    kept = mcp_bridge.narrow_mcp_connections(
        [_PRESS, _DOOR], {"named_services": True}, dropped_sink=dropped
    )
    assert [c["server_id"] for c in kept] == ["press"]
    assert dropped == ["named_services"]


def test_a_partial_denial_keeps_the_connection() -> None:
    """A tool-level opt-out narrows what the model may call; the server itself
    still has to be reachable for the surviving tools."""
    kept = mcp_bridge.narrow_mcp_connections([_PRESS], {"press": ["commit_entry"]})
    assert [c["server_id"] for c in kept] == ["press"]


def test_nothing_picked_keeps_the_whole_declared_inventory() -> None:
    for disabled in ({}, None):
        kept = mcp_bridge.narrow_mcp_connections([_PRESS, _DOOR], disabled)
        assert [c["server_id"] for c in kept] == ["press", "named_services"]


def test_non_mcp_entries_pass_through_untouched() -> None:
    kept = mcp_bridge.narrow_mcp_connections([_PRESS, _ROSTER], {"press": True})
    assert [c.get("kind") for c in kept] == ["named_service"]


def test_a_dropped_server_is_never_resolved_and_mints_no_bearer(monkeypatch) -> None:
    """The ordering contract end to end: narrow first, resolve second — the
    resolver never sees a server the user turned off, so no grant token is read
    for it and it can raise no consent card."""
    seen: dict = {}

    async def _stub_resolver(connections, *, user_sub=None, client_id="", bearer_provider=None, drop_sink=None, **kw):
        seen["connections"] = [c.get("server_id") for c in connections]
        return {}

    monkeypatch.setattr(mcp_bridge, "resolve_mcp_server_map", _stub_resolver)
    entrypoint = SimpleNamespace(comm=SimpleNamespace(user_id="operator-1"))
    kept = mcp_bridge.narrow_mcp_connections([_PRESS, _DOOR], {"press": True})
    asyncio.run(
        mcp_bridge.resolve_turn_mcp(
            entrypoint, kept, agent_id="press", application="press@2026-08-16"
        )
    )
    assert seen["connections"] == ["named_services"]


# ── claude_code_tool_rules (the permission grammar, no wildcards) ────────────

def test_an_untouched_server_is_allowed_whole() -> None:
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [_PRESS], {}, server_ids=("press",), base_allowed=("Read", "Write")
    )
    assert allow == ("Read", "Write", "mcp__press")
    assert deny == ()


def test_a_partial_denial_names_the_survivors_from_the_declaration() -> None:
    """The declaration enumerates the server's tools, so the survivors are known
    from config alone — no live tools/list handshake."""
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [_PRESS], {"press": ["commit_entry"]}, server_ids=("press",)
    )
    assert allow == ("mcp__press__search", "mcp__press__get_post")
    assert deny == ("mcp__press__commit_entry",)


def test_a_wildcard_server_falls_back_to_denying_the_removed_tools() -> None:
    wildcard = {**_PRESS, "allowed": ["*"]}
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [wildcard], {"press": ["commit_entry"]}, server_ids=("press",)
    )
    assert allow == ("mcp__press",)
    assert deny == ("mcp__press__commit_entry",)


def test_a_server_that_did_not_resolve_gets_no_rule() -> None:
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [_PRESS, _DOOR], {}, server_ids=("press",)
    )
    assert allow == ("mcp__press",)
    assert deny == ()


def test_tool_overrides_carry_a_narrowing_computed_elsewhere() -> None:
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [_DOOR], {},
        server_ids=("named_services",),
        tool_overrides={"named_services": ["named_services_list", "named_services_search"]},
    )
    assert allow == (
        "mcp__named_services__named_services_list",
        "mcp__named_services__named_services_search",
    )
    assert deny == ()


def test_an_override_that_survives_nothing_denies_the_server_whole() -> None:
    allow, deny = mcp_bridge.claude_code_tool_rules(
        [_DOOR], {}, server_ids=("named_services",), tool_overrides={"named_services": []}
    )
    assert allow == ()
    assert deny == ("mcp__named_services",)


def test_connection_allowed_tools_reads_wildcards_as_unenumerable() -> None:
    assert mcp_bridge.connection_allowed_tools(_PRESS) == ["search", "get_post", "commit_entry"]
    assert mcp_bridge.connection_allowed_tools({"allowed": ["*"]}) == []
    assert mcp_bridge.connection_allowed_tools({}) == []


# ── A fixed-header runtime gets a per_call turn token ───────────────────

_DELEGATED = [{"kind": "mcp", "server_id": "hub", "url": "https://hub/mcp", "delegated": True},
              {"kind": "mcp", "server_id": "plain", "url": "https://plain/mcp"}]


def _w40_world(monkeypatch, answer):
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.runtime import agent_capability_control

    seen = {}

    async def _resolver(connections, **kwargs):
        seen["servers"] = [conn["server_id"] for conn in connections]
        return {conn["server_id"]: {"url": conn["url"], "headers": {"Authorization": "Bearer card"}}
                for conn in connections}

    async def _token(entrypoint, **kwargs):
        seen["request"] = kwargs
        return answer

    monkeypatch.setattr(mcp_bridge, "resolve_mcp_server_map", _resolver)
    monkeypatch.setattr(agent_capability_control, "request_agent_turn_token", _token)
    entrypoint = SimpleNamespace(comm=SimpleNamespace(user_id="u1", conversation={"conversation_id": "c1",
                                                                                    "turn_id": "t1"}))
    return entrypoint, seen


def test_delegated_servers_carry_a_per_call_turn_token(monkeypatch) -> None:
    entrypoint, seen = _w40_world(monkeypatch, {"ok": True, "turn_token": "ts1.per.call"})
    server_map = asyncio.run(mcp_bridge.resolve_turn_mcp(entrypoint, list(_DELEGATED), agent_id="lg-react",
                                                         application="app@1-0", user_sub="u1"))
    assert server_map["hub"]["headers"] == {"Authorization": "Bearer card", "x-connection-hub-turn": "ts1.per.call"}
    assert "x-connection-hub-turn" not in server_map["plain"]["headers"]
    assert seen["request"] == {"agent_id": "lg-react", "conversation_id": "c1", "turn_id": "t1", "mode": "per_call"}


def test_a_refused_turn_token_keeps_the_delegated_servers_out_of_the_turn(monkeypatch) -> None:
    entrypoint, seen = _w40_world(monkeypatch, {"ok": False, "error": "agent_authority_updating"})
    drops = {}
    server_map = asyncio.run(mcp_bridge.resolve_turn_mcp(entrypoint, list(_DELEGATED), agent_id="lg-react",
                                                         application="app@1-0", user_sub="u1", drop_sink=drops))
    assert list(server_map) == ["plain"] and seen["servers"] == ["plain"]
    assert drops == {"hub": mcp_bridge.DROP_TURN_TOKEN_UNAVAILABLE}


def test_an_automation_run_asks_no_turn_token_and_keeps_its_delegated_servers(monkeypatch) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context

    entrypoint, seen = _w40_world(monkeypatch, {"ok": False, "error": "conversation_not_owned"})
    with bind_current_bundle_call_context({"kind": "automation_execution", "conversation_id": "automation_job_1"}):
        server_map = asyncio.run(mcp_bridge.resolve_turn_mcp(entrypoint, list(_DELEGATED), agent_id="lg-react",
                                                             application="app@1-0", user_sub="u1"))
    # The Agent Card defaults apply: no token requested, no turn header, nothing dropped.
    assert "request" not in seen
    assert sorted(server_map) == ["hub", "plain"]
    assert server_map["hub"]["headers"] == {"Authorization": "Bearer card"}

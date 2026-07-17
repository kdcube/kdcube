"""The "tools, both ways" seam (platform/tools_mcp.py) — thin over the SDK.

The mechanism moved to SDK: `solutions/connections/delegated_mcp` resolves the
per-user MCP server map (minting a delegated bearer for delegated connections)
and `frameworks/langchain/mcp` binds it as LangChain tools. This bundle module
is the thin adapter (connection list + turn user -> LangChain tools). Asserts:
only `kind: mcp` connections are considered, and clean degradation (no MCP
connections / adapter absent -> [] , the agent still builds with plain tools).
Fully offline.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _mcp_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "tools_mcp.py")
    return module


_PLAIN_CONNS = [
    {"name": "calc", "kind": "python", "alias": "calc", "allowed": ["calc"]},
    {"name": "code_exec", "kind": "python", "alias": "code_exec", "allowed": ["run_python"]},
]
_MCP_CONN = {"name": "memory", "kind": "mcp", "url": "https://h/api/mcp/mem", "delegated": True, "scopes": ["memories:read"]}


def test_mcp_connections_filters_by_kind() -> None:
    m = _mcp_module()
    assert m.mcp_connections(_PLAIN_CONNS) == []
    assert m.mcp_connections(_PLAIN_CONNS + [_MCP_CONN]) == [_MCP_CONN]


def test_no_mcp_connection_returns_empty() -> None:
    m = _mcp_module()
    # No kind:mcp declared -> no MCP tools (the agent still builds with plain tools).
    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(_PLAIN_CONNS, user_sub="u"))
    assert tools == [] and consents == []


def test_mcp_connection_degrades_to_empty_when_adapter_absent() -> None:
    m = _mcp_module()
    # langchain-mcp-adapters is not installed in the test env: the delegated MCP
    # connection resolves a server map but binding degrades to [] — never a crash.
    # (No user -> the delegated server is dropped before any bind is attempted.)
    tools, consents = asyncio.run(m.load_mcp_tools_for_connections([_MCP_CONN], user_sub=None))
    assert tools == [] and consents == []


def test_consent_pending_drop_raises_a_demand_not_a_silent_gap() -> None:
    # REGRESSION (surfaced live 2026-07-17): with the consented-token path, a
    # connection whose user hasn't granted THIS agent is dropped BEFORE any
    # server contact — no 403 ever happens. The demand must come from the drop,
    # otherwise the agent silently loses the tool and tells the user the
    # capability does not exist.
    m = _mcp_module()

    async def no_grant(conn, user_sub):
        return None  # consent pending for this agent

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_MCP_CONN], user_sub="u1",
        application="ported-langgraph-agents@2026-07-13", agent_id="lg-react",
        bearer_provider=no_grant,
    ))
    assert tools == []
    assert len(consents) == 1
    c = consents[0]
    assert c.claims == ["memories:read"]
    # The demand is actionable: it carries the one-click grant for THIS agent.
    grant = c.consent["grant"]
    assert grant["payload"]["client_id"] == "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    assert grant["payload"]["resource"] == "https://h/api/mcp/mem"


def test_no_user_drop_stays_silent() -> None:
    # An anonymous turn cannot grant; the delegated connection is dropped with
    # no demand (nothing for the user to act on).
    m = _mcp_module()

    async def boom(conn, user_sub):  # must not even be called without a user
        raise AssertionError("bearer provider must not run without a user")

    tools, consents = asyncio.run(m.load_mcp_tools_for_connections(
        [_MCP_CONN], user_sub=None, application="a", agent_id="b", bearer_provider=boom,
    ))
    assert tools == [] and consents == []


def test_user_opt_out_drops_the_mcp_connection() -> None:
    m = _mcp_module()
    # The picker deny-map opts the whole MCP tool out this turn -> it is not bound
    # (governance: admin-declared ∩ user-enabled, same as plain/code-exec tools).
    # _MCP_CONN's name/alias is "memory".
    assert m.mcp_connections([_MCP_CONN], None) == [_MCP_CONN]          # not opted out -> kept
    assert m.mcp_connections([_MCP_CONN], {"memory": True}) == []       # opted out -> dropped
    assert m.mcp_connections([_MCP_CONN], {"other": True}) == [_MCP_CONN]  # unrelated opt-out ignored

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
    assert asyncio.run(m.load_mcp_tools_for_connections(_PLAIN_CONNS, user_sub="u")) == []


def test_mcp_connection_degrades_to_empty_when_adapter_absent() -> None:
    m = _mcp_module()
    # langchain-mcp-adapters is not installed in the test env: the delegated MCP
    # connection resolves a server map but binding degrades to [] — never a crash.
    # (No user -> the delegated server is dropped before any bind is attempted.)
    assert asyncio.run(m.load_mcp_tools_for_connections([_MCP_CONN], user_sub=None)) == []


def test_user_opt_out_drops_the_mcp_connection() -> None:
    m = _mcp_module()
    # The picker deny-map opts the whole MCP tool out this turn -> it is not bound
    # (governance: admin-declared ∩ user-enabled, same as plain/code-exec tools).
    # _MCP_CONN's name/alias is "memory".
    assert m.mcp_connections([_MCP_CONN], None) == [_MCP_CONN]          # not opted out -> kept
    assert m.mcp_connections([_MCP_CONN], {"memory": True}) == []       # opted out -> dropped
    assert m.mcp_connections([_MCP_CONN], {"other": True}) == [_MCP_CONN]  # unrelated opt-out ignored

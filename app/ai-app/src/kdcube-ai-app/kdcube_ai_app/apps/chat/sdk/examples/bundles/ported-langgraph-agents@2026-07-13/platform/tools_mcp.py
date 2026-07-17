# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools_mcp.py ── the "tools, both ways" seam (thin over the SDK) ──
#
# The preserved agent binds PLAIN LangChain tools (solution/tools.py) — "bring your
# own tools", external to the host and, running no accounted model calls, unmetered.
# This module adds the SECOND way: bind a KDCube-served MCP endpoint's tools as
# LangChain tools.
#
# The mechanism is now SHARED SDK, reused by any hosted LangGraph/LangChain agent:
#   - `solutions/connections/delegated_mcp.resolve_mcp_server_map` — framework-neutral:
#     turn the agent's `kind: mcp` connections into an MCP server map, minting a
#     per-user DELEGATED bearer for any connection marked `delegated: true` (the same
#     `@mcp`-surface auth platform bundles use) and injecting it; static connections
#     keep their declared headers.
#   - `frameworks/langchain/mcp.load_mcp_tools_from_server_map` — bind that map as
#     LangChain tools via `langchain-mcp-adapters` (degrades to none when absent).
#
# This bundle file is the thin adapter: pass the agent's connection list + this
# turn's user, get LangChain tools.
#
# ACCOUNTING (the honest rule — "marked = counted"): binding a tool via MCP does not
# by itself make it accounted; a tool whose KDCube-side implementation runs a marked
# model call IS metered, a plain lookup is not.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import (
    resolve_mcp_server_map,
    delegated_client_id_for_agent,
    is_delegated_connection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_consent import (
    MCPConsentRequired,
    mcp_consent_from_denial,
)
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain.mcp import (
    load_mcp_tools_from_server_map,
    load_error_looks_like_denial,
    mcp_adapters_available,  # re-exported for callers/tests
)

logger = logging.getLogger(__name__)

__all__ = ["mcp_connections", "load_mcp_tools_for_connections", "mcp_adapters_available"]


def _conn_alias(conn: Mapping[str, Any]) -> str:
    return str(conn.get("alias") or conn.get("name") or "").strip()


def mcp_connections(
    connections: List[Dict[str, Any]],
    disabled_map: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """The `kind: mcp` entries of the agent's declared tool-connection list, minus
    any the user opted OUT of this turn (whole-tool opt-out `{alias: true}` from the
    capabilities picker deny-map) — the same admin-ceiling ∩ user-enabled narrowing
    the plain/code-exec tools get, so MCP tools are governed too ("which agent")."""
    disabled = disabled_map or {}
    out: List[Dict[str, Any]] = []
    for c in connections or []:
        if not (isinstance(c, dict) and str(c.get("kind") or "").strip().lower() == "mcp"):
            continue
        if disabled.get(_conn_alias(c)) is True:
            continue
        out.append(c)
    return out


async def load_mcp_tools_for_connections(
    connections: List[Dict[str, Any]],
    *,
    user_sub: Optional[str] = None,
    disabled_map: Optional[Mapping[str, Any]] = None,
    application: str = "",
    agent_id: str = "",
) -> tuple[List[Any], List[MCPConsentRequired]]:
    """Bind the agent's declared, user-enabled `kind: mcp` connections as LangChain
    tools for THIS turn's user, AS this agent.

    The delegated bearer is minted under the AGENT's delegated-client identity
    (`application` + `agent_id`) — the agent is a "Delegated By KDCube" entity, so
    consent is per-agent. Returns ``(tools, consent_demands)``: when a KDCube
    `@mcp` load is denied for missing consent (a 403 at connect time), the tools
    are absent and a ``MCPConsentRequired`` is returned for each delegated
    connection so the caller can bubble it into chat and explain it to the agent.
    Never raises."""
    conns = mcp_connections(connections, disabled_map)
    if not conns:
        return [], []
    client_id = delegated_client_id_for_agent(application, agent_id)
    server_map = await resolve_mcp_server_map(conns, user_sub=user_sub, client_id=client_id)
    error_sink: Dict[str, Any] = {}
    tools = await load_mcp_tools_from_server_map(server_map, error_sink=error_sink)

    consents: List[MCPConsentRequired] = []
    load_error = error_sink.get("_load_error")
    if load_error is not None and load_error_looks_like_denial(load_error) and not tools:
        # A consent/auth denial at connect time — shape a consent demand for each
        # delegated connection whose claims the user hasn't granted THIS agent.
        for c in conns:
            if not is_delegated_connection(c):
                continue
            claims = c.get("scopes") or c.get("claims") or []
            if isinstance(claims, str):
                claims = [claims]
            consents.append(mcp_consent_from_denial(
                {"status": 403, "reason": "authority_mismatch"},
                resource=str(c.get("url") or ""),
                claims=claims,
                tool_name=str(c.get("alias") or c.get("name") or ""),
            ))
    return tools, consents

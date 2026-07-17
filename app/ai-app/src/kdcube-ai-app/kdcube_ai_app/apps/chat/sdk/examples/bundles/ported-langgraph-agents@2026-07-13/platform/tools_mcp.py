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
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_mcp import resolve_mcp_server_map
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain.mcp import (
    load_mcp_tools_from_server_map,
    mcp_adapters_available,  # re-exported for callers/tests
)

logger = logging.getLogger(__name__)

__all__ = ["mcp_connections", "load_mcp_tools_for_connections", "mcp_adapters_available"]


def mcp_connections(connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The `kind: mcp` entries of the agent's declared tool-connection list."""
    return [
        c for c in (connections or [])
        if isinstance(c, dict) and str(c.get("kind") or "").strip().lower() == "mcp"
    ]


async def load_mcp_tools_for_connections(
    connections: List[Dict[str, Any]],
    *,
    user_sub: Optional[str] = None,
) -> List[Any]:
    """Bind the agent's declared `kind: mcp` connections as LangChain tools for
    THIS turn's user. Delegated connections get a minted per-user bearer; static
    ones keep their headers. Always returns a list (degrades to [] on any absence
    or failure), so a graph build never fails over an optional MCP source."""
    conns = mcp_connections(connections)
    if not conns:
        return []
    server_map = await resolve_mcp_server_map(conns, user_sub=user_sub)
    return await load_mcp_tools_from_server_map(server_map)

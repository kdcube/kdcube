# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools_mcp.py ── the "tools, both ways" seam ──
#
# The vendored agent binds PLAIN LangChain tools (solution/tools.py) — "bring your
# own tools", external to the host and, since they run no accounted model calls,
# unmetered. This module adds the SECOND way to give the same prebuilt agent
# tools: load a KDCube-served MCP endpoint's tools as LangChain tools via
# `langchain-mcp-adapters` and bind them into `create_agent` alongside (or
# instead of) the plain ones.
#
# ACCOUNTING (the honest rule — "marked = counted"):
#   - A plain tool, or an MCP tool that only does a lookup, runs NO KDCube-marked
#     model call, so it is NOT metered. It is just an external capability.
#   - An MCP tool whose implementation runs KDCube-marked model calls (e.g. a
#     knowledge tool that embeds a query through the accounted model service) IS
#     metered — the marking, not the transport, is what makes a call counted.
#   Binding a tool via MCP does not by itself make it accounted; what the tool
#   does on the KDCube side does.
#
# DEGRADATION: `langchain-mcp-adapters` is an OPTIONAL dependency. When it is not
# installed, `load_mcp_tools` returns [] and the agent is built with plain tools
# only. Installing it (`pip install langchain-mcp-adapters`) plus pointing config
# at a reachable KDCube MCP endpoint turns the MCP tool set on — no code change.

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def mcp_adapters_available() -> bool:
    """Whether the optional `langchain-mcp-adapters` package is importable."""
    try:
        import langchain_mcp_adapters  # noqa: F401
        return True
    except Exception:
        return False


def build_mcp_server_config(mcp_cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Translate this bundle's `config.tools.mcp.servers` into the
    `MultiServerMCPClient` server map.

    Expected bundle config shape (one or more servers)::

        tools:
          mode: both          # plain | mcp | both
          mcp:
            servers:
              knowledge:
                url: "https://<host>/api/mcp/<tenant>/<project>/<bundle>/mcp"
                transport: streamable_http
                # Optional auth header for a protected KDCube MCP endpoint.
                headers:
                  Authorization: "Bearer <token>"

    A KDCube-served MCP endpoint (e.g. a knowledge MCP) is reached over
    `streamable_http` at its published URL, with an optional bearer header. This
    is exactly the transport KDCube's own MCP adapter uses.
    """
    servers: Dict[str, Dict[str, Any]] = {}
    for name, spec in (mcp_cfg.get("servers") or {}).items():
        url = (spec or {}).get("url")
        if not url:
            continue
        entry: Dict[str, Any] = {
            "url": url,
            "transport": (spec.get("transport") or "streamable_http"),
        }
        if spec.get("headers"):
            entry["headers"] = dict(spec["headers"])
        servers[str(name)] = entry
    return servers


def mcp_cfg_from_connections(connections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the `{servers: {...}}` shape `load_mcp_tools` consumes from the agent's
    `kind: mcp` tool-connection entries (the standard per-agent tools list). Each MCP
    connection carries `server_id`/`name`, `url`, optional `transport`/`headers`."""
    servers: Dict[str, Dict[str, Any]] = {}
    for conn in connections or []:
        if str((conn or {}).get("kind") or "").strip().lower() != "mcp":
            continue
        server_id = str(
            conn.get("server_id") or conn.get("server") or conn.get("name") or ""
        ).strip()
        url = conn.get("url")
        if not server_id or not url:
            continue
        entry: Dict[str, Any] = {
            "url": url,
            "transport": conn.get("transport") or "streamable_http",
        }
        if conn.get("headers"):
            entry["headers"] = dict(conn["headers"])
        servers[server_id] = entry
    return {"servers": servers}


async def load_mcp_tools(mcp_cfg: Dict[str, Any]) -> List[Any]:
    """Load a KDCube-served MCP endpoint's tools as LangChain tools.

    Returns [] (degrades cleanly, logs a hint) when `langchain-mcp-adapters` is
    absent, no servers are configured, or the endpoint is unreachable — so the
    agent is always buildable with plain tools regardless of MCP state.
    """
    servers = build_mcp_server_config(mcp_cfg or {})
    if not servers:
        return []
    if not mcp_adapters_available():
        logger.warning(
            "tools_mcp: langchain-mcp-adapters not installed; skipping MCP tools. "
            "Install `langchain-mcp-adapters` to enable the KDCube-served MCP tool set."
        )
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # lazy, optional

        client = MultiServerMCPClient(servers)
        tools = await client.get_tools()
        logger.info("tools_mcp: loaded %d MCP tool(s) from %d server(s).", len(tools), len(servers))
        return list(tools)
    except Exception as e:  # noqa: BLE001 - never fail a build over an optional tool source
        logger.warning("tools_mcp: MCP tool load failed (%s); continuing with plain tools.", e)
        return []


async def resolve_tools(tools_cfg: Dict[str, Any], plain_tools: List[Any]) -> List[Any]:
    """Assemble the tool list per `config.tools.mode`:

      - ``plain`` (default) — the vendored plain LangChain tools only.
      - ``mcp``             — KDCube-served MCP tools only (falls back to plain if
                              MCP is unavailable, so the agent always has tools).
      - ``both``            — plain tools + MCP tools.
    """
    mode = str((tools_cfg or {}).get("mode") or "plain").strip().lower()
    if mode == "plain":
        return list(plain_tools)

    mcp_tools = await load_mcp_tools((tools_cfg or {}).get("mcp") or {})
    if mode == "mcp":
        return mcp_tools or list(plain_tools)
    # both
    return list(plain_tools) + mcp_tools

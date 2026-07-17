# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Bind a KDCube-served MCP endpoint's tools as LangChain tools.

For any hosted LangGraph/LangChain agent: given a standard MCP server map —
``{server_id: {url, transport, headers}}`` — load its tools as LangChain
``BaseTool``s via ``langchain-mcp-adapters``. Reusable by any bundle; the
per-user delegated bearer (if any) is already resolved into ``headers`` by
``solutions/connections/delegated_mcp.resolve_mcp_server_map`` — this module
knows nothing about delegated credentials, only the neutral server map.

Degrades cleanly: returns ``[]`` (with a logged hint) when the map is empty,
``langchain-mcp-adapters`` is not installed, or the endpoint is unreachable — so
the agent is always buildable with its plain tools regardless of MCP state.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def mcp_adapters_available() -> bool:
    """Whether the optional ``langchain-mcp-adapters`` package is importable."""
    try:
        import langchain_mcp_adapters  # noqa: F401
        return True
    except Exception:
        return False


async def load_mcp_tools_from_server_map(
    server_map: Dict[str, Dict[str, Any]],
    *,
    error_sink: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Load LangChain tools from a resolved MCP server map. ``[]`` on any
    absence/failure — never raises, so a graph build never fails over an
    optional MCP tool source.

    ``error_sink``: when a dict is passed, a load failure records the raw
    exception under ``error_sink["_load_error"]``. Loading CONNECTS to each
    server, so a consent/auth denial (a KDCube `@mcp` 403) surfaces HERE, at
    load, before any tool call — the caller inspects the error and shapes a
    consent demand instead of silently dropping the tools."""
    if not server_map:
        return []
    if not mcp_adapters_available():
        logger.warning(
            "frameworks.langchain.mcp: langchain-mcp-adapters not installed; skipping "
            "MCP tools. Install `langchain-mcp-adapters` (>=0.1.7) to enable them."
        )
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # lazy, optional

        client = MultiServerMCPClient(server_map)
        tools = await client.get_tools()
        logger.info(
            "frameworks.langchain.mcp: loaded %d MCP tool(s) from %d server(s).",
            len(tools), len(server_map),
        )
        return list(tools)
    except Exception as e:  # noqa: BLE001 - never fail a build over an optional tool source
        if error_sink is not None:
            error_sink["_load_error"] = e
        logger.warning("frameworks.langchain.mcp: MCP tool load failed (%s); continuing without.", e)
        return []


def _iter_exc_chain(error: Any):
    seen: set = set()
    stack = [error]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        for nxt in (getattr(e, "__cause__", None), getattr(e, "__context__", None)):
            if nxt is not None:
                stack.append(nxt)
        for sub in getattr(e, "exceptions", None) or ():  # ExceptionGroup / TaskGroup
            stack.append(sub)


def load_error_looks_like_denial(error: Any) -> bool:
    """Whether a MultiServerMCPClient load error carries an auth/consent denial
    (403/401). langchain-mcp-adapters wraps the HTTP failure in a TaskGroup /
    ExceptionGroup, so walk the exception chain's text for the status."""
    if error is None:
        return False
    text = " ".join(str(x) for x in _iter_exc_chain(error)).lower()
    return "403" in text or "forbidden" in text or "401" in text or "unauthorized" in text

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools_descriptor.py ──
# Declares which tools the react.code bundle makes available to the LLM agent.
# Extends react.doc tools with code knowledge graph tools.

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

TOOLS_SPECS: List[Dict[str, Any]] = [
    # SDK tool modules
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",
        "alias": "io_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.ctx_tools",
        "alias": "ctx_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.exec_tools",
        "alias": "exec_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",
        "alias": "web_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",
        "alias": "rendering_tools",
        "use_sk": True,
    },

    # Bundle-local tools
    {
        "ref": "tools/react_tools.py",          # Knowledge space search (react.search_knowledge)
        "alias": "react",
        "use_sk": True,
    },
    {
        "ref": "tools/exec_space_tools.py",     # Exec-only namespace resolver
        "alias": "bundle_data",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.code_graph_tools",  # Code knowledge graph (Neo4j) — SDK level
        "alias": "code_graph",
        "use_sk": True,
    },
]

MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
    {"server_id": "local", "alias": "local", "tools": ["*"]},
    {"server_id": "deepwiki", "alias": "deepwiki", "tools": ["*"]},
]

TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

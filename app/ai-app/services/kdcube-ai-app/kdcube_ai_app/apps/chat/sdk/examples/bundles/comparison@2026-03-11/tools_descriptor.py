# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── tools_descriptor.py ──
# Tool descriptor for the comparison demo bundle.
# Registers SDK tools + bundle-local comparison tools.

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

TOOLS_SPECS: List[Dict[str, Any]] = [
    # SDK tools — web search for up-to-date research
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
    # Bundle-local comparison tools
    {
        "ref": "tools/comparison_tools.py",
        "alias": "comparison_tools",
        "use_sk": True,
    },
]

MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
]

TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

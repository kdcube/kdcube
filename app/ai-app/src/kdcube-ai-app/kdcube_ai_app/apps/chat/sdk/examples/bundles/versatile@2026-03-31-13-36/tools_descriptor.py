from __future__ import annotations

import pathlib
from typing import Any, Dict, List

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

TOOLS_SPECS: List[Dict[str, Any]] = [
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
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",
        "alias": "rendering_tools",
        "use_sk": True,
    },
    {
        "ref": "tools/preference_tools.py",
        "alias": "preferences",
        "use_sk": True,
    },
]

MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "deepwiki", "alias": "deepwiki", "tools": ["*"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
    {"server_id": "local", "alias": "local", "tools": ["*"]},
    {"server_id": "firecrawl", "alias": "firecrawl", "tools": ["*"]},
]

TOOL_RUNTIME: Dict[str, str] = {}

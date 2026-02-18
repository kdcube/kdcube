# tool_descriptor.py

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

# Bundle root = directory containing this file (i.e., the bundle root)
BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# Pure data: this is the *portable* descriptor.
# - "module" entries refer to installed packages (same name in host & container)
# - "ref" entries are *relative paths from bundle root*
# - Tool IDs are resolved as: "<alias>.<tool_name>" (alias comes from this file).
#   Example: alias "generic_tools" + function "web_search" => "generic_tools.web_search".
# - Optional runtime mapping can be provided per-tool; if missing, tool runs in-memory.
#   Example:
#     TOOL_RUNTIME = {
#         "generic_tools.web_search": "local",
#         "generic_tools.fetch_url_contents": "local",
#     }
TOOLS_SPECS: List[Dict[str, Any]] = [
    # package tools (same in host+docker)
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

    # bundle-local tools (relative to bundle root)
    {
        "ref": "tools/local_tools.py",
        "alias": "local_tools",
        "use_sk": True,
    },
]

# MCP tool sources (server_id + optional alias + optional tool allowlist).
# Tool ids will be namespaced as: "mcp.<alias>.<tool_id>"
MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    # Built-in MCP web_search server (stdio or http/sse)
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    # Stdio (local or npx mcp-remote)
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    # HTTP / Streamable HTTP
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
    # SSE (server-sent events)
    {"server_id": "local", "alias": "local", "tools": ["*"]},
]

# Optional per-tool runtime overrides.
# Valid values: "none" | "local" - subprocess | "docker" | "fargate"
TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

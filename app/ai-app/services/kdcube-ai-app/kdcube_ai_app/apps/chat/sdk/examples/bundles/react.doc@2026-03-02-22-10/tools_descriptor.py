# tool_descriptor.py

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

# Bundle root = directory containing this file (i.e., the bundle root)
BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# Pure data: this is the *portable* descriptor.
# - Workflow imports this file and passes TOOLS_SPECS/MCP_TOOL_SPECS/TOOL_RUNTIME
#   into create_tool_subsystem_with_mcp(...).
# - ToolSubsystem does not auto-scan this descriptor file on disk.
# - "module" entries are import names resolved dynamically to module files at runtime.
# - "ref" entries are bundle-relative paths and work in host + iso/docker runtime
#   because bundle paths are rewritten during runtime bootstrap.
# - Tool IDs are resolved as: "<alias>.<tool_name>" (alias comes from this file).
#   Example: alias "generic_tools" + function "web_search" => "generic_tools.web_search".
# - Optional runtime mapping can be provided per-tool; if missing, tool runs in-memory.
#   Example:
#     TOOL_RUNTIME = {
#         "generic_tools.web_search": "local",
#         "generic_tools.fetch_url_contents": "local",
#     }
TOOLS_SPECS: List[Dict[str, Any]] = [
    # SDK tool modules (import-by-name, dynamically resolved in each runtime)
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

    # Bundle-local tools (bundle-relative refs; portable across host and iso/docker)
    # {
    #     "ref": "tools/local_tools.py",
    #     "alias": "doc",
    #     "use_sk": True,
    # },
    {
        "ref": "tools/react_tools.py",
        "alias": "react",
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

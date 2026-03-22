# ── tools_descriptor.py ──
# Declares which tools this bundle makes available to the LLM agent.
#
# The tool subsystem reads this file at runtime to discover, load, and
# register tools. There are three types of tool sources:
#
#   1. TOOLS_SPECS — Python modules containing Semantic Kernel (SK) tool classes
#      - "module": installed package path (works in host + Docker)
#      - "ref":    relative path from bundle root (bundle-local tools)
#      - "alias":  namespace prefix for tool IDs (e.g. "io_tools.read_file")
#      - "use_sk": True means tools are SK @kernel_function decorated
#
#   2. MCP_TOOL_SPECS — MCP (Model Context Protocol) server connections
#      - "server_id": matches server config in bundle props mcp.services
#      - "tools": ["*"] = all tools, or explicit allowlist
#      - Tool IDs namespaced as "mcp.<alias>.<tool_id>"
#
#   3. TOOL_RUNTIME — per-tool runtime override
#      - "local": run in a subprocess (for network/IO tools)
#      - default (omitted): run in-memory in the same process

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

# Bundle root = directory containing this file
BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# ──────────────────────────────────────────────────────────────
# 1. Tool specs — SDK-provided and bundle-local tool modules
# ──────────────────────────────────────────────────────────────
# This is the *portable* descriptor.
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
     # SDK tool modules (import-by-name, dynamically resolved in each runtime, host + Docker)
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",    # File read/write tools
        "alias": "io_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.ctx_tools",   # Context/memory tools
        "alias": "ctx_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.exec_tools",  # Code execution tools
        "alias": "exec_tools",
        "use_sk": True,
    },

    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",   # Web search / fetch
        "alias": "web_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",  # Chart/render tools
        "alias": "rendering_tools",
        "use_sk": True,
    },

    # Bundle-local tools (bundle-relative refs, relative path from bundle root.  Portable across host and iso/docker)
    # {
    #     "ref": "tools/local_tools.py",       # site-scoped web search (disabled)
    #     "alias": "doc",
    #     "use_sk": True,
    # },
    {
        "ref": "tools/react_tools.py",          # Knowledge space search (react.search_knowledge)
        "alias": "react",
        "use_sk": True,
    },
    {
        "ref": "tools/exec_space_tools.py",     # Exec-only namespace resolver for generated code
        "alias": "bundle_data",
        "use_sk": True,
    },
]

# ──────────────────────────────────────────────────────────────
# 2. MCP tool sources — external tool servers via MCP protocol
# ──────────────────────────────────────────────────────────────
MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},  # Built-in web search
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},   # Stdio transport
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},     # HTTP / Streamable HTTP
    {"server_id": "local", "alias": "local", "tools": ["*"]},   # SSE transport
    {"server_id": "deepwiki", "alias": "deepwiki", "tools": ["*"]},  # DeepWiki — GitHub repo docs (streamable-http)
]

# ──────────────────────────────────────────────────────────────
# 3. Per-tool runtime overrides
#    "local" = run in a separate subprocess (sandboxed)
#    omitted = run in-memory in the same process (default, fastest)
# ──────────────────────────────────────────────────────────────
TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

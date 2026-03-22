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
TOOLS_SPECS: List[Dict[str, Any]] = [
    # SDK tools (installed packages, same in host + Docker)
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

    # Bundle-local tool (relative path from this file's directory)
    # See tools/local_tools.py for implementation
    {
        "ref": "tools/local_tools.py",
        "alias": "local_tools",
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
]

# ──────────────────────────────────────────────────────────────
# 3. Per-tool runtime overrides
#    "local" = run in a separate subprocess via _InProcessRuntime
#              with Linux network namespace isolation (unshare CLONE_NEWNET)
#              and UID/GID drop; parent state is transferred via PortableSpec
#    omitted = run in-memory in the same process (default, fastest, no isolation)
# ──────────────────────────────────────────────────────────────
TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

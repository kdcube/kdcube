# ── tools_descriptor.py ──
# Bundle tool descriptor consumed by workflow -> ToolSubsystem.
# It defines Python module sources, MCP sources, and optional per-tool runtime overrides.
#
# Runtime flow:
# - Workflow imports this module and passes TOOLS_SPECS / MCP_TOOL_SPECS / TOOL_RUNTIME
#   into create_tool_subsystem_with_mcp(...).
# - ToolSubsystem resolves and loads modules, builds tool catalog, and provides callables.
#
# TOOLS_SPECS source types:
#   1) "module": importable Python module name (typically SDK-installed modules)
#   2) "ref":    bundle-relative file path (preferred for bundle-local tools)
#      Note: "ref" is portable and works in isolated runtimes (Docker/Fargate/local),
#      resolved against bundle_root and rewritten/restored if needed.
#
# Tool IDs are "<alias>.<tool_name>" (example: "io_tools.read_file").
# "use_sk": True => introspect Semantic Kernel metadata (@kernel_function decorated)

from __future__ import annotations

from typing import List, Dict, Any
import pathlib

# Bundle root = directory containing this file
BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

# ──────────────────────────────────────────────────────────────
# 1. Tool specs — SDK-provided and bundle-local tool modules
# ──────────────────────────────────────────────────────────────
TOOLS_SPECS: List[Dict[str, Any]] = [
    # Modular tools from installed packages (host + Docker).
    # These are discoverable by the KDCube tool discovery mechanism
    # and are not limited to the SDK; any package following the convention
    # can provide tools.
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

    #{
    #    "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",   # Web search / fetch
    #    "alias": "web_tools",
    #    "use_sk": True,
    #},
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",  # Chart/render tools
        "alias": "rendering_tools",
        "use_sk": True,
    }
]

# ──────────────────────────────────────────────────────────────
# 2. MCP tool sources — external tool servers via MCP protocol
# ──────────────────────────────────────────────────────────────
MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    # {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},  # Built-in web search
    {"server_id": "deepwiki", "alias": "deepwiki", "tools": ["*"]},               # Streamable HTTP — GitHub repo docs
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},   # Stdio transport
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},     # HTTP / Streamable HTTP
    {"server_id": "local", "alias": "local", "tools": ["*"]},   # SSE transport
    {"server_id": "firecrawl", "alias": "firecrawl", "tools": ["*"]},  # Web scraping/crawling via Firecrawl
]

# ──────────────────────────────────────────────────────────────
# 3. Per-tool runtime overrides
#    "local" = run in a separate subprocess
#    omitted = run in-memory in the same process (default, fastest)
# ──────────────────────────────────────────────────────────────
TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
}

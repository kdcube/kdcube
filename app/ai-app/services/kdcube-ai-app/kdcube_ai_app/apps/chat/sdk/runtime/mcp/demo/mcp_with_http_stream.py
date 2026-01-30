# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Demo: call MCP web_search via HTTP/SSE (remote server).

Requirements:
  - MCP_SERVICES env configured with an http/sse server for "web_search"
  - web_search MCP server running separately
"""

import asyncio
import json
import os

from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import MCPToolsSubsystem


async def main() -> None:
    env_json = os.environ.get("MCP_SERVICES")
    if not env_json:
        raise RuntimeError("MCP_SERVICES is not set")

    mcp_specs = [
        {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    ]

    mcp = MCPToolsSubsystem(
        bundle_id=os.environ.get("AGENTIC_BUNDLE_ID") or "demo",
        mcp_tool_specs=mcp_specs,
        env_json=env_json,
    )

    tools = await mcp.list_tools()
    print("Tools:", [t.id for t in tools])

    result = await mcp.execute_tool(
        alias="web_search",
        tool_name="web_search",
        params={"queries": "mcp server", "n": 3, "fetch_content": False},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

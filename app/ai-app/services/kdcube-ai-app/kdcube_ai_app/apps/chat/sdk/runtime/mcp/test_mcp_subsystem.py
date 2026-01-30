# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import asyncio

from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_adapter import MCPServerSpec, MCPToolSchema
from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import MCPToolsSubsystem


class _DummyAdapter:
    def __init__(self, server: MCPServerSpec):
        self.server = server

    async def list_tools(self):
        return [
            MCPToolSchema(
                id="so_search",
                name="so_search",
                description="Search",
                params_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
            MCPToolSchema(
                id="get_content",
                name="get_content",
                description="Get content",
                params_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            ),
        ]

    async def call_tool(self, tool_id, params, *, trace_id=None):
        return {"ok": True, "tool_id": tool_id, "params": params, "trace_id": trace_id}


def _dummy_factory(server: MCPServerSpec):
    return _DummyAdapter(server)


def test_mcp_services_env_accepts_mcpServers():
    env_json = """
    {
      "mcpServers": {
        "stack": { "transport": "stdio", "command": "npx", "args": ["mcp-remote", "mcp.stackoverflow.com"] }
      }
    }
    """
    ss = MCPToolsSubsystem(
        bundle_id="b1",
        mcp_tool_specs=[{"server_id": "stack", "alias": "stack"}],
        adapter_factory=_dummy_factory,
        env_json=env_json,
    )
    server = ss._server_spec("stack")
    assert server is not None
    assert server.transport == "stdio"
    assert server.command == "npx"


def test_interactive_auth_is_hidden():
    env_json = """
    {
      "mcpServers": {
        "stack": { "transport": "stdio", "command": "npx", "auth": { "type": "oauth_gui" } }
      }
    }
    """
    ss = MCPToolsSubsystem(
        bundle_id="b1",
        mcp_tool_specs=[{"server_id": "stack", "alias": "stack"}],
        adapter_factory=_dummy_factory,
        env_json=env_json,
    )
    assert ss._server_spec("stack") is None


def test_transport_validation():
    env_json = """
    {
      "mcpServers": {
        "s1": { "transport": "stdio" },
        "s2": { "transport": "http", "url": "https://mcp.example.com" },
        "s3": { "transport": "sse", "url": "http://127.0.0.1:8787/sse" }
      }
    }
    """
    ss = MCPToolsSubsystem(
        bundle_id="b1",
        mcp_tool_specs=[
            {"server_id": "s1", "alias": "s1"},
            {"server_id": "s2", "alias": "s2"},
            {"server_id": "s3", "alias": "s3"},
        ],
        adapter_factory=_dummy_factory,
        env_json=env_json,
    )
    assert ss._server_spec("s1") is None  # stdio requires command
    assert ss._server_spec("s2") is not None
    assert ss._server_spec("s3") is not None


def test_build_entries_and_execute():
    env_json = """
    {
      "mcpServers": {
        "stack": { "transport": "stdio", "command": "npx", "args": ["mcp-remote", "mcp.stackoverflow.com"] }
      }
    }
    """
    ss = MCPToolsSubsystem(
        bundle_id="b1",
        mcp_tool_specs=[{"server_id": "stack", "alias": "stack", "tools": ["so_search"]}],
        adapter_factory=_dummy_factory,
        env_json=env_json,
    )

    async def _run():
        entries = await ss.build_tool_entries()
        assert any(e["id"] == "mcp.stack.so_search" for e in entries)
        assert all(e["id"] != "mcp.stack.get_content" for e in entries)
        out = await ss.execute_tool(alias="stack", tool_name="so_search", params={"q": "auth"})
        assert out.get("ok") is True

    asyncio.run(_run())

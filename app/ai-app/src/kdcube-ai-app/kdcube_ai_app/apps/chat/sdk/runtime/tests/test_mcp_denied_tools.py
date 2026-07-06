# SPDX-License-Identifier: MIT

"""MCP spec `denied_tools`: per-user denials subtract from a wildcard allow."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_adapter import MCPToolSchema
from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import (
    MCPToolsSubsystem,
    _normalize_mcp_specs,
)


class _FakeAdapter:
    def __init__(self, server):
        self.server = server

    async def list_tools(self):
        return [
            MCPToolSchema(id="kb_search", name="kb_search", description="Search the knowledge base.", params_schema={}),
            MCPToolSchema(id="kb_fetch", name="kb_fetch", description="Fetch one document.", params_schema={}),
        ]


def _subsystem(spec: dict) -> MCPToolsSubsystem:
    subsystem = MCPToolsSubsystem(
        bundle_id="test-bundle",
        mcp_tool_specs=[spec],
        adapter_factory=lambda server: _FakeAdapter(server),
        services_config={"knowledge": {"transport": "stdio", "command": "kb-server"}},
    )
    subsystem.cache = None  # force the live (fake) listing; no redis in tests
    return subsystem


def test_normalize_parses_denied_tools():
    specs = _normalize_mcp_specs([
        {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"], "denied_tools": ["kb_fetch"]},
    ])
    assert specs[0].denied_tools == ["kb_fetch"]

    nested = _normalize_mcp_specs([
        {"mcp": {"server_id": "knowledge", "tools": ["*"], "denied_tools": ["kb_fetch"]}},
    ])
    assert nested[0].denied_tools == ["kb_fetch"]


@pytest.mark.asyncio
async def test_denied_tools_filter_applies_after_wildcard_allow():
    subsystem = _subsystem(
        {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"], "denied_tools": ["kb_fetch"]}
    )
    tools = await subsystem.list_tools()
    assert [t.id for t in tools] == ["kb_search"]

    entries = await subsystem.build_tool_entries()
    assert [e["id"] for e in entries] == ["mcp.knowledge.kb_search"]


@pytest.mark.asyncio
async def test_without_denied_tools_wildcard_lists_everything():
    subsystem = _subsystem({"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]})
    tools = await subsystem.list_tools()
    assert [t.id for t in tools] == ["kb_search", "kb_fetch"]

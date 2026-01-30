# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/runtime/mcp/mcp_adapter.py
#
# MCP adapter interface and tool schema contracts.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import inspect
import os


@dataclass
class MCPServerSpec:
    server_id: str
    display_name: str
    transport: str = "stdio"  # stdio | sse | streamable-http | http
    endpoint: str = ""        # URL for http/sse transports
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    auth_profile: Optional[Dict[str, Any]] = None


@dataclass
class MCPToolSchema:
    id: str
    name: str
    description: str
    params_schema: Dict[str, Any]
    returns_schema: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None


class MCPAdapter(Protocol):
    """
    Minimal MCP adapter contract.
    Implementations are responsible for:
      - connecting to an MCP server
      - listing tools + schemas
      - executing tools remotely
    """

    server: MCPServerSpec

    async def list_tools(self) -> List[MCPToolSchema]:
        ...

    async def call_tool(
        self,
        tool_id: str,
        params: Dict[str, Any],
        *,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...


class PythonSDKMCPAdapter:
    """
    MCP adapter using the official Python SDK (mcp package).
    Supports stdio, sse and streamable-http transports.
    """

    def __init__(self, server: MCPServerSpec):
        self.server = server

    async def list_tools(self) -> List[MCPToolSchema]:
        async with self._session() as session:
            resp = await session.list_tools()
            return [self._tool_from_sdk(t) for t in (getattr(resp, "tools", []) or [])]

    async def call_tool(
        self,
        tool_id: str,
        params: Dict[str, Any],
        *,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._session() as session:
            result = await session.call_tool(tool_id, params or {})
            # Prefer structuredContent if present, otherwise return raw content blocks
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                return structured
            return {
                "content": [
                    {"type": getattr(b, "type", None), "text": getattr(b, "text", None)}
                    for b in (getattr(result, "content", []) or [])
                ],
            }

    def _tool_from_sdk(self, tool: Any) -> MCPToolSchema:
        # tool can be pydantic or dataclass-like
        tool_id = getattr(tool, "name", None) or getattr(tool, "id", None) or ""
        desc = getattr(tool, "description", None) or ""
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "params_schema", None) or {}
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump()
        return MCPToolSchema(
            id=str(tool_id),
            name=str(tool_id),
            description=str(desc),
            params_schema=schema if isinstance(schema, dict) else {},
            returns_schema=None,
            tags=None,
        )

    def _auth_headers(self) -> Dict[str, str]:
        auth = self.server.auth_profile or {}
        if not isinstance(auth, dict):
            return {}
        auth_type = (auth.get("type") or "").strip().lower()
        if auth_type in {"oauth_gui", "oauth-gui", "interactive"}:
            return {}
        env_key = auth.get("env")
        header = auth.get("header")
        token = os.environ.get(env_key) if env_key else None
        if not token:
            return {}
        if auth_type in {"bearer", "oauth"}:
            return {"Authorization": f"Bearer {token}"}
        if auth_type in {"api_key", "apikey", "key"}:
            return {str(header or "X-API-Key"): str(token)}
        if auth_type in {"header"} and header:
            return {str(header): str(token)}
        return {}

    def _session(self):
        transport = (self.server.transport or "stdio").strip().lower()
        if transport in {"stdio", "local"}:
            return _stdio_session(self.server)
        if transport in {"sse"}:
            return _sse_session(self.server, headers=self._auth_headers())
        if transport in {"streamable-http", "streamable_http", "http"}:
            return _streamable_http_session(self.server, headers=self._auth_headers())
        return _stdio_session(self.server)


def _supports_kwarg(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except Exception:
        return False


def _stdio_session(server: MCPServerSpec):
    async def _cm():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=server.command or "",
            args=server.args or [],
            env=server.env or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    return _async_cm(_cm)


def _sse_session(server: MCPServerSpec, *, headers: Dict[str, str] | None):
    async def _cm():
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        kwargs = {"url": server.endpoint}
        if headers and _supports_kwarg(sse_client, "headers"):
            kwargs["headers"] = headers
        async with sse_client(**kwargs) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    return _async_cm(_cm)


def _streamable_http_session(server: MCPServerSpec, *, headers: Dict[str, str] | None):
    async def _cm():
        from mcp import ClientSession
        client_fn = None
        try:
            from mcp.client.streamable_http import streamablehttp_client as _client_fn  # type: ignore
            client_fn = _client_fn
        except Exception:
            try:
                from mcp.client import streamable_http as _streamable_http  # type: ignore
                client_fn = getattr(_streamable_http, "streamablehttp_client", None)
            except Exception:
                client_fn = None
        if not client_fn:
            raise ImportError("mcp streamable-http client is not available")
        kwargs = {"url": server.endpoint}
        if headers and _supports_kwarg(client_fn, "headers"):
            kwargs["headers"] = headers
        async with client_fn(**kwargs) as (read, write, *_rest):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    return _async_cm(_cm)


def _async_cm(factory):
    class _Wrapper:
        def __init__(self, f):
            self._f = f
            self._agen = None

        async def __aenter__(self):
            self._agen = self._f()
            return await self._agen.__anext__()

        async def __aexit__(self, exc_type, exc, tb):
            if self._agen:
                await self._agen.aclose()
            return False
    return _Wrapper(factory)


class MCPToolsSubsystemLike(Protocol):
    async def execute_tool(
        self,
        *,
        alias: str,
        tool_name: str,
        params: Dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...


def mcp_tools_to_catalog(tools: List[MCPToolSchema]) -> List[Dict[str, Any]]:
    """
    Convert MCP tool schemas into the internal tool-catalog shape used by ReAct.
    """
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        out.append({
            "id": t.id,
            "doc": {
                "purpose": t.description or t.name,
                "args": t.params_schema or {},
                "returns": t.returns_schema or {},
            },
        })
    return out


async def execute_mcp_tool(
    *,
    tool_id: str,
    params: Dict[str, Any],
    mcp_subsystem: MCPToolsSubsystemLike,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute an MCP tool by tool_id using the MCPToolsSubsystem.
    tool_id format: mcp.<alias>.<tool_id...>
    """
    from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import parse_tool_id

    origin, provider, name = parse_tool_id(tool_id)
    if origin != "mcp" or not provider or not name:
        return {"error": f"Invalid MCP tool_id: {tool_id}"}
    if mcp_subsystem is None:
        return {"error": "MCP subsystem is not configured"}
    return await mcp_subsystem.execute_tool(alias=provider, tool_name=name, params=params, trace_id=trace_id)

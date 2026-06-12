from __future__ import annotations

import pathlib
from typing import Any, Dict, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    DEFAULT_AGENT_ID,
    AgentToolConfig,
    agent_tool_config_from_bundle_props,
)

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

DEFAULT_AGENT_TOOL_CONNECTIONS: list[dict[str, Any]] = [
    {
        "name": "io",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",
        "alias": "io_tools",
        "allowed": ["tool_call"],
    },
    {
        "name": "context",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.ctx_tools",
        "alias": "ctx_tools",
        "allowed": ["merge_sources", "fetch_ctx"],
    },
    {
        "name": "memory",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.context.memory.tools",
        "alias": "memory",
        "allowed": [
            "search_memory",
            "recent_memories",
            "read_memory",
            "record_memory",
            "confirm_memory",
            "retire_memory",
        ],
    },
    {
        "name": "canvas",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools",
        "alias": "canvas",
        "allowed": ["patch"],
    },
    {
        "name": "exec",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.exec_tools",
        "alias": "exec_tools",
        "allowed": ["execute_code_python"],
    },
    {
        "name": "web",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",
        "alias": "web_tools",
        "allowed": ["web_search", "web_fetch"],
        "runtime": {
            "web_search": "local",
            "web_fetch": "local",
        },
    },
    {
        "name": "rendering",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",
        "alias": "rendering_tools",
        "allowed": ["write_pptx", "write_png", "write_pdf", "write_docx"],
    },
    {
        "name": "browser",
        "kind": "python",
        "module": "kdcube_ai_app.apps.chat.sdk.tools.browser_tools",
        "alias": "browser_tools",
        "allowed": ["open_page", "click", "fill", "scroll", "status", "close"],
        "runtime": {
            "open_page": "none",
            "click": "none",
            "fill": "none",
            "scroll": "none",
            "status": "none",
            "close": "none",
        },
    },
    {
        "name": "knowledge",
        "kind": "mcp",
        "server_id": "knowledge",
        "alias": "knowledge",
        "allowed": ["*"],
    },
]


def default_tools_props() -> dict[str, Any]:
    return {
        "surfaces": {
            "as_consumer": {
                "default_agent": "main",
                "agents": {
                    "main": {
                        "tools": list(DEFAULT_AGENT_TOOL_CONNECTIONS),
                        "event_sources": [],
                    },
                },
                "ui": {
                    "canvas": {
                        "resolvers": [],
                    },
                    "scene": {
                        "external_panels": [],
                    },
                },
            },
        },
    }


def _with_default_tools(bundle_props: Mapping[str, Any] | None) -> dict[str, Any]:
    props = dict(bundle_props or {})
    surfaces = props.get("surfaces") if isinstance(props.get("surfaces"), Mapping) else {}
    as_consumer = surfaces.get("as_consumer") if isinstance(surfaces.get("as_consumer"), Mapping) else {}
    surfaces_agents = as_consumer.get("agents")
    if isinstance(surfaces_agents, Mapping):
        return props
    tools = props.get("tools")
    if isinstance(tools, Mapping) and isinstance(tools.get("agents"), Mapping):
        return props
    merged = default_tools_props()
    merged.update(props)
    return merged


def config_for_agent(
    agent_id: str | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
) -> AgentToolConfig:
    return agent_tool_config_from_bundle_props(
        _with_default_tools(bundle_props),
        agent_id or DEFAULT_AGENT_ID,
        bundle_root=BUNDLE_ROOT,
    )


def tools_for_client(
    client_id: str | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return config_for_agent(client_id, bundle_props=bundle_props).tool_specs


def mcp_tools_for_client(
    client_id: str | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return config_for_agent(client_id, bundle_props=bundle_props).mcp_tool_specs


def tool_runtime_for_client(
    client_id: str | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    return config_for_agent(client_id, bundle_props=bundle_props).tool_runtime


_DEFAULT_CONFIG = config_for_agent(DEFAULT_AGENT_ID, bundle_props=default_tools_props())
TOOLS_SPECS: list[Dict[str, Any]] = _DEFAULT_CONFIG.tool_specs
MCP_TOOL_SPECS: list[Dict[str, Any]] = _DEFAULT_CONFIG.mcp_tool_specs
TOOL_RUNTIME: dict[str, str] = _DEFAULT_CONFIG.tool_runtime

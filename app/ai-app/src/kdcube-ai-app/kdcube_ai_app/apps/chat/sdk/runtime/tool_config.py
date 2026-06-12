# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.event_identity import normalize_agent_id
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    NAMED_SERVICE_TOOLS_ALIAS,
    named_service_tool_spec,
)


DEFAULT_AGENT_ID = "default_agent"

_NAMED_SERVICE_OPERATION_TO_TOOL = {
    "provider.about": "provider_about",
    "provider.capabilities": "provider_capabilities",
    "object.list": "list_objects",
    "object.search": "search_objects",
    "object.get": "get_object",
    "object.schema": "object_schema",
    "object.upsert": "upsert_object",
    "object.delete": "delete_object",
}


@dataclass(frozen=True)
class AgentToolConfig:
    """Resolved, runtime-ready tool config for one model-facing agent."""

    tool_specs: list[dict[str, Any]] = field(default_factory=list)
    mcp_tool_specs: list[dict[str, Any]] = field(default_factory=list)
    tool_runtime: dict[str, str] = field(default_factory=dict)
    allowed_plugins: list[str] = field(default_factory=list)
    allowed_tool_names_by_alias: dict[str, list[str] | None] = field(default_factory=dict)


def _get_path(data: Mapping[str, Any] | None, path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_wildcard(items: Sequence[str] | None) -> bool:
    return not items or "*" in items


def _append_unique(items: list[str], value: str | None) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _agent_keys(agent_id: str | None, *, default_agent_id: str = DEFAULT_AGENT_ID) -> list[str]:
    keys: list[str] = []
    for key in (
        str(agent_id or "").strip(),
        normalize_agent_id(agent_id),
        str(agent_id or "").strip().replace(".", "_").replace("-", "_"),
        default_agent_id,
        "main",
        "default",
    ):
        _append_unique(keys, key)
    return keys


def _legacy_agent_keys(agent_id: str | None, *, default_agent_id: str = DEFAULT_AGENT_ID) -> list[str]:
    keys: list[str] = []
    for key in (
        str(agent_id or "").strip(),
        normalize_agent_id(agent_id),
        str(agent_id or "").strip().replace(".", "_").replace("-", "_"),
        default_agent_id,
        "default",
    ):
        _append_unique(keys, key)
    return keys


def _resolve_ref(value: str, *, bundle_root: str | pathlib.Path | None) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    path = pathlib.Path(text)
    if path.is_absolute() or bundle_root is None:
        return str(path if path.is_absolute() else path)
    return str((pathlib.Path(bundle_root) / path).resolve())


def _agent_tool_connections(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str | None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> list[Mapping[str, Any]]:
    surface_agents = _get_path(bundle_props or {}, "surfaces.as_consumer.agents", {})
    if isinstance(surface_agents, Mapping):
        for key in _agent_keys(agent_id, default_agent_id=default_agent_id):
            agent = surface_agents.get(key)
            if isinstance(agent, Mapping) and isinstance(agent.get("tools"), list):
                return [item for item in agent["tools"] if isinstance(item, Mapping)]

    agents = _get_path(bundle_props or {}, "tools.agents", {})
    if not isinstance(agents, Mapping):
        return []
    for key in _legacy_agent_keys(agent_id, default_agent_id=default_agent_id):
        raw = agents.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, Mapping)]
    return []


def named_service_agent_connections(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str | None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> list[Mapping[str, Any]]:
    return [
        item
        for item in _agent_tool_connections(
            bundle_props,
            agent_id=agent_id,
            default_agent_id=default_agent_id,
        )
        if str(item.get("kind") or "python").strip().lower() == "named_service"
    ]


def _merge_allowed(
    target: dict[str, list[str] | None],
    *,
    alias: str,
    allowed: Sequence[str] | None,
) -> None:
    if not alias:
        return
    if _is_wildcard(allowed):
        target[alias] = None
        return
    if target.get(alias) is None and alias in target:
        return
    existing = list(target.get(alias) or [])
    for name in allowed or []:
        _append_unique(existing, name)
    target[alias] = existing


def _merge_runtime(
    target: dict[str, str],
    *,
    alias: str,
    runtime: Any,
) -> None:
    if not isinstance(runtime, Mapping):
        return
    for key, value in runtime.items():
        tool_name = str(key or "").strip()
        mode = str(value or "").strip()
        if not tool_name or not mode:
            continue
        full_id = tool_name if "." in tool_name else f"{alias}.{tool_name}"
        target[full_id] = mode


def _named_service_tools_for_connection(connection: Mapping[str, Any]) -> list[str]:
    namespaces = connection.get("namespaces")
    if not isinstance(namespaces, Mapping):
        return []
    tools: list[str] = []
    for namespace_cfg in namespaces.values():
        if not isinstance(namespace_cfg, Mapping):
            continue
        operations = _string_list(
            namespace_cfg.get("allowed")
            or namespace_cfg.get("allowed_operations")
            or namespace_cfg.get("operations")
        )
        for operation in operations:
            tool_name = _NAMED_SERVICE_OPERATION_TO_TOOL.get(operation)
            if tool_name:
                _append_unique(tools, tool_name)
    return tools


def agent_tool_config_from_bundle_props(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> AgentToolConfig:
    tool_specs: list[dict[str, Any]] = []
    mcp_tool_specs: list[dict[str, Any]] = []
    tool_runtime: dict[str, str] = {}
    allowed_plugins: list[str] = []
    allowed_tool_names_by_alias: dict[str, list[str] | None] = {}

    for connection in _agent_tool_connections(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    ):
        kind = str(connection.get("kind") or "python").strip().lower()
        alias = str(connection.get("alias") or connection.get("name") or "").strip()
        if kind == "python":
            if not alias:
                continue
            spec: dict[str, Any] = {"alias": alias}
            if connection.get("module"):
                spec["module"] = str(connection.get("module")).strip()
            elif connection.get("ref"):
                spec["ref"] = _resolve_ref(str(connection.get("ref")), bundle_root=bundle_root)
            else:
                continue
            discovery = str(connection.get("discovery") or "semantic_kernel").strip().lower()
            spec["use_sk"] = discovery in {"semantic_kernel", "sk", "kernel"}
            tool_specs.append(spec)
            _append_unique(allowed_plugins, alias)
            _merge_allowed(
                allowed_tool_names_by_alias,
                alias=alias,
                allowed=_string_list(connection.get("allowed")),
            )
            _merge_runtime(tool_runtime, alias=alias, runtime=connection.get("runtime"))
            continue

        if kind == "mcp":
            server_id = str(
                connection.get("server_id")
                or connection.get("server")
                or connection.get("name")
                or ""
            ).strip()
            if not server_id:
                continue
            alias = alias or f"mcp_{server_id}"
            allowed = _string_list(connection.get("allowed") or connection.get("tools"))
            mcp_tool_specs.append({"server_id": server_id, "alias": alias, "tools": allowed or ["*"]})
            _append_unique(allowed_plugins, alias)
            _merge_allowed(allowed_tool_names_by_alias, alias=alias, allowed=allowed or ["*"])
            _merge_runtime(tool_runtime, alias=alias, runtime=connection.get("runtime"))
            continue

        if kind == "named_service":
            alias = alias or NAMED_SERVICE_TOOLS_ALIAS
            allowed_tools = _named_service_tools_for_connection(connection)
            if not allowed_tools:
                continue
            if not any(str(spec.get("alias") or "").strip() == alias for spec in tool_specs):
                tool_specs.append(named_service_tool_spec(alias=alias))
            _append_unique(allowed_plugins, alias)
            _merge_allowed(allowed_tool_names_by_alias, alias=alias, allowed=allowed_tools)
            _merge_runtime(tool_runtime, alias=alias, runtime=connection.get("runtime"))
            continue

    return AgentToolConfig(
        tool_specs=tool_specs,
        mcp_tool_specs=mcp_tool_specs,
        tool_runtime=tool_runtime,
        allowed_plugins=allowed_plugins,
        allowed_tool_names_by_alias=allowed_tool_names_by_alias,
    )


__all__ = [
    "AgentToolConfig",
    "DEFAULT_AGENT_ID",
    "agent_tool_config_from_bundle_props",
    "named_service_agent_connections",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/runtime/mcp/mcp_tools_subsystem.py

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_adapter import (
    MCPAdapter,
    MCPServerSpec,
    MCPToolSchema,
)
from kdcube_ai_app.infra.service_hub.cache import (
    NamespacedKVCache,
    NamespacedKVCacheConfig,
    create_namespaced_kv_cache_from_config,
)
from kdcube_ai_app.infra.namespaces import REDIS
from kdcube_ai_app.apps.chat.sdk.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class MCPToolSpec:
    server_id: str
    alias: Optional[str] = None
    tools: Optional[List[str]] = None  # None or ["*"] means all


class MCPAdapterFactory(Protocol):
    def __call__(self, server: MCPServerSpec) -> MCPAdapter:
        ...


def _parse_mcp_services_env(env_json: str) -> Dict[str, Dict[str, Any]]:
    if not env_json:
        return {}
    try:
        data = json.loads(env_json)
    except Exception:
        logger.warning("MCP_SERVICES env is not valid JSON")
        return {}
    if isinstance(data, dict) and "servers" in data and isinstance(data["servers"], dict):
        return data["servers"]
    if isinstance(data, dict):
        return data
    return {}


def _normalize_mcp_specs(raw_specs: List[Dict[str, Any]]) -> List[MCPToolSpec]:
    out: List[MCPToolSpec] = []
    for spec in raw_specs or []:
        if not isinstance(spec, dict):
            continue
        mcp_block = spec.get("mcp")
        server_id = None
        alias = None
        tools = None
        if isinstance(mcp_block, dict):
            server_id = mcp_block.get("server") or mcp_block.get("server_id")
            alias = mcp_block.get("alias") or spec.get("alias")
            tools = mcp_block.get("tools")
        elif isinstance(mcp_block, str):
            server_id = mcp_block
            alias = spec.get("alias")
            tools = spec.get("tools") or spec.get("tool_ids")
        else:
            server_id = spec.get("mcp_server") or spec.get("server")
            alias = spec.get("alias")
            tools = spec.get("tools") or spec.get("tool_ids")
        if not server_id:
            continue
        out.append(MCPToolSpec(server_id=str(server_id), alias=alias, tools=tools))
    return out


class MCPToolsSubsystem:
    """
    MCP tools loader + cache manager. All MCP-specific logic lives here.
    """

    def __init__(
        self,
        *,
        bundle_id: str,
        mcp_tool_specs: List[Dict[str, Any]],
        adapter_factory: Optional[MCPAdapterFactory] = None,
        cache: Optional[NamespacedKVCache] = None,
        env_json: Optional[str] = None,
    ):
        self.bundle_id = bundle_id or "default"
        self.adapter_factory = adapter_factory
        self.mcp_specs = _normalize_mcp_specs(mcp_tool_specs)

        env_json = env_json or ""
        self._services_cfg = _parse_mcp_services_env(env_json)

        self.cache = cache or self._build_cache()

        self._tool_index: Dict[str, Dict[str, Any]] = {}

    def _build_cache(self) -> Optional[NamespacedKVCache]:
        try:
            settings = get_settings()
            tenant = settings.TENANT
            project = settings.PROJECT
            namespace = f"{REDIS.CACHE.MCP}:{tenant}:{project}:{self.bundle_id}"
            default_ttl = int(os.getenv("MCP_CACHE_TTL_SECONDS", "3600"))
            cfg = NamespacedKVCacheConfig(
                redis_url=settings.REDIS_URL,
                namespace=namespace,
                tenant=tenant,
                project=project,
                default_ttl_seconds=default_ttl,
                decode_responses=True,
            )
            return create_namespaced_kv_cache_from_config(cfg)
        except Exception:
            return None

    def _server_spec(self, server_id: str) -> Optional[MCPServerSpec]:
        cfg = self._services_cfg.get(server_id) or {}
        if not isinstance(cfg, dict):
            return None
        endpoint = cfg.get("endpoint") or cfg.get("url") or ""
        if not endpoint:
            return None
        return MCPServerSpec(
            server_id=server_id,
            display_name=str(cfg.get("name") or server_id),
            endpoint=str(endpoint),
            auth_profile=cfg.get("auth") or cfg.get("credentials"),
        )

    async def list_tools(self) -> List[MCPToolSchema]:
        if not self.mcp_specs:
            return []
        results: List[MCPToolSchema] = []
        for spec in self.mcp_specs:
            server = self._server_spec(spec.server_id)
            if not server:
                logger.warning("MCP server not configured: %s", spec.server_id)
                continue
            tools = await self._tools_for_server(server)
            if not tools:
                continue
            if spec.tools and "*" not in spec.tools:
                tools = [t for t in tools if t.id in set(spec.tools)]
            results.extend(tools)
        return results

    async def build_tool_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for spec in self.mcp_specs:
            server = self._server_spec(spec.server_id)
            if not server:
                continue
            tools = await self._tools_for_server(server)
            if spec.tools and "*" not in spec.tools:
                tools = [t for t in tools if t.id in set(spec.tools)]
            alias = spec.alias or f"mcp_{spec.server_id}"
            for t in tools:
                params = _params_from_schema(t.params_schema)
                entry = _mk_mcp_entry(
                    alias=alias,
                    tool=t,
                    server_id=spec.server_id,
                    params=params,
                )
                entries.append(entry)
                self._tool_index[entry["id"]] = entry
        return entries

    async def _tools_for_server(self, server: MCPServerSpec) -> List[MCPToolSchema]:
        cache_key = f"{server.server_id}:tools"
        cached = await self.cache.get_json(cache_key) if self.cache else None
        if cached:
            return [MCPToolSchema(**t) for t in cached if isinstance(t, dict)]
        if not self.adapter_factory:
            return []
        adapter = self.adapter_factory(server)
        tools = await adapter.list_tools()
        ttl = _ttl_for_server(self._services_cfg.get(server.server_id) or {})
        if self.cache:
            await self.cache.set_json(cache_key, [t.__dict__ for t in tools], ttl_seconds=ttl)
        return tools


def _ttl_for_server(cfg: Dict[str, Any]) -> int:
    ttl = cfg.get("ttl_seconds") or cfg.get("ttl") or 3600
    try:
        return int(ttl)
    except Exception:
        return 3600


def _params_from_schema(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    params: List[Dict[str, Any]] = []
    if not isinstance(schema, dict):
        return params
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not isinstance(props, dict):
        return params
    for name, meta in props.items():
        if not isinstance(meta, dict):
            meta = {}
        annot = meta.get("type") or ""
        desc = meta.get("description") or ""
        if annot and desc:
            annot = f"{annot}, {desc}"
        params.append({
            "name": name,
            "annotation": annot,
            "default": None if name in required else meta.get("default"),
            "kind": "POSITIONAL_OR_KEYWORD",
        })
    return params


def _mk_mcp_entry(
    *,
    alias: str,
    tool: MCPToolSchema,
    server_id: str,
    params: List[Dict[str, Any]],
) -> Dict[str, Any]:
    args_doc = {}
    for p in params:
        type_hint = (p.get("annotation") or "any")
        if p.get("default") not in (None,):
            type_hint += f" (default={p['default']})"
        args_doc[p["name"]] = type_hint
    returns_doc = (tool.returns_schema or {}) if isinstance(tool.returns_schema, dict) else {}
    return {
        "id": f"{alias}.{tool.id}",
        "desc": tool.description or tool.name,
        "params": params,
        "import": f"mcp.{server_id}",
        "call_template": (
            f"{alias}.{tool.id}({', '.join([p['name']+'={'+p['name']+'}' for p in params])})"
            if params else f"{alias}.{tool.id}()"
        ),
        "is_async": True,
        "doc": {
            "purpose": tool.description or tool.name,
            "args": args_doc,
            "returns": returns_doc or "JSON (tool-specific)",
            "constraints": [],
            "examples": [],
        },
        "raw": {"mcp": {"server": server_id, "tool": tool.id}},
        "plugin": "mcp",
        "plugin_alias": alias,
    }

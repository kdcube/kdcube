# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Per-user agent capability inventory + selection narrowing.

The bundle config (``surfaces.as_consumer.agents.<id>.{tools,skills}``) is the
INVENTORY an administrator grants an agent. This module enumerates that
inventory for a picker UI (``agent_capabilities_catalog``) and applies a
per-user deny-list selection as a pure narrowing of the resolved runtime
configs (``narrow_agent_tool_config`` / ``narrow_agent_skill_config``).

Selection record shape (deny-list; absent key/entry = enabled):

    {
      "tools": {"<alias>": true | ["<tool_name>", ...]},
      "mcp": {"<server_id>": true},
      "named_services": {"<namespace>": true},
      "skills": ["<namespace>.<skill_id>", ...]
    }

The user can only remove; nothing outside the configured inventory can ever be
enabled (``clamp_selection``). System tool groups (``io``/``context``) are
locked on and immune to denial.
"""

from __future__ import annotations

import importlib
import pathlib
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    _NAMED_SERVICE_OPERATION_TO_TOOL,
    _agent_tool_connections,
    _named_service_tools_for_connection,
    AgentToolConfig,
    DEFAULT_AGENT_ID,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    NAMED_SERVICE_TOOLS_ALIAS,
    NAMED_SERVICE_TOOLS_MODULE,
)

# io_tools carries the ReAct `tool_call` mechanism and ctx_tools the context
# plumbing — always present regardless of the user's pick, else the agent
# cannot act. Config `name:` forms included so denials keyed either way are
# stripped.
SYSTEM_TOOL_ALIASES = frozenset({"io_tools", "ctx_tools", "io", "context"})


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_namespace(value: Any) -> str:
    return _norm(value).lower().rstrip(":")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = _norm(item)
            if text and text not in out:
                out.append(text)
        return out
    text = _norm(value)
    return [text] if text else []


def _first_para(text: str) -> str:
    return _norm(text).split("\n\n")[0].strip()


def is_system_tool_alias(alias: Any) -> bool:
    return _norm(alias) in SYSTEM_TOOL_ALIASES


# ── catalog (the pickable inventory) ─────────────────────────────────────────


def _module_tool_docs(module_name: str) -> dict[str, str]:
    """``{tool_name: first-paragraph description}`` via light introspection.

    Mirrors the tool manager's own extraction (`ToolSubsystem._introspect_module`):
    a tool's doc is its ``list_tools()`` meta ``description``, else the callable's
    SK/``description`` attribute, else its ``__doc__``.
    """
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return {}
    owner = getattr(mod, "tools", mod)
    reg: Mapping[str, Any] = {}
    if hasattr(mod, "list_tools"):
        try:
            reg = mod.list_tools() or {}
        except Exception:
            reg = {}
    docs: dict[str, str] = {}
    names = list(reg.keys()) if isinstance(reg, Mapping) and reg else [
        name for name in dir(owner) if not name.startswith("_")
    ]
    for fn_name in names:
        meta = reg.get(fn_name) if isinstance(reg, Mapping) else None
        fn = (meta.get("callable") if isinstance(meta, Mapping) else None) or getattr(owner, fn_name, None)
        desc = _norm(meta.get("description")) if isinstance(meta, Mapping) else ""
        if not desc and fn is not None:
            desc = (
                getattr(fn, "__kernel_function_description__", "")
                or getattr(fn, "description", "")
                or (getattr(fn, "__doc__", "") or "")
            )
        desc = _first_para(str(desc or ""))
        if callable(fn) or (isinstance(meta, Mapping) and meta):
            docs[fn_name] = desc
    return docs


def _module_tool_names(module_name: str) -> list[str] | None:
    """Concrete tool names published by a module, or None when unknowable."""
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return None
    if hasattr(mod, "list_tools"):
        try:
            reg = mod.list_tools() or {}
            if isinstance(reg, Mapping):
                return [str(k) for k in reg.keys()]
        except Exception:
            return None
    owner = getattr(mod, "tools", mod)
    names = [name for name in dir(owner) if not name.startswith("_") and callable(getattr(owner, name, None))]
    return names or None


def agent_capabilities_catalog(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> dict[str, Any]:
    """The pickable inventory for one agent, ready for a selection UI.

    Categories match the selection record: python tool groups (with per-tool
    names + descriptions), MCP entries per server, named-service namespaces,
    and skills expanded to concrete entries with front-matter.
    """
    tools_out: list[dict[str, Any]] = []
    mcp_out: list[dict[str, Any]] = []
    namespaces_out: list[dict[str, Any]] = []

    for connection in _agent_tool_connections(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    ):
        kind = str(connection.get("kind") or "python").strip().lower()
        alias = _norm(connection.get("alias") or connection.get("name"))

        if kind == "python":
            if not alias:
                continue
            allowed = _string_list(connection.get("allowed"))
            module = _norm(connection.get("module"))
            docs: dict[str, str] = _module_tool_docs(module) if module else {}
            names = allowed or (list(docs.keys()) if docs else [])
            tools_out.append({
                "alias": alias,
                "name": _norm(connection.get("name")) or alias,
                "kind": "python",
                "system": is_system_tool_alias(alias) or is_system_tool_alias(connection.get("name")),
                "tools": [
                    {"name": tool_name, "description": docs.get(tool_name, "")}
                    for tool_name in names
                ],
            })
            continue

        if kind == "mcp":
            server_id = _norm(
                connection.get("server_id") or connection.get("server") or connection.get("name")
            )
            if not server_id:
                continue
            mcp_out.append({
                "server_id": server_id,
                "alias": alias or f"mcp_{server_id}",
                "name": _norm(connection.get("name")) or server_id,
                "tools": _string_list(connection.get("allowed") or connection.get("tools")) or ["*"],
            })
            continue

        if kind == "named_service":
            raw_namespaces = connection.get("namespaces")
            if not isinstance(raw_namespaces, Mapping):
                continue
            ns_alias = alias or NAMED_SERVICE_TOOLS_ALIAS
            for namespace, namespace_cfg in raw_namespaces.items():
                ns = _norm_namespace(namespace)
                if not ns or not isinstance(namespace_cfg, Mapping):
                    continue
                operations = _string_list(
                    namespace_cfg.get("allowed")
                    or namespace_cfg.get("allowed_operations")
                    or namespace_cfg.get("operations")
                )
                namespaces_out.append({
                    "namespace": ns,
                    "alias": ns_alias,
                    "operations": operations,
                    "tools": [
                        _NAMED_SERVICE_OPERATION_TO_TOOL[op]
                        for op in operations
                        if op in _NAMED_SERVICE_OPERATION_TO_TOOL
                    ],
                })
            continue

    skills_out = _catalog_skills(
        bundle_props,
        agent_id,
        bundle_root=bundle_root,
        default_agent_id=default_agent_id,
    )

    return {
        "agent": _norm(agent_id) or default_agent_id,
        "tools": tools_out,
        "mcp": mcp_out,
        "named_services": namespaces_out,
        "skills": skills_out,
    }


def _skill_enabled_patterns(skill_config: AgentSkillConfig) -> list[str]:
    patterns: list[str] = []
    for cfg in (skill_config.agents_config or {}).values():
        for pat in _string_list((cfg or {}).get("enabled")):
            if pat not in patterns:
                patterns.append(pat)
    return patterns


def _catalog_skills(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None,
    default_agent_id: str,
) -> list[dict[str, Any]]:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import (
            agent_skill_config_from_bundle_props,
        )
        from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem

        skill_config = agent_skill_config_from_bundle_props(
            bundle_props,
            agent_id,
            bundle_root=bundle_root,
            default_agent_id=default_agent_id,
        )
        if skill_config.custom_skills_root == "":
            # Skills surface explicitly disabled for this agent.
            custom_root = None
        else:
            custom_root = skill_config.custom_skills_root
        subsystem = SkillsSubsystem(
            descriptor={
                "custom_skills_root": str(custom_root) if custom_root else None,
                "agents_config": dict(skill_config.agents_config or {}),
            },
            bundle_root=pathlib.Path(bundle_root) if bundle_root else None,
        )
        return subsystem.picker_catalog(_skill_enabled_patterns(skill_config))
    except Exception:
        return []


# ── selection clamp (write-side guard) ───────────────────────────────────────


def clamp_selection(
    disabled: Mapping[str, Any] | None,
    catalog: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Sanitize a deny-list so it never references anything outside the live
    inventory; system tool aliases are stripped (locked on)."""
    catalog = catalog or {}
    disabled = disabled or {}

    tool_names_by_alias: dict[str, set[str]] = {}
    system_aliases: set[str] = set(SYSTEM_TOOL_ALIASES)
    for group in catalog.get("tools") or []:
        alias = _norm((group or {}).get("alias"))
        if not alias:
            continue
        if bool((group or {}).get("system")):
            system_aliases.add(alias)
        tool_names_by_alias[alias] = {
            _norm(t.get("name")) for t in (group.get("tools") or []) if _norm(t.get("name"))
        }
    mcp_servers = {_norm(e.get("server_id")) for e in (catalog.get("mcp") or []) if _norm(e.get("server_id"))}
    namespaces = {
        _norm_namespace(e.get("namespace"))
        for e in (catalog.get("named_services") or [])
        if _norm_namespace(e.get("namespace"))
    }
    skill_ids = {_norm(s.get("id")) for s in (catalog.get("skills") or []) if _norm(s.get("id"))}

    out_tools: dict[str, Any] = {}
    raw_tools = disabled.get("tools")
    if isinstance(raw_tools, Mapping):
        for alias, value in raw_tools.items():
            alias = _norm(alias)
            if not alias or alias in system_aliases or alias not in tool_names_by_alias:
                continue
            if value is True:
                out_tools[alias] = True
                continue
            names = [n for n in _string_list(value) if n in tool_names_by_alias[alias]]
            if names:
                out_tools[alias] = names

    out_mcp: dict[str, Any] = {}
    raw_mcp = disabled.get("mcp")
    if isinstance(raw_mcp, Mapping):
        for server_id, value in raw_mcp.items():
            server_id = _norm(server_id)
            if server_id and server_id in mcp_servers and value is True:
                out_mcp[server_id] = True

    out_namespaces: dict[str, Any] = {}
    raw_namespaces = disabled.get("named_services")
    if isinstance(raw_namespaces, Mapping):
        for namespace, value in raw_namespaces.items():
            namespace = _norm_namespace(namespace)
            if namespace and namespace in namespaces and value is True:
                out_namespaces[namespace] = True

    out_skills: list[str] = []
    for skill_id in _string_list(disabled.get("skills")):
        if skill_id in skill_ids and skill_id not in out_skills:
            out_skills.append(skill_id)

    out: dict[str, Any] = {}
    if out_tools:
        out["tools"] = out_tools
    if out_mcp:
        out["mcp"] = out_mcp
    if out_namespaces:
        out["named_services"] = out_namespaces
    if out_skills:
        out["skills"] = out_skills
    return out


# ── narrowing (read-side application; effective = configured − disabled) ─────


def _disabled_tool_maps(disabled: Mapping[str, Any] | None) -> tuple[set[str], dict[str, set[str]]]:
    fully: set[str] = set()
    per_tool: dict[str, set[str]] = {}
    raw = (disabled or {}).get("tools")
    if isinstance(raw, Mapping):
        for alias, value in raw.items():
            alias = _norm(alias)
            if not alias or alias in SYSTEM_TOOL_ALIASES:
                continue
            if value is True:
                fully.add(alias)
            else:
                names = set(_string_list(value))
                if names:
                    per_tool[alias] = names
    return fully, per_tool


def _disabled_flag_set(disabled: Mapping[str, Any] | None, key: str, *, namespace: bool = False) -> set[str]:
    raw = (disabled or {}).get(key)
    out: set[str] = set()
    if isinstance(raw, Mapping):
        for name, value in raw.items():
            text = _norm_namespace(name) if namespace else _norm(name)
            if text and value:
                out.add(text)
    return out


def _materialize_alias_tool_names(cfg: AgentToolConfig, alias: str) -> list[str] | None:
    """Expand a None (wildcard) configured allowed list to concrete tool names."""
    for spec in cfg.tool_specs:
        if _norm(spec.get("alias")) != alias:
            continue
        module = _norm(spec.get("module"))
        if module:
            return _module_tool_names(module)
        return None
    return None


def _named_service_aliases(cfg: AgentToolConfig) -> set[str]:
    aliases: set[str] = set()
    for spec in cfg.tool_specs:
        if _norm(spec.get("module")) == NAMED_SERVICE_TOOLS_MODULE:
            alias = _norm(spec.get("alias"))
            if alias:
                aliases.add(alias)
    return aliases


def _recomputed_named_service_tools(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str | None,
    default_agent_id: str,
    denied_namespaces: set[str],
) -> dict[str, list[str]]:
    """``{alias: [tool names]}`` union over the ENABLED namespaces only."""
    out: dict[str, list[str]] = {}
    for connection in _agent_tool_connections(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    ):
        if str(connection.get("kind") or "python").strip().lower() != "named_service":
            continue
        alias = _norm(connection.get("alias") or connection.get("name")) or NAMED_SERVICE_TOOLS_ALIAS
        raw_namespaces = connection.get("namespaces")
        if not isinstance(raw_namespaces, Mapping):
            continue
        enabled_only = {
            ns: ns_cfg
            for ns, ns_cfg in raw_namespaces.items()
            if _norm_namespace(ns) not in denied_namespaces
        }
        tools = _named_service_tools_for_connection({"namespaces": enabled_only})
        bucket = out.setdefault(alias, [])
        for tool_name in tools:
            if tool_name not in bucket:
                bucket.append(tool_name)
    return out


def _prune_tool_id_keys(mapping: Mapping[str, Any], removed_aliases: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tool_id, value in mapping.items():
        alias = str(tool_id).split(".", 1)[0]
        mcp_alias = ""
        if str(tool_id).startswith("mcp."):
            parts = str(tool_id).split(".", 2)
            mcp_alias = parts[1] if len(parts) > 1 else ""
        if alias in removed_aliases or (mcp_alias and mcp_alias in removed_aliases):
            continue
        out[tool_id] = value
    return out


def narrow_agent_tool_config(
    cfg: AgentToolConfig,
    disabled: Mapping[str, Any] | None,
    *,
    bundle_props: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> AgentToolConfig:
    """Return a narrowed copy of ``cfg`` (effective = configured − disabled).

    Pure: never widens, never mutates ``cfg``. System tool aliases are immune.
    ``bundle_props``/``agent_id`` are needed only to recompute the
    named-service tool allowlist over the enabled namespaces.
    """
    if not disabled:
        return cfg

    fully_disabled, per_tool_disabled = _disabled_tool_maps(disabled)
    denied_servers = _disabled_flag_set(disabled, "mcp")
    denied_namespaces = _disabled_flag_set(disabled, "named_services", namespace=True)

    removed_aliases: set[str] = set(fully_disabled)
    allowed_map: dict[str, list[str] | None] = {
        alias: (list(names) if names is not None else None)
        for alias, names in cfg.allowed_tool_names_by_alias.items()
    }

    # MCP: drop denied servers (whole server per D3).
    new_mcp_specs: list[dict[str, Any]] = []
    for spec in cfg.mcp_tool_specs:
        server_id = _norm(spec.get("server_id"))
        alias = _norm(spec.get("alias")) or f"mcp_{server_id}"
        if server_id in denied_servers:
            removed_aliases.add(alias)
            continue
        new_mcp_specs.append(dict(spec))

    # Named service: recompute the tool allowlist over enabled namespaces only.
    ns_aliases = _named_service_aliases(cfg)
    if denied_namespaces and ns_aliases:
        recomputed = _recomputed_named_service_tools(
            bundle_props,
            agent_id=agent_id,
            default_agent_id=default_agent_id,
            denied_namespaces=denied_namespaces,
        ) if bundle_props is not None else {}
        for alias in ns_aliases:
            if alias in removed_aliases:
                continue
            if bundle_props is None:
                # Cannot recompute per-namespace tools without the inventory;
                # fail open for this alias (dispatch-time namespace deny still
                # applies via the runtime deny-set hook).
                continue
            tools = recomputed.get(alias) or []
            if tools:
                allowed_map[alias] = tools
            else:
                removed_aliases.add(alias)

    # Python per-tool denials (materialize wildcard entries first).
    for alias, denied_names in per_tool_disabled.items():
        if alias in removed_aliases or alias not in allowed_map:
            continue
        configured = allowed_map.get(alias)
        if configured is None:
            configured = _materialize_alias_tool_names(cfg, alias)
            if configured is None:
                # Unknowable wildcard: fail open for this alias.
                continue
        effective = [name for name in configured if name not in denied_names]
        if effective:
            allowed_map[alias] = effective
        else:
            removed_aliases.add(alias)

    new_tool_specs = [
        dict(spec) for spec in cfg.tool_specs if _norm(spec.get("alias")) not in removed_aliases
    ]
    new_allowed_plugins = [alias for alias in cfg.allowed_plugins if alias not in removed_aliases]
    new_allowed_map = {
        alias: (list(names) if names is not None else None)
        for alias, names in allowed_map.items()
        if alias not in removed_aliases
    }

    # Drop runtime/traits/claim policies for removed aliases and denied tools,
    # so e.g. connected-account preflight never demands consent for a tool the
    # user turned off.
    denied_tool_ids = {
        f"{alias}.{name}" for alias, names in per_tool_disabled.items() for name in names
    }
    new_tool_runtime = {
        tool_id: mode
        for tool_id, mode in _prune_tool_id_keys(cfg.tool_runtime, removed_aliases).items()
        if tool_id not in denied_tool_ids
    }
    new_tool_traits = {
        tool_id: dict(traits)
        for tool_id, traits in _prune_tool_id_keys(cfg.tool_traits, removed_aliases).items()
        if tool_id not in denied_tool_ids
    }
    new_claim_policies = []
    for policy in cfg.tool_claim_policies:
        tool_name = _norm(getattr(policy, "tool_name", ""))
        alias = tool_name.split(".", 1)[0]
        mcp_alias = tool_name.split(".", 2)[1] if tool_name.startswith("mcp.") and tool_name.count(".") >= 2 else ""
        if alias in removed_aliases or (mcp_alias and mcp_alias in removed_aliases):
            continue
        if tool_name in denied_tool_ids:
            continue
        new_claim_policies.append(policy)

    return AgentToolConfig(
        tool_specs=new_tool_specs,
        mcp_tool_specs=new_mcp_specs,
        tool_runtime=new_tool_runtime,
        tool_traits=new_tool_traits,
        allowed_plugins=new_allowed_plugins,
        allowed_tool_names_by_alias=new_allowed_map,
        tool_claim_policies=new_claim_policies,
    )


def narrow_agent_skill_config(
    cfg: AgentSkillConfig,
    disabled_skills: Sequence[str] | None,
) -> AgentSkillConfig:
    """Return a copy of ``cfg`` with the denied skill ids appended to every
    consumer's disabled list, plus the ``"*"`` catch-all consumer so agents
    without per-consumer entries still honour the denial."""
    denied = _string_list(disabled_skills)
    if not denied:
        return cfg
    agents_config: dict[str, dict[str, Any]] = {
        consumer: dict(entry or {}) for consumer, entry in (cfg.agents_config or {}).items()
    }
    for consumer in [*agents_config.keys(), "*"]:
        entry = agents_config.setdefault(consumer, {})
        merged = _string_list(entry.get("disabled"))
        for skill_id in denied:
            if skill_id not in merged:
                merged.append(skill_id)
        entry["disabled"] = merged
    return AgentSkillConfig(
        custom_skills_root=cfg.custom_skills_root,
        agents_config=agents_config,
    )


def selection_deltas(disabled: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compact, log-friendly summary of what a selection turns off."""
    fully, per_tool = _disabled_tool_maps(disabled)
    return {
        "tools_off": sorted(fully),
        "tool_names_off": {alias: sorted(names) for alias, names in per_tool.items()},
        "mcp_off": sorted(_disabled_flag_set(disabled, "mcp")),
        "named_services_off": sorted(_disabled_flag_set(disabled, "named_services", namespace=True)),
        "skills_off": _string_list((disabled or {}).get("skills")),
    }


__all__ = [
    "SYSTEM_TOOL_ALIASES",
    "agent_capabilities_catalog",
    "clamp_selection",
    "is_system_tool_alias",
    "narrow_agent_skill_config",
    "narrow_agent_tool_config",
    "selection_deltas",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.event_identity import normalize_agent_id

DEFAULT_AGENT_ID = "default_agent"


@dataclass(frozen=True)
class AgentSkillConfig:
    """Resolved, runtime-ready skill config for one model-facing agent."""

    custom_skills_root: pathlib.Path | str | None = None
    agents_config: dict[str, dict[str, Any]] = field(default_factory=dict)


def _get_path(data: Mapping[str, Any] | None, path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur


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


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                _append_unique(out, text)
        return out
    text = str(value or "").strip()
    return [text] if text else []


def _agent_skill_surface(
    bundle_props: Mapping[str, Any] | None,
    *,
    agent_id: str | None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> Mapping[str, Any] | None:
    surface_agents = _get_path(bundle_props or {}, "surfaces.as_consumer.agents", {})
    if not isinstance(surface_agents, Mapping):
        return None
    for key in _agent_keys(agent_id, default_agent_id=default_agent_id):
        agent = surface_agents.get(key)
        if isinstance(agent, Mapping):
            skills = agent.get("skills")
            if isinstance(skills, Mapping):
                return skills
            if skills is False:
                return {"enabled": False}
    return None


def _resolve_custom_root(value: Any, *, bundle_root: str | pathlib.Path | None) -> pathlib.Path | str | None:
    if value is False:
        return ""
    if value is None:
        return None
    text = str(value or "").strip()
    if not text:
        return ""
    path = pathlib.Path(text)
    if path.is_absolute() or bundle_root is None:
        return path
    return (pathlib.Path(bundle_root) / path).resolve()


def _visibility_cfg(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    enabled = _string_list(raw.get("enabled") or raw.get("enabled_skills"))
    disabled = _string_list(raw.get("disabled") or raw.get("disabled_skills"))
    if enabled:
        out["enabled"] = enabled
    if disabled:
        out["disabled"] = disabled
    return out


def agent_skill_config_from_bundle_props(
    bundle_props: Mapping[str, Any] | None,
    agent_id: str | None,
    *,
    bundle_root: str | pathlib.Path | None = None,
    default_agent_id: str = DEFAULT_AGENT_ID,
) -> AgentSkillConfig:
    """Resolve `surfaces.as_consumer.agents.<agent>.skills`.

    Bundle authors configure skills next to tools for the consuming agent. The
    returned `agents_config` is the internal shape expected by SkillsSubsystem;
    it may contain ReAct sub-consumers such as the decision prompt.
    """

    raw = _agent_skill_surface(
        bundle_props,
        agent_id=agent_id,
        default_agent_id=default_agent_id,
    )
    if not isinstance(raw, Mapping):
        return AgentSkillConfig()

    if raw.get("enabled") is False:
        return AgentSkillConfig(custom_skills_root="")

    root_value = (
        raw.get("custom_root")
        if "custom_root" in raw
        else raw.get("custom_skills_root", raw.get("root"))
    )
    custom_root = _resolve_custom_root(root_value, bundle_root=bundle_root)

    agents_config: dict[str, dict[str, Any]] = {}
    direct_visibility = _visibility_cfg(raw)
    if direct_visibility:
        for key in _agent_keys(agent_id, default_agent_id=default_agent_id):
            agents_config[key] = dict(direct_visibility)

    consumers = raw.get("consumers") or raw.get("agents_config") or raw.get("AGENTS_CONFIG")
    if isinstance(consumers, Mapping):
        for consumer, cfg in consumers.items():
            if not isinstance(cfg, Mapping):
                continue
            key = str(consumer or "").strip()
            if key:
                agents_config[key] = _visibility_cfg(cfg)

    return AgentSkillConfig(
        custom_skills_root=custom_root,
        agents_config=agents_config,
    )


__all__ = [
    "AgentSkillConfig",
    "DEFAULT_AGENT_ID",
    "agent_skill_config_from_bundle_props",
]

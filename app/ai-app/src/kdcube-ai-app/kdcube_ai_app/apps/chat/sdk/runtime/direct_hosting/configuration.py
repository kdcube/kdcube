"""YAML-owned configuration for directly hosted agent adapters."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    AgentToolConfig,
    agent_tool_config_from_bundle_props,
)


@dataclass(frozen=True)
class ConfiguredSkills:
    root: Path | None
    enabled: tuple[str, ...]


@dataclass(frozen=True)
class ConfiguredAgentInput:
    """Explicit caller and conversation input for one direct agent run."""

    user_id: str
    user_type: str
    session_id: str
    conversation_id: str
    recall_conversation_id: str = ""

    def continuity_key(
        self,
        *,
        tenant: str,
        project: str,
        agent_id: str,
    ) -> str:
        """Stable agent-private key layered on the durable conversation scope."""
        return "/".join(
            quote(str(value), safe="-._~@")
            for value in (
                tenant,
                project,
                self.user_id,
                self.conversation_id,
                agent_id,
            )
        )

    def run_path(self, root: Path, *, run_id: str) -> Path:
        """Filesystem-safe evidence path for one invocation of the conversation."""
        return (
            root
            / "runs"
            / quote(self.user_id, safe="-._~@")
            / quote(self.conversation_id, safe="-._~@")
            / quote(run_id, safe="-._~@")
        )


def _agent(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("agent")
    if not isinstance(value, Mapping):
        raise ValueError("configuration section 'agent' must be a mapping")
    return value


def agent_instructions(config: Mapping[str, Any], *, fallback: str) -> str:
    value = str(_agent(config).get("instructions") or "").strip()
    return value or fallback


def configured_agent_input(
    config: Mapping[str, Any],
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    session_id: str | None = None,
    recall_conversation_id: str | None = None,
) -> ConfiguredAgentInput:
    """Resolve required direct-run input, with explicit CLI overrides."""
    raw = _agent(config).get("input")
    if not isinstance(raw, Mapping):
        raise ValueError("agent.input must be a mapping")

    values = {
        "user_id": user_id if user_id is not None else raw.get("user_id"),
        "user_type": raw.get("user_type") or "regular",
        "session_id": session_id if session_id is not None else raw.get("session_id"),
        "conversation_id": (
            conversation_id
            if conversation_id is not None
            else raw.get("conversation_id")
        ),
    }
    normalized = {key: str(value or "").strip() for key, value in values.items()}
    missing = [key for key, value in normalized.items() if not value]
    if missing:
        raise ValueError(
            "agent.input is missing required values: " + ", ".join(missing)
        )
    recall = str(
        recall_conversation_id
        if recall_conversation_id is not None
        else raw.get("recall_conversation_id")
        or ""
    ).strip()
    return ConfiguredAgentInput(**normalized, recall_conversation_id=recall)


def configured_tool_connections(
    config: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    """Return the standard tool-source rows declared by a direct agent."""
    raw_tools = _agent(config).get("tools")
    if not isinstance(raw_tools, Sequence) or isinstance(raw_tools, (str, bytes)):
        raise ValueError("agent.tools must be a list of tool mappings")

    tools: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_tools):
        if not isinstance(raw, Mapping):
            raise ValueError(f"agent.tools[{index}] must be a mapping")
        connection_id = str(raw.get("id") or "").strip()
        if not connection_id:
            raise ValueError(f"agent.tools[{index}].id is required")
        if connection_id in seen:
            raise ValueError(f"agent.tools contains duplicate id {connection_id!r}")
        raw_settings = raw.get("settings")
        if raw_settings is not None and not isinstance(raw_settings, Mapping):
            raise ValueError(f"agent.tools[{index}].settings must be a mapping")
        seen.add(connection_id)
        tools.append(dict(raw))
    if not tools:
        raise ValueError("agent.tools must declare at least one tool")
    return tuple(tools)


def configured_agent_tool_config(
    config: Mapping[str, Any],
    *,
    agent_id: str,
    bundle_root: Path,
) -> AgentToolConfig:
    """Project direct-agent YAML through KDCube's canonical tool parser."""
    connections = configured_tool_connections(config)
    props = {
        "surfaces": {
            "as_consumer": {
                "default_agent": agent_id,
                "agents": {agent_id: {"tools": list(connections)}},
            }
        }
    }
    resolved = agent_tool_config_from_bundle_props(
        props,
        agent_id,
        bundle_root=bundle_root,
        default_agent_id=agent_id,
    )
    if not resolved.allowed_plugins:
        raise ValueError("agent.tools did not resolve any supported tool sources")
    return resolved


def configured_tool_ids(tool_config: AgentToolConfig) -> tuple[str, ...]:
    """Return explicit canonical ids selected by a direct-agent tool config."""
    mcp_aliases = {
        str(spec.get("alias") or "").strip()
        for spec in tool_config.mcp_tool_specs
        if str(spec.get("alias") or "").strip()
    }
    resolved: list[str] = []
    for alias, names in tool_config.allowed_tool_names_by_alias.items():
        if names is None or "*" in names:
            raise ValueError(
                "direct agent examples require explicit allowed tool names for "
                f"source {alias!r}"
            )
        for name in names:
            tool_id = (
                f"mcp.{alias}.{name}" if alias in mcp_aliases else f"{alias}.{name}"
            )
            if tool_id not in resolved:
                resolved.append(tool_id)
    return tuple(resolved)


def configured_tool_settings(
    config: Mapping[str, Any],
    *,
    connection_id: str,
) -> Mapping[str, Any]:
    """Return settings attached to one exact configured source row."""
    for connection in configured_tool_connections(config):
        if str(connection.get("id") or "").strip() == connection_id:
            return dict(connection.get("settings") or {})
    raise ValueError(f"agent.tools has no tool source with id {connection_id!r}")


def configured_web_search(
    config: Mapping[str, Any],
    *,
    connection_id: str,
) -> Mapping[str, Any]:
    """Validate Web Search settings attached to its configured source row."""
    raw = configured_tool_settings(config, connection_id=connection_id)
    filter_config = raw.get("filter")
    if not isinstance(filter_config, Mapping):
        raise ValueError(
            f"agent.tools[{connection_id!r}].settings.filter must be a mapping"
        )
    for key in ("allowlist", "blocklist"):
        value = filter_config.get(key)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(
                f"agent.tools[{connection_id!r}].settings.filter.{key} must be a list"
            )
    if not isinstance(filter_config.get("ssrf_guard"), bool):
        raise ValueError(
            f"agent.tools[{connection_id!r}].settings.filter.ssrf_guard must be true or false"
        )
    return raw


def configured_run_directory(
    config: Mapping[str, Any],
    *,
    config_path: Path,
) -> Path:
    """Resolve the directory that receives run files and evidence."""
    raw = _agent(config).get("run_directory") or "./output"
    if not isinstance(raw, (str, Path)):
        raise ValueError("agent.run_directory must be a path string")
    candidate = Path(raw).expanduser()
    return (
        candidate.resolve()
        if candidate.is_absolute()
        else (config_path.parent / candidate).resolve()
    )


def configured_skills(
    config: Mapping[str, Any], *, config_path: Path
) -> ConfiguredSkills:
    raw = _agent(config).get("skills") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("agent.skills must be a mapping")
    enabled_raw = raw.get("enabled") or []
    if not isinstance(enabled_raw, Sequence) or isinstance(enabled_raw, (str, bytes)):
        raise ValueError("agent.skills.enabled must be a list")
    enabled = tuple(
        dict.fromkeys(str(item).strip() for item in enabled_raw if str(item).strip())
    )
    root_raw = str(raw.get("root") or "").strip()
    root = None
    if root_raw:
        candidate = Path(root_raw).expanduser()
        root = (
            candidate.resolve()
            if candidate.is_absolute()
            else (config_path.parent / candidate).resolve()
        )
        if not root.is_dir():
            raise ValueError(f"agent.skills.root does not exist: {root}")
    return ConfiguredSkills(root=root, enabled=enabled)


def activate_configured_skills(
    config: Mapping[str, Any],
    *,
    config_path: Path,
    consumers: Sequence[str],
) -> tuple[Any, ConfiguredSkills]:
    from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
        SkillsSubsystem,
        set_active_skills_subsystem,
    )

    selection = configured_skills(config, config_path=config_path)
    policy = (
        {"enabled": list(selection.enabled)}
        if selection.enabled
        else {"disabled": ["*"]}
    )
    visibility = {str(consumer): dict(policy) for consumer in consumers}
    subsystem = SkillsSubsystem(
        descriptor={
            "custom_skills_root": str(selection.root) if selection.root else None,
            "agents_config": visibility,
        },
        bundle_root=config_path.parent,
    )
    set_active_skills_subsystem(subsystem)
    registry = subsystem.get_skill_registry()
    missing = sorted(
        skill_id for skill_id in selection.enabled if skill_id not in registry
    )
    if missing:
        raise ValueError(
            f"agent.skills.enabled contains unknown skills: {', '.join(missing)}"
        )
    return subsystem, selection


def verify_docker_image(profile: Mapping[str, Any]) -> str:
    image = str(profile.get("image") or "").strip()
    if not image:
        raise ValueError("isolated execution profile has no image")
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError(
            "Docker is required when the isolated code-execution tool is enabled"
        )
    result = subprocess.run(
        [docker, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"isolated execution image {image!r} is not available; "
            "build it before enabling code execution"
        )
    return image


async def verify_playwright_chromium() -> str:
    """Prove that the host-side renderer can launch its Chromium runtime."""
    from kdcube_ai_app.infra.rendering.shared_browser import SharedBrowserService

    browser = SharedBrowserService(headless=True, auto_install_browser=False)
    try:
        await browser.start()
    except Exception as exc:
        raise RuntimeError(
            "the document-rendering tools require Playwright Chromium; run "
            "'.venv/bin/python -m playwright install chromium' in this example"
        ) from exc
    finally:
        await browser.close()
    return "chromium"


__all__ = [
    "ConfiguredAgentInput",
    "ConfiguredSkills",
    "activate_configured_skills",
    "agent_instructions",
    "configured_agent_input",
    "configured_agent_tool_config",
    "configured_skills",
    "configured_run_directory",
    "configured_tool_connections",
    "configured_tool_ids",
    "configured_tool_settings",
    "configured_web_search",
    "verify_docker_image",
    "verify_playwright_chromium",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

import yaml

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    SkillSpec,
    get_skill,
    import_skillset,
    resolve_skill_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import ClaudeCodeWorkspaceConfig


def _deep_merge_dict(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in dict(overlay or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, Mapping):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _write_json(path: Path, payload: Mapping[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _skill_key(spec: SkillSpec) -> str:
    return f"{spec.namespace}.{spec.id}" if spec.namespace else spec.id


def _slugify_skill_id(skill_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(skill_id).strip()).strip("-").lower()
    return slug or "skill"


def _read_skill_instruction(spec: SkillSpec) -> str:
    if spec.instruction_text:
        return spec.instruction_text.strip()
    path = spec.instruction_paths.full
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip()
    return ""


def _copy_skill_support_files(source_dir: Path | None, target_dir: Path, *, overwrite: bool) -> list[str]:
    if source_dir is None or not source_dir.exists():
        return []
    copied: list[str] = []
    ignored = {
        "SKILL.md",
        "skill.yaml",
        "skill.yml",
        "tools.yaml",
        "tools.yml",
    }
    for source in source_dir.rglob("*"):
        if not source.is_file() or source.is_symlink():
            continue
        rel = source.relative_to(source_dir)
        if rel.parts and rel.parts[0] in {"node_modules", ".git", "__pycache__"}:
            continue
        if source.name in ignored:
            continue
        target = target_dir / rel
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _render_claude_skill_markdown(
    spec: SkillSpec,
    *,
    allowed_tools: tuple[str, ...] = (),
) -> str:
    description_parts = [str(spec.description or "").strip()]
    if spec.when_to_use:
        description_parts.append("Use when: " + "; ".join(str(item).strip() for item in spec.when_to_use if str(item).strip()))
    description = " ".join(part for part in description_parts if part).strip()

    frontmatter: dict[str, Any] = {
        "name": spec.name or spec.id,
        "description": description or f"KDCube skill {_skill_key(spec)}.",
    }
    if allowed_tools:
        frontmatter["allowed-tools"] = ", ".join(allowed_tools)

    body = _read_skill_instruction(spec)
    if not body:
        body = f"# {spec.name or spec.id}\n\nImported from KDCube skill `{_skill_key(spec)}`."
    elif not body.lstrip().startswith("#"):
        body = "\n".join([f"# {spec.name or spec.id}", "", body])

    header = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return "\n".join(["---", header, "---", "", body.rstrip(), ""])


def _resolve_skill_allowed_tools(config: ClaudeCodeWorkspaceConfig) -> dict[str, tuple[str, ...]]:
    resolved: dict[str, tuple[str, ...]] = {}
    for raw_id, tools in dict(config.skill_allowed_tools or {}).items():
        sid = resolve_skill_ref(raw_id) or str(raw_id).strip()
        resolved[sid] = tuple(str(tool).strip() for tool in tools if str(tool).strip())
    return resolved


def materialize_kdcube_skills_for_claude(
    workspace_path: str | Path,
    skill_ids: tuple[str, ...] | list[str],
    *,
    skill_allowed_tools: Mapping[str, tuple[str, ...]] | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write KDCube skills as native Claude Code project skills.

    The active KDCube skills subsystem resolves ids and imports. This helper
    only materializes instructions/support files. Claude tool access is still
    controlled separately by MCP configuration and Claude allowed tools.
    """

    normalized = import_skillset(skill_ids)
    workspace = Path(workspace_path)
    skills_root = workspace / ".claude" / "skills"
    written: list[str] = []
    materialized: list[str] = []
    allowed_by_skill = dict(skill_allowed_tools or {})

    for sid in normalized:
        spec = get_skill(sid)
        if not spec:
            continue
        full_id = _skill_key(spec)
        target_dir = skills_root / _slugify_skill_id(full_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        source_path = getattr(spec, "source_path", None)
        source_dir = Path(source_path).parent if source_path else None
        written.extend(_copy_skill_support_files(source_dir, target_dir, overwrite=overwrite))
        skill_path = target_dir / "SKILL.md"
        if overwrite or not skill_path.exists():
            skill_path.write_text(
                _render_claude_skill_markdown(
                    spec,
                    allowed_tools=tuple(allowed_by_skill.get(full_id) or allowed_by_skill.get(sid) or ()),
                ),
                encoding="utf-8",
            )
        written.append(str(skill_path))
        materialized.append(full_id)

    return {
        "workspace_path": str(workspace),
        "skills_root": str(skills_root),
        "materialized_skill_ids": materialized,
        "written_files": written,
    }


def prepare_claude_code_workspace(
    workspace_path: str | Path,
    config: ClaudeCodeWorkspaceConfig,
) -> dict[str, Any]:
    """Write standard Claude Code support files into a workspace.

    This helper prepares configuration files only. It does not sandbox Claude,
    resolve secrets, clone repositories, or publish session state.
    """

    workspace = Path(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    materialized_skills: list[str] = []

    mcp_config = dict(config.mcp_config or {})
    if config.mcp_servers:
        mcp_servers = dict(mcp_config.get("mcpServers") or {})
        mcp_servers.update(dict(config.mcp_servers))
        mcp_config["mcpServers"] = mcp_servers
    if mcp_config:
        path = workspace / ".mcp.json"
        _write_json(path, mcp_config, overwrite=config.overwrite)
        written.append(str(path))

    settings = dict(config.settings or {})
    enabled_servers = config.enabled_mcp_servers
    if enabled_servers is None and config.mcp_servers:
        enabled_servers = tuple(config.mcp_servers.keys())
    if enabled_servers is not None:
        settings.setdefault("enableAllProjectMcpServers", False)
        settings["enabledMcpjsonServers"] = list(enabled_servers)
    if config.allowed_tools or config.denied_tools:
        permissions = dict(settings.get("permissions") or {})
        if config.allowed_tools:
            permissions["allow"] = list(config.allowed_tools)
        if config.denied_tools:
            permissions["deny"] = list(config.denied_tools)
        settings["permissions"] = permissions
    if settings:
        path = workspace / ".claude" / "settings.local.json"
        _write_json(path, settings, overwrite=config.overwrite)
        written.append(str(path))

    if config.instructions_markdown is not None:
        path = workspace / "CLAUDE.md"
        if config.overwrite or not path.exists():
            path.write_text(config.instructions_markdown, encoding="utf-8")
        written.append(str(path))

    if config.skill_ids:
        skills_result = materialize_kdcube_skills_for_claude(
            workspace,
            tuple(config.skill_ids),
            skill_allowed_tools=_resolve_skill_allowed_tools(config),
            overwrite=config.overwrite,
        )
        written.extend(skills_result.get("written_files") or [])
        materialized_skills = list(skills_result.get("materialized_skill_ids") or [])

    return {
        "workspace_path": str(workspace),
        "written_files": written,
        "mcp_servers": list((mcp_config.get("mcpServers") or {}).keys()),
        "enabled_mcp_servers": list(enabled_servers or []),
        "materialized_skill_ids": materialized_skills,
    }

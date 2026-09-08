"""Instruction selection and composition for directly hosted agent adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.apps.chat.sdk.skills.instructions.instructions_extra_lite import (
    REACT_XLITE_PROFILE_BLOCKS,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
    REACT_LITE_PROFILE_BLOCKS,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.workspace_agent_instructions import (
    exec_capability_guide,
    prose_only_output_guide,
    workspace_agent_conduct_guards,
)
from kdcube_ai_app.apps.chat.sdk.solutions.agentic_config.instructions.refs import (
    resolve_profile_alias,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
    append_agent_admin_customization,
)


PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE = "workspace-files"


@dataclass(frozen=True)
class DirectInstructionSelection:
    """Configuration-owned base profile plus additive administrator guidance."""

    profile: str
    additional_instructions: str = ""


def configured_instruction_selection(
    config: Mapping[str, Any],
    *,
    default_profile: str,
    fallback_additional_instructions: str = "",
) -> DirectInstructionSelection:
    """Read the direct agent instruction contract from ``agent`` configuration.

    The current shape is::

        instructions:
          profile: <profile-id>
        additional_instructions: <administrator text>

    A legacy scalar ``instructions`` value remains readable and is treated as
    additive customization, so an old direct-host config cannot accidentally
    replace the SDK-owned workspace profile.
    """

    agent = config.get("agent")
    if not isinstance(agent, Mapping):
        raise ValueError("configuration section 'agent' must be a mapping")

    profile = str(default_profile or "").strip()
    if not profile:
        raise ValueError("default instruction profile is required")

    raw_instructions = agent.get("instructions")
    legacy_additional: str | None = None
    if raw_instructions is None:
        pass
    elif isinstance(raw_instructions, Mapping):
        unknown = sorted(set(raw_instructions).difference({"profile"}))
        if unknown:
            raise ValueError(
                "agent.instructions contains unsupported keys: " + ", ".join(unknown)
            )
        raw_profile = raw_instructions.get("profile")
        if raw_profile is not None and not isinstance(raw_profile, str):
            raise ValueError("agent.instructions.profile must be a string")
        profile = str(raw_profile or profile).strip()
    elif isinstance(raw_instructions, str):
        legacy_additional = raw_instructions.strip()
    else:
        raise ValueError("agent.instructions must be a mapping")

    if not profile:
        raise ValueError("agent.instructions.profile is required")

    if legacy_additional is not None and "additional_instructions" in agent:
        raise ValueError(
            "legacy scalar agent.instructions cannot be combined with "
            "agent.additional_instructions"
        )

    if legacy_additional is not None:
        additional = legacy_additional
    elif "additional_instructions" in agent:
        raw_additional = agent.get("additional_instructions")
        if raw_additional is not None and not isinstance(raw_additional, str):
            raise ValueError("agent.additional_instructions must be a string")
        additional = str(raw_additional or "").strip()
    else:
        additional = str(fallback_additional_instructions or "").strip()

    return DirectInstructionSelection(
        profile=profile,
        additional_instructions=additional,
    )


def _react_profile(profile: str) -> tuple[str, frozenset[str]]:
    configured = str(profile or "").strip().lower()
    canonical = resolve_profile_alias(configured) or configured
    if canonical == "full":
        return "full", frozenset()
    if ":" not in canonical:
        raise ValueError(f"unknown ReAct instruction profile: {profile!r}")
    tier, name = canonical.split(":", 1)
    profiles = {
        "lite": REACT_LITE_PROFILE_BLOCKS,
        "xlite": REACT_XLITE_PROFILE_BLOCKS,
    }.get(tier)
    if profiles is None or name not in profiles:
        known = ["full"]
        known.extend(f"lite:{item}" for item in sorted(REACT_LITE_PROFILE_BLOCKS))
        known.extend(f"xlite:{item}" for item in sorted(REACT_XLITE_PROFILE_BLOCKS))
        known.extend(
            ("instr:profile:full", "instr:profile:lite", "instr:profile:extra-lite")
        )
        raise ValueError(
            f"unknown ReAct instruction profile: {profile!r}; choose one of "
            + ", ".join(known)
        )
    return tier, frozenset(profiles[name])


def native_react_instruction_blocks(
    selection: DirectInstructionSelection,
    *,
    enabled_tool_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return a ReAct profile plus standard blocks for enabled capabilities."""

    tier, included = _react_profile(selection.profile)
    blocks = [selection.profile]
    if tier == "full":
        return tuple(blocks)

    ids = {str(tool_id or "").strip() for tool_id in enabled_tool_ids}
    capability_blocks = {
        "lite": {
            "exec": "REACT_LITE_EXEC_TOOL",
            "rendering": "REACT_LITE_RENDERING_TOOLS",
            "web": "REACT_LITE_WEB_TOOLS",
        },
        "xlite": {
            "exec": "REACT_XLITE_EXEC",
            "rendering": "REACT_XLITE_DOCUMENTS_RENDERING",
            "web": "REACT_XLITE_WEB",
        },
    }[tier]
    enabled = {
        "exec": "exec_tools.execute_code_python" in ids,
        "rendering": any(tool_id.startswith("rendering_tools.") for tool_id in ids),
        "web": any(tool_id.startswith("web_tools.") for tool_id in ids),
    }
    for capability in ("exec", "rendering", "web"):
        block = capability_blocks[capability]
        if enabled[capability] and block not in included:
            blocks.append(block)
    return tuple(blocks)


_DIRECT_WORKSPACE_FILES = """
[KDCUBE DIRECT AGENT HARNESS]
- You run as an agent core inside a KDCube Agent Harness conversation. The harness records model accounting, streamed communicator events, turn records, attachments, produced files, and execution evidence.
- The configured tool inventory and each tool's schema are the authority for what you can call. Never invent a tool or claim that work succeeded before its successful result is visible.
- A model response is not a file. A user-downloadable artifact exists only after a configured KDCube file-producing tool reports the created file.

[CURRENT-TURN ARTIFACT WORKSPACE]
- KDCube execution and rendering tools use safe relative paths under `files/<scope>/<name>`. The harness binds those paths to the current turn and hosts contracted outputs as conversation artifacts.
- Every turn has a separate artifact workspace. Use the exact paths returned by tools; do not assume a previous turn's local files are present merely because its conversation text or framework checkpoint is available.
- Files read or written by framework-native filesystem tools belong to that framework's process workspace. They are not user-facing KDCube artifacts unless a configured KDCube artifact tool creates or hosts them.
- Isolated generated code has no ambient network or secret access. Supply only the required materialized inputs and declare every file that must survive in the execution artifact contract.
""".strip()


_DIRECT_EXECUTION_GUIDE = """
[ISOLATED PYTHON EXECUTION]
- The execution tool evaluates the submitted Python as an asynchronous module body. Use `await` directly at module scope when calling an injected async tool; never call `asyncio.run()`, `loop.run_until_complete()`, or start another event loop.
- Execution-enabled catalog handles are injected into the program. Call one with `await agent_io_tools.tool_call(fn=<handle>, params={...}, call_reason=..., tool_id=...)`; do not import the tool module.
- Treat a nested tool failure as an execution failure. Do not catch it and continue with invented, stale, or unverified data.
- `OUTPUT_DIR` is already injected as the artifact root. Use it directly; do not replace it with a literal path or derive a different output root.
- Map every artifact contract path exactly below `Path(OUTPUT_DIR)`. For example, contract path `files/research/report.xlsx` means `Path(OUTPUT_DIR) / "files/research/report.xlsx"`; keep the leading `files/` or `git/projects/` namespace and create its parent directory.
""".strip()


def _web_research_guide(search_tool: str, fetch_tool: str | None = None) -> str:
    fetch = str(fetch_tool or "").strip()
    inspect_line = (
        f"- After search, use `{fetch}` to inspect at least one selected source page before "
        "treating its claims as research evidence."
        if fetch
        else ""
    )
    return f"""
[WEB RESEARCH - {search_tool}{f' + {fetch}' if fetch else ''}]
- Use `{search_tool}` when the task needs current public information. Preserve the returned source URLs and ground factual claims in the results you actually inspected.
{inspect_line}
- Inside isolated Python, KDCube Web Search returns its normal `{{"ok": ..., "error": ..., "ret": [...]}}` envelope. Require `ok`, consume the result rows from `ret`, and treat an empty `ret` as a failed verification.
- Search is a governed capability with configured egress policy. A denied domain or failed request is evidence to report or work around within the allowed source set, never a reason to bypass the tool.
""".strip()


def _conversation_search_guide(search_tool: str) -> str:
    return f"""
[CONVERSATION RECALL - {search_tool}]
- Use `{search_tool}` when the user refers to facts, decisions, files, or work from an earlier conversation and the needed material is not already visible.
- Use `scope="user"` for the current user's other conversations. Search with concrete words likely to occur in the stored text, then cite the returned conversation and turn references when reporting what was recovered.
- Caller identity is bound by the harness. Never ask for, invent, or pass a user, tenant, project, conversation, or agent identity as a tool argument.
""".strip()


def _rendering_guide(tool_names: Sequence[str]) -> str:
    rendered = ", ".join(f"`{name}`" for name in tool_names)
    return f"""
[DOCUMENT RENDERING - {rendered}]
- Use the configured renderer for PDF, DOCX, or PPTX deliverables instead of generating those container formats in ad hoc Python. PDF and PPTX consume their documented HTML source; DOCX consumes its documented Markdown source.
- First create the complete source as a contracted current-turn file, inspect the successful execution result, then call the renderer with that source path and a separate external output path. Do not claim delivery until the renderer reports the output file.
""".strip()


def _native_skills_guide(skill_ids: Sequence[str]) -> str:
    rendered = ", ".join(f"`{skill_id}`" for skill_id in skill_ids)
    return f"""
[SELECTED KDCUBE SKILLS]
- The harness materializes these selected skills into this agent's native skill surface: {rendered}. When the current task matches one, load and follow its `SKILL.md` before acting.
- Skill guidance explains procedure; the configured tool inventory still determines which actions are callable.
""".strip()


def compose_provider_native_instructions(
    selection: DirectInstructionSelection,
    *,
    exec_tool: str | None = None,
    rendering_tools: Sequence[str] = (),
    conversation_search_tool: str | None = None,
    web_search_tool: str | None = None,
    web_fetch_tool: str | None = None,
    skill_instructions: str = "",
    native_skill_ids: Sequence[str] = (),
) -> str:
    """Compose the framework-neutral direct-host prompt for foreign agents."""

    if selection.profile != PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE:
        raise ValueError(
            f"unknown provider-native instruction profile: {selection.profile!r}; "
            f"expected {PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE!r}"
        )
    if str(skill_instructions or "").strip() and native_skill_ids:
        raise ValueError(
            "skills must be supplied as prompt text or native skills, not both"
        )

    parts = [_DIRECT_WORKSPACE_FILES, workspace_agent_conduct_guards()]
    conversation_search = str(conversation_search_tool or "").strip()
    if conversation_search:
        parts.append(_conversation_search_guide(conversation_search))
    web = str(web_search_tool or "").strip()
    if web:
        parts.append(_web_research_guide(web, web_fetch_tool))
    executable = str(exec_tool or "").strip()
    if executable:
        parts.append(exec_capability_guide(exec_tool=executable, pull_tool=""))
        parts.append(_DIRECT_EXECUTION_GUIDE)
    elif not rendering_tools:
        parts.append(prose_only_output_guide())
    renderers = tuple(
        str(item).strip() for item in rendering_tools if str(item).strip()
    )
    if renderers:
        parts.append(_rendering_guide(renderers))
    skill_text = str(skill_instructions or "").strip()
    if skill_text:
        parts.append(skill_text)
    elif native_skill_ids:
        parts.append(_native_skills_guide(native_skill_ids))

    body = "\n\n".join(part for part in parts if part)
    return append_agent_admin_customization(
        body,
        additional_instructions=selection.additional_instructions,
    )


__all__ = [
    "DirectInstructionSelection",
    "PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE",
    "compose_provider_native_instructions",
    "configured_instruction_selection",
    "native_react_instruction_blocks",
]

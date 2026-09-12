#!/usr/bin/env python3
"""Run Claude Code through ClaudeCodeAgent as a direct SDK process."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SDK_ROOT = REPO_ROOT / "app" / "ai-app" / "src" / "kdcube-ai-app"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.infrastructure import (  # noqa: E402
    activate_platform_descriptors,
    direct_harness_config,
    platform_exec_profile,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (  # noqa: E402
    activate_configured_skills,
    configured_agent_tool_config,
    configured_agent_input,
    configured_run_directory,
    configured_tool_ids,
    configured_web_search,
    verify_docker_image,
    verify_playwright_chromium,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.instructions import (  # noqa: E402
    DirectInstructionSelection,
    PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
    compose_provider_native_instructions,
    configured_instruction_selection,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.lifecycle import (  # noqa: E402
    direct_host_process_lifespan,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.evidence import (  # noqa: E402
    ConsoleEmitter,
    print_evidence_summary,
    recorded_tool_items,
    write_evidence_index,
    xlsx_contains_tool_evidence,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.channels import (  # noqa: E402
    DirectInputAttachment,
    DirectTurnRequest,
    add_direct_input_attachments,
    completed_direct_turn_result,
    prompt_with_attachment_manifest,
    run_terminal_chat,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.model_service import (  # noqa: E402
    configured_model_selection,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.telegram import (  # noqa: E402
    configured_direct_telegram,
    serve_direct_telegram,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (  # noqa: E402
    DirectTurnWorkspace,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (  # noqa: E402
    DirectToolRuntime,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_harness import (  # noqa: E402
    DirectAgentHarness,
)
from kdcube_ai_app.apps.chat.sdk.config import get_secret  # noqa: E402
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (  # noqa: E402
    ClaudeCodeAgent,
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
    ClaudeCodeSessionStoreConfig,
    ClaudeCodeWorkspaceConfig,
    bootstrap_claude_code_session_store,
    claude_code_session_branch_ref,
    publish_claude_code_session_store,
    run_claude_code_turn,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.streaming import (  # noqa: E402
    extract_tool_uses_from_claude_event,
)


WEB_SEARCH_TOOL_ID = "mcp__kdcube_web_search__web_search"
WEB_FETCH_TOOL_ID = "mcp__kdcube_web_search__web_fetch"
CONVERSATION_SEARCH_TOOL_ID = "mcp__kdcube_harness__conversation_search"
EXEC_TOOL_ID = "mcp__kdcube_harness__execute_python"
RENDER_TOOL_IDS = (
    "mcp__kdcube_harness__write_pdf",
    "mcp__kdcube_harness__write_docx",
    "mcp__kdcube_harness__write_pptx",
)
BUILTIN_TOOL_IDS = ("Read", "Write", "Edit", "Grep", "Glob")
CANONICAL_TOOL_IDS = {
    "web_tools.web_search": WEB_SEARCH_TOOL_ID,
    "web_tools.web_fetch": WEB_FETCH_TOOL_ID,
    "conversation_tools.search": CONVERSATION_SEARCH_TOOL_ID,
    "exec_tools.execute_code_python": EXEC_TOOL_ID,
    "rendering_tools.write_pdf": RENDER_TOOL_IDS[0],
    "rendering_tools.write_docx": RENDER_TOOL_IDS[1],
    "rendering_tools.write_pptx": RENDER_TOOL_IDS[2],
}
BUNDLE_ID = "standalone-claude-demo@1-0"
AGENT_ID = "claude"


def canonical_tool_id(provider_tool_id: str) -> str:
    """Resolve a Claude-facing tool name to its canonical SDK identity."""
    matches = [
        canonical_id
        for canonical_id, mapped_id in CANONICAL_TOOL_IDS.items()
        if mapped_id == provider_tool_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Claude tool {provider_tool_id!r} does not have one canonical SDK identity"
        )
    return matches[0]


def load_config(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("configuration root must be a mapping")
    return value


def _require_claude_credential(env: dict[str, str]) -> None:
    """A git transcript store points the CLI at its own config directory.

    The credential is therefore either supplied by the descriptor or linked in
    from this machine's Claude Code login. With neither, the CLI subprocess
    reports `Not logged in` and names no cause.
    """
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_KEY"):
        return
    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return
    raise RuntimeError(
        "no Claude credential available: the git transcript store redirects the "
        "CLI config directory, so this machine's ~/.claude login is linked in "
        "only when it exists. Authenticate Claude Code here, set "
        "platform.services.anthropic.api_key in descriptors.local/secrets.yaml, "
        "or select claude_code_session.type: local."
    )


async def descriptor_claude_cli_credentials() -> dict[str, str]:
    """Project descriptor-owned Anthropic credentials into the Claude CLI."""
    api_key = str(
        await get_secret("platform.services.anthropic.api_key") or ""
    ).strip()
    claude_code_key = str(
        await get_secret("platform.services.anthropic.claude_code_key") or ""
    ).strip()
    env: dict[str, str] = {}
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if claude_code_key:
        env["CLAUDE_CODE_KEY"] = claude_code_key
    return env


async def agent_config(
    config: dict[str, Any],
    *,
    workspace: Path,
    config_path: Path,
    descriptors_dir: Path,
    run_root: Path,
    tool_events_path: Path,
    conversation_id: str,
    user_id: str,
    user_type: str,
    session_id: str,
    turn_id: str,
    bundle_id: str,
    agent_id: str,
    check_only: bool,
    skill_ids: tuple[str, ...],
    instruction_selection: DirectInstructionSelection,
    model: str,
) -> ClaudeCodeAgentConfig:
    agent = config.get("agent") or {}
    adapter = agent.get("adapter") or {}
    if not isinstance(adapter, dict):
        raise ValueError("agent.adapter must be a mapping")
    command = str(adapter.get("command") or "claude")
    if not check_only and shutil.which(command) is None:
        raise RuntimeError(f"Claude Code executable {command!r} is not on PATH")
    python_bin = Path(sys.executable).resolve().parent
    env = {
        "PATH": os.pathsep.join((str(python_bin), os.environ.get("PATH", ""))),
        "VIRTUAL_ENV": str(python_bin.parent),
    }
    env.update(await descriptor_claude_cli_credentials())
    tool_config = configured_agent_tool_config(
        config,
        agent_id=agent_id,
        bundle_root=config_path.parent,
    )
    configured_ids = set(configured_tool_ids(tool_config))
    unsupported = sorted(configured_ids.difference(CANONICAL_TOOL_IDS))
    if unsupported:
        raise ValueError(
            "Claude Code direct example has no adapter for configured tools: "
            + ", ".join(unsupported)
        )
    unsupported_runtimes = sorted(
        tool_id
        for tool_id in configured_ids
        if tool_config.tool_runtime.get(tool_id)
        != ("docker" if tool_id == "exec_tools.execute_code_python" else "local")
    )
    if unsupported_runtimes:
        raise ValueError(
            "Claude Code tool runtime does not match the sample contract: "
            + ", ".join(unsupported_runtimes)
        )
    configured_builtins = adapter.get("allowed_tools") or []
    if not isinstance(configured_builtins, list):
        raise ValueError("agent.adapter.allowed_tools must be a list")
    unknown_builtins = sorted(set(configured_builtins).difference(BUILTIN_TOOL_IDS))
    if unknown_builtins:
        raise ValueError(
            "unsupported Claude Code built-in tools: " + ", ".join(unknown_builtins)
        )
    allowed_tools = tuple(
        [str(item) for item in configured_builtins]
        + [CANONICAL_TOOL_IDS[tool_id] for tool_id in configured_tool_ids(tool_config)]
    )
    web_search_server_id = "kdcube_web_search"
    harness_server_id = "kdcube_harness"
    instructions = compose_provider_native_instructions(
        instruction_selection,
        conversation_search_tool=(
            CONVERSATION_SEARCH_TOOL_ID
            if CONVERSATION_SEARCH_TOOL_ID in allowed_tools
            else None
        ),
        exec_tool=EXEC_TOOL_ID if EXEC_TOOL_ID in allowed_tools else None,
        rendering_tools=tuple(
            tool_id for tool_id in RENDER_TOOL_IDS if tool_id in allowed_tools
        ),
        web_search_tool=(
            WEB_SEARCH_TOOL_ID if WEB_SEARCH_TOOL_ID in allowed_tools else None
        ),
        web_fetch_tool=(
            WEB_FETCH_TOOL_ID if WEB_FETCH_TOOL_ID in allowed_tools else None
        ),
        native_skill_ids=skill_ids,
    )
    mcp_servers: dict[str, dict[str, Any]] = {}
    enabled_mcp_servers: list[str] = []
    if {"web_tools.web_search", "web_tools.web_fetch"}.intersection(configured_ids):
        mcp_servers[web_search_server_id] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                str(REPO_ROOT / "mcp" / "web-search" / "server.py"),
                "--transport",
                "stdio",
                "--config",
                str(config_path),
                "--tool-id",
                "web",
            ],
            "env": {},
        }
        enabled_mcp_servers.append(web_search_server_id)
    if {
        "conversation_tools.search",
        "exec_tools.execute_code_python",
        "rendering_tools.write_pdf",
        "rendering_tools.write_docx",
        "rendering_tools.write_pptx",
    }.intersection(configured_ids):
        mcp_servers[harness_server_id] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                "-m",
                "kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_server",
                "--config",
                str(config_path),
                "--descriptors",
                str(descriptors_dir),
                "--run-root",
                str(run_root),
                "--events",
                str(tool_events_path),
                "--conversation-id",
                conversation_id,
                "--user-id",
                user_id,
                "--user-type",
                user_type,
                "--session-id",
                session_id,
                "--turn-id",
                turn_id,
                "--bundle-id",
                bundle_id,
                "--agent-id",
                agent_id,
                "--bundle-root",
                str(HERE),
                "--bundle-module",
                "agent",
            ],
            "env": {"PYTHONPATH": str(SDK_ROOT)},
        }
        enabled_mcp_servers.append(harness_server_id)

    return ClaudeCodeAgentConfig(
        agent_name=agent_id,
        workspace_path=workspace,
        model=model,
        allowed_tools=allowed_tools,
        command=command,
        env=env,
        timeout_seconds=float(adapter.get("timeout_seconds") or 900),
        permission_mode="acceptEdits",
        workspace_config=ClaudeCodeWorkspaceConfig(
            mcp_servers=mcp_servers,
            enabled_mcp_servers=tuple(enabled_mcp_servers),
            instructions_markdown=instructions,
            allowed_tools=allowed_tools,
            denied_tools=("WebSearch", "WebFetch", "Bash"),
            skill_ids=skill_ids,
            overwrite=True,
        ),
    )


def _git_repo(settings: Any, *, descriptors_dir: Path) -> str | None:
    raw = str(getattr(settings, "CLAUDE_CODE_SESSION_GIT_REPO", "") or "").strip()
    if not raw:
        return None
    if raw.startswith("git@") or urlparse(raw).scheme:
        return raw
    return str((descriptors_dir / raw).expanduser().resolve())


def session_store_config(
    settings: Any,
    *,
    descriptors_dir: Path,
    workspace: Path,
    user_id: str,
    conversation_id: str,
    agent_id: str,
) -> ClaudeCodeSessionStoreConfig:
    return ClaudeCodeSessionStoreConfig(
        implementation=str(
            getattr(settings, "CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION", "local")
            or "local"
        ),
        local_root=workspace / ".claude",
        tenant=str(getattr(settings, "TENANT", "") or "standalone"),
        project=str(getattr(settings, "PROJECT", "") or "claude-agent-demo"),
        user_id=user_id,
        conversation_id=conversation_id,
        agent_name=agent_id,
        git_repo=_git_repo(settings, descriptors_dir=descriptors_dir),
    )


def _called_tool_names(raw_output_lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in raw_output_lines:
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        names.update(
            call["name"] for call in extract_tool_uses_from_claude_event(event)
        )
    return names


async def run_one_turn(
    *,
    prompt: str,
    number: int,
    resume: bool,
    raw_config: dict[str, Any],
    config_path: Path,
    descriptors_dir: Path,
    run_root: Path,
    workspace: Path,
    skill_ids: tuple[str, ...],
    binding: ClaudeCodeBinding,
    harness: DirectAgentHarness,
    session_store: ClaudeCodeSessionStoreConfig,
    instruction_selection: DirectInstructionSelection,
    model: str,
    input_attachments: tuple[DirectInputAttachment, ...] = (),
) -> tuple[str, str, set[str], Any]:
    turn_id = f"turn_{number:02d}_{uuid.uuid4().hex[:8]}"
    turn_workspace = DirectTurnWorkspace(run_root=run_root, turn_id=turn_id)
    async with harness.turn(
        conversation_id=binding.conversation_id,
        turn_id=turn_id,
    ) as turn:
        attachments = await add_direct_input_attachments(
            turn=turn,
            workspace=turn_workspace,
            attachments=input_attachments,
            mirror_to=workspace / "attachments",
        )
        config = await agent_config(
            raw_config,
            workspace=workspace,
            config_path=config_path,
            descriptors_dir=descriptors_dir,
            run_root=run_root,
            tool_events_path=turn_workspace.turn_root / "mcp-events.jsonl",
            conversation_id=binding.conversation_id,
            user_id=binding.user_id,
            user_type=harness.config.user_type,
            session_id=binding.session_id,
            turn_id=turn_id,
            bundle_id=harness.config.bundle_id,
            agent_id=harness.config.agent_id,
            check_only=False,
            skill_ids=skill_ids,
            instruction_selection=instruction_selection,
            model=model,
        )
        agent = ClaudeCodeAgent(config=config, binding=binding, comm=turn.comm)
        await turn.comm.start(message=prompt)
        model_prompt = prompt_with_attachment_manifest(prompt, attachments)
        result = await run_claude_code_turn(
            agent=agent,
            prompt=model_prompt,
            resume_existing=resume,
            session_store=session_store,
        )
        if result.status != "completed":
            raise RuntimeError(
                result.error_message or f"Claude exited with {result.exit_code}"
            )
        # Claude reaches the harness tools through the stdio tool server, so the
        # declarations were recorded by that process into this turn's workspace.
        files = DirectToolRuntime.declared_files_for_workspace(turn_workspace)
        if files:
            await turn.host_files(files=files, outdir=turn_workspace.runtime_outdir)
        await turn.persist_workspace(
            outdir=turn_workspace.runtime_outdir,
            workdir=turn_workspace.workdir,
            execution_id=f"{turn_id}_workspace",
        )
        await turn.complete(prompt=prompt, final_answer=result.final_text)
    print(
        f"[accounting] Redis turn cache contains {len(turn.accounting_events)} event(s)"
    )
    return result.final_text, turn_id, _called_tool_names(result.raw_output_lines), turn


async def main_async(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser().resolve()
    descriptors_dir = Path(args.descriptors).expanduser().resolve()
    settings = activate_platform_descriptors(descriptors_dir)
    selected_model = configured_model_selection()
    if selected_model.provider != "anthropic":
        raise ValueError(
            "the Claude Code adapter requires models.default_llm_provider: "
            "anthropic; use Native or LangGraph for KDCube custom-model routing"
        )
    config = load_config(config_path)
    agent_input = configured_agent_input(
        config,
        user_id=args.user_id,
        conversation_id=args.conversation_id,
        session_id=args.session_id,
        recall_conversation_id=args.recall_conversation_id,
    )
    configured_web_search(config, connection_id="web")
    output = configured_run_directory(config, config_path=config_path)
    conversation_id = agent_input.conversation_id
    run_id = "check" if args.check else f"run_{uuid.uuid4().hex[:12]}"
    run_root = agent_input.run_path(output, run_id=run_id)
    workspace = run_root / "workspace"
    _skills_subsystem, skill_config = activate_configured_skills(
        config,
        config_path=config_path,
        consumers=("claude",),
    )
    instruction_selection = configured_instruction_selection(
        config,
        default_profile=PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
        fallback_additional_instructions=(
            "You are a research agent. Work only inside this workspace, preserve public-web "
            "source URLs, create the requested deliverables, and report tool failures truthfully."
        ),
    )
    harness_config = direct_harness_config(
        settings=settings,
        descriptors_dir=descriptors_dir,
        bundle_id=BUNDLE_ID,
        agent_id=AGENT_ID,
        user_id=agent_input.user_id,
        user_type=agent_input.user_type,
        session_id=agent_input.session_id,
        check_only=args.check,
    )
    private_state_key = agent_input.continuity_key(
        tenant=harness_config.tenant,
        project=harness_config.project,
        agent_id=harness_config.agent_id,
    )
    binding = ClaudeCodeBinding(
        user_id=agent_input.user_id,
        conversation_id=conversation_id,
        session_id=agent_input.session_id,
        claude_session_id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"kdcube-agent:{private_state_key}",
            )
        ),
    )
    cfg = await agent_config(
        config,
        workspace=workspace,
        config_path=config_path,
        descriptors_dir=descriptors_dir,
        run_root=run_root,
        tool_events_path=run_root / "turn_check" / "mcp-events.jsonl",
        conversation_id=conversation_id,
        user_id=agent_input.user_id,
        user_type=agent_input.user_type,
        session_id=agent_input.session_id,
        turn_id="turn_check",
        bundle_id=BUNDLE_ID,
        agent_id=AGENT_ID,
        check_only=args.check or args.infra_check,
        skill_ids=skill_config.enabled,
        instruction_selection=instruction_selection,
        model=selected_model.model,
    )
    session_store = session_store_config(
        settings,
        descriptors_dir=descriptors_dir,
        workspace=workspace,
        user_id=agent_input.user_id,
        conversation_id=conversation_id,
        agent_id=harness_config.agent_id,
    )
    exec_runtime = (
        platform_exec_profile(settings) if EXEC_TOOL_ID in cfg.allowed_tools else None
    )
    print("mode: standalone SDK process")
    print("adapter: ClaudeCodeAgent -> local Claude Code subprocess")
    print(f"model: {selected_model.provider}/{cfg.model}")
    print(f"tools: {', '.join(cfg.allowed_tools) or '(none)'}")
    print(f"instruction profile: {instruction_selection.profile}")
    print(
        "custom instructions: "
        + ("configured" if instruction_selection.additional_instructions else "(none)")
    )
    print(
        "web research: KDCube Web Search and Web Fetch MCP "
        f"({config_path}#agent.tools[id=web].settings)"
    )
    print(f"skills: {', '.join(skill_config.enabled) or '(none)'}")
    print(f"workspace: {workspace}")
    print(f"conversation storage: {harness_config.storage_uri}")
    print(f"user: {agent_input.user_id} ({agent_input.user_type})")
    print(f"session: {agent_input.session_id}")
    print(f"conversation: {agent_input.conversation_id}")
    print(f"agent state scope: {private_state_key}")
    print(
        "Claude transcript store: "
        f"{session_store.implementation} -> {session_store.git_repo or session_store.local_root}"
    )
    if session_store.implementation == "git":
        print(
            f"Claude transcript branch: {claude_code_session_branch_ref(session_store)}"
        )
        if not args.check:
            _require_claude_credential(cfg.env)
    if exec_runtime is not None and not args.check:
        image = verify_docker_image(exec_runtime)
        print(f"isolated code-execution image: {image}")
    if set(RENDER_TOOL_IDS).intersection(cfg.allowed_tools) and not args.check:
        browser = await verify_playwright_chromium()
        print(f"document renderer: Playwright {browser} ready")
    if args.check:
        emitter = ConsoleEmitter(Path(os.devnull))
        harness = DirectAgentHarness(
            config=harness_config,
            model_service=None,
            emitter=emitter,
        )
        agent = ClaudeCodeAgent(
            config=cfg,
            binding=binding,
            comm=harness.communicator(
                conversation_id=binding.conversation_id,
                turn_id="turn_check",
            ),
        )
        if not isinstance(agent, ClaudeCodeAgent):
            raise RuntimeError("ClaudeCodeAgent construction failed")
        print("check: PASS")
        return

    workspace.mkdir(parents=True, exist_ok=True)

    async def run_channel_turn(request: DirectTurnRequest):
        request_config = replace(
            harness_config,
            user_id=request.user_id,
            user_type=request.user_type,
            session_id=request.session_id,
        )
        request_root = request.agent_input.run_path(
            output,
            run_id=f"run_{uuid.uuid4().hex[:12]}",
        )
        request_workspace = request_root / "workspace"
        request_workspace.mkdir(parents=True, exist_ok=True)
        request_private_state_key = request.agent_input.continuity_key(
            tenant=request_config.tenant,
            project=request_config.project,
            agent_id=request_config.agent_id,
        )
        request_binding = ClaudeCodeBinding(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            claude_session_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"kdcube-agent:{request_private_state_key}",
                )
            ),
        )
        request_session_store = session_store_config(
            settings,
            descriptors_dir=descriptors_dir,
            workspace=request_workspace,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            agent_id=request_config.agent_id,
        )
        request_harness = DirectAgentHarness(
            config=request_config,
            model_service=None,
            emitter=ConsoleEmitter(request_root / "communicator.jsonl"),
        )
        async with request_harness:
            answer, turn_id, _called_tools, _turn = await run_one_turn(
                prompt=request.prompt,
                number=1,
                resume=True,
                raw_config=config,
                config_path=config_path,
                descriptors_dir=descriptors_dir,
                run_root=request_root,
                workspace=request_workspace,
                skill_ids=skill_config.enabled,
                binding=request_binding,
                harness=request_harness,
                session_store=request_session_store,
                instruction_selection=instruction_selection,
                model=selected_model.model,
                input_attachments=request.attachments,
            )
            return await completed_direct_turn_result(
                harness=request_harness,
                conversation_id=request.conversation_id,
                turn_id=turn_id,
                answer=answer,
                metadata={"source": request.source},
            )

    if args.interactive:
        await run_terminal_chat(agent_input=agent_input, run_turn=run_channel_turn)
        return
    if args.telegram_local:
        await serve_direct_telegram(
            config=configured_direct_telegram(config),
            run_turn=run_channel_turn,
        )
        return

    required_demo_tools = {
        WEB_SEARCH_TOOL_ID,
        WEB_FETCH_TOOL_ID,
        CONVERSATION_SEARCH_TOOL_ID,
        EXEC_TOOL_ID,
        RENDER_TOOL_IDS[0],
    }
    missing_demo_tools = sorted(required_demo_tools.difference(cfg.allowed_tools))
    if missing_demo_tools:
        raise RuntimeError(
            "the built-in demonstration requires tools: "
            + ", ".join(missing_demo_tools)
        )
    recall_conversation_id = agent_input.recall_conversation_id
    if not recall_conversation_id:
        raise ValueError(
            "agent.input.recall_conversation_id is required for the built-in recall demonstration"
        )
    if recall_conversation_id == conversation_id:
        raise ValueError(
            "agent.input.recall_conversation_id must differ from conversation_id"
        )

    emitter = ConsoleEmitter(run_root / "communicator.jsonl")
    harness = DirectAgentHarness(
        config=harness_config,
        model_service=None,
        emitter=emitter,
    )
    topic = str(
        (config.get("agent") or {}).get("topic") or "accountable agent runtimes"
    )
    request_path = workspace / "research-request.md"
    request_path.write_text(
        f"# Research request\n\nResearch topic: {topic}\n\n"
        "Produce sourced findings, an XLSX evidence table, and a polished PDF brief.\n",
        encoding="utf-8",
    )
    prompts = [
        (
            "Read the attached research-request.md, then use the kdcube_web_search web_search "
            "MCP tool with use_llm=false and fetch_content=false to find recent, concrete "
            f"information about {topic}. Use the kdcube_web_search web_fetch MCP tool to "
            "inspect at least one selected result URL. Return five sourced findings grounded "
            "in the inspected page and retain them for the next turn."
        ),
        (
            "Continue this same session and use the retained findings. Author complete Python "
            "and call the kdcube_harness execute_python MCP tool. Inside that generated Python, "
            "write an asynchronous module body: use top-level await directly, do not define a "
            "main runner, and never call asyncio.run or run_until_complete. First make this "
            "uncaught call and require its returned result before writing the workbook: "
            "call the configured Web Search handle with `await "
            "agent_io_tools.tool_call(fn=web_tools.web_search, "
            "params={'queries': 'site:python.org current stable Python release', "
            "'objective': 'Verify the stable release used in this report', 'n': 3, "
            "'use_llm': False, 'fetch_content': False}, "
            "call_reason='Verify release from generated code', "
            "tool_id='web_tools.web_search')`. The result is an envelope with `ok` and `ret`; "
            "raise if it is not ok or ret is empty, then include at least the first returned "
            "title and URL in the workbook. "
            "Do not import `web_tools`; the isolated runtime supplies the handle from the "
            "configured tool catalog. Write files below Path(OUTPUT_DIR), creating their "
            "parents. The Python must use openpyxl "
            "to create files/research/research-data.xlsx and create polished, print-ready HTML "
            "at files/research/research-brief.html. Declare the XLSX external and the HTML "
            "internal in the artifact contract. After execution succeeds, call the "
            "kdcube_harness write_pdf MCP tool with source_path "
            "files/research/research-brief.html and output_path "
            "files/research/research-brief.pdf. Do not use Bash and do not construct PDF bytes "
            "in Python. Verify and report the exact paths."
        ),
    ]
    turn_ids: list[str] = []
    called_tools_by_turn: list[set[str]] = []
    completed_turns: list[Any] = []
    recall_turn_id = ""
    recall_records: list[dict[str, Any]] = []
    async with harness:
        if args.infra_check:
            await bootstrap_claude_code_session_store(config=session_store)
            await publish_claude_code_session_store(config=session_store)
            print(
                "infrastructure: Redis, Postgres conversation tables, storage, and "
                "Claude git session store ready"
            )
            print("infrastructure check: PASS")
            return
        print("infrastructure: Redis, Postgres conversation tables, and storage ready")
        for number, prompt in enumerate(prompts, start=1):
            answer, turn_id, called_tools, turn = await run_one_turn(
                prompt=prompt,
                number=number,
                resume=number > 1,
                raw_config=config,
                config_path=config_path,
                descriptors_dir=descriptors_dir,
                run_root=run_root,
                workspace=workspace,
                skill_ids=skill_config.enabled,
                binding=binding,
                harness=harness,
                session_store=session_store,
                instruction_selection=instruction_selection,
                model=selected_model.model,
                input_attachments=(
                    (DirectInputAttachment.from_path(request_path),)
                    if number == 1
                    else ()
                ),
            )
            turn_ids.append(turn_id)
            called_tools_by_turn.append(called_tools)
            completed_turns.append(turn)
            print(f"\n[turn {number} answer]\n{answer}\n")
        required_research = {WEB_SEARCH_TOOL_ID, WEB_FETCH_TOOL_ID}
        missing_research = sorted(required_research.difference(called_tools_by_turn[0]))
        if missing_research:
            raise RuntimeError(
                "the research turn completed without required tools: "
                + ", ".join(missing_research)
            )
        recall_input = replace(
            agent_input,
            conversation_id=recall_conversation_id,
        )
        recall_private_state_key = recall_input.continuity_key(
            tenant=harness_config.tenant,
            project=harness_config.project,
            agent_id=harness_config.agent_id,
        )
        recall_binding = ClaudeCodeBinding(
            user_id=agent_input.user_id,
            conversation_id=recall_conversation_id,
            session_id=agent_input.session_id,
            claude_session_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"kdcube-agent:{recall_private_state_key}",
                )
            ),
        )
        recall_workspace = run_root / "recall-workspace"
        recall_workspace.mkdir(parents=True, exist_ok=True)
        recall_session_store = session_store_config(
            settings,
            descriptors_dir=descriptors_dir,
            workspace=recall_workspace,
            user_id=agent_input.user_id,
            conversation_id=recall_conversation_id,
            agent_id=harness_config.agent_id,
        )
        recall_answer, recall_turn_id, recall_tools, _recall_turn = await run_one_turn(
            prompt=(
                f"Call the kdcube_harness conversation_search MCP tool with scope='user' "
                f"to recover the research about {topic!r} from my other conversation. "
                "Report one recovered source URL and name the source conversation id."
            ),
            number=3,
            resume=False,
            raw_config=config,
            config_path=config_path,
            descriptors_dir=descriptors_dir,
            run_root=run_root,
            workspace=recall_workspace,
            skill_ids=skill_config.enabled,
            binding=recall_binding,
            harness=harness,
            session_store=recall_session_store,
            instruction_selection=instruction_selection,
            model=selected_model.model,
        )
        if CONVERSATION_SEARCH_TOOL_ID not in recall_tools:
            raise RuntimeError(
                "the recall turn completed without calling conversation_search"
            )
        print(f"\n[cross-conversation answer]\n{recall_answer}\n")
        records = await harness.verify_conversation(
            conversation_id=conversation_id,
            expected_turn_ids=turn_ids,
        )
        print(f"[conversation] materialized {len(records)} durable turn record(s)")
        recall_records = await harness.verify_conversation(
            conversation_id=recall_conversation_id,
            expected_turn_ids=(recall_turn_id,),
        )
        print(
            "[conversation] conversation_search recovered a different conversation "
            "for the same user"
        )
        evidence_path = run_root / "evidence.json"
        evidence = write_evidence_index(
            evidence_path,
            config=harness_config,
            conversation_id=conversation_id,
            turns=completed_turns,
            conversation_records=records,
            adapter_evidence={
                "transcript_store": session_store.implementation,
                "transcript_branch": (
                    claude_code_session_branch_ref(session_store)
                    if session_store.implementation == "git"
                    else None
                ),
                "generated_source_archive_path": f"{turn_ids[-1]}/executions/*/pkg/user_code.py",
                "cross_conversation_turn": recall_turn_id,
                "cross_conversation_records": len(recall_records),
            },
        )
        print_evidence_summary(evidence_path, evidence)

    deliverable_workspace = DirectTurnWorkspace(
        run_root=run_root,
        turn_id=turn_ids[-1],
    )
    expected_files = (
        deliverable_workspace.current_file("research/research-brief.pdf"),
        deliverable_workspace.current_file("research/research-data.xlsx"),
    )
    missing = [path.name for path in expected_files if not path.is_file()]
    if missing:
        raise RuntimeError(
            f"agent completed without required artifacts: {', '.join(missing)}"
        )
    nested_search_items = recorded_tool_items(
        deliverable_workspace.runtime_outdir,
        canonical_tool_id(WEB_SEARCH_TOOL_ID),
    )
    if not nested_search_items:
        raise RuntimeError(
            "generated Python called Web Search but received no recorded results; "
            "verify trusted-supervisor networking and Web Search credentials"
        )
    if not xlsx_contains_tool_evidence(
        deliverable_workspace.current_file("research/research-data.xlsx"),
        nested_search_items,
    ):
        raise RuntimeError(
            "research-data.xlsx does not contain a title and URL returned by the "
            "generated program's nested Web Search call"
        )
    print(
        f"[isolated execution] nested Web Search returned "
        f"{len(nested_search_items)} item(s)"
    )
    print("demonstration: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(
            HERE / "config.local.yaml"
            if (HERE / "config.local.yaml").is_file()
            else HERE / "config.template.yaml"
        ),
    )
    parser.add_argument(
        "--descriptors",
        default=str(
            HERE / "descriptors.local"
            if (HERE / "descriptors.local").is_dir()
            else HERE / "descriptors.template"
        ),
        help="Directory containing the standard platform descriptor set.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check",
        action="store_true",
        help="Construct the direct SDK path without starting Claude Code.",
    )
    modes.add_argument(
        "--infra-check",
        action="store_true",
        help="Verify independent support services without starting Claude Code.",
    )
    parser.add_argument("--user-id", help="Override agent.input.user_id.")
    parser.add_argument(
        "--conversation-id",
        help="Override agent.input.conversation_id and resume that conversation.",
    )
    parser.add_argument("--session-id", help="Override agent.input.session_id.")
    parser.add_argument(
        "--recall-conversation-id",
        help="Override agent.input.recall_conversation_id for the recall demonstration.",
    )
    modes.add_argument(
        "--interactive",
        action="store_true",
        help="Read messages from this terminal and continue the configured conversation.",
    )
    modes.add_argument(
        "--telegram-local",
        action="store_true",
        help="Run the local inline Telegram webhook configured under agent.ingress.telegram.",
    )
    try:
        args = parser.parse_args()

        async def run() -> None:
            async with direct_host_process_lifespan():
                await main_async(args)

        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

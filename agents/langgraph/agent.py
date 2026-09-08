#!/usr/bin/env python3
"""Run LangGraph create_agent through KDCubeChatModel as a direct SDK process."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver


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
    postgres_label,
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
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.telegram import (  # noqa: E402
    configured_direct_telegram,
    serve_direct_telegram,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (  # noqa: E402
    DirectToolRuntime,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (  # noqa: E402
    DirectTurnWorkspace,
)
from agents.langgraph.tools import (  # noqa: E402
    CONVERSATION_SEARCH_TOOL_ID,
    build_tools,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.model_service import (  # noqa: E402
    build_model_service,
    embedding_service_if_configured,
)
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator  # noqa: E402
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import (
    KDCubeChatModel,
)  # noqa: E402
from kdcube_ai_app.apps.chat.sdk.runtime.direct_harness import (  # noqa: E402
    DirectAgentHarness,
)
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (  # noqa: E402
    build_skills_instruction_block,
)


ROLE = "standalone.langgraph.answer"
WEB_SEARCH_TOOL_ID = "web_tools.web_search"
WEB_FETCH_TOOL_ID = "web_tools.web_fetch"
EXEC_TOOL_ID = "exec_tools.execute_code_python"
RENDER_TOOL_IDS = (
    "rendering_tools.write_pdf",
    "rendering_tools.write_docx",
    "rendering_tools.write_pptx",
)
MODEL_TOOL_NAMES = {
    WEB_SEARCH_TOOL_ID: "web_search",
    WEB_FETCH_TOOL_ID: "web_fetch",
    CONVERSATION_SEARCH_TOOL_ID: "conversation_search",
    EXEC_TOOL_ID: "execute_python",
    RENDER_TOOL_IDS[0]: "write_pdf",
    RENDER_TOOL_IDS[1]: "write_docx",
    RENDER_TOOL_IDS[2]: "write_pptx",
}


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in value
        )
    return str(value or "")


def load_config(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("configuration root must be a mapping")
    return value


def build_graph(
    model: KDCubeChatModel,
    checkpointer: Any,
    tools: list[Any],
    *,
    instructions: str,
) -> Any:
    return create_agent(
        model=model,
        tools=tools,
        checkpointer=checkpointer,
        system_prompt=instructions,
    )


@asynccontextmanager
async def open_postgres_checkpointer(settings: Any, database_url: str):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    stack = AsyncExitStack()
    try:
        checkpointer = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(database_url)
        )
        await checkpointer.setup()
    except Exception as exc:
        await stack.aclose()
        raise RuntimeError(
            f"Postgres checkpoint bootstrap failed at {postgres_label(settings)}; "
            "start the independent agent-example services and verify secrets.yaml"
        ) from exc
    try:
        yield checkpointer
    finally:
        await stack.aclose()


async def stream_turn(
    graph: Any,
    prompt: str,
    run_config: dict[str, Any],
    comm: ChatCommunicator,
) -> tuple[str, set[str]]:
    index = 0
    called_tools: set[str] = set()
    await comm.start(message=prompt)
    async for event in graph.astream_events(
        {"messages": [{"role": "user", "content": prompt}]},
        run_config,
        version="v2",
    ):
        kind = str(event.get("event") or "")
        name = str(event.get("name") or "")
        node = str((event.get("metadata") or {}).get("langgraph_node") or "")
        if kind == "on_chat_model_stream" and node == "model":
            chunk = (event.get("data") or {}).get("chunk")
            if getattr(chunk, "tool_call_chunks", None):
                continue
            text = _content_text(getattr(chunk, "content", ""))
            if text:
                await comm.delta(
                    text=text, index=index, marker="answer", agent="langgraph"
                )
                index += 1
        elif kind == "on_tool_start":
            called_tools.add(name)
            await comm.step(
                step=f"tool.{name}.{str(event.get('run_id') or '')[:8]}",
                status="running",
                title=f"Calling {name}",
                markdown=json.dumps(
                    (event.get("data") or {}).get("input") or {},
                    ensure_ascii=False,
                    default=str,
                ),
                agent="langgraph",
            )
        elif kind == "on_tool_end":
            await comm.step(
                step=f"tool.{name}.{str(event.get('run_id') or '')[:8]}",
                status="completed",
                title=f"Completed {name}",
                markdown=str((event.get("data") or {}).get("output") or ""),
                agent="langgraph",
            )
    snapshot = await graph.aget_state(run_config)
    messages = list((snapshot.values or {}).get("messages") or [])
    answer = _content_text(getattr(messages[-1], "content", "")) if messages else ""
    return answer, called_tools


async def host_demo_outputs(
    *,
    turn: Any,
    workspace: DirectTurnWorkspace,
) -> None:
    expected = (
        (
            "research/research-data.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "external",
            EXEC_TOOL_ID,
        ),
        ("research/research-brief.html", "text/html", "internal", EXEC_TOOL_ID),
        ("research/research-brief.pdf", "application/pdf", "external", "write_pdf"),
    )
    files: list[dict[str, Any]] = []
    for relpath, mime, visibility, tool_id in expected:
        path = workspace.current_file(relpath)
        if not path.is_file():
            continue
        files.append(
            {
                "type": "file",
                "output": {
                    "type": "file",
                    "path": f"{workspace.turn_id}/files/{relpath}",
                    "filename": path.name,
                    "mime": mime,
                    "visibility": visibility,
                },
                "mime": mime,
                "visibility": visibility,
                "description": f"LangGraph demo output: {path.name}",
                "resource_id": path.stem,
                "tool_id": tool_id,
            }
        )
    if files:
        await turn.host_files(files=files, outdir=workspace.runtime_outdir)


async def run_one_turn(
    *,
    prompt: str,
    number: int,
    conversation_id: str,
    run_root: Path,
    run_config: dict[str, Any],
    model: KDCubeChatModel,
    checkpointer: Any,
    instructions: str,
    tool_config: Any,
    configured_ids: set[str],
    exec_runtime: dict[str, Any] | None,
    service: Any,
    harness: DirectAgentHarness,
    input_attachments: tuple[DirectInputAttachment, ...] = (),
) -> tuple[str, str, set[str], Any]:
    turn_id = f"turn_{number:02d}_{uuid.uuid4().hex[:8]}"
    workspace = DirectTurnWorkspace(run_root=run_root, turn_id=turn_id)
    async with harness.turn(conversation_id=conversation_id, turn_id=turn_id) as turn:
        attachments = await add_direct_input_attachments(
            turn=turn,
            workspace=workspace,
            attachments=input_attachments,
        )
        runtime = DirectToolRuntime(
            service=service,
            comm=turn.comm,
            workspace=workspace,
            exec_runtime=exec_runtime,
            bundle_id=harness.config.bundle_id,
            bundle_root=HERE,
            bundle_module="agent",
            tool_config=tool_config,
            context_rag_client=turn.conversation_client,
        )
        graph = build_graph(
            model,
            checkpointer,
            build_tools(runtime, configured_ids=configured_ids),
            instructions=instructions,
        )
        model_prompt = prompt_with_attachment_manifest(prompt, attachments)
        answer, called_tools = await stream_turn(
            graph, model_prompt, run_config, turn.comm
        )
        await host_demo_outputs(turn=turn, workspace=workspace)
        await turn.persist_workspace(
            outdir=workspace.runtime_outdir,
            workdir=workspace.workdir,
            execution_id=f"{turn_id}_workspace",
        )
        await turn.complete(prompt=prompt, final_answer=answer)
    print(
        f"[accounting] Redis turn cache contains {len(turn.accounting_events)} event(s)"
    )
    return answer, turn_id, called_tools, turn


async def main_async(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser().resolve()
    descriptors_dir = Path(args.descriptors).expanduser().resolve()
    settings = activate_platform_descriptors(descriptors_dir)
    config = load_config(config_path)
    agent_input = configured_agent_input(
        config,
        user_id=args.user_id,
        conversation_id=args.conversation_id,
        session_id=args.session_id,
        recall_conversation_id=args.recall_conversation_id,
    )
    configured_web_search(config, connection_id="web")
    from kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search import web_search_server

    web_search_server.load_config(config_path, tool_id="web")
    output_dir = configured_run_directory(config, config_path=config_path)
    harness_config = direct_harness_config(
        settings=settings,
        descriptors_dir=descriptors_dir,
        bundle_id="standalone-langgraph-demo@1-0",
        agent_id="langgraph",
        user_id=agent_input.user_id,
        user_type=agent_input.user_type,
        session_id=agent_input.session_id,
        check_only=args.check,
    )
    agent_cfg = dict(config.get("agent") or {})
    tool_config = configured_agent_tool_config(
        config,
        agent_id="langgraph",
        bundle_root=HERE,
    )
    configured_ids = set(configured_tool_ids(tool_config))
    supported = {
        WEB_SEARCH_TOOL_ID,
        WEB_FETCH_TOOL_ID,
        CONVERSATION_SEARCH_TOOL_ID,
        EXEC_TOOL_ID,
        *RENDER_TOOL_IDS,
    }
    unsupported = sorted(configured_ids.difference(supported))
    if unsupported:
        raise ValueError(
            "LangGraph direct example has no adapter for configured tools: "
            + ", ".join(unsupported)
        )
    unsupported_runtimes = sorted(
        tool_id
        for tool_id in configured_ids
        if tool_config.tool_runtime.get(tool_id)
        != ("docker" if tool_id == EXEC_TOOL_ID else "local")
    )
    if unsupported_runtimes:
        raise ValueError(
            "LangGraph tool runtime does not match the sample contract: "
            + ", ".join(unsupported_runtimes)
        )
    exec_runtime = (
        platform_exec_profile(settings) if EXEC_TOOL_ID in configured_ids else None
    )
    _skills_subsystem, skill_config = activate_configured_skills(
        config,
        config_path=config_path,
        consumers=("langgraph",),
    )
    skill_block = build_skills_instruction_block(skill_config.enabled)
    instruction_selection = configured_instruction_selection(
        config,
        default_profile=PROVIDER_NATIVE_WORKSPACE_FILES_PROFILE,
        fallback_additional_instructions=(
            "You are a research agent. Use web_search for current facts, inspect selected sources "
            "with web_fetch, and preserve source URLs. "
            "Author Python for structured artifacts, execute it with execute_python, and use the "
            "document-rendering tools for polished PDF, DOCX, and PPTX deliverables."
        ),
    )
    instructions = compose_provider_native_instructions(
        instruction_selection,
        conversation_search_tool=(
            MODEL_TOOL_NAMES[CONVERSATION_SEARCH_TOOL_ID]
            if CONVERSATION_SEARCH_TOOL_ID in configured_ids
            else None
        ),
        exec_tool=(
            MODEL_TOOL_NAMES[EXEC_TOOL_ID] if EXEC_TOOL_ID in configured_ids else None
        ),
        rendering_tools=tuple(
            MODEL_TOOL_NAMES[tool_id]
            for tool_id in RENDER_TOOL_IDS
            if tool_id in configured_ids
        ),
        web_search_tool=(
            MODEL_TOOL_NAMES[WEB_SEARCH_TOOL_ID]
            if WEB_SEARCH_TOOL_ID in configured_ids
            else None
        ),
        web_fetch_tool=(
            MODEL_TOOL_NAMES[WEB_FETCH_TOOL_ID]
            if WEB_FETCH_TOOL_ID in configured_ids
            else None
        ),
        skill_instructions=skill_block,
    )
    print("mode: standalone SDK process")
    print(f"adapter: LangGraph create_agent -> KDCubeChatModel ({ROLE})")
    print(f"tools: {', '.join(sorted(configured_ids)) or '(none)'}")
    print(f"instruction profile: {instruction_selection.profile}")
    print(
        "custom instructions: "
        + ("configured" if instruction_selection.additional_instructions else "(none)")
    )
    print(
        "web research: KDCube Web Search and Web Fetch "
        f"({config_path}#agent.tools[id=web].settings)"
    )
    print(f"skills: {', '.join(skill_config.enabled) or '(none)'}")
    print(f"run directory: {output_dir}")
    print(f"conversation storage: {harness_config.storage_uri}")
    print(f"user: {agent_input.user_id} ({agent_input.user_type})")
    print(f"session: {agent_input.session_id}")
    print(f"conversation: {agent_input.conversation_id}")
    private_state_key = agent_input.continuity_key(
        tenant=harness_config.tenant,
        project=harness_config.project,
        agent_id=harness_config.agent_id,
    )
    print(f"agent state scope: {private_state_key}")
    if exec_runtime is not None and not args.check:
        image = verify_docker_image(exec_runtime)
        print(f"isolated code-execution image: {image}")
    if set(RENDER_TOOL_IDS).intersection(configured_ids) and not args.check:
        browser = await verify_playwright_chromium()
        print(f"document renderer: Playwright {browser} ready")
    if args.infra_check:
        output_dir.mkdir(parents=True, exist_ok=True)
        harness = DirectAgentHarness(
            config=harness_config,
            model_service=None,
            emitter=ConsoleEmitter(output_dir / "communicator.jsonl"),
        )
        async with harness:
            async with open_postgres_checkpointer(
                settings, harness_config.postgres_url
            ):
                print(
                    "infrastructure: Redis, Postgres conversation/checkpoint tables, and storage ready"
                )
        print("infrastructure check: PASS")
        return

    service = await build_model_service(role=ROLE, check_only=args.check)
    embedding_service = embedding_service_if_configured(service)
    selected_model = service.config.ensure_role(ROLE)
    print(f"model: {selected_model['provider']}/{selected_model['model']}")
    if selected_model["provider"] == "custom":
        print(f"model endpoint: {service.config.custom_model_endpoint}")
        print(
            "model context: "
            f"{service.config.custom_model_num_ctx or 'gateway default'}"
        )
    print(
        "conversation search: "
        + (
            "semantic + lexical + trigram"
            if embedding_service is not None
            else "lexical + trigram (embedding provider not configured)"
        )
    )
    model = KDCubeChatModel(
        models_service=service,
        role=ROLE,
        temperature=0.2,
        max_tokens=int(agent_cfg.get("max_tokens") or 12000),
    )
    if args.check:
        tools = build_tools(None, configured_ids=configured_ids)
        graph = build_graph(model, MemorySaver(), tools, instructions=instructions)
        if graph is None:
            raise RuntimeError("LangGraph construction failed")
        print("check: PASS")
        return

    async def run_channel_turn(request: DirectTurnRequest, checkpointer: Any):
        request_config = replace(
            harness_config,
            user_id=request.user_id,
            user_type=request.user_type,
            session_id=request.session_id,
        )
        request_root = request.agent_input.run_path(
            output_dir,
            run_id=f"run_{uuid.uuid4().hex[:12]}",
        )
        request_root.mkdir(parents=True, exist_ok=True)
        request_harness = DirectAgentHarness(
            config=request_config,
            model_service=embedding_service,
            emitter=ConsoleEmitter(request_root / "communicator.jsonl"),
        )
        request_run_config = {
            "configurable": {
                "thread_id": request.agent_input.continuity_key(
                    tenant=request_config.tenant,
                    project=request_config.project,
                    agent_id=request_config.agent_id,
                )
            },
            "recursion_limit": int(agent_cfg.get("recursion_limit") or 24),
        }
        async with request_harness:
            answer, turn_id, _called_tools, _turn = await run_one_turn(
                prompt=request.prompt,
                number=1,
                conversation_id=request.conversation_id,
                run_root=request_root,
                run_config=request_run_config,
                model=model,
                checkpointer=checkpointer,
                instructions=instructions,
                tool_config=tool_config,
                configured_ids=configured_ids,
                exec_runtime=exec_runtime,
                service=service,
                harness=request_harness,
                input_attachments=request.attachments,
            )
            return await completed_direct_turn_result(
                harness=request_harness,
                conversation_id=request.conversation_id,
                turn_id=turn_id,
                answer=answer,
                metadata={"source": request.source},
            )

    if args.interactive or args.telegram_local:
        async with open_postgres_checkpointer(
            settings, harness_config.postgres_url
        ) as checkpointer:

            async def channel_callback(request: DirectTurnRequest):
                return await run_channel_turn(request, checkpointer)

            if args.interactive:
                await run_terminal_chat(
                    agent_input=agent_input,
                    run_turn=channel_callback,
                )
            else:
                await serve_direct_telegram(
                    config=configured_direct_telegram(config),
                    run_turn=channel_callback,
                )
        return

    required_demo_tools = {
        WEB_SEARCH_TOOL_ID,
        WEB_FETCH_TOOL_ID,
        CONVERSATION_SEARCH_TOOL_ID,
        EXEC_TOOL_ID,
        RENDER_TOOL_IDS[0],
    }
    missing_demo_tools = sorted(required_demo_tools.difference(configured_ids))
    if missing_demo_tools:
        raise RuntimeError(
            "the built-in demonstration requires tools: "
            + ", ".join(missing_demo_tools)
        )

    conversation_id = agent_input.conversation_id
    recall_conversation_id = agent_input.recall_conversation_id
    if not recall_conversation_id:
        raise ValueError(
            "agent.input.recall_conversation_id is required for the built-in recall demonstration"
        )
    if recall_conversation_id == conversation_id:
        raise ValueError(
            "agent.input.recall_conversation_id must differ from conversation_id"
        )
    run_root = agent_input.run_path(
        output_dir,
        run_id=f"run_{uuid.uuid4().hex[:12]}",
    )
    run_root.mkdir(parents=True, exist_ok=True)
    emitter = ConsoleEmitter(run_root / "communicator.jsonl")
    harness = DirectAgentHarness(
        config=harness_config,
        model_service=embedding_service,
        emitter=emitter,
    )
    print(f"run output: {run_root}")
    run_config = {
        "configurable": {"thread_id": private_state_key},
        "recursion_limit": int(agent_cfg.get("recursion_limit") or 24),
    }
    topic = str(agent_cfg.get("topic") or "accountable agent runtimes")
    request_path = run_root / "inputs" / "research-request.md"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        f"# Research request\n\nResearch topic: {topic}\n\n"
        "Produce sourced findings, an XLSX evidence table, and a polished PDF brief.\n",
        encoding="utf-8",
    )
    prompts = [
        (
            f"The attached research request asks about {topic}. Call web_search for recent, "
            "concrete information; it returns snippets and URLs only. Then issue a separate "
            "web_fetch call on at least one selected result URL before completing the turn. "
            "Return five sourced findings grounded in the inspected page and retain them for "
            "the next turn."
        ),
        (
            "Use the findings from the previous turn. Author complete Python and call "
            "execute_python. Inside that generated Python, call the configured Web Search "
            "handle with `await agent_io_tools.tool_call(fn=web_tools.web_search, "
            "params={'queries': 'site:python.org current stable Python release', "
            "'objective': 'Verify the stable release used in this report', 'n': 3}, "
            "call_reason='Verify release from generated code', "
            "tool_id='web_tools.web_search')`. The result is an envelope with `ok` and `ret`; "
            "raise if it is not ok or ret is empty, then include at least the first returned "
            "title and URL in the workbook. "
            "Do not import `web_tools`; the isolated runtime supplies the handle from the "
            "configured tool catalog. `OUTPUT_DIR` is already injected: do not assign it "
            "and do not read it from os.environ. Create the output directory exactly with "
            "`research_dir = Path(OUTPUT_DIR) / 'files/research'`. Every artifacts item must "
            "use exactly `filepath`, `description`, and `visibility` keys; visibility is "
            "`external` for the XLSX and `internal` for the HTML. The Python must use "
            "openpyxl to create "
            "files/research/research-data.xlsx and create polished, print-ready HTML at "
            "files/research/research-brief.html. Declare the XLSX external and the HTML "
            "internal in the artifact contract. After execution succeeds, call write_pdf "
            "with source_path files/research/research-brief.html and output_path "
            "files/research/research-brief.pdf. Do not construct PDF bytes in Python. "
            "Verify and report the exact paths."
        ),
    ]
    turn_ids: list[str] = []
    called_tools_by_turn: list[set[str]] = []
    completed_turns: list[Any] = []
    recall_turn_id = ""
    recall_records: list[dict[str, Any]] = []
    async with harness:
        async with open_postgres_checkpointer(
            settings, harness_config.postgres_url
        ) as checkpointer:
            print(
                "infrastructure: Redis, Postgres conversation/checkpoint tables, and storage ready"
            )
            for number, prompt in enumerate(prompts, start=1):
                answer, turn_id, called_tools, turn = await run_one_turn(
                    prompt=prompt,
                    number=number,
                    conversation_id=conversation_id,
                    run_root=run_root,
                    run_config=run_config,
                    model=model,
                    checkpointer=checkpointer,
                    instructions=instructions,
                    tool_config=tool_config,
                    configured_ids=configured_ids,
                    exec_runtime=exec_runtime,
                    service=service,
                    harness=harness,
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
            required_research = {
                MODEL_TOOL_NAMES[WEB_SEARCH_TOOL_ID],
                MODEL_TOOL_NAMES[WEB_FETCH_TOOL_ID],
            }
            missing_research = sorted(
                required_research.difference(called_tools_by_turn[0])
            )
            if missing_research:
                raise RuntimeError(
                    "the research turn completed without required tools: "
                    + ", ".join(missing_research)
                )
            recall_input = replace(
                agent_input,
                conversation_id=recall_conversation_id,
            )
            recall_run_config = {
                "configurable": {
                    "thread_id": recall_input.continuity_key(
                        tenant=harness_config.tenant,
                        project=harness_config.project,
                        agent_id=harness_config.agent_id,
                    )
                },
                "recursion_limit": int(agent_cfg.get("recursion_limit") or 24),
            }
            recall_answer, recall_turn_id, recall_tools, _recall_turn = (
                await run_one_turn(
                    prompt=(
                        f"Call conversation_search with scope='user' to recover the research "
                        f"about {topic!r} from my other conversation. Report one recovered "
                        "source URL and name the source conversation id."
                    ),
                    number=3,
                    conversation_id=recall_conversation_id,
                    run_root=run_root,
                    run_config=recall_run_config,
                    model=model,
                    checkpointer=checkpointer,
                    instructions=instructions,
                    tool_config=tool_config,
                    configured_ids=configured_ids,
                    exec_runtime=exec_runtime,
                    service=service,
                    harness=harness,
                )
            )
            if MODEL_TOOL_NAMES[CONVERSATION_SEARCH_TOOL_ID] not in recall_tools:
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
                "checkpoint": "Postgres LangGraph thread",
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
    missing = [
        name
        for name in ("research-brief.pdf", "research-data.xlsx")
        if not deliverable_workspace.current_file(f"research/{name}").is_file()
    ]
    if missing:
        raise RuntimeError(
            f"agent completed without required artifacts: {', '.join(missing)}"
        )
    nested_search_items = recorded_tool_items(
        deliverable_workspace.runtime_outdir, WEB_SEARCH_TOOL_ID
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
        help="Construct the direct SDK path without calling a provider.",
    )
    modes.add_argument(
        "--infra-check",
        action="store_true",
        help="Verify Redis and bootstrap Postgres checkpoint tables without calling a provider.",
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

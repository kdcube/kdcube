
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/codegen/codegen.py

from __future__ import annotations

import json
import pathlib
import textwrap
from typing import Any, Dict, List, Optional, Type, Callable, Awaitable

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, TurnPhaseError, BaseTurnView
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import reconcile_citations_for_context, \
    ContextBrowser, ContextBundle
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import build_turn_session_journal
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval import transform_codegen_to_turnid
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.util import _today_str, strip_lone_surrogates
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger

from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import (mk_thinking_streamer, emit_event, collect_outputs,
                                                                get_exec_workspace_root)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SolutionPlan, SERVICE_LOG_SLOT, _service_log_contract_entry
from kdcube_ai_app.apps.chat.sdk.runtime.solution.protocol import (
    UnifiedCoordinatorOut,
    compose_objective,
    format_turn_decision_line,
)

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import (
    _InProcessRuntime,
    build_current_tool_imports,
    build_packages_installed_block,
    _merge_timeout_result,
)

class CodegenRunner:
    """
    Extracted codegen runtime:
      - streams codegen
      - materializes files
      - runs main.py (or emits synthetic result on failure)
      - collects outputs
      - supports optional chaining via next_codegen.json

    Returns: {"rounds": [...], "outdir": str, "workdir": str, "run_id": str}
    """

    AGENT_NAME = "solver.codegen"

    @staticmethod
    def _should_wrap_snippet(src: str) -> bool:
        s = (src or "")
        return not any(k in s for k in ("async def _main", "async def main", "if __name__"))

    @staticmethod
    def _wrap_snippet(src: str) -> str:
        snippet = textwrap.indent(src or "pass", "        ")
        return "\n".join([
            "import asyncio",
            "import traceback",
            "import sys",
            "",
            "async def _main():",
            "    try:",
            snippet,
            "    except Exception as e:",
            "        tb = traceback.format_exc()",
            "        try:",
            "            await fail(\"Unhandled error\", where=\"main\", error=f\"{type(e).__name__}: {e}\", details=tb, managed=False)",
            "        except Exception:",
            "            pass",
            "        print(tb, file=sys.stderr)",
            "        raise",
            "",
            "if __name__ == '__main__':",
            "    asyncio.run(_main())",
            "",
        ])

    def __init__(
            self,
            *,
            service: ModelServiceBase,
            comm: ChatCommunicator,
            comm_context: ChatTaskPayload,
            logger: AgentLogger,
            tool_subsystem,                # ToolSubsystem instance
            scratchpad: TurnScratchpad,
            runtime: Optional[_InProcessRuntime] = None,
            turn_view_class: Type[BaseTurnView] = BaseTurnView,
    ):
        self.svc = service
        self.comm = comm
        self.comm_context = comm_context
        self.log = logger or AgentLogger(self.AGENT_NAME)
        self.tools = tool_subsystem
        self.runtime = runtime or _InProcessRuntime(self.log)
        self.scratchpad = scratchpad
        self.tool_manager = tool_subsystem
        self.turn_view_class = turn_view_class

        # ---- public entrypoint --------------------------------------------------

    async def run(
            self,
            *,
            coordinator_out: UnifiedCoordinatorOut,
            allowed_plugins: List[str],
            materialize_turn_ids: List[str],
            runtime_ctx: Dict[str, Any],
            solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:

        import uuid as _uuid
        import tempfile as _tempfile

        # ---------- working dirs ----------
        # Use smart workspace root (isolation-aware)
        root = get_exec_workspace_root()
        tmp = pathlib.Path(_tempfile.mkdtemp(prefix="codegen_", dir=str(root)))
        # tmp = pathlib.Path(_tempfile.mkdtemp(prefix="codegen_"))
        workdir, outdir = tmp / "pkg", tmp / "out"
        workdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        run_id = f"cg-{_uuid.uuid4().hex[:8]}"
        self.log.log(f"[{self.AGENT_NAME}] workdir={workdir}", level="INFO")

        # ---------- derive plan/solvability/policy from coordinator ----------
        plan: SolutionPlan = coordinator_out.to_plan()
        user_msg = self.scratchpad.user_text
        decision = getattr(coordinator_out, "decision", None)
        if hasattr(decision, "model_dump"):
            solvability: Dict[str, Any] = decision.model_dump()
        else:
            solvability = decision or {}

        # ---------- adapters (selected + infra) ----------
        # chosen_ids = [t.id for t in (plan.tools or [])]
        adapters = self.tool_manager.adapters_for_codegen(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,  # ← None means "all tools" (subject to allowed_plugins)
            denied_ids=["codegen_tools.codegen_python", "exec_tools.execute_code_python", "ctx_tools.fetch_ctx"],  # exclude codegen tool itself and the new fetch ctx tool
        )

        # ---------- context materialization (program history) ----------
        user_id = runtime_ctx.get("user_id")
        conversation_id = runtime_ctx.get("conversation_id")

        browser = ContextBrowser(tool_manager=self.tool_manager,
                                 logger=self.log,
                                 turn_view_class=self.turn_view_class)
        bundle = await browser.materialize(
            materialize_turn_ids=materialize_turn_ids or [],
            user_id=user_id,
            conversation_id=conversation_id,
        )
        program_history: List[dict] = bundle.program_history or []

        # ---------- current turn / user text ----------
        try:
            user_text = (
                getattr(self.scratchpad, "user_text", None)
                or getattr(self.scratchpad, "user_message", None)
                or ""
            )
        except Exception:
            user_text = ""
        current_turn = {"user": {"prompt": user_text}, "ts": getattr(self.scratchpad, "started_at", None)}
        if current_turn:
            program_history.insert(0, {"current_turn": current_turn})

        # ---------- light defaults (not passed in this signature) ----------
        topics: List[str] = []
        policy_summary: str = ""
        prefs_hint: Dict[str, Any] = {}
        constraints: Dict[str, Any] = {
            "prefer_direct_tools_exec": True,
            "minimize_logic": True,
            "concise": True,
            "line_budget": 80,
        }
        timeout_s = 600  # keep generous like React path

        # ---------- history reconciliation / turn session journal ----------
        program_history_reconciled = program_history
        if program_history:
            import copy
            program_history_reconciled = copy.deepcopy(program_history)
            rec = reconcile_citations_for_context(
                program_history_reconciled, max_sources=60, rewrite_tokens_in_place=True
            )
            sources_pool = rec.get("sources_pool", [])
            self.log.log(f"[{self.AGENT_NAME}] sources pool: {len(sources_pool)}")

        # program_playbook = build_program_playbook_codegen(program_history_reconciled, max_turns=5)
        react_context = ReactContext(history_turns=program_history_reconciled,
                                     scratchpad=self.scratchpad)
        coordinator_turn_line = format_turn_decision_line(coordinator_out)
        program_playbook = build_turn_session_journal(
            context=react_context,
            output_contract=plan.output_contract,
            max_prior_turns=5,
            turn_view_class=self.turn_view_class,
            coordinator_turn_line=coordinator_turn_line,
            is_codegen_agent=True
        )

        self.log.log(f"[{self.AGENT_NAME}] turn_session_journal: {json.dumps(program_playbook or {}, indent=2, ensure_ascii=False)}")

        # ---------- tool imports / packages ----------
        alias_to_dyn, _alias_to_file = self.tools.get_alias_maps()
        current_tool_imports = build_current_tool_imports(alias_to_dyn)
        code_packages = build_packages_installed_block()

        # ---------- loop (single or chained) ----------
        rounds: List[Dict[str, Any]] = []
        remaining = 1  # default single pass;
        current_task_spec = {
            # "objective": user_text,
            "constraints": constraints,
            "tools_selected": [a["id"] for a in adapters],
            "notes": []
        }
        if prefs_hint:
            current_task_spec["prefs_hint"] = prefs_hint

        track_id = runtime_ctx.get("track_id")
        bundle_id = runtime_ctx.get("bundle_id")

        output_contract = {k: v.model_dump() for k, v in plan.output_contract.items()}
        async with with_accounting(
            bundle_id,
            track_id=track_id,
            agent=self.AGENT_NAME,
            metadata={"track_id": track_id, "agent": self.AGENT_NAME},
        ):
            with self.scratchpad.phase("solver.codegen", agent="solver.codegen"):
                cg_stream = await solution_gen_stream(
                    self.svc,
                    task=current_task_spec,
                    adapters=adapters,
                    program_playbook=program_playbook,
                    output_contract=output_contract,
                    on_thinking_delta=mk_thinking_streamer(comm=self.comm,
                                                           author=f"{self.AGENT_NAME}.solver.codegen"),
                    ctx=self.AGENT_NAME,
                    current_tool_imports=current_tool_imports,
                    code_packages=code_packages,
                    timezone=self.comm_context.user.timezone,
                )
                if cg_stream and (cg_stream.get("log") or {}).get("error"):
                    err = cg_stream["log"]["error"]
                    msg = (
                              err.get("message") if isinstance(err, dict)
                              else str(err)
                          ) or "Codegen failed"
                    raise TurnPhaseError(
                        msg,
                        code="solver.codegen.error",
                        data={"solver.codegen.raw": cg_stream},
                    )

        # ensure hidden service slot exists in plan post-submit
        try:
            if plan is not None and plan.output_contract is None:
                plan.output_contract = {}
            if plan is not None and SERVICE_LOG_SLOT not in plan.output_contract:
                plan.output_contract[SERVICE_LOG_SLOT] = _service_log_contract_entry()
            self.log.log(f"[{self.AGENT_NAME}] injected service slot into plan.output_contract", level="INFO")
        except Exception:
            pass

        contract_out = solvability.get("output_contract") if isinstance(solvability, dict) else {}
        logging_helpers.log_agent_packet(self.AGENT_NAME, "codegen", cg_stream)
        cg = (cg_stream or {}).get("agent_response") or {}
        self.scratchpad.register_agentic_response("solver.codegen", cg)
        internal_thinking = (cg_stream or {}).get("internal_thinking") or ""
        elog = cg_stream.get("log") or {}
        error_text = (elog.get("error") or "").strip()

        await emit_event(
            comm=self.comm,
            etype="solver.codegen",
            title=f"Solver Codegen",
            step="plan",
            data=cg if cg else {"error": error_text or "no agent_response"},
            agent=f"solver.codegen",
        )

        files = cg.get("files") or []
        entrypoint = cg.get("entrypoint") or "python main.py"
        result_interpretation_instruction = cg.get("result_interpretation_instruction") or ""
        outputs = cg.get("outputs") or [{"filename": "result.json", "kind": "json", "key": "solver_output"}]
        notes = cg.get("notes") or ""
        current_task_spec["notes"].append(notes)

        # materialize files
        files_map = {f["path"]: f["content"] for f in files if f.get("path") and f.get("content") is not None}
        for rel, content in files_map.items():
            if rel == "main.py" and self._should_wrap_snippet(content):
                content = self._wrap_snippet(content)
            p = workdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        # quick fail for non-runnable generations
        missing_files_reason = None
        if not files_map:
            missing_files_reason = "codegen_no_files"
        elif "main.py" not in files_map:
            missing_files_reason = "codegen_no_main_py"

        if missing_files_reason:
            self.log.log(f"[{self.AGENT_NAME}] {missing_files_reason}; skipping runtime execution", level="ERROR")
            try:
                self._write_codegen_failure_result(
                    outdir / "result.json",
                    reason=missing_files_reason,
                    details={
                        "entrypoint": entrypoint,
                        "received_files": sorted(list(files_map.keys())),
                        "notes": notes,
                    },
                    contract_out=contract_out,
                )
            except Exception:
                pass

            run_res = {"status": "skipped", "error": missing_files_reason, "workdir": str(workdir), "outdir": str(outdir)}
            collected = collect_outputs(output_dir=outdir, outputs=outputs)
            rounds.append(
                self._mk_round_record(
                    entrypoint=entrypoint,
                    files_map=files_map,
                    run_res=run_res,
                    notes=current_task_spec["notes"],
                    collected=collected,
                    internal_thinking=internal_thinking,
                    rii=result_interpretation_instruction,
                    constraints=constraints,
                    user_text=user_text,
                    topics=topics,
                    policy_summary=policy_summary,
                    workdir=workdir,
                    outdir=outdir,
                    run_id=run_id,
                    main_src=files_map.get("main.py"),
                )
            )

        # Rehost prior files referenced in history using browser utility
        await browser.rehost_previous_files(
            bundle=bundle,
            workdir=workdir,  # Codegen uses workdir (where code runs)
            ctx="codegen"
        )
        await browser.rehost_previous_files(
            bundle=bundle,
            workdir=outdir,  # Codegen uses workdir (where code runs)
            ctx="codegen"
        )

        # transform exec-id → turn-id for runtime context
        program_history_for_runtime = transform_codegen_to_turnid(program_history)

        # runtime inputs for child process
        context_blob = {
            "program_history": program_history_for_runtime or [],
            "program_playbook": program_playbook,
            "topics": topics,
            "prefs_hint": prefs_hint or {},
            "policy_summary": policy_summary,
            "today": _today_str(),
            "notes": notes,
            "run_id": run_id,
            "result_interpretation_instruction": result_interpretation_instruction,
            "internal_thinking": internal_thinking,
            "current_turn": current_turn if current_turn else {},
            "current_user": (current_turn or {}).get("user"),
        }
        self.log.log(f"[{self.AGENT_NAME}]\nworkdir={workdir} \noutdir={outdir}", level="INFO")
        run_res = await self._execute_program(
            workdir=workdir,
            output_dir=outdir,
            timeout_s=timeout_s,
            current_task_spec=current_task_spec,
            context_blob=context_blob,
            contract_out=contract_out,
            adapters=adapters
        )

        # merge streamed deltas from child
        try:
            self.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
        except Exception:
            pass

        if run_res.get("error") == "timeout":
            try:
                _merge_timeout_result(outdir / "result.json", objective=user_text, seconds=timeout_s)
            except Exception:
                pass

        collected = collect_outputs(output_dir=outdir, outputs=outputs)
        rounds.append(
            self._mk_round_record(
                entrypoint=entrypoint,
                files_map=files_map,
                run_res=run_res,
                notes=current_task_spec["notes"],
                collected=collected,
                internal_thinking=internal_thinking,
                rii=result_interpretation_instruction,
                constraints=constraints,
                user_text=user_text,
                topics=topics,
                policy_summary=policy_summary,
                workdir=workdir,
                outdir=outdir,
                run_id=run_id,
                main_src=files_map.get("main.py"),
            )
        )
        remaining -= 1

        return {"rounds": rounds, "outdir": str(outdir), "workdir": str(workdir), "run_id": run_id}

    async def run_as_a_tool(
            self,
            *,
            program_playbook: str, # sent from above
            output_contract: dict, # sent from above. contain the definition of the artifacts to produce. Similar to slots concept.
            allowed_plugins: List[str],
            result_filename: str,
            bundle: ContextBundle,
            instruction: str,
            outdir: pathlib.Path,
            workdir: pathlib.Path,
            solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]],
            exec_id: Optional[str] = None,
            invocation_idx: Optional[int] = None,
    ) -> Dict[str, Any]:
        import uuid as _uuid
        import tempfile as _tempfile

        # ---------- working dirs ----------
        # Use smart workspace root (isolation-aware)
        root = get_exec_workspace_root()
        if not outdir:
            tmp = pathlib.Path(_tempfile.mkdtemp(prefix="codegen_", dir=str(root)))
            # tmp = pathlib.Path(_tempfile.mkdtemp(prefix="codegen_"))
            workdir, outdir = tmp / "pkg", tmp / "out"
            workdir.mkdir(parents=True, exist_ok=True)
            outdir.mkdir(parents=True, exist_ok=True)
        run_id = f"cg-{_uuid.uuid4().hex[:8]}"
        self.log.log(f"[{self.AGENT_NAME}] workdir={workdir}", level="INFO")

        # ---------- adapters (infra). Not including codegen tool  ----------
        adapters = self.tool_manager.adapters_for_codegen(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,  # ← None means "all tools" (subject to allowed_plugins)
            denied_ids=["codegen_tools.codegen_python", "exec_tools.execute_code_python", "ctx_tools.fetch_turn_artifacts"],  # exclude codegen tool itself and the old fetch ctx tool
        )
        common_ids = {
            a.get("id") for a in adapters
            if a.get("id") not in tools_insights.INFRA_TOOL_IDS
        }
        special_adapters = [a for a in adapters if a.get("id") not in common_ids]
        # ---------- light defaults (not passed in this signature) ----------
        topics: List[str] = []
        policy_summary: str = ""
        prefs_hint: Dict[str, Any] = {}
        constraints: Dict[str, Any] = {
            "prefer_direct_tools_exec": True,
            "minimize_logic": True,
            "concise": True,
            "line_budget": 80,
        }
        timeout_s = 600  # keep generous like React path

        # ---------- history reconciliation / turn session journal ----------
        self.log.log(f"[{self.AGENT_NAME}] turn_session_journal: {json.dumps(program_playbook or {}, indent=2, ensure_ascii=False)}")

        # ---------- tool imports / packages ----------
        alias_to_dyn, _alias_to_file = self.tools.get_alias_maps()
        current_tool_imports = build_current_tool_imports(alias_to_dyn)
        code_packages = build_packages_installed_block()

        # ---------- loop (single or chained) ----------
        rounds: List[Dict[str, Any]] = []
        remaining = 1  # default single pass;
        current_task_spec = {
            # "objective": user_text,
            "constraints": constraints,
            # "tools_selected": [a["id"] for a in special_adapters],
            "notes": []
        }
        if prefs_hint:
            current_task_spec["prefs_hint"] = prefs_hint

        track_id = bundle.track_id
        bundle_id = bundle.bundle_id

        async with with_accounting(
                bundle_id,
                track_id=track_id,
                agent=self.AGENT_NAME,
                metadata={"track_id": track_id, "agent": self.AGENT_NAME},
        ):
            with self.scratchpad.phase("solver.codegen", agent="solver.codegen"):
                author = f"{self.AGENT_NAME}.solver.codegen"
                if invocation_idx is not None:
                    author = f"{author}.{invocation_idx}"
                cg_stream = await solution_gen_stream(
                    self.svc,
                    timezone=self.comm_context.user.timezone,
                    task=current_task_spec,
                    adapters=special_adapters,
                    output_contract=output_contract,
                    program_playbook=program_playbook,
                    instruction=instruction,
                    on_thinking_delta=mk_thinking_streamer(comm=self.comm,
                                                           author=author),
                    ctx=self.AGENT_NAME,
                    code_packages=code_packages,
                    max_tokens=7000

                )
                if cg_stream and (cg_stream.get("log") or {}).get("error"):
                    err = cg_stream["log"]["error"]
                    msg = (
                              err.get("message") if isinstance(err, dict)
                              else str(err)
                          ) or "Codegen failed"
                    raise TurnPhaseError(
                        msg,
                        code="solver.codegen.error",
                        data={"solver.codegen.raw": cg_stream},
                    )

        # ensure hidden service slot exists in plan post-submit
        output_contract[SERVICE_LOG_SLOT] = _service_log_contract_entry()
        logging_helpers.log_agent_packet(self.AGENT_NAME, "codegen", cg_stream)
        cg = (cg_stream or {}).get("agent_response") or {}
        self.scratchpad.register_agentic_response("solver.codegen", cg)
        internal_thinking = (cg_stream or {}).get("internal_thinking") or ""
        elog = cg_stream.get("log") or {}
        error_text = (elog.get("error") or "").strip()

        await emit_event(
            comm=self.comm,
            etype="solver.codegen",
            title=f"Solver Codegen",
            step="plan",
            data=cg if cg else {"error": error_text or "no agent_response"},
            agent=f"solver.codegen",
        )

        files = cg.get("files") or []
        entrypoint = cg.get("entrypoint") or "python main.py"
        result_interpretation_instruction = cg.get("result_interpretation_instruction") or ""
        outputs = cg.get("outputs") or [{"filename": result_filename, "kind": "json", "key": "solver_output"}]
        notes = cg.get("notes") or ""
        current_task_spec["notes"].append(notes)

        # materialize files
        files_map = {f["path"]: f["content"] for f in files if f.get("path") and f.get("content") is not None}
        for rel, content in files_map.items():
            if rel == "main.py" and self._should_wrap_snippet(content):
                content = self._wrap_snippet(content)
            p = workdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        # quick fail for non-runnable generations
        missing_files_reason = None
        if not files_map:
            missing_files_reason = "codegen_no_files"
        elif "main.py" not in files_map:
            missing_files_reason = "codegen_no_main_py"

        if missing_files_reason:
            self.log.log(f"[{self.AGENT_NAME}] {missing_files_reason}; skipping runtime execution", level="ERROR")
            try:
                self._write_codegen_failure_result(
                    outdir / result_filename,
                    reason=missing_files_reason,
                    details={
                        "entrypoint": entrypoint,
                        "received_files": sorted(list(files_map.keys())),
                        "notes": notes,
                    },
                    contract_out=output_contract,
                    )
            except Exception:
                pass

            run_res = {"status": "skipped", "error": missing_files_reason, "workdir": str(workdir), "outdir": str(outdir)}
            collected = collect_outputs(output_dir=outdir, outputs=outputs)
            rounds.append(
                self._mk_round_record(
                    entrypoint=entrypoint,
                    files_map=files_map,
                    run_res=run_res,
                    notes=current_task_spec["notes"],
                    collected=collected,
                    internal_thinking=internal_thinking,
                    rii=result_interpretation_instruction,
                    constraints=constraints,
                    user_text="",
                    topics=topics,
                    policy_summary=policy_summary,
                    workdir=workdir,
                    outdir=outdir,
                    run_id=run_id,
                    main_src=files_map.get("main.py"),
                )
            )
        # Do not write context because it is already written by react
        self.log.log(f"[{self.AGENT_NAME}]\nworkdir={workdir} \noutdir={outdir}", level="INFO")
        run_res = await self._execute_program(
            workdir=workdir,
            output_dir=outdir,
            timeout_s=timeout_s,
            current_task_spec=current_task_spec,
            contract_out=output_contract,
            adapters=adapters,
            result_filename=result_filename,
            extra_env={
                "EXECUTION_MODE": "TOOL",
                "RESULT_FILENAME": result_filename,
                **({"EXECUTION_ID": exec_id} if exec_id else {}),
            }
        )

        # merge streamed deltas from child
        try:
            self.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
        except Exception:
            pass

        if run_res.get("error") == "timeout":
            try:
                _merge_timeout_result(outdir / result_filename, objective="", seconds=timeout_s)
            except Exception:
                pass

        collected = collect_outputs(output_dir=outdir, outputs=outputs)
        rounds.append(
            self._mk_round_record(
                entrypoint=entrypoint,
                files_map=files_map,
                run_res=run_res,
                notes=current_task_spec["notes"],
                collected=collected,
                internal_thinking=internal_thinking,
                rii=result_interpretation_instruction,
                constraints=constraints,
                user_text="",
                topics=topics,
                policy_summary=policy_summary,
                workdir=workdir,
                outdir=outdir,
                run_id=run_id,
                main_src=files_map.get("main.py"),
            )
        )
        remaining -= 1

        return {"rounds": rounds, "outdir": str(outdir), "workdir": str(workdir), "run_id": run_id}

    # ---- helpers ------------------------------------------------------------

    def _write_runtime_inputs(self, *, output_dir: pathlib.Path, context: Optional[Dict[str, Any]] = None, task: Optional[Dict[str, Any]] = None) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        if context:
            (output_dir / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        if task:
            (output_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _execute_program(
            self,
            *,
            workdir: pathlib.Path,
            output_dir: pathlib.Path,
            contract_out: Dict[str, Any],
            adapters: List[Dict[str, Any]],
            current_task_spec: Optional[Dict[str, Any]] = None,
            context_blob: Optional[Dict[str, Any]] = None,
            timeout_s: int = 90,
            result_filename: Optional[str] = "result.json",
            extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:

        runtime_globals = self.tool_manager.export_runtime_globals()

        self._write_runtime_inputs(
            output_dir=output_dir,
            context=context_blob,
            task={
                **current_task_spec,
                "adapters_spec": adapters,
                "contract": {"out": contract_out},
            },
        )

        spec = build_portable_spec(svc=self.svc, chat_comm=self.comm)
        globals = {
            "CONTRACT": contract_out,
            "COMM_SPEC": self.comm._export_comm_spec_for_runtime(),
            "PORTABLE_SPEC_JSON": spec.to_json(),
            "RESULT_FILENAME": result_filename,
            **runtime_globals
        }
        if extra_env and isinstance(extra_env, dict):
            globals.update(extra_env)

        run_res = await self.runtime.execute_py_code(
            workdir=workdir,
            output_dir=output_dir,
            tool_modules=self.tools.tool_modules_tuple_list(),
            globals=globals or {},
            timeout_s=timeout_s,
            isolation="docker",
            # isolation="none",
            bundle_root=self.tools.bundle_root,
            extra_env=extra_env,
        )
        return run_res

    def _mk_round_record(
            self,
            *,
            entrypoint: str,
            files_map: Dict[str, str],
            run_res: Dict[str, Any],
            notes: List[Any],
            collected: Dict[str, Any],
            internal_thinking: str,
            rii: str,
            constraints: Dict[str, Any],
            user_text: str,
            topics: List[str],
            policy_summary: str,
            workdir: pathlib.Path,
            outdir: pathlib.Path,
            run_id: str,
            main_src: Optional[str],
    ) -> Dict[str, Any]:
        rec = {
            "entrypoint": entrypoint,
            "files": [{"path": p, "size": len(c or "")} for p, c in files_map.items()],
            "run": run_res,
            "notes": notes,
            "outputs": collected,
            "internal_thinking": internal_thinking,
            "result_interpretation_instruction": rii,
            "inputs": {
                "constraints": constraints,
                "objective": user_text,
                "topics": topics,
                "tools_selected": [t for t in (constraints and [])],  # informational / not used downstream
                "policy_summary": policy_summary,
            },
            "workdir": str(workdir),
            "outdir": str(outdir),
            "run_id": run_id,
        }
        if main_src and len(main_src) <= 8000:
            rec["main_preview"] = main_src
        return rec

    def _write_codegen_failure_result(
            self,
            result_path: pathlib.Path,
            *,
            reason: str,
            details: Optional[Dict[str, Any]] = None,
            contract_out: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "ok": False,
            "out": [],
            "contract": contract_out or {},
            "error": {
                "where": "codegen",
                "error": reason,
                "description": "Code generation did not produce runnable code.",
                "details": details or {},
            },
        }
        try:
            result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except UnicodeError:
            safe_json = strip_lone_surrogates(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
            result_path.write_text(safe_json, encoding="utf-8")
        except Exception:
            pass

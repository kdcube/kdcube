# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/react.py

import asyncio
import json
import datetime
import pathlib
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable, Type

from langgraph.graph import StateGraph, END

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import (
    build_turn_session_journal,
    build_operational_digest,
    format_react_events_block,
    format_current_artifacts_block,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import emit_event, collect_outputs, get_exec_workspace_root
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext, format_tool_signature
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.execution import execute_tool, _safe_label
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.agents.v2.decision import react_decision_stream_v2
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.agents.v2.coordinator import UnifiedCoordinatorOutV2
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.budget import BudgetStateV2
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.canvas import DecisionRecordStreamer
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
import kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends as summary
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger, _mid

from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.runtime.files_and_attachments import (
    resolve_cited_file_sources_from_content,
    unwrap_llm_content_payload,
)
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any


@dataclass
class ReactStateV2:
    session_id: str
    turn_id: str

    exploration_budget: int
    exploitation_budget: int
    output_contract: Dict[str, Any]

    adapters: List[Dict[str, Any]]

    workdir: pathlib.Path
    outdir: pathlib.Path
    context: ReactContext

    plan_steps: List[str]
    plan_status: Dict[str, str] = field(default_factory=dict)

    coordinator_turn_line: str = ""
    next_decision_model: str = "strong"

    # Loop control
    iteration: int = 0
    max_iterations: int = 6
    decision_retries: int = 0
    max_decision_retries: int = 2

    exit_reason: Optional[str] = None
    clarification_questions: Optional[List[str]] = None

    last_decision: Optional[Dict[str, Any]] = None
    last_tool_result: Optional[List[Dict[str, Any]]] = None

    pending_tool_skills: Optional[List[str]] = None

    explore_used: int = 0
    exploit_used: int = 0

    session_log: List[Dict[str, Any]] = field(default_factory=list)
    round_timings: List[Dict[str, Any]] = field(default_factory=list)


class ReactSolverV2:
    MODULE_AGENT_NAME = "solver.react.v2"
    DECISION_AGENT_NAME = "decision.v2"

    def __init__(
        self,
        *,
        service: ModelServiceBase,
        logger: AgentLogger,
        tool_manager: Any,
        skills_subsystem: Any,
        scratchpad: TurnScratchpad,
        comm: ChatCommunicator,
        comm_context: ChatTaskPayload,
        hosting_service: Optional[Any] = None,
        turn_view_class=None,
        react_decision_stream: Callable[..., Awaitable[Dict[str, Any]]] = react_decision_stream_v2,
        solution_gen_stream: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
    ) -> None:
        self.svc = service
        self.log = logger
        self.tool_manager = tool_manager
        self.skills_subsystem = skills_subsystem
        self.scratchpad = scratchpad
        self.comm = comm
        self.comm_context = comm_context
        self.hosting_service = hosting_service
        self.turn_view_class = turn_view_class
        self.react_decision_stream = react_decision_stream
        self.solution_gen_stream = solution_gen_stream
        self.graph = self._build_graph()
        self._timeline_text_idx = {}
        self._outdir_cv_token = None
        self._workdir_cv_token = None

    def _build_graph(self) -> StateGraph:
        wf = StateGraph(dict)
        wf.add_node("decision", self._decision_node)
        wf.add_node("tool_execution", self._tool_execution_node)
        wf.add_node("exit", self._exit_node)

        wf.set_entry_point("decision")
        wf.add_conditional_edges(
            "decision",
            self._route_after_decision,
            {"tool_execution": "tool_execution", "decision": "decision", "exit": "exit"},
        )
        wf.add_edge("tool_execution", "decision")
        wf.add_edge("exit", END)
        return wf.compile()

    def _mk_thinking_streamer(self, phase: str) -> Callable[[str], Awaitable[None]]:
        counter = {"n": 0}

        async def emit_thinking_delta(text: str, completed: bool = False):
            if not text:
                return
            i = counter["n"]
            counter["n"] += 1
            author = f"{self.MODULE_AGENT_NAME}.{phase}"
            await self.comm.delta(text=text, index=i, marker="thinking", agent=author, completed=completed)

        return emit_thinking_delta

    def _mk_exec_code_streamer(
        self,
        phase: str,
        idx: int,
        execution_id: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], DecisionExecCodeStreamer]:
        artifact_suffix = execution_id or str(idx)
        streamer = DecisionExecCodeStreamer(
            emit_delta=self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=f"react.exec.{artifact_suffix}",
            execution_id=execution_id,
        )
        async def emit_json_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed(text)
        return emit_json_delta, streamer

    def _mk_record_streamer(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        artifact_name: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], DecisionRecordStreamer]:
        safe_name = artifact_name or f"react.record.{uuid.uuid4().hex[:8]}"
        streamer = DecisionRecordStreamer(
            emit_delta=self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=safe_name,
            sources_list=sources_list or [],
        )

        async def emit_json_delta(text: str, completed: bool = False, **_kwargs):
            if completed:
                await streamer.finish()
                return
            await streamer.feed(text)

        return emit_json_delta, streamer

    def _next_tool_streamer_idx(self, outdir: pathlib.Path, tool_id: str) -> int:
        dest_dir = outdir / "executed_programs"
        if not dest_dir.exists():
            return 0
        label = _safe_label(tool_id)
        max_idx = -1
        for path in dest_dir.glob("*_main.py"):
            if not path.name.startswith(label + "_"):
                continue
            parts = path.name.split("_")
            if len(parts) < 2:
                continue
            try:
                idx = int(parts[1])
                max_idx = max(max_idx, idx)
            except Exception:
                continue
        return max_idx + 1

    def _missing_contract_artifacts(self, context: ReactContext, output_contract: Dict[str, Any]) -> List[str]:
        if not output_contract:
            return []
        existing = set((context.artifacts or {}).keys())
        missing = [name for name in output_contract.keys() if name and name not in existing]
        return missing

    def _short_json(self, obj: Any, *, max_len: int = 800) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except Exception:
            try:
                text = str(obj)
            except Exception:
                return "<unprintable>"
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    def _protocol_violation_message(
        self,
        *,
        code: str,
        decision: Dict[str, Any],
        state: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        action = (decision.get("action") or "").strip()
        tool_id = ((decision.get("tool_call") or {}).get("tool_id") or "").strip()
        completion_summary = decision.get("completion_summary") or ""
        missing_contract = extra.get("missing") if isinstance(extra, dict) else None
        if code.startswith("invalid_action"):
            return f"Action '{action}' is not allowed. Allowed: call_tool | complete | exit | clarify."
        if code == "completion_summary_not_allowed":
            return f"completion_summary is only allowed for complete/exit. action={action}."
        if code == "missing_tool_id":
            return "tool_call.tool_id is missing for action=call_tool."
        if code == "missing_out_artifacts_spec":
            return f"out_artifacts_spec is required for call_tool (tool_id={tool_id or 'unknown'})."
        if code == "contract_not_completed":
            return f"Attempted to complete/exit while contract artifacts are missing: {missing_contract}."
        if code == "tool_call_invalid":
            return f"tool_call failed protocol validation for tool_id={tool_id or 'unknown'}."
        if code == "tool_signature_red":
            return f"tool params failed signature validation for tool_id={tool_id or 'unknown'}."
        if completion_summary:
            return f"completion_summary present with action={action}."
        return f"Protocol violation: {code}."

    async def _emit_timeline_text(self, *, text: str, agent: str, artifact_name: str):
        if not text:
            return
        idx = int(self._timeline_text_idx.get(artifact_name, 0))
        await self.comm.delta(
            text=text,
            index=idx,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=False,
        )
        self._timeline_text_idx[artifact_name] = idx + 1
        await self.comm.delta(
            text="",
            index=idx + 1,
            marker="timeline_text",
            agent=agent,
            format="markdown",
            artifact_name=artifact_name,
            completed=True,
        )
        self._timeline_text_idx[artifact_name] = idx + 2

    def _validate_decision(self, decision: Dict[str, Any]) -> Optional[str]:
        action = (decision.get("action") or "").strip()
        if action not in {"call_tool", "complete", "exit", "clarify"}:
            return f"invalid_action:{action}"
        if action not in {"complete", "exit"} and (decision.get("completion_summary") or ""):
            return "completion_summary_not_allowed"
        if action != "call_tool":
            return None
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        if not tool_id:
            return "missing_tool_id"
        if tool_id == "infra.show":
            return None
        out_specs = tool_call.get("out_artifacts_spec") or []
        if not isinstance(out_specs, list) or not out_specs:
            return "missing_out_artifacts_spec"
        if tool_id == "infra.record":
            params = tool_call.get("params") or {}
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "artifact_name":
                    return "artifact_name_not_first"
                if len(keys) >= 2 and keys[1] != "format":
                    return "format_not_second"
                if len(keys) >= 3 and keys[2] != "generated_data":
                    return "generated_data_not_third"
        for spec in out_specs:
            if not isinstance(spec, dict):
                return "invalid_out_artifacts_spec"
            name = (spec.get("name") or "").strip()
            filename = (spec.get("filename") or "").strip()
            mime = (spec.get("mime") or "").strip()
            if not name or not filename or not mime:
                return f"invalid_artifact_spec:{name or 'unknown'}"
        return None

    def _validate_tool_call_protocol(
        self,
        *,
        tool_call: Any,
        adapters_by_id: Dict[str, Dict[str, Any]],
        param_binding: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        violations: List[Dict[str, Any]] = []

        tc = tool_call if isinstance(tool_call, dict) else {}
        tool_id = (tc.get("tool_id") or "").strip() or None

        params = tc.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            violations.append({
                "code": "bad_params",
                "message": "tool_call.params must be a dict",
            })
            params = {}

        if not tool_id:
            violations.append({
                "code": "missing_tool_id",
                "message": "tool_call.tool_id is missing or empty",
            })
        else:
            if tool_id not in adapters_by_id and tool_id not in {"infra.show", "infra.record"}:
                violations.append({
                    "code": "unknown_tool_id",
                    "message": f"tool_id '{tool_id}' is not in adapters/available tools",
                    "tool_id": tool_id,
                })
        allowed_params: set[str] = set()
        if tool_id in {"infra.show", "infra.record"}:
            if tool_id == "infra.show":
                allowed_params.update({"load", "unload"})
            else:
                allowed_params.update({"artifact_name", "format", "generated_data", "sources_list"})
        else:
            adapter = adapters_by_id.get(tool_id) if tool_id else None
            if adapter:
                doc_args = (adapter.get("doc") or {}).get("args") or {}
                if isinstance(doc_args, dict):
                    allowed_params.update(str(k) for k in doc_args.keys())
                template = adapter.get("call_template") or ""
                if "(" in template and ")" in template:
                    inner = template.split("(", 1)[1].rsplit(")", 1)[0]
                    parts = [p.strip() for p in inner.split(",") if p.strip()]
                    for p in parts:
                        name = p.split("=", 1)[0].strip()
                        if name:
                            allowed_params.add(name)

        tras = tc.get("out_artifacts_spec", None)
        artifact_specs: List[Dict[str, Any]] = []
        if isinstance(tras, list):
            for a in tras:
                if not isinstance(a, dict):
                    continue
                nm = (a.get("name") or "").strip()
                if nm:
                    artifact_specs.append(a)
        if not artifact_specs and tools_insights.is_exec_tool(tool_id):
            alt = params.get("out_artifacts_spec") if isinstance(params, dict) else None
            if isinstance(alt, list):
                for a in alt:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get("name") or a.get("artifact_name") or a.get("filename") or "").strip()
                    if not nm:
                        continue
                    rec = {"name": nm, "kind": "file"}
                    if a.get("filename"):
                        rec["filename"] = a.get("filename")
                    if a.get("mime"):
                        rec["mime"] = a.get("mime")
                    if a.get("description"):
                        rec["description"] = a.get("description")
                    artifact_specs.append(rec)
        if not artifact_specs:
            if tool_id != "infra.show":
                violations.append({
                    "code": "missing_out_artifacts_spec",
                    "message": "call_tool requires non-empty out_artifacts_spec[] with dicts having non-empty name",
                    "tool_id": tool_id,
                })
        if tool_id == "infra.record":
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "artifact_name":
                    violations.append({
                        "code": "artifact_name_not_first",
                        "message": "infra.record params must start with artifact_name",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 2 and keys[1] != "format":
                    violations.append({
                        "code": "format_not_second",
                        "message": "infra.record params must have format as the second field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 3 and keys[2] != "generated_data":
                    violations.append({
                        "code": "generated_data_not_third",
                        "message": "infra.record params must have generated_data as the third field",
                        "tool_id": tool_id,
                    })
        if allowed_params and isinstance(param_binding, list):
            for fd in param_binding:
                if not isinstance(fd, dict):
                    continue
                pn = (fd.get("param_name") or "").strip()
                if pn and pn not in allowed_params:
                    violations.append({
                        "code": "unknown_fetch_param",
                        "message": f"param_binding param '{pn}' not accepted by tool",
                        "tool_id": tool_id,
                        "param": pn,
                    })

        ok = len(violations) == 0
        return {
            "ok": ok,
            "tool_id": tool_id,
            "artifact_specs": artifact_specs,
            "violations": violations,
            "params": params,
        }

    async def prepare_session(
        self,
        *,
        coordinator_out: UnifiedCoordinatorOutV2,
        allowed_plugins: List[str],
        materialize_turn_ids: List[str],
        runtime_ctx: Dict[str, Any],
    ) -> ReactStateV2:
        import tempfile

        root = get_exec_workspace_root()
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="react_v2_", dir=str(root)))
        workdir = tmp / "work"
        outdir = tmp / "out"
        workdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            self._outdir_cv_token = OUTDIR_CV.set(str(outdir))
            self._workdir_cv_token = WORKDIR_CV.set(str(workdir))
        except Exception as e:
            self.log.log(f"[react-subsystem] Failed to set CVs: {e}", level="ERROR")
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_active_skills_subsystem
            if self.skills_subsystem:
                set_active_skills_subsystem(self.skills_subsystem)
        except Exception:
            pass

        session_id = f"react-v2-{uuid.uuid4().hex[:8]}"
        turn_id = runtime_ctx.get("turn_id") or "current_turn"
        user_id = runtime_ctx.get("user_id")
        conversation_id = runtime_ctx.get("conversation_id")
        bundle_id = runtime_ctx.get("bundle_id")

        self.log.log(f"[react.v2] Start {session_id} in {workdir}")

        browser = ContextBrowser(
            ctx_client=getattr(self.tool_manager, "context_rag_client", None),
            logger=self.log,
            turn_view_class=self.turn_view_class,
            model_service=self.svc
        )
        bundle = getattr(self.scratchpad, "context_bundle", None)
        if not bundle:
            bundle = await browser.materialize(
                materialize_turn_ids=materialize_turn_ids,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            try:
                setattr(self.scratchpad, "context_bundle", bundle)
            except Exception:
                pass

        t_rehost = time.perf_counter()
        await asyncio.gather(
            browser.rehost_previous_files(bundle=bundle, workdir=outdir, ctx="react_v2"),
            browser.rehost_previous_attachments(bundle=bundle, workdir=outdir, ctx="react_v2"),
            return_exceptions=True,
        )
        self._append_react_timing(
            round_idx=0,
            stage="rehost.prev",
            elapsed_ms=int((time.perf_counter() - t_rehost) * 1000),
        )

        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution import solution_workspace
            if getattr(self.scratchpad, "user_attachments", None):
                t_rehost = time.perf_counter()
                rehosted = await solution_workspace.rehost_previous_attachments(
                    self.scratchpad.user_attachments,
                    outdir,
                    turn_id=turn_id or "current_turn",
                )
                self.scratchpad.user_attachments = rehosted
                self._append_react_timing(
                    round_idx=0,
                    stage="rehost.current",
                    elapsed_ms=int((time.perf_counter() - t_rehost) * 1000),
                )
        except Exception as e:
            self.log.log(f"[react.v2] Warning: Failed to rehost current attachments: {e}", level="WARNING")

        context = browser.make_react_context(
            bundle=bundle,
            scratchpad=self.scratchpad,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            bundle_id=bundle_id,
        )
        context.bind_storage(outdir)
        context.timezone = self.comm_context.user.timezone

        adapters = await self.tool_manager.tools.react_tools(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,
        )

        plan = coordinator_out.decision.plan
        plan_steps = list(plan.steps or [])
        exploration_budget = int(plan.exploration_budget or 0)
        exploitation_budget = int(plan.exploitation_budget or 0)
        try:
            context.plan_steps = plan_steps
            context.plan_status = {}
        except Exception:
            pass

        coordinator_turn_line = (
            f"plan_steps={len(plan_steps)} | budgets: explore={exploration_budget}, exploit={exploitation_budget}"
        )

        max_iterations = max(2, exploration_budget + exploitation_budget + 2)
        bs_v2 = BudgetStateV2(
            exploration_budget=exploration_budget,
            exploitation_budget=exploitation_budget,
            explore_used=0,
            exploit_used=0,
            max_iterations=max_iterations,
            decision_rounds_used=0,
        )
        setattr(context, "budget_state_v2", bs_v2)

        return ReactStateV2(
            session_id=session_id,
            turn_id=turn_id,
            plan_steps=plan_steps,
            plan_status={},
            exploration_budget=exploration_budget,
            exploitation_budget=exploitation_budget,
            output_contract={k: v.model_dump() for k, v in (coordinator_out.decision.output_contract or {}).items()},
            adapters=adapters,
            workdir=workdir,
            outdir=outdir,
            context=context,
            coordinator_turn_line=coordinator_turn_line,
            max_iterations=max_iterations,
            decision_retries=0,
            max_decision_retries=2,
        )

    async def run(
        self,
        *,
        coordinator_out: UnifiedCoordinatorOutV2,
        allowed_plugins: List[str],
        materialize_turn_ids: List[str],
        runtime_ctx: Dict[str, Any],
    ) -> dict:
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent=self.MODULE_AGENT_NAME,
        )
        await turn_status.send(random.choice(["executing", "working", "solving"]))

        state = await self.prepare_session(
            coordinator_out=coordinator_out,
            allowed_plugins=allowed_plugins,
            materialize_turn_ids=materialize_turn_ids,
            runtime_ctx=runtime_ctx,
        )

        start_ts = time.time()
        try:
            recursion_limit = max(20, (int(state.max_iterations) * 3) + 6)
            final_state = await self.graph.ainvoke(self._to_dict(state), config={"recursion_limit": recursion_limit})
        except Exception as exc:
            self.log.log(f"[react.v2] Graph error: {exc}", level="ERROR")
            final_state = self._to_dict(state)
            final_state["exit_reason"] = "error"
            final_state["error"] = {
                "where": "react.v2",
                "error": "graph_error",
                "message": str(exc),
                "managed": True,
            }
        finally:
            if self._outdir_cv_token is not None:
                try:
                    OUTDIR_CV.reset(self._outdir_cv_token)
                except Exception:
                    pass
                self._outdir_cv_token = None
            if self._workdir_cv_token is not None:
                try:
                    WORKDIR_CV.reset(self._workdir_cv_token)
                except Exception:
                    pass
                self._workdir_cv_token = None

        try:
            final_state["total_runtime_sec"] = float(time.time() - start_ts)
        except Exception:
            pass

        return await self._analyze_and_build_result(final_state)

    def _to_dict(self, s: ReactStateV2) -> Dict[str, Any]:
        return {
            "session_id": s.session_id,
            "turn_id": s.turn_id,
            "plan_steps": s.plan_steps,
            "plan_status": s.plan_status,
            "exploration_budget": s.exploration_budget,
            "exploitation_budget": s.exploitation_budget,
            "output_contract": s.output_contract,
            "adapters": s.adapters,
            "workdir": str(s.workdir),
            "outdir": str(s.outdir),
            "context": s.context,
            "coordinator_turn_line": s.coordinator_turn_line,
            "next_decision_model": s.next_decision_model,
            "iteration": s.iteration,
            "max_iterations": s.max_iterations,
            "decision_retries": s.decision_retries,
            "max_decision_retries": s.max_decision_retries,
            "exit_reason": s.exit_reason,
            "clarification_questions": s.clarification_questions,
            "last_decision": s.last_decision,
            "last_tool_result": s.last_tool_result,
            "pending_tool_skills": s.pending_tool_skills,
            "explore_used": s.explore_used,
            "exploit_used": s.exploit_used,
            "session_log": s.session_log,
            "round_timings": s.round_timings,
        }

    def _route_after_decision(self, state: Dict[str, Any]) -> str:
        if state.get("retry_decision"):
            try:
                self.log.log("[react.v2] retry_decision=True -> route=decision", level="INFO")
            except Exception:
                pass
            state["retry_decision"] = False
            return "decision"
        decision = state.get("last_decision") or {}
        action = decision.get("action")
        if action == "call_tool":
            try:
                self.log.log("[react.v2] route=tool_execution", level="INFO")
            except Exception:
                pass
            return "tool_execution"
        try:
            self.log.log(f"[react.v2] route=exit action={action}", level="INFO")
        except Exception:
            pass
        return "exit"

    async def _decision_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        context: ReactContext = state["context"]
        iteration = int(state.get("iteration") or 0)
        if iteration >= int(state.get("max_iterations") or 0):
            state["exit_reason"] = "max_iterations"
            return state
        try:
            return await self._decision_node_impl(state, context, iteration)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] decision_node error: {exc}\n{tb}", level="ERROR")
                show_arts = state.get("show_artifacts") or []
                self.log.log(
                    f"[react.v2] decision_node context: show_artifacts={len(show_arts)} "
                    f"types={[type(x).__name__ for x in show_arts[:3]]}",
                    level="ERROR",
                )
            except Exception:
                pass
            raise

    async def _decision_node_impl(self, state: Dict[str, Any], context: ReactContext, iteration: int) -> Dict[str, Any]:
        turn_journal = build_turn_session_journal(
            context=context,
            output_contract=state.get("output_contract") or {},
            max_prior_turns=5,
            turn_view_class=self.turn_view_class,
            coordinator_turn_line=state.get("coordinator_turn_line"),
        )
        if not state.get("show_artifacts"):
            context.show_artifact_attachments = []
        show_artifacts_for_journal = state.get("show_artifacts")
        show_skills_for_journal = state.get("show_skills")
        try:
            self.log.log(
                f"[react.v2] decision journal: show_artifacts={len(show_artifacts_for_journal or [])} "
                f"show_skills={len(show_skills_for_journal or [])}",
                level="INFO",
            )
        except Exception:
            pass
        if state.get("show_artifacts"):
            state["show_artifacts"] = None
        if state.get("show_skills"):
            state["show_skills"] = None

        announced_adapters = [
            a for a in (state.get("adapters") or [])
            if a.get("id") not in tools_insights.CODEGEN_ONLY_TOOL_IDS and not tools_insights.is_code_tool(a["id"])
        ]
        extra_adapters = [a for a in (state.get("adapters") or []) if tools_insights.is_code_tool(a["id"])]
        extra_adapters_for_decision = [
            a for a in extra_adapters if not tools_insights.is_codegen_tool(a["id"])
        ]

        operational_digest = build_operational_digest(
            turn_session_journal=turn_journal,
            session_log=state.get("session_log") or [],
            adapters=announced_adapters,
            show_artifacts=show_artifacts_for_journal,
        )
        context.operational_digest = operational_digest

        model_kind = state.get("next_decision_model") or "strong"
        role = f"{self.MODULE_AGENT_NAME}.{self.DECISION_AGENT_NAME}.{model_kind}"
        async with with_accounting(
            context.bundle_id,
            agent=role,
            metadata={"agent": role},
        ):
            thinking_streamer = self._mk_thinking_streamer(f"decision ({iteration})")
            pending_tool_call_id = uuid.uuid4().hex[:12]
            exec_id = f"exec_{pending_tool_call_id}"
            exec_streamer_idx = self._next_tool_streamer_idx(
                pathlib.Path(state["outdir"]),
                "exec_tools.execute_code_python",
            )
            exec_streamer_fn, exec_streamer_widget = self._mk_exec_code_streamer(
                f"decision ({iteration})",
                exec_streamer_idx,
                execution_id=exec_id,
            )
            record_streamer_fn, record_streamer_widget = self._mk_record_streamer(
                f"decision.record ({iteration})",
                sources_list=context.sources_pool or [],
                artifact_name=f"react.record.{pending_tool_call_id}",
            )

            async def _hub_on_json(text: str, completed: bool = False, **_kwargs):
                await exec_streamer_fn(text, completed=completed)
                await record_streamer_fn(text, completed=completed)

            thinking_streamer._on_json = _hub_on_json
            t0 = time.perf_counter()
            decision = await self.react_decision_stream(
                svc=self.svc,
                operational_digest=operational_digest,
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
                active_skills=show_skills_for_journal or None,
                on_progress_delta=thinking_streamer,
                iteration_idx=iteration,
                max_iterations=int(state.get("max_iterations") or 0),
                agent_name=role,
                is_wrapup_round=False,
                timezone=self.comm_context.user.timezone,
                max_tokens=6000,
                attachments=context.show_artifact_attachments,
                plan_steps=state.get("plan_steps") or [],
                exploration_budget=int(state.get("exploration_budget") or 0),
                exploitation_budget=int(state.get("exploitation_budget") or 0),
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
        logging_helpers.log_agent_packet(role, "react.decision.v2", decision)
        decision = decision.get("agent_response") or {}
        if not isinstance(decision, dict):
            decision = {}

        validation_error = self._validate_decision(decision)
        if validation_error:
            context.add_event(kind="protocol_violation", data={
                "code": validation_error,
                "message": self._protocol_violation_message(
                    code=validation_error,
                    decision=decision,
                    state=state,
                ),
            })
            try:
                self.log.log(
                    f"[react.v2] decision validation failed: {validation_error} | decision={self._short_json(decision)}",
                    level="ERROR",
                )
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision = {"action": "call_tool", "notes": f"{validation_error}; retry decision"}
                state["session_log"].append({
                    "type": "decision_invalid",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "error": validation_error,
                })
                state["last_decision"] = decision
                return state
            else:
                decision = {"action": "exit", "completion_summary": "Decision validation failed."}
        action = (decision.get("action") or "").strip()
        strategy = (decision.get("strategy") or "").strip() or None
        notes = (decision.get("notes") or "").strip()
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()

        def _extract_plan_status_from_notes(notes_text: str, total: int) -> Dict[str, str]:
            out: Dict[str, str] = {}
            if not notes_text:
                return out
            for raw in notes_text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if not (line.startswith("✓") or line.startswith("□") or line.startswith("✗")):
                    continue
                mark = line[0]
                rest = line[1:].strip()
                num = ""
                if rest.startswith("["):
                    idx = rest.find("]")
                    if idx != -1:
                        num = rest[1:idx].strip()
                if not num:
                    parts = rest.split(None, 1)
                    if parts:
                        num = parts[0].strip().rstrip(".").rstrip(":")
                if not num.isdigit():
                    continue
                n = int(num)
                if n <= 0 or (total and n > total):
                    continue
                status = "pending" if mark == "□" else "done" if mark == "✓" else "failed"
                out[str(n)] = status
            return out

        plan_steps = state.get("plan_steps") or []
        plan_updates = _extract_plan_status_from_notes(notes, len(plan_steps))
        if plan_updates:
            status_map = state.get("plan_status") or {}
            if not isinstance(status_map, dict):
                status_map = {}
            new_updates: Dict[str, str] = {}
            for k, v in plan_updates.items():
                if status_map.get(k) != v:
                    new_updates[k] = v
            if new_updates:
                status_map.update(new_updates)
                state["plan_status"] = status_map
                try:
                    context.plan_status = status_map
                except Exception:
                    pass
                try:
                    ack_items = []
                    for k, v in new_updates.items():
                        idx = int(k) - 1
                        step_text = plan_steps[idx] if 0 <= idx < len(plan_steps) else ""
                        ack_items.append({
                            "step": int(k),
                            "status": v,
                            "text": step_text,
                        })
                    if ack_items:
                        context.add_event(kind="plan_ack", data={"items": ack_items, "iteration": iteration})
                except Exception:
                    pass

        missing_contract = self._missing_contract_artifacts(context, state.get("output_contract") or {})
        exec_only_ids = {"ctx_tools.fetch_ctx", "generic_tools.read_file", "llm_tools.generate_content_llm"}
        if action == "call_tool" and tool_id in exec_only_ids:
            context.add_event(kind="protocol_violation", data={
                "code": "tool_not_allowed_in_react",
                "tool_id": tool_id,
                "iteration": iteration,
                "message": self._protocol_violation_message(
                    code="tool_not_allowed_in_react",
                    decision=decision,
                    state=state,
                    extra={"tool_id": tool_id},
                ),
            })
            try:
                self.log.log(f"[react.v2] tool_not_allowed_in_react: {tool_id}", level="ERROR")
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "tool_not_allowed_in_react; retry decision"
                action = "call_tool"
            else:
                decision = {
                    "action": "exit",
                    "completion_summary": "Tool not allowed in react loop.",
                    "notes": "tool_not_allowed_in_react",
                }
                action = "exit"
        plan_steps = state.get("plan_steps") or []
        plan_status = state.get("plan_status") or {}
        total_steps = len(plan_steps) if isinstance(plan_steps, list) else 0
        done_steps = len([k for k, v in (plan_status or {}).items() if v == "done"]) if isinstance(plan_status, dict) else 0
        failed_steps = len([k for k, v in (plan_status or {}).items() if v == "failed"]) if isinstance(plan_status, dict) else 0
        pending_steps = max(0, total_steps - done_steps - failed_steps)
        if action in {"complete", "exit"} and missing_contract:
            context.add_event(kind="protocol_violation", data={
                "code": "contract_not_completed",
                "missing": missing_contract,
                "iteration": iteration,
                "message": self._protocol_violation_message(
                    code="contract_not_completed",
                    decision=decision,
                    state=state,
                    extra={"missing": missing_contract},
                ),
            })
            state["session_log"].append({
                "type": "contract_not_completed",
                "iteration": iteration,
                "timestamp": time.time(),
                "missing": missing_contract,
            })
            try:
                self.log.log(f"[react.v2] contract_not_completed: missing={missing_contract}", level="ERROR")
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "contract_not_completed; retry decision"
                action = "call_tool"
            else:
                decision = {
                    "action": "exit",
                    "completion_summary": "Contract artifacts not completed.",
                    "notes": "contract_not_completed",
                }
                action = "exit"
        if action in {"complete", "exit"} and pending_steps > 0:
            context.add_event(kind="protocol_violation", data={
                "code": "plan_not_completed",
                "pending": pending_steps,
                "iteration": iteration,
                "message": self._protocol_violation_message(
                    code="plan_not_completed",
                    decision=decision,
                    state=state,
                    extra={"pending": pending_steps},
                ),
            })
            state["session_log"].append({
                "type": "plan_not_completed",
                "iteration": iteration,
                "timestamp": time.time(),
                "pending": pending_steps,
            })
            try:
                self.log.log(f"[react.v2] plan_not_completed: pending={pending_steps}", level="ERROR")
            except Exception:
                pass
            retries = int(state.get("decision_retries") or 0)
            if retries < int(state.get("max_iterations") or 0):
                state["decision_retries"] = retries + 1
                state["retry_decision"] = True
                decision["notes"] = "plan_not_completed; retry decision"
                action = "call_tool"
            else:
                decision = {
                    "action": "exit",
                    "completion_summary": "Plan steps not completed.",
                    "notes": "plan_not_completed",
                }
                action = "exit"
        if action == "call_tool":
            adapters_by_id = self._adapters_index(state.get("adapters") or [])
            verdict = self._validate_tool_call_protocol(
                tool_call=tool_call,
                adapters_by_id=adapters_by_id,
                param_binding=decision.get("param_binding") or [],
            )
            protocol_entry = {
                "type": "protocol_verify",
                "iteration": iteration,
                "timestamp": time.time(),
                "tool_id": verdict.get("tool_id"),
                "ok": bool(verdict.get("ok")),
                "violations": verdict.get("violations") or [],
            }
            if verdict.get("ok"):
                state["session_log"].append(protocol_entry)
            else:
                context.add_event(kind="protocol_violation", data={
                    "code": "tool_call_invalid",
                    "violations": verdict.get("violations") or [],
                    "tool_id": verdict.get("tool_id"),
                    "iteration": iteration,
                    "message": self._protocol_violation_message(
                        code="tool_call_invalid",
                        decision=decision,
                        state=state,
                    ),
                })
                try:
                    self.log.log(
                        f"[react.v2] tool_call_invalid: {verdict.get('violations')} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                state["session_log"].append(protocol_entry)
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_call_invalid; retry decision"
                else:
                    decision = {
                        "action": "exit",
                        "completion_summary": "Tool call validation failed.",
                        "notes": "tool_call_invalid",
                    }
                    action = "exit"
                    tool_call = {}
                    tool_id = ""
            # else:
            #     pass

            # ---- tool signature validation (filter params + classify issues) ----
            sig_status = None
            sig_issues: List[Dict[str, Any]] = []
            filtered_params = tool_call.get("params") if isinstance(tool_call, dict) else {}
            try:
                if tool_id and tool_id not in {"infra.show", "infra.record"}:
                    tv = await self.tool_manager.tools.validate_tool_params(tool_id=tool_id, params=filtered_params)
                    sig_status = tv.get("status")
                    sig_issues = tv.get("issues") or []
                    filtered_params = tv.get("params") or {}
                    if isinstance(tool_call, dict):
                        tool_call["params"] = filtered_params
                        decision["tool_call"] = tool_call
                        state["last_decision"] = decision
            except Exception:
                sig_status = None
                sig_issues = []

            if sig_status in ("yellow", "red"):
                context.add_event(kind="tool_signature_validation", data={
                    "iteration": iteration,
                    "tool_id": tool_id,
                    "status": sig_status,
                    "issues": sig_issues,
                })
                state["session_log"].append({
                    "type": "tool_signature_validation",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": tool_id,
                    "status": sig_status,
                    "issues": sig_issues,
                })

            if sig_status == "red":
                context.add_event(kind="protocol_violation", data={
                    "code": "tool_signature_red",
                    "violations": sig_issues,
                    "tool_id": tool_id,
                    "iteration": iteration,
                    "message": self._protocol_violation_message(
                        code="tool_signature_red",
                        decision=decision,
                        state=state,
                    ),
                })
                try:
                    self.log.log(
                        f"[react.v2] tool_signature_red: {sig_issues} | decision={self._short_json(decision)}",
                        level="ERROR",
                    )
                except Exception:
                    pass
                if protocol_entry is not None:
                    protocol_entry["ok"] = False
                    protocol_entry["violations"] = (protocol_entry.get("violations") or []) + (sig_issues or [])
                retries = int(state.get("decision_retries") or 0)
                if retries < int(state.get("max_iterations") or 0):
                    state["decision_retries"] = retries + 1
                    state["retry_decision"] = True
                    decision["notes"] = "tool_signature_red; retry decision"
                else:
                    decision = {
                        "action": "exit",
                        "completion_summary": "Tool signature validation failed.",
                        "notes": "tool_signature_red",
                    }
                    action = "exit"
                    tool_call = {}
                    tool_id = ""
        call_reason = (tool_call.get("reasoning") or "").strip()
        if call_reason:
            if tools_insights.is_exec_tool(tool_id) and exec_streamer_widget:
                await exec_streamer_widget.emit_reasoning(call_reason)
            else:
                turn_id = state.get("turn_id") or "current_turn"
                timeline_agent = f"{role}.timeline.{turn_id}.{iteration}"
                await self._emit_timeline_text(
                    text=call_reason,
                    agent=timeline_agent,
                    artifact_name=f"timeline_text.react.decision.{iteration}",
                )

        # Budget enforcement: only count explore/exploit strategies
        if action == "call_tool" and strategy in {"explore", "exploit"}:
            if strategy == "explore" and int(state.get("explore_used") or 0) >= int(state.get("exploration_budget") or 0):
                action = "exit"
                decision["action"] = "exit"
                decision["completion_summary"] = "Exploration budget exhausted; stopping this turn."
                state["exit_reason"] = "budget_exhausted"
            if strategy == "exploit" and int(state.get("exploit_used") or 0) >= int(state.get("exploitation_budget") or 0):
                action = "exit"
                decision["action"] = "exit"
                decision["completion_summary"] = "Exploitation budget exhausted; stopping this turn."
                state["exit_reason"] = "budget_exhausted"

        if not state.get("retry_decision") and action in {"complete", "exit"}:
            state["exit_reason"] = action
        if not state.get("retry_decision") and action == "clarify":
            state["exit_reason"] = "clarify"
            state["clarification_questions"] = decision.get("clarification_questions") or []

        context.add_event(
            kind="decision",
            data={
                "action": action,
                "strategy": strategy,
                "notes": notes,
                "tool_call": decision.get("tool_call"),
                "param_binding": decision.get("param_binding") or [],
                "iteration": iteration,
            },
        )
        await emit_event(
            comm=self.comm,
            etype="solver.react.decision",
            title=f"ReAct Round ({iteration}). Decision Node",
            step=f"react({iteration}).decision",
            data=decision if decision else {"error": "no_decision"},
            agent=f"solver.react({iteration}).decision",
        )
        try:
            self.scratchpad.register_agentic_response(
                f"solver.react.decision ({iteration})",
                decision,
            )
        except Exception as exc:
            try:
                self.log.log(f"[react.v2] register_agentic_response failed: {exc}", level="ERROR")
            except Exception:
                pass

        state["exec_code_streamer"] = exec_streamer_widget
        state["record_streamer"] = record_streamer_widget
        state["pending_exec_id"] = exec_id
        state["pending_tool_call_id"] = pending_tool_call_id
        state["last_decision"] = decision
        state["iteration"] = iteration + 1
        bs = getattr(context, "budget_state_v2", None)
        if bs is not None:
            bs.decision_rounds_used = int(state.get("iteration") or 0)
        return state

    async def _tool_execution_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        context: ReactContext = state["context"]
        decision = state.get("last_decision") or {}
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        tool_call_id = state.pop("pending_tool_call_id", None) or tool_call.get("tool_call_id") or uuid.uuid4().hex[:12]
        if not tool_id:
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "missing_tool_id", "managed": True}
            return state

        if tool_id == "infra.show":
            params = tool_call.get("params") or {}
            load = params.get("load") or []
            unload = params.get("unload") or []
            raw_load = [str(p).strip() for p in (load if isinstance(load, list) else []) if str(p).strip()]
            unload_paths = [str(p).strip() for p in (unload if isinstance(unload, list) else []) if str(p).strip()]
            try:
                self.log.log(
                    f"[react.v2] infra.show requested load={raw_load} unload={unload_paths}",
                    level="INFO",
                )
            except Exception:
                pass

            skill_load = [p for p in raw_load if p.startswith("SK") or p.startswith("skill:")]
            artifact_load = [p for p in raw_load if p not in skill_load]
            skill_unload = [p for p in unload_paths if p.startswith("SK") or p.startswith("skill:")]
            artifact_unload = [p for p in unload_paths if p not in skill_unload]

            current_items = list(state.get("show_artifacts") or [])
            if artifact_unload:
                current_items = [
                    it for it in current_items
                    if not (isinstance(it, dict) and str(it.get("context_path") or "").strip() in artifact_unload)
                ]
            missing_artifacts: List[str] = []
            if artifact_load:
                # detect missing paths for explicit feedback
                for path in artifact_load:
                    if path.startswith("sources_pool[") and path.endswith("]"):
                        sids_raw = path[len("sources_pool["):-1]
                        sids = []
                        for tok in sids_raw.split(","):
                            tok = tok.strip()
                            if not tok:
                                continue
                            try:
                                sids.append(int(tok))
                            except Exception:
                                continue
                        if not sids or not context.materialize_sources_by_sids(sids):
                            missing_artifacts.append(path)
                        continue
                    if path.endswith(".user") or path.endswith(".assistant") or ".user.prompt." in path or ".assistant.completion." in path:
                        val, _owner = context.resolve_path(path)
                        if not val:
                            missing_artifacts.append(path)
                        continue
                    base_path = path
                    parts = path.split(".")
                    if len(parts) >= 3 and parts[1] in ("slots", "artifacts"):
                        base_path = ".".join(parts[:3])
                    if context.resolve_object(base_path) is None:
                        missing_artifacts.append(path)
                loaded = context.materialize_show_artifacts(artifact_load)
                if loaded:
                    current_items.extend(loaded)
                try:
                    self.log.log(
                        f"[react.v2] infra.show loaded artifacts={len(loaded or [])}",
                        level="INFO",
                    )
                except Exception:
                    pass
            state["show_artifacts"] = current_items or []
            context.show_artifact_attachments = current_items or []

            current_skills = list(state.get("show_skills") or [])
            if skill_load or skill_unload:
                try:
                    from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import import_skillset, build_skill_short_id_map
                    short_map = build_skill_short_id_map(consumer="solver.react.decision")
                    if skill_load:
                        skill_load = import_skillset(skill_load, short_id_map=short_map)
                    if skill_unload:
                        skill_unload = import_skillset(skill_unload, short_id_map=short_map)
                except Exception:
                    pass
            if skill_unload:
                current_skills = [s for s in current_skills if str(s).strip() not in skill_unload]
            if skill_load:
                for s in skill_load:
                    if s not in current_skills:
                        current_skills.append(s)
            state["show_skills"] = current_skills or []

            context.add_event(kind="tool_started", data={
                "artifact_ids": [],
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "signature": "infra.show",
                "reasoning": tool_call.get("reasoning") or "",
            })
            if artifact_load:
                context.add_event(kind="change_visibility", data={"paths": artifact_load, "action": "load", "target": "artifacts"})
            if artifact_unload:
                context.add_event(kind="change_visibility", data={"paths": artifact_unload, "action": "unload", "target": "artifacts"})
            if skill_load:
                context.add_event(kind="change_visibility", data={"paths": skill_load, "action": "load", "target": "skills"})
            if skill_unload:
                context.add_event(kind="change_visibility", data={"paths": skill_unload, "action": "unload", "target": "skills"})
            if missing_artifacts:
                context.add_event(kind="protocol_violation", data={
                    "code": "show_artifacts_missing",
                    "missing": missing_artifacts,
                    "tool_id": tool_id,
                    "message": f"infra.show requested non-existent artifacts: {missing_artifacts}",
                })
                try:
                    self.log.log(f"[react.v2] infra.show missing paths: {missing_artifacts}", level="ERROR")
                except Exception:
                    pass

            context.add_event(kind="tool_finished", data={
                "artifact_ids": [],
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "produced_artifact_ids": [],
                "error": None,
            })
            state["last_tool_result"] = []
            return state

        if tool_id == "infra.record":
            params = tool_call.get("params") or {}
            artifact_name = str(params.get("artifact_name") or "").strip()
            fmt = str(params.get("format") or "markdown").strip() or "markdown"
            generated_data = params.get("generated_data")

            declared_specs = tool_call.get("out_artifacts_spec") or []
            spec = None
            if artifact_name:
                spec = next((s for s in declared_specs if isinstance(s, dict) and s.get("name") == artifact_name), None)
            if spec is None:
                spec = next((s for s in declared_specs if isinstance(s, dict)), None)
            if not artifact_name and isinstance(spec, dict):
                artifact_name = str(spec.get("name") or "").strip()
            if not artifact_name:
                state["exit_reason"] = "error"
                state["error"] = {"where": "tool_execution", "error": "missing_artifact_name", "managed": True}
                return state

            text = None
            if isinstance(generated_data, str):
                text = generated_data
            else:
                try:
                    text = json.dumps(generated_data, ensure_ascii=False, indent=2)
                except Exception:
                    text = str(generated_data)

            context.add_event(kind="tool_started", data={
                "artifact_ids": [artifact_name],
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "signature": "infra.record",
                "reasoning": tool_call.get("reasoning") or "",
            })

            sources_used: List[Any] = []
            raw_sources = params.get("sources_list")
            if isinstance(raw_sources, list):
                sources_used.extend(context.ensure_sources_in_pool(raw_sources))
            if isinstance(text, str) and text.strip():
                try:
                    fmt_norm = (fmt or "").strip().lower()
                    if fmt_norm in {"markdown", "md", "text", "html"}:
                        sources_used.extend(extract_citation_sids_any(text))
                    elif fmt_norm in {"json", "yaml", "yml"}:
                        try:
                            payload = json.loads(text)
                        except Exception:
                            payload = None
                        if isinstance(payload, dict):
                            sidecar = payload.get("citations")
                            if isinstance(sidecar, list):
                                buf = []
                                for it in sidecar:
                                    if isinstance(it, dict) and isinstance(it.get("sids"), list):
                                        for x in it["sids"]:
                                            if isinstance(x, int):
                                                buf.append(x)
                                if buf:
                                    sources_used.extend(sorted(set(buf)))
                except Exception:
                    pass

            artifact = context.register_tool_result(
                artifact_id=artifact_name,
                tool_id=tool_id,
                value={"format": fmt, "content": text},
                summary="",
                artifact_type="inline",
                artifact_kind="display",
                visibility="external",
                sources_used=sources_used,
                inputs=tool_call.get("params") or {},
                call_record_rel=None,
                call_record_abs=None,
                error=None,
                content_lineage=[],
                tool_call_id=tool_call_id,
                tool_call_item_index=0,
                artifact_stats=None,
            )

            filename_hint = (spec.get("filename") if isinstance(spec, dict) else None)
            mime_hint = (spec.get("mime") if isinstance(spec, dict) else None)
            self._materialize_inline_artifact_to_file(
                context=context,
                artifact_id=artifact_name,
                artifact=artifact,
                workdir=pathlib.Path(state["outdir"]),
                filename_hint=filename_hint,
                mime_hint=mime_hint,
                visibility="external",
            )

            tag = f"artifact:name:{state.get('turn_id')}.{artifact_name}"
            context.add_event(
                kind="artifact_produced",
                data={
                    "artifact_id": artifact_name,
                    "tag": tag,
                    "summary": artifact.get("summary") or "",
                    "tool_id": tool_id,
                    "artifact_kind": artifact.get("artifact_kind"),
                    "visibility": artifact.get("visibility"),
                    "filename": (artifact.get("value") or {}).get("filename"),
                    "mime": (artifact.get("value") or {}).get("mime"),
                },
            )

            context.add_event(kind="tool_finished", data={
                "artifact_ids": [artifact_name],
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "produced_artifact_ids": [artifact_name],
                "error": None,
            })

            state["last_tool_result"] = []
            return state

        base_params = tool_call.get("params") or {}
        fetch_ctx = decision.get("param_binding") or []
        final_params, content_lineage = context.bind_params_with_sources(
            base_params=base_params,
            fetch_directives=fetch_ctx,
            tool_id=tool_id,
        )

        sig = format_tool_signature(
            tool_id,
            base_params,
            fetch_ctx,
            [a for a in (state.get("adapters") or []) if a.get("id") not in ("ctx_tools.fetch_ctx", "ctx_tools.fetch_turn_artifacts")],
        )

        declared_specs = tool_call.get("out_artifacts_spec") or []
        declared_names = [((a.get("name") or "").strip()) for a in declared_specs if isinstance(a, dict)]
        declared_mimes = [((a.get("mime") or "").strip()) for a in declared_specs if isinstance(a, dict)]

        context.add_event(
            kind="tool_started",
            data={
                "artifact_ids": declared_names,
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "signature": sig,
                "reasoning": tool_call.get("reasoning") or "",
            },
        )
        fmt_artifacts = []
        for n, m in zip(declared_names, declared_mimes):
            rec = f"{n} (file"
            rec += f"| {m})" if m else ")"
            fmt_artifacts.append(rec)
        self.scratchpad.tlog.solver(f"[react.tool] {tool_id} → {', '.join(fmt_artifacts) if fmt_artifacts else '?'}")

        workdir = pathlib.Path(state["workdir"])
        outdir = pathlib.Path(state["outdir"])

        exec_streamer = state.get("exec_code_streamer") if tools_insights.is_exec_tool(tool_id) else None
        exec_id = state.pop("pending_exec_id", None) if tools_insights.is_exec_tool(tool_id) else None
        if tools_insights.is_exec_tool(tool_id) and not exec_id:
            exec_id = f"exec_{tool_call.get('tool_call_id') or uuid.uuid4().hex[:8]}"
        use_llm_summary = True
        llm_service = self.svc if use_llm_summary else None
        param_bindings_for_summary = summary._render_param_bindings_for_summary(
            base_params=base_params,
            fetch_ctx=fetch_ctx,
            final_params=final_params,
        )
        adapter = next((a for a in (state.get("adapters") or []) if a.get("id") == tool_id), None)
        tool_doc_for_summary = (
            (adapter or {}).get("doc_for_llm")
            or (adapter or {}).get("doc")
            or (adapter or {}).get("description")
            or ""
        )
        try:
            from kdcube_ai_app.apps.chat.sdk.tools.ctx_tools import SourcesUsedStore

            records: List[Dict[str, Any]] = []
            declared_name_set = {n for n in declared_names if n}
            for name in declared_name_set:
                records.append({"artifact_name": name})

            if tools_insights.is_write_tool(tool_id):
                raw_path = final_params.get("path")
                filename = pathlib.Path(raw_path).name if isinstance(raw_path, str) and raw_path.strip() else ""
                if filename:
                    if declared_name_set:
                        for name in declared_name_set:
                            records.append({"artifact_name": name, "filename": filename})
                    else:
                        records.append({"filename": filename})

            if tools_insights.is_exec_tool(tool_id):
                for art in final_params.get("out_artifacts_spec") or []:
                    if not isinstance(art, dict):
                        continue
                    name = (art.get("name") or "").strip()
                    filename = (art.get("filename") or "").strip()
                    if name or filename:
                        records.append({"artifact_name": name or None, "filename": filename or None})

            if records:
                store = SourcesUsedStore()
                store.load()
                store.upsert(records)
        except Exception:
            pass

        t0 = time.perf_counter()
        tool_response = await execute_tool(
            tool_execution_context={
                **tool_call,
                "params": final_params,
                "call_signature": sig,
                "param_bindings_for_summary": param_bindings_for_summary,
                "tool_doc_for_summary": tool_doc_for_summary,
                **({"exec_id": exec_id} if exec_id else {}),
            },
            context=context,
            solution_gen_stream=self.solution_gen_stream,
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tool_manager.tools,
            logger=self.log,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            artifacts_contract=declared_specs,
            tool_call_id=tool_call_id,
            exec_streamer=exec_streamer,
        )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        self._append_react_timing(round_idx=int(state["iteration"]), stage="tool.call", elapsed_ms=elapsed_ms, tool_id=tool_id)
        items = tool_response.get("items") or []
        call_error = tool_response.get("error") if isinstance(tool_response, dict) else None

        tool_exec_status = "unknown"
        artifact = None
        tool_exec_error = None

        actual_artifact_ids = [
            tr.get("artifact_id")
            for tr in items
            if tr.get("artifact_id")
            and tr.get("status") == "success"
            and tr.get("output") is not None
            and not tr.get("error")
        ]
        if tool_call_id and tool_call_id in context.tool_call_index:
            context.tool_call_index[tool_call_id]["produced_artifact_ids"] = actual_artifact_ids or []

        hosted_files_to_emit: List[Dict[str, Any]] = []
        for tr in items:
            it_round = int(state["iteration"])

            artifact_id = tr.get("artifact_id")
            spec = next(iter([i for i in declared_specs if i.get("name") == artifact_id or ""]), None) or {}
            artifact_type = tr.get("artifact_type") or spec.get("type")
            artifact_kind = tr.get("artifact_kind") or spec.get("kind")
            if tools_insights.is_exploration_tool(tool_id):
                artifact_kind = "file"
            if not artifact_kind:
                artifact_kind = "file"

            artifact_summary = tr.get("summary") or ""
            tool_exec_output = tr.get("output")
            tool_exec_error = tr.get("error")
            tool_exec_status = tr.get("status") or "ok"
            summary_timing_ms = tr.get("summary_timing_ms")
            if isinstance(summary_timing_ms, (int, float)):
                self._append_react_timing(
                    round_idx=it_round,
                    stage="summary",
                    elapsed_ms=int(summary_timing_ms),
                    tool_id=tool_id,
                    artifact_id=artifact_id,
                )

            await emit_event(
                comm=self.comm,
                etype="solver.react.tool",
                title=f"ReAct Round ({it_round}): Tool Node ({tool_id})",
                step=f"react({it_round}).tool.{tool_id}",
                data={
                    "tool_doc": tool_doc_for_summary,
                    "call_params": param_bindings_for_summary,
                    "output": tool_exec_output,
                    "output_summary": artifact_summary,
                    "error": tool_exec_error,
                    "status": tool_exec_status,
                    "artifact_id": artifact_id,
                },
                agent=f"solver.react({it_round}).tool.{tool_id}",
            )

            if tool_exec_error:
                err_msg = f"[react.tool] Tool {tool_id} failed: {tool_exec_error.get('code')} - {tool_exec_error.get('message')}"
                self.log.log(err_msg, level="ERROR")
                context.add_event(kind="tool_error", data={
                    "artifact_id": artifact_id,
                    "tool_id": tool_id,
                    "tool_call_id": tool_call_id,
                    "error": tool_exec_error,
                })
                self.scratchpad.tlog.solver(err_msg)

            srcs_for_artifact: List[Any] = []
            if tools_insights.is_search_tool(tool_id):
                out = tr.get("output")
                if isinstance(out, list):
                    srcs_for_artifact = [r for r in out if isinstance(r, dict) and r.get("url")]
                    _ = await self._merge_with_pool(
                        context=context,
                        collections=[srcs_for_artifact],
                        workdir=workdir,
                        outdir=outdir,
                    )
                    srcs_for_artifact = context._extract_source_sids(
                        context.remap_sources_to_pool_sids(srcs_for_artifact)
                    )
                    context.add_event(kind="search_sources_merged", data={
                        "tool": tool_id,
                        "tool_call_id": tool_call_id,
                        "added": len(srcs_for_artifact),
                        "pool_size": len(context.sources_pool),
                    })
            elif tools_insights.is_fetch_uri_content_tool(tool_id):
                out = tr.get("output")
                data: Optional[Dict[str, Any]] = None

                if isinstance(out, str):
                    try:
                        data = json.loads(out)
                    except Exception:
                        data = None
                elif isinstance(out, dict):
                    data = out

                rows: list[dict] = []
                if isinstance(data, dict):
                    for url, payload in data.items():
                        if not isinstance(url, str) or not url.strip():
                            continue
                        if not isinstance(payload, dict):
                            continue

                        url = url.strip()
                        content = (payload.get("content") or "").strip()

                        row: dict = {
                            "url": url,
                        }
                        if content:
                            row["content"] = content

                        title = payload.get("title")
                        if isinstance(title, str) and title.strip():
                            row["title"] = title.strip()

                        for meta_key in (
                            "published_time_iso",
                            "modified_time_iso",
                            "fetched_time_iso",
                            "date_method",
                            "date_confidence",
                            "status",
                            "content_length",
                            "mime",
                            "base64",
                            "size_bytes",
                            "fetch_status",
                        ):
                            if meta_key in payload:
                                row[meta_key] = payload[meta_key]

                        rows.append(row)

                srcs_for_artifact = rows

                if srcs_for_artifact:
                    _ = await self._merge_with_pool(
                        context=context,
                        collections=[srcs_for_artifact],
                        workdir=workdir,
                        outdir=outdir,
                    )
                    srcs_for_artifact = context._extract_source_sids(
                        context.remap_sources_to_pool_sids(srcs_for_artifact)
                    )
                    context.add_event(kind="fetch_sources_merged", data={
                        "tool": tool_id,
                        "added": len(srcs_for_artifact),
                        "pool_size": len(context.sources_pool),
                    })

            elif tools_insights.does_tool_accept_sources(tool_id):
                raw = final_params.get("sources_list")
                if isinstance(raw, list):
                    srcs_for_artifact = raw or []

                if srcs_for_artifact:
                    srcs_for_artifact = context.ensure_sources_in_pool(srcs_for_artifact)

            if tools_insights.is_write_tool(tool_id):
                content_raw = final_params.get("content")
                content_text = unwrap_llm_content_payload(content_raw)
                if isinstance(content_text, str) and content_text.strip():
                    cited_sids = extract_citation_sids_any(content_text)
                    if cited_sids:
                        srcs_for_artifact.extend(cited_sids)

                    file_sources = resolve_cited_file_sources_from_content(
                        content_text,
                        outdir=outdir,
                        get_turn_log=context._get_turn_log,
                        produced_files=self.scratchpad.produced_files or [],
                    )
                    if file_sources:
                        file_sids = context.ensure_sources_in_pool(file_sources)
                        if file_sids:
                            srcs_for_artifact.extend(file_sids)

            tool_call_item_index = tr.get("tool_call_item_index") or None
            visibility = "external" if (tools_insights.is_exec_tool(tool_id) or tools_insights.is_write_tool(tool_id)) else "internal"
            artifact = context.register_tool_result(
                artifact_id=artifact_id,
                tool_id=tool_id,
                value=tool_exec_output,
                summary=artifact_summary,
                artifact_type=artifact_type,
                artifact_kind=artifact_kind,
                visibility=visibility,
                sources_used=srcs_for_artifact,
                inputs=tr.get("inputs"),
                call_record_rel=tr.get("call_record_rel"),
                call_record_abs=tr.get("call_record_abs"),
                error=tool_exec_error,
                content_lineage=content_lineage,
                tool_call_id=tool_call_id,
                tool_call_item_index=tool_call_item_index,
                artifact_stats=tr.get("artifact_stats") if isinstance(tr, dict) else None,
            )
            msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
            message_id = f"{_mid('artifact', msg_ts)}{'-' + artifact_id}"
            artifact["message_id"] = message_id

            self._materialize_inline_artifact_to_file(
                context=context,
                artifact_id=artifact_id,
                artifact=artifact,
                workdir=outdir,
                filename_hint=spec.get("filename"),
                mime_hint=spec.get("mime"),
                visibility=visibility,
            )

            tag = f"artifact:name:{state.get('turn_id')}.{artifact_id}"
            context.add_event(
                kind="artifact_produced",
                data={
                    "artifact_id": artifact_id,
                    "tag": tag,
                    "summary": artifact.get("summary") or "",
                    "tool_id": tool_id,
                    "artifact_kind": artifact.get("artifact_kind"),
                    "visibility": artifact.get("visibility"),
                    "filename": (artifact.get("value") or {}).get("filename"),
                    "mime": (artifact.get("value") or {}).get("mime"),
                },
            )

            artifact_stats = tr.get("artifact_stats") if isinstance(tr, dict) else None
            if tools_insights.is_write_tool(tool_id) and isinstance(artifact_stats, dict):
                file_path = ""
                if isinstance(artifact.get("value"), dict):
                    file_path = (artifact.get("value", {}).get("path") or "").strip()
                if artifact_stats.get("write_error"):
                    context.add_event(kind="error", data={
                        "reason": "write_tool_output_invalid",
                        "tool_id": tool_id,
                        "artifact_id": artifact_id,
                        "file_path": file_path,
                        "size_bytes": artifact_stats.get("size_bytes"),
                        "write_error": artifact_stats.get("write_error"),
                    })
                elif artifact_stats.get("write_warning"):
                    context.add_event(kind="note", data={
                        "reason": "write_tool_output_suspicious",
                        "tool_id": tool_id,
                        "artifact_id": artifact_id,
                        "file_path": file_path,
                        "size_bytes": artifact_stats.get("size_bytes"),
                        "write_warning": artifact_stats.get("write_warning"),
                    })
            is_file_artifact = bool(
                artifact_kind == "file"
                or (isinstance(artifact.get("value"), dict) and artifact.get("value", {}).get("type") == "file")
            )
            if is_file_artifact and self.hosting_service:
                svc = self.comm.service or {}
                host_iso = datetime.datetime.utcnow().isoformat() + "Z"
                t_host = time.perf_counter()
                hosted = await self.hosting_service.host_files_to_conversation(
                    rid=svc.get("request_id") or "",
                    files=[artifact],
                    outdir=outdir,
                    tenant=svc.get("tenant") or "",
                    project=svc.get("project") or "",
                    user=svc.get("user") or self.comm.user_id,
                    conversation_id=svc.get("conversation_id") or context.conversation_id or "",
                    user_type=svc.get("user_type") or self.comm.user_type or "",
                    turn_id=context.turn_id or "",
                )
                host_ms = int((time.perf_counter() - t_host) * 1000)
                self._append_react_timing(
                    round_idx=it_round,
                    stage="file.hosting",
                    elapsed_ms=host_ms,
                    tool_id=tool_id,
                    artifact_id=artifact_id,
                )
                if hosted:
                    hosted_uri = hosted[0].get("hosted_uri") or ""
                    if isinstance(artifact.get("value"), dict) and hosted_uri:
                        artifact["value"]["hosted_uri"] = hosted_uri
                        artifact["value"]["key"] = hosted[0].get("key")
                        artifact["value"]["rn"] = hosted[0].get("rn")
                        artifact["value"]["local_path"] = hosted[0].get("local_path")
                    if hosted_uri:
                        artifact["hosted_uri"] = hosted_uri
                        artifact["rn"] = hosted[0].get("rn")

                    try:
                        for h in hosted:
                            if not isinstance(h, dict):
                                continue
                            h_uri = (h.get("hosted_uri") or "").strip()
                            if not h_uri:
                                continue
                            local_path = (h.get("local_path") or "").strip()
                            rel_path = ""
                            if local_path:
                                try:
                                    rel_path = str(pathlib.Path(local_path).resolve().relative_to(outdir))
                                except Exception:
                                    rel_path = ""
                            filename = (h.get("filename") or "").strip() or pathlib.Path(local_path).name
                            updated = False
                            for src in (context.sources_pool or []):
                                if not isinstance(src, dict):
                                    continue
                                src_type = (src.get("source_type") or "")
                                if src_type not in ("file", "attachment"):
                                    continue
                                if rel_path and (src.get("local_path") == rel_path or src.get("url") == rel_path):
                                    if h.get("rn"):
                                        src["url"] = h.get("rn")
                                        src["rn"] = h.get("rn")
                                    else:
                                        src["url"] = h_uri
                                    if rel_path:
                                        src["local_path"] = rel_path
                                    art_name = (h.get("slot") or h.get("artifact_name") or "").strip()
                                    if art_name:
                                        src["artifact_path"] = f"{context.turn_id}.files.{art_name}"
                                    if h.get("mime"):
                                        src["mime"] = h.get("mime")
                                    hosted_title = (h.get("description") or "").strip() or (h.get("artifact_name") or "").strip()
                                    if hosted_title:
                                        src["title"] = hosted_title
                                    elif filename and not src.get("title"):
                                        src["title"] = filename
                                    if h.get("size") is not None:
                                        src["size_bytes"] = h.get("size")
                                    updated = True
                                    break
                            if not updated:
                                hosted_title = (h.get("description") or "").strip() or (h.get("artifact_name") or "").strip()
                                row = {
                                    "url": h.get("rn") or h_uri,
                                    "title": hosted_title or filename or h_uri,
                                    "source_type": "file",
                                }
                                if rel_path:
                                    row["local_path"] = rel_path
                                if h.get("rn"):
                                    row["rn"] = h.get("rn")
                                art_name = (h.get("slot") or h.get("artifact_name") or "").strip()
                                if art_name:
                                    row["artifact_path"] = f"{context.turn_id}.files.{art_name}"
                                if h.get("mime"):
                                    row["mime"] = h.get("mime")
                                if h.get("size") is not None:
                                    row["size_bytes"] = h.get("size")
                                context.ensure_sources_in_pool([row])
                    except Exception:
                        pass

                    hosted_files_to_emit.extend(hosted)
                    try:
                        if hasattr(self.scratchpad, "add_produced_file"):
                            value_obj = artifact.get("value") if isinstance(artifact.get("value"), dict) else {}
                            summary_text = (artifact.get("summary") or "").strip()
                            desc = (artifact.get("description") or spec.get("description") or "")
                            sources_used = artifact.get("sources_used") or []
                            used_sids = []
                            if isinstance(sources_used, list):
                                for s in sources_used:
                                    if isinstance(s, dict):
                                        sid = s.get("sid")
                                        if isinstance(sid, (int, float)) and int(sid) not in used_sids:
                                            used_sids.append(int(sid))
                                    elif isinstance(s, (int, float)) and int(s) not in used_sids:
                                        used_sids.append(int(s))
                            for h in hosted:
                                file_item = {
                                    "mid": message_id,
                                    "artifact_id": artifact_id,
                                    "artifact_name": artifact_id or h.get("slot") or h.get("filename"),
                                    "slot": h.get("slot"),
                                    "filename": value_obj.get("filename") or h.get("filename") or "",
                                    "mime": value_obj.get("mime") or h.get("mime") or "",
                                    "size": h.get("size"),
                                    "hosted_uri": h.get("hosted_uri"),
                                    "key": h.get("key"),
                                    "rn": h.get("rn"),
                                    "ts": host_iso,
                                    "text": value_obj.get("text") or "",
                                    "summary": summary_text,
                                    "visibility": "external" if (tools_insights.is_exec_tool(tool_id) or tools_insights.is_write_tool(tool_id)) else "internal",
                                    "kind": (artifact.get("artifact_kind") or "file"),
                                    "used_sids": used_sids,
                                    "tool_id": artifact.get("tool_id") or h.get("tool_id") or "",
                                    "description": desc or h.get("description") or "",
                                }
                                self.scratchpad.add_produced_file(file_item)
                                try:
                                    name = (file_item.get("artifact_name") or "file").strip()
                                    fname = (file_item.get("filename") or "").strip()
                                    mime = (file_item.get("mime") or "").strip()
                                    size = file_item.get("size")
                                    parts = [name]
                                    if fname:
                                        parts.append(f"filename={fname}")
                                    if mime:
                                        parts.append(f"mime={mime}")
                                    if size is not None:
                                        parts.append(f"size={size}")
                                    self.scratchpad.tlog.solver("FILE: " + " | ".join(parts))
                                except Exception:
                                    pass
                    except Exception:
                        pass

            log_entry = {
                "type": "tool_execution",
                "iteration": state["iteration"],
                "timestamp": time.time(),
                "tool_id": tool_id,
                "params_summary": self._summarize_params(final_params),
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "artifact_kind": artifact_kind,
                "signature": sig,
                "result_summary": artifact.get("summary", ""),
                "status": tool_exec_status,
            }
            if tool_exec_error:
                log_entry["error"] = tool_exec_error
                details = tool_exec_error.get("details") if isinstance(tool_exec_error, dict) else None
                if isinstance(details, dict):
                    tail = details.get("stderr_tail") or details.get("errors_log_tail")
                    if isinstance(tail, str) and tail.strip():
                        log_entry["stderr_tail"] = tail[-1000:]
            if call_error:
                log_entry["call_error"] = call_error
            if declared_names:
                log_entry["planned_artifact_ids"] = declared_names
            if tool_call_id:
                log_entry["tool_call_id"] = tool_call_id
            if tool_call_item_index is not None:
                log_entry["tool_call_item_index"] = tool_call_item_index
            state["session_log"].append(log_entry)

        # Emit only exec and write_* tool artifacts to user; others stay hosted but not emitted.
        should_emit = tools_insights.is_exec_tool(tool_id) or tools_insights.is_write_tool(tool_id)
        if hosted_files_to_emit and self.hosting_service and should_emit:
            await self.hosting_service.emit_solver_artifacts(
                files=hosted_files_to_emit, citations=[]
            )

        finish_data = {
            "artifact_ids": actual_artifact_ids or declared_names,
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "signature": sig,
            "status": tool_exec_status,
            "planned_artifact_ids": declared_names,
            "produced_artifact_ids": actual_artifact_ids or [],
        }
        if tool_exec_error:
            finish_data["error"] = {
                "code": tool_exec_error.get("code"),
                "message": tool_exec_error.get("message"),
            }
        elif isinstance(call_error, dict):
            finish_data["error"] = {
                "code": call_error.get("code") or call_error.get("error") or "tool_failed",
                "message": (call_error.get("message") or call_error.get("description") or ""),
            }
        context.add_event(kind="tool_finished", data=finish_data)

        strategy = (decision.get("strategy") or "").strip()
        if strategy == "explore" and not tools_insights.is_write_tool(tool_id):
            state["explore_used"] = int(state.get("explore_used") or 0) + 1
        if strategy == "exploit" and not tools_insights.is_write_tool(tool_id):
            state["exploit_used"] = int(state.get("exploit_used") or 0) + 1
        bs = getattr(context, "budget_state_v2", None)
        if bs is not None:
            bs.explore_used = int(state.get("explore_used") or 0)
            bs.exploit_used = int(state.get("exploit_used") or 0)

        state["last_tool_result"] = items
        return state

    def _materialize_inline_artifact_to_file(
        self,
        *,
        context: ReactContext,
        artifact_id: str,
        artifact: Dict[str, Any],
        workdir: pathlib.Path,
        filename_hint: Optional[str] = None,
        mime_hint: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> None:
        value = artifact.get("value")
        # If already marked as file, ensure the file exists on disk.
        if isinstance(value, dict) and value.get("type") == "file":
            path = value.get("path")
            if isinstance(path, str) and path.strip():
                file_path = pathlib.Path(path)
                if not file_path.exists():
                    text = value.get("text")
                    if text is None:
                        text = value.get("content")
                    if text is None:
                        try:
                            text = json.dumps(value, ensure_ascii=False, indent=2)
                        except Exception:
                            text = ""
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(str(text), encoding="utf-8")
            return
        if (artifact.get("artifact_kind") or "").strip() == "file":
            return

        text = None
        fmt = None
        if isinstance(value, dict):
            fmt = value.get("format") if isinstance(value.get("format"), str) else None
            if isinstance(value.get("content"), str):
                text = value.get("content")
            elif isinstance(value.get("text"), str):
                text = value.get("text")
        if text is None and isinstance(value, str):
            text = value

        if text is None:
            try:
                text = json.dumps(value, ensure_ascii=False, indent=2)
                fmt = fmt or "json"
            except Exception:
                return

        ext = "txt"
        if isinstance(fmt, str):
            f = fmt.strip().lower()
            if f in {"md", "markdown"}:
                ext = "md"
            elif f in {"json"}:
                ext = "json"
            elif f in {"html", "htm"}:
                ext = "html"
            elif f in {"yaml", "yml"}:
                ext = "yaml"

        files_dir = workdir
        files_dir.mkdir(parents=True, exist_ok=True)
        filename = str(filename_hint).strip() if isinstance(filename_hint, str) and filename_hint.strip() else ""
        if not filename:
            filename = f"{artifact_id}.{ext}"
        elif "." not in filename:
            filename = f"{filename}.{ext}"
        file_path = files_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")

        mime = str(mime_hint).strip() if isinstance(mime_hint, str) and mime_hint.strip() else ""
        if not mime:
            if ext == "md":
                mime = "text/markdown"
            elif ext == "json":
                mime = "application/json"
            elif ext == "html":
                mime = "text/html"
            elif ext == "yaml":
                mime = "application/x-yaml"
            else:
                mime = "text/plain"

        if not (artifact.get("artifact_kind") or "").strip():
            artifact["artifact_kind"] = "file"
        if visibility:
            artifact["visibility"] = visibility
        artifact["value"] = {
            "type": "file",
            "path": str(file_path),
            "text": text,
            "mime": mime,
            "filename": filename,
        }
        context.artifacts[artifact_id] = artifact
        context.persist()

        try:
            self.scratchpad.add_produced_file(
                {
                    "filename": filename,
                    "artifact_name": artifact_id,
                    "mime": mime,
                    "size": len(text.encode("utf-8")),
                    "summary": artifact.get("summary") or "",
                    "visibility": visibility or "internal",
                    "kind": (artifact.get("artifact_kind") or "file"),
                }
            )
        except Exception:
            pass

    async def _exit_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reason = state.get("exit_reason", "complete")
        state["context"].add_event(kind="exit", data={
            "reason": reason,
            "clarification": state.get("clarification_questions"),
        })
        state["session_log"].append({
            "type": "exit",
            "timestamp": time.time(),
            "reason": reason,
            "clarification": state.get("clarification_questions"),
        })
        return state

    def _summarize_params(self, params: Dict[str, Any]) -> str:
        if not params:
            return "{}"
        summary_obj = {}
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 100:
                summary_obj[k] = f"{v[:97]}..."
            elif isinstance(v, (list, dict)):
                summary_obj[k] = f"<{type(v).__name__} len={len(v)}>"
            else:
                summary_obj[k] = v
        return json.dumps(summary_obj, ensure_ascii=False)[:200]

    def _adapters_index(self, adapters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
        for a in adapters or []:
            tid = (a.get("id") or "").strip()
            if tid:
                idx[tid] = a
        return idx

    def _append_react_timing(
        self,
        *,
        round_idx: int,
        stage: str,
        elapsed_ms: Optional[int],
        tool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
    ) -> None:
        if elapsed_ms is None:
            return
        if not getattr(self, "scratchpad", None):
            return
        timings = getattr(self.scratchpad, "timings", None)
        if not isinstance(timings, list):
            return
        title = f"react.{round_idx}.{stage}"
        if tool_id:
            title += f".{tool_id}"
        if artifact_id:
            title += f".{artifact_id}"
        timings.append({"title": title, "elapsed_ms": int(elapsed_ms)})

    async def _merge_with_pool(
        self,
        *,
        context: ReactContext,
        collections: List[List[Dict[str, Any]]],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
    ) -> List[Dict[str, Any]]:
        params = {
            "source_collections": [context.sources_pool] + [c or [] for c in collections]
        }
        tr = await execute_tool(
            tool_execution_context={"tool_id": "ctx_tools.merge_sources", "params": params},
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tool_manager.tools,
            logger=self.log,
            context=context,
        )
        items = tr.get("items") or []
        first_item = next(iter(items), None)
        merged = first_item.get("output") if isinstance(first_item, dict) else None
        if not isinstance(merged, list):
            merged = []
        if merged:
            turn_id = context.turn_id or ""
            if turn_id:
                for row in merged:
                    if isinstance(row, dict) and not row.get("turn_id"):
                        row["turn_id"] = turn_id

        if not merged and collections:
            self.log.log(
                f"[react.merge_sources] empty merge result; "
                f"inputs={[len(c or []) for c in collections]} "
                f"raw_type={type(merged).__name__}",
                level="WARNING",
            )
        else:
            try:
                context.set_sources_pool(merged, persist=False)
            except Exception:
                context.sources_pool = merged
        if merged:
            try:
                mx = max(int(s.get("sid") or 0) for s in merged if isinstance(s, dict))
                if mx > context.max_sid:
                    context.max_sid = mx
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
                    val = context.max_sid
                    try:
                        next_sid = int(val) + 1
                    except (TypeError, ValueError):
                        next_sid = 0

                    SOURCE_ID_CV.set({"next": next_sid})
                except Exception:
                    pass
            except Exception:
                pass
        return merged

    async def _analyze_and_build_result(self, state: Dict[str, Any]) -> dict:
        context: ReactContext = state["context"]
        outdir = pathlib.Path(state["outdir"])
        workdir = pathlib.Path(state["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        sources_pool = [s for s in (context.sources_pool or []) if isinstance(s, dict)]
        sources_pool.sort(key=lambda s: int(s.get("sid") or 0))

        try:
            used_sids: set[int] = set()
            for art in (context.artifacts or {}).values():
                if not isinstance(art, dict):
                    continue
                for sid in context._extract_source_sids(art.get("sources_used")):
                    if isinstance(sid, int):
                        used_sids.add(sid)
            for row in sources_pool:
                if not isinstance(row, dict):
                    continue
                sid = row.get("sid")
                if isinstance(sid, (int, float)):
                    row["used"] = int(sid) in used_sids
            context._sources_pool = sources_pool
            context.persist()
        except Exception:
            pass

        artifacts_out: List[Dict[str, Any]] = []
        try:
            for aid, art in (context.artifacts or {}).items():
                if not isinstance(art, dict):
                    continue
                value = art.get("value")
                is_file = bool(
                    (art.get("artifact_kind") or "").strip() == "file"
                    or (isinstance(value, dict) and value.get("type") == "file")
                )
                output_obj: Dict[str, Any] = {}
                fmt = ""
                mime = ""
                filename = ""
                if is_file:
                    if isinstance(value, dict):
                        output_obj["path"] = value.get("path") or ""
                        output_obj["text"] = value.get("text") or ""
                        mime = value.get("mime") or ""
                        filename = value.get("filename") or ""
                else:
                    if isinstance(value, dict):
                        fmt = value.get("format") or ""
                        text = value.get("text")
                        if text is None:
                            text = value.get("content")
                        if text is None:
                            try:
                                text = json.dumps(value, ensure_ascii=False, indent=2)
                            except Exception:
                                text = ""
                        output_obj["text"] = text
                    elif isinstance(value, str):
                        output_obj["text"] = value
                    else:
                        try:
                            output_obj["text"] = json.dumps(value, ensure_ascii=False, indent=2)
                        except Exception:
                            output_obj["text"] = ""

                rec = {
                    "resource_id": f"artifact:{aid}",
                    "artifact_id": aid,
                    "type": "file" if is_file else "inline",
                    "tool_id": art.get("tool_id") or "",
                    "output": output_obj,
                    "description": art.get("description") or "",
                    "summary": art.get("summary") or "",
                    "sources_used": art.get("sources_used") or [],
                }
                if fmt:
                    rec["format"] = fmt
                if mime:
                    rec["mime"] = mime
                if filename:
                    rec["filename"] = filename
                artifacts_out.append(rec)
        except Exception:
            pass

        def _extract_completion_from_events(events: List[Dict[str, Any]]) -> str:
            for ev in reversed(events or []):
                if not isinstance(ev, dict):
                    continue
                if (ev.get("kind") or "") != "decision":
                    continue
                data = ev.get("data") or {}
                if not isinstance(data, dict):
                    continue
                action = (data.get("action") or "").strip().lower()
                if action not in {"complete", "exit"}:
                    continue
                summary = (data.get("completion_summary") or "").strip()
                if summary:
                    return summary
            return ""

        completion_summary = _extract_completion_from_events(context.events or [])

        def _tool_call_log(session_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for entry in (session_log or []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "tool_execution":
                    continue
                out.append({
                    "iteration": entry.get("iteration"),
                    "timestamp": entry.get("timestamp"),
                    "tool_id": entry.get("tool_id"),
                    "params_summary": entry.get("params_summary"),
                    "artifact_id": entry.get("artifact_id"),
                    "artifact_kind": entry.get("artifact_kind"),
                    "artifact_type": entry.get("artifact_type"),
                    "signature": entry.get("signature"),
                    "status": entry.get("status"),
                    "result_summary": entry.get("result_summary"),
                    "error": entry.get("error") or entry.get("call_error"),
                    "tool_call_id": entry.get("tool_call_id"),
                    "tool_call_item_index": entry.get("tool_call_item_index"),
                })
            return out

        solver_payload = {
            "ok": True,
            "out": artifacts_out,
            "sources_pool": sources_pool,
            "react_events": context.events if isinstance(context.events, list) else [],
            "react": {
                "events": context.events if isinstance(context.events, list) else [],
                "artifacts": context.artifacts if isinstance(context.artifacts, dict) else {},
                "completion_summary": completion_summary,
                "error": state.get("error"),
                "plan_steps": state.get("plan_steps") or [],
                "plan_status": state.get("plan_status") or {},
            },
            "tool_calls": _tool_call_log(state.get("session_log") or []),
            "react_timings": {
                "rounds": state.get("round_timings") or [],
                "total_runtime_sec": float(state.get("total_runtime_sec") or 0.0),
            },
            "run_id": state.get("session_id"),
            "outdir": str(outdir),
            "workdir": str(workdir),
            "mode": "react_loop",
        }

        if state.get("exit_reason") == "clarify":
            solver_payload["clarification_questions"] = state.get("clarification_questions") or []

        (outdir / "result.json").write_text(
            json.dumps(solver_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "solver_payload": solver_payload,
            "outdir": str(outdir),
            "workdir": str(workdir),
            "run_id": state["session_id"],
            "mode": "react_loop",
        }

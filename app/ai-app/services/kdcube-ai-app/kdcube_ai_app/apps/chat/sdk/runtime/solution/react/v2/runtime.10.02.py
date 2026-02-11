# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/runtime.py

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
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import emit_event
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.execution import execute_tool, _safe_label
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.agents.decision import react_decision_stream_v2
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.agents.coordinator import UnifiedCoordinatorOutV2
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.budget import BudgetStateV2
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.solution_workspace import ApplicationHostingService
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.canvas import ToolContentStreamer
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.infra.service_hub.errors import ServiceException, is_context_limit_error

from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights


@dataclass
class ReactStateV2:
    session_id: str
    turn_id: str

    exploration_budget: int
    exploitation_budget: int

    adapters: List[Dict[str, Any]]

    workdir: pathlib.Path
    outdir: pathlib.Path
    budget_state_v2: BudgetStateV2

    plan_steps: List[str]
    plan_status: Dict[str, str] = field(default_factory=dict)

    coordinator_turn_line: str = ""
    # next_decision_model: str = "strong"

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
        hosting_service: Optional[ApplicationHostingService] = None,
        ctx_browser: Optional[ContextBrowser] = None,
    ) -> None:
        self.svc = service
        self.log = logger
        self.tool_manager = tool_manager
        self.skills_subsystem = skills_subsystem
        self.scratchpad = scratchpad
        self.comm = comm
        self.comm_context = comm_context
        self.hosting_service = hosting_service
        self.ctx_browser = ctx_browser
        self.graph = self._build_graph()
        self._timeline_text_idx = {}
        self._outdir_cv_token = None
        self._workdir_cv_token = None

    async def _render_timeline_with_announce(
        self,
        *,
        budget_state_v2: BudgetStateV2,
        plan_steps: List[str],
        exploration_budget: int,
        exploitation_budget: int,
        explore_used: int = 0,
        exploit_used: int = 0,
        iteration: int,
        max_iterations: int,
        force_sanitize: bool = False,
    ) -> List[Dict[str, Any]]:
        if not self.ctx_browser:
            return []
        try:
            lines = [
                "[ACTIVE STATE]",
                f"- iteration: {int(iteration)}/{int(max_iterations)}",
            ]
            strategies = []
            bs = budget_state_v2
            if bs and hasattr(bs, "strategies"):
                try:
                    bs.ensure_strategies()
                    for s in bs.strategies:
                        strategies.append({
                            "name": getattr(s, "name", ""),
                            "budget": int(getattr(s, "budget", 0) or 0),
                            "used": int(getattr(s, "used", 0) or 0),
                        })
                except Exception:
                    strategies = []
            if not strategies:
                strategies = [
                    {"name": "explore", "budget": int(exploration_budget or 0), "used": int(explore_used or 0)},
                    {"name": "exploit", "budget": int(exploitation_budget or 0), "used": int(exploit_used or 0)},
                ]
            if strategies:
                lines.append("- strategies:")
                for s in strategies:
                    try:
                        name = s.get("name") or ""
                        budget = int(s.get("budget") or 0)
                        used = int(s.get("used") or 0)
                        remaining = max(0, budget - used)
                        lines.append(f"  • {name}: {used}/{budget} used (remaining {remaining})")
                    except Exception:
                        continue
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import collect_plan_snapshots, PlanSnapshot
                plans_by_id, order = collect_plan_snapshots(self.ctx_browser.timeline.blocks)
                if order:
                    lines.append("- plans:")
                    for idx, pid in enumerate(order, start=1):
                        plan = plans_by_id.get(pid) or {}
                        steps = list(plan.get("steps") or [])
                        status = plan.get("status") or {}
                        created_ts = (plan.get("created_ts") or "").strip()
                        last_ts = (plan.get("last_ts") or "").strip()
                        header = f"  • plan #{idx}"
                        if idx == len(order):
                            header += " (current)"
                        if last_ts:
                            header += f" last={last_ts}"
                        elif created_ts:
                            header += f" ts={created_ts}"
                        lines.append(header)
                        for i, step in enumerate(steps, start=1):
                            mark = "✓" if status.get(str(i)) == "done" else "✗" if status.get(str(i)) == "failed" else "□"
                            lines.append(f"    {mark} [{i}] {step}")
                    last_payload = plans_by_id.get(order[-1]) or {}
                    snap = PlanSnapshot.from_any(last_payload)
                    if snap:
                        summary = snap.plan_summary()
                        lines.append(
                            f"- plan_status: done={summary.get('done')} failed={summary.get('failed')} pending={summary.get('pending')}"
                        )
                        lines.append(f"- plan_complete: {str(bool(summary.get('complete'))).lower()}")
                        if snap.last_ts:
                            lines.append(f"- plan_last_update: {snap.last_ts}")
            except Exception:
                pass
            active_block = "\n".join(lines) + "\n"
            self.ctx_browser.announce(
                blocks=[{"text": active_block}],
            )
        except Exception:
            pass
        return await self.ctx_browser.timeline.render(
            cache_last=True,
            force_sanitize=force_sanitize,
            include_sources=True,
            include_announce=True,
        )

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

    def _mk_content_streamer(
        self,
        phase: str,
        *,
        sources_list: Optional[List[Dict[str, object]]] = None,
        artifact_name: Optional[str] = None,
    ) -> tuple[Callable[[str], Awaitable[None]], ToolContentStreamer]:
        safe_name = artifact_name or f"react.record.{uuid.uuid4().hex[:8]}"
        streamer = ToolContentStreamer(
            emit_delta=self.comm.delta,
            agent=f"{self.MODULE_AGENT_NAME}.{phase}",
            artifact_name=safe_name,
            sources_list=sources_list or [],
            turn_id=(self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser and self.ctx_browser.runtime_ctx else ""),
            stream_tool_id="react.write",
            write_tool_prefix="rendering_tools.write_",
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
        if code.startswith("invalid_action"):
            return f"Action '{action}' is not allowed. Allowed: call_tool | complete | exit | clarify."
        if code == "completion_summary_not_allowed":
            return f"completion_summary is only allowed for complete/exit. action={action}."
        if code == "missing_tool_id":
            return "tool_call.tool_id is missing for action=call_tool."
        if code == "missing_contract":
            return f"exec tool requires params.contract (tool_id={tool_id or 'unknown'})."
        if code == "tool_call_invalid":
            return f"tool_call failed protocol validation for tool_id={tool_id or 'unknown'}."
        if code == "tool_signature_red":
            return f"tool params failed signature validation for tool_id={tool_id or 'unknown'}."
        if completion_summary:
            return f"completion_summary present with action={action}."
        if extra:
            try:
                return f"Protocol violation: {code}. extra={self._short_json(extra)}"
            except Exception:
                return f"Protocol violation: {code}."
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
        if tool_id == "react.read":
            return None
        if tool_id == "react.write":
            params = tool_call.get("params") or {}
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    return "path_not_first"
                if len(keys) >= 2 and keys[1] != "channel":
                    return "channel_not_second"
                if len(keys) >= 3 and keys[2] != "content":
                    return "content_not_third"
                if len(keys) >= 4 and keys[3] != "kind":
                    return "kind_not_fourth"
        return None

    def _validate_tool_call_protocol(
        self,
        *,
        tool_call: Any,
        adapters_by_id: Dict[str, Dict[str, Any]],
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
            if tool_id not in adapters_by_id and tool_id not in {"react.read", "react.write"}:
                violations.append({
                    "code": "unknown_tool_id",
                    "message": f"tool_id '{tool_id}' is not in adapters/available tools",
                    "tool_id": tool_id,
                })
        allowed_params: set[str] = set()
        if tool_id in {"react.read", "react.write"}:
            if tool_id == "react.read":
                allowed_params.update({"paths"})
            else:
                allowed_params.update({"path", "channel", "content", "kind"})
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

        artifact_specs: List[Dict[str, Any]] = []
        if tools_insights.is_exec_tool(tool_id):
            contract = params.get("contract") if isinstance(params, dict) else None
            if not isinstance(contract, list) or not contract:
                violations.append({
                    "code": "missing_contract",
                    "message": "exec_tools.execute_code_python requires params.contract",
                    "tool_id": tool_id,
                })
            else:
                for a in contract:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get("name") or "").strip()
                    if not nm:
                        continue
                    rec = {"name": nm, "kind": "file"}
                    if a.get("filename"):
                        rec["filename"] = a.get("filename")
                    if a.get("description"):
                        rec["description"] = a.get("description")
                    artifact_specs.append(rec)
        if tool_id == "react.write":
            if isinstance(params, dict) and params:
                keys = list(params.keys())
                if len(keys) >= 1 and keys[0] != "path":
                    violations.append({
                        "code": "path_not_first",
                        "message": "react.write params must start with path",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 2 and keys[1] != "channel":
                    violations.append({
                        "code": "channel_not_second",
                        "message": "react.write params must have channel as the second field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 3 and keys[2] != "content":
                    violations.append({
                        "code": "content_not_third",
                        "message": "react.write params must have content as the third field",
                        "tool_id": tool_id,
                    })
                if len(keys) >= 4 and keys[3] != "kind":
                    violations.append({
                        "code": "kind_not_fourth",
                        "message": "react.write params must have kind as the fourth field",
                        "tool_id": tool_id,
                    })
        if tool_id == "react.read":
            if isinstance(params, dict):
                if not isinstance(params.get("paths"), list):
                    violations.append({
                        "code": "bad_params",
                        "message": "react.read params.paths must be a list",
                        "tool_id": tool_id,
                    })
        # ref: bindings validated separately (need visibility + sources_pool rules)

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
    ) -> ReactStateV2:
        outdir = pathlib.Path(self.ctx_browser.runtime_ctx.outdir or "")
        workdir = pathlib.Path(self.ctx_browser.runtime_ctx.workdir or "")
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import set_active_skills_subsystem
            if self.skills_subsystem:
                set_active_skills_subsystem(self.skills_subsystem)
        except Exception:
            pass

        session_id = f"react-v2-{uuid.uuid4().hex[:8]}"
        turn_id = self.ctx_browser.runtime_ctx.turn_id
        self.log.log(f"[react.v2] Start {session_id} in {workdir}")

        adapters = await self.tool_manager.tools.react_tools(
            allowed_plugins=allowed_plugins,
            allowed_ids=None,
        )

        plan = coordinator_out.plan
        plan_steps = list(plan.steps or [])
        exploration_budget = int(plan.exploration_budget or 0)
        exploitation_budget = int(plan.exploitation_budget or 0)
        try:
            mode = str(getattr(plan, "mode", None) or (plan.get("mode") if isinstance(plan, dict) else "") or "new").strip()
            if mode == "close":
                plan_steps = []
            elif mode == "active":
                # reuse latest plan from timeline (if any)
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import collect_plan_snapshots, PlanSnapshot
                    plans_by_id, order = collect_plan_snapshots(self.ctx_browser.timeline.blocks)
                    if order:
                        last_payload = plans_by_id.get(order[-1]) or {}
                        snap = PlanSnapshot.from_any(last_payload)
                        if snap:
                            plan_steps = list(snap.steps or [])
                except Exception:
                    pass
            else:
                pass
        except Exception:
            pass

        # initial plan block emitted above when mode=new|update

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

        return ReactStateV2(
            session_id=session_id,
            turn_id=turn_id,
            plan_steps=plan_steps,
            plan_status={},
            exploration_budget=exploration_budget,
            exploitation_budget=exploitation_budget,
            adapters=adapters,
            workdir=workdir,
            outdir=outdir,
            budget_state_v2=bs_v2,
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
    ) -> dict:
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent=self.MODULE_AGENT_NAME,
        )
        await turn_status.send(random.choice(["executing", "working", "solving"]))

        state = await self.prepare_session(
            coordinator_out=coordinator_out,
            allowed_plugins=allowed_plugins,
        )

        start_ts = time.time()
        try:
            recursion_limit = max(20, (int(state.max_iterations) * 3) + 6)
            final_state = await self.graph.ainvoke(self._to_dict(state), config={"recursion_limit": recursion_limit})
        except Exception as exc:
            self.log.log(f"[react.v2] Graph error: {exc}", level="ERROR")
            try:
                if self.ctx_browser:
                    self.ctx_browser.contribute_notice(
                        code="graph_error",
                        message=str(exc),
                        extra={"where": "react.v2"},
                    )
            except Exception:
                pass
            final_state = self._to_dict(state)
            final_state["exit_reason"] = "error"
            final_state["error"] = {
                "where": "react.v2",
                "error": "graph_error",
                "message": str(exc),
                "managed": True,
            }
        finally:
            # workspace is managed by ContextBrowser; no CV reset here
            self._outdir_cv_token = None
            self._workdir_cv_token = None

        try:
            final_state["total_runtime_sec"] = float(time.time() - start_ts)
        except Exception:
            pass

        return await self._analyze_and_build_result(final_state)

    def _to_dict(self, s: ReactStateV2) -> Dict[str, Any]:
        strategies = []
        try:
            bs = getattr(s, "budget_state_v2", None)
            if bs:
                bs.ensure_strategies()
                for st in bs.strategies:
                    strategies.append({
                        "name": getattr(st, "name", ""),
                        "budget": int(getattr(st, "budget", 0) or 0),
                        "used": int(getattr(st, "used", 0) or 0),
                    })
        except Exception:
            strategies = []
        return {
            "session_id": s.session_id,
            "turn_id": s.turn_id,
            "plan_steps": s.plan_steps,
            "plan_status": s.plan_status,
            "exploration_budget": s.exploration_budget,
            "exploitation_budget": s.exploitation_budget,
            "strategies": strategies,
            "adapters": s.adapters,
            "workdir": str(s.workdir),
            "outdir": str(s.outdir),
            "budget_state_v2": s.budget_state_v2,
            "coordinator_turn_line": s.coordinator_turn_line,
            # "next_decision_model": s.next_decision_model,
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
        iteration = int(state.get("iteration") or 0)
        if iteration >= int(state.get("max_iterations") or 0):
            state["exit_reason"] = "max_iterations"
            return state
        try:
            return await self._decision_node_impl(state, iteration)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.log.log(f"[react.v2] decision_node error: {exc}\n{tb}", level="ERROR")
                self.log.log(
                    "[react.v2] decision_node context: show_artifacts=0 types=[]",
                    level="ERROR",
                )
            except Exception:
                pass
            raise

    async def _decision_node_impl(self, state: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        announced_adapters = [
            a for a in (state.get("adapters") or [])
            if a.get("id") not in tools_insights.CODEGEN_ONLY_TOOL_IDS and not tools_insights.is_code_tool(a["id"])
        ]
        extra_adapters = [a for a in (state.get("adapters") or []) if tools_insights.is_code_tool(a["id"])]
        extra_adapters_for_decision = [
            a for a in extra_adapters if not tools_insights.is_codegen_tool(a["id"])
        ]

        user_blocks = await self._render_timeline_with_announce(
            budget_state_v2=state.get("budget_state_v2") or BudgetStateV2(),
            plan_steps=state.get("plan_steps") or [],
            exploration_budget=int(state.get("exploration_budget") or 0),
            exploitation_budget=int(state.get("exploitation_budget") or 0),
            explore_used=int(state.get("explore_used") or 0),
            exploit_used=int(state.get("exploit_used") or 0),
            iteration=iteration,
            max_iterations=int(state.get("max_iterations") or 0),
        )
        model_kind = state.get("next_decision_model") or "strong"
        role = f"{self.MODULE_AGENT_NAME}.{self.DECISION_AGENT_NAME}.{model_kind}"
        async with with_accounting(
            self.ctx_browser.runtime_ctx.bundle_id,
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
            sources_list = []
            try:
                if self.ctx_browser:
                    sources_list = list(self.ctx_browser.sources_pool or [])
            except Exception:
                sources_list = []
        record_streamer_fn, record_streamer_widget = self._mk_content_streamer(
            f"decision.record ({iteration})",
            sources_list=sources_list,
            artifact_name=f"react.record.{pending_tool_call_id}",
        )
        try:
            if record_streamer_widget and hasattr(record_streamer_widget, "update_sources"):
                record_streamer_widget.update_sources(sources_list)
        except Exception:
            pass

        async def _hub_on_json(text: str, completed: bool = False, **_kwargs):
            await exec_streamer_fn(text, completed=completed)
            await record_streamer_fn(text, completed=completed)

        thinking_streamer._on_json = _hub_on_json
        t0 = time.perf_counter()
        try:
            decision = await react_decision_stream_v2(
                    svc=self.svc,
                    adapters=announced_adapters,
                    infra_adapters=extra_adapters_for_decision,
                    active_skills=None,
                    on_progress_delta=thinking_streamer,
                    agent_name=role,
                    timezone=self.comm_context.user.timezone,
                    max_tokens=6000,
                    attachments=None,
                    plan_steps=state.get("plan_steps") or [],
                    exploration_budget=int(state.get("exploration_budget") or 0),
                    exploitation_budget=int(state.get("exploitation_budget") or 0),
                    user_blocks=user_blocks,
            )
        except ServiceException as exc:
            if not is_context_limit_error(exc.err):
                raise
            user_blocks = await self._render_timeline_with_announce(
                budget_state_v2=state.get("budget_state_v2") or BudgetStateV2(),
                plan_steps=state.get("plan_steps") or [],
                exploration_budget=int(state.get("exploration_budget") or 0),
                exploitation_budget=int(state.get("exploitation_budget") or 0),
                explore_used=int(state.get("explore_used") or 0),
                exploit_used=int(state.get("exploit_used") or 0),
                iteration=iteration,
                max_iterations=int(state.get("max_iterations") or 0),
                force_sanitize=True,
            )
            decision = await react_decision_stream_v2(
                svc=self.svc,
                adapters=announced_adapters,
                infra_adapters=extra_adapters_for_decision,
                active_skills=None,
                on_progress_delta=thinking_streamer,
                agent_name=role,
                timezone=self.comm_context.user.timezone,
                max_tokens=6000,
                attachments=None,
                plan_steps=state.get("plan_steps") or [],
                exploration_budget=int(state.get("exploration_budget") or 0),
                exploitation_budget=int(state.get("exploitation_budget") or 0),
                user_blocks=user_blocks,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        self._append_react_timing(round_idx=iteration, stage="decision", elapsed_ms=elapsed_ms)
        logging_helpers.log_agent_packet(role, "react.decision.v2", decision)
        decision = decision.get("agent_response") or {}
        if not isinstance(decision, dict):
            decision = {}

        validation_error = self._validate_decision(decision)
        if validation_error:
            try:
                self.ctx_browser.contribute_notice(
                    code=f"protocol_violation.{validation_error}",
                    message=self._protocol_violation_message(
                        code=validation_error,
                        decision=decision,
                        state=state,
                    ),
                    extra={"validation_error": validation_error},
                )
            except Exception:
                pass
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

        plan_steps = state.get("plan_steps") or []
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import apply_plan_updates
            status_map = state.get("plan_status") or {}
            status_map, plan_blocks = apply_plan_updates(
                notes=notes,
                plan_steps=plan_steps,
                status_map=status_map if isinstance(status_map, dict) else {},
                timeline_blocks=self.ctx_browser.timeline.blocks if self.ctx_browser else [],
                turn_id=state.get("turn_id") or "",
                iteration=iteration,
                ts=time.time(),
            )
            if status_map:
                state["plan_status"] = status_map
            if plan_blocks and self.ctx_browser:
                self.ctx_browser.contribute(blocks=plan_blocks)
        except Exception:
            pass

        exec_only_ids = {"ctx_tools.fetch_ctx"}
        if action == "call_tool" and tool_id in exec_only_ids:
            try:
                self.ctx_browser.contribute_notice(
                    code="protocol_violation.tool_not_allowed_in_react",
                    message=self._protocol_violation_message(
                        code="tool_not_allowed_in_react",
                        decision=decision,
                        state=state,
                        extra={"tool_id": tool_id},
                    ),
                    extra={"tool_id": tool_id, "iteration": iteration},
                )
            except Exception:
                pass
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
        # Plan acknowledgements are informative only; do not block completion.
        if action == "call_tool":
            adapters_by_id = self._adapters_index(state.get("adapters") or [])
            verdict = self._validate_tool_call_protocol(
                tool_call=tool_call,
                adapters_by_id=adapters_by_id,
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
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_call_invalid",
                        message=self._protocol_violation_message(
                            code="tool_call_invalid",
                            decision=decision,
                            state=state,
                        ),
                        extra={
                            "violations": verdict.get("violations") or [],
                            "tool_id": verdict.get("tool_id"),
                            "iteration": iteration,
                        },
                    )
                except Exception:
                    pass
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
                if tool_id and tool_id not in {"react.read", "react.write"}:
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
                try:
                    self.ctx_browser.contribute_notice(
                        code="tool_signature_validation",
                        message=f"tool_signature_validation={sig_status}",
                        extra={"iteration": iteration, "tool_id": tool_id, "status": sig_status, "issues": sig_issues},
                    )
                except Exception:
                    pass
                state["session_log"].append({
                    "type": "tool_signature_validation",
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "tool_id": tool_id,
                    "status": sig_status,
                    "issues": sig_issues,
                })

            if sig_status == "red":
                try:
                    self.ctx_browser.contribute_notice(
                        code="protocol_violation.tool_signature_red",
                        message=self._protocol_violation_message(
                            code="tool_signature_red",
                            decision=decision,
                            state=state,
                        ),
                        extra={"violations": sig_issues, "tool_id": tool_id, "iteration": iteration},
                    )
                except Exception:
                    pass
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
        call_reason = (tool_call.get("notes") or "").strip()
        if call_reason:
            if tools_insights.is_exec_tool(tool_id) and exec_streamer_widget:
                await exec_streamer_widget.emit_reasoning(call_reason)
            else:
                turn_id = state.get("turn_id") or ""
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
            state["completion_summary"] = (decision.get("completion_summary") or "").strip()
        if not state.get("retry_decision") and action == "clarify":
            state["exit_reason"] = "clarify"
            state["clarification_questions"] = decision.get("clarification_questions") or []

        try:
            if self.ctx_browser and self.scratchpad:
                self.ctx_browser.contribute(
                    blocks=[{
                        "type": "react.decision",
                        "author": "react",
                        "turn_id": state.get("turn_id") or "",
                        "ts": time.time(),
                        "mime": "text/markdown",
                        "path": f"ar:{state.get('turn_id')}.react.decision.{iteration}",
                        "text": "\n".join([l for l in [
                            f"action={action}",
                            f"strategy={strategy}" if strategy else None,
                            f"notes={notes}" if notes else None,
                        ] if l]).strip(),
                    }],
                )
        except Exception:
            pass
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
        bs = state.get("budget_state_v2")
        if bs is not None:
            try:
                bs.decision_rounds_used = int(state.get("iteration") or 0)
            except Exception:
                pass
        return state

    async def _tool_execution_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.round import ReactRound
        state = await ReactRound.execute(react=self, state=state)
        pending_sources = state.pop("pending_sources", None)
        if pending_sources:
            try:
                await self._merge_with_pool(
                    collections=[pending_sources],
                    workdir=pathlib.Path(state["workdir"]),
                    outdir=pathlib.Path(state["outdir"]),
                    record_streamer=state.get("record_streamer"),
                )
            except Exception:
                pass
        return state

    async def _exit_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reason = state.get("exit_reason", "complete")
        state["session_log"].append({
            "type": "exit",
            "timestamp": time.time(),
            "reason": reason,
            "clarification": state.get("clarification_questions"),
        })
        try:
            if self.ctx_browser and self.scratchpad:
                self.ctx_browser.contribute(
                    blocks=[{
                        "type": "react.exit",
                        "author": "react",
                        "turn_id": self.scratchpad.turn_id or "",
                        "ts": time.time(),
                        "mime": "application/json",
                        "path": f"ar:{self.scratchpad.turn_id}.react.exit",
                        "text": json.dumps({
                            "reason": reason,
                            "clarification": state.get("clarification_questions"),
                        }, ensure_ascii=False, indent=2),
                    }],
                )
        except Exception:
            pass
        # persist final ACTIVE STATE to contrib log, then clear announce
        try:
            announce_blocks = self.ctx_browser.timeline.announce_blocks
            if announce_blocks:
                self.ctx_browser.contribute(
                    blocks=announce_blocks,
                )
            self.ctx_browser.announce(blocks=None)
        except Exception:
            pass
        # persist react_state snapshot as a contribution block
        try:
            if self.ctx_browser:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import ReactStateSnapshot
                react_state = ReactStateSnapshot.from_state(state)
                self.ctx_browser.contribute(
                    blocks=[{
                        "type": "react.state",
                        "author": "react",
                        "turn_id": self.ctx_browser.runtime_ctx.turn_id or "",
                        "ts": time.time(),
                        "mime": "application/json",
                        "path": f"ar:{self.ctx_browser.runtime_ctx.turn_id}.react.state",
                        "text": json.dumps(react_state.to_dict(), ensure_ascii=False, indent=2),
                    }],
                )
        except Exception:
            pass

        # Emit citations used in this turn (files already emitted on host)
        try:
            if self.hosting_service and self.ctx_browser and self.ctx_browser.timeline:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import extract_sources_used_from_blocks
                blocks = self.ctx_browser.timeline.get_turn_blocks()
                used_sids = extract_sources_used_from_blocks(blocks)
                if used_sids:
                    sid_set = set(used_sids)
                    citations = []
                    for row in (self.ctx_browser.timeline.sources_pool or []):
                        if not isinstance(row, dict):
                            continue
                        if row.get("sid") in sid_set:
                            # strip heavy fields
                            clean = {k: v for k, v in row.items() if k not in ("content", "base64")}
                            citations.append(clean)
                    await self.hosting_service.emit_solver_artifacts(files=[], citations=citations)
        except Exception:
            pass
        return state

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
        collections: List[List[Dict[str, Any]]],
        workdir: pathlib.Path,
        outdir: pathlib.Path,
        record_streamer: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:

        params = {
            "source_collections": [list(self.ctx_browser.sources_pool or [])] + [c or [] for c in collections]
        }
        tr = await execute_tool(
            runtime_ctx=self.ctx_browser.runtime_ctx,
            tool_execution_context={"tool_id": "ctx_tools.merge_sources", "params": params},
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tool_manager.tools,
            logger=self.log,
        )
        items = tr.get("items") or []
        first_item = next(iter(items), None)
        merged = first_item.get("output") if isinstance(first_item, dict) else None
        if not isinstance(merged, list):
            merged = []
        if merged:
            turn_id = self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser else ""
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
            self.ctx_browser.set_sources_pool(sources_pool=merged)
        if merged:
            try:
                mx = max(int(s.get("sid") or 0) for s in merged if isinstance(s, dict))
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import SOURCE_ID_CV
                    try:
                        next_sid = int(mx) + 1
                    except (TypeError, ValueError):
                        next_sid = 0
                    SOURCE_ID_CV.set({"next": next_sid})
                except Exception:
                    pass
                try:
                    if record_streamer and hasattr(record_streamer, "update_sources"):
                        record_streamer.update_sources(self.ctx_browser.sources_pool if self.ctx_browser else [])
                except Exception:
                    pass
            except Exception:
                pass
        return merged

    async def _analyze_and_build_result(self, state: Dict[str, Any]) -> dict:
        outdir = pathlib.Path(state["outdir"])
        workdir = pathlib.Path(state["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        sources_pool = [s for s in (self.ctx_browser.sources_pool or []) if isinstance(s, dict)]
        sources_pool.sort(key=lambda s: int(s.get("sid") or 0))

        # Keep sources_pool as-is; used flags are inferred from timeline blocks.

        artifacts_out: List[Dict[str, Any]] = []

        completion_summary = (state.get("completion_summary") or "").strip()
        if completion_summary:
            try:
                if self.ctx_browser and self.scratchpad:
                    self.ctx_browser.contribute(
                        blocks=[{
                            "type": "react.completion",
                            "author": "react",
                            "turn_id": state.get("turn_id") or "",
                            "ts": time.time(),
                            "mime": "text/markdown",
                            "path": f"ar:{state.get('turn_id')}.react.completion",
                            "text": completion_summary,
                        }],
                    )
            except Exception:
                pass

        # Persist end-of-turn react_state snapshot into scratchpad (assistant.react_state)
        try:
            if self.scratchpad is not None:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import ReactStateSnapshot
                react_state = ReactStateSnapshot.from_state(state)
                self.scratchpad.react_state = react_state.to_dict()
        except Exception:
            pass

        solver_payload = {
            "ok": True,
            "out": artifacts_out,
            "sources_pool": sources_pool,
            "react": {
                "completion_summary": completion_summary,
                "error": state.get("error"),
            "plan_steps": state.get("plan_steps") or [],
            "plan_status": state.get("plan_status") or {},
        },
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

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/react.py

import json
import pathlib

import time
import uuid

from typing import Any, Dict, List, Optional, Callable, Awaitable, Type
from dataclasses import dataclass, field

from langgraph.graph import StateGraph, END

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.solution.codegen.codegen import CodegenRunner
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.runtime.solution.protocol import (
    UnifiedCoordinatorOut,
    compose_objective,
    format_turn_decision_line,
)
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import (
    _promote_tool_calls,
    _canonical_sources_from_citable_tools_generators,
    _enrich_canonical_sources_with_deliverables,
    normalize_contract_deliverables,
)

from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers
from kdcube_ai_app.infra.accounting import with_accounting
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SolutionPlan
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext, format_tool_signature
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.strategy_and_budget import init_budget_state_for_turn
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import (
    build_turn_session_journal,
    build_operational_digest,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import emit_event, collect_outputs, get_exec_workspace_root

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, BaseTurnView
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger, _mid
import kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends as summary
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.execution import execute_tool

@dataclass
class ReactState:
    session_id: str
    turn_id: str

    # Planning
    plan: SolutionPlan
    output_contract: Dict[str, Any]
    coordinator_guide: str                     # ← derived from Coordinator

    # Tools/adapters
    adapters: List[Dict[str, Any]]

    # FS
    workdir: pathlib.Path
    outdir: pathlib.Path

    # Context
    context: ReactContext

    coordinator_turn_line: str = ""            # ← compact turn-decision line
    next_decision_model: str = "strong"        # strong | regular

    # Timeline
    session_log: List[Dict[str, Any]] = field(default_factory=list)

    # Loop control  (decisions are counted)
    iteration: int = 0
    max_iterations: int = 4
    wrapup_round_used: bool = False
    # Exit
    exit_reason: Optional[str] = None
    clarification_questions: Optional[List[str]] = None

    # Lasts
    last_decision: Optional[Dict[str, Any]] = None
    last_tool_result: Optional[list[Dict[str, Any]]] = None

    # Protocol health
    protocol_violation_count: int = 0
    last_protocol_violation: Optional[Dict[str, Any]] = None

    # Protocol-verify outcome for routing
    protocol_verify_ok: Optional[bool] = None

    # Round timing
    round_open: bool = False
    round_start_ts: Optional[float] = None
    round_index: int = 0
    round_timings: List[Dict[str, Any]] = field(default_factory=list)

    # Strategy tracking
    last_strategy: Optional[str] = None

    # --- enforce “map only to artifacts that existed BEFORE this round”
    round_snapshot_artifact_ids: List[str] = field(default_factory=list)
    mapped_round_index: Optional[int] = None


class ReactSolver:

    MODULE_AGENT_NAME = "solver.react"
    DECISION_AGENT_NAME = "decision"

    def __init__(
            self,
            *,
            service: ModelServiceBase,
            logger: AgentLogger,
            tool_manager: Any,
            scratchpad: TurnScratchpad,
            comm: ChatCommunicator,
            comm_context: ChatTaskPayload,
            hosting_service: Optional[Any] = None,
            turn_view_class: Type[BaseTurnView] = BaseTurnView,
            react_decision_stream: Callable[..., Awaitable[Dict[str, Any]]],
            solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]],
    ):
        self.svc = service
        self.log = logger
        self.tool_manager = tool_manager
        self.scratchpad = scratchpad
        self.comm = comm
        self.comm_context = comm_context
        self.turn_view_class = turn_view_class
        self.graph = self._build_graph()
        self.react_decision_stream = react_decision_stream
        self.solution_gen_stream = solution_gen_stream
        self.hosting_service = hosting_service

        self.codegen_runner = CodegenRunner(
            service=self.svc,
            comm=self.comm,
            logger=self.log,
            tool_subsystem=tool_manager.tools,
            scratchpad=scratchpad,
            comm_context=comm_context,
            turn_view_class=turn_view_class,
        )

    def _build_graph(self) -> StateGraph:
        wf = StateGraph(dict)
        wf.add_node("decision", self._decision_node)
        wf.add_node("protocol_verify", self._protocol_verify_node)
        wf.add_node("tool_execution", self._tool_execution_node)
        wf.add_node("exit", self._exit_node)

        wf.set_entry_point("decision")
        wf.add_conditional_edges(
            "decision",
            self._route_after_decision,
            {"protocol_verify": "protocol_verify", "decision": "decision", "exit": "exit", "max_iterations": "exit"},
        )

        wf.add_conditional_edges(
            "protocol_verify",
            self._route_after_protocol_verify,
            {"tool_execution": "tool_execution", "decision": "decision", "exit": "exit"},
        )

        wf.add_edge("tool_execution", "decision")
        wf.add_edge("exit", END)
        return wf.compile()

    async def prepare_session(
            self,
            *,
            coordinator_out: UnifiedCoordinatorOut,    # ← full planner output
            allowed_plugins: List[str],
            materialize_turn_ids: List[str],
            runtime_ctx: Dict[str, Any],
    ) -> ReactState:
        import tempfile

        # Use smart workspace root (isolation-aware)
        root = get_exec_workspace_root()
        # tmp = pathlib.Path(tempfile.mkdtemp(prefix="react_"))
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="react_", dir=str(root)))
        workdir = tmp / "work"
        outdir = tmp / "out"
        workdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)

        session_id = f"react-{uuid.uuid4().hex[:8]}"
        turn_id = runtime_ctx.get("turn_id") or "current_turn"
        user_id = runtime_ctx.get("user_id")
        conversation_id = runtime_ctx.get("conversation_id")
        bundle_id = runtime_ctx.get("bundle_id")

        self.log.log(f"[react] Start {session_id} in {workdir}")

        # use ContextBrowser
        browser = ContextBrowser(tool_manager=self.tool_manager,
                                 logger=self.log,
                                 turn_view_class=self.turn_view_class)
        bundle = await browser.materialize(
            materialize_turn_ids=materialize_turn_ids,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        await browser.rehost_previous_files(bundle=bundle,
                                            workdir=outdir,
                                            ctx="react")
        await browser.rehost_previous_attachments(bundle=bundle,
                                                  workdir=outdir,
                                                  ctx="react")
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.solution import solution_workspace
            if getattr(self.scratchpad, "user_attachments", None):
                rehosted = await solution_workspace.rehost_previous_attachments(
                    self.scratchpad.user_attachments,
                    outdir,
                    turn_id=turn_id or "current_turn",
                )
                self.scratchpad.user_attachments = rehosted
        except Exception as e:
            self.log.log(f"[react] Warning: Failed to rehost current attachments: {e}", level="WARNING")
        context = browser.make_react_context(
            bundle=bundle,
            scratchpad=self.scratchpad,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            bundle_id=bundle_id
        )

        # Build plan + objective as before
        plan = coordinator_out.to_plan()
        policy = coordinator_out.policy
        sb_hint = getattr(policy, "sb", None) or {}
        context.budget_state = init_budget_state_for_turn(
            plan.output_contract or {},
            sb_hint=sb_hint,
            )
        context.bind_storage(outdir)
        context.timezone = self.comm_context.user.timezone
        context.track_id = runtime_ctx.get("track_id")

        # Get ALL available tools (respecting allowed_plugins filter for safety)
        adapters = self.tool_manager.tools.adapters_for_codegen(
            allowed_plugins=allowed_plugins,
            allowed_ids=None  # ← None means "all tools" (subject to allowed_plugins)
        )
        try:
            user_msg = getattr(self.scratchpad, "user_text", None) or getattr(self.scratchpad, "user_message", None) or ""
        except Exception:
            user_msg = ""
        coordinator_guide = compose_objective(coordinator_out, user_msg)
        coordinator_turn_line = format_turn_decision_line(coordinator_out)

        # Sync max_iterations with global budget (max_decision_rounds)
        max_decision_rounds = context.budget_state.global_budget.max_decision_rounds

        state = ReactState(
            session_id=session_id,
            turn_id=turn_id,
            plan=plan,
            output_contract=plan.output_contract or {},
            coordinator_guide=coordinator_guide,
            coordinator_turn_line=coordinator_turn_line,
            next_decision_model="strong",
            adapters=adapters,
            workdir=workdir,
            outdir=outdir,
            context=context,
            max_iterations=max_decision_rounds,
        )
        return state

    async def run(
            self,
            *,
            coordinator_out: UnifiedCoordinatorOut,    # ← full planner output
            allowed_plugins: List[str],
            materialize_turn_ids: List[str],
            runtime_ctx: Dict[str, Any],
    ) -> dict:

        state = await self.prepare_session(
            coordinator_out=coordinator_out,
            allowed_plugins=allowed_plugins,
            materialize_turn_ids=materialize_turn_ids,
            runtime_ctx=runtime_ctx,
        )
        start_ts = time.time()
        try:
            max_iters = int(state.max_iterations or 0)
            # Each iteration may traverse decision -> protocol_verify -> tool_execution -> decision.
            recursion_limit = max(25, (max_iters * 4) + 10)
            final_state = await self.graph.ainvoke(
                self._to_dict(state),
                config={"recursion_limit": recursion_limit},
            )
        except Exception as e:
            import traceback
            self.log.log(traceback.format_exc())
            self.log.log(f"[react] Graph error: {e}", level="ERROR")
            sd = self._to_dict(state)
            sd["exit_reason"] = "error"
            sd["error"] = {"where": "react", "error": "graph_error", "message": str(e), "managed": True}
            final_state = sd

        try:
            final_state["total_runtime_sec"] = float(time.time() - start_ts)
        except Exception:
            pass

        return await self._analyze_and_build_result(final_state)

    def _to_dict(self, s: ReactState) -> Dict[str, Any]:
        return {
            "session_id": s.session_id,
            "turn_id": s.turn_id,
            "plan": s.plan,
            "output_contract": s.output_contract,
            "coordinator_guide": s.coordinator_guide,
            "coordinator_turn_line": s.coordinator_turn_line,
            "next_decision_model": s.next_decision_model,
            "adapters": s.adapters,
            "workdir": str(s.workdir),
            "outdir": str(s.outdir),
            "context": s.context,
            "session_log": s.session_log,
            "iteration": s.iteration,
            "max_iterations": s.max_iterations,
            "wrapup_round_used": s.wrapup_round_used,
            "exit_reason": s.exit_reason,
            "clarification_questions": s.clarification_questions,
            "last_decision": s.last_decision,
            "last_tool_result": s.last_tool_result,
            "protocol_violation_count": s.protocol_violation_count,
            "last_protocol_violation": s.last_protocol_violation,
            "protocol_verify_ok": s.protocol_verify_ok,
            "round_open": s.round_open,
            "round_start_ts": s.round_start_ts,
            "round_index": s.round_index,
            "round_timings": s.round_timings,
            "last_strategy": s.last_strategy,
            "round_snapshot_artifact_ids": s.round_snapshot_artifact_ids,
            "mapped_round_index": s.mapped_round_index,
        }

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

    # ----------------------------
    # Routing
    # ----------------------------

    def _route_after_decision(self, state: Dict[str, Any]) -> str:
        last_decision = state.get("last_decision") or {}
        action = last_decision.get("action")
        if action in ("complete", "exit") and not state.get("exit_reason"):
            state["exit_reason"] = "complete"
        # Centralized wrap-up gate: only when exiting with pending slots and unused artifacts.
        if state.get("exit_reason") and not state.get("wrapup_round_used", False):
            context: ReactContext = state.get("context")
            declared = list((state.get("output_contract") or {}).keys())
            filled = list((getattr(context, "current_slots", {}) or {}).keys()) if context else []
            pending = [s for s in declared if s not in set(filled)]
            artifacts = list((getattr(context, "artifacts", {}) or {}).keys()) if context else []
            mapped_artifacts = set()
            if context and getattr(context, "current_slots", None):
                for slot in (context.current_slots or {}).values():
                    if isinstance(slot, dict):
                        aid = (slot.get("mapped_artifact_id") or "").strip()
                        if aid:
                            mapped_artifacts.add(aid)
            has_unmapped = any(aid not in mapped_artifacts for aid in artifacts)
            if pending and has_unmapped:
                state["wrapup_round_used"] = True
                state["is_wrapup_round"] = True
                state["exit_reason"] = None
                return "decision"

        if state.get("exit_reason"):
            return "max_iterations" if state["exit_reason"] == "max_iterations" else "exit"

        nxt = last_decision.get("action", "complete")
        if nxt == "call_tool":
            return "protocol_verify"
        if nxt == "decision":
            return "decision"
        return "exit"

    def _route_after_protocol_verify(self, state: Dict[str, Any]) -> str:
        if state.get("exit_reason"):
            return "exit"
        ok = state.get("protocol_verify_ok", False)
        return "tool_execution" if ok else "decision"

    # ----------------------------
    # Helpers
    # ----------------------------

    def _finalize_round_timing(self, state: Dict[str, Any], *, end_reason: str) -> None:
        start = state.get("round_start_ts")
        if not start:
            return

        try:
            duration = float(time.time() - float(start))
        except Exception:
            duration = None

        round_index = int(state.get("round_index", state.get("iteration", 0)))

        decision = state.get("last_decision") or {}
        strategy = decision.get("strategy")
        action = decision.get("action")
        tool_call = decision.get("tool_call") or {}
        tool_id = tool_call.get("tool_id")

        rec = {
            "iteration": round_index,
            "duration_sec": duration,
            "strategy": strategy,
            "action": action,
            "tool_id": tool_id,
            "end_reason": end_reason,
        }

        timings = state.get("round_timings")
        if not isinstance(timings, list):
            timings = []
            state["round_timings"] = timings
        timings.append(rec)

        state["round_start_ts"] = None
        state["round_open"] = False

        # round snapshot is only meaningful while round is open
        state["round_snapshot_artifact_ids"] = []
        # mapped_round_index remains (it’s a safety gate)

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

    def _snapshot_artifact_ids(self, context: ReactContext) -> List[str]:
        """
        Best-effort snapshot of artifact IDs visible *before* the round begins.
        We only strictly enforce this for paths under: current_turn.artifacts.<ID>...
        """
        ids: List[str] = []
        try:
            arts = getattr(context, "artifacts", None)
            if isinstance(arts, dict):
                ids = [str(k) for k in arts.keys()]
            elif isinstance(arts, list):
                for a in arts:
                    if not isinstance(a, dict):
                        continue
                    for key in ("artifact_id", "id", "name"):
                        v = a.get(key)
                        if v:
                            ids.append(str(v))
                            break
            # de-dupe preserve order
            seen = set()
            ids = [x for x in ids if not (x in seen or seen.add(x))]
            return ids
        except Exception:
            return []

    def _extract_current_turn_artifact_id(self, source_path: str) -> Optional[str]:
        """
        If path is like: current_turn.artifacts.<ID>...
        return <ID>.
        """
        if not isinstance(source_path, str):
            return None
        prefix = "current_turn.artifacts."
        if not source_path.startswith(prefix):
            return None
        rest = source_path[len(prefix):]
        if not rest:
            return None
        # ID ends at next dot if present
        parts = rest.split(".", 1)
        aid = (parts[0] or "").strip()
        return aid or None

    def _safe_apply_mappings_best_effort(
        self,
        *,
        context: ReactContext,
        output_contract: Dict[str, Any],
        map_slots: List[Dict[str, Any]],
        logger: AgentLogger,
        label: str,
        round_snapshot_artifact_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Mandatory best-effort mapping (called only from DECISION node):
          - Apply mapping only once per round (guarded by caller).
          - Mapping allowed ONLY to artifacts that existed before this round:
              for current_turn.artifacts.<ID>... require <ID> in round snapshot.
          - Never hard-fail the run if mapping fails.
        """
        applied: List[str] = []
        dropped: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        snapshot_set = set(round_snapshot_artifact_ids or [])

        for ms in (map_slots or []):
            if not isinstance(ms, dict):
                continue
            slot_name = (ms.get("slot_name") or "").strip()
            sp = (ms.get("source_path") or ms.get("artifact") or "").strip()
            if not slot_name or not sp:
                dropped.append({"slot_name": slot_name, "source_path": sp, "reason": "missing_slot_or_path"})
                continue

            # # Enforce “artifact existed BEFORE this round” (strict only for current_turn.artifacts.<ID>)
            aid = self._extract_current_turn_artifact_id(sp)
            if aid is not None and aid not in snapshot_set:
                dropped.append({"slot_name": slot_name, "source_path": sp, "reason": "not_in_round_snapshot"})
                try:
                    context.add_event(kind="mapping_dropped_not_in_round_snapshot", data={"slot": slot_name, "source_path": sp})
                except Exception:
                    pass
                continue

            # Resolvability check (must exist now; decision already saw it)
            try:
                val, _owner = context.resolve_path(sp, mode="summary")
                obj_now = context.resolve_object(sp)
                resolvable = (val is not None) or isinstance(obj_now, (dict, list, str))
            except Exception:
                resolvable = False

            if not resolvable:
                dropped.append({"slot_name": slot_name, "source_path": sp, "reason": "not_resolvable_now"})
                try:
                    context.add_event(kind="mapping_dropped_unseen_artifact", data={"slot": slot_name, "source_path": sp})
                except Exception:
                    pass
                continue

            try:
                context.map_from_decision(
                    decision={"map_slot": ms},
                    output_contract=output_contract,
                    logger=logger,
                )
                applied.append(slot_name)
                # --- annotate slot with mapping provenance (safe, ignored by final out_dyn) ---
                try:
                    art_now = (context.current_slots or {}).get(slot_name)
                    if isinstance(art_now, dict):
                        # full source path (useful for debugging; playbook may truncate)
                        art_now.setdefault("mapped_from", sp)

                        # if source_path is current_turn.artifacts.<ID>..., preserve <ID>
                        aid = self._extract_current_turn_artifact_id(sp)
                        if aid:
                            art_now.setdefault("mapped_artifact_id", aid)
                except Exception:
                    pass
                try:
                    line = context.slot_mapping_trace(ms, label=label)
                    self.scratchpad.tlog.solver(line)
                except Exception:
                    pass
            except Exception as e:
                errors.append({"slot_name": slot_name, "source_path": sp, "error": str(e)[:200]})
                self.log.log(
                    f"[react.decision] Failed to apply mapping for {ms.get('slot_name')}: {e}",
                    level="ERROR"
                )

        return {"applied": applied, "dropped": dropped, "errors": errors}

    def _validate_tool_call_protocol(
        self,
        *,
        tool_call: Any,
        adapters_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Validate the tool call structure.
        Returns:
          { ok: bool, tool_id: str|None, artifact_specs: list[dict], violations: list[dict], params: dict }
        """
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
            if tool_id not in adapters_by_id:
                violations.append({
                    "code": "unknown_tool_id",
                    "message": f"tool_id '{tool_id}' is not in adapters/available tools",
                    "tool_id": tool_id,
                })

        tras = tc.get("tool_res_artifacts", None)
        artifact_specs: List[Dict[str, Any]] = []
        if isinstance(tras, list):
            for a in tras:
                if not isinstance(a, dict):
                    continue
                nm = (a.get("name") or "").strip()
                if nm:
                    artifact_specs.append(a)
        if not artifact_specs:
            is_codegen = tool_id == "codegen_tools.codegen_python"
            has_contract = isinstance(params.get("output_contract"), (dict, str))
            if not (is_codegen and has_contract):
                violations.append({
                    "code": "missing_tool_res_artifacts",
                    "message": "call_tool requires non-empty tool_res_artifacts[] with dicts having non-empty name",
                    "tool_id": tool_id,
                })

        ok = len(violations) == 0
        return {
            "ok": ok,
            "tool_id": tool_id,
            "artifact_specs": artifact_specs,
            "violations": violations,
            "params": params,
        }

    # ----------------------------
    # Nodes
    # ----------------------------

    async def _decision_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        it = int(state["iteration"])
        context: ReactContext = state["context"]

        # ---------- Budget hard gate ----------
        bs = getattr(context, "budget_state", None)
        if bs is not None and bs.must_finish():
            # Budget exhausted: allow ONE wrapup round for mapping if needed.
            declared = list((state.get("output_contract") or {}).keys())
            filled = list((getattr(context, "current_slots", {}) or {}).keys()) if context else []
            pending = [s for s in declared if s not in set(filled)]
            artifacts = list((getattr(context, "artifacts", {}) or {}).keys()) if context else []
            mapped_artifacts = set()
            if context and getattr(context, "current_slots", None):
                for slot in (context.current_slots or {}).values():
                    if isinstance(slot, dict):
                        aid = (slot.get("mapped_artifact_id") or "").strip()
                        if aid:
                            mapped_artifacts.add(aid)
            has_unmapped = any(aid not in mapped_artifacts for aid in artifacts)
            if pending and has_unmapped and not state.get("wrapup_round_used", False):
                state["wrapup_round_used"] = True
                state["is_wrapup_round"] = True
            else:
                state["exit_reason"] = "max_iterations"
                state["error"] = {
                    "where": "react.decision",
                    "error": "budget_exhausted",
                    "description": "Global decision/tool-call budget exhausted before explicit EXIT/COMPLETE.",
                    "details": {
                        "iteration": it,
                        "global_budget": {
                            "max_decision_rounds": bs.global_budget.max_decision_rounds,
                            "decision_rounds_used": bs.global_budget.decision_rounds_used,
                            "max_tool_calls": bs.global_budget.max_tool_calls,
                            "tool_calls_used": bs.global_budget.tool_calls_used,
                            "max_explore_rounds": bs.global_budget.max_explore_rounds,
                            "explore_rounds_used": bs.global_budget.explore_rounds_used,
                            "max_exploit_rounds": bs.global_budget.max_exploit_rounds,
                            "exploit_rounds_used": bs.global_budget.exploit_rounds_used,
                            "max_render_rounds": bs.global_budget.max_render_rounds,
                            "render_rounds_used": bs.global_budget.render_rounds_used,
                        },
                    },
                    "managed": True,
                }
                self.log.log("[react.decision] Budget exhausted; exiting", level="WARNING")
                return state

        # ---------- Start timing for this round (decision + optional tool) ----------
        if not state.get("round_open"):
            state["round_open"] = True
            state["round_start_ts"] = time.time()
            # store the iteration index for this round (0-based)
            state["round_index"] = it

            # Snapshot artifacts BEFORE the round starts
            snap_ids = self._snapshot_artifact_ids(context)
            state["round_snapshot_artifact_ids"] = snap_ids

        turn_session_journal = build_turn_session_journal(
            context=context,
            output_contract=state["output_contract"],
            turn_view_class=self.turn_view_class,
            is_codegen_agent=False,
            coordinator_turn_line=state.get("coordinator_turn_line"),
        )
        contract_for_agent = {k: v.model_dump() for k, v in (state["output_contract"] or {}).items()}
        announced_adapters = [
            a for a in state["adapters"]
            if a["id"] not in tools_insights.INFRA_TOOL_IDS and not tools_insights.is_code_tool(a["id"])
        ]
        extra_adapters = [a for a in state["adapters"] if tools_insights.is_code_tool(a["id"])]
        operational_digest = build_operational_digest(
            turn_session_journal=turn_session_journal,
            session_log=state["session_log"],
            slot_specs=contract_for_agent,
            adapters=announced_adapters,
        )
        context.operational_digest = operational_digest

        model_kind = state.get("next_decision_model") or "strong"
        role = f"{self.MODULE_AGENT_NAME}.{self.DECISION_AGENT_NAME}.{model_kind}"
        async with with_accounting(
                context.bundle_id,
                track_id="A",
                agent=role,
                metadata={"track_id": "A", "agent": role},
        ):
            is_wrapup = state.get("is_wrapup_round", False)
            decision_out = await self.react_decision_stream(
                svc=self.svc,
                operational_digest=operational_digest,
                adapters=extra_adapters,
                on_progress_delta=self._mk_thinking_streamer(f"decision ({it})"),
                iteration_idx=it,
                max_iterations=state["max_iterations"],
                agent_name=role,
                is_wrapup_round=is_wrapup,
                timezone=self.comm_context.user.timezone,
                max_tokens=6000
            )
        logging_helpers.log_agent_packet(role, "react.decision", decision_out)

        # Accept both legacy envelope and direct schema
        agent_response = (decision_out or {}).get("agent_response") or (decision_out or {})
        if agent_response and agent_response.get("strategy") == "exit":
            agent_response["action"] = "exit"

        elog = decision_out.get("log") or {}
        internal_thinking = decision_out.get("internal_thinking")
        error_text = (elog.get("error") or "").strip()
        focus_slot = agent_response.get("focus_slot") or ""
        action = agent_response.get("action") or "complete"
        next_decision_step = agent_response.get("next_decision_step") or ""
        completion_summary = (agent_response.get("completion_summary") or "").strip()
        raw_strategy = agent_response.get("strategy")
        allowed_strategies = {"explore", "exploit", "render", "exit"}
        strategy = raw_strategy
        if isinstance(strategy, str):
            strategy = strategy.strip().lower() or None
        if completion_summary and (not strategy or strategy not in allowed_strategies):
            strategy = "exit"
            agent_response["strategy"] = "exit"
        elif not strategy or strategy not in allowed_strategies:
            pv = {
                "code": "decision_invalid_strategy",
                "message": f"strategy={raw_strategy!r}",
                "tool_id": "react.decision",
                "iteration": it + 1,
            }
            try:
                state["protocol_violation_count"] = int(state.get("protocol_violation_count") or 0) + 1
            except Exception:
                state["protocol_violation_count"] = 1
            state["last_protocol_violation"] = pv
            context.add_event(kind="protocol_violation", data=pv)
            context.add_event(kind="decision_protocol_violation", data=pv)
            state["session_log"].append({
                "type": "decision_protocol_violation",
                "iteration": it + 1,
                "timestamp": time.time(),
                "details": pv,
            })
            strategy = state.get("last_strategy") or "exploit"
            agent_response["strategy"] = strategy
            agent_response["action"] = "decision"
            action = "decision"
            state["force_decision_rerun"] = True
        if not agent_response.get("action"):
            agent_response["action"] = action
        self.scratchpad.tlog.solver(
            f"[react.decision] action={action} next_decision_step={next_decision_step} "
            f"reason={agent_response.get('reasoning','')[:120]} focus_slot={focus_slot}"
        )
        await emit_event(
            comm=self.comm,
            etype="solver.react.decision",
            title=f"ReAct Round ({it}). Decision Node",
            step=f"react({it}).decision",
            data=agent_response if agent_response else {"error": "no agent_response"},
            agent=f"solver.react({it}).decision",
        )
        self.scratchpad.register_agentic_response(f"solver.react.decision ({it})", agent_response)

        # Update model selection for next round (if provided)
        if agent_response.get("next_decision_model"):
            state["next_decision_model"] = agent_response.get("next_decision_model")

        # Track strategy + focus_slot
        strategy = agent_response.get("strategy") or None
        focus_slot = agent_response.get("focus_slot") or None

        show_paths = agent_response.get("show_artifacts") or []
        tool_call_id = ""
        if isinstance(agent_response.get("tool_call"), dict):
            tool_call_id = (agent_response["tool_call"].get("tool_id") or "").strip()
        if isinstance(show_paths, list) and show_paths:
            if action == "decision" or tool_call_id == "codegen_tools.codegen_python":
                bs = getattr(context, "budget_state", None)
                if bs is not None and bs.global_budget.remaining_context_reads() <= 0:
                    self.log.log("[react.journal] context_reads budget exhausted; skipping show_artifacts", level="WARNING")
                    if action == "decision":
                        agent_response["action"] = "exit"
                        action = "exit"
                        state["exit_reason"] = "context_reads_exhausted"
                    show_paths = []
                else:
                    if bs is not None:
                        bs.note_context_read()
                if show_paths:
                    self.log.log(
                        f"[react.journal] rebuilding with show_artifacts={len(show_paths)}",
                        level="INFO",
                    )
                    show_items = context.materialize_show_artifacts(show_paths)
                    fetch_context_tool_retrieval_example = ""
                    if tool_call_id == "codegen_tools.codegen_python":
                        fetch_context_tool_retrieval_example = "ctx_tools.fetch_ctx([<turn_id>.artifacts.<ID>])"
                    turn_session_journal = build_turn_session_journal(
                        context=context,
                        output_contract=state["output_contract"],
                        turn_view_class=self.turn_view_class,
                        show_artifacts=show_items or None,
                        is_codegen_agent=True,
                        coordinator_turn_line=state.get("coordinator_turn_line"),
                    )
                    operational_digest = build_operational_digest(
                        turn_session_journal=turn_session_journal,
                        session_log=state["session_log"],
                        slot_specs=contract_for_agent,
                        adapters=announced_adapters,
                    )
                    context.operational_digest = operational_digest

        # Update current stage if focus_slot is a known contract slot
        bs = getattr(context, "budget_state", None)
        if focus_slot and focus_slot in (state["output_contract"] or {}):
            if bs is not None:
                bs.set_current_stage(focus_slot)

        # Signature (for logs)
        tool_call = (agent_response.get("tool_call") or {})
        fetch_ctx = (agent_response.get("fetch_context") or [])

        # Filter out invalid / unsafe map_slots (no future-artifact mapping).
        requested_maps = [m for m in (agent_response.get("map_slots") or []) if isinstance(m, dict)]
        tras = tool_call.get("tool_res_artifacts") if isinstance(tool_call, dict) else None
        planned_names: List[str] = []
        if isinstance(tras, list):
            for a in tras:
                if isinstance(a, dict):
                    n = (a.get("name") or "").strip()
                    if n:
                        planned_names.append(n)
        # de-dupe, preserve order
        seen = set()
        planned_names = [x for x in planned_names if not (x in seen or seen.add(x))]

        # Only map artifacts that already exist (and not the ones about to be produced)
        filtered_maps: List[Dict[str, Any]] = []
        snap_ids = state.get("round_snapshot_artifact_ids") or []
        snap_set = set(snap_ids)

        for ms in requested_maps:
            slot_name = (ms.get("slot_name") or "").strip()
            sp = (ms.get("source_path") or ms.get("artifact") or "").strip()
            if not slot_name or not sp:
                continue
            # prevent mapping to future artifact(s)
            if planned_names and any(sp.startswith(f"current_turn.artifacts.{nm}") for nm in planned_names):
                context.add_event(kind="mapping_dropped_future_artifact", data={"slot": slot_name, "source_path": sp})
                continue

            # Enforce “current_turn.artifacts.<ID> must exist in round snapshot”
            aid = self._extract_current_turn_artifact_id(sp)
            if aid is not None and aid not in snap_set:
                context.add_event(kind="mapping_dropped_not_in_round_snapshot", data={"slot": slot_name, "source_path": sp})
                continue

            # Must be resolvable now (decision saw it)
            try:
                val, _owner = context.resolve_path(sp, mode="summary")
                obj_now = context.resolve_object(sp)
                resolvable = (val is not None) or isinstance(obj_now, (dict, list, str))
            except Exception:
                resolvable = False

            if not resolvable:
                context.add_event(kind="mapping_dropped_unseen_artifact", data={"slot": slot_name, "source_path": sp})
                continue

            one_map: Dict[str, Any] = {"slot_name": slot_name, "source_path": sp}
            if "draft" in ms:
                one_map["draft"] = bool(ms.get("draft"))
            gaps = ms.get("gaps")
            if isinstance(gaps, str) and gaps.strip():
                # keep it short-ish defensively
                one_map["gaps"] = gaps.strip()[:200]

            filtered_maps.append(one_map)

        if requested_maps:
            agent_response = dict(agent_response, map_slots=filtered_maps)

        action = agent_response.get("action") or "complete"
        # Decision timeline event
        context.add_event(kind="decision", data={
            "iteration": it + 1,
            "action": action,
            "next_decision_step": agent_response.get("next_decision_step"),
            "reasoning": agent_response.get("reasoning"),
            "tool_call": agent_response.get("tool_call") or {},
            "map_slots": filtered_maps,
            "fetch_context_count": len(fetch_ctx) if isinstance(fetch_ctx, list) else 0,
            "fetch_context": fetch_ctx,
            "strategy": strategy,
            "focus_slot": focus_slot,
        })

        # Update state (decision counter advances here)
        state["last_decision"] = agent_response
        state["last_strategy"] = strategy
        state["protocol_verify_ok"] = None
        state["iteration"] = it + 1

        # ---- Handle action ----
        nxt = action
        is_wrapup = state.get("is_wrapup_round", False)
        if is_wrapup and nxt == "call_tool":
            nxt = "exit"
            agent_response["action"] = "exit"
        if nxt == "decision":
            show_paths = agent_response.get("show_artifacts") or []
            if not (isinstance(show_paths, list) and show_paths) and not state.get("force_decision_rerun"):
                self.log.log("[react.decision] decision rerun without show_artifacts; exiting", level="WARNING")
                nxt = "exit"
                agent_response["action"] = "exit"
                state["exit_reason"] = "decision_no_show_artifacts"
            state["force_decision_rerun"] = False

        # ---------- MANDATORY mapping step: ONCE per round, only here ----------
        if filtered_maps and state.get("mapped_round_index") != state.get("round_index"):
            best = self._safe_apply_mappings_best_effort(
                context=context,
                output_contract=state["output_contract"],
                map_slots=filtered_maps,
                logger=self.log,
                label="react.map.decision_once",
                round_snapshot_artifact_ids=snap_ids,
            )
            state["mapped_round_index"] = state.get("round_index")

            context.add_event(kind="best_effort_mapping", data={
                "when": "decision_once",
                "round_index": state.get("round_index"),
                "iteration": it + 1,
                "applied": best["applied"],
                "dropped": best["dropped"][:10],
                "errors": best["errors"][:10],
            })

            self.scratchpad.tlog.solver(
                f"[react.map] round={state.get('round_index')} applied={len(best['applied'])} "
                f"dropped={len(best['dropped'])} errors={len(best['errors'])}"
            )

            state["session_log"].append({
                "type": "slot_mapping",
                "iteration": it + 1,
                "round_index": state.get("round_index"),
                "timestamp": time.time(),
                "count": len(filtered_maps),
                "applied": best["applied"],
                "dropped": best["dropped"][:10],
                "errors": best["errors"][:10],
                "slots": [m.get("slot_name") for m in filtered_maps if isinstance(m, dict)],
            })

        # Log decision in session log (always)
        state["session_log"].append({
            "type": "decision",
            "iteration": it + 1,
            "timestamp": time.time(),
            "decision": agent_response,
        })

        # decision rerun (show_artifacts) closes the round without tool calls
        if nxt == "decision":
            if is_wrapup:
                self.log.log("[react.decision] decision rerun blocked during wrapup; exiting", level="WARNING")
                nxt = "exit"
                agent_response["action"] = "exit"
                state["exit_reason"] = "wrapup_no_decision_rerun"
            else:
                if bs is not None:
                    gb = bs.global_budget
                    if gb.decision_reruns_used >= gb.max_decision_reruns:
                        self.log.log("[react.decision] decision rerun budget exhausted; exiting", level="WARNING")
                        nxt = "exit"
                        agent_response["action"] = "exit"
                        state["exit_reason"] = "decision_reruns_exhausted"
                    else:
                        bs.note_decision_rerun()
                        try:
                            s = (strategy or "finish")
                            bs.note_decision_round(
                                strategy=s,
                                tool_ids=[],
                                is_render_round=(s == "render"),
                            )
                        except Exception:
                            pass
                        self._finalize_round_timing(state, end_reason="decision")
                        return state

        # Exit/complete/clarify closes the round
        if nxt in ("complete", "exit", "clarify"):
            # Budget: decision round consumed, no tool call
            if bs is not None and not is_wrapup:
                try:
                    s = (strategy or "finish")
                    bs.note_decision_round(
                        strategy=s,
                        tool_ids=[],
                        is_render_round=(s == "render"),
                    )
                except Exception:
                    pass

            if nxt == "clarify":
                state["exit_reason"] = "clarify"
                state["clarification_questions"] = agent_response.get("clarification_questions")
            else:
                state["exit_reason"] = "complete"

            self._finalize_round_timing(state, end_reason=nxt)
            return state

        # call_tool keeps the round open; protocol_verify/tool_execution will close it
        return state


    async def _protocol_verify_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate tool_call protocol ONLY.
        No mapping here (mapping is decision-only, once per round).
        """
        context: ReactContext = state["context"]
        decision = state.get("last_decision") or {}
        it = int(state.get("iteration") or 0)  # note: iteration already incremented in decision node
        strategy = state.get("last_strategy")

        # Default: blocked unless proven OK
        state["protocol_verify_ok"] = False

        nxt = decision.get("action")
        if nxt != "call_tool":
            # Nothing to verify
            state["protocol_verify_ok"] = True
            return state

        tool_call = decision.get("tool_call") or {}

        adapters_by_id = self._adapters_index(state.get("adapters") or [])
        verdict = self._validate_tool_call_protocol(tool_call=tool_call, adapters_by_id=adapters_by_id)

        tool_id = verdict.get("tool_id")
        violations = verdict.get("violations") or []
        ok = bool(verdict.get("ok"))

        context.add_event(kind="protocol_verify", data={
            "iteration": it,
            "tool_id": tool_id,
            "ok": ok,
            "violations_count": len(violations),
        })

        state["session_log"].append({
            "type": "protocol_verify",
            "iteration": it,
            "timestamp": time.time(),
            "tool_id": tool_id,
            "ok": ok,
            "violations": violations,
        })

        self.scratchpad.tlog.solver(
            f"[react.protocol_verify] ok={ok} tool_id={tool_id or '?'} violations={[v.get('code') for v in violations]}"
        )

        if ok:
            state["protocol_verify_ok"] = True
            return state

        # Invalid tool call: log violations, block tool execution, go back to decision.
        for v in violations:
            pv = {
                "code": v.get("code") or "protocol_violation",
                "message": v.get("message") or "",
                "tool_id": v.get("tool_id") or tool_id,
                "iteration": it,
            }
            try:
                state["protocol_violation_count"] = int(state.get("protocol_violation_count") or 0) + 1
            except Exception:
                state["protocol_violation_count"] = 1
            state["last_protocol_violation"] = pv

            context.add_event(kind="protocol_violation", data=pv)

        context.add_event(kind="tool_call_invalid", data={
            "iteration": it,
            "tool_id": tool_id,
            "violations": violations,
            "action": "return_to_decision",
        })

        # Budget: decision round used, no tool call
        bs = getattr(context, "budget_state", None)
        if bs is not None:
            try:
                s = (strategy or "exploit")
                bs.note_decision_round(strategy=s, tool_ids=[], is_render_round=(s == "render"))
            except Exception:
                pass

        # Close timing for this round (decision + verify; no tool)
        self._finalize_round_timing(state, end_reason="protocol_violation")
        state["protocol_verify_ok"] = False
        return state

    async def _tool_execution_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        decision = state.get("last_decision") or {}
        context: ReactContext = state["context"]  # moved up so we can use it in the early-return path
        tool_call = decision.get("tool_call") or {}
        focus_slot = decision.get("focus_slot") or "Unknown"
        tool_id = (tool_call.get("tool_id") or "").strip()
        tool_call_id = uuid.uuid4().hex[:12]

        def _derive_codegen_specs(params: Dict[str, Any]) -> List[Dict[str, Any]]:
            oc = params.get("output_contract")
            if isinstance(oc, str):
                try:
                    oc = json.loads(oc)
                except Exception:
                    oc = None
            if not isinstance(oc, dict):
                return []
            specs: List[Dict[str, Any]] = []
            for name, spec in oc.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                rec: Dict[str, Any] = {"name": name.strip()}
                if isinstance(spec, dict):
                    kind = (spec.get("type") or "").strip()
                    if kind in ("inline", "file"):
                        rec["kind"] = kind
                    fmt = (spec.get("format") or "").strip()
                    if fmt and rec.get("kind") == "inline":
                        rec["type"] = fmt
                specs.append(rec)
            return specs

        if not tool_id:
            # Defensive: should have been blocked in protocol_verify.
            context.add_event(kind="tool_call_invalid", data={
                "iteration": int(state.get("iteration") or 0),
                "tool_id": None,
                "violations": [{"code": "missing_tool_id", "message": "tool_execution reached without tool_id"}],
                "action": "return_to_decision",
            })
            self._finalize_round_timing(state, end_reason="invalid_tool_call")
            return state

        tras = tool_call.get("tool_res_artifacts") or []
        declared_specs = [a for a in (tras or []) if isinstance(a, dict) and (a.get("name") or "").strip()]
        if not declared_specs and tool_id == "codegen_tools.codegen_python":
            declared_specs = _derive_codegen_specs(tool_call.get("params") or {})

        if not declared_specs:
            # Defensive: should have been blocked in protocol_verify.
            context.add_event(kind="tool_call_invalid", data={
                "iteration": int(state.get("iteration") or 0),
                "tool_id": tool_id,
                "violations": [{"code": "missing_tool_res_artifacts", "message": "tool_execution reached without artifacts spec"}],
                "action": "return_to_decision",
            })
            self._finalize_round_timing(state, end_reason="invalid_tool_call")
            return state

        if tool_id == "codegen_tools.codegen_python":
            oc = (tool_call.get("params") or {}).get("output_contract")
            if isinstance(oc, str):
                try:
                    oc = json.loads(oc)
                except Exception:
                    oc = None
            if isinstance(oc, dict):
                contract_names = [k for k in oc.keys() if isinstance(k, str) and k.strip()]
                declared_names_now = [((a.get("name") or "").strip()) for a in declared_specs]
                missing = [n for n in contract_names if n not in declared_names_now]
                extra = [n for n in declared_names_now if n not in contract_names]
                if missing or extra:
                    self.log.log(
                        f"[react.codegen] tool_res_artifacts mismatch: "
                        f"missing={missing} extra={extra}",
                        level="WARNING",
                    )

        declared_names = [((a.get("name") or "").strip()) for a in declared_specs]
        declared_types = [a.get("type") for a in declared_specs]
        declared_kinds = [a.get("kind") for a in declared_specs]

        base_params = tool_call.get("params") or {}
        fetch_ctx = decision.get("fetch_context") or []

        final_params, content_lineage = context.bind_params_with_sources(
            base_params=base_params,
            fetch_directives=fetch_ctx,
            tool_id=tool_id,
        )

        sig = format_tool_signature(
            tool_id,
            base_params,
            fetch_ctx,
            [a for a in state["adapters"]
             if a["id"] not in (
                 "io_tools.save_ret",
                 "io_tools.tool_call",
                 "ctx_tools.fetch_turn_artifacts",
                 "ctx_tools.fetch_ctx",
                 "ctx_tools.merge_sources",
             )]
        )

        # Build a *summarizer-friendly* view of params:
        param_bindings_for_summary = summary._render_param_bindings_for_summary(
            base_params=base_params,
            fetch_ctx=fetch_ctx,
            final_params=final_params,
        )

        # Events: start
        context.add_event(kind="tool_started", data={
            "artifact_ids": declared_names,
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "signature": sig,
            "reasoning": tool_call.get("reasoning") or "",
        })

        fmt_artifacts = []
        for n, t, k in zip(declared_names, declared_types, declared_kinds):
            rec = f"{n} ({k or 'inline'}"
            rec += f"| {t})" if t else ")"
            fmt_artifacts.append(rec)
        self.scratchpad.tlog.solver(f"[react.tool] {tool_id} → {', '.join(fmt_artifacts) if fmt_artifacts else '?'}")

        # Execute with error capture
        workdir = pathlib.Path(state["workdir"])
        outdir = pathlib.Path(state["outdir"])

        # Optional: enable LLM summary for complex outputs
        use_llm_summary = True  # Set to True to enable, or make configurable
        llm_service = self.svc if use_llm_summary else None

        adapter = next((a for a in state["adapters"] if a.get("id") == tool_id), None)
        tool_doc_for_summary = (
                (adapter or {}).get("doc_for_llm")
                or (adapter or {}).get("doc")
                or (adapter or {}).get("description")
                or ""
        )
        # we must be able to accept the list of "tool results" here and save them all in context with certain mark which the note
        # that relayed these results to the common tool call
        tool_response = await execute_tool(
            tool_execution_context={
                **tool_call,
                "params": final_params,
                "call_signature": sig,
                "param_bindings_for_summary": param_bindings_for_summary,
                "tool_doc_for_summary": tool_doc_for_summary,
                # "fs_isolated": True,      # Optional: request FS sandbox. This currently overridden in the exec layer.
                # "net_isolated": False,    # Optional: disable network (False for most tools, including built-in).  This currently overridden in the exec layer.
            },
            context=context,
            solution_gen_stream=self.solution_gen_stream,
            workdir=workdir,
            outdir=outdir,
            tool_manager=self.tool_manager.tools,
            logger=self.log,
            use_llm_summary=use_llm_summary,
            llm_service=llm_service,
            codegen_runner=self.codegen_runner,
            artifacts_contract=declared_specs,
            tool_call_id=tool_call_id
        )
        items = tool_response.get("items") or []
        call_error = tool_response.get("error") if isinstance(tool_response, dict) else None

        tool_exec_status = "unknown"
        artifact = None
        tool_exec_error = None

        actual_artifact_ids = [tr.get("artifact_id") for tr in items if tr.get("artifact_id")]
        if tool_call_id and tool_call_id in context.tool_call_index:
            context.tool_call_index[tool_call_id]["produced_artifact_ids"] = actual_artifact_ids or []

        hosted_files_to_emit: List[Dict[str, Any]] = []
        for tr in items:
            it_round = int(state["iteration"])

            artifact_id = tr.get("artifact_id")
            spec = next(iter([i for i in declared_specs if i.get("name") == artifact_id or ""]), None) or {}
            artifact_type = tr.get("artifact_type") or spec.get("type")
            artifact_kind = tr.get("artifact_kind") or spec.get("kind")

            artifact_summary = tr.get("summary") or ""
            tool_exec_output = tr.get("output")
            tool_exec_error = tr.get("error")
            tool_exec_status = tr.get("status")

            # if tool_exec_summary:
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

            # Extract error if present
            if tool_exec_error:
                err_msg = f"[react.tool] Tool {tool_id} failed: {tool_exec_error.get('code')} - {tool_exec_error.get('message')}"
                self.log.log(
                    err_msg,
                    level="ERROR"
                )
                context.add_event(kind="tool_error", data={
                    "artifact_id": artifact_id,
                    "tool_id": tool_id,
                    "tool_call_id": tool_call_id,
                    "error": tool_exec_error,
                })
                self.scratchpad.tlog.solver(err_msg)


            # Sources handling
            srcs_for_artifact: List[Dict[str, Any]] = []
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
                    srcs_for_artifact = context.remap_sources_to_pool_sids(srcs_for_artifact)
                    context.add_event(kind="search_sources_merged", data={
                        "tool": tool_id,
                        "tool_call_id": tool_call_id,
                        "added": len(srcs_for_artifact),
                        "pool_size": len(context.sources_pool),
                    })
            elif tools_insights.is_fetch_uri_content_tool(tool_id):
                out = tr.get("output")
                data: Optional[Dict[str, Any]] = None

                # fetch tool may return JSON string or already-parsed dict
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
                        # optional but very helpful for LLM
                        if content:
                            row["content"] = content

                        title = payload.get("title")
                        if isinstance(title, str) and title.strip():
                            row["title"] = title.strip()

                        # propagate useful metadata if present
                        for meta_key in (
                            "published_time_iso",
                            "modified_time_iso",
                            "date_method",
                            "date_confidence",
                            "status",
                            "content_length",
                        ):
                            if meta_key in payload:
                                row[meta_key] = payload[meta_key]

                        rows.append(row)

                srcs_for_artifact = rows

                if srcs_for_artifact:
                    # Merge into canonical pool (assign/align SIDs)
                    _ = await self._merge_with_pool(
                        context=context,
                        collections=[srcs_for_artifact],
                        workdir=workdir,
                        outdir=outdir,
                    )
                    # Rewrite our rows to use the pool SIDs
                    srcs_for_artifact = context.remap_sources_to_pool_sids(srcs_for_artifact)
                    context.add_event(kind="fetch_sources_merged", data={
                        "tool": tool_id,
                        "added": len(srcs_for_artifact),
                        "pool_size": len(context.sources_pool),
                    })

            elif tools_insights.does_tool_accept_sources(tool_id):
                # Try to parse sources from the params we just bound
                raw = final_params.get("sources_list") or final_params.get("sources")
                if isinstance(raw, list):
                    srcs_for_artifact = raw or []

                # Optionally reconcile with existing pool SIDs
                if srcs_for_artifact:
                    srcs_for_artifact = context._reconcile_sources_lists([srcs_for_artifact])

            # Register artifact
            tool_call_item_index = tr.get("tool_call_item_index") or None
            artifact = context.register_tool_result(
                artifact_id=artifact_id,
                tool_id=tool_id,
                value=tool_exec_output,
                summary=artifact_summary,
                artifact_type=artifact_type,
                artifact_kind=artifact_kind,
                sources_used=srcs_for_artifact,
                inputs=tr.get("inputs"),
                call_record_rel=tr.get("call_record_rel"),
                call_record_abs=tr.get("call_record_abs"),
                error=tool_exec_error,  # ← pass error to artifact
                content_inventorization=tr.get("content_inventorization"),
                content_lineage=content_lineage,
                tool_call_id=tool_call_id,
                tool_call_item_index=tool_call_item_index,
            )
            msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
            message_id = f"{_mid('artifact', msg_ts)}{'-' + artifact_id}"
            artifact["message_id"] = message_id
            is_file_artifact = bool(
                artifact_kind == "file"
                or (isinstance(artifact.get("value"), dict) and artifact.get("value", {}).get("type") == "file")
            )
            if is_file_artifact and self.hosting_service:
                svc = self.comm.service or {}
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
                    track_id=context.track_id or svc.get("request_id") or "",
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
                                    "text": value_obj.get("text") or "",
                                    "summary": summary_text,
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

            # Session log
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
                    tail = details.get("stderr_tail")
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

        if hosted_files_to_emit and self.hosting_service:
            await self.hosting_service.emit_solver_artifacts(
                files=hosted_files_to_emit, citations=[]
            )

        # Events: finish
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
                "message": tool_exec_error.get("message")[:200],
            }
        elif isinstance(call_error, dict):
            finish_data["error"] = {
                "code": call_error.get("code") or call_error.get("error") or "tool_failed",
                "message": (call_error.get("message") or call_error.get("description") or "")[:200],
            }
        context.add_event(kind="tool_finished", data=finish_data)


        state["last_tool_result"] = items
        # self.scratchpad.tlog.solver(f"[react.tool] {tool_id} done; status={tr.get('status')}; summary={tool_exec_summary}; finish_data={finish_data}")

        # --- Update budget state based on strategy + tool usage ---
        context_bs = getattr(context, "budget_state", None)
        is_wrapup = state.get("is_wrapup_round", False)
        if context_bs is not None and not is_wrapup:
            try:
                # Strategy for this decision (as chosen by the decision agent)
                strategy = (state.get("last_strategy") or "exploit")

                # Render round detection:
                #  - Prefer the agent's explicit "render" strategy
                #  - Fallback: treat write_* tools as render rounds even if strategy was mis-set
                is_render_round = bool(
                    strategy == "render" or tools_insights.is_write_tool(tool_id)
                )
                is_wrapup = state.get("is_wrapup_round", False)
                context_bs.note_decision_round(
                    strategy=strategy,
                    tool_ids=[tool_id],
                    is_render_round=is_render_round,
                )
            except Exception:
                # Budget updates are best-effort; don't crash the flow
                pass

        # Close timing for this round (decision + tool)
        self._finalize_round_timing(state, end_reason="call_tool")

        return state

    async def _exit_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reason = state.get("exit_reason", "complete")
        state["context"].add_event(kind="exit", data={
            "reason": reason,
            "clarification": state.get("clarification_questions"),
        })
        self.log.log(f"[react.exit] {reason}")
        state["session_log"].append({
            "type": "exit",
            "timestamp": time.time(),
            "reason": reason,
            "clarification": state.get("clarification_questions"),
        })
        return state

    # ----------------------------
    # Source pool merge helper
    # ----------------------------

    async def _merge_with_pool(
            self,
            *,
            context: ReactContext,
            collections: List[List[Dict[str, Any]]],
            workdir: pathlib.Path,
            outdir: pathlib.Path,
    ) -> List[Dict[str, Any]]:
        """
        Call ctx_tools.merge_sources in a subprocess to merge:
        [context.sources_pool, *collections]. Preserves pool SIDs; assigns new SIDs for novel URLs.
        """
        params = {
            "source_collections": [context.sources_pool] + [c or [] for c in collections]
        }
        tr = await execute_tool(
            tool_execution_context={"tool_id": "ctx_tools.merge_sources",
                                    "params": params},
            workdir=workdir, outdir=outdir,
            tool_manager=self.tool_manager.tools,
            logger=self.log,
            context=context,
        )
        items = tr.get("items") or []
        first_item = next(iter(items), None)
        merged = first_item.get("output") if isinstance(first_item, dict) else None
        if not isinstance(merged, list):
            merged = []

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
                # Keep SOURCE_ID_CV in sync with the highest SID in the pool
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
        context.persist()
        return merged

    async def _analyze_and_build_result(self, state: Dict[str, Any]) -> dict:  # SolveResult-like dict
        from pathlib import Path

        context: ReactContext = state["context"]
        output_contract = state["output_contract"]  # slot_name -> SlotSpec
        outdir = Path(state["outdir"])
        workdir = Path(state["workdir"])
        outdir.mkdir(parents=True, exist_ok=True)

        declared_slots = set((output_contract or {}).keys())
        filled_slots = set((context.current_slots or {}).keys())
        missing_slots = declared_slots - filled_slots

        exit_reason = state.get("exit_reason")
        legitimate_failures = {"clarify", "max_iterations", "error"}

        # Determine success: all slots filled OR legitimate failure mode
        contract_fulfilled = len(missing_slots) == 0

        # Override exit_reason if contract is incomplete on "complete" exit
        if missing_slots and exit_reason == "complete":
            self.log.log(
                f"[react.result] Contract incomplete: {len(missing_slots)} slots not filled: {sorted(missing_slots)}",
                level="WARNING"
            )
            exit_reason = "incomplete_contract"
            state["exit_reason"] = exit_reason

            # Add event for visibility
            context.add_event(kind="contract_validation_failed", data={
                "declared": sorted(list(declared_slots)),
                "filled": sorted(list(filled_slots)),
                "missing": sorted(list(missing_slots)),
            })

        # Success only if contract fulfilled OR legitimate failure
        # ok = contract_fulfilled or (exit_reason in legitimate_failures)
        ok = contract_fulfilled

        # -------- 1) Build out_dyn from current slots --------
        out_dyn: Dict[str, Any] = {}

        def _get(obj, key, default=None):
            try:
                return getattr(obj, key)
            except Exception:
                pass
            if isinstance(obj, dict):
                v = obj.get(key)
                return default if v is None else v
            return default

        for slot_name, spec in (output_contract or {}).items():
            art = (context.current_slots or {}).get(slot_name)
            if not isinstance(art, dict):
                continue

            t = (art.get("type") or _get(spec, "type", "inline")).lower()
            desc = art.get("description") or _get(spec, "description", "") or ""
            draft = bool(art.get("draft"))
            gaps = art.get("gaps")
            sources_used = art.get("sources_used")  # pass-through; SID recon happens later
            summary = art.get("summary")

            inventory = art.get("content_inventorization")
            if t == "inline":
                fmt = art.get("format") or _get(spec, "format", "markdown")
                # prefer explicit text; fall back to "value" for convenience
                txt = art.get("text") or art.get("value") or ""
                out_dyn[slot_name] = {
                    "type": "inline",
                    "format": fmt,
                    "value": txt,
                    "description": desc,
                }

                # IMPORTANT: do NOT mark slots citable by default
                if "citable" in art:
                    out_dyn[slot_name]["citable"] = bool(art.get("citable"))
                if inventory is not None:
                    out_dyn[slot_name]["content_inventorization"] = inventory

            else:  # file
                mime = art.get("mime") or _get(spec, "mime", "application/octet-stream")
                path = art.get("path") or ""
                text = art.get("text") or ""
                out_dyn[slot_name] = {
                    "type": "file",
                    "mime": mime,
                    "path": path,
                    "text": text,
                    "description": desc,
                }
                filename = art.get("filename")
                if filename:
                    out_dyn[slot_name]["filename"] = filename
                hosted_uri = art.get("hosted_uri")
                if hosted_uri:
                    out_dyn[slot_name]["hosted_uri"] = hosted_uri
                hosted_key = art.get("key")
                if hosted_key:
                    out_dyn[slot_name]["key"] = hosted_key
                hosted_rn = art.get("rn")
                if hosted_rn:
                    out_dyn[slot_name]["rn"] = hosted_rn
            if draft:
                out_dyn[slot_name]["draft"] = True
            if gaps:
                out_dyn[slot_name]["gaps"] = gaps
            if sources_used:
                out_dyn[slot_name]["sources_used"] = sources_used
            if inventory is not None:
                out_dyn[slot_name]["content_inventorization"] = inventory
            if summary:
                out_dyn[slot_name]["summary"] = summary
                # files are not citable

        # -------- 2) Discover raw tool-call files (index may be absent in ReAct path) --------
        # We scan OUTDIR for JSONs that look like saved tool calls: {"in":{"tool_id":...}, "ret": ...}
        raw_files: Dict[str, List[str]] = {}
        for p in outdir.glob("*.json"):
            name = p.name
            if name == "result.json":
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            inner = data.get("in") or {}
            tool_id = (inner.get("tool_id") or "").strip()
            if tool_id:
                raw_files.setdefault(tool_id, []).append(name)

        # -------- 3) Promote tool calls & build canonical citation space --------
        promoted = _promote_tool_calls(raw_files, outdir)
        canonical_list, canonical_by_sid = _canonical_sources_from_citable_tools_generators(promoted)
        canonical_list, canonical_by_sid = _enrich_canonical_sources_with_deliverables(
            canonical_list, canonical_by_sid, out_dyn
        )

        # -------- 4) Normalize slots with canonical SIDs, then merge with promoted tool artifacts --------
        normalized_slots = normalize_contract_deliverables(out_dyn, canonical_by_sid=canonical_by_sid)

        def _key(a: Dict[str, Any]):
            rid = a.get("resource_id")
            if rid:
                return ("rid", rid)
            return ("fallback", a.get("type"), (a.get("output") or {}).get("text") or a.get("path"))

        seen = set()
        merged_out: List[Dict[str, Any]] = []
        for row in normalized_slots + promoted:
            k = _key(row)
            if k in seen:
                continue
            seen.add(k)
            merged_out.append(row)

        # -------- 5) Write solver payload (same schema as codegen’s result.json) --------
        contract_dump = {
            k: (v.model_dump() if hasattr(v, "model_dump") else v)
            for k, v in (output_contract or {}).items()
        }

        solver_payload = {
            "ok": ok,
            "out": merged_out,
            "contract": contract_dump,
            "canonical_sources": canonical_list,
            "raw_files": raw_files,
            "react_timings": {
                "rounds": state.get("round_timings") or [],
                "total_rounds_duration_sec": float(sum((r.get("duration_sec") or 0.0) for r in (state.get("round_timings") or []))),
                "total_runtime_sec": float(state.get("total_runtime_sec") or 0.0),
            },
        }

        if exit_reason == "clarify":
            solver_payload["clarification_questions"] = state.get("clarification_questions") or []

        if not ok:
            error_details = state.get("error") or {
                "where": "react",
                "error": exit_reason or "unhandled",
                "description": "Task incomplete",
                "details": {
                    "exit_reason": exit_reason,
                    "iteration": state.get("iteration"),
                    "max_iterations": state.get("max_iterations"),
                },
                "managed": True,
            }

            # Add contract validation details if that's why we failed
            if not contract_fulfilled and exit_reason not in legitimate_failures:
                error_details["description"] = f"Contract incomplete: {len(missing_slots)} of {len(declared_slots)} slots not filled"
                error_details["details"]["contract_validation"] = {
                    "declared_slots": sorted(list(declared_slots)),
                    "filled_slots": sorted(list(filled_slots)),
                    "missing_slots": sorted(list(missing_slots)),
                }

            solver_payload["error"] = error_details

        (outdir / "result.json").write_text(
            json.dumps(solver_payload, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # -------- 6) Get result interpretation --------
        rii = ""
        last_decision = state.get("last_decision")
        if last_decision and isinstance(last_decision, dict):
            rii = (last_decision.get("completion_summary") or "") or (last_decision.get("reasoning") or "")
            if rii:
                rii = rii.strip()
                idx = 0
                await self.comm.delta(text=rii, index=idx, marker="thinking",
                                        agent="solver.react.decision.summary", format="markdown")
                idx += 1
                await self.comm.delta(text="", completed=True, index=idx, marker="thinking",
                                      agent="solver.react.decision.summary", format="markdown")

        # Append contract status to interpretation
        if not contract_fulfilled and exit_reason not in legitimate_failures:
            rii_suffix = f"\n\nNote: Contract incomplete - {len(missing_slots)} slots not filled: {', '.join(sorted(missing_slots))}"
            rii = (rii or "ReAct flow completed via tool chaining.") + rii_suffix
        elif not rii:
            rii = "ReAct flow completed via tool chaining."

        # -------- 7) Collect outputs --------
        outputs_spec = [{"filename": "result.json", "kind": "json", "key": "solver_output"}]
        collected = collect_outputs(output_dir=outdir, outputs=outputs_spec)

        rounds = [{
            "outputs": collected,
            "result_interpretation_instruction": rii,

            "outdir": str(outdir),
            "workdir": str(workdir),
        }]
        return {
            "rounds": rounds,
            "outdir": str(outdir),
            "workdir": str(workdir),
            "run_id": state["session_id"],
            "mode": "react_loop",
        }

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/solution_engine.py

import traceback
import random
import uuid

from typing import Dict, Any, Optional, List, Tuple, Type
import pathlib
import importlib.util
import importlib

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, TurnPhaseError, BaseTurnView
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.react import ReactSolver
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    SkillsSubsystem,
    set_active_skills_subsystem,
)
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger

from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SolutionPlan, SolveResult, \
    SolutionExecution, SlotSpec, ensure_contract_dict

from kdcube_ai_app.apps.chat.sdk.runtime.solution.infra import mk_thinking_streamer, emit_event
from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import _InProcessRuntime, build_packages_installed_block
from kdcube_ai_app.apps.chat.sdk.runtime.solution.widgets.conversation_turn_work_status import (
    ConversationTurnWorkStatus,
)

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.agents.coordinator import coordinator_planner_stream, UnifiedCoordinatorOut
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.agents.decision import react_decision_stream


def _rid(prefix: str = "r") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def _here(*parts: str) -> pathlib.Path:
    """Path relative to this file (workflow.py)."""
    return pathlib.Path(__file__).resolve().parent.joinpath(*parts)

def _module_to_file(module_name: str) -> pathlib.Path:
    """
    Resolve a dotted module to a concrete .py file path.
    Works for single-file modules and packages (returns __init__.py).
    """
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        raise ImportError(f"Cannot resolve module '{module_name}' to a file (no spec.origin).")
    return pathlib.Path(spec.origin).resolve()

def _group_calls_sequential(out_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group normalized out[] into *ordered* calls by sequence:
    consecutive items with identical (tool_id, tool_input) belong to the same call.
    This preserves call order and avoids merging separate identical calls.
    Returns:
      [{"order": i, "tool_id": "...", "input": {...}, "outputs":[out-item,...]}]
    """
    calls: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return (a.get("tool_id") or "") == (b.get("tool_id") or "") and (a.get("input") or {}) == (b.get("input") or {})

    for it in (out_items or []):
        if not isinstance(it, dict):
            continue
        base = {"tool_id": it.get("tool_id") or "", "input": it.get("input") or {}}
        if cur and _same(cur, base):
            cur["outputs"].append(it)
        else:
            if cur:
                calls.append(cur)
            cur = {"tool_id": base["tool_id"], "input": base["input"], "outputs": [it]}
    if cur:
        calls.append(cur)
    for i, c in enumerate(calls, 1):
        c["order"] = i
    return calls

def _extract_solver_json_from_round(r0: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fetch the primary JSON payload (first output item with kind=json)."""
    items = (r0.get("outputs") or {}).get("items", [])
    for it in items:
        data = it.get("data")
        if isinstance(data, dict) and data.get("ok") is not None:
            return data
    return None

def _spec_to_dict(x: Any) -> Dict[str, Any]:
    # Accept SlotSpec or dict
    if isinstance(x, SlotSpec):
        try:
            return x.model_dump()
        except Exception:
            # very defensive
            return {
                "type": x.type if getattr(x, "type", None) else "inline",
                "description": getattr(x, "description", "") or "",
                "format": getattr(x, "format", None),
                "mime": getattr(x, "mime", None),
                "filename_hint": getattr(x, "filename_hint", None),
            }
    return x if isinstance(x, dict) else {}

def analyze_execution(rounds,
                      plan: SolutionPlan,
                      scratchpad: TurnScratchpad,
                      log: AgentLogger)-> SolutionExecution:

    # 1) Extract solver JSON from the first round
    round0 = next(iter(rounds or []), None)
    solver_json = (_extract_solver_json_from_round(round0) or {}) if round0 else {}
    out_items: List[Dict[str, Any]] = (solver_json or {}).get("out") or []
    sources_pool = getattr(scratchpad, "sources_pool", None)
    if not sources_pool:
        sources_pool = (solver_json or {}).get("sources_pool") or []
        scratchpad.sources_pool = sources_pool

    plan.output_contract = ensure_contract_dict(plan.output_contract)
    # 2) Build single-artifact-per-slot mapping from normalized artifacts
    #    io_tools emits artifacts like:
    #    {
    #       "resource_id": "slot:<slot_name>",
    #       "type": "inline"|"file",
    #       "tool_id": "program" | "<tool>",
    #       "output": {"text": "..."} | {"path": "...", "text": "..."},
    #       "mime": "<mime for files>",
    #       "format": "<inline format>",
    #       "description": "...",
    #       "draft": True|False,
    #       "citable": bool,
    #       "sources_used": [...],
    #       "sources_used_sids": [...]
    #    }
    by_slot_single: Dict[str, Dict[str, Any]] = {}
    for art in out_items:
        rid = str(art.get("resource_id") or "")
        if not rid.startswith("slot:"):
            continue
        slot_name = rid.split(":", 1)[1]
        # last write wins (runtime already aims to write once)
        by_slot_single[slot_name] = art

    # 3) Contract (from planner) is used for: slot list & optional content guidance
    raw_contract = plan.output_contract or {}
    contract_all = {k: _spec_to_dict(v) for k, v in (raw_contract.items() if isinstance(raw_contract, dict) else [])}
    # Drop project_log from contract validation – it's special and always best-effort
    contract: Dict[str, Dict[str, Any]] = {k: v for k, v in contract_all.items() if k != "project_log"}
    contract_keys = set(contract.keys())

    # 4) Construct deliverables map the orchestrator expects:
    #    { slot: {"description": str, "value": <artifact dict>, "type": "inline|file"} }
    deliverables: Dict[str, Dict[str, Any]] = {}
    for slot in contract_keys:
        c = contract.get(slot) or {}
        produced = by_slot_single.get(slot)  # may be None if missing
        a_desc = (produced or {}).get("description")
        a_type = (produced or {}).get("type")

        deliverables[slot] = {
            "description": a_desc if isinstance(a_desc, str) and a_desc.strip() else c.get("description") or "",
            "type": a_type if isinstance(a_type, str) and a_type.strip() else c.get("type") or "inline",
            "value": produced,
        }


    # Optional: if runtime explicitly produced slot:project_log, keep it as a non-contract deliverable
    if "project_log" in by_slot_single:
        art = by_slot_single["project_log"]
        desc = art.get("description") or "Per-turn solver execution log."
        deliverables["project_log"] = {
            "description": desc,
            "type": art.get("type") or "inline",
            "value": art,
        }

    # 5) Citations: keep citable inline artifacts (as normalization already does)
    citations = []
    for a in out_items:
        if a.get("type") == "inline" and bool(a.get("citable")):
            citations.append(a)

    execution = SolutionExecution(
        deliverables=deliverables,
        citations=citations,
        calls=_group_calls_sequential(out_items),
        result_interpretation_instruction=round0.get("result_interpretation_instruction") if round0 else None,
        sources_pool=sources_pool,
    )

    # 6) Validate text surrogates for file slots (runtime contract)
    missing_text_surrogates = []
    for slot_name, spec in (deliverables or {}).items():
        art = spec.get("value") or {}
        if isinstance(art, dict) and art.get("type") == "file":
            txt = ((art.get("output") or {}).get("text") or "").strip()
            if not txt:
                missing_text_surrogates.append(slot_name)
    if missing_text_surrogates:
        msg = f"file slots missing 'text' surrogate: {missing_text_surrogates}"
        scratchpad.tlog.note(f"[solver.validation_error] {msg}")
        try:
            log.log(f"[solver.analysis] {msg}", level="WARNING")
        except Exception:
            pass
        execution.error = (execution.error + " | " if execution.error else "") + msg

    # 8) Summarize status & build failure presentation if not ok
    try:
        # Raw ok flag from solver_json (may be overly optimistic for old runs)
        ok_flag = bool((solver_json or {}).get("ok"))

        # slot completeness vs contract (excluding project_log)
        filled = sorted(
            [
                k
                for k in contract_keys
                if isinstance((deliverables.get(k) or {}).get("value"), dict)
            ]
        )
        missing = sorted(list(contract_keys - set(filled)))
        drafts = sorted(
            [
                k
                for k in filled
                if (deliverables.get(k) or {}).get("value", {}).get("draft")
            ]
        )
        complete = sorted([k for k in filled if k not in drafts])

        # total_slots = len(contract or {})
        total_slots = len(contract_keys or {})

        # Determine overall status based on CONTRACT, not just ok_flag
        if total_slots == 0:
            # No contract → fall back to runtime ok flag
            status = "ok" if ok_flag else "failed"
        elif len(complete) == total_slots and ok_flag:
            # All slots complete and runtime says ok → fully successful
            status = "ok"
        elif len(complete) > 0:
            # Some slots complete, some missing/draft → partial
            status = "partial"
        else:
            # No complete slots at all → failed (even if ok_flag was True in old runs)
            status = "failed"

        scratchpad.tlog.solver(
            f"[solver] mode={plan.mode}; status={status}; complete={complete}; drafts={drafts}; "
            f"missing={missing}; result_interpretation_instruction="
            f"{execution.result_interpretation_instruction or plan.result_interpretation_instruction(solved=(status=='ok'))}"
        )
        try:
            log.log(
                f"[solver.analysis] mode={plan.mode}; status={status}; "
                f"slots_total={total_slots}; complete={complete}; drafts={drafts}; "
                f"missing={missing}; ok_flag={ok_flag}",
                level="INFO",
            )
        except Exception:
            pass

        # capture validation summary into solver_json for downstream inspection
        validation_error = {
            "missing": missing,
            "drafts": drafts,
            "complete": complete,
            "contract": contract,
        # } if len(complete) != len(contract) else {}
        } if total_slots and len(complete) != total_slots else {}
        if validation_error:
            solver_json["validation_error"] = validation_error

        # compact tool calls note
        try:
            tlog_line = ";".join(
                [f"{c.get('order')}:{c.get('tool_id','')}" for c in (execution.calls or [])]
            )
            if tlog_line:
                scratchpad.tlog.solver(f"[tools.calls]: {tlog_line}")
        except Exception:
            pass

        # 🔴 Use status instead of ok_flag
        if status != "ok":
            err = (solver_json or {}).get("error") or {}
            val = (solver_json or {}).get("validation_error") or {}

            description = err.get("description", "")
            error = err.get("error", "")
            where = err.get("where", "")
            details = err.get("details", "")

            real_problem = bool(val) or any([
                err.get("description"),
                err.get("where"),
                err.get("details"),
                err.get("error"),
            ])

            if real_problem:

                error_parts = []
                if any([description, where, details, error]):
                    runtime_error = f"where={where} error={error} details={details}"
                    error_parts.append(runtime_error)
                    scratchpad.tlog.note(f"[solver.runtime_error] {runtime_error}")

                if val:
                    missing_list = val.get("missing", [])
                    filled_list = val.get("complete", [])
                    draft_list = val.get("drafts", [])
                    contract_dict = val.get("contract", {})

                    validation_summary = (
                        f"validation_failed: {len(filled_list)}/{len(contract_dict)} complete, "
                        f"{len(draft_list)} drafts, {len(missing_list)} missing; "
                        f"complete={filled_list}; drafts={draft_list}; missing={missing_list}"
                    )
                    error_parts.append(validation_summary)
                    scratchpad.tlog.note(f"[solver.validation_error] {validation_summary}")

                execution.error = " | ".join(error_parts) if error_parts else "Unknown solver failure"

                # Build failure presentation (markdown) – unchanged logic, just based on status
                sections = []

                if val:
                    missing_list = val.get("missing", [])
                    filled_list = val.get("complete", [])
                    draft_list = val.get("drafts", [])
                    contract_dict = val.get("contract", {})

                    md = [
                        "## Contract Validation Failure",
                        "",
                        f"**Status:** {len(filled_list)} complete / {len(draft_list)} drafts / {len(contract_dict)} required",
                        "",
                    ]
                    if filled_list:
                        md += ["### ✅ Complete", ""]
                        for key in filled_list:
                            spec = contract_dict.get(key, {})
                            t = spec.get("type", "inline")
                            d = spec.get("description", "")
                            md.append(f"**`{key}`** ({t})")
                            if d:
                                md.append(f"  - {d}")
                            md.append("")
                    if draft_list:
                        md += ["### ⚠️ Drafts", ""]
                        for key in draft_list:
                            spec = contract_dict.get(key, {})
                            t = spec.get("type", "inline")
                            d = spec.get("description", "")

                            md.append(f"**`{key}`** ({t}) — draft")
                            if d:
                                md.append(f"  - {d}")
                            gaps = (deliverables.get(key) or {}).get("value", {}).get("gaps")
                            if gaps:
                                md.append(f". Gaps: {gaps}")
                            md.append("")
                    if missing_list:
                        md += ["### ❌ Missing", ""]
                        for key in missing_list:
                            spec = contract_dict.get(key, {})
                            t = spec.get("type", "inline")
                            d = spec.get("description", "")
                            md.append(f"**`{key}`** ({t})")
                            if d:
                                md.append(f"  - {d}")
                            md.append("")
                    sections.append("\n".join(md))

                if any([description, where, details, error]):
                    md = [
                        "## Runtime Error",
                        "",
                        f"**Location:** `{where or 'unknown'}`",
                        f"**Error Type:** `{error or 'UnknownError'}`",
                        "",
                    ]
                    if description:
                        md += ["### Description", description, ""]
                    if details:
                        md += ["### Details", "```", str(details)[:1000], "```", ""]
                    sections.append("\n".join(md))

                total_slots = len(contract or {})
                combined_md = [
                    "# Solver Execution Failure",
                    "",
                    f"**Status:** {status}",
                    f"**Deliverables:** {len(complete)}/{total_slots} satisfied"
                    if total_slots
                    else "**Deliverables:** (no contract slots)",
                    "",
                    "---",
                    "",
                ]
                instructions = plan.instructions_for_downstream or ""
                if instructions:
                    combined_md += [
                        "## Solver was instructed this way:",
                        "",
                        instructions.strip(),
                        "",
                        "---",
                        "",
                    ]
                combined_md.append("\n\n---\n\n".join(sections))

                execution.failure_presentation = {
                    "markdown": "\n".join(combined_md),
                    "struct": {
                        "runtime_error": err
                        if any([description, where, details, error])
                        else None,
                        "validation_error": {
                            "complete": complete,
                            "missing": missing,
                            "total": len(contract or {}),
                        }
                        if val
                        else None,
                        "status": status,
                        "instructions_for_downstream": instructions,
                    },
                }

                scratchpad.tlog.note(
                    f"[solver.failure] Validation: {len(complete)}/{len(contract)} slots; "
                    f"Runtime errors: {'yes' if err else 'no'}"
                )
            else:
                # Non-OK/partial, but no concrete error and no validation detail → just log a warning
                scratchpad.tlog.note(
                    "[solver.warning] Non-OK/partial but no concrete error; treating as partial."
                )

    except Exception as e:
        log.log(f"[solver] error during tlog summary\n" + traceback.format_exc(), level="ERROR")
        execution.error = f"tlog_summary_error: {str(e)}"
        execution.failure_presentation = {
            "markdown": f"### Internal Error\n\nFailed to process solver results:\n```\n{traceback.format_exc()}\n```",
            "struct": {"internal_error": str(e)}
        }

    # 9) Artificial project_log deliverable + [solve.log]
    # If deliverables already contain a project_log (e.g. codegen path), reuse its
    # text. Otherwise (typical ReAct case), synthesize project_log from all
    # existing TurnLog.solver entries, including the status and tools.calls lines
    # we just logged above. Then log the final project log once as [solve.log].
    try:
        pl = (deliverables or {}).get("project_log") or {}
        v = pl.get("value")

        # 9.1 Extract existing text if provided
        if isinstance(v, dict):
            pl_text = (v.get("output") or {}).get("text") or ""
        elif isinstance(v, str):
            pl_text = v
        else:
            pl_text = ""

        # 9.2 Synthesise from TurnLog.solver entries if no explicit project_log
        if not pl_text:
            # Get all solver-area lines except previous [solve.log] entries
            lines = scratchpad.tlog.lines_for_area("solver", exclude_contains=["[solve.log]"])
            pl_text = "\n".join(lines).strip()

        # 9.3 Ensure deliverables contain an artificial project_log slot
        if pl_text:
            desc = pl.get("description") or "Per-turn solver execution log."

            if isinstance(v, dict):
                v.setdefault("output", {})["text"] = pl_text
                deliverables["project_log"] = {
                    "description": desc,
                    "type": v.get("type") or "inline",
                    "value": v,
                }
            else:
                artifact = {
                    "resource_id": "slot:project_log",
                    "type": "inline",
                    "tool_id": "program",
                    "format": "markdown",
                    "description": desc,
                    "output": {"text": pl_text},
                    "draft": False,
                }
                deliverables["project_log"] = {
                    "description": desc,
                    "type": "inline",
                    "value": artifact,
                }

            # 9.4 Do not mirror project_log into turn log; it is already captured as a deliverable.

    except Exception:
        # Project log is best-effort; never fail the solve because of it
        pass

    return execution

# ---------- Manager ----------

class SolverSystem:
    AGENT_NAME = "solver.system"

    def __init__(
            self,
            *,
            service: ModelServiceBase,
            comm: ChatCommunicator,
            comm_context: ChatTaskPayload,
            bundle_spec: BundleSpec,
            logger: Optional[AgentLogger] = None,
            registry: Optional[Dict[str, Any]] = None,
            context_rag_client: Optional[ContextRAGClient] = None,
            tools_specs: Optional[List[Dict[str, Any]]] = None, # list of {ref, use_sk, alias}
            raw_tool_specs: Optional[List[Dict[str, Any]]] = None, # non-resolved tool specs
            tool_runtime: Optional[Dict[str, str]] = None,
            tool_subsystem: Optional[ToolSubsystem] = None,
            skills_descriptor: Optional[Dict[str, Any]] = None,
            skills_subsystem: Optional[SkillsSubsystem] = None,
            turn_view_class: Type[BaseTurnView] = BaseTurnView,
            hosting_service: Optional[Any] = None,
    ):
        self.svc = service
        self.comm = comm
        self.context_rag_client = context_rag_client
        self.log = logger or AgentLogger("tool_manager")
        self.registry = registry or {}
        self.runtime = _InProcessRuntime(self.log)
        self.tools = tool_subsystem or ToolSubsystem(
            service=service,
            comm=comm,
            bundle_spec=bundle_spec,
            logger=self.log,
            context_rag_client=context_rag_client,
            registry=registry,
            tools_specs=tools_specs,
            raw_tool_specs=raw_tool_specs,
            tool_runtime=tool_runtime,
        )
        bundle_root = None
        if getattr(self.tools, "bundle_root", None):
            bundle_root = self.tools.bundle_root
        self.skills = skills_subsystem or SkillsSubsystem(
            descriptor=skills_descriptor,
            bundle_root=bundle_root,
        )
        set_active_skills_subsystem(self.skills)
        self.comm_context = comm_context
        self.turn_view_class = turn_view_class
        self.hosting_service = hosting_service

    # -------- router / solvability --------
    async def coordinate(
            self,
            *,
            ctx: Dict[str, Any],
            scratchpad: TurnScratchpad,
            allowed_plugins: Optional[List[str]] = None,
            allowed_ids: Optional[List[str]] = None,
    ) -> Tuple[UnifiedCoordinatorOut, SolutionPlan, dict, dict]:
        """
        Single call to Unified Planner (Coordinator+Selector+Solvability).
        Returns (UnifiedCoordinatorOut, SolutionPlan, decision_dict, policy_dict).
        Logs to tlog and builds failure_presentation on planner error/malformed output.
        """
        # 1) Build a scoped tool catalog for the planner (id/purpose/args)
        tool_catalog = self.tools.tool_catalog_for_prompt(
            allowed_plugins=allowed_plugins,
            allowed_ids=allowed_ids,
        )

        # 2) Call unified planner with accounting
        runtime_ctx = ctx.get("runtime_ctx") or {}
        track_id = runtime_ctx.get("track_id")
        bundle_id = runtime_ctx.get("bundle_id")

        import json as _json, time as _time
        from kdcube_ai_app.apps.chat.sdk.viz import logging_helpers

        t0 = _time.perf_counter()
        turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent="solver.coordinator",
        )
        await turn_status.send(
            random.choice(
                [
                    "forming the plan",
                    "charting the approach",
                    "planning",
                    "gathering my thoughts",
                    "mapping the route",
                ]
            )
        )
        async with with_accounting(
                bundle_id,
                track_id=track_id,
                agent="solver.coordinator",
                metadata={"track_id": track_id, "agent": "solver.coordinator"},
        ):
            with scratchpad.phase("solver.unified-planner", agent="solver.unified-planner"):
                out = await coordinator_planner_stream(
                    self.svc,
                    user_message=ctx["text"],
                    guess_package_json=(ctx.get("context_hint") or ""),
                    tool_catalog_json=_json.dumps(tool_catalog, ensure_ascii=False),
                    on_progress_delta=mk_thinking_streamer(comm=self.comm,
                                                           author=f"{self.AGENT_NAME}.unified_planner"),
                    code_packages=build_packages_installed_block(),
                    max_tokens=3000,
                    timezone=self.comm_context.user.timezone,
                )
                if out and (out.get("log") or {}).get("error"):
                    err = out["log"]["error"]
                    msg = (
                              err.get("message") if isinstance(err, dict)
                              else str(err)
                          ) or "Unified Coordinator failed"
                    raise TurnPhaseError(
                        msg,
                        code="solver.unified-planner.error",
                        data={"solver.unified-planner.raw": out},
                    )
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if scratchpad and hasattr(scratchpad, "timings"):
            scratchpad.timings.append({
                "title": "solver.unified_planner",
                "elapsed_ms": elapsed_ms,
            })

        # 3) Log/emit for observability
        logging_helpers.log_agent_packet(self.AGENT_NAME, "unified_planner", out)
        agent = out.get("agent_response") or {}
        elog = out.get("log") or {}
        internal_thinking = out.get("internal_thinking")
        error_text = (elog.get("error") or "").strip()

        await emit_event(
            comm=self.comm,
            etype="solver.unified-planner",
            title="Unified Coordinator-Planner",
            step="solver.unified-planner",
            data=agent if agent else {"error": error_text or "no agent_response"},
            agent="solver.unified-planner",
        )
        scratchpad.register_agentic_response("solver.unified-planner", agent)

        # 4) Handle errors
        if error_text or not agent:
            reason = error_text or "planner returned no agent_response"
            scratchpad.tlog.solver(f"[unified.plan] ERROR: {reason}")

            # Use factory method to create error coordinator output
            error_coordinator_out = UnifiedCoordinatorOut.for_error()
            plan = error_coordinator_out.to_plan(
                error_context={"error": reason, **elog},
                internal_thinking=internal_thinking,
            )
            return error_coordinator_out, plan, {}, {}

        # 5) Parse successful response
        unified_coordinator_out = UnifiedCoordinatorOut.model_validate(agent)
        policy = agent.get("policy") or {}
        decision = agent.get("decision") or {}

        # 6) Build plan from coordinator output (handles all cases internally)
        plan = unified_coordinator_out.to_plan()

        # 7) Tlog signals for observability
        next_step = (decision.get("next_step") or "llm_only").strip().lower()

        if next_step == "clarification_only":
            scratchpad.tlog.solver("[unified.plan] clarification_only — coordinator requires unblockers.")
        elif plan.mode == "llm_only":
            scratchpad.tlog.solver(
                "[unified.plan] llm_only; skipping execution; "
                f"instructions_for_downstream={decision.get('instructions_for_downstream') or ''}"
            )
        else:
            slots = list((decision.get("output_contract") or {}).keys())
            scratchpad.tlog.solver(
                f"[unified.plan] step={plan.mode}; "
                f"tools={[t.id for t in (plan.tools or [])]}; slots={slots}; "
                f"instructions_for_downstream={(decision.get('instructions_for_downstream') or '')[:200]}"
            )

        return unified_coordinator_out, plan, decision, policy

    # -------- solution entry point --------

    async def solve(
            self,
            *,
            request_id: str,
            user_text: str,
            policy_summary: str = "",
            topic_hint: Optional[str] = None,
            topics: Optional[List[str]] = None,
            allowed_plugins: Optional[List[str]] = None,
            prefs_hint: Optional[dict] = None,
            context_hint: str = "",
            materialize_turn_ids: Optional[List[str]] = None,
            scratchpad: TurnScratchpad = None
    ) -> SolveResult:
        """
        Unified-coordinator-first flow.
        - Plan with unified coordinator (returns SolutionPlan + raw decision/policy).
        - If clarification_only → return plan.
        - If llm_only → return plan (FinalAnswer will use plan.instructions_for_downstream).
        - If codegen → run codegen with decision as `solvability`, analyze execution, return.
        """

        topics = topics or []

        runtime_ctx = await self.context_rag_client.runtime_ctx()
        # reconciled context hint
        context_hint = f"{context_hint or ''}".strip()

        ctx = {
            "request_id": request_id,
            "text": user_text,
            "topics": topics,
            "policy_summary": policy_summary,
            "context_hint": context_hint,
            "topic_hint": topic_hint or ", ".join((topics or [])),
            "prefs_hint": prefs_hint or {},
            "runtime_ctx": runtime_ctx,
        }
        track_id = runtime_ctx.get("track_id")
        bundle_id = runtime_ctx.get("bundle_id")

        # -------- PLAN (Unified Coordinator) with accounting --------
        async with with_accounting(
                bundle_id,
                track_id=track_id,
                agent="solver.coordinator",
                metadata={"track_id": track_id, "agent": "solver.coordinator"}
        ):
            unified_coordinator_out, plan, decision, policy = await self.coordinate(
                ctx=ctx,
                scratchpad=scratchpad,
                allowed_plugins=allowed_plugins,
                allowed_ids=None,
            )

        result: Dict[str, Any] = {"plan": plan, "execution": None}

        # short-circuit: clarification requested
        if plan.mode == "clarification_only":
            scratchpad.tlog.solver("[solver] mode=clarification_only; returning questions to the orchestrator.")
            return SolveResult(result)

        # llm_only: no runtime execution — FinalAnswer will act guided by plan.instructions_for_downstream
        if plan.mode == "llm_only":
            scratchpad.tlog.solver(
                "[solver] mode=llm_only; skipping codegen; FinalAnswer should follow instructions_for_downstream. "
                f"instructions_for_downstream={plan.instructions_for_downstream or ''}"
            )
            return SolveResult(result)

        # -------- REACT path --------
        s_res = None
        try:
            scratchpad.tlog.solver(
                f"[solver.codegen] mode={unified_coordinator_out.decision.next_step}; slots={list((unified_coordinator_out.decision.output_contract or {}).keys())}"
            )
        except Exception:
            pass
        # if plan.mode == "codegen":
        #     plan.mode = "react_loop"
        if plan.mode == "react_loop":
            s_res = await self.run_react(scratchpad,
                                         coordinator_out=unified_coordinator_out,
                                         allowed_plugins=allowed_plugins,
                                         materialize_turn_ids=materialize_turn_ids,
                                         ctx=ctx)
        elif plan.mode == "codegen":
            s_res = await self.run_codegen(scratchpad,
                                           coordinator_out=unified_coordinator_out,
                                           allowed_plugins=allowed_plugins,
                                           materialize_turn_ids=materialize_turn_ids,
                                           ctx=ctx)

        s_res = s_res or {}

        rounds = s_res.get("rounds") or []
        execution = analyze_execution(rounds=rounds, plan=plan, scratchpad=scratchpad, log=self.log)
        result["codegen"] = s_res
        result["execution"] = execution

        try:
            ok = "ok" if not (execution.error) else "failed"
            filled = sorted([k for k,v in (execution.deliverables or {}).items() if isinstance((v or {}).get("value"), dict)])
            scratchpad.tlog.solver(f"[solver.result] status={ok}; produced={filled}; error={(execution.error or '')[:220]}")
        except Exception:
            pass

        return SolveResult(result)

    # -------- react runtime --------
    async def run_react(self,
                        scratchpad: TurnScratchpad,
                        coordinator_out: UnifiedCoordinatorOut,
                        ctx: dict,
                        allowed_plugins: Optional[List[str]] = None,
                        materialize_turn_ids: Optional[List[str]] = None,):

        # Create a ReAct solver using the existing subsystems
        react_solver = ReactSolver(
            service=self.svc,
            logger=self.log,
            tool_manager=self,     # exposes .tools to React
            skills_subsystem=self.skills,
            scratchpad=scratchpad,
            comm=self.comm,
            comm_context=self.comm_context,
            hosting_service=self.hosting_service,
            turn_view_class=self.turn_view_class,
            react_decision_stream=react_decision_stream,
            solution_gen_stream=None
        )

        # Run ReAct; we won't trust its own analysis—centralize via analyze_execution below
        react_res = await react_solver.run(
            coordinator_out=coordinator_out,
            allowed_plugins=allowed_plugins or [],
            materialize_turn_ids=materialize_turn_ids or [],
            runtime_ctx=ctx.get("runtime_ctx") or {},
        )
        return react_res

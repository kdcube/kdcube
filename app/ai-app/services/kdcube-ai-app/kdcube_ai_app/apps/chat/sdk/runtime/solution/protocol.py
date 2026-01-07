# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/protocol.py

import json
from typing import Literal, Dict, Any, List, Optional
from pydantic import Field, BaseModel

from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SlotSpec, SolutionPlan, PlannedTool
from kdcube_ai_app.apps.chat.sdk.util import _shorten

# ---------- Shared enums ----------


ProjectMode = Literal[
    "plan_breakdown",        # produce project plan / breakdown first
    "stage_zoom_in",         # drill into a specific stage/artifact
    "single_deliverable",    # one small, atomic deliverable
    "integration_zoom_out"   # integrate/align multiple parts/stages
]
TurnScope = Literal["new_request", "continue_request"]
NextStep = Literal["codegen", "react_loop", "llm_only", "clarification_only"]


# ---------- Policy (advisory posture/scope only — no slots here) ----------
class CoordinatorPolicy(BaseModel):
    objective_hint: str = ""
    turn_scope: TurnScope = "new_request"
    project_mode: ProjectMode = "plan_breakdown"
    context_use: Literal["always", "auto", "never"] = "auto"
    scope_notes: str = ""
    avoid: List[str] = Field(default_factory=lambda: ["avoid giant one-shot solutions"])
    turn_scope_contract: Dict[str, Any] = Field(default_factory=lambda: {
        "unit_of_work": "plan_surface",     # plan_surface | stage_solution | integrational_alignment | single_note
        "max_depth": "shallow",             # shallow | medium | deep
        "notes": "Select a narrow scope and complete it this turn."
    })

    # Thin per-slot budget hint. Runtime derives global caps from this.
    # Only put per-slot numbers here; everything global is derived.
    sb: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-slot budget hints for solver runtime. Shape:\n"
            "{\n"
            "  '<slot_id>': {\n"
            "    'explore': int,  # max exploration rounds for this slot (search, data discovery, exploration APIs, etc.)\n"
            "    'exploit': int,  # max exploitation rounds for this slot (transforms, analysis, generation using known inputs)\n"
            "    'render': int,   # max render/write rounds for this slot - rendering/write operations (write_pdf/html/pptx/file, image render, etc.). Only for file slot\n"
            "    'ctx_reads': int # max full-context reads for this slot\n"
            "  }, ...\n"
            "}\n"
            "All values are max counts in *rounds* for that slot. Global caps and per-slot\n"
            "limits are derived at runtime from these hints."
        )
    )

# ---------- Decision (Tool selection + Solvability; binding) ----------
class SelectedTool(BaseModel):
    name: str                                 # must match catalog id
    reason: str = ""
    confidence: float = Field(0.6, ge=0.0, le=1.0)
    # parameters: Dict[str, Any] = Field(default_factory=dict)  # minimal scaffolding only

class UnifiedDecision(BaseModel):
    solvable: bool = True
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reasoning: str = ""                          # ≤25–30 words; user-safe
    selected_tools: List[SelectedTool] = Field(default_factory=list)
    context_use: bool = True                     # binding for execution path

    # Authoritative, typed dynamic contract: slot_name -> SlotSpec
    output_contract: Dict[str, SlotSpec] = Field(default_factory=dict)

    instructions_for_downstream: str = ""        # 1–3 lines; if llm_only, say why
    next_step: NextStep = "llm_only"             # codegen | llm_only | clarification_only | react_loop
    clarification_questions: List[str] = Field(default_factory=list)  # only if next_step=clarification_only


def _build_compact_guidance(c_out: "UnifiedCoordinatorOut") -> str:
    """Build compact guidance for quick reference."""
    pol = c_out.policy
    dec = c_out.decision

    parts: list[str] = []

    if pol.objective_hint:
        parts.append(f"Turn Objective: {pol.objective_hint}")

    if pol.turn_scope and pol.project_mode:
        parts.append(f"Scope: {pol.turn_scope} | Mode: {pol.project_mode}")

    if pol.turn_scope_contract:
        uow = pol.turn_scope_contract.get("unit_of_work")
        depth = pol.turn_scope_contract.get("max_depth")
        if uow or depth:
            parts.append(f"Unit: {uow or 'N/A'} | Depth: {depth or 'N/A'}")

    if dec.reasoning:
        parts.append(f"Reasoning: {dec.reasoning}")

    if dec.instructions_for_downstream:
        parts.append(f"Instructions: {dec.instructions_for_downstream}")

    return " • ".join(parts) if parts else "(no guidance)"

def format_turn_decision_line(c_out: "UnifiedCoordinatorOut") -> str:
    """
    Single-line coordinator summary with full (untruncated) fields.
    Pipe-separated for compact display in playbooks.
    """
    pol = c_out.policy
    dec = c_out.decision

    def _json(v: Any) -> str:
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)

    parts: list[str] = []
    def _add(label: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            if not value.strip():
                return
            parts.append(f"{label}={value}")
            return
        if isinstance(value, (list, dict)):
            if not value:
                return
            parts.append(f"{label}={_json(value)}")
            return
        parts.append(f"{label}={value}")

    _add("Turn Objective", pol.objective_hint)
    _add("turn_scope", pol.turn_scope)
    _add("project_mode", pol.project_mode)
    _add("context_use", pol.context_use)
    _add("scope_notes", pol.scope_notes)
    _add("avoid", pol.avoid)
    _add("turn_scope_contract", pol.turn_scope_contract)
    _add("sb", pol.sb)
    _add("solvable", dec.solvable)
    _add("confidence", dec.confidence)
    _add("reasoning", dec.reasoning)
    _add("next_step", dec.next_step)
    _add("context_use_decision", dec.context_use)
    _add("instructions_for_downstream", dec.instructions_for_downstream)
    _add("clarification_questions", dec.clarification_questions)
    return " | ".join(parts) if parts else "(no decision line)"
def _build_detailed_guidance(c_out: "UnifiedCoordinatorOut") -> str:
    """Build detailed guidance for downstream agents (unifies _build_planner_guidance and presentation_detailed)."""
    pol = c_out.policy
    dec = c_out.decision
    tools = dec.selected_tools or []
    contract = dec.output_contract or {}
    header_lines: list[str] = []
    if pol.objective_hint:
        header_lines.append(f"- Turn Objective: {pol.objective_hint}")
    if pol.turn_scope or pol.project_mode:
        header_lines.append(f"- Scope: {pol.turn_scope} | {pol.project_mode}")
    if pol.turn_scope_contract:
        uow = pol.turn_scope_contract.get("unit_of_work")
        depth = pol.turn_scope_contract.get("max_depth")
        if uow or depth:
            header_lines.append(f"- Depth: {uow or 'N/A'} | {depth or 'N/A'}")

    # Policy section
    policy_lines = []
    if pol.objective_hint:
        policy_lines.append(f"- objective_hint: {pol.objective_hint}")
    if pol.turn_scope:
        policy_lines.append(f"- turn_scope: {pol.turn_scope}")
    if pol.project_mode:
        policy_lines.append(f"- project_mode: {pol.project_mode}")
    if pol.context_use:
        policy_lines.append(f"- context_use: {pol.context_use}")
    if pol.scope_notes:
        policy_lines.append(f"- scope_notes: {pol.scope_notes}")
    if pol.avoid:
        policy_lines.append(f"- avoid: {', '.join(pol.avoid)}")

    # Turn scope contract details
    if pol.turn_scope_contract:
        uow = pol.turn_scope_contract.get("unit_of_work")
        depth = pol.turn_scope_contract.get("max_depth")
        notes = pol.turn_scope_contract.get("notes")
        policy_lines.append(f"- unit_of_work: {uow or 'N/A'}")
        policy_lines.append(f"- max_depth: {depth or 'N/A'}")
        if notes:
            policy_lines.append(f"- contract_notes: {notes}")

    # Tools section
    tool_lines = []
    for i, t in enumerate(tools, 1):
        name = t.name or ""
        reason = t.reason.strip() or ""
        conf = t.confidence or 0.0
        tool_line = [
            f"{i}. tool_id: {name}",
            f"   confidence: {conf}"
        ]
        if reason:
            tool_line.append(f"   reason: {reason}")
        tool_lines.append(
            "\n".join(tool_line)
        )

    # Contract slots section
    slot_lines = []
    for slot, spec in contract.items():
        dtype = spec.type or "inline"
        fmt = (spec.format or spec.mime or "") or ""
        desc = (spec.description or "").strip()
        slot_lines.append(f"- {slot} ({dtype}{f', {fmt}' if fmt else ''}): {desc[:160]}")

    # Solvability and decision section
    decision_lines = []
    decision_lines.append(f"- solvable: {dec.solvable}")
    decision_lines.append(f"- confidence: {dec.confidence}")
    if dec.reasoning:
        decision_lines.append(f"- reasoning: {dec.reasoning}")
    decision_lines.append(f"- next_step: {dec.next_step}")
    decision_lines.append(f"- context_use: {dec.context_use}")

    # Budget hints section
    budget_lines = []
    if pol.sb:
        for slot_id, budget in pol.sb.items():
            explore = budget.get("explore", 0)
            exploit = budget.get("exploit", 0)
            render = budget.get("render", 0)
            ctx_reads = budget.get("ctx_reads", 0)
            budget_lines.append(
                f"- {slot_id}: explore={explore}, exploit={exploit}, render={render}, ctx_reads={ctx_reads}"
            )

    # Coordinator instructions
    downstream = (dec.instructions_for_downstream or "").strip()

    # Build final output
    parts = [
        "# Planner Guidance",
        "",
        "## Coordinator Turn Decision",
        *([*header_lines] if header_lines else ["(none)"]),
        "",
        "## Policy (Advisory Posture/Scope)",
        *([*policy_lines] if policy_lines else ["(none)"]),
        "",
        "## Decision & Solvability",
        *([*decision_lines] if decision_lines else ["(none)"]),
        "",
        "## Contract Slots (Output Deliverables)",
        *([*slot_lines] if slot_lines else ["(none)"]),
    ]

    if budget_lines:
        parts.extend([
            "",
            "## Budget Hints (Per-Slot)",
            *budget_lines,
        ])

    parts.extend([
        "",
        "## Coordinator Instructions for Downstream",
        downstream or "(none)",
        "",
        "## Notes",
        "- Treat recommended tools as hints, not a fixed chain.",
        "- You may choose tools/params that best fill the contract slots.",
        "- You may adapt steps if context suggests a better path.",
        ])

    return "\n".join(parts)


class UnifiedCoordinatorOut(BaseModel):
    policy: CoordinatorPolicy
    decision: UnifiedDecision

    @staticmethod
    def for_error() -> "UnifiedCoordinatorOut":
        """Create a minimal UnifiedCoordinatorOut for error cases."""
        return UnifiedCoordinatorOut(
            policy=CoordinatorPolicy(
                objective_hint="",
                turn_scope="new_request",
                project_mode="plan_breakdown",
                context_use="never",
                scope_notes="Error state - planner failed",
                avoid=[],
                turn_scope_contract={
                    "unit_of_work": "single_note",
                    "max_depth": "shallow",
                    "notes": "Error recovery mode"
                },
            ),
            decision=UnifiedDecision(
                next_step="llm_only",
                reasoning="Planner error",
                confidence=0.0,
                selected_tools=[],
                clarification_questions=[],
                instructions_for_downstream="",
                output_contract={},
                solvable=False,
                context_use=False,
            ),
        )

    def to_plan(
            self,
            *,
            error_context: Optional[Dict[str, Any]] = None,
            internal_thinking: Optional[str] = None,
    ) -> SolutionPlan:
        """
        Build a SolutionPlan from coordinator output.
        Handles all cases: failure, clarification, normal execution.

        Args:
            error_context: Optional error log dict for building failure plans
            internal_thinking: Optional internal thinking text for failure diagnostics
        """
        # 1) Handle failure case
        if error_context:
            error_text = error_context.get("error", "Unknown planner error")
            md = (
                "Solver.Coordinator error.\n\n"
                f"**Reason:** {error_text}\n\n"
                "We could not construct a decision for this turn."
            )
            failure_struct = {
                "unified_planner": {
                    "internal_thinking": internal_thinking,
                    "raw_data": error_context
                }
            }
            return SolutionPlan(
                mode="llm_only",
                tools=[],
                confidence=0.0,
                reasoning="Planner failed to produce a valid decision.",
                clarification_questions=[],
                instructions_for_downstream="Explain failure briefly and request the user to rephrase or narrow scope.",
                error=error_text,
                failure_presentation={"markdown": md, "struct": failure_struct},
                tool_router_notes="",
                output_contract={},
                service={"unified_planner": {"error": error_text, "log": error_context}},
                solvable=False,
            )

        # 2) Extract decision and policy
        dec = self.decision
        pol = self.policy

        next_step = (dec.next_step or "llm_only").strip()
        # Validate and normalize mode
        if next_step not in ("codegen", "react_loop", "llm_only", "clarification_only"):
            next_step = "llm_only"

        # 3) Handle clarification case
        if next_step == "clarification_only":
            return SolutionPlan(
                mode="clarification_only",
                tools=[],
                confidence=float(dec.confidence or 0.0),
                reasoning=str(dec.reasoning or ""),
                clarification_questions=list(dec.clarification_questions or []),
                instructions_for_downstream=str(dec.instructions_for_downstream or ""),
                output_contract={},
                solvable=False,
                service={
                    "unified_planner": {
                        "policy": pol.model_dump(),
                        "decision": dec.model_dump()
                    }
                },
            )

        # 4) Handle normal execution (codegen, react_loop, llm_only)
        mode = next_step
        output_contract = dec.output_contract or {}
        confidence = float(dec.confidence or 0.0)
        reasoning = str(dec.reasoning or "")

        planned_tools: List[PlannedTool] = []
        for t in dec.selected_tools or []:
            planned_tools.append(
                PlannedTool(
                    id=t.name or "",
                    reason=t.reason or "",
                    confidence=float(t.confidence or 0.0),
                )
            )

        # Build both compact and detailed guidance
        compact_guidance = _build_compact_guidance(self)
        detailed_guidance = _build_detailed_guidance(self)
        solvable = mode in ("codegen", "react_loop", "llm_only")

        return SolutionPlan(
            mode=mode,
            tools=planned_tools,
            confidence=confidence,
            reasoning=reasoning,
            clarification_questions=list(dec.clarification_questions or []),
            instructions_for_downstream=detailed_guidance,
            instructions_for_downstream_compact=compact_guidance,
            tool_router_notes="",
            output_contract=output_contract,
            service={
                "unified_planner": {
                    "policy": pol.model_dump(),
                    "decision": dec.model_dump(),
                }
            },
            solvable=solvable,
        )

    @property
    def presentation_compact(self) -> str:
        """Derive a compact 'objective' string for the Decision agent from the full coordinator output."""
        return _build_compact_guidance(self)

    @property
    def presentation_detailed(self) -> str:
        """Derive a compact 'objective' string for the Decision agent from the full coordinator output."""
        return _build_detailed_guidance(self)

def compose_objective(c_out: UnifiedCoordinatorOut,
                      user_text: str | None = None,
                      user_message_truncation: int = 250) -> str:

    obj = c_out.presentation_detailed
    if user_text:
        obj += "\nUser Request (truncated! Focus on Coordinator problem framing. If you need to see user message in full, read it from context): " + _shorten(user_text.strip(), user_message_truncation)
    return obj

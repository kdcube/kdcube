# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/strategy_and_budget.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Any

from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SlotSpec

Strategy = Literal["explore", "exploit", "render", "finish"]


# ---------- Per-stage budget ----------

@dataclass
class StageCaps:
    """
    Turn-level per-stage budget hints.

    All values are in *decision rounds* for that stage:
      - min_*: derived minimum number of rounds (advisory)
      - max_*: advisory upper bound for that strategy
      - render: max number of render/write rounds for this slot

    Runtime enforces only global caps; per-stage caps are for guidance + logging
    and for the decision agent to read in BUDGET_STATE.
    """
    min_explore: int = 0
    max_explore: int = 2
    min_exploit: int = 0
    max_exploit: int = 3
    render: int = 2
    ctx_reads: int = 1


@dataclass
class StageUsage:
    explore_used: int = 0
    exploit_used: int = 0
    render_used: int = 0
    context_reads_used: int = 0


@dataclass
class StageBudget:
    stage_id: str
    caps: StageCaps = field(default_factory=StageCaps)
    usage: StageUsage = field(default_factory=StageUsage)

    def note_decision_round(self, *, strategy: Strategy, is_render_round: bool = False) -> None:
        """
        Called once per ReAct iteration for the *current* stage.

        - strategy='explore' → explore_used++
        - strategy='exploit' → exploit_used++
        - strategy='render'  → normally combined with is_render_round=True
        - is_render_round=True → render_used++
        """
        if strategy == "explore":
            self.usage.explore_used += 1
        elif strategy == "exploit":
            self.usage.exploit_used += 1

        if is_render_round:
            self.usage.render_used += 1
        # "finish" does not affect per-stage usage directly

    def note_context_read(self) -> None:
        self.usage.context_reads_used += 1

# ---------- Global budget ----------

@dataclass
class GlobalBudget:
    # All rounds (explore + exploit + render)
    max_decision_rounds: int = 12
    decision_rounds_used: int = 0

    # All tool calls (any type)
    max_tool_calls: int = 20
    tool_calls_used: int = 0

    # Strategy-specific aggregates (derived from per-slot hints)
    max_explore_rounds: int = 0
    explore_rounds_used: int = 0

    max_exploit_rounds: int = 0
    exploit_rounds_used: int = 0

    max_render_rounds: int = 0
    render_rounds_used: int = 0

    # Decision rerun budget (show_artifacts -> decision loop)
    max_decision_reruns: int = 2
    decision_reruns_used: int = 0

    # Full-context reads (show_artifacts)
    ctx_reads: int = 0
    context_reads_used: int = 0

    def remaining_decision_rounds(self) -> int:
        return max(0, self.max_decision_rounds - self.decision_rounds_used)

    def remaining_tool_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    def remaining_decision_reruns(self) -> int:
        return max(0, self.max_decision_reruns - self.decision_reruns_used)

    def remaining_context_reads(self) -> int:
        return max(0, self.ctx_reads - self.context_reads_used)

    def can_continue(self) -> bool:
        """
        Hard gate for the runtime: if False, no new decisions with tool calls should be started.
        """
        return (
                self.remaining_decision_rounds() > 0
                and self.remaining_tool_calls() > 0
        )


# ---------- Turn-level state ----------

@dataclass
class BudgetState:
    global_budget: GlobalBudget = field(default_factory=GlobalBudget)
    stages: Dict[str, StageBudget] = field(default_factory=dict)
    current_stage_id: Optional[str] = None

    def set_current_stage(self, stage_id: str) -> None:
        if stage_id in self.stages:
            self.current_stage_id = stage_id

    def current_stage(self) -> Optional[StageBudget]:
        if self.current_stage_id:
            return self.stages.get(self.current_stage_id)
        return None

    def note_decision_round(
            self,
            *,
            strategy: Strategy,
            tool_ids: List[str],
            is_render_round: bool = False,
    ) -> None:
        """
        Called once per ReAct iteration AFTER tools have run
        (or on clarify/exit with tool_ids=[]).

        - strategy='explore' → global.explore_rounds_used++
        - strategy='exploit' → global.exploit_rounds_used++
        - strategy='render'  → counted via is_render_round=True
        - any strategy       → decision_rounds_used++, tool_calls_used+=len(tool_ids)
        """
        gb = self.global_budget
        gb.decision_rounds_used += 1
        gb.tool_calls_used += len(tool_ids)

        # Strategy-specific global counters
        if strategy == "explore":
            gb.explore_rounds_used += 1
        elif strategy == "exploit":
            gb.exploit_rounds_used += 1
        # 'render' is tracked through render_rounds_used

        if is_render_round:
            gb.render_rounds_used += 1

        st = self.current_stage()
        if st is not None:
            st.note_decision_round(strategy=strategy, is_render_round=is_render_round)

    def must_finish(self) -> bool:
        """
        Hard gate: if True, runtime must not start another decision with tool calls.
        We still allow a final 'finish' decision to EXIT/COMPLETE if needed.
        """
        return not self.global_budget.can_continue()

    def note_decision_rerun(self) -> None:
        """Track a decision rerun (show_artifacts -> decision)."""
        self.global_budget.decision_reruns_used += 1

    def note_context_read(self) -> None:
        """Track a full-context read (show_artifacts)."""
        self.global_budget.context_reads_used += 1
        st = self.current_stage()
        if st is not None:
            st.note_context_read()

# ---------- Helpers ----------
def _normalize_max(value: Any, default_max: int) -> int:
    try:
        if value is None:
            return max(0, default_max)
        return max(0, int(value))
    except Exception:
        return max(0, default_max)


def _derive_stage_caps_for_slot(slot_id: str, raw_hint: Any) -> StageCaps:
    """
    Map thin per-slot sb hint into StageCaps.
    raw_hint is expected to be a dict with:
      explore: int
      exploit: int
      render: int
      ctx_reads: int
    All values are best-effort; missing/invalid → defaults.
    """
    h = raw_hint or {}
    if not isinstance(h, dict):
        h = {}

    max_explore = _normalize_max(h.get("explore"), 2)
    max_exploit = _normalize_max(h.get("exploit"), 3)
    render = _normalize_max(h.get("render"), 0)
    ctx_reads = _normalize_max(h.get("ctx_reads"), 0)
    min_explore = 1 if max_explore > 0 else 0
    min_exploit = 1 if max_exploit > 0 else 0

    return StageCaps(
        min_explore=min_explore,
        max_explore=max_explore,
        min_exploit=min_exploit,
        max_exploit=max_exploit,
        render=render,
        ctx_reads=ctx_reads,
    )


def _derive_global_caps_from_stage_caps(
        caps_by_stage: Dict[str, StageCaps],
        *,
        default_max_decision_rounds: int = 12,
        default_max_tool_calls: int = 20,
) -> GlobalBudget:
    """
    Aggregate per-stage caps into global caps.

    global_max_decision_rounds = sum(max_explore + max_exploit + render) over all stages.
    global_max_explore_rounds  = sum(max_explore)
    global_max_exploit_rounds  = sum(max_exploit)
    global_max_render_rounds   = sum(render)

    If totals are 0, fall back to defaults.
    """
    total_max_explore = sum(c.max_explore for c in caps_by_stage.values())
    total_max_exploit = sum(c.max_exploit for c in caps_by_stage.values())
    total_max_render = sum(c.render for c in caps_by_stage.values())
    total_max_context_reads = sum(c.ctx_reads for c in caps_by_stage.values())

    max_decision_rounds = total_max_explore + total_max_exploit + total_max_render
    if max_decision_rounds <= 0:
        max_decision_rounds = default_max_decision_rounds

    # Simple heuristic: allow one tool call per decision round.
    max_tool_calls = max_decision_rounds if max_decision_rounds > 0 else default_max_tool_calls

    gb = GlobalBudget(
        max_decision_rounds=max_decision_rounds,
        max_tool_calls=max_tool_calls,
        max_explore_rounds=total_max_explore,
        max_exploit_rounds=total_max_exploit,
        max_render_rounds=total_max_render,
        ctx_reads=total_max_context_reads,
    )
    return gb


def init_budget_state_for_turn(
        output_contract: Dict[str, SlotSpec],
        primary_stage_ids: Optional[List[str]] = None,
        *,
        sb_hint: Optional[Dict[str, Any]] = None,
        default_global_max_decision_rounds: int = 12,
        default_global_max_tool_calls: int = 20,
) -> BudgetState:
    """
    Initialize BudgetState for a turn: one StageBudget per 'stage' (slot).

    primary_stage_ids: optional explicit order; otherwise uses output_contract.keys().

    sb_hint:
      Coordinator-provided thin budgets, shape:
        {
          "<slot_id>": {
            "explore": int,
            "exploit": int,
            "render": int,
            "ctx_reads": int
          },
          ...
        }
      All global caps are derived from these per-slot hints.
    """
    stage_ids = primary_stage_ids or list(output_contract.keys())
    raw = sb_hint or {}
    if not isinstance(raw, dict):
        raw = {}

    # Build per-stage caps
    caps_by_stage: Dict[str, StageCaps] = {}
    for sid in stage_ids:
        caps_by_stage[sid] = _derive_stage_caps_for_slot(sid, raw.get(sid))

    # Derive global caps from per-stage caps
    gb = _derive_global_caps_from_stage_caps(
        caps_by_stage,
        default_max_decision_rounds=default_global_max_decision_rounds,
        default_max_tool_calls=default_global_max_tool_calls,
    )

    stages = {sid: StageBudget(stage_id=sid, caps=caps_by_stage[sid]) for sid in stage_ids}

    bs = BudgetState(
        global_budget=gb,
        stages=stages,
        current_stage_id=stage_ids[0] if stage_ids else None,
    )
    return bs

def format_budget_for_llm(budget: BudgetState) -> str:
    """
    Extremely compact budget snapshot for the decision LLM.

    Format:
      BUDGET_STATE: global(decisions left D/T, tools left C/T[, explore left E/T, exploit left X/T, render left R/T])
                    stage[slot](explore left e/E, exploit left x/X[, render left r/R])

    Only numbers, no long prose.
    """
    gb = budget.global_budget
    lines: list[str] = []

    def _rem(left: int, total: int) -> str:
        total = max(0, int(total))
        left = max(0, int(left))
        if total <= 0:
            return "0/0"
        return f"{left}/{total}"

    # ---- Global line ----
    max_dec = getattr(gb, "max_decision_rounds", 0) or 0
    used_dec = getattr(gb, "decision_rounds_used", 0) or 0
    max_tools = getattr(gb, "max_tool_calls", 0) or 0
    used_tools = getattr(gb, "tool_calls_used", 0) or 0

    dec_left = max_dec - used_dec
    tools_left = max_tools - used_tools

    global_parts = [
        f"decisions left { _rem(dec_left, max_dec) }",
        f"tools left { _rem(tools_left, max_tools) }",
    ]

    # Optional strategy-specific globals (if you have them in GlobalBudget)
    max_explore_g = getattr(gb, "max_explore_rounds", 0) or 0
    used_explore_g = getattr(gb, "explore_rounds_used", 0) or 0
    if max_explore_g > 0:
        explore_left_g = max_explore_g - used_explore_g
        global_parts.append(f"explore left { _rem(explore_left_g, max_explore_g) }")

    max_exploit_g = getattr(gb, "max_exploit_rounds", 0) or 0
    used_exploit_g = getattr(gb, "exploit_rounds_used", 0) or 0
    if max_exploit_g > 0:
        exploit_left_g = max_exploit_g - used_exploit_g
        global_parts.append(f"exploit left { _rem(exploit_left_g, max_exploit_g) }")

    max_render_g = getattr(gb, "max_render_rounds", 0) or 0
    used_render_g = getattr(gb, "render_rounds_used", 0) or 0
    if max_render_g > 0:
        render_left_g = max_render_g - used_render_g
        global_parts.append(f"render left { _rem(render_left_g, max_render_g) }")

    max_rerun = getattr(gb, "max_decision_reruns", 0) or 0
    used_rerun = getattr(gb, "decision_reruns_used", 0) or 0
    if max_rerun > 0:
        rerun_left = max_rerun - used_rerun
        global_parts.append(f"decision_reruns left { _rem(rerun_left, max_rerun) }")

    max_ctx = getattr(gb, "ctx_reads", 0) or 0
    used_ctx = getattr(gb, "context_reads_used", 0) or 0
    if max_ctx > 0:
        ctx_left = max_ctx - used_ctx
        global_parts.append(f"context_reads left { _rem(ctx_left, max_ctx) }")

    lines.append("BUDGET_STATE: " + "global(" + ", ".join(global_parts) + ")")

    # ---- Current stage line ----
    cur = budget.current_stage()
    if cur:
        caps = cur.caps
        usage = cur.usage

        # Support both NEW and OLD caps models

        # Explore max
        if hasattr(caps, "max_explore"):  # new model
            max_explore = caps.max_explore
        else:  # old model: use hard cap as "max"
            max_explore = getattr(caps, "explore_hard_cap", 0) or getattr(caps, "explore_soft_cap", 0)

        # Exploit max
        if hasattr(caps, "max_exploit"):  # new model
            max_exploit = caps.max_exploit
        else:
            max_exploit = getattr(caps, "exploit_hard_cap", 0) or getattr(caps, "exploit_soft_cap", 0)

        # Render max (only exists in new model; else 0)
        render = getattr(caps, "render", 0) or 0

        explore_left = max(0, (max_explore or 0) - getattr(usage, "explore_used", 0))
        exploit_left = max(0, (max_exploit or 0) - getattr(usage, "exploit_used", 0))
        render_left = max(0, (render or 0) - getattr(usage, "render_used", 0))
        max_ctx = getattr(caps, "ctx_reads", 0) or 0
        ctx_left = max(0, max_ctx - getattr(usage, "context_reads_used", 0))

        stage_parts: list[str] = []
        if max_explore:
            stage_parts.append(f"explore left { _rem(explore_left, max_explore) }")
        if max_exploit:
            stage_parts.append(f"exploit left { _rem(exploit_left, max_exploit) }")
        if render:
            stage_parts.append(f"render left { _rem(render_left, render) }")
        if max_ctx:
            stage_parts.append(f"context_reads left { _rem(ctx_left, max_ctx) }")

        lines.append(f"        stage[{cur.stage_id}](" + ", ".join(stage_parts) + ")")

    return "\n".join(lines)

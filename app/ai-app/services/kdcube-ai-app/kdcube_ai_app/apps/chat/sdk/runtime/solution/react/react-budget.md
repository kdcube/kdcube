# ReAct Budget Model

This document explains how the ReAct budget works, how it is computed, how it is presented to the decision model, and how coordinator guidance shapes it.

## Overview

Budgets are tracked per turn in `BudgetState` (see `strategy_and_budget.py`). They serve two purposes:

1) **Hard gates**: stop tool execution when global limits are exceeded.
2) **Planning hints**: guide the decision model on how many rounds are left for explore/exploit/render and context reads.

The budget snapshot is emitted into the decision journal as `BUDGET_STATE`.

## Inputs to the budget

Budgets are derived from:
- **Output contract** (slot list) for the turn
- **Coordinator policy** (scope, deliverable count, complexity, optional SB hints)
- **Default caps** (fallbacks if no hints are provided)

Coordinator is the primary owner of scope and budgeting. It chooses:
- number of deliverables
- max depth (shallow/medium/deep)
- plan shape (single deliverable vs multi‑step)

These signals are translated into budget caps in `strategy_and_budget.py`.

## Global vs stage budgets

### GlobalBudget (hard gate)

Tracks:
- decision rounds
- tool calls
- explore rounds
- exploit rounds
- render rounds
- decision reruns
- context reads

If **decision rounds** or **tool calls** are exhausted, the runtime enters a terminal state (`must_finish`).

### Stage budgets (per slot)

Each slot has an advisory stage budget:
- explore
- exploit
- render
- ctx_reads

Stage budgets guide the decision model and are used for exploit overdraft eligibility.

## BUDGET_STATE format

`format_budget_for_llm` produces a compact snapshot:

```
BUDGET_STATE: global(decisions left D/T, tools left C/T, explore left E/T, exploit left X/T, render left R/T, decision_reruns left r/R, context_reads left c/C)
        stage[slot_id](explore left e/E, exploit left x/X, render left r/R, context_reads left c/C)
```

When wrap‑up is active, all remaining values are forced to `0/*` in the snapshot.

## Wrap‑up behavior

Wrap‑up is a **single extra decision round** used to map existing artifacts to remaining slots.

When wrap‑up is activated:
- The runtime **disables** exploit overdraft.
- The budget snapshot shows **zero remaining** for explore/exploit/render/reruns/context reads.
- The decision agent must only map slots (no tool calls).

## Exploit overdraft

If global limits are exhausted but exploit rounds remain, the runtime may allow a limited number of exploit rounds:
- `allow_exploit_overdraft = True`
- Only `strategy=exploit` + `action=call_tool` is allowed
- Any non‑exploit tool call exits the loop (`exploit_overdraft_blocked`)

Exploit overdraft is **not permitted** once wrap‑up starts.

## Where this is implemented

- `strategy_and_budget.py` — budget derivation and formatting
- `react.py` — must_finish gate, wrap‑up activation, exploit overdraft control
- `react-state-machine.md` — routing model
- `coordinator.py` — scope/budget policy that seeds the caps


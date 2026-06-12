---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-budget-README.md
title: "React Budget"
summary: "Budget model used by React v2 (BudgetStateV2)."
tags: ["sdk", "agents", "react", "budget"]
keywords: ["BudgetStateV2", "token budget", "remaining budget", "budget snapshot"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-state-machine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
---
# ReAct v2 Budget Model

This document describes the current React budget model.
It reflects the shared runtime semantics used by `v2` and `v3`, while `BudgetStateV2`
still names the turn-level strategy budget structure.

---

## Overview

Budget is tracked **per turn** and controls how many decision rounds and tool calls the
ReAct loop can perform.

There are **no slots** and **no per-slot budgets**. Budget is global for the turn.

The decision model sees a snapshot in the timeline’s active state block.

---

## BudgetStateV2

Tracked fields:
- `exploration_budget` / `exploitation_budget`
- `explore_used` / `exploit_used`
- `max_iterations`
- `base_max_iterations`
- `reactive_iteration_credit`
- `reactive_iteration_credit_cap`
- `decision_rounds_used`

Hard gate:
- if `decision_rounds_used >= max_iterations` ⇒ exit with `max_iterations`

Important nuance:
- `max_iterations` is the **effective** round ceiling for the current turn.
- `base_max_iterations` is the original configured limit from `RuntimeCtx.max_iterations`.
- when the active turn consumes a live **reactive** external event such as `followup`, React mints extra iteration credit on that same turn
- effective `max_iterations = base_max_iterations + reactive_iteration_credit`
- `reactive_iteration_credit` is bounded by `reactive_iteration_credit_cap`

This is intentional. A live additive followup should not consume the same fixed loop budget as if it were merely a correction to the prior draft.

Strategy gate:
- a decision proposing `strategy=explore` must have remaining exploration budget
- a decision proposing `strategy=exploit` must have remaining exploitation budget

---

## Snapshot Format (for the decision agent)

Example:
```
Budget v2 (turn-level)
- explore: 1/2 used (remaining 1)
- exploit: 0/2 used (remaining 2)
- decision rounds: 2/6 used (remaining 4)
```

The snapshot appears in the active state block (tail, uncached).

If the turn later consumes a live followup and receives one extra reactive credit, the same snapshot becomes:

```
Budget v2 (turn-level)
- explore: 1/2 used (remaining 1)
- exploit: 0/2 used (remaining 2)
- decision rounds: 2/7 used (remaining 5)
```

The runtime updates the effective ceiling before the next decision gate.

---

## Where it lives

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/budget.py` — BudgetStateV2
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/live_events.py` — reactive external-event iteration credit rules
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/runtime_state.py` — shared runtime state fields
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/runtime.py` — v2 decision/runtime wiring
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v3/runtime.py` — v3 decision/runtime wiring
- `docs/sdk/agents/react/react-state-machine-README.md` — loop and exit conditions

# ReAct v2 Budget Model

This document describes the **v2** budget model used by the ReAct runtime.
It reflects the current `BudgetStateV2` semantics.

---

## Overview

Budget is tracked **per turn** and controls how many decision rounds and tool calls the
ReAct loop can perform.

In v2 there are **no slots** and **no per‑slot budgets**. Budget is global for the turn.

The decision model sees a snapshot in the timeline’s active state block.

---

## BudgetStateV2

Tracked fields:
- `exploration_budget` / `exploitation_budget`
- `explore_used` / `exploit_used`
- `max_iterations`
- `decision_rounds_used`

Hard gate:
- if `decision_rounds_used >= max_iterations` ⇒ exit with `max_iterations`

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

---

## Where it lives

- `react/v2/budget.py` — BudgetStateV2
- `react/v2/runtime.py` — budget gates + decision routing
- `react/doc/react-state-machine-README.md` — loop and exit conditions

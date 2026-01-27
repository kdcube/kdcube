# Coordinator (ReAct) — Role and Signals

This document explains the coordinator’s role in the ReAct solver, the signals it consumes, and how it shapes scope and budget. The coordinator is a planning agent and does not execute tools.

## Role

The coordinator:
- Defines **turn scope** (what is in or out for the current turn).
- Chooses **deliverables** and their types (inline vs file).
- Sets **budget intent** (shallow/medium/deep) and high‑level plan.
- Provides **instructions_for_downstream** for the decision agent and tool execution.

Think of the coordinator as the “turn planner” that sets the contract and budget envelope.

## Inputs (signals it reads)

The coordinator consumes a compact, curated journal that includes:
- **USER_MESSAGE** (full user input)
- **CONVERSATION_HISTORY** (compressed turn log)
- **TURN MEMORIES** (chronological prefs/facts/exceptions)
- **USER FEEDBACK** (chronological feedback items)
- **Current context signals** (route hints, topics, attachments, tools allowed)

It does **not** need the full raw context; the journal is designed for planning.

## Outputs (what it emits)

The coordinator returns a policy object and downstream instructions. Typical fields:
- `turn_scope` (new_request / refine / continue / clarification)
- `project_mode` (single_deliverable / multi_deliverable / multi_step)
- `scope_notes` (constraints or boundaries)
- `output_contract` (slot definitions)
- `instructions_for_downstream` (key guidance for the decision agent)
- `sb` (optional budget hints per slot)

These outputs seed:
- **Output contract** for the solver
- **Budget caps** derived in `strategy_and_budget.py`
- **Decision model routing** in the ReAct loop

## How it connects to ReAct

1) Coordinator runs once at the beginning of the turn.
2) Its `output_contract` defines the slot(s) for the ReAct session.
3) Its budget intent is converted into caps (global + per‑slot stage budgets).
4) The decision agent uses those caps to plan search/explore/exploit/render.

## Coordinator and budgets

Coordinator is the primary driver of budget sizing. If it marks the turn as:
- **shallow** → fewer rounds, smaller caps
- **deep** → more rounds, larger caps

See: `react-budget.md` for budget mechanics and the BUDGET_STATE snapshot.

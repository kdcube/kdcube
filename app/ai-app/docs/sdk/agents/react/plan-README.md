---
id: ks:docs/sdk/agents/react/plan-README.md
title: "Plan"
summary: "How React v2 stores plans, shows them to the model, and lets the model create, supersede, close, and reread them."
tags: ["sdk", "agents", "react", "plan"]
keywords: ["plan_id", "announce", "snapshot_ref", "superseded", "close", "notes acknowledgements"]
see_also:
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
---
# React Plan Model

This document explains the current React v2 plan mechanism from scratch:

- what is stored internally
- what the model actually sees
- how the model should manage plans
- how plans behave across turns, pruning, and compaction

The important distinction is:

- **internal state** is append-only timeline data
- **model-facing state** is the rendered timeline plus ANNOUNCE

The model should reason from the rendered contract, not from assumptions about raw internal blocks.

## 1) Core idea

React plans are timeline-backed lineages.

- A plan lineage has a stable `plan_id`.
- The runtime stores plan state as append-only `react.plan` snapshots.
- The latest snapshot for a given `plan_id` is the current truth for that lineage.
- The model does **not** manage raw snapshots directly in the normal rendered timeline.

There is no separate database or plan store outside the timeline.

## 2) Internal representation

Internally, plans are persisted as timeline blocks of type `react.plan`.

```text
type: react.plan
path: ar:<turn_id>.react.plan.<plan_id>
mime: application/json
```

Each snapshot carries:

- `plan_id`
- `steps`
- `status`
- `created_ts`
- `last_ts`
- `origin_turn_id`
- `last_turn_id`
- `last_ack_turn_id`
- `last_ack_ts`
- `closed_ts`
- `closed_turn_id`
- `superseded_ts`
- `superseded_turn_id`
- `superseded_by_plan_id`

Progress acknowledgements are also stored internally as:

```text
type: react.plan.ack
path: ar:<turn_id>.react.plan.ack.<iteration>
mime: text/markdown
```

These ack blocks are runtime machinery. They are not the primary model-facing representation.

## 3) What the model actually sees

The model-facing contract is intentionally narrower than the internal storage.

The model normally sees:

1. `react.notes`
2. `react.plan` tool-call blocks
3. ANNOUNCE plan summaries
4. `react.plan.history` blocks after compaction
5. stable reread handles: `ar:plan.latest:<plan_id>`

The model does **not** normally rely on:

- raw `react.plan` JSON snapshot blocks
- raw `react.plan.ack` blocks

Those internal blocks still exist in the timeline, but the renderer does not expose them directly as the main plan UX.

## 4) Stable reread handle

Every plan lineage has a stable latest-snapshot alias:

```text
ar:plan.latest:<plan_id>
```

This is the important handle for the model.

Important:

- the alias does **not** have a separate turn-id segment
- `plan_id` is the whole suffix after `ar:plan.latest:`
- current runtime-generated `plan_id` values often happen to include the origin turn id inside the id text itself
- the model should treat `plan_id` as an opaque stable identifier, not parse it structurally
- examples below use simplified ids like `plan_alpha` for readability
- in real runtime output today, a generated id may look like `plan:turn_12:abcd1234`, and the matching stable alias would then be `ar:plan.latest:plan:turn_12:abcd1234`

Use it when:

- the plan is not currently expanded in visible context
- the model knows the `plan_id`
- the model wants the latest snapshot for that lineage

Example:

```text
react.read(["ar:plan.latest:<plan_id>"])
```

This returns the latest snapshot for that lineage, regardless of which turn last updated it.

Dedup behavior:

- if the current timeline already contains an equivalent visible block at the same alias path, `react.read` may report it under `exists_in_visible_context` instead of re-emitting the snapshot payload
- this is not based on “was read sometime earlier” in the abstract; it is based on the current timeline block set, compared by logical path plus normalized block content
- hidden/pruned copies do not block reread emission, because `react.read` re-emits the alias block as visible content

## 5) What the model sees when it creates a plan

When the model calls `react.plan(mode="new")`, the rendered timeline shows:

1. the normal tool call
2. a tool-result summary carrying the stable latest-snapshot handle

It does not show the raw internal snapshot block directly.

Example:

```text
[AI Agent say]: Create a plan for the investigation.

[TOOL CALL tc_plan_1].call react.plan
tc:turn_12.tc_plan_1.call
Params:
{
  "mode": "new",
  "steps": [
    "collect metrics",
    "compare trends",
    "draft answer"
  ]
}

[TOOL RESULT tc_plan_1].summary react.plan
mode: new
plan_id: plan_alpha
latest_snapshot_ref: ar:plan.latest:plan_alpha
```

So the model can learn the stable latest-snapshot handle directly from the tool result, and ANNOUNCE then shows the open plan:

```text
[ACTIVE PLANS]
  - plans: 1 visible
    • plan_id=plan_alpha (current)
      snapshot_ref=ar:plan.latest:plan_alpha
      created_turn=turn_12
      created_ts=2026-03-28T10:00:00Z
      last_update_turn=turn_12
      last_update_ts=2026-03-28T10:00:00Z
      □ [1] collect metrics
      □ [2] compare trends
      □ [3] draft answer
```

That is the intended model-facing surface.

## 6) How progress is tracked

Progress is not primarily driven by another tool call.

The model acknowledges step progress in `notes`, for example:

```text
✓ [1] collect metrics
… [2] compare trends — in progress
```

Runtime then:

1. parses those markers
2. appends internal `react.plan.ack`
3. appends a new internal `react.plan` snapshot for the same `plan_id`

Model-facing effect:

- ANNOUNCE updates the visible step markers
- the stable alias `ar:plan.latest:<plan_id>` now resolves to the newer snapshot

So the model should think of `notes` as the normal progress-reporting mechanism.

Important:

- status-marker notes are applied only in rounds that are not also changing plan lifecycle
- if the model is calling `react.plan(mode="activate"|"replace"|"close"|"new")`, it should use `notes` only for short explanation in that round
- if it wants to acknowledge progress on an older plan, it should first activate that plan, then acknowledge progress in a later round

There is a reserved structured tool `react.plan_ack`, but it is not published to the model yet.

## 7) Lifecycle operations

### 7.1 `mode="new"`

Use when starting a fresh plan.

- creates a new lineage
- assigns a new `plan_id`
- stores ordered `steps`
- appears in ANNOUNCE immediately

### 7.2 `mode="activate"`

Use when an older open plan should become current again.

Current semantics:

- target an existing open `plan_id`
- runtime appends a fresh snapshot for that same lineage
- that refreshed snapshot becomes the newest active plan
- future note-based acknowledgements apply to that now-current lineage

Rendered example:

```text
[TOOL CALL tc_plan_8].call react.plan
tc:turn_17.tc_plan_8.call
Params:
{
  "mode": "activate",
  "plan_id": "plan_alpha"
}

[TOOL RESULT tc_plan_8].summary react.plan
mode: activate
target_plan_id: plan_alpha
target_snapshot_ref: ar:plan.latest:plan_alpha
plan_id: plan_alpha
latest_snapshot_ref: ar:plan.latest:plan_alpha
```

Important:

- activation changes which open lineage is current
- it does not create a new `plan_id`
- do not try to acknowledge progress for the newly activated plan in the same decision; do that in a later round

### 7.3 `mode="replace"`

Use when replacing an existing plan with a new one.

Current semantics:

- target an existing `plan_id`
- runtime appends a terminal snapshot for that old lineage with `superseded_*`
- runtime appends a new lineage with a new `plan_id`

So `replace` means:

- “this old plan is no longer the open plan”
- “here is the replacement plan”

This matters because old plans should not remain forever as ambiguous unfinished work.

Rendered example:

```text
[TOOL CALL tc_plan_9].call react.plan
tc:turn_18.tc_plan_9.call
Params:
{
  "mode": "replace",
  "plan_id": "plan_alpha",
  "steps": [
    "draft answer",
    "verify citations"
  ]
}

[TOOL RESULT tc_plan_9].summary react.plan
mode: replace
target_plan_id: plan_alpha
target_snapshot_ref: ar:plan.latest:plan_alpha
plan_id: plan_beta
latest_snapshot_ref: ar:plan.latest:plan_beta
```

After execution:

- `plan_alpha` is marked superseded internally
- the replacement plan appears in ANNOUNCE with its own `plan_id`
- the stable reread handle for the new plan is `ar:plan.latest:<new_plan_id>`

### 7.4 `mode="close"`

Use when a plan should stop being open without being replaced.

Current semantics:

- target a `plan_id`
- runtime appends a terminal snapshot with `closed_*`
- the lineage stays in history
- the lineage disappears from ANNOUNCE open-plan view

Rendered example:

```text
[TOOL CALL tc_plan_10].call react.plan
tc:turn_19.tc_plan_10.call
Params:
{
  "mode": "close",
  "plan_id": "plan_beta"
}

[TOOL RESULT tc_plan_10].summary react.plan
mode: close
target_plan_id: plan_beta
target_snapshot_ref: ar:plan.latest:plan_beta
plan_id: plan_beta
latest_snapshot_ref: ar:plan.latest:plan_beta
```

## 8) What “open plan” means

A plan lineage is considered open only if its latest snapshot is:

- not `closed`
- not `superseded`
- not `complete`

So an old lineage can remain in history but still be excluded from ANNOUNCE because its latest snapshot is terminal.

## 9) Multiple open plans

Multiple open plans can exist in history at once.

That is why ANNOUNCE is important:

- it shows the last few open plans
- marks the newest one as current
- gives each visible open plan:
  - `plan_id`
  - `snapshot_ref`
  - created turn/time
  - last update turn/time
  - current step markers

Current ANNOUNCE policy:

- show only the last **N** open plans
- current implementation uses `N = 4`

So the model should not assume ANNOUNCE is a complete dump of all historical plan lineages.

## 10) How the model should manage plans

This is the intended behavior for the model.

### If the current plan still applies

Do not call `react.plan` again.

- keep working
- acknowledge progress in `notes`
- rely on ANNOUNCE for current visible status

### If the current plan is wrong and must be replaced

Call:

```text
react.plan(mode="replace", plan_id="<old_plan_id>", steps=[...])
```

This explicitly retires the old lineage and creates the replacement.

### If an older open plan should become current again

Call:

```text
react.plan(mode="activate", plan_id="<older_open_plan_id>")
```

Then, in a later round, acknowledge progress on that now-current plan in `notes`.

### If a plan is no longer relevant and should disappear from open plans

Call:

```text
react.plan(mode="close", plan_id="<plan_id>")
```

### If an older plan may matter again

1. get the `plan_id` from ANNOUNCE or `react.plan.history`
2. inspect its latest snapshot if needed:

```text
react.read(["ar:plan.latest:<plan_id>"])
```

If the alias-backed snapshot is already present as an equivalent visible block in the current timeline, `react.read` may only emit the status/result block and mark it as already present instead of repeating the full snapshot payload.

3. decide whether to:
   - activate it
   - close it
   - or replace it with a new plan

Important:

- if the model wants to continue an older open plan directly, it should activate it first
- if the model wants a new current plan instead, it should issue an explicit replacement plan

## 11) Hot timeline vs cold timeline

### Hot timeline

When history is still hot and not compacted away:

- the model sees plan tool calls where they happened
- the model sees notes where they happened
- ANNOUNCE shows current open plans

### Cold / compacted timeline

When older history has been compacted:

- older raw blocks are not the main signal anymore
- the model may see a visible `react.plan.history` block
- that block includes step skeletons, statuses, and stable `snapshot_ref`s

Example:

```text
[COMPACTED PLAN HISTORY]
Older plans were compacted out of the main visible stream.
Use react.read([...]) on the refs below if one becomes relevant again.

- plan #1 id=plan_alpha (unfinished) last=2026-03-28T10:08:00Z
  ✓ [1] collect metrics
  … [2] compare trends
  snapshot_ref: ar:plan.latest:plan_alpha
  latest_note_preview: Need to revisit the trend break later.
```

That is how the model is expected to rediscover older plans on a cold timeline.

## 12) Cross-turn behavior

Runtime rehydrates only the latest active lineage automatically.

That means:

- if a plan is still the newest open plan, it comes back naturally on the next turn
- older open or historical plans are not auto-activated
- the model must inspect them explicitly if they become relevant

This keeps cross-turn behavior simple while still allowing deliberate recovery of older work.

## 13) What is internal vs model-facing

This distinction is important.

Internal:

- rolling `react.plan` JSON snapshots
- `react.plan.ack`
- append-only terminal snapshots for `closed` / `superseded`

Model-facing:

- `react.notes`
- `react.plan` tool-call blocks
- ANNOUNCE plan list
- `react.plan.history`
- `ar:plan.latest:<plan_id>`

If you are reasoning about the agent behavior, reason from the model-facing layer first.

## 14) Efficiency and limitations

The current mechanism is efficient enough for normal use:

- plan blocks are sparse
- latest state is derived by scanning plan snapshots
- stable alias resolution avoids exposing every rolling `ar:<turn_id>.react.plan...` path to the model

Current limitations:

- ANNOUNCE shows only the last few open plans
- progress acknowledgements are still parsed from `notes`
- progress acknowledgements happen in a later round than lifecycle changes such as activate/replace/close

Those are known constraints of the current design, not accidental behavior.

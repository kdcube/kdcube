---
id: ks:docs/sdk/agents/react/react-announce-README.md
title: "React Announce"
summary: "Announce block semantics and lifecycle in React v2."
tags: ["sdk", "agents", "react", "announce", "timeline"]
keywords: ["announce banner", "system signals", "plan status", "feedback"]
see_also:
  - ks:docs/sdk/agents/react/feedback-README.md
  - ks:docs/sdk/agents/react/flow-README.md
  - ks:docs/sdk/agents/react/plan-README.md
---
# ReAct Announce Block (ANNOUNCE banner)

This doc describes how the **announce** block is used for ReAct v2.

## What it is
- An **ephemeral tail block** added by the runtime for each decision round.
- Contains ANNOUNCE️:
  - iteration
  - explicit iteration-budget explanation when live reactive credits increased the turn ceiling
  - open-plan summary with plan ids, snapshot refs, and status markers (if any exist)
  - compact live-turn external event summary (`followup`, `steer`) when present
  - compact workspace status
  - authoritative temporal context (UTC + user timezone)
  - optional system notices (e.g., cache TTL pruning)

## Where it appears
- It is appended at the end of the timeline when `timeline(include_announce=True)` is called.
- It is **never cached**.

## Lifecycle
- During the loop: updated each round with a fresh ANNOUNCE.
- On exit: the final announce block is **persisted** into the turn log blocks,
  then announce is cleared.

## Feedback in ANNOUNCE
- Feedback updates are fetched **only at turn start** (timeline load).
- If cache is **hot**, feedback remains in ANNOUNCE each round until a cold turn incorporates it.
- If cache is **cold**, feedback is injected into the target turn(s) and ANNOUNCE shows the same updates
  once with “(incorporated into turn timeline)”.
- After incorporation, those items are **not** repeated in later turns.

## System messages in announce
When cache TTL pruning occurs, the render path appends a one-time announce block
containing a system notice. It appears after the budget section and advises
the agent to use `react.read(path)` to restore truncated context.

## Workspace state in ANNOUNCE
ANNOUNCE may include a compact `[WORKSPACE]` section.

Its purpose is operational guidance, not full git/debug observability.

It can include:
- `implementation: custom|git`
- `current_turn_root`
- `materialized_turn_roots`
- `current_turn_scopes`
- in `git` mode only:
  - `repo_mode`
  - `repo_status`
- publish state:
  - `current_turn_publish`
  - `last_published_turn`
  - `publish_error` only when the current turn publish failed

Important rule:
- ANNOUNCE should stay compact
- raw git refs, commit shas, and other low-level publish metadata do not belong here unless there is a failure that React must react to immediately

## Live turn events in ANNOUNCE
When the current turn has already consumed busy-turn external events, ANNOUNCE includes a compact
`[LIVE TURN EVENTS]` section.

Its purpose is operational orientation, not full replay. It surfaces current-turn:
- `followup`
- `steer`

Important semantics:
- `followup` means the current turn already accepted additional same-turn user input
- `steer` means engineering already recorded a stop/reorient control event on the current turn timeline
- a live steer can interrupt an in-flight generation or cancellable tool phase before React re-enters a short bounded finalize phase

ANNOUNCE only shows current-turn live events. Historical preserved event blocks remain on the timeline itself.

## Why it exists
- Keeps high‑frequency state updates out of the cached timeline.
- Allows downstream agents (final answer generator) to see the **last ℹ️ ANNOUNCE ℹ️**
  via the persisted contribution block.

## Example (open plans)
```
╔══════════════════════════════════════╗
║  ANNOUNCE — Iteration 3/15           ║
╚══════════════════════════════════════╝

[BUDGET]
  iterations  ███░░░░░░░  12 remaining
  time_elapsed_in_turn   2m15s

[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]
  user_timezone: Europe/Berlin
  current_utc_timestamp: 2026-02-10T13:55:01Z
  current_utc_date: 2026-02-10
  All relative dates MUST be interpreted against this context.

[OPEN PLANS]
  - plans: 2 visible
    • plan_id=plan_alpha
      snapshot_ref=ar:plan.latest:plan_alpha
      created_turn=turn_1
      created_ts=2026-02-07T19:10:00Z
      last_update_turn=turn_1
      last_update_ts=2026-02-07T19:22:10Z
      ✓ [1] gather sources
      □ [2] draft report
    • plan_id=plan_beta (current)
      snapshot_ref=ar:plan.latest:plan_beta
      created_turn=turn_3
      created_ts=2026-02-10T13:50:00Z
      last_update_turn=turn_3
      last_update_ts=2026-02-10T13:54:00Z
      □ [1] draft answer
      □ [2] verify citations

[LIVE TURN EVENTS]
  - events: 2 visible
    • followup seq=7 explicit=True
      text=especially interesting in quantum
    • steer seq=8 explicit=True
      text=(empty stop control)

[WORKSPACE]
  implementation: git
  current_turn_root: turn_1775153963506_m1wj6f/
  materialized_turn_roots: turn_1775153855448_qryil1, turn_1775153963506_m1wj6f (current)
  current_turn_scopes:
    - docs/ (2 files)
    - src/ (5 files)
  repo_mode: sparse git repo
  repo_status: dirty
  current_turn_publish: pending
```

When a live reactive event increased the turn budget, ANNOUNCE must explain that explicitly instead of silently changing the denominator:

```text
╔══════════════════════════════════════════════════════════════╗
║  ANNOUNCE — Iteration 4/16 (15 + 1 reactive bonus)          ║
╚══════════════════════════════════════════════════════════════╝

[BUDGET]
  iterations  ██░░░░░░░░  12 remaining  (base 15 + 1 bonus from live reactive events)
  time_elapsed_in_turn   2m41s
```

This avoids the misleading shape where the user only sees the ceiling jump from `15` to `16` with no cause given.

## Notes
- ANNOUNCE is the open/current plan presentation layer; React does not rely on a separate persistent `react.plan.active` tail artifact.
- Closed, complete, and superseded plans are excluded from ANNOUNCE.
- An open plan is not automatically current. Only explicitly current plans carry the `(current)` tag.
- If a plan is shown without `(current)`, React must activate it before acknowledging any of its steps.
- Announce is not cached and is re‑rendered each decision round.
- The `[WORKSPACE]` section is intentionally brief; detailed publish metadata belongs in internal event blocks, not the visible announce surface.
- The `[LIVE TURN EVENTS]` section is also intentionally brief; it is a same-turn control summary, not a replacement for the underlying timeline blocks.
- The example uses simplified plan ids (`plan_alpha`, `plan_beta`) for readability. Real runtime-generated `plan_id` values may look like `plan:turn_3:efgh5678`, and the matching stable alias would then be `ar:plan.latest:plan:turn_3:efgh5678`.

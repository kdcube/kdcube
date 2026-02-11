# ReAct Announce Block (ANNOUNCE banner)

This doc describes how the **announce** block is used for ReAct v2.

## What it is
- An **ephemeral tail block** added by the runtime for each decision round.
- Contains ANNOUNCE️:
  - iteration
  - current plan with status markers
  - authoritative temporal context (UTC + user timezone)

## Where it appears
- It is appended at the end of the timeline when `timeline(include_announce=True)` is called.
- It is **never cached**.

## Lifecycle
- During the loop: updated each round with a fresh ANNOUNCE.
- On exit: the final announce block is **persisted** into the turn log blocks,
  then announce is cleared.

## Why it exists
- Keeps high‑frequency state updates out of the cached timeline.
- Allows downstream agents (final answer generator) to see the **last ℹ️ ANNOUNCE ℹ️**
  via the persisted contribution block.

## Example (single plan)
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

[ACTIVE PLAN]
  - plans:
    • plan #1 (current) last=2026-02-07T19:22:10Z
      ✓ [1] gather sources
      □ [2] draft report
  - plan_status: done=1 failed=0 pending=1
  - plan_complete: false
```

## Notes
- Only the latest plan is shown in ACTIVE PLAN.
- Announce is not cached and is re‑rendered each decision round.

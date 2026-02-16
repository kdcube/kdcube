# Context Caching (Dual Checkpoints, Round-Based)

The context browser uses **two cache checkpoints** to keep stable prefixes cached while allowing
the tail to grow. This reduces cache invalidations when the timeline grows or when older blocks
are compacted.

## Strategy
- **Tail checkpoint**: placed at the last stable **round**.
- **Additional checkpoint (pre‑tail)**: placed `offset_rounds` **before** the tail checkpoint,
  only when there are at least `min_rounds` rounds.

This yields two cache anchors in the stable prefix. If the tail cache breaks, the additional
checkpoint still provides a usable cached prefix.

With `cache_point_offset_rounds=4`, the **pre‑tail checkpoint is placed on the last block of
round N‑4** (counting from the tail), when enough rounds exist.

## Rounds
A **round** is keyed by `tool_call_id`, plus a **final completion round** that contains:
`assistant.completion`, `stage.suggested_followups`, `react.exit`, `react.state`.

Rounds are counted across the **visible timeline slice**, which may include blocks
from previous turns (post‑compaction). Cache points are **not** restricted to the
current turn.

## Parameters
Configured on `RuntimeCtx.cache`:
- `cache_point_min_rounds`: minimum **total** rounds required before placing the additional checkpoint (default: `2`)
- `cache_point_offset_rounds`: distance (in rounds) from tail to the additional checkpoint once placed (default: `4`)

## Application
- Cache points are applied to the **stable timeline** (post‑compaction, pre‑tail).
- Sources/announce are appended after rendering and remain uncached.
- If `cache_last=True`, the last rendered block is additionally cached (cache points still apply).

## Implementation
See `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/caching.py`.

## Eviction Rule
Eviction is only allowed **after** the additional checkpoint. Use
`is_before_pre_tail_cache(...)` or `cache_points_for_blocks(...)` from
`react/v2/caching.py` to validate.

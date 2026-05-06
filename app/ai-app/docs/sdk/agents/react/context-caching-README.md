---
id: ks:docs/sdk/agents/react/context-caching-README.md
title: "Context Caching"
summary: "Dual checkpoint caching strategy for stable prefixes and growing tails."
tags: ["sdk", "agents", "react", "context", "caching"]
keywords: ["cache checkpoints", "prefix", "tail", "Anthropic cache", "cache TTL"]
see_also:
  - ks:docs/sdk/agents/react/compaction-README.md
  - ks:docs/sdk/agents/react/context-browser-README.md
  - ks:docs/sdk/agents/react/context-layout.md
---
# Context Caching (Dual Checkpoints, Round-Based)

The context browser uses **two cache checkpoints** to keep stable prefixes cached while allowing
the tail to grow. This reduces cache invalidations when the timeline grows or when older blocks
are compacted.

## Strategy
- **Previous‑turn checkpoint**: placed on the **last block before the current turn header**, if any.
- **Tail checkpoint**: placed at the last stable **round**.
- **Additional checkpoint (pre‑tail)**: placed `offset_rounds` **before** the tail checkpoint,
  only when there are at least `min_rounds` rounds.

This yields **three** cache anchors in the stable prefix when a previous turn exists.
If the tail cache breaks, the additional checkpoint still provides a usable cached prefix.

system message
timeline
## Schematic (cache points)
```
... previous turns ...
[TURN turn_A header]
  ... blocks ...
  (last block of turn_A)                => [CP: prev-turn]
[TURN turn_B header]  <-- current turn
  ... round N-5 ...
  ... round N-4 (last block)           => [CP: pre-tail]  (offset_rounds=4)
  ... round N-3 ...
  ... round N-2 ...
  ... round N-1 ...
  ... round N (last block)             => [CP: tail]
```

With `cache_point_offset_rounds=4`, the **pre‑tail checkpoint is placed on the last block of
round N‑4** (counting from the tail), when enough rounds exist.

### Hide interaction
If `react.hide` is used and the **pre‑tail checkpoint is above the previous‑turn checkpoint**,
the previous‑turn checkpoint is **reset to the pre‑tail checkpoint** for the remainder of the turn.
This prevents hide operations from being constrained by a cache point that is older than the
current pre‑tail boundary.

## Rounds
A **round** is keyed by `tool_call_id`, plus a **final completion round** that contains:
`assistant.completion`, `stage.suggested_followups`, `react.turn.finalize`,
`react.exit`, `react.state`.

If one turn produces multiple visible `assistant.completion` blocks, they still belong to that
turn's final completion family. Caching remains round-based; the latest unsuffixed completion path
is preserved separately as the stable alias.

Rounds are counted across the **visible timeline slice**, which may include blocks
from previous turns (post‑compaction). Cache points are **not** restricted to the
current turn.

## Parameters
Configured on `RuntimeCtx.cache`:
- `cache_point_min_rounds`: minimum **total** rounds required before placing the additional checkpoint (default: `2`)
- `cache_point_offset_rounds`: distance (in rounds) from tail to the additional checkpoint once placed (default: `4`)

Context-size and TTL-pruning defaults are configured separately:

- `ai.react.context_max_tokens` / `AI_REACT_CONTEXT_MAX_TOKENS`: default hard
  render budget used before sending to the model when a bundle does not set
  `max_tokens` (default: `80000`)
- `ai.react.cache_keep_recent_turns` / `AI_REACT_CACHE_KEEP_RECENT_TURNS`:
  number of recent turns kept visible after TTL pruning (default: `6`)
- `ai.react.cache_keep_recent_intact_turns` /
  `AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS`: newest turns kept untrimmed
  during TTL pruning (default: `1`)
- `cache_truncation_replacement_max_tokens` on `RuntimeCtx.session`:
  maximum size for automatic TTL-generated replacement text (default: `240`
  tokens). This is intentionally separate from explicit `react.hide`, which
  preserves the requested replacement exactly.

## Application
- Cache points are applied to the **stable timeline** (post‑compaction, pre‑tail).
- Sources/announce are appended after rendering and remain uncached.
- If `cache_last=True`, the last rendered block is additionally cached (cache points still apply).
- Live external events (`user.followup`, `user.steer`) are folded into the same timeline
  stream while a turn is active. They typically invalidate only the tail portion; the
  stable prefix remains reusable because the previous-turn and pre-tail checkpoints stay
  anchored earlier in the visible stream.

## Cold Cache Mitigation
When a provider-side prompt cache expires, rebuilding a very large prefix is
expensive. React mitigates that by combining:

- TTL pruning: historical turns with `conv.working.summary` blocks collapse to
  those compact semantic cards; multiple summaries from one turn are preserved.
  Historical turns without summaries fall back to retrieval-index rows with
  logical paths and tiny semantic hints
- TTL replacement bounding: automatic replacement text is capped before
  `Timeline.hide_paths(...)` is called, preventing a cold-cache pruning pass from
  expanding the prompt with verbose tool payloads. The guard applies both to
  absolute oversize replacements and to material growth over the original block.
- turn-status collapse: React finalization internals render as one compact
  `[TURN STATUS]` card
- round-scaffolding suppression: hidden `react.round.start`,
  `react.thinking`, `react.notes`, `react.notice`, and
  `stage.suggested_followups` blocks are not rendered as separate pruned refs
- hard compaction: the rendered model view is capped by `context_max_tokens`
- source/artifact retrieval: full content stays available via `react.read(path)`

The intended model-view shape after a cold cache is therefore a compact summary
plus recent working tail, not a replay of the entire conversation history.

The cold-cache mitigation depends on the model actually emitting useful
`summary` channel blocks at final/exit answer attempts. Without working
summaries, historical turns render as retrieval-index stubs; that is safe for
tokens, but weaker semantically.

## Implementation
See `kdcube_ai_app/apps/chat/sdk/solutions/react/caching.py`.

## Eviction Rule
Eviction is only allowed **after** the additional checkpoint. Use
`is_before_pre_tail_cache(...)` or `cache_points_for_blocks(...)` from
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/caching.py` to validate.

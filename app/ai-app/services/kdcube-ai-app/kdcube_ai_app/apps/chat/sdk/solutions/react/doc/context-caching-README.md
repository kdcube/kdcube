# Context Caching (Dual Checkpoints)

The context browser uses **two cache checkpoints** to keep stable prefixes cached while allowing
the tail to grow. This reduces cache invalidations when the timeline grows or when older blocks
are compacted.

## Strategy
- **Tail checkpoint**: always placed at the last stable block.
- **Additional checkpoint**: placed `offset` blocks before tail (only when the log is large enough).

This yields two cache anchors in the stable prefix. If the tail cache breaks, the additional
checkpoint still provides a usable cached prefix.

## Parameters
Configured on `ContextBrowser`:
- `cache_additional_min_blocks`: minimum stable blocks before placing additional checkpoint (default: 4)
- `cache_additional_offset`: distance from tail to additional checkpoint (default: 2)

## Application
- Cache points are applied only to **stable blocks** (history/current/contributions).
- Tail blocks (sources pool + announce) are always uncached.
- If `cache_last=True` is used explicitly, only the last block is cached and dual checkpoints are skipped.

## Implementation
See `kdcube_ai_app/apps/chat/sdk/runtime/solution/context/caching.py`.

## Eviction Rule
Eviction is only allowed **after** the additional checkpoint. Use
`can_evict(block_idx, total_blocks, min_blocks, offset)` to validate.

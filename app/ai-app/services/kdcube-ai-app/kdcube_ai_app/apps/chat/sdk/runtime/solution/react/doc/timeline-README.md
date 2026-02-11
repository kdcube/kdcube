# Timeline (react v2)

The **timeline** is the single source of truth for turn context. It stores:
- ordered **blocks** (user prompts, attachments, agent contributions, tool calls/results)
- the **sources_pool**
- conversation metadata (title, started_at)
- transient **announce** blocks (in‑memory only, not persisted)
 - **plan history blocks** (`react.plan` / `react.plan.ack`) persisted in the block stream

It is persisted as a single artifact: `artifact:conv.timeline.v1`.

## Stored payload
```
{
  "version": 1,
  "ts": "2026-02-09T...",
  "blocks": [ ... ],
  "sources_pool": [ ... ],
  "turn_ids": ["turn_...","turn_..."],
  "conversation_title": "...",
  "conversation_started_at": "...",
  "cache_last_touch_at": 1739078400,
  "cache_last_ttl_seconds": 1800
}
```

## Lifecycle
1) **Load** at turn start (from the latest timeline artifact).
2) **Contribute** blocks as the turn progresses (gate/coordinator/react/final).
3) **Render** a message view with cache points + optional sources/announce.
4) **Persist** at end of turn.

### Persistence behavior
If compaction occurred, the persisted timeline contains **only the post‑summary window** (summary + following blocks).

### Compaction
When the visible window exceeds the model budget, the timeline compacts earlier blocks into a single summary:
- A `conv.range.summary` block is inserted at the cut point.
- The compacted blocks before the summary are removed from the persisted payload.
- Future renders start from the most recent summary block onward.

## Block ordering (schematic)
```
[TURN <id> header]
  user.prompt
  user.attachment.meta
  user.attachment (optional binary)
  stage.gate [optional] / stage.react / ...
  react.tool.call / react.tool.result / ...
  assistant.completion

[TURN <id> header]
  ...
```

## Cache points
Timeline uses two cache checkpoints (see `context-caching-README.md`):
- **checkpoint 1** (intermediate)
- **checkpoint 2** (tail)

Cache points are inserted when rendering the message blocks, not stored in timeline payload.

## Cache TTL pruning
When `RuntimeCtx.session.cache_ttl_seconds` is set, the timeline applies TTL-based pruning on render:
- `cache_last_touch_at` and `cache_last_ttl_seconds` are stored in the timeline payload.
- On the **first render** after loading a timeline, the stored TTL is used if present.
- Subsequent renders use the runtime session settings.
- A prune buffer (`cache_ttl_prune_buffer_seconds`) can force pruning *before* the TTL expires.
- When pruning occurs, a one-time announce block is injected (after budget), and a
  persistent `system.message` block is appended to the timeline to explain how to
  restore paths via `react.read(path)`. Hidden replacement blocks do **not**
  include per-block hints.

### Pruned view (schematic)
```
[TURN turn_...]
  [TRUNCATED] user prompt snippet...
  [TRUNCATED FILE] path=fi:turn_... mime=image/png size=...
  [TRUNCATED] tool result summary...

[TURN turn_...]
  user.prompt
  react.tool.call
  react.tool.result
  assistant.completion

[SYSTEM MESSAGE] Context was pruned because the session TTL (300s) was exceeded.
Use react.read(path) to restore a logical path (fi:/ar:/so:/sk:).
```

## Concurrency / locking
- `contribute_async`, `render`, and `persist` are guarded by an internal async lock.
- This prevents interleaving of compaction and persistence when agents contribute in parallel.

## Contribute vs announce
- **Contribute** = persistent block additions (saved in timeline on persist).  
  Used for user prompts, attachments, stage outputs, tool call/results, assistant completion.
- **Announce** = ephemeral, turn‑local signals (not persisted).  
  Used for ACTIVE STATE (plans, budgets) and transient notices.  
  Announce is appended after sources when `include_announce=True` in `timeline.render(...)`.

## Rendering
`timeline.render(...)` returns model message blocks:
- converts text blocks to `{"type":"text","text":...}`
- converts binary blocks to `{"type":"image"|"document", ...}`
- inserts cache checkpoints (see `context-caching-README.md`)
- optionally appends sources/announce blocks at the tail

Debugging:
- `timeline.render(..., debug_print=True)` prints the rendered message stream,
  with cache points marked (e.g., `=>[1]`).

## Storage location
Timeline is stored as a single artifact:
- **kind**: `artifact:conv.timeline.v1`
- **role**: `artifact`
- **payload**: timeline JSON (blocks + sources_pool + metadata)
- **content_str**: compact summary (counts/title/turn_ids)

In S3 / DB this appears alongside other conversation artifacts.  
It is fetched via `ctx_client.recent(kinds=("artifact:conv.timeline.v1",), ...)`.

## Block metadata
Each block may include:
- `turn_id`, `ts`, `path`, `mime`
- `meta`: hosted_uri / rn / key / physical_path / artifact_path / sources_used

See `event-blocks-README.md` for concrete block examples.

## Plans in timeline
- Plans are stored only as blocks:
  - `react.plan` (JSON snapshot)
  - `react.plan.ack` (human‑readable ack lines)
- Active plan is derived at render time by scanning the latest `react.plan` block.
- `react.plan.active` is **announced** (ephemeral), not persisted.

Note that what makes the timeline a cache hit is the combination of system message and the messages build based on timeline contents.
Agents with a different system message consuming the same timeline won't get cache hit and will have 2 separate caches.

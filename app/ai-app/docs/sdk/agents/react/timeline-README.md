---
id: ks:docs/sdk/agents/react/timeline-README.md
title: "Timeline"
summary: "Timeline as single source of truth for context and artifacts."
tags: ["sdk", "agents", "react", "timeline", "blocks"]
keywords: ["timeline blocks", "ordered events", "artifacts", "turn context"]
see_also:
  - ks:docs/sdk/agents/react/structure-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
---
# Timeline (react v2)

The **timeline** is the single source of truth for turn context. It stores:
- ordered **blocks** (user prompts, attachments, agent contributions, tool calls/results)
- live-folded external user event blocks (`user.followup`, `user.steer`)
- Internal Memory Beacons (`react.note`) and preserved beacons (`react.note.preserved`)
- conversation metadata (title, started_at, last_activity_at)
- the persisted external-event replay cursor (`last_external_event_id`, `last_external_event_seq`)
- transient **announce** blocks (in‑memory only, not persisted)
 - **plan history blocks** (`react.plan` / `react.plan.ack`) persisted in the block stream

It is persisted as a single artifact: `artifact:conv.timeline.v1`.
The sources pool is persisted separately as `artifact:conv:sources_pool`.

## Stored payload
```
{
  "version": 1,
  "ts": "2026-02-09T...",
  "blocks": [ ... ],
  "turn_ids": ["turn_...","turn_..."],
  "conversation_title": "...",
  "conversation_started_at": "...",
  "last_activity_at": "...",
  "last_external_event_id": "17-0",
  "last_external_event_seq": 42,
  "cache_last_touch_at": 1739078400,
  "cache_last_ttl_seconds": 1800
}
```

## Lifecycle
1) **Load** at turn start (from the latest timeline + sources_pool artifacts).
   - Loader: `ContextBrowser.load_timeline()` combines the latest
     `artifact:conv.timeline.v1` + `artifact:conv:sources_pool`.
   - During load, unread external `followup` / `steer` events after the stored cursor are folded into the timeline.
2) **Contribute** blocks as the turn progresses (gate/react).
3) **Listen** for shared external events while the turn owns the timeline.
   - `ContextBrowser` holds a fenced owner lease.
   - The live listener waits on the shared durable event source and folds accepted
     `followup` / `steer` events into the same timeline.
   - Runtime hooks (`on_timeline_event(...)`) are invoked after folding.
4) **Render** a message view with cache points + optional sources/announce.
5) **Persist** at end of turn.
   - Persister: `Timeline.persist()` writes both artifacts:
     `artifact:conv.timeline.v1` and `artifact:conv:sources_pool`.

### External event delivery model
The delivery model is now shared and durable:
- ingress appends busy-turn `followup` / `steer` requests into one canonical conversation event source
- the live React turn consumes from that source when it owns the timeline
- if there is no live owner, processor promotion continues from that same source

This avoids having one path for “live events” and a different source of truth for fallback continuation.

### Persistence behavior
If compaction occurred, the persisted timeline contains **only the post‑summary window** (summary + following blocks).
Internal Memory Beacons from the compacted region are copied into that post-summary window as `react.note.preserved`
so they remain visible as durable beacons.

### Compaction
When the visible window exceeds the model budget, the timeline compacts earlier blocks into a single summary:
- A `conv.range.summary` block is inserted at the cut point.
- Internal Memory Beacons from the compacted region are cloned after the summary as `react.note.preserved`.
- The compacted blocks before the summary are removed from the persisted payload.
- Future renders start from the most recent summary block onward.

## Block ordering (schematic)
```
[TURN <id> header]
  user.prompt
  user.followup / user.steer [optional]
  user.attachment.meta
  user.attachment (optional binary)
  stage.gate [optional] / stage.react / ...
  react.tool.call / react.tool.result / ...
  react.note [optional]
  assistant.completion

[TURN <id> header]
  ...
```

## Cache points (round‑based)
Timeline uses cache checkpoints (see `context-caching-README.md`):
- **checkpoint 1**: last block of the previous turn (if any)
- **checkpoint 2**: intermediate (pre‑tail)
- **checkpoint 3**: tail

Cache points are computed by **rounds** (tool_call_id plus the final completion round):
- `RuntimeCtx.cache.cache_point_min_rounds`
- `RuntimeCtx.cache.cache_point_offset_rounds`

Cache points are inserted when rendering the message blocks, not stored in the timeline payload.
`react.hide` cannot hide blocks **before** the pre‑tail checkpoint.

Rounds are counted across the **visible timeline slice** (post‑compaction), which may
include blocks from previous turns.

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
- Internal Memory Beacons (`react.note`, `react.note.preserved`) are exempt from TTL hiding and remain visible.
- External `user.followup` / `user.steer` blocks are normal timeline blocks. They are not TTL-special-cased;
  they remain or disappear according to the same visible-window rules as other ordinary blocks.

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
- Live external folding is still serialized through timeline contribution, but ownership of the
  active listener is protected separately by a fenced lease (`lease_token`, `lease_epoch`) in the
  shared external event source.

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

### Rendered tool results (model view)
Tool calls/results are rendered into a compact, consistent view:
- **Tool call**: `[TOOL CALL <id>].call <tool_id>` + bare `tc:<turn_id>.<tool_call_id>.call` line + params
- **Artifact‑producing tools**: `[TOOL RESULT <id>].summary <tool_id>`  
  Status + artifact list (logical_path + key metadata, including `sources_used` when present)
- **Non‑artifact tools**: `[TOOL RESULT <id>].result <tool_id>`  
  `logical_path: ...` + result payload
- **Each artifact**: `[TOOL RESULT <id>].artifact <tool_id>`  
  `logical_path: ...` (+ `physical_path` only if hosted) followed immediately by content

Assistant completion blocks (`assistant.completion`) are rendered with an extra line when
`meta.sources_used` is present:
```
[sources_used: [1,2,3]]
```

Debugging:
- `timeline.render(..., debug_print=True)` prints the rendered message stream,
  with cache points marked (e.g., `=>[1]`).

## Storage location
Timeline is stored as:
- **artifact**: `artifact:conv.timeline.v1`
- **payload**: timeline JSON (blocks + metadata; no sources_pool)
- **content_str**: compact summary (counts/title/turn_ids/last_activity_at)

Sources pool is stored as:
- **artifact**: `artifact:conv:sources_pool`
- **payload**: `{ "sources_pool": [...] }`

In S3 / DB these appear alongside other conversation artifacts.  
They are fetched via `ctx_client.recent(kinds=("artifact:conv.timeline.v1",), ...)`
and `ctx_client.recent(kinds=("artifact:conv:sources_pool",), ...)`.

## Block metadata
Each block may include:
- `turn_id`, `ts`, `path`, `mime`
- `meta`: hosted_uri / rn / key / physical_path / artifact_path / sources_used  
  (hosted fields are **not** rendered to the model; logical paths are surfaced instead)

See `event-blocks-README.md` for concrete block examples.

## Internal Memory Beacons

Internal Memory Beacons are short user-invisible notes written with `react.write(channel="internal")`.
They are intended for stable facts worth carrying forward, usually written near the end of a turn:
- `[P]` preferences
- `[D]` decisions
- `[S]` technical/spec details
- `[A]` achievements or milestones
- `[K]` key artifacts with logical path and why they matter

They enter the timeline as `react.note` and, if compaction later hides their original region, are preserved as
`react.note.preserved` directly after the summary block.

## Plans in timeline
- Plans are stored only as blocks:
  - `react.plan` (JSON snapshot)
  - `react.plan.ack` (human‑readable ack lines)
- Active plan is derived at render time by scanning the latest `react.plan` block.
- `react.plan.active` is **announced** (ephemeral), not persisted.

Note that what makes the timeline a cache hit is the combination of system message and the messages build based on timeline contents.
Agents with a different system message consuming the same timeline won't get cache hit and will have 2 separate caches.

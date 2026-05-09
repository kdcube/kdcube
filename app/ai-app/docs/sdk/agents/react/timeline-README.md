---
id: ks:docs/sdk/agents/react/timeline-README.md
title: "Timeline"
summary: "Timeline as the single source of truth for React context, artifacts, round state, and live external events."
tags: ["sdk", "agents", "react", "timeline", "blocks"]
keywords: ["timeline blocks", "ordered events", "artifacts", "turn context"]
see_also:
  - ks:docs/sdk/agents/react/structure-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/session-view-README.md
---
# Timeline (React)

The **timeline** is the single source of truth for turn context. It stores:
- ordered **blocks** (user prompts, attachments, agent contributions, tool calls/results)
- live-folded external user event blocks (`user.followup`, `user.steer`)
- live-folded attachment blocks for busy-turn followups that carry attachments
- Internal Memory Beacons (`react.note`) and preserved beacons (`react.note.preserved`)
- working summary blocks (`conv.working.summary`) emitted from React's
  `summary` channel
- conversation metadata (title, started_at, last_activity_at)
- the persisted external-event replay cursor (`last_external_event_id`, `last_external_event_seq`)
- transient **announce** blocks (in‑memory only, not persisted)
- **plan history blocks** (`react.plan` / `react.plan.ack`) persisted in the block stream

One turn may contain:
- multiple prompt-like user blocks (`user.prompt`, `user.followup`, `user.steer`)
- multiple `assistant.completion` blocks if the user saw more than one completion before the turn closed
- multiple `conv.working.summary` blocks if multiple final/exit answer attempts
  were produced in the same long turn

Working summaries are first-class timeline blocks. They are not synthetic
post-processing at persistence time: when React emits `channel:summary` together
with a final/exit answer attempt, the summary is contributed into the same turn.
If a later followup causes another answer attempt, that attempt can contribute a
new summary with its own path. The unsuffixed `ws:<turn_id>.conv.working.summary`
handle resolves to the latest summary for that turn.

It is persisted as a single artifact: `artifact:conv.timeline.v1`.
The sources pool is persisted separately as `artifact:conv:sources_pool`.

Both `v2` and `v3` use this same timeline model. The difference is the decision contract, not the persistence model.

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
- a consumed `followup` remains on the same turn and becomes visible to the next decision round
- a consumed `steer` is an engineering-layer interrupt first, not just extra timeline text
- engineering attempts to cancel the active decision generation or cancellable tool phase immediately
- React then sees the steer block on the same turn timeline and gets a short bounded finalize phase before turn completion
- if a live `followup` carries attachments, those attachments are folded into the current turn as normal attachment blocks instead of text-only control input

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
  react.round.start [optional, when a round has begun]
  user.followup / user.steer [optional]
  user.attachment.meta
  user.attachment (optional binary)
  stage.gate [optional] / stage.react / ...
  react.notice [optional]
  react.tool.call / react.tool.result / ...
  react.note [optional]
  conv.working.summary [0..n, emitted with final/exit answer attempts]
  assistant.completion [0..n]
  react.turn.finalize [optional compact final stats block]
  react.state / react.exit [internal]

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
- When pruning occurs, a persistent `system.message` block is appended to the
  timeline to explain that logical paths still exist. Prune notices whose own
  blocks are hidden are omitted from the rendered model view.
- If a pruned historical turn has `conv.working.summary` blocks, that turn
  renders as working-summary card(s). Multiple summaries for the same turn are
  preserved because same-turn followups may represent separate work portions.
- Hidden historical blocks without a working summary render as compact
  retrieval-index rows. Each row includes the logical path plus a tiny semantic
  hint so the model can decide whether the full block is worth reading.
- The pruned retrieval skeleton consists of tool calls/results and
  assistant/user messages as recoverable facts. Round scaffolding and transient
  chatter are suppressed: `react.round.start`, `react.thinking`,
  `react.notes`, `react.notice`, and `stage.suggested_followups`.
- Pruned historical turns render one `[PRUNED TURN DATA]` marker before their
  fallback retrieval rows. The rows use neutral labels (`user:`, `assistant:`,
  `tool_call:`, `tool_result:`, `file:`, `skill:`, `source:`). User and
  assistant rows use the same retrieval-row style as the other hidden blocks.
- Turn-finalization internals render as a single compact
  `[TURN STATUS]` card. This card carries useful operational facts such as
  rounds used, exit reason, error if any, elapsed time, plan status, and selected
  workspace/publish fields.
- Each retrieval row exposes a logical path as the recovery handle. Use
  `react.read([path])` when the row's hint indicates that hidden content is
  needed.
- Timestamps appear on pruned turn headers.
  Hidden tool calls/results, files, skills, sources, and user prompts render
  without timestamps in the pruned skeleton.
- Internal Memory Beacons (`react.note`, `react.note.preserved`) are exempt from TTL hiding and remain visible.
- External `user.followup` / `user.steer` are treated as primary user control input and remain visible through TTL pruning.
- If compaction later hides their original region, preserved copies remain visible as `user.followup.preserved` / `user.steer.preserved`.
- Automatic TTL pruning bounds the stored `replacement_text` before hiding a
  block. The bound is controlled by `cache_truncation_replacement_max_tokens`.
  This guard is only for TTL-generated replacements; explicit `react.hide`
  stores the replacement text exactly as requested. TTL also compacts material
  replacement growth even when the replacement is below the absolute token cap.
- Tiny replacement expansions on already-small blocks are debug-level noise, not
  warning-worthy. Large expansions still indicate a bad TTL replacement shape.

### Pruned view (schematic)
```
TURN turn_13083620 (started at 2026-05-03T01:17:11Z)
[WORKING SUMMARY]
[path: ws:turn_13083620.conv.working.summary]
Goal: Find the 2 most exciting medical news stories from the last two weeks.
Outcome: Answered with two MedicalNewsToday-backed stories and direct links.
Key facts:
- Initial web searches returned empty, direct source fetch succeeded.
- Selected stories: oral GLP-1 pill approval and stroke-risk reduction drug.
Refs:
- user: ar:turn_13083620.user.prompt
- source fetch: tc:turn_13083620.tc_530c5199feb6.result
- assistant final: ar:turn_13083620.assistant.completion

TURN turn_without_summary (started at 2026-05-03T01:19:24Z)
[PRUNED TURN DATA]
turn_id: turn_without_summary
retrieval_rows: logical paths and hints for hidden historical blocks
user: path=ar:turn_without_summary.user.prompt hint="great, but by some reason i do not see the actual links..."
assistant: path=ar:turn_without_summary.assistant.completion hint="Provided direct URLs for raw citation tokens."
[TURN STATUS]
turn_id: turn_without_summary
rounds: 3/12
exit_reason: complete
time_elapsed_in_turn: 23s
plans:
  - plans: none
workspace:
  - implementation: git
  - current_turn_root: turn_without_summary/
  - current_turn_publish: succeeded


TURN turn_13083707 (started at 2026-05-05T21:28:15Z)
... recent intact turn renders normally ...
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
  It may also include a compact `[LIVE TURN EVENTS]` view for current-turn consumed `followup` / `steer`.
  Announce is appended after sources when `include_announce=True` in `timeline.render(...)`.

## Sequential block stream in v3

Experimental `v3` may accept multiple requested actions in one response, but the timeline remains one ordered block stream:

- accepted actions are executed sequentially
- each executed action still produces its own `react.tool.call` / `react.tool.result` group
- rendering, cache points, compaction, and turn reconstruction continue to operate on one ordered sequence

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
  `logical_path: ...` + result payload. If the payload exceeds
  `tool_result_preview_max_text_symbols`, the prompt-visible view contains
  `[TOOL RESULT PREVIEW TRUNCATED]`, size metadata, a depth-limited shape,
  a bounded raw preview, and recovery instructions. The stored `tc:` result is
  not truncated.
- **Source-row payloads**: `so:sources_pool[...]` JSON rows are not passed
  through the generic tool-result preview cap. They stay structured and visible
  so the model can cite/use searched content without guessing from a blind cut.
- **Each artifact**: `[TOOL RESULT <id>].artifact <tool_id>`  
  `logical_path: ...` (+ `physical_path` only if hosted) followed immediately by content

### Visible read payload caps

`react.read` can add new visible blocks to this same timeline. Its admission
rules are separate from TTL pruning:

- text content is bounded by `read_visible_max_text_symbols`,
  `read_visible_max_tokens`, `read_visible_context_fraction`, and optional
  per-call `max_text_symbols`; the cap is per requested path
- once `react.read` has admitted or explicitly preview-capped content, prompt
  rendering does not apply the generic tool-result preview cap again
- `so:sources_pool[...]` reads are structured JSON source-row reads. They are
  full by default and include `items_stats`; explicit `max_text_symbols` caps
  only source text fields while preserving valid JSON rows.
- raw payloads are bounded by `read_visible_max_bytes`
- `stats_only: true` records path metadata in the `react.read` status block
  without adding content blocks to the visible timeline
- PDF/image content is all-or-marker: if under the byte cap it renders as a
  normal multimodal block; if over the cap, React renders a recovery marker
  with bytes/path metadata
- admitted PDF/image blocks count toward prompt-size estimates as model tokens
  (image dimensions/PDF pages), not as raw base64 bytes
- unsupported binaries such as xlsx/pptx/docx/zip remain metadata-only

Assistant completion blocks (`assistant.completion`) are rendered with an extra line when
`meta.sources_used` is present:
```
[sources_used: [1,2,3]]
```

Path convention:
- latest completion in the turn keeps `ar:<turn_id>.assistant.completion`
- earlier visible completions in the same turn use `ar:<turn_id>.assistant.completion.<n>`
- fetch reconstruction can therefore emit multiple `chat:assistant` entries for one turn

Debugging:
- `timeline.render(..., debug_print=True)` prints the rendered message stream,
  with cache points marked (e.g., `=>[1]`).
- When inspecting `react.read` results, `exists_in_visible_context` means the
  requested logical path is already visible in the current model view. A good
  rendered result should identify that visible location, not force another read.

## Storage location
Timeline is stored as:
- **artifact**: `artifact:conv.timeline.v1`
- **payload**: timeline JSON (blocks + metadata + current full `sources_pool` for local/exec recovery)
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

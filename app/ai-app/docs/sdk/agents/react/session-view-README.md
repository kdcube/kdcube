---
id: ks:docs/sdk/agents/react/session-view-README.md
title: "Session View"
summary: "Session view derived from timeline under TTL pruning."
tags: ["sdk", "agents", "react", "session", "ttl"]
keywords: ["cache TTL", "session view", "pruning", "visible context"]
see_also:
  - ks:docs/sdk/agents/react/context-browser-README.md
  - ks:docs/sdk/agents/react/context-layout.md
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/memory-recovery-path-README.md
---
# Session View (Cache TTL)

This document describes how the session view is derived from the timeline when cache TTL pruning is enabled. The goal is to keep the visible context small and stable after an Anthropic prompt cache TTL expires.

## Overview

- The session view is the list of timeline blocks rendered for the React agent.
- When `RuntimeCtx.session.cache_ttl_seconds` is set, the timeline render path applies TTL-based pruning before the final render.
- The last touch timestamp is stored on the timeline payload as `cache_last_touch_at` and updated on each render.
- The last TTL used is stored as `cache_last_ttl_seconds`.
  - **Bootstrap rule**: on the first render after loading a timeline, the stored TTL is used to decide pruning.
  - After that first render, the timeline TTL is synced to the current runtime/session TTL.
  - If both `cache_last_touch_at` and `cache_last_ttl_seconds` are missing, `cache_last_touch_at` is inferred from the block immediately before the latest `assistant.completion` block (fallback: that completion block).

## TTL Pruning Flow

- If `cache_ttl_seconds` is unset or <= 0, TTL pruning is disabled.
- If `cache_last_touch_at` is missing, it is set and no pruning happens (the cache is “armed”).
- If TTL has not expired, the timestamp is refreshed and no pruning happens.
- If TTL has expired, pruning is applied before rendering:
- Blocks older than the last N turns are hidden by path.
- Blocks inside the last N turns remain visible, except oversized binary artifacts (images/PDFs) which may be hidden unless in the intact window.
- If `keep_recent_turns` covers all turns, turn-window pruning is skipped, but lightweight artifact pruning still applies inside the recent window.
- The most recent M turns are guaranteed intact (no pruning).
- Hidden blocks keep a TTL-generated `replacement_text` for token estimates, compaction serializers, and retrieval metadata. This replacement is bounded by `cache_truncation_replacement_max_tokens` and by material growth over the original block.
- Rendered hidden completed React turns use the turn's
  `conv.working.summary` blocks as their primary model-facing representation.
  Non-turn, imported, or diagnostic blocks that have no working summary may
  still expose compact retrieval-index stubs with logical paths and small
  hints.
- In the normal new-timeline case, every completed React turn has a
  `conv.working.summary`, so the pruned historical turn renders as that summary
  card and not as a wall of individual hidden refs. React finalization internals
  are suppressed once the working summary covers the turn.
- Round scaffolding and transient chatter are suppressed in the pruned model
  view. This includes `react.round.start`,
  `react.thinking`, `react.notes`, `react.notice`, and
  `stage.suggested_followups`.
- Retrieval rows are implementation fallback rows for data without a working
  summary, not the old-turn representation for new completed React turns.
- User/assistant blocks are eligible for pruning when they are older than `keep_recent_turns` (they remain intact in the recent windows). This applies per block, so multiple prompt-like user entries or assistant completions from one older turn can be pruned independently.
- Internal Memory Beacons (`react.note`, `react.note.preserved`) and `conv.working.summary` blocks are not hidden by TTL pruning.
- External `user.followup`, `user.steer`, and their preserved copies are also not hidden by TTL pruning.
- If compaction also ran, older plan history may still remain directly reopenable through stable `ar:plan.latest:<plan_id>` refs that sit behind the visible history summaries.
- A system notice is appended when pruning runs:
  - Announce stack: `[SYSTEM MESSAGE] Context was pruned...`
  - Timeline block: `type=system.message`, `meta.kind=cache_ttl_pruned`
- Pruning is idempotent in practice: already-hidden blocks are skipped, so repeated TTL passes only hide additional eligible blocks.
- Rendering behavior for hidden blocks:
  - Hidden blocks stay in the timeline with `hidden=true` and optional `replacement_text`.
  - `Timeline.render()` prefers working-summary cards for hidden turns. Without a working summary, it renders compact retrieval stubs derived from the block metadata.
  - Stored `replacement_text` is not guaranteed to be rendered verbatim.
- If multiple hidden blocks share the same path, only one carries the TTL
  replacement text; the rest render empty. This hidden-block replacement rule
  does not suppress explicit ranged `react.read` blocks. A line/symbol range
  read is a distinct visible evidence block even when a full or preview block
  for the same logical path is already visible.

## Model-Facing Generations (new timelines)

New timelines should be read as three model-facing generations. The debug files
under `debug/rendering/rendered-...txt` show this rendered message stream.

### G0: Hot/full view

No TTL pruning has fired for the current render. The visible stream is the
post-compaction timeline window plus the current turn.

```text
[COMPACTED PRIOR CONVERSATION MEMORY]    # only if hard compaction already happened
  ... prior compacted memory checkpoint ...

TURN previous_visible_turn
  [USER MESSAGE]
  [AI Agent thinking...]
  [TOOL CALL ...]
  [TOOL RESULT ...]
  [WORKING SUMMARY]                      # durable turn summary, if already emitted
  [ASSISTANT MESSAGE]

TURN current_turn
  ... current active work ...

SOURCES POOL / ANNOUNCE                  # appended when render requested them
```

### G1: TTL-pruned session view

After prompt-cache TTL expiry, old turns outside the recent window are hidden.
For new React turns, the working summary is the historical turn's primary
model-facing representation.

```text
[COMPACTED PRIOR CONVERSATION MEMORY]    # still visible if already present
  ... prior compacted memory checkpoint ...

[WORKING SUMMARY]
[path: ws:turn_old.conv.working.summary.attempt.N]
Goal: ...
Outcome: ...
Key facts:
- ...
Refs:
- user: ar:turn_old.user.prompt
- decisive result: tc:turn_old.tc_x.result
- artifact: fi:turn_old.outputs/report.xlsx

[WORKING SUMMARY]
[path: ws:turn_other.conv.working.summary.attempt.N]
Goal: ...
Outcome: ...

TURN recent_visible_turn                 # within keep_recent_turns
  ... renders normally, except oversized files may be hidden ...

TURN current_turn                         # within keep_recent_intact_turns
  ... renders normally ...

[SYSTEM MESSAGE]
Context was pruned because the session TTL (...) was exceeded.
Logical paths still exist. Use currently visible summaries/checkpoints first; call
react.read(path) only when hidden content is actually needed.
```

### G2: Post-compaction view

When the TTL-pruned view is still too large, hard compaction replaces the older
visible prefix with a compacted prior-memory checkpoint. The retained suffix
starts at the selected cut point.

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
[path: su:turn_cut.conv.range.summary]
covered_turns: first_turn, second_turn, ... penultimate_turn, last_turn (count=N)
compacted_time_range: 2026-05-03T01:15:31Z -> 2026-05-05T23:42:47Z
conversation_first_message_ts: 2026-05-03T01:15:31Z
origin: model-generated compaction of older timeline blocks removed from the visible stream
use: treat this as prior conversation state; newer visible turns below may supersede it
recovery: use logical paths from the summary or react.memsearch/react.read when exact old content is needed
Goal: ...
Outcome: ...
Key facts:
- ...
Key artifacts:
- fi:...
[END COMPACTED PRIOR CONVERSATION MEMORY]

[ACTIVE/CARRIED PLAN]                    # only if needed
[PLAN HISTORY]                           # only if older plans were compacted
[FOLLOWUP DURING TURN preserved]         # only if user control input fell behind the cut

TURN retained_suffix_turn
  ... renders normally from the cut point onward ...

TURN current_turn
  ... renders normally ...
```

After persistence / next load, blocks before the latest
`conv.range.summary` are not part of the visible timeline payload. Their
artifacts and logical paths are still recoverable through stored artifacts,
`react.read`, `react.memsearch`, and refs carried in the summary/digest.

## Tool Call Truncation (hidden blocks)

Tool call and tool result blocks (`react.tool.call` / `react.tool.result`) are summarized in replacement text via a per-tool view:

- `react.read`, `react.write`, `react.patch`: tool-specific truncation.
- Other tools: default truncation.

Default replacement behavior:

- Parameters are truncated field-by-field (dict/list aware).
- Any string value starting with `ref:` is preserved verbatim.
- Search results are reduced to `sid`, `url`, `title`, `text`.
- Fetch results are reduced to `url`, `title`, `content`.

## Presentation Policy By Block Class

Current implemented policy is hard-coded in the timeline/session renderer. This
table is the contract a future per-tool/per-event policy registry should
preserve.

| Block class | G0 full view | G1 TTL-pruned view | G2 compaction input / output |
|---|---|---|---|
| `conv.working.summary` | Visible durable turn summary | Not hidden; used as the primary old-turn representation | Injected into the compaction prompt for covered turns; visible if retained |
| User/assistant messages | Render normally | Hidden outside recent window; represented through the turn working summary | Serialized into compaction prompt if covered; exact content recoverable by path |
| Tool calls/results | Render as compact call/result view | Hidden outside recent window; per-tool replacement text is stored for estimates/retrieval, but working summary suppresses row spam | Serialized for compaction; digest carries tool/artifact facts |
| Files/artifacts (`fi:`) | Metadata plus supported inline media | Oversized image/PDF base64 may be hidden even in recent window; old files are named through summary refs | Digest carries produced artifact refs; files remain recoverable by logical path |
| Sources (`so:`) | Sources pool or source rows when rendered | Old source rows may become compact source refs; no per-row timestamp in the pruned skeleton | Compaction may include source/tool facts if in covered blocks |
| Skills (`sk:`) / skill reads | Render where the read/materialized block is visible | Current behavior uses ordinary path visibility plus `react.read` result hints such as `exists_in_visible_context`; no generic singleton registry yet | Treat as context facts if covered; future singleton policy should avoid duplicate visible skill bodies |
| `react.note` / preserved notes | Visible internal memory beacons | Not hidden by TTL pruning | Preserved after compaction when needed |
| `user.followup` / `user.steer` | Visible in timeline order as user control input | Not hidden by TTL pruning | If behind the cut, copied forward as `.preserved` blocks |
| React round scaffolding/chatter | Visible while hot | Suppressed when hidden | Not promoted unless summarized |
| React finalization internals | Visible as final stats while hot | Suppressed when a working summary covers the turn | Summarized/digested if covered |

Per-tool replacement policy currently lives in
`apps/chat/sdk/solutions/react/session.py` (`ToolCallView` and
`VIEW_REGISTRY`). This is where a web-search-specific pruned result shape belongs
today. A future generalized event/tool policy should define, per block/event
type:

- G0 full-view rendering
- G1 TTL-pruned rendering or suppression
- G2 compaction serialization and whether to promote facts into the summary
- recovery refs and logical paths
- singleton/dedupe key, if the event should appear only once in visible context

## File and Artifact Pruning

- Image and PDF blocks keep only the most recent items within a total base64 budget.
- Oversized base64 artifacts are hidden and replaced.
- TTL-generated replacement text is separately bounded by `cache_truncation_replacement_max_tokens`.
- Explicit `react.hide` stores its replacement exactly as supplied. The
  replacement bound applies only to automatic TTL pruning.

## Turn Windows (configurable)

- `keep_recent_turns` is configurable via `RuntimeCtx.session.keep_recent_turns`.
- `keep_recent_intact_turns` is configurable via `RuntimeCtx.session.keep_recent_intact_turns`.

## Session View Behavior (current vs recommended)

| Aspect | Current behavior (our implementation) | Recommended behavior (our system) |
|---|---|---|
| Where it runs | Render-time pruning inside `Timeline.render()` | Render-time pruning under lock, same place |
| TTL state | `cache_last_touch_at`, `cache_last_ttl_seconds` persisted on timeline | Same, required for cross-node determinism |
| Trigger | TTL exceeded + buffer, per session config | Same |
| Older than `keep_recent_turns` | Hidden by `path` with `replacement_text` | Same |
| Between `keep_recent_turns` and `keep_recent_intact_turns` | Visible, but oversized file blocks may be hidden | Same |
| Last `keep_recent_intact_turns` | Fully intact (no hiding or truncation) | Same |
| User/assistant blocks | Eligible for hiding only when older than `keep_recent_turns` | Same |
| Tool calls/results | Summarized via tool views and hidden by `path` | Same |
| Files/artifacts | Image/PDF budget enforced; oversized base64 hidden | Same |
| Turn finalization internals | Suppressed after pruning when a working summary covers the turn | Same |
| Round scaffolding/chatter | Suppressed after pruning | Same |
| Recovery | `react.read(path)` can unhide originals | Same |
| Replacement text format | TTL tool views emit bounded JSON summaries with `tool_id`, `tool_call_id`, truncated `params`/`result`; files use `[TRUNCATED FILE] …`; generic uses `[TRUNCATED] …` | Same; keep `ref:` values unmodified |
| Announce/system messages | On prune: add announce entry and a persistent `system.message` (`meta.kind=cache_ttl_pruned`) | Same |
| Hidden vs visible semantics | Originals remain on the timeline as hidden blocks; render uses working summaries or retrieval stubs | Same |
| Image/PDF budget rules | `cache_truncation_keep_recent_images` + `cache_truncation_max_image_pdf_b64_sum` enforce caps | Same; configurable via `RuntimeCtx.session` |
| Skip rules | Always skip `turn.header`, `conv.range.summary`, `conv.working.summary`, `react.note`, `react.note.preserved`, `user.followup`, `user.steer`, `user.followup.preserved`, `user.steer.preserved`; others follow window rules | Same |
| TTL bootstrap | First render uses stored `cache_last_ttl_seconds`, then sync to runtime | Same |
| Size thresholds | Configurable: `cache_truncation_max_text_chars`, `cache_truncation_max_field_chars`, `cache_truncation_max_list_items`, `cache_truncation_max_dict_keys`, `cache_truncation_max_base64_chars`, `cache_truncation_replacement_max_tokens` | Same |
| Extensibility | Per-tool truncation views in `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/session.py` | Same |

## Runtime Configuration

TTL pruning reads from `RuntimeCtx.session` when available.
See `runtime-configuration-README.md` for the full list of runtime fields.

- `tenant`: tenant identifier.
- `project`: project identifier.
- `user_id`: user id for the conversation.
- `conversation_id`: conversation id.
- `user_type`: user type string.
- `turn_id`: current turn id.
- `bundle_id`: bundle id for the agent run.
- `timezone`: user timezone.
- `max_tokens`: max model tokens used for compaction decisions.
- `max_iterations`: max React iterations.
- `workdir`: working directory for this run.
- `outdir`: output directory for this run.
- `model_service`: model service handle.
- `on_before_compaction`: async hook before compaction.
- `on_after_compaction`: async hook after compaction.
- `save_summary`: async hook to persist compaction summaries.
- `started_at`: run start time in ISO format.
- `debug_log_announce`: emit announce blocks in debug logs.
- `debug_log_sources_pool`: emit sources pool in debug logs.
- `session`: session-level configuration for cache TTL and truncation thresholds.
- `read_visible_max_text_symbols`: max visible text characters for each
  `react.read` text path.
- `read_visible_max_tokens`: max visible tokens for each `react.read` text path.
- `read_visible_max_bytes`: raw byte cap for every `react.read` payload,
  including PDF/image.
- `read_visible_context_fraction`: additional read cap relative to `max_tokens`.
- `exec_text_preview_max_symbols`: max text characters embedded from each
  exec-produced text artifact.
- `tool_result_preview_max_text_symbols`: max text characters embedded from a
  large initial tool result before the rest is represented as shape/recovery
  metadata.

Runtime session fields:

- `cache_ttl_seconds`: TTL in seconds for prompt cache pruning.
- `cache_ttl_prune_buffer_seconds`: seconds subtracted from TTL to prune early before the next model call.
- `cache_truncation_max_text_chars`: max chars for text blocks in TTL truncation.
- `cache_truncation_max_field_chars`: max chars per field in tool params/results.
- `cache_truncation_max_list_items`: max list items retained during truncation.
- `cache_truncation_max_dict_keys`: max dict keys retained during truncation.
- `cache_truncation_max_base64_chars`: max base64 length before hiding.
- `cache_truncation_keep_recent_images`: number of image/PDF base64 artifacts to keep.
- `cache_truncation_max_image_pdf_b64_sum`: total base64 budget for kept image/PDF artifacts.
- `cache_truncation_replacement_max_tokens`: max tokens for automatic TTL-generated replacement text. Explicit `react.hide` stores its replacement exactly.
- `keep_recent_turns`: number of most recent turns to keep visible.
- `keep_recent_intact_turns`: number of most recent turns to keep intact (no pruning).

## Recovery Rule

When the model needs exact old content, it should first use the visible
working-summary/checkpoint facts. It should call `react.read(path)` only when
the exact hidden artifact/message/tool result is needed. When plan-history refs
are present after compaction, those `ar:` refs are usually the smoothest way to
reopen an older compacted plan in the same turn.

`react.read` is visible-context retrieval, not an unlimited loader. Text reads
are bounded by text-character and token caps, and every payload is bounded by a
raw byte cap. If text is larger than the visible read caps, React emits a
bounded preview with `status=truncated_for_visible_context`. The agent can
request a smaller visible preview with `max_text_symbols`; for large text that
must be model-visible, it should use `stats_only:true` and bounded
`react.read` ranges. Exec output is also capped and is not an uncapped read
channel. Caps apply independently per requested path. For discovery without
visible content, call `react.read` with `stats_only: true`; it emits metadata in
the status block only. PDF/image reads are all-or-marker under the raw byte cap.

## Timeline Persistence (what is stored)

Timeline payload includes:
- `blocks` (including hidden/truncated replacements)
- `sources_pool`
- `conversation_title`
- `conversation_started_at`
- `ts` (timeline timestamp)
- `cache_last_touch_at` (last render time)
- `cache_last_ttl_seconds` (TTL used for last prune/bootstrap)

Prune events add:
- An announce message to the per-render announce stack
- A persistent timeline block: `type=system.message` with `meta.kind=cache_ttl_pruned`

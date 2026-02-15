# Session View (Cache TTL)

This document describes how the session view is derived from the timeline when cache TTL pruning is enabled. The goal is to keep the visible context small and stable after an Anthropic prompt cache TTL expires.

## Overview

- The session view is the list of timeline blocks rendered for the React agent.
- When `RuntimeCtx.session.cache_ttl_seconds` (or legacy `RuntimeCtx.cache_ttl_seconds`) is set, the timeline render path applies TTL-based pruning before the final render.
- The last touch timestamp is stored on the timeline payload as `cache_last_touch_at` and updated on each render.
- The last TTL used is stored as `cache_last_ttl_seconds`.
  - **Bootstrap rule**: on the first render after loading a timeline, the stored TTL is used to decide pruning.
  - After that first render, the timeline TTL is synced to the current runtime/session TTL.
  - If both `cache_last_touch_at` and `cache_last_ttl_seconds` are missing, `cache_last_touch_at` is inferred from the block immediately before the last `assistant.completion` (fallback: the assistant block).

## TTL Pruning Flow

- If `cache_ttl_seconds` is unset or <= 0, TTL pruning is disabled.
- If `cache_last_touch_at` is missing, it is set and no pruning happens (the cache is “armed”).
- If TTL has not expired, the timestamp is refreshed and no pruning happens.
- If TTL has expired, pruning is applied before rendering:
- Blocks older than the last N turns are hidden by path with a replacement text.
- Blocks inside the last N turns remain visible, except oversized binary artifacts (images/PDFs) which may be hidden unless in the intact window.
- If `keep_recent_turns` covers all turns, old-turn pruning is skipped, but lightweight artifact pruning still applies inside the recent window.
- The most recent M turns are guaranteed intact (no pruning).
- Hidden blocks keep a short replacement text (no per-block `react.read` hint).
- User/assistant blocks are eligible for pruning when they are older than `keep_recent_turns` (they remain intact in the recent windows).
- A system notice is appended when pruning runs:
  - Announce stack: `[SYSTEM MESSAGE] Context was pruned...`
  - Timeline block: `type=system.message`, `meta.kind=cache_ttl_pruned`
- Pruning is idempotent in practice: already-hidden blocks are skipped, so repeated TTL passes only hide additional eligible blocks.
- Rendering behavior for hidden blocks:
  - Hidden blocks stay in the timeline with `hidden=true` and optional `replacement_text`.
  - `Timeline.render()` replaces their visible content with `replacement_text` (or `meta.replacement_text`) in the output stream.
  - If multiple blocks share the same path, only one carries the replacement text; the rest render empty.

## Tool Call Truncation (hidden blocks)

Tool call and tool result blocks (`react.tool.call` / `react.tool.result`) are summarized in replacement text via a per-tool view:

- `react.read`, `react.write`, `react.patch`: tool-specific truncation.
- Other tools: default truncation.

Default replacement behavior:

- Parameters are truncated field-by-field (dict/list aware).
- Any string value starting with `ref:` is preserved verbatim.
- Search results are reduced to `sid`, `url`, `title`, `text`.
- Fetch results are reduced to `url`, `title`, `content`.

## File and Artifact Pruning

- Image and PDF blocks keep only the most recent items within a total base64 budget.
- Oversized base64 artifacts are hidden and replaced.
- Replacement text is capped to the maximum text size.

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
| Recovery | `react.read(path)` can unhide originals | Same |
| Replacement text format | Tool views emit JSON summaries with `tool_id`, `tool_call_id`, truncated `params`/`result`; files use `[TRUNCATED FILE] …`; generic uses `[TRUNCATED] …` | Same; keep `ref:` values unmodified |
| Announce/system messages | On prune: add announce entry and a persistent `system.message` (`meta.kind=cache_ttl_pruned`) | Same |
| Hidden vs visible semantics | Hidden blocks render `replacement_text` only; originals are kept on the timeline | Same |
| Image/PDF budget rules | `cache_truncation_keep_recent_images` + `cache_truncation_max_image_pdf_b64_sum` enforce caps | Same; configurable via `RuntimeCtx.session` |
| Skip rules | Always skip `turn.header`, `conv.range.summary`; others follow window rules | Same |
| TTL bootstrap | First render uses stored `cache_last_ttl_seconds`, then sync to runtime | Same |
| Size thresholds | Configurable: `cache_truncation_max_text_chars`, `cache_truncation_max_field_chars`, `cache_truncation_max_list_items`, `cache_truncation_max_dict_keys`, `cache_truncation_max_base64_chars` | Same |
| Extensibility | Per-tool truncation views in `tools/session.py` | Same |

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
- `keep_recent_turns`: number of most recent turns to keep visible.
- `keep_recent_intact_turns`: number of most recent turns to keep intact (no pruning).

## Example (schematic)

After TTL pruning, the session view looks like this (system message appended at the end):

```
[TURN turn_...]
  [TRUNCATED] user prompt snippet...
  [TRUNCATED FILE] path=fi:turn_... mime=image/png size=...
  [TRUNCATED] tool call summary...

[TURN turn_...]
  user.prompt
  react.tool.call
  react.tool.result
  assistant.completion

[SYSTEM MESSAGE] Context was pruned because the session TTL (300s) was exceeded.
Use react.read(path) to restore a logical path (fi:/ar:/so:/sk:).
```

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

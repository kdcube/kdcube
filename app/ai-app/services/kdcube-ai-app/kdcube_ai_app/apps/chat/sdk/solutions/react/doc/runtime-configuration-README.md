# Runtime Configuration

This document summarizes runtime configuration fields for the React runtime (`RuntimeCtx`) and its session-level settings.

## RuntimeCtx

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
- `debug_timeline`: when `true`, write the fully rendered model context to `debug/rendering/` (one file per render).
- `session`: session-level configuration (see below).
- `cache`: cache-related limits (see below).

## RuntimeSessionConfig (RuntimeCtx.session)

Cache TTL pruning and truncation settings used by the session view:

| Setting                                  | Description                                                            | Default     |
|------------------------------------------|------------------------------------------------------------------------|-------------|
| `cache_ttl_seconds`                      | TTL in seconds for prompt cache pruning.                               | `300`       |
| `cache_ttl_prune_buffer_seconds`         | Seconds subtracted from TTL to prune early before the next model call. | `10`        |
| `cache_truncation_max_text_chars`        | Max chars for text blocks in TTL pruning replacement text.             | `4000`      |
| `cache_truncation_max_field_chars`       | Max chars per field in tool params/results summaries.                  | `1000`      |
| `cache_truncation_max_list_items`        | Max list items retained during summaries.                              | `50`        |
| `cache_truncation_max_dict_keys`         | Max dict keys retained during summaries.                               | `80`        |
| `cache_truncation_max_base64_chars`      | Max base64 length before hiding.                                       | `4000`      |
| `cache_truncation_keep_recent_images`    | Number of image/PDF base64 artifacts to keep (within recent turns).    | `2`         |
| `cache_truncation_max_image_pdf_b64_sum` | Total base64 budget for kept image/PDF artifacts.                      | `1_000_000` |
| `keep_recent_turns`                      | Number of most recent turns to keep visible.                           | `10`        |
| `keep_recent_intact_turns`               | Number of most recent turns to keep intact (no pruning).               | `2`         |

When TTL pruning runs, a system notice is emitted:
- Announce stack: `[SYSTEM MESSAGE] Context was pruned...`
- Timeline block: `type=system.message`, `meta.kind=cache_ttl_pruned`

## RuntimeCacheConfig (RuntimeCtx.cache)

Limits for cache-related operations outside TTL pruning.

| Setting                         | Description                                                                 | Default |
|---------------------------------|-----------------------------------------------------------------------------|---------|
| `editable_tail_size_in_tokens`  | Max token distance from static tail allowed for `react.hide`.               | `2000`  |
| `cache_point_min_rounds`        | Minimum **total** rounds required before placing the pre‑tail checkpoint.   | `2`     |
| `cache_point_offset_rounds`     | Distance (rounds) from tail to the pre‑tail checkpoint once placed.         | `4`     |

These cache‑point settings are applied when rendering the timeline context and also
gate `react.hide` (paths before the pre‑tail cache point cannot be hidden).

Rounds are counted across the **visible timeline slice** (post‑compaction), which
may include blocks from previous turns.

## Legacy Cache Fields

For backward compatibility, `RuntimeCtx` still exposes top-level cache fields (for now). If set, they override the session defaults.

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
- `session`: session-level configuration (see below).

## RuntimeSessionConfig (RuntimeCtx.session)

Cache TTL pruning and truncation settings used by the session view:

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

When TTL pruning runs, a system notice is emitted:
- Announce stack: `[SYSTEM MESSAGE] Context was pruned...`
- Timeline block: `type=system.message`, `meta.kind=cache_ttl_pruned`

## Legacy Cache Fields

For backward compatibility, `RuntimeCtx` still exposes top-level cache fields (for now). If set, they override the session defaults.

---
id: ks:docs/sdk/agents/react/runtime-configuration-README.md
title: "Runtime Configuration"
summary: "RuntimeCtx and session configuration fields for React v2, including knowledge hooks."
tags: ["sdk", "agents", "react", "configuration"]
keywords: ["RuntimeCtx", "RuntimeSessionConfig", "cache config", "pruning settings", "knowledge_search_fn", "knowledge_read_fn", "bundle_storage"]
see_also:
  - ks:docs/sdk/agents/react/compaction-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
  - ks:docs/sdk/agents/react/feedback-README.md
---
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
- `bundle_storage`: optional per-bundle managed storage directory for bundle-owned data such as cloned repos, built indexes, and other readonly data prepared by the bundle.
- `workspace_implementation`: workspace backend selector. `custom` uses the existing artifact/timeline rehost model. `git` resolves `fi:<turn>.files/...` slices from the configured git-backed lineage snapshots. This does not by itself force the prompt into explicit-pull mode.
- `workspace_git_repo`: optional remote git repo URL used as the authoritative backup/version-control store for React's git-backed workspace lineage snapshots.
- `model_service`: model service handle.
- `knowledge_search_fn`: bundle‑supplied search function for `react.search_knowledge`.
- `knowledge_read_fn`: bundle‑supplied resolver for `react.read(ks:...)` paths.
- `on_before_compaction`: async hook before compaction.
- `on_after_compaction`: async hook after compaction.
- `save_summary`: async hook to persist compaction summaries.
- `started_at`: run start time in ISO format.
- `debug_log_announce`: emit announce blocks in debug logs.
- `debug_log_sources_pool`: emit sources pool in debug logs.
- `debug_timeline`: when `true`, write the fully rendered model context to `debug/rendering/` (one file per render).
- `workspace_implementation`: workspace paradigm selector. `custom` keeps artifact/timeline/hosting-backed slice hydration. `git` enables git-backed `fi:<turn>.files/...` slice hydration and the corresponding git-aware agent instructions.
- `session`: session-level configuration (see below).
- `cache`: cache-related limits (see below).

### `bundle_storage` semantics

- `bundle_storage` is not the shared storage root. It is the resolved directory for the current bundle under that shared root, typically isolated by tenant and project.
- Main workflow initialization attempts to populate it from bundle spec + tenant + project via the bundle storage helper.
- Cached workflow instances should also refresh it when request context is rebound to a new tenant/project/turn.
- Typical contents are bundle-managed assets such as cloned repos, generated indexes, and readonly data areas prepared by the bundle.
- Bundles that expose `ks:` usually rely on this directory together with their `knowledge_read_fn`.
- In isolated exec, the corresponding exec-visible env var is `BUNDLE_STORAGE_DIR`.
- Isolated exec can derive the same directory from bundle spec + tenant + project when `RuntimeCtx.bundle_storage` is missing, but that is a fallback for robustness, not the primary contract.
- Many tests and synthetic runtime constructions use `RuntimeCtx()` directly, so code must not assume `bundle_storage` is always populated outside real workflow initialization.

### `workspace_git_repo` semantics

- `workspace_git_repo` matters only when `workspace_implementation=git`.
- `workspace_git_repo` is a runtime hint, not a local filesystem path.
- It should come from `REACT_WORKSPACE_GIT_REPO`.
- It identifies the remote repo engineering uses to persist conversation-scoped workspace lineage history.
- React itself should not treat it as a repo to clone/fetch from inside exec; exec remains network-isolated.
- Authentication should reuse the same git auth environment already used by bundle git loading:
  - `GIT_HTTP_TOKEN`
  - `GIT_HTTP_USER`
  - `GIT_SSH_KEY_PATH`
  - `GIT_SSH_KNOWN_HOSTS`
  - `GIT_SSH_STRICT_HOST_KEY_CHECKING`

### `workspace_implementation`

This is the only React workspace paradigm switch:

- `custom`
  - agent uses `fi:` + `react.pull(...)`
  - `.files/...` pulls are hydrated from artifact/timeline/hosting-backed snapshot state
  - agent is not instructed to reason about the workspace as git
- `git`
  - agent uses `fi:` + `react.pull(...)`
  - `.files/...` pulls are hydrated from git-backed lineage snapshots
  - current turn root `out/<current_turn>/` is bootstrapped as a local repo
  - that repo keeps lineage history available but does not eagerly populate the worktree
  - agent is instructed that the activated current-turn workspace can be explored locally with git commands except pull/push/fetch

Exact attachment/binary pulls remain point-wise and hosting-backed in both modes.

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

## Workspace persistence (env)

Turn workspaces can be persisted as execution snapshots. This is **diagnostic only** and
not required for conversation correctness.

- `REACT_PERSIST_WORKSPACE=1` (default): persist snapshot
- `REACT_PERSIST_WORKSPACE=0`: skip snapshot

See `react-turn-workspace-README.md` for details.

## Legacy Cache Fields

For backward compatibility, `RuntimeCtx` still exposes top-level cache fields (for now). If set, they override the session defaults.

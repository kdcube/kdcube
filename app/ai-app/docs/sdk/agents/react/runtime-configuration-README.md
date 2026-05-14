---
id: ks:docs/sdk/agents/react/runtime-configuration-README.md
title: "Runtime Configuration"
summary: "RuntimeCtx, version selection, and session configuration fields for the React runtime, including knowledge hooks and experimental multi-action mode."
tags: ["sdk", "agents", "react", "configuration"]
keywords: ["RuntimeCtx", "RuntimeSessionConfig", "cache config", "pruning settings", "knowledge_search_fn", "knowledge_read_fn", "bundle_storage", "AI_REACT_AGENT_VERSION", "AI_REACT_AGENT_MULTI_ACTION", "AI_REACT_MAX_ITERATIONS", "AI_REACT_RENDER_THINKING", "multi_action_mode"]
see_also:
  - ks:docs/sdk/agents/react/compaction-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
  - ks:docs/sdk/agents/react/feedback-README.md
  - ks:docs/sdk/agents/react/react-round-README.md
---
# Runtime Configuration

This document summarizes runtime configuration fields for the React runtime (`RuntimeCtx`) and its session-level settings.

## Runtime version selection

React is selected before the runtime instance is built:

- `AI_REACT_AGENT_VERSION=v2|v3`
  - `v2` is the production single-action runtime
  - `v3` is the experimental runtime
- `AI_REACT_AGENT_MULTI_ACTION=off|safe_fanout`
  - passed through `RuntimeCtx.multi_action_mode`
  - currently relevant only for `v3`
  - `safe_fanout` allows repeated action-channel instances in one response, but accepted actions are still executed sequentially, not in parallel

## Iteration budget selection

The base ReAct decision/tool-use round cap is resolved before the runtime loop starts:

1. Bundle props `config.react.max_iterations` / `react.max_iterations`
2. Assembly/env `ai.react.max_iterations` / `AI_REACT_MAX_ITERATIONS`
3. Runtime fallback `15`

The resolved value is passed through `RuntimeCtx.max_iterations`. Reactive external-event credit, when enabled, can temporarily add bounded extra iterations during the active turn.

## Thinking rendering

Live model thinking blocks are persisted as `react.thinking` timeline blocks. Rendering them into the active ReAct context is controlled by:

1. Bundle props `config.react.render_thinking` / `react.render_thinking`
2. Assembly/env `ai.react.render_thinking` / `AI_REACT_RENDER_THINKING`
3. Runtime fallback `true`

This switch only controls rendering. It does not change channel parsing or persistence. Thinking from pruned/compacted historical rounds is not rendered.

## Timeline Render Debug

Rendered prompt snapshots are controlled separately from thinking visibility:

1. Bundle props `config.react.debug_timeline` / `react.debug_timeline`
2. Assembly/env `ai.react.debug_timeline` / `AI_REACT_DEBUG_TIMELINE`
3. Bundle code default, usually `false` for normal bundles and `true` for
   diagnostic/reference bundles

When enabled, snapshots are written under `REACT_DEBUG_ROOT`, normally
`/react-debug` in CLI and ECS deployments, with retention controlled by
`REACT_DEBUG_KEEP_FILES`.

## RuntimeCtx

- `tenant`: tenant identifier.
- `project`: project identifier.
- `user_id`: user id for the conversation.
- `conversation_id`: conversation id.
- `user_type`: user type string.
- `turn_id`: current turn id.
- `bundle_id`: bundle id for the agent run.
- `timezone`: user timezone.
- `max_tokens`: max model input tokens used for compaction decisions; this
  budget includes system/instruction text and the rendered timeline.
- `read_visible_max_text_symbols`: max visible text characters per `react.read`
  text path.
- `read_visible_max_tokens`: max model-visible tokens per `react.read` text
  path.
- `read_visible_max_bytes`: raw byte cap for every `react.read` payload.
- `read_visible_context_fraction`: additional clamp that limits a read preview
  to a fraction of `max_tokens`.
- `exec_text_preview_max_symbols`: max text characters embedded as preview for
  each text artifact produced by exec tools.
- `tool_result_preview_max_text_symbols`: max text characters embedded from a
  large initial tool result before the prompt renderer replaces the remainder
  with shape/recovery metadata.
- `max_iterations`: base max ReAct decision/tool-use iterations, resolved from bundle config, then assembly/env, then fallback `15`.
- `reactive_event_iteration_credit_enabled`: enable live reactive-event iteration credit on the current turn. Default `true`.
- `reactive_event_iteration_credit_per_event`: default iteration credit minted by one accepted live reactive event. Default `1`.
- `reactive_event_iteration_credit_cap`: max extra iterations that one turn may accumulate from live reactive events. When unset, React defaults it to the configured `max_iterations`.
- `workdir`: working directory for this run.
- `outdir`: output directory for this run.
- `bundle_storage`: optional per-bundle managed storage directory for bundle-owned data such as cloned repos, built indexes, and other readonly data prepared by the bundle.
- `workspace_implementation`: workspace backend selector. `custom` uses the existing artifact/timeline rehost model. `git` resolves `fi:<turn>.files/...` slices from the configured git-backed lineage snapshots. This does not by itself force the prompt into explicit-pull mode.
- `workspace_git_repo`: optional remote git repo URL used as the authoritative backup/version-control store for React's git-backed workspace lineage snapshots.
- `multi_action_mode`: decision contract selector. `off` keeps the one-action-per-response contract. `safe_fanout` enables the experimental v3 multi-action protocol.
- `model_service`: model service handle.
- `knowledge_search_fn`: bundle‑supplied search function for `react.search_knowledge`.
- `knowledge_read_fn`: bundle‑supplied resolver for `react.read(ks:...)` paths.
- `on_before_compaction`: async hook before compaction.
- `on_after_compaction`: async hook after compaction.
- `save_summary`: async hook to persist compaction summaries.
- `started_at`: run start time in ISO format.
- `debug_log_announce`: emit announce blocks in debug logs.
- `debug_log_sources_pool`: emit sources pool in debug logs.
- `debug_timeline`: when `true`, write the fully rendered model context to the configured render-debug root. The runtime no longer writes into the package directory by default.
- `debug_timeline_root`: process-visible directory for render-debug files. It is normally populated from `REACT_DEBUG_ROOT`; CLI and ECS deployments mount it as `/react-debug`.
- `debug_timeline_keep_files`: rolling retention for `rendered-*.txt` files. The platform default is `100`.
- `render_thinking`: when `true`, render live `react.thinking` blocks as `[thinking]` timeline sections. When `false`, hide those blocks from the rendered model context.
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

`react.rg` searches only files already materialized in the local artifact workspace on the worker handling the turn. It does not search unpulled lineage snapshots, hidden/pruned timeline blocks, or `ks:`. If a task needs local search over older state, first identify the `fi:` ref from visible context or `react.memsearch`, then materialize it with `react.pull`; use `react.checkout` only when the pulled `files/...` ref must become an editable current-turn copy. Preferred `react.rg` roots are visible path forms: `files/...`, `outputs/...`, `attachments/...`, `turn_<id>/files/...`, `turn_<id>/outputs/...`, `turn_<id>/attachments/...`, or matching `fi:` artifact paths.

## Visible read limits

`react.read` has three different limits because the payloads have different
units:

| RuntimeCtx field | Assembly path | Unit | Applies to |
|---|---|---|---|
| `read_visible_max_text_symbols` | `ai.react.read_visible_max_text_symbols` | text characters | text content returned by `react.read` |
| `read_visible_max_tokens` | `ai.react.read_visible_max_tokens` | model tokens | text content returned by `react.read` |
| `read_visible_max_bytes` | `ai.react.read_visible_max_bytes` | raw bytes | every readable payload, including PDF/image |
| `read_visible_context_fraction` | `ai.react.read_visible_context_fraction` | fraction of `max_tokens` | text read budget clamp |
| `exec_text_preview_max_symbols` | `ai.react.exec_text_preview_max_symbols` | text characters | text files emitted by exec tools |
| `tool_result_preview_max_text_symbols` | `ai.react.tool_result_preview_max_text_symbols` | text characters | large normal tool-result render previews |

Text reads return a configured bounded preview when the payload is larger than
the visible caps. Per-call
`react.read({"paths":[...],"max_text_symbols":N})` is a text-only request for a
smaller explicit preview. It is clamped by
`read_visible_max_text_symbols`, `read_visible_max_tokens`, and
`read_visible_context_fraction`. These caps apply per requested path, not across
the whole `paths` list. `stats_only: true` bypasses content materialization and
returns metadata in the `react.read` status block.

Large initial tool results use `tool_result_preview_max_text_symbols` before
the next decision prompt is built. The timeline keeps the full `tc:` result,
but the model-visible view contains a truncated preview, a depth-limited shape,
size metadata, and recovery instructions.

ANNOUNCE includes a short `[CONTEXT CAPS]` line with the active regular-read,
`ks_read`, tool-result-preview, and exec-file-preview caps so the model can
choose between bounded previews and ranged `react.read` recovery. `ks_read`
prints `none` for each dimension when no `knowledge_read_visible_*` cap is
configured. Exec output is also capped; it can compute over data or create
smaller derived artifacts, but it is not an uncapped channel for putting full
content into model-visible context.

Skills are not read-capped. `ks:` knowledge-space article reads are uncapped
only when the `ai.react.knowledge_read_visible_*` values are unset/null; if a
deployment configures them, capped `ks:` content should be recovered by
`stats_only` and ranged reads like any other capped text.

PDF/image reads are not partially sliced. If the raw payload is under
`read_visible_max_bytes`, React attaches it whole as multimodal content. If it
is over the cap, React emits a metadata/recovery marker instead of a partial
image or partial PDF.

`read_visible_max_bytes` is only the admission cap. Once admitted, image/PDF
blocks are counted in prompt-size estimates as model tokens (image
dimensions/PDF pages), because multimodal providers bill and limit them as
tokens.

### Reactive external-event iteration credit

These fields control how active-turn external events affect loop budget:

- only **reactive** events mint credit
- current runtime behavior treats `followup` as reactive by default
- `steer` never mints iteration credit; it is a control interrupt
- future structured external events may opt into the same policy through their event payload

Effective loop ceiling:

```text
effective_max_iterations = base_max_iterations + reactive_iteration_credit
```

where:
- `base_max_iterations` comes from `RuntimeCtx.max_iterations`
- `reactive_iteration_credit` accumulates during the turn
- `reactive_iteration_credit` is bounded by `reactive_event_iteration_credit_cap`

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

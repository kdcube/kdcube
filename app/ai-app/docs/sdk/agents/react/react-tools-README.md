---
id: ks:docs/sdk/agents/react/react-tools-README.md
title: "React Tools"
summary: "Current built-in react.* tool catalog, path contracts, and execution semantics."
tags: ["sdk", "agents", "react", "tools"]
keywords: ["react.read", "react.pull", "react.checkout", "react.write", "react.patch", "react.memsearch", "react.hide", "react.rg", "react.plan"]
see_also:
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/react-round-README.md
  - ks:docs/sdk/agents/react/tool-call-blocks-README.md
  - ks:docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
---
# React Tools (react.*)

This document describes the current built-in `react.*` tool surface.

These tools are injected into the React decision runtime. They are not the same as:

- external tools such as `web_tools.*` or `rendering_tools.*`
- bundle tools
- exec runtime shell commands

## Common Rules

- All React tools are invoked through `action=call_tool`.
- A tool call appends `react.tool.call` and one or more `react.tool.result` blocks.
- If decision `notes` are present, the runtime emits a user-visible notes block before the tool call.
- React tools work with logical paths and current-turn workspace paths, not arbitrary host paths.
- Built-in React tools do not accept exec-only `physical_path` values returned from other tool results unless explicitly documented.
- Each tool section below defines its accepted inputs, timeline effects, and normal use case.

## Path Contracts

### Logical paths

Used for reading, hiding, or reopening existing artifacts:

- `fi:...` — files, outputs, attachments
- `ar:...` — timeline artifact aliases
- `so:...` — sources pool rows
- `su:...` — summary blocks
- `tc:...` — tool call / tool result records
- bundle-provided logical namespaces such as `ks:...`

### Current-turn workspace paths

Used for writing or patching new current-turn files:

- OUT_DIR-relative paths under the current turn
- `files/<scope>/...` for durable workspace/project state
- `outputs/<scope>/...` for reports, exports, test results, demos, and other produced artifacts
- unqualified `react.write` and exec contract paths default to `outputs/...`; use `files/...` explicitly for durable workspace/project state

Do not pass logical `fi:` paths to `react.write` or `react.patch`.

## Built-in Tool Catalog

### `react.read`

Reads existing logical artifacts back into the visible timeline.

- Text artifact previews shown on the timeline are line-numbered. The numbers
  are model-facing viewing prefixes, not file bytes; use them for `react.read`
  ranges and patch locations, but omit them from patch/full-file content.
- input: `paths: list[str]`
- optional input: `items: list[object]` — exact read specs with `path` plus
  optional `line_start`/`line_count` or `offset_text_symbols`/`max_text_symbols`.
  `react.rg` returns ready-to-pass `read_item` entries for this field.
- optional input: `max_text_symbols` — maximum visible text characters per text
  path. This requests a smaller explicit preview than the configured default.
- optional input: `line_numbers: true|false` — include line numbers for ranged
  line reads. Defaults to true for ranged items.
- line range previews are labeled as `lines: [start-end]/total`; if a text
  preview is cut mid-line, that partial line is called out separately and is not
  counted as fully visible.
- optional input: `stats_only: true` — return size/mime/token metadata in the
  status block without emitting content blocks.
- byte cap: `read_visible_max_bytes` is a raw-payload guard for every path.
  For PDF/image multimodal reads there is no partial read: the payload is
  attached only when it is under the byte cap; otherwise `react.read` returns a
  recovery marker.
- accepted paths: `ar:`, `tc:`, `fi:`, `so:`, `su:`, `ws:`, `ks:`, `sk:`
- emits: one JSON status/result block plus one visible content block per reopened path
- deduplication: visible blocks are not duplicated
- hidden data: hidden/pruned blocks can be reopened by exact path
- generated views: `ar:<turn_id>.react.turn.index` is reconstructed on demand from the persisted turn log
- large text guard: oversized text payloads are copied back only as bounded
  visible previews. The status row uses
  `status=truncated_for_visible_context`, the preview names the exact path, and
  the full payload remains recoverable by logical path. If the caller supplies
  `max_text_symbols`, the preview is further clamped to that text-only limit.
- cap distribution: caps are applied independently per requested path, not
  divided across the `paths` list. For broad batches, use `stats_only: true` or
  a smaller `max_text_symbols` before deciding what to materialize.
- caps are configured under `ai.react` in `assembly.yaml`:
  `read_visible_max_text_symbols`, `read_visible_max_tokens`,
  `read_visible_max_bytes`, `read_visible_context_fraction`, and
  `exec_text_preview_max_symbols`. Normal large tool results are also rendered
  through `tool_result_preview_max_text_symbols` before the next decision
  prompt, with a shape preview and recovery instructions.
- units are intentionally separate:
  - text symbols = text characters, only for text previews
  - tokens = model-visible budget guard
  - bytes = raw payload admission guard for all readable payloads

Use it when the path already exists and React needs to inspect the content again.

For large `tc:`/`fi:` payloads, use `react.read` to inspect a bounded preview and
confirm the path. Then process the exact content inside
`exec_tools.execute_code_python` by calling `ctx_tools.fetch_ctx(path=...)` from
the exec code. `fetch_ctx` returns `path`, `mime`, and `payload`; for JSON mime,
`payload` is parsed JSON. Repeating `react.read` on the same large payload
returns another bounded preview and should not be used as a bulk-data processing
loop.

For `so:sources_pool[...]`, `fetch_ctx` returns source rows. Web rows use `text`
for the preview/snippet and `content` for the fetched page body when available;
bulk processors should prefer `content` over `text`.

`react.read` on `so:sources_pool[...]` also returns JSON source rows, not a prose
view. These reads are full by default and include `items_stats` metadata. If
`max_text_symbols` is explicitly provided, the runtime caps only source text
fields while preserving valid JSON rows. `so:sources_pool[...]` result blocks
are not passed through the generic prompt preview cap.

Example bounded preview:

```json
{"paths":["fi:turn_abc.outputs/report.md"],"max_text_symbols":4000}
```

Example exact range read:

```json
{"items":[{"path":"fi:turn_abc.outputs/page.html","line_start":812,"line_count":60}]}
```

Example metadata-only read:

```json
{"paths":["tc:turn_abc.tc_big.result","fi:turn_abc.outputs/report.pdf"],"stats_only":true}
```

### `react.pull`

Materializes historical `fi:` refs locally so code or later tools can use them by local path.

- input: `paths: list[str]`
- accepted paths: exact `fi:` refs
- output: local files plus a result block listing pulled paths
- workspace effect: does not define or replace the active current-turn workspace
- historical layout: keeps historical material under its historical turn root
- `git` workspace mode: hydrates text workspace slices from git-backed lineage snapshots
- binary refs: exact binary refs may remain hosting-backed if the execution sandbox cannot materialize them locally

Use it for historical/reference material, not for defining what the current editable workspace should contain.

### `react.checkout`

Copies materialized historical `fi:<turn>.files/...` refs into the active current-turn workspace.

- input: `paths: list[str]`, `mode: replace|overlay`
- accepted paths: workspace `fi:<turn>.files/...` refs
- output: current-turn workspace files plus a compact checkout result block
- checkout result: includes `checked_out_from`, per-source file counts, and a tree-like `materialized` summary under `turn_<current>/files`; it is not a per-file manifest
- `mode=replace`: clears and rebuilds the current-turn workspace
- `mode=overlay`: applies refs on top of the existing current-turn workspace
- conflict rule: later refs override earlier refs if they overlap

Use it when React needs an editable runnable/searchable/testable project tree in the current turn. For older refs that may not be local on the worker, call `react.pull(paths=[...])` first, then `react.checkout(...)`. Use `react.pull` alone when the older version is only reference material.

### `react.write`

Creates a new text artifact.

- input: `path`, `content`, optional `channel`, optional `kind`
- accepted paths: current-turn relative paths, not logical `fi:` paths
- namespace behavior: use `files/...` for durable workspace/project state and `outputs/...` for produced artifacts; unqualified paths default to `outputs/...`
- channels: `canvas`, `timeline_text`, `internal`
- kinds: `display`, `file`
- output: local text artifact plus timeline/result blocks
- external file behavior: hosts and emits a downloadable file only when external and `kind=file`
- internal behavior: `channel=internal` writes Internal Memory Beacons as `react.note`

Use it for text artifacts only. For PDFs, PPTX, DOCX, PNG, and other binary deliverables, use `rendering_tools.write_*` or exec tools.

### `react.patch`

Updates an existing current-turn materialized text file under `files/...` or `outputs/...`.

- input: `path`, `patch`
- accepted paths: current-turn physical OUT_DIR-relative paths; prefer concise paths such as `files/<scope>/file.py` or `outputs/<scope>/page.html`, not logical `fi:` paths
- patch format: unified diff or full replacement text
- generated unified-diff hunk counts are normalized before apply; a wrong `@@ -a,b +c,d @@` count should not force a full-file rewrite when the hunk content is otherwise correct
- rendered-preview line-number prefixes are rejected; remove the viewing prefixes and retry
- output: updated local file plus normal tool call/result blocks
- file origin does not matter: current-turn files produced by exec, `react.write`, `react.patch`, or `react.checkout` are patchable once they exist locally
- older files are never patched in place; materialize with `react.pull` if needed, then use `react.checkout` for historical `files/...` refs you need to edit and patch the resulting current-turn path

Use it when the file already exists and React wants a targeted edit instead of a full rewrite. Prefer unified diff for targeted changes; use full replacement only for intentional whole-file rewrites or when a targeted diff still cannot match the file. Do not use `react.write` just to register an existing file for patching.

### `react.memsearch`

Searches prior conversation memory and returns turn-level recovery handles.

Inputs:

- `query`: natural-language query. Required for semantic search. Optional for ordinal/temporal/timeline lookup.
- `targets`: list of snippet families to return. Supported values: `summary`, `user`, `assistant`, `attachment`.
- `mode`: `semantic`, `ordinal`, `temporal`, or `timeline`. Default: `semantic`.
- `scope`: `conversation` or `user`. Default: `conversation`. `user` searches the current user across conversations while preserving the same tenant/project and storage boundary.
- `ordinal`: 1-based turn number in the selected scope/window. Used by `mode="ordinal"`.
- `from` / `to`: ISO timestamps. `to` is exclusive.
- `top_k`: maximum turn hits.
- `days`: lookback limit. Defaults wider for catalog modes than for semantic mode.

Output per hit:

- `turn_id`
- `turn_index_path`, usually `ar:<turn_id>.react.turn.index`
- `working_summary_path`, usually `ws:<turn_id>.conv.working.summary`
- `ordinal` and `total_turns` when the hit came from the turn catalog
- `started_at` / `ended_at` when known
- `snippets`: compact readable rows with `role`, `path`, `ts`
- semantic scores when the hit came from vector/text search

Targets:

- `summary`: working-summary snippets, best first target for recovery.
- `user`: user prompt/followup/steer snippets.
- `assistant`: assistant completion snippets.
- `attachment`: user attachments, including event-scoped followup attachments.

Event-scoped attachment paths use:

```text
fi:<turn>.external.<kind>.attachments/<message_id>/<filename>
```

Scenarios:

| User intent | Tool params | Next step |
|---|---|---|
| "What have we talked about so far?" | `mode="timeline"`, `targets=["summary"]`, `order="asc"`, high enough `top_k`, no `query` | summarize returned turn summaries |
| "Find the Anthropic invoice ZIP attempt" | `query`, `targets=["summary"]` | read returned `ws:` summary, then exact refs |
| "What was the second turn about?" | `mode="ordinal"`, `ordinal=2`, `targets=["summary","user","assistant"]` | answer from snippets or read `turn_index_path` |
| "What did we discuss in March?" | `mode="temporal"`, `from`, `to`, `targets=["summary","user"]` | scan returned turns, then read exact refs |
| "Find invoice discussion from March" | `query`, `from`, `to`, `targets=["summary","user","assistant"]`, no `mode` | semantic search narrowed to the time window |
| "I need the old file but only remember the topic" | `query`, `targets=["summary","attachment"]` | read/pull returned `fi:` refs or read the turn index |

Examples:

```json
{"query": "Anthropic invoice ZIP", "targets": ["summary"], "top_k": 5}
```

```json
{"mode": "ordinal", "ordinal": 2, "targets": ["summary", "user", "assistant"]}
```

```json
{"mode": "temporal", "from": "2026-03-01T00:00:00Z", "to": "2026-04-01T00:00:00Z", "targets": ["summary", "user"]}
```

Rules:

- Do not use memsearch when the exact needed path is already visible; call `react.read` or `react.pull`.
- Prefer `targets=["summary"]` first for broad recovery because summaries carry goal/outcome/refs.
- For broad overview questions, use `mode="timeline"` with `targets=["summary"]` and no query. Generic query strings such as `"conversation topics discussed"` do not help.
- For `mode="ordinal"`, `mode="temporal"`, and `mode="timeline"`, omit `query`. These modes use the turn catalog, not semantic matching.
- For topic plus date range, omit `mode`; pass `query`, `from`, and `to` so semantic search is narrowed by time.
- Read `turn_index_path` when the returned snippets identify the turn but do not name the needed exact `ar:`, `tc:`, `fi:`, or `so:` path.
- Batch-read exact refs after discovery; avoid one round per path.

### `react.hide`

Replaces a visible tail block with a short placeholder.

- input: `paths: list[str]`, optional replacement text
- accepted paths: logical paths such as `ar:`, `fi:`, `tc:`, `so:`, `ks:`
- output: hidden replacement blocks; original content remains stored
- restriction: only the editable tail window can be hidden
- cache safety: runtime enforces checkpoint rules before hiding

Use it to shrink still-visible bulky material that is no longer needed in the active prompt.

### `react.rg`

Searches safely over files already materialized in the local artifact workspace without shell execution.

- input: file name regex and/or content regex
- scope: rooted search only, under runtime-managed artifact files already present on this worker; root can be a file or a subtree
- preferred roots: omit `root`, or use `files/...`, `outputs/...`, `attachments/...`, `turn_<id>/files/...`, `turn_<id>/outputs/...`, `turn_<id>/attachments/...`, or matching `fi:` artifact paths such as `fi:<turn_id>.files/...`, `fi:<turn_id>.outputs/...`, and `fi:<turn_id>.user.attachments/...`
- legacy roots: `outdir` and `outdir/<path>` are still accepted for older callers, but new calls should use visible path forms
- not a search over hidden/pruned timeline, unpulled historical snapshots, or `ks:`; locate older refs first, then `react.pull` them before local search; checkout only when you need an editable current-turn copy
- hits: include logical paths suitable for `react.read`
- content matches: include line-numbered previews and `read_item` ranges
- `context_lines` controls how many surrounding lines are included in the
  suggested `read_item` range around each match
- next step: pass `read_items` to `react.read({"items":[...]})` for exact visible regions

### `react.plan`

Creates or updates the plan tracked inside the same React loop.

- input: create/update/close operation plus plan step data
- output: `react.plan` timeline blocks and ANNOUNCE updates
- plan is a tool, not a separate planner component
- later rounds can update step state

## Bundle-Provided React-Style Tools

Some bundles also expose tools that fit the same interaction model but are not part of the built-in core set.

Examples:

- `react.search_knowledge`
  - bundle-provided knowledge search
- bundle-specific logical namespaces such as `ks:...`

Those are bundle contracts. The built-in React runtime only guarantees the core tools listed above.

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
  - ks:docs/sdk/memory/conversational-memory-search-README.md
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

- artifact-root-relative paths under the current turn
- `OUTPUT_DIR` is the artifact root; in local host storage that root is
  `out/workdir`
- `files/<scope>/...` for durable workspace/project state
- `outputs/<scope>/...` for reports, exports, test results, demos, and other produced artifacts
- unqualified `react.write` and exec contract paths default to `outputs/...`; use `files/...` explicitly for durable workspace/project state

Do not pass logical `fi:` paths to `react.write` or `react.patch`.
Do not pass absolute host paths, hosted `file://` paths, or `out/workdir`
prefixed paths. Tool params use the agent-visible relative path only.

## Built-in Tool Catalog

### `react.read`

Reads existing logical artifacts back into the visible timeline.

- Text artifact previews shown on the timeline are line-numbered. The numbers
  are model-facing viewing prefixes, not file bytes; use them for `react.read`
  ranges and patch locations, but omit them from patch/full-file content.
- input: `paths: list[str]`
- optional input: `items: list[object]` — exact read specs with `path` plus
  optional `line_start`/`line_count` or `offset_text_symbols`/`max_text_symbols`.
  `react.rg` returns ready-to-pass `read_item` entries for this field. Text-backed
  logical paths such as `fi:`, `tc:`, `ar:`, and `ks:` can also be read directly
  by line or symbol ranges through this field.
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
- `ev:` refs identify event objects on the timeline. Read them like `tc:` refs
  when the event block itself is needed. If an event points to payload bytes,
  use the event's `hosted_uri`, `payload.event_ref`, or artifact refs carried
  inside `payload.event`.
- cross-conversation `fi:` paths: if a path starts
  `fi:conv_<conversation_id>.turn_<id>...`, it belongs to another
  conversation. Current-conversation `fi:` paths do not have this segment; use
  scoped paths exactly as supplied.
- emits: one JSON status/result block plus one visible content block per reopened path
- deduplication: full visible blocks are not duplicated; ranged reads are
  emitted as distinct range blocks
- hidden data: hidden/pruned blocks can be reopened by exact path
- generated views: `ar:<turn_id>.react.turn.index` is reconstructed on demand from the persisted turn log
- large text guard: oversized text payloads are copied back only as bounded
  visible previews. The status row uses
  `status=truncated_for_visible_context`, the preview names the exact path, and
  the full payload remains recoverable by logical path and bounded range reads.
  If the caller supplies `max_text_symbols`, the preview is further clamped to
  that text-only limit.
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

Skills are not read-capped. `ks:` knowledge-space articles are uncapped only
when no `ai.react.knowledge_read_visible_*` cap is configured. If a `ks:` cap is
configured, or if a regular text-backed logical path (`fi:`, `tc:`, `ar:`) is
too large, call `react.read` with `stats_only:true` to get line/count metadata,
then recover the needed content through bounded `items` ranges:

```json
{"paths":["ks:docs/example.md"],"stats_only":true}
{"items":[{"path":"ks:docs/example.md","line_start":1,"line_count":120}]}
{"items":[{"path":"ks:docs/example.md","line_start":121,"line_count":120}]}
```

Do not use exec stdout as an uncapped read channel; exec output is capped too.
Use exec for computation or to create smaller derived artifacts, then inspect
those artifacts with `react.read`.

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

Ranged reads always materialize a new range block. They are not suppressed just
because the same logical path already has a full or preview block visible.

Example exact range read:

```json
{"items":[{"path":"fi:turn_abc.outputs/page.html","line_start":812,"line_count":60}]}
```

Example metadata-only read:

```json
{"paths":["tc:turn_abc.tc_big.result","fi:turn_abc.outputs/report.pdf"],"stats_only":true}
```

### `react.pull`

Materializes historical `fi:` refs and registered externally tracked artifact
refs locally so code or later tools can use them by local path.

- input: `paths: list[str]`
- accepted paths: `fi:` refs and registered external artifact namespaces such
  as `ext:...`
- cross-conversation refs: `fi:conv_<conversation_id>.turn_<id>...` belongs to
  another conversation and is resolved with that scope
- external artifact refs: `react.pull(paths=["ext:..."])` calls the registered
  namespace rehoster and returns the materialized `logical_path` /
  `physical_path`; use those returned paths next
- event refs: `ev:` identifies a readable timeline event object and is not
  accepted by `react.pull`; pull the event's `hosted_uri`,
  `payload.event_ref`, or refs inside `payload.event`
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
- rejected paths: `ext:...`, other external artifact namespaces, and `ev:...`
  are not checkout refs. Pull/rehost first, then checkout only if the returned
  `fi:` ref is a `files/...` workspace ref.
- cross-conversation refs: `fi:conv_<conversation_id>.turn_<id>.files/...`
  belongs to another conversation and is resolved with that scope
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
- accepted paths: current-turn artifact-root-relative paths; prefer concise paths such as `files/<scope>/file.py` or `outputs/<scope>/page.html`, not logical `fi:` paths
- patch format: unified diff or full replacement text
- generated unified-diff hunk counts are normalized before apply; a wrong `@@ -a,b +c,d @@` count should not force a full-file rewrite when the hunk content is otherwise correct
- rendered-preview line-number prefixes are rejected; remove the viewing prefixes and retry
- output: updated local file plus normal tool call/result blocks
- file origin does not matter: current-turn files produced by exec, `react.write`, `react.patch`, or `react.checkout` are patchable once they exist locally
- older files are never patched in place; materialize with `react.pull` if needed, then use `react.checkout` for historical `files/...` refs you need to edit and patch the resulting current-turn path

Use it when the file already exists and React wants a targeted edit instead of a full rewrite. Prefer unified diff for targeted changes; use full replacement only for intentional whole-file rewrites or when a targeted diff still cannot match the file. Do not use `react.write` just to register an existing file for patching.

### `react.memsearch`

Searches the **conversational memory index** — a distinct district of memory
managed by the agent as a byproduct of operation. Every user prompt, assistant
completion, working summary, internal note, and user attachment is persisted
into `conv_messages` and made retrievable here. It is separate from the
current visible timeline (which compaction prunes) and from durable user
memory (which is a curated user-visible product surface).

For the full data model, scope semantics, retrieval function, and
`Retrieval-anchors:` contract, see
[Conversational Memory Search](../../memory/conversational-memory-search-README.md).
The agent-facing surface is summarized below.

**What it searches.** Rows in `conv_messages` for the current user, optionally
across the user's other conversations under the same tenant/project/storage
boundary. Each row carries embedding (semantic), `search_tsv` (BM25F-style
lexical with anchors at weight A and body at weight B), timestamp, and tags.
The targets the agent selects determine which row families come back:

| Target | Picks rows tagged | Useful for |
| --- | --- | --- |
| `summary` | `kind:working.summary` | Goal/outcome/refs of a prior turn — best first target |
| `user` | `chat:user`, `artifact:user.attachment` | What the user said or attached |
| `assistant` | `chat:assistant` (non-summary) | What the agent answered |
| `attachment` | `artifact:user.attachment` and inline followup attachments | Find a turn by an attached file |
| `notes` | `kind:react.note` | Internal beacons left in prior turns |

**Scopes.**

- `scope="conversation"` (default): the current conversation only. Cannot leak across conversations.
- `scope="user"`: the same user across all of their conversations, inside the same tenant + project + storage boundary. Does not cross tenants or projects. Returned `fi:` paths shaped `fi:conv_<id>.turn_<id>...` indicate cross-conversation artifacts; pass them verbatim to `react.read` / `react.pull` / `react.checkout` / `react.rg` and they resolve against the other conversation's storage.

**Retrieval function.** When `query` is set, semantic and lexical retrievals run
in parallel and are fused by Reciprocal Rank Fusion (`k=60`) per target with a
multiplicative recency lift (`1 + 0.25 × recency`, half-life 7 days). A turn
does not have to appear in both lists — single-side matches still count, they
just contribute one RRF term. Catalog routing (no-query / ordinal / date-only
window) bypasses the hybrid path and uses the deterministic turn catalog.

Inputs:

- `query`: natural-language query. Set it for topic search (hybrid). Omit it for catalog routing.
- `targets`: list of snippet families to return. Defaults to `["assistant", "user", "attachment", "summary"]`. Use `"notes"` explicitly when looking for internal beacons.
- `scope`: `conversation` (default) or `user`.
- `ordinal`: 1-based turn number in the selected scope/window. Set this to look up the N-th turn.
- `from` / `to`: ISO timestamps. `to` is exclusive. Set without `query` to scan a date window; set with `query` to narrow hybrid search to that window.
- `order`: `asc` (default) or `desc` for catalog results.
- `top_k`: maximum turn hits (default 5).
- `days`: lookback limit (default 365 for topic queries, wider for catalog).

The tool infers routing from which fields are set; there is no `mode` knob.

Output per hit:

- `turn_id`
- `turn_index_path`, usually `ar:<turn_id>.react.turn.index`
- `working_summary_path`, usually `ws:<turn_id>.conv.working.summary`
- `ordinal` and `total_turns` when the hit came from the turn catalog
- `started_at` / `ended_at` when known
- `snippets`: compact rows with `role`, `path`, `ts`, optional `conversation_id` for cross-scope hits
- For hybrid hits: `score` (RRF + recency lift), `sim_score`, `recency_score`, plus telemetry fields `rrf_score`, `sem_rank`, `lex_rank`, `primary_source`

Event-scoped attachment paths use:

```text
fi:<turn>.external.<event_kind>.attachments/<event_id>/<filename>
```

Scenarios:

| User intent | Tool params | Next step |
|---|---|---|
| "What have we talked about so far?" | `targets=["summary"]`, `order="asc"`, high enough `top_k`, no `query`, no bounds | summarize returned turn summaries |
| "Find the brief on the Q2 spreadsheet" | `query`, `targets=["summary"]` | read returned `ws:` summary, then exact refs |
| "What was the second turn about?" | `ordinal=2`, `targets=["summary","user","assistant"]` | answer from snippets or read `turn_index_path` |
| "What did we discuss in March?" | `from`, `to`, `targets=["summary","user"]`, no `query` | scan returned turns, then read exact refs |
| "Find the openpyxl issue from March" | `query`, `from`, `to`, `targets=["summary","user","assistant"]` | hybrid search narrowed to the time window |
| "I need that old file but only remember the topic" | `query`, `targets=["summary","attachment"]` | read/pull returned `fi:` refs or read the turn index |
| "Find the renderer-ref decision I left for myself" | `query`, `targets=["notes","summary"]` | read the returned note snippet, then exact refs |

Examples:

```json
{"query": "openpyxl IndexError", "targets": ["summary"], "top_k": 5}
```

```json
{"ordinal": 2, "targets": ["summary", "user", "assistant"]}
```

```json
{"from": "2026-03-01T00:00:00Z", "to": "2026-04-01T00:00:00Z", "targets": ["summary", "user"]}
```

Rules:

- Do not use memsearch when the exact needed path is already visible; call `react.read` or `react.pull`.
- Prefer `targets=["summary"]` first for broad recovery because summaries carry goal/outcome/refs (and their `Retrieval-anchors:` block is what the lexical side ranks against).
- For broad overview questions, set no `query`, no `ordinal`, no bounds, and `targets=["summary"]`. Generic queries like `"conversation topics discussed"` are not useful — they should be omitted, not invented.
- For an exact turn position, set `ordinal` and omit `query`.
- For a date-only window, set `from`/`to` and omit `query`. For topic plus date range, set all three; the search narrows to the window.
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
- cross-conversation roots: if the root starts `fi:conv_<conversation_id>.turn_<id>...`, it belongs to another conversation and is resolved with that scope after the file has been pulled locally
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

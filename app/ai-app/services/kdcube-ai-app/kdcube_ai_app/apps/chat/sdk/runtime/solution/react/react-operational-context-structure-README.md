# React Operational Context Structure

This document describes the ReAct operational context as used by the SDK runtime (ReAct solver + codegen) and how it is persisted, addressed, and bound into tool calls.

## Overview

ReAct builds a per-turn, in-memory context (ReactContext) that is:
- Materialized from prior turns selected into context.
- Updated as new artifacts are produced in the current turn.
- Persisted to disk so tools and isolated code execution can reuse artifacts by path.

The context is the canonical source of truth for:
- Current turn artifacts and slots.
- Rehosted files/attachments from prior turns.
- A global sources pool (SIDs are stable across turns in the current session).
- The event timeline, tool call index, and content lineage for auditing.
- The budget snapshot exposed to the decision model.

Coordinator policy (scope + output contract + budget intent) seeds the budget caps shown in the journal and used by the decision agent.

## Filesystem Layout

Each ReAct run uses a temporary workspace with:
- `out/` for persisted context and produced artifacts.
- `work/` for execution scratch space.

Key files under `out/`:
- `context.json`: the session index (turn metadata, artifacts, slots, events, tool index, and a snapshot of sources pool).
- `sources_pool.json`: the canonical, heavy sources pool (stored separately; **fully duplicated** in `context.json`).
- Tool outputs: files created by tools or codegen (PDF/PPTX/PNG/XLSX/etc.).
- Auxiliary files (e.g., tool call records, delta caches) when tools emit them.

## Rehosting Previous Files and Attachments

Prior turn files and attachments are rehosted into the current `out/` directory so they are addressable by stable, OUT_DIR-relative paths.

- Files rehost to: `out/<turn_id>/files/<filename>`
- Attachments rehost to: `out/<turn_id>/attachments/<filename>`
- Current user attachments are also rehosted into `out/current_turn/attachments/<filename>` when present.

This is performed at the start of ReAct via ContextBrowser rehosting helpers.

## Sources Pool

### What it is

The sources pool is the canonical list of source records for the current turn.
Each record is a single source (text or multimodal) with a stable SID. The pool is used for citations and for slicing sources into tool inputs.

### Typical source record fields

- `sid` (int)
- `url` (str) or `rn` (resource name for hosted assets)
- `title` (str)
- `text` or `content` (str, for text sources)
- `mime` (str)
- `base64` (str, for multimodal sources)
- `size_bytes` (int)
- `source_type` (web | file | attachment | search)
- `local_path` (OUT_DIR-relative, when applicable)
- `artifact_path` (path back to the originating artifact)

### When entries are added

Sources are added to the pool when any of the following occur:
- Web search tools return results (each result becomes a source record).
- Fetch tools return content (each fetched URL becomes a source record).
- Rendering tools consume content that references local files; embedded file references are discovered and added.
- Hosted files and attachments are registered (e.g., produced artifacts or user attachments).

The pool is deduped by URL and SIDs are preserved across merges.

## Artifact Kinds and Producers

Artifacts are typed by kind:
- `inline`   — text/structured content stored inline in the artifact value.
- `file`     — file output with an authoritative text surrogate.
- `search`   — list of source records (web search or fetch output).

Producers:
- Codegen program output: files only.
- Decision-exec code: files only (exec always produces file artifacts, even for text).
- LLM gen tool: inline artifacts; file outputs are produced via write_* tools.
- write_* tools: file artifacts.

## Artifact Paths (Decision/Codegen View)

The journal advertises artifacts using stable paths. Common patterns:

- Current turn artifacts:
  - `current_turn.artifacts.<artifact_id>`
  - `current_turn.artifacts.<artifact_id>.value.<leaf>`

- Slots (deliverables):
  - `current_turn.slots.<slot_name>.<leaf>`
  - `<turn_id>.slots.<slot_name>.<leaf>`

- Messages:
  - `<turn_id>.user.prompt.text`
  - `<turn_id>.assistant.completion.text`

- Attachments:
  - `current_turn.user.attachments.<name>`
  - `<turn_id>.user.attachments.<name>`

- Files:
  - `current_turn.files.<filename>`
  - `<turn_id>.files.<filename>`

- Sources pool slice:
  - `sources_pool[1,2,3]`

- Search/fetch artifacts must be sliced:
  - `current_turn.artifacts.<search_id>[1,3]` or `current_turn.artifacts.<search_id>[2:6]`

## Param Binding and Relaxations

Binding is performed through `fetch_context` and merged into tool params. Key behaviors:

- Text params: multiple bindings are concatenated with two newlines.
- `sources_list`: lists from multiple bindings are merged, deduped by URL, and normalized.
- Citation-aware tools automatically receive sources from any bound artifacts that carry `sources_used`.
- For LLM generation tools, sources are capped and balanced to fit context limits.
- For write_* tools, if a content binding points to an LLM envelope, the runtime rewrites it to `.value.content`.
- `sources_pool[...]` and sliced search/fetch artifacts are valid sources_list inputs; unsliced search artifacts are skipped.

## Slot Mapping and Relaxations

Slot mapping supports:
- Mapping from a leaf (`.value.content`, `.text`, etc.) or from the artifact object itself (if the leaf is implied).
- Normalization of `current_turn.files.<filename>` to the matching artifact ID when possible.
- Draft file mapping from inline text when a rendered file is missing (wrap-up support). Draft mappings carry `gaps`.

## Multimodal Artifacts

Multimodal sources (images/PDFs) are represented in the sources pool with `mime` + `base64` (when available) and optional `size_bytes`.
These sources can be bound through `sources_list` to tools that support multimodal inputs.

User attachments and show_artifacts may also contribute multimodal blocks to the prompt context.

## Show Artifacts

`show_artifacts` selects specific artifacts to display in the agent journal. The runtime:
- Materializes each artifact into a compact, readable form.
- Extracts up to two multimodal attachments across all shown artifacts (deduped by MIME type) to keep context small.

## Notes on isolation modes

ReAct may execute tools in-process or via isolated runtime (local subprocess or Docker sandbox). The on-disk context layout is the same across modes; only the execution boundary changes.

## Lineage Between Artifacts

Artifacts record `content_lineage`, which is derived from the paths used in `fetch_context`.
This lineage is used to trace provenance and, when mapping file slots, to resolve the best summary if multiple inputs contributed.

## Sources provenance in generated artifacts

Artifacts and slots can carry `sources_used` to show which sources were actually referenced:

- LLM generation tools emit `sources_used` when `sources_list` is bound.
- When artifacts are mapped into slots, `sources_used` is propagated into the slot.
- The sources pool maintains stable SIDs and is deduped by URL, so citations remain consistent across merges.

Where to look:
- Tool artifacts: `current_turn.artifacts.<id>.value.sources_used` (or `sources_used` at the root for legacy artifacts).
- Slots: `current_turn.slots.<slot>.sources_used` / `<turn_id>.slots.<slot>.sources_used`.

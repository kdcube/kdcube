---
id: ks:docs/sdk/agents/react/react-tools-README.md
title: "React Tools"
summary: "React‑only tools catalog injected into the decision runtime."
tags: ["sdk", "agents", "react", "tools"]
keywords: ["react.read", "react.pull", "react.write", "react.search_files", "react.memsearch", "react.search_knowledge"]
see_also:
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/event-blocks-README.md
  - ks:docs/sdk/agents/react/react-round-README.md
---
# React Tools (react.*)

This document describes the react-only tools injected into the decision tool catalog. These tools
are not available in external execution environments. All effects are reflected in the timeline
via react.tool.call / react.tool.result blocks.

Common behaviors
- All react tools are invoked as a call_tool action.
- If decision `notes` are present, a `react.notes` block is emitted **before** the tool call.
- All tool calls append a react.tool.call block (params only) and one or more react.tool.result blocks.
- Tool result blocks are **rendered views** (status/errors + artifact metadata; inline output only for non‑file tools).
  File artifacts are referenced by `artifact_path`; use `react.read(...)` to load content.
- Artifacts created by tools are referenced by stable paths (use turn_id, never current_turn):
  - fi:<turn_id>.files/<relative_path>
  - ar:<turn_id>.artifacts.<artifact_path>
  - tc:<turn_id>.<id>.call / .result
  - ks:<relpath> (knowledge space; read-only)

Data spaces (quick guide)
- Knowledge Space (`ks:`): read-only reference files prepared by the system (docs, indexes, repos).
- OUT_DIR (`fi:`): per‑turn output artifacts (read/write during the turn).
- Versioned snapshot refs (`fi:<turn_id>...`): historical artifacts and workspace slices.
- Conversation Workspace (future): shared writable workspace across turns (not implemented yet).

Accepted path families (built-in react tools)
- `react.read` accepts logical paths, not raw host/runtime paths:
  - `fi:...`
  - `ar:...`
  - `so:...`
  - `su:...`
  - `tc:...`
  - bundle-provided logical namespaces such as `ks:...` when the active bundle supports them
- `react.pull` accepts logical `fi:` paths only:
  - `fi:<turn_id>.files/<path-or-subtree>` for versioned files/workspace slices
  - `fi:<turn_id>.user.attachments/<file>` or legacy `fi:<turn_id>.attachments/<file>` for exact attachment/binary pulls
- `react.file`, `react.patch`, and `react.stream` accept OUT_DIR-relative file targets under the current turn's `files/` area. They do not accept logical `fi:` paths.
- `react.search_files` accepts only rooted search prefixes:
  - `outdir`
  - `outdir/<subdir>`
  - `workdir`
  - `workdir/<subdir>`
- `react.hide` accepts logical block paths such as `ar:`, `fi:`, `tc:`, `so:`, and bundle-provided logical namespaces such as `ks:`.
- Built-in `react.*` tools do not accept exec-runtime `physical_path` values returned by bundle namespace resolvers.
- Bundles may extend the React tool surface with additional tools and additional logical path families, but those are bundle-specific contracts.

react.read
- Purpose: load an existing artifact into the timeline for inspection.
- Accepts logical paths only. Do not pass exec-runtime resolver `physical_path` values here.
- Effect:
  - Emits a **status block first** (at `tc:<turn>.<call>.result`) with `paths`, `missing`,
    and `exists_in_visible_context` (dedup).
  - Emits artifact blocks **after** the status block.
- Visibility: re‑exposes hidden artifacts; output blocks always clear `hidden`.
- Dedup: if the reconstructed block already exists in visible context (same path + hash),
  it is **not** re‑emitted and the status block reports `exists_in_visible_context`.
Params:
- paths: list[str] (FIRST FIELD). One or more artifact/context paths to load.
Behavior by path:
- `fi:` → rehost file locally, emit metadata digest block + file content (text or base64 if pdf/image;
  binary files emit metadata only).
  Supported `fi:` forms:
  - `fi:<turn_id>.files/<relpath>`
  - `fi:<turn_id>.user.attachments/<relpath>`
  - `fi:<outdir-relative-path>` for any readable file already present inside OUT_DIR, for example `fi:logs/docker.err.log`
- `ks:` → read from knowledge space (read‑only reference files).
- `so:sources_pool[...]` → if rows contain file/attachment sources, they are resolved as `fi:`; other rows
  are rendered as sources_pool text.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "ar:turn_123.artifacts.notes", "mime": "text/markdown", "text": "..." }
```

react.pull
- Purpose: materialize selected `fi:` snapshot refs locally under OUT_DIR so code/execution can use them by physical path.
- Use this when `fi:` data must exist as a local file, not just as visible timeline content.
- Path contract:
  - `fi:<turn_id>.files/<path>` may be an exact file or a subtree/prefix.
  - `fi:<turn_id>.user.attachments/<file>` and legacy `fi:<turn_id>.attachments/<file>` must be exact file refs.
- Binary rule:
  - folder pulls do not imply hosted binaries/attachments under that subtree
  - if you need a binary file, name that exact `fi:` file in `paths`
- Result:
  - `pulled[]` rows contain `logical_path`, `physical_path`, and `kind`
  - those `physical_path` values are OUT_DIR-relative local paths you can use in exec code
Example result (simplified):
```json
{
  "type": "react.tool.result",
  "path": "tc:turn_123.abc.result",
  "mime": "application/json",
  "text": "{ \"pulled\": [{\"logical_path\": \"fi:turn_120.files/projectA/src/app.py\", \"physical_path\": \"turn_120/files/projectA/src/app.py\", \"kind\": \"files\"}] }"
}
```

react.stream
- Purpose: generate or stream content for a new artifact.
- Effect: emits tool.result blocks with streamed content and metadata.
- If kind='display', content is shown to the user (external). If kind='file', it is recorded as a file.
- Path contract: OUT_DIR-relative path under the current turn's `files/` area, not a logical `fi:` path.
Params:
- path: str (FIRST FIELD). Artifact path under <turn_id>/files/.
- channel: str (SECOND FIELD). 'canvas' or 'timeline_text'.
- content: str (THIRD FIELD). Streamed content.
- kind: str (FOURTH FIELD). 'display' or 'file'.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/report.md", "mime": "text/markdown", "text": "..." }
```

react.file
- Purpose: write content to a file path (relative under <turn_id>/files/).
- Effect: records a file artifact and emits tool.result metadata; content is stored on disk.
- Path contract: OUT_DIR-relative path under the current turn's `files/` area, not a logical `fi:` path.
Params:
- path: str (FIRST FIELD). File path under <turn_id>/files/.
- content: str (SECOND FIELD). File content.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/data.json", "mime": "application/json", "text": "..." }
```

react.patch
- Purpose: patch an existing file and stream the patch.
- Path contract: OUT_DIR-relative path under the current turn's `files/` area, not a logical `fi:` path and not an exec-runtime resolver `physical_path`.
- Patch format:
  - If patch starts with ---/+++/@@, treat as unified diff.
  - Otherwise replace the entire file with the patch text.
- Effect: emits tool.result with patch text; optionally marks the file external if kind='file'.
Params:
- path: str (FIRST FIELD). File path under <turn_id>/files/.
- channel: str (SECOND FIELD). 'canvas' or 'timeline_text'.
- patch: str (THIRD FIELD). Unified diff or full replacement text.
- kind: str (FOURTH FIELD). 'display' or 'file'.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "fi:turn_123.files/report.md", "mime": "text/markdown", "text": "<patch>..." }
```

react.memsearch
- Purpose: semantic search in past turns and surface relevant snippets into context.
- Effect: emits a tool.result block with compact snippets and metadata (turn_id, timestamps, scores).
- Use when you suspect missing details exist in prior turns but are not visible.
Params:
- query: str (FIRST FIELD). Search query.
- targets: list[str] (SECOND FIELD). assistant|user|attachment.
- top_k: int (optional). Default 5.
- days: int (optional). Default 365.

Example result block (simplified):
```json
{
  "type": "react.tool.result",
  "call_id": "<tool_call_id>",
  "mime": "application/json",
  "path": "tc:<turn_id>.<id>.result",
  "text": "[{'turn_id': 'turn_123', 'text': '...', 'score': 0.84, 'ts': '2026-02-01T...Z'}]"
}
```

react.search_knowledge (bundle‑provided)
- Purpose: search knowledge space (read‑only reference materials).
- Availability: only when the active bundle registers this tool (e.g., `react.doc`).
- Use when you need docs/KB, not conversation history.
- This is a logical bundle tool surface, separate from exec-only namespace resolver tools.
Params:
- query: str (FIRST FIELD). Search query.
- root: str (optional). `ks:<relpath>` or namespace like `kb` (treated as `ks:kb`).
- keywords: list[str] (optional). Extra tags/keywords to bias ranking.
- top_k: int (optional). Default 20.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "tc:turn_123.abc.result", "mime": "application/json", "text": "{ \"hits\": [ {\"path\": \"ks:docs/intro.md\"} ] }" }
```

react.hide
- Purpose: replace a large snippet in the visible timeline with a short placeholder.
- Use only when the snippet is near the tail and clearly no longer needed.
- The original content remains retrievable via react.read(path).
- Enforced tail window: only paths within `RuntimeCtx.cache.editable_tail_size_in_tokens` from the static tail are allowed.
- Uses a logical path (ar: fi: tc: so: ks:), not a search query.
- Does not accept exec-runtime resolver `physical_path` values.
Params:
- path: str (FIRST FIELD). Block path to hide.
- replacement: str (SECOND FIELD). Replacement text.
Example result (simplified):
```json
{ "type": "react.tool.result", "path": "tc:turn_123.abc.result", "mime": "application/json", "text": "{...}" }
```

react.search_files
- Purpose: safely search files under OUT_DIR or workdir without shell execution.
- Returns: `{root, hits}`. Each hit contains:
  - `path`: relative to the searched root and does not include that root prefix
  - `size_bytes`
  - `logical_path` for OUT_DIR hits, suitable for `react.read`
Params:
- root: str (optional). `outdir` or `outdir/<subdir>`; `workdir` or `workdir/<subdir>`; default is `outdir`.
- name_regex: str (optional). Regex for filenames.
- content_regex: str (optional). Regex for file content.
- max_hits: int (optional). Default 200.
- Notes:
  - `react.search_files` does not load file content.
  - For OUT_DIR hits, call `react.read` on the returned `logical_path`.
  - workdir hits are discovery-only with the current toolset.
  - Do not pass exec-runtime bundle resolver `physical_path` values as `root`.
Example result (simplified):
```json
{
  "type": "react.tool.result",
  "path": "tc:turn_123.abc.result",
  "mime": "application/json",
  "text": "{ \"root\": \"outdir/logs\", \"hits\": [{\"path\": \"docker.err.log\", \"size_bytes\": 1234, \"logical_path\": \"fi:logs/docker.err.log\"}] }"
}
```

Notes
- react.* tools are only valid inside the react loop.
- The React agent must order params exactly as specified in each tool signature.
- If generated exec code uses a bundle-defined namespace resolver and gets back
  `{physical_path, access, browseable}`:
  - `physical_path` is valid only inside that exec runtime and only with the returned access mode
  - use the resolver input logical_ref itself as the logical base if code wants the agent to continue later with `react.read(...)`

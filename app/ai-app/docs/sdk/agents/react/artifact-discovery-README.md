---
id: ks:docs/sdk/agents/react/artifact-discovery-README.md
title: "Artifact Discovery"
summary: "How artifacts are discovered from timeline blocks and how logical/physical paths resolve for tools."
tags: ["sdk", "agents", "react", "artifacts", "paths"]
keywords: ["logical paths", "physical paths", "react.read", "attachments", "artifact resolution", "timeline blocks"]
see_also:
  - ks:docs/sdk/agents/react/artifact-storage-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/files-vs-outputs-README.md
  - ks:docs/sdk/agents/react/memory-recovery-path-README.md
---
# Artifact Discovery (Logical/Physical Paths)

This document defines how artifacts are discovered from timeline blocks and how logical/physical paths
are resolved for tools (`react.read`, `fetch_ctx`, `react.patch`, exec code).

Important distinction:
- this document is about artifact discovery and artifact-root-relative paths
- it is not the contract for bundle namespace resolution inside isolated exec
- it describes the current artifact path model, including the phase-1 `files/...` vs `outputs/...` namespace split

For bundle namespace browsing such as `ks:` via generated exec code, the relevant model is:
- a bundle may expose an exec-only namespace resolver tool
- that tool returns an exec-visible path contract for code
- this is separate from timeline artifact discovery

## Key Concepts

**Logical path**  
Stable identifier used in `react.read` / `fetch_ctx`. Examples:
- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `ar:<turn_id>.assistant.completion.<n>`
- `ar:plan.latest:<plan_id>` (stable latest snapshot of a plan lineage)
- `fi:<turn_id>.files/<relpath>`
- `fi:<turn_id>.outputs/<relpath>`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:logs/docker.err.log`
- `so:sources_pool[...]`
- `su:<turn_id>.conv.range.summary`
- `tc:<turn_id>.<call_id>.result`

**Physical path**  
Artifact-root-relative path used for `react.patch`, rendering tools, and exec code file I/O.
Common forms:
- `turn_<id>/files/<relpath>` (files; current or historical)
- `turn_<id>/outputs/<relpath>` (non-workspace produced artifacts)
- `turn_<id>/attachments/<name>` (attachments)

This `physical_path` means:
- artifact location relative to the agent artifact root
- in local runtime storage, the actual host path is under `out/workdir/<physical_path>`
- in Docker/Fargate, `OUTPUT_DIR` points directly at the equivalent artifact root

It does **not** mean:
- an absolute host path
- a hosted `file://` or S3 path
- a path relative to the runtime metadata root `out/`
- bundle namespace resolution for readonly bundle data inside isolated exec

That other case uses a different contract:
- bundle-defined exec-only resolver
- returns `ret = {physical_path: str | null, access: 'r' | 'rw', browseable: bool}`
- the returned `physical_path` is valid only inside the current isolated execution runtime
- it must not be reused by the agent outside exec, copied into normal react tool calls, or treated as a stable artifact path
- generated code may use it only according to `access`:
  - `access='r'` means read-only
  - `access='rw'` means the code may read and write there
- if generated code wants the agent to continue later with `react.read(...)`, it should use the resolver input logical_ref as the logical base and emit derived logical refs itself

**Block metadata**  
Artifacts are described by a **metadata JSON result block** plus one or more **content blocks**:
- **Metadata block** is a `react.tool.result` JSON block whose **text** is a **safe digest** of artifact
  metadata.
  - `artifact_path` (logical path)
  - `physical_path` (artifact-root-relative `turn_...` path, when applicable)
  - `tool_call_id`
  - `mime`, `kind`, `visibility`, `channel` (when applicable)
  - hosted blob handles (`hosted_uri`, `key`, `rn`) when file bytes were hosted
  - `sources_used` (if known)
  - `edited` (boolean if a prior version exists)
- **Content block(s)** carry the same logical path in `path` and include the payload:
  - `text` for textual artifacts
  - `base64` for binary artifacts (image/pdf)
  - `meta.tool_call_id` is present.
- **File blocks** also include **hosting metadata** in `meta` (`hosted_uri`, `rn`, `key`, `physical_path`)
    and the safe digest in `meta.digest`.

Text content blocks may be previews. A preview block describes the artifact for
the model; it is not authoritative storage. File materialization must use the
hosted blob handles when present.

For **user attachments**, `user.attachment.meta` stores the safe digest in `text`, while the
attachment file block stores hosting metadata + `meta.digest`.

## Discovery Rules

Artifacts are reconstructed from **timeline blocks**, not from the turn log directly.

1) **Find by logical path**
   - Match blocks by `path == logical_path`.
   - `ar:<turn_id>.assistant.completion` resolves to the latest completion in that turn.
   - `ar:<turn_id>.assistant.completion.<n>` resolves to an earlier visible completion from that same turn.
   - If a metadata JSON block exists with `artifact_path == logical_path`, it supplies the canonical
     `mime/kind/visibility/physical_path` metadata.

2) **Group by tool call**
   - Blocks for the same artifact in one tool call share `meta.tool_call_id`.
   - `tool_id` is derived via the call map (`tool_call_id → tool_id`) from the tool call block.
   - Grouping key: `(tool_call_id, artifact_path)`.

3) **Edits**
   - If a new block arrives for an existing `artifact_path`, it is marked `edited=true`.
   - The timeline keeps all versions; discovery resolves to the latest.

4) **Hidden blocks**
   - Hidden is stored in `meta.hidden`; replacement text in `meta.replacement_text`.
   - Timeline renderers replace hidden groups with a single replacement block.
   - Discovery still resolves to the latest version, hidden or not.

## Path Normalization & Current-Turn Paths

Writer-like tools normalize current-turn paths into the active turn namespace. Use `files/...` for durable workspace/project state and `outputs/...` for produced artifacts. Unqualified `react.write` and exec contract paths default to `outputs/...`.

Example:
```
input path: outputs/report.csv
current turn: turn_999
rewrite -> turn_999/outputs/report.csv (physical), fi:turn_999.outputs/report.csv (logical)
```

The rewrite is recorded as a **protocol notice** in the timeline so the agent can learn.
`react.patch` is stricter: it accepts only existing current-turn `files/...` or `outputs/...` physical paths. It does not rewrite logical `fi:` paths or historical `turn_old/...` paths.

## Tool Behaviors (Summary)

**react.read / fetch_ctx**
- Accept logical path (fi:/ar:/so:/su:/tc:).
- Resolve artifact by logical path; return canonical artifact payload.
- For plan-related `ar:` paths, the recovery handle to use is the stable latest-snapshot alias: `ar:plan.latest:<plan_id>`.
- For `fi:` paths, `react.read` **rehosts** the file into the artifact root and reconstructs the
  metadata block from `meta.digest` (if present). It then emits:
  - metadata digest block (text only)
  - file content block (text or base64) when readable; binary files emit **metadata only**
- If an `fi:` path starts `fi:conv_<conversation_id>.turn_<id>...`, the
  `conv_` segment is the conversation scope. The artifact belongs to another
  conversation and consumers must preserve that segment.
- `react.read` also accepts `fi:<artifact-root-relative-path>` for readable
  files already present in the artifact root. New artifact reads should use
  `turn_...` paths or canonical `fi:<turn_id>...` logical paths. Runtime logs
  and metadata are platform diagnostics, not normal agent artifacts.

**react.patch**
- Accepts current-turn physical path (artifact-root-relative), usually `files/<scope>/...`, `outputs/<scope>/...`, or their canonical `turn_<id>/...` form.
- Patches any existing current-turn materialized text file, including files produced by exec; it is not limited to files created by `react.write`.
- Historical (`turn_X/files/...`) files are never patched directly. Pull first if needed, then checkout/copy the pulled file into the current turn and patch the current-turn path.

**rendering_tools.write_*** / **react.write**
- Use physical path; timeline stores logical path in `meta`.
- For `kind=file`, file bytes are hosted and metadata (`hosted_uri`, `key`, `rn`) is stored in metadata blocks.
  - `visibility=external` files are also emitted to the UI.
  - `visibility=internal` files are hosted for later agent/runtime use but are not emitted to the UI.
- For `kind=display`, content is emitted and stored as text block.

**react.rg**
- Searches files already materialized under the artifact root and returns discovery metadata plus optional line-numbered content matches.
- It does not search hidden/pruned timeline, unpulled historical snapshots, or `ks:`. Use visible refs or `react.memsearch` to identify older `fi:` refs, then `react.pull` them before local search. Checkout only when you need an editable current-turn copy.
- `fi:conv_<conversation_id>.turn_<id>...` roots are cross-conversation refs and
  are resolved with that conversation scope after materialization.
- Result shape is `{root, hits}`.
- Each hit contains:
  - `path`: relative to the searched root
  - `size_bytes`
  - `text_symbols` and `line_count` for recognizable text files
  - `logical_path`, suitable for `react.read`
- Content matches also include `read_item` ranges; pass them to `react.read({"items":[...]})` to inspect exact regions.
- This is the bridge from filesystem discovery to content loading.

**react.pull**
- Accepts `fi:` refs only.
- `fi:conv_<conversation_id>.turn_<id>...` paths are cross-conversation refs and
  are resolved with that conversation scope.
- For `fi:<turn>.files/<prefix>` folder pulls, the current implementation does **not** scan all hosted storage.
- In `workspace_implementation=custom`, it inspects artifact metadata for the referenced turn from timeline/turn-log state, expands the matching descendants, and fetches only the exact matched hosted blobs.
- In `workspace_implementation=git`, `fi:<turn>.files/...` resolves against the git-backed lineage snapshot for that version instead of scanning artifact history.
- Folder pulls currently imply:
  - descendants are discovered from metadata, not by listing object storage
  - file bytes come from hosted blob handles, not from text previews
  - no execution workspace archive extraction
- User attachments are allowed only by exact logical ref, for example:
  - `fi:<turn>.user.attachments/template.xlsx`
- Exact produced artifacts are also allowed:
  - `fi:<turn>.outputs/analysis/zip_contents.json`
- Future design note:
  - `fi:<turn>.outputs/...` is the explicit non-workspace artifact retrieval namespace
  - unlike `fi:<turn>.files/...`, it does not participate in workspace history semantics

**bundle_data.resolve_namespace** (bundle-defined, exec-only)
- Not a general artifact-discovery tool.
- Not driven by timeline blocks.
- Intended only for generated code inside `execute_code_python(...)`.
- Resolves a logical bundle namespace/path to:
  - `physical_path: str | null`
  - `access: 'r' | 'rw'`
  - `browseable: bool`
- Use the resolver input logical_ref itself as the logical base if generated code wants the agent to follow up later with `react.read(...)`.
- Example:
  - set `logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk"` and resolve it
  - code inspects files under the returned `physical_path`
  - if code finds `runtime/execution.py`, it should emit logical ref `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/execution.py` in an `OUTPUT_DIR` file or short `user.log` note
- The returned `physical_path` is an exec-runtime path only.
- It is not an artifact `physical_path`, not artifact-root-relative, and not a valid input to normal react tools.
- Generated code must respect `access` exactly; for example, `access='r'` means browse/read only.
- The generated code must then decide what to do and propagate any useful result back through:
  - files written under `OUTPUT_DIR`
  - and/or short `user.log` output

## Compaction Notes

Compaction summarizes **blocks**, not artifacts. Artifact metadata must remain visible:
- Compaction serializer includes the **metadata JSON block** (`artifact_path`, `physical_path`, `mime`,
  `tool_call_id`, etc.).
- This preserves discovery even if older content blocks are summarized.

## Artifact Mentions Cache Misses → Pull

When rendering tools (e.g., `rendering_tools.write_pdf`, `write_pptx`, `write_png`) receive
HTML/Markdown content, that content may reference **local artifacts** that are not currently
present in the artifact root. We treat these as **cache misses** and pull
the required assets before rendering.

### How it works

1) **Local path mentions**
   - The content is scanned for local paths (`turn_<id>/files/...`, `turn_<id>/outputs/...`, and `turn_<id>/attachments/...`).
   - For each referenced path, the runtime rehosts that file into the artifact root.
   - If any are missing, a tool notice is emitted (`tool_call_error.missing_assets`).

2) **SID mentions**
   - The content is scanned for citation tokens (`[[S:n]]`).
   - Each SID is resolved against `sources_pool`.
   - If a SID maps to a file/attachment source (via `physical_path` or `artifact_path`),
     that file is rehosted into the artifact root.
   - If a SID is missing in sources_pool, a warning notice is emitted
     (`tool_call_warning.missing_sources`).

### Required sources_pool metadata

To enable SID→file resolution, file/attachment sources MUST carry:
- `physical_path` (e.g., `turn_123/attachments/photo.png`)
- `artifact_path` (e.g., `fi:turn_123.user.attachments/photo.png`)
- `source_type` = `attachment` or `file`
- `mime` (used for read/render and announce)

These fields are populated automatically for:
- **User attachments** at turn start (source_type=`attachment`)
- **Produced files** (exec/render/write tools) when hosted (source_type=`file`)

### Why this matters

Rendering tools run in isolated workspaces and only see the artifact root through **OUTPUT_DIR**. Rehosting ensures:
- `<img src="turn_123/attachments/x.png">` renders correctly
- `[[S:n]]` that references a file source becomes renderable
- Tool outputs are reproducible even when prior turns are compacted

## Examples

### Namespace resolver result inside exec
```python
{
  "physical_path": "/exec-visible/resolved/src",
  "access": "r",
  "browseable": True,
}
```

This is intentionally different from artifact metadata `physical_path` like:
- `turn_abc/files/report.pdf`
- `turn_abc/outputs/analysis/report.json`
- `turn_abc/attachments/photo.png`

### File artifact (exec output)
```
meta = {
  artifact_path: "fi:turn_abc.outputs/analysis/report.json",
  physical_path: "turn_abc/outputs/analysis/report.json",
  mime: "application/json",
  kind: "file",
  visibility: "internal",
  tool_call_id: "c1a2",
  hosted_uri: "s3://...",
  key: "cb/tenants/.../attachments/.../turn_abc/outputs/analysis/report.json",
  rn: "ef:..."
}
```

### Display artifact (react.write, kind=display)
```
meta = {
  artifact_path: "fi:turn_abc.files/summary.md",
  physical_path: "turn_abc/files/summary.md",
  kind: "display",
  tool_call_id: "c1a2",
  sources_used: [1,2]
}
```

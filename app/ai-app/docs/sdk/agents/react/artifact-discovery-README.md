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
---
# Artifact Discovery (Logical/Physical Paths)

This document defines how artifacts are discovered from timeline blocks and how logical/physical paths
are resolved for tools (`react.read`, `fetch_ctx`, `react.patch`, exec code).

Important distinction:
- this document is about artifact discovery and OUT_DIR-relative artifact paths
- it is not the contract for bundle namespace resolution inside isolated exec

For bundle namespace browsing such as `ks:` via generated exec code, the relevant model is:
- a bundle may expose an exec-only namespace resolver tool
- that tool returns an exec-visible path contract for code
- this is separate from timeline artifact discovery

## Key Concepts

**Logical path**  
Stable identifier used in `react.read` / `fetch_ctx`. Examples:
- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `ar:plan.latest:<plan_id>` (stable latest snapshot of a plan lineage)
- `fi:<turn_id>.files/<relpath>`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:logs/docker.err.log`
- `so:sources_pool[...]`
- `su:<turn_id>.conv.range.summary`
- `tc:<turn_id>.<call_id>.result`

**Physical path**  
OUT_DIR‑relative path used for `react.patch`, rendering tools, and exec code file I/O.
Common forms:
- `<turn_id>/files/<relpath>` (files; current or historical)
- `<turn_id>/attachments/<name>` (attachments)
- `logs/<name>` and other runtime-managed files already present in OUT_DIR

This `physical_path` means:
- OUT_DIR-relative artifact location for normal artifact/tool workflows

It does **not** mean:
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
  metadata (no hosted_uri/rn/key/physical_path).
  - `artifact_path` (logical path)
  - `physical_path` (OUT_DIR‑relative, when applicable)
  - `tool_call_id`
  - `mime`, `kind`, `visibility`, `channel` (when applicable)
  - `sources_used` (if known)
  - `edited` (boolean if a prior version exists)
- **Content block(s)** carry the same logical path in `path` and include the payload:
  - `text` for textual artifacts
  - `base64` for binary artifacts (image/pdf)
  - `meta.tool_call_id` is present.
- **File blocks** also include **hosting metadata** in `meta` (`hosted_uri`, `rn`, `key`, `physical_path`)
    and the safe digest in `meta.digest`.

For **user attachments**, `user.attachment.meta` stores the safe digest in `text`, while the
attachment file block stores hosting metadata + `meta.digest`.

## Discovery Rules

Artifacts are reconstructed from **timeline blocks**, not from the turn log directly.

1) **Find by logical path**
   - Match blocks by `path == logical_path`.
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

## Path Normalization & Rewrite

If a tool is given a path in another turn’s namespace, it is rewritten to current turn:

Example:
```
input path: turn_123/files/output.csv
current turn: turn_999
rewrite -> output.csv (physical), fi:turn_999.files/output.csv (logical)
```

The rewrite is recorded as a **protocol notice** in the timeline so the agent can learn.

## Tool Behaviors (Summary)

**react.read / fetch_ctx**
- Accept logical path (fi:/ar:/so:/su:/tc:).
- Resolve artifact by logical path; return canonical artifact payload.
- For plan-related `ar:` paths, the recovery handle to use is the stable latest-snapshot alias: `ar:plan.latest:<plan_id>`.
- For `fi:` paths, `react.read` **rehosts** the file into OUT_DIR and reconstructs the
  metadata block from `meta.digest` (if present). It then emits:
  - metadata digest block (text only)
  - file content block (text or base64) when readable; binary files emit **metadata only**
- `react.read` also accepts `fi:<outdir-relative-path>` for any readable file already present in OUT_DIR.
  Example: `fi:logs/docker.err.log`

**react.patch**
- Accepts physical path (OUT_DIR‑relative).
- If path is historical (`turn_X/files/...`), a copy is created in current turn before patching.

**rendering_tools.write_*** / **react.write**
- Use physical path; timeline stores logical path in `meta`.
- For `kind=file`, file is hosted and metadata (hosted_uri/rn/key) stored in `meta`.
  - `visibility=internal` files are **not hosted** (stored only in OUT_DIR + timeline).
- For `kind=display`, content is emitted and stored as text block.

**react.search_files**
- Searches under `outdir` or `workdir` and returns discovery metadata only.
- Result shape is `{root, hits}`.
- Each hit contains:
  - `path`: relative to the searched root
  - `size_bytes`
  - `logical_path` for OUT_DIR hits, suitable for `react.read`
- This is the bridge from filesystem discovery to content loading.

**react.pull**
- Accepts `fi:` refs only.
- For `fi:<turn>.files/<prefix>` folder pulls, the current implementation does **not** scan all hosted storage.
- It inspects artifact metadata for the referenced turn from timeline/turn-log state, expands the matching descendants, and fetches only the exact matched blobs.
- Folder pulls currently imply:
  - textual/tree content backed by turn artifacts
  - no implicit hosted-binary descendants
- Hosted binaries are allowed only by exact logical ref, for example:
  - `fi:<turn>.user.attachments/template.xlsx`

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
- It is not an artifact `physical_path`, not an OUT_DIR-relative path, and not a valid input to normal react tools.
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
present in the execution workspace (OUT_DIR). We treat these as **cache misses** and pull
the required assets before rendering.

### How it works

1) **Local path mentions**
   - The content is scanned for local paths (`turn_<id>/files/...` and `turn_<id>/attachments/...`).
   - For each referenced path, the runtime rehosts that file into OUT_DIR.
   - If any are missing, a tool notice is emitted (`tool_call_error.missing_assets`).

2) **SID mentions**
   - The content is scanned for citation tokens (`[[S:n]]`).
   - Each SID is resolved against `sources_pool`.
   - If a SID maps to a file/attachment source (via `physical_path` or `artifact_path`),
     that file is rehosted into OUT_DIR.
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

Rendering tools run in isolated workspaces and only see **OUT_DIR**. Rehosting ensures:
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
- `turn_abc/attachments/photo.png`

### File artifact (exec output)
```
meta = {
  artifact_path: "fi:turn_abc.files/report.pdf",
  physical_path: "turn_abc/files/report.pdf",
  mime: "application/pdf",
  kind: "file",
  tool_call_id: "c1a2",
  hosted_uri: "s3://...",
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

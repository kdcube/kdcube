# Artifact Discovery (Logical/Physical Paths)

This document defines how artifacts are discovered from timeline blocks and how logical/physical paths
are resolved for tools (`react.read`, `fetch_ctx`, `react.patch`, exec code).

## Key Concepts

**Logical path**  
Stable identifier used in `react.read` / `fetch_ctx`. Examples:
- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `fi:<turn_id>.files/<relpath>`
- `fi:<turn_id>.user.attachments/<name>`
- `so:sources_pool[...]`
- `su:<turn_id>.conv.range.summary`
- `tc:<turn_id>.<call_id>.result`

**Physical path**  
OUT_DIR‑relative path used for `react.patch`, rendering tools, and exec code file I/O.
Always normalized to:
- `<turn_id>/files/<relpath>` (files; current or historical)
- `<turn_id>/attachments/<name>` (attachments)

**Block metadata**  
All artifact‑bearing blocks include a `meta` object with at least:
- `artifact_path` (logical path)
- `physical_path` (OUT_DIR‑relative, when applicable)
- `tool_call_id`
- `mime`, `kind`, `visibility`, `channel` (when applicable)
- `sources_used` (if known)
- `edited` (boolean if a prior version exists)

## Discovery Rules

Artifacts are reconstructed from **timeline blocks**, not from the turn log directly.

1) **Find by logical path**
   - Group blocks by `meta.artifact_path`.
   - Within a group, the **latest** block (by timeline order) is the current version.

2) **Group by tool call**
   - Blocks for the same artifact in one tool call share `meta.tool_call_id`.
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

**react.patch**
- Accepts physical path (OUT_DIR‑relative).
- If path is historical (`turn_X/files/...`), a copy is created in current turn before patching.

**rendering_tools.write_*** / **react.write**
- Use physical path; timeline stores logical path in `meta`.
- For `kind=file`, file is hosted and metadata (hosted_uri/rn/key) stored in `meta`.
  - `visibility=internal` files are **not hosted** (stored only in OUT_DIR + timeline).
- For `kind=display`, content is emitted and stored as text block.

## Compaction Notes

Compaction summarizes **blocks**, not artifacts. Artifact metadata must remain visible:
- Compaction serializer includes `artifact_path`, `physical_path`, `mime`, and `tool_call_id`.
- This preserves discovery even if older blocks are summarized.

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
   - If a SID maps to a file/attachment source (via `local_path` or `artifact_path`),
     that file is rehosted into OUT_DIR.
   - If a SID is missing in sources_pool, a warning notice is emitted
     (`tool_call_warning.missing_sources`).

### Required sources_pool metadata

To enable SID→file resolution, file/attachment sources MUST carry:
- `local_path` (e.g., `turn_123/attachments/photo.png`)
- `artifact_path` (e.g., `fi:turn_123.user.attachments/photo.png`)
- `source_type` = `attachment` or `file`

These fields are populated automatically for:
- **User attachments** at turn start (source_type=`attachment`)
- **Produced files** (exec/render/write tools) when hosted (source_type=`file`)

### Why this matters

Rendering tools run in isolated workspaces and only see **OUT_DIR**. Rehosting ensures:
- `<img src="turn_123/attachments/x.png">` renders correctly
- `[[S:n]]` that references a file source becomes renderable
- Tool outputs are reproducible even when prior turns are compacted

## Examples

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

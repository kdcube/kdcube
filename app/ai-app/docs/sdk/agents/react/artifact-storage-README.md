---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
title: "Artifact Storage"
summary: "Where artifacts are stored and how artifact files/attachments are organized."
tags: ["sdk", "agents", "react", "artifacts", "storage"]
keywords: ["artifact storage", "attachments", "turn artifacts", "timeline files", "storage rules"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/files-vs-outputs-README.md
---
# Artifact Storage Rules

Scope:
- this document describes current artifact persistence and hosting behavior
- it does not define workspace-membership semantics by itself
- the current namespace split between durable workspace `files/...` and non-workspace `outputs/...` is described in `files-vs-outputs-README.md`

## Files vs Tool Results
Tool results are stored in the **event log blocks** (timeline), not on disk.
Only the following are written to disk in the artifact output root:
- exec tool outputs
- `react.write` artifacts
- `rendering_tools.write_*` outputs
- user attachments (materialized when needed)

Path root contract:
- Agent-visible physical paths are always relative paths such as
  `turn_<id>/files/...`, `turn_<id>/outputs/...`, and
  `turn_<id>/attachments/...`.
- The agent should not know or mention the host/runtime prefix. It should only
  use the `turn_...` path.
- In local runtime storage, those paths live under the artifact root
  `out/workdir/`.
- The sibling runtime root `out/` is reserved for platform metadata such as
  `timeline.json`, `tool_calls_index.json`, tool-call JSON records, logs, and
  execution diagnostics.

User attachments:
- PDFs/images are attached as binary blocks.
- Other types (e.g., `.docx`, `.xlsx`, `.txt`) are **meta only** and must be read via `physical_path`
  (e.g., `turn_<id>/attachments/<filename>`).

## Hosted storage location
Assistant-produced file bytes are hosted in the conversation file storage
surface under the current turn. The stored key preserves the full
artifact-root-relative path, so files with the same basename in different
directories remain distinct.

General shape:
```
s3://<bucket>/<prefix>/cb/tenants/<tenant>/projects/<project>/attachments/<user_id>/<conversation_id>/<turn_id>/<artifact-root-relative-path>
```

Example:
```text
physical_path:
  turn_2026-05-27-21-10-44-540/outputs/analysis/zip_contents.json

storage key:
  cb/tenants/demo/projects/demo/attachments/<user>/<conversation>/
    turn_2026-05-27-21-10-44-540/
    turn_2026-05-27-21-10-44-540/outputs/analysis/zip_contents.json
```

Visibility controls transport/UI emission, not whether the file bytes are
hosted. External files are hosted and emitted to the UI. Internal files are
hosted for later agent/runtime use and recorded in metadata, but are not emitted
as visible user files.

## Conversation State Artifacts
Conversation state is stored as two artifacts:
- `artifact:conv.timeline.v1` — timeline blocks + conversation metadata + current full `sources_pool`
- `artifact:conv:sources_pool` — sources pool only

The timeline snapshot includes `react.thinking` blocks (hidden) but **does not**
persist a `conv.thinking.stream` artifact anymore. UI-facing thinking items are
reconstructed from the turn log timeline during fetch.

The sources pool payload is:
```json
{ "sources_pool": [ ... ] }
```

### PostgreSQL index record (conv_messages)
Both artifacts are indexed in `conv_messages` with:
- `role = "artifact"`
- `tags` include `artifact:<kind>` and `turn:<turn_id>`
- `text` is a compact JSON summary (not the full payload)

**Timeline record (`artifact:conv.timeline.v1`)**
`text` contains:
```json
{
  "conversation_title": "...",
  "conversation_started_at": "...",
  "last_activity_at": "...",
  "blocks_count": 18,
  "sources_pool_count": 2,
  "turn_ids": ["turn_..."]
}
```

**Sources pool record (`artifact:conv:sources_pool`)**
`text` contains:
```json
{
  "sources_pool": [
    { "sid": 1, "title": "...", "url": "...", "text": "short snippet", "published_time_iso": "...", "favicon": "..." }
  ],
  "sources_pool_count": 2,
  "turn_ids": ["turn_..."],
  "last_activity_at": "..."
}
```

## Paths (Stable)
- Logical artifact path (used by `react.read` / `fetch_ctx`):
  - `ar:<turn_id>.user.prompt`
  - `ar:<turn_id>.assistant.completion`
  - `ar:<turn_id>.assistant.completion.<n>` for earlier visible completions from the same turn
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- `fi:<turn_id>.outputs/<relative_path>`
- Physical paths (artifact-root-relative, under `out/workdir` locally):
  - Files: `turn_<id>/files/<relative_path>`
  - Outputs: `turn_<id>/outputs/<relative_path>`
  - Attachments: `turn_<id>/attachments/<filename>`

Artifacts never use `current_turn` in their paths. Always use the concrete `turn_id`.
Do not expose absolute host paths, execution sandbox paths, or hosted `file://`
paths to the agent as write/read targets.

## Hosted file fields
When a file is hosted, metadata blocks include:
- `rn` (resource name; primary download handle)
- `hosted_uri` (S3 path)
- `key` (storage key)
These are **not interchangeable**; UI expects `rn` for downloads.

`react.pull` uses `hosted_uri` or `key` to materialize file bytes. It must not
fall back to a visible text preview as if that preview were the complete file.

## Visibility
- `visibility=external`: sent to user (chat or file attachment)
- `visibility=internal`: hosted and stored for agent/runtime use, not emitted to the user
  - Internal notes written via `react.write(channel="internal")` are stored as `react.note` blocks.
  - Files created with `kind=file` are hosted with their full workspace-relative path.
  - Exec contract files may explicitly request `visibility=internal` to keep the output agent-only.

Important:
- `visibility` answers who receives the artifact
- it does not suppress persistence of produced file bytes
- it does **not** answer whether the artifact is part of the durable workspace/project tree
- that distinction lives at the namespace level (`files/...` vs `outputs/...`), not in `visibility`

## Kind
- `kind=file`: normal file artifact
- `kind=display`: streamed display artifact
- `kind=file`: downloadable file artifact when hosting succeeds

## Example (meta block)
```json
{
  "artifact_path": "fi:turn_1771234567890_abcd.files/report.md",
  "physical_path": "turn_1771234567890_abcd/files/report.md",
  "mime": "text/markdown",
  "kind": "display",
  "visibility": "external",
  "rn": "ef:...:artifact:report.md",
  "hosted_uri": "s3://...",
  "key": "..."
}
```

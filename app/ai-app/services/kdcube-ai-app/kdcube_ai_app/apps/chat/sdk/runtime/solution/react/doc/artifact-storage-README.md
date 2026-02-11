# Artifact Storage Rules

## Files vs Tool Results
Tool results are stored in the **event log blocks** (timeline), not on disk.
Only the following are written to disk in OUT_DIR:
- exec tool outputs
- `react.file` / `react.write` (when `kind=file`)
- `rendering_tools.write_*` outputs
- user attachments (materialized when needed)

User attachments:
- PDFs/images are attached as binary blocks.
- Other types (e.g., `.docx`, `.xlsx`, `.txt`) are **meta only** and must be read via `physical_path`
  (e.g., `turn_<id>/attachments/<filename>`).

## Hosted storage location
Assistant‑produced files are stored in the conversation attachments path:
```
s3://<bucket>/<prefix>/cb/tenants/<tenant>/projects/<project>/attachments/<role>/<user_id>/<conversation_id>/<turn_id>/<filename>
```

## Paths (Stable)
- Logical artifact path (used by `react.read` / `fetch_ctx`):
  - `ar:<turn_id>.user.prompt`
  - `ar:<turn_id>.assistant.completion`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- Physical paths (OUT_DIR‑relative):
  - Files: `turn_<id>/files/<relative_path>`
  - Attachments: `turn_<id>/attachments/<filename>`

Artifacts never use `current_turn` in their paths. Always use the concrete `turn_id`.

## Hosted file fields (external only)
When a file is hosted, metadata blocks include:
- `rn` (resource name; primary download handle)
- `hosted_uri` (S3 path)
- `key` (storage key)
These are **not interchangeable**; UI expects `rn` for downloads.

## Visibility
- `visibility=external`: sent to user (chat or file attachment)
- `visibility=internal`: stored only for agent use
  - Internal notes written via `react.write(channel="internal")` are stored as `react.note` blocks.
  - Files created with `kind=file` are **not hosted** (they remain in OUT_DIR and the timeline).

## Kind
- `kind=file`: normal file artifact
- `kind=display`: streamed display artifact (shown to the user; also stored as a file if `react.file` is used)

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

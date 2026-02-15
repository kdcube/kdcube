# Turn Log Structure (Current)

The turn log is the single source of truth for reconstructing a turn.
It stores **only the ordered timeline blocks** emitted during that turn.
All user/assistant text, attachments, tool results, and artifacts are encoded in those blocks.

## Top‑level shape
```json
{
  "turn_id": "turn_1777000000000_abcd12",
  "ts": "2026-02-07T10:12:33Z",
  "blocks": [ ... ]
}
```

## Blocks (contrib log)
`blocks` is the ordered list of event blocks for this turn. Each block has:
- `type`
- `author`
- `turn_id`
- `ts`
- `mime`
- `path`
- `text` (text content) or `base64` (binary)
- `meta` (optional; includes artifact metadata, hosted file fields, etc.)

See `event-blocks-README.md` for block types and examples.

## Reconstruction
The turn view is reconstructed by:
```
Timeline.build_turn_view(turn_id, blocks, sources_pool)
```
which yields:
- user prompt (text + ts)
- assistant completion (text + ts)
- user attachments (payloads with rn/hosted_uri/filename/mime)
- assistant files (payloads with rn/hosted_uri/filename/mime)
- citations (sources_used resolved against sources_pool)
- follow‑up suggestions (from `stage.suggested_followups` blocks)

## Notes
- No `user` / `assistant` fields are stored in the turn log.
- No `files` list is stored separately. Files are reconstructed from blocks.
- All paths must include concrete `turn_id` (no `current_turn`).

# Streaming + Storage (Minimal Bundle)

This bundle demonstrates two fundamentals:
- Streaming via `versatile_streamer` with `thinking` + `answer` channels.
- Per-bundle storage via `AIBundleStorage` (prompt + turn JSON).

## Flow (schema)

1) Receive user text + attachments
2) Ingest attachments (extract text + modal payloads)
3) Write `turns/<turn_id>/prompt.md`
4) Stream model response with channel tags (strict):
   - `<channel:thinking> ... </channel:thinking>`
   - `<channel:answer> ... </channel:answer>`
5) Write `turns/<turn_id>/turn.json`

## Stored files

- `turns/<turn_id>/prompt.md`
- `turns/<turn_id>/turn.json`

`turn.json` includes:
- `ts`, `request_id`, `turn_id`, `conversation_id`
- `user.prompt` → `{ mime, path }`
- `user.attachments[]` → `{ mime, filename, size_bytes, path, hosted_uri, summary }`
- `assistant.thinking` + `assistant.answer`

## Entrypoint

See `entrypoint.py` in this directory. It uses:
- `create_cached_system_message` / `create_cached_human_message`
- `stream_with_channels` from `sdk/streaming/versatile_streamer.py`
- `AIBundleStorage` from `sdk/storage/ai_bundle_storage.py`

Notes:
- `stream_with_channels` routes deltas by channel; the model must emit channel tags.
- The emitter routes by `marker`; `channel` is stripped before calling `AIBEmitters.delta`.

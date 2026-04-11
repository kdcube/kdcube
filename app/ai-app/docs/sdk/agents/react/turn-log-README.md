---
id: ks:docs/sdk/agents/react/turn-log-README.md
title: "Turn Log"
summary: "Turn log structure used to reconstruct a turn."
tags: ["sdk", "agents", "react", "turn-log"]
keywords: ["turn log", "timeline blocks", "reconstruct turn", "block order"]
see_also:
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/react-context-README.md
  - ks:docs/sdk/agents/react/timeline-README.md
---
# Turn Log Structure (Current)

The turn log is the single source of truth for reconstructing a turn.
It stores **only the ordered timeline blocks** emitted during that turn.
All user/assistant text, attachments, tool results, and artifacts are encoded in those blocks.
That includes Internal Memory Beacons written with `react.write(channel="internal")`.
It can also include live-folded external user event blocks (`user.followup`, `user.steer`)
when those events reached the active timeline owner during the turn.

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

Important React-specific beacon blocks:
- `react.note` for freshly written Internal Memory Beacons
- `react.note.preserved` for beacon copies kept visible after compaction

Important external-event blocks:
- `user.followup`
- `user.steer`

These are persisted as ordinary turn blocks once folded into the active timeline. Their metadata
retains the durable event identity (`message_id`, `stream_id`, `sequence`) and routing hints such as
`target_turn_id` / `owner_turn_id`.

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

Rendered model view (via `timeline.render`) groups tool output into:
- `[TOOL CALL <id>].call <tool_id>`
- `[TOOL RESULT <id>].summary <tool_id>` (artifact tools)
- `[TOOL RESULT <id>].result <tool_id>` (non‑artifact tools)
- `[TOOL RESULT <id>].artifact <tool_id>` per artifact (logical_path + content)

## Notes
- No `user` / `assistant` fields are stored in the turn log.
- No `files` list is stored separately. Files are reconstructed from blocks.
- All paths must include concrete `turn_id` (no `current_turn`).
- Internal Memory Beacons are stored in the turn log as normal blocks, but they are not user-facing UI artifacts.
- The turn log itself does not store the conversation-level external replay cursor; that cursor lives in
  the timeline artifact payload (`last_external_event_id`, `last_external_event_seq`).

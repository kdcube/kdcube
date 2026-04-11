---
id: ks:docs/sdk/agents/react/turn-data-README.md
title: "Turn Data"
summary: "Turn fetch payload and how it is constructed for the client."
tags: ["sdk", "agents", "react", "api", "turn-data"]
keywords: ["/conversations/{id}/fetch", "turn log", "conversation payload", "client expectations"]
see_also:
  - ks:docs/sdk/agents/react/conversation-artifacts-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/react-context-README.md
  - ks:docs/sdk/agents/react/design/files-vs-outputs-README.md
---
# Turn Data (Conversation Fetch)

This document describes the `/conversations/{id}/fetch` payload and how it is constructed from
turn logs and stream artifacts. It reflects the current UI expectations in the reference client.

Scope:
- this document is about fetch/UI payload shape
- it does not define workspace-membership semantics
- `outputs/...` artifacts already surface through the same external file artifact path unless UI needs a stronger distinction

## Fetch flow (server)
Endpoint: `POST /api/cb/conversations/{tenant}/{project}/{conversation_id}/fetch`

Implementation: `kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py::fetch_conversation_artifacts`

The response shape:
```json
{
  "user_id": "...",
  "conversation_id": "...",
  "conversation_title": "...",
  "turns": [
    { "turn_id": "...", "artifacts": [ ... ] }
  ]
}
```

### Where `conversation_title` comes from
`conversation_title` is read from the timeline artifact (`artifact:conv.timeline.v1`)
and parsed from its payload. If the timeline is missing or malformed, the title is `null`.

Artifacts per turn are assembled from:
1) Indexed artifacts (tagged in conv index)
2) Turn log fields (user prompt, assistant completion, attachments, files)
3) Optional stream artifacts emitted by the communicator

## Artifact types used by UI
Client parsing: `ui/src/components/chat/types/chat.ts` → `getHistoricalTurn()`

Expected types:
- `chat:user` (from turn log)
- `chat:assistant` (from turn log)
- `artifact:user.attachment` (from turn log)
- `artifact:assistant.file` (from turn log; external only)
- `artifact:solver.program.citables` (from turn log sources_pool)
- `artifact:conv.thinking.stream` (optional, synthesized from turn log)
- `artifact:conv.timeline_text.stream` (optional, synthesized from turn log)
- `artifact:conv.artifacts.stream` (optional, emitted by communicator)
- `artifact:conv.user_shortcuts` (from turn log follow‑ups)
- `artifact:conv.clarification_questions` (from turn log clarification stage)
- `artifact:turn.log.reaction` (optional feedback)

## Turn log fields used by fetch (v2)
`fetch_conversation_artifacts` reads:
- `blocks[]` → reconstructed via `Timeline.build_turn_view(...)`
- timeline `sources_pool[]` → `artifact:solver.program.citables`

### Artifacts included by fetch
From the reconstructed turn view:
- `chat:user` (user prompt text)
- `artifact:user.attachment` (attachments with filename/mime/rn/hosted_uri)
- `chat:assistant` (assistant completion text)
- `artifact:assistant.file` (external files only; kind != display)
- `artifact:solver.program.citables` (citations resolved from sources_pool + sources_used)
- `artifact:conv.user_shortcuts` (follow‑up suggestions, if provided this turn)
- `artifact:conv.clarification_questions` (clarification questions, if provided this turn)

### Example payloads (follow‑ups & clarifications)
```json
{
  "type": "artifact:conv.user_shortcuts",
  "ts": "2026-02-09T02:14:32.676425Z",
  "data": {
    "payload": {
      "items": [
        "Want a map view of these restaurants?",
        "Prefer vegetarian-only options?"
      ]
    },
    "meta": { "kind": "conv.user_shortcuts", "turn_id": "turn_123" }
  }
}
```

```json
{
  "type": "artifact:conv.clarification_questions",
  "ts": "2026-02-09T02:14:32.676425Z",
  "data": {
    "payload": {
      "items": [
        "Do you want fine dining or casual?",
        "Any cuisine preferences?"
      ]
    },
    "meta": { "kind": "conv.clarification_questions", "turn_id": "turn_123" }
  }
}
```

## Notes / ambiguities
- **Display artifacts** (`kind=display`) are not emitted as `artifact:assistant.file`.
  If UI needs them, they must be surfaced via stream artifacts (conv.timeline_text.stream)
  or by adding a new artifact type.
- Internal Memory Beacons (`react.note` / `react.note.preserved`) are not exposed as fetch/UI artifacts.
  They remain model-side timeline memory and turn-log reconstruction data.
- External `user.followup` / `user.steer` blocks are also not surfaced today as dedicated fetch/UI
  artifacts. They exist in the timeline and turn-log reconstruction path, but the current fetch payload
  does not project them into a separate top-level client artifact family.
- Future namespace note:
  - assistant artifact paths are now split into durable workspace `files/...` and non-workspace `outputs/...`
  - fetch does not need a new top-level artifact family immediately
  - both still surface as external assistant files, with the namespace preserved in metadata/path
- Stream artifacts `conv.thinking.stream` and `conv.timeline_text.stream` are now **synthesized**
  from the turn log timeline (no longer persisted blobs).

## Action items
- Decide whether display artifacts should be included in fetch output.

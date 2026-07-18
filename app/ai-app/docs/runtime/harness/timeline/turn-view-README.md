---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-view-README.md
title: "Harness Turn View"
summary: "Framework-neutral turn-view projection and its use by conversation fetch."
tags: ["runtime", "harness", "timeline", "api", "turn-view"]
updated_at: 2026-07-18
keywords: ["/conversations/{id}/fetch", "turn log", "conversation payload", "client expectations"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
---
# Harness Turn View And Conversation Fetch

The shared turn-view projection reconstructs client-facing turn data from
persisted blocks. The conversation fetch endpoint uses that projection rather
than depending on ReAct's in-memory timeline object.

Scope:
- this document is about fetch/UI payload shape
- it does not define workspace-membership semantics
- `files/...` artifacts already surface through the same external file artifact path unless UI needs a stronger distinction

## Fetch flow (server)
Endpoint: `POST /api/cb/conversations/{tenant}/{project}/{conversation_id}/fetch`

Implementation: `kdcube_ai_app/apps/chat/sdk/solutions/conversation/ctx_rag.py::fetch_conversation_artifacts`

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
- `blocks[]` → reconstructed via
  `runtime.harness.timeline.turn_view.build_turn_view(...)`
- timeline `sources_pool[]` → `artifact:solver.program.citables`

### Artifacts included by fetch
From the reconstructed turn view / ordered turn-log block stream:
- `chat:user` (one or more prompt-like user entries from `user.prompt`, `user.followup`, `user.steer`)
- `artifact:user.attachment` (attachments with filename/mime/rn/hosted_uri)
- `chat:assistant` (one or more assistant completion entries from `assistant.completion` blocks)
- `artifact:assistant.file` (external files only; kind != display)
- `artifact:solver.program.citables` (citations resolved from sources_pool + sources_used)
- `artifact:conv.user_shortcuts` (follow‑up suggestions, if provided this turn)
- `artifact:conv.clarification_questions` (clarification questions, if provided this turn)

Framework-specific prompt visibility caps affect only model-facing blocks
during a run. For ReAct, `react.read` caps apply per requested path and large
tool results are prompt-capped before the next decision round while the full
`conv:tc:` block remains stored. Fetch/download reconstruction uses artifact
metadata and hosting fields (`rn`, `hosted_uri`, `physical_path`) when
available; it does not rely on complete file bytes having been inlined into a
model prompt.

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
- External `user.followup` / `user.steer` are not emitted as a separate top-level artifact family,
  but they are now projected into the fetched `chat:user` sequence for that turn.
- One turn can now produce multiple `chat:user` and multiple `chat:assistant` artifacts.
- Future namespace note:
  - assistant artifact paths are now split into durable workspace `git/projects/...` and produced artifact `files/...`
  - fetch does not need a new top-level artifact family immediately
  - both still surface as external assistant files, with the namespace preserved in metadata/path
- Stream artifacts `conv.thinking.stream` and `conv.timeline_text.stream` are now **synthesized**
  from the turn log timeline (no longer persisted blobs).

## Action items
- Decide whether display artifacts should be included in fetch output.

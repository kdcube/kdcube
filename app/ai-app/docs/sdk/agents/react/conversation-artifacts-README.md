---
id: ks:docs/sdk/agents/react/conversation-artifacts-README.md
title: "Conversation Artifacts"
summary: "Artifacts persisted in conversation storage for v2 flow."
tags: ["sdk", "agents", "react", "artifacts", "conversation"]
keywords: ["ContextRAGClient", "save_artifact", "save_turn_log_artifact", "conversation store"]
see_also:
  - ks:docs/sdk/agents/react/turn-data-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
---
# Conversation Artifacts (v2)

This document lists artifacts persisted in the conversation store/index in the v2 flow.
It focuses on artifacts written via `ContextRAGClient.save_artifact(...)` or
`ContextRAGClient.save_turn_log_as_artifact(...)`.

Notes:
- `content_str` is the text stored in the index row (`conv_messages.text`).
- `Indexed` means an embedding is computed and stored for this artifact.
- `index_only=True` means **no blob** is written; the index row stores `hosted_uri="index_only"`.
- `store_only=True` means **no index row** is written (not used by artifacts listed below).
- Embeddings are caller‑supplied; the store does not compute embeddings.

Reference implementations:
- Save API: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py`
- Core workflow writers: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py`
- Streaming artifacts persistence: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`

## Artifact Table

| Artifact kind                               | Stored blob     | Indexed | Tags (base)                                                                 | When stored                                   | Embedding                        | Description |
|---------------------------------------------|-----------------|---------|------------------------------------------------------------------------------|------------------------------------------------|----------------------------------|-------------|
| `conv.timeline.v1`                          | Yes             | Yes     | `artifact:conv.timeline.v1`, `turn:<turn_id>`                               | End of turn (persist timeline).               | Yes (compact summary text)       | Timeline payload with blocks + metadata + **compact** sources_pool snapshot. |
| `conv:sources_pool`                         | Yes             | Yes     | `artifact:conv:sources_pool`, `turn:<turn_id>`                              | End of turn (persist sources pool).           | Yes (compact summary text)       | Full sources pool (authoritative, progressive conversation‑level artifact). |
| `turn.log` (`artifact:turn.log`)            | Yes             | No      | `kind:turn.log`, `artifact:turn.log`, `turn:<turn_id>`                      | End of turn when `TurnLog` is persisted.      | No                               | Minimal turn log: blocks produced this turn (JSON payload). Index text is a compact JSON summary. |
| `turn.log.reaction`                         | Yes             | No      | `artifact:turn.log.reaction`, `turn:<turn_id>`, `origin:<user|machine>`     | When feedback is added.                       | No                               | Feedback / reaction linked to a turn. |
| `conv.range.summary`                        | No (index‑only) | Yes     | `artifact:conv.range.summary`, `turn:<turn_id>`                              | When context compaction runs.                 | Yes (summary text)               | Summary for a range of turns. |
| `conv.thinking.stream`                      | No (synthesized) | No      | `artifact:conv.thinking.stream`, `turn:<turn_id>`                            | Fetch (from turn log timeline).                | No                               | Thinking items reconstructed from `react.thinking` blocks in turn log. |
| `conv.artifacts.stream`                     | Yes             | No      | `artifact:conv.artifacts.stream`, `turn:<turn_id>`, `conversation`, `stream` | End of turn (stream aggregation).             | No                               | Aggregated canvas/tool stream blocks. |
| `conv.timeline_text.stream`                 | Yes             | No      | `artifact:conv.timeline_text.stream`, `turn:<turn_id>`, `conversation`, `stream` | End of turn (stream aggregation).         | No                               | Aggregated timeline text blocks. |

## Notes
- Internal Memory Beacons are **not** a separate conversation artifact kind.
  They live inside:
  - `conv.timeline.v1` as `react.note` / `react.note.preserved` blocks
  - `artifact:turn.log` as ordinary per-turn blocks
- Compaction may also absorb beacon content into `conv.range.summary`, but the timeline now preserves visible
  beacon copies after the summary as `react.note.preserved`.
- Display artifacts (`kind=display`) are **not** emitted as `artifact:assistant.file`.
  They are surfaced through stream artifacts (timeline/artifacts streams).
- Turn log blocks are stored and used by ContextBrowser; they are not
  UI artifacts in the fetch payload.
- User attachments and produced files are hosted separately (rn/hosted_uri) and referenced
  via block metadata; they are not standalone conversation artifacts here.
- Feedback is persisted as `artifact:turn.log.reaction` and mirrored into the **turn log payload**
  (`turn_log.feedbacks[]` and `turn_log.entries[]`) inside `artifact:turn.log`.
  When cache is cold, React v2 injects `turn.feedback` blocks into the timeline and those
  blocks are persisted inside `conv.timeline.v1`.
- React v2 refreshes feedback by querying **latest reaction per turn** (SQL `DISTINCT ON`),
  filtered by `artifact:turn.log.reaction` tag and the timeline’s `turn_id`s.
- `artifact:turn.log.reaction` rows store reaction JSON in `conv_messages.text`
  for fast index‑only reads. Shape:
  ```json
  {
    "turn_id": "<turn_id>",
    "text": "<feedback text>",
    "confidence": 1.0,
    "ts": "<feedback_ts>",
    "reaction": "ok|not_ok|neutral",
    "origin": "user|machine"
  }
  ```
- `artifact:turn.log` rows store a **compact JSON summary** in `conv_messages.text`
  for fast index‑only reads. Shape:
  ```json
  {
    "turn_id": "<turn_id>",
    "ts": "<turn_start_ts>",
    "end_ts": "<turn_end_ts>",
    "sources_used": [1, 2, 3],
    "blocks_count": 42,
    "tokens": 1234,
    "feedback": {
      "count": 2,
      "last_ts": "<feedback_ts>",
      "last_reaction": "ok|not_ok|neutral|null",
      "last_origin": "user|machine",
      "last_text": "<latest feedback text>"
    }
  }
  ```
- The timeline artifact stores only a lightweight sources_pool snapshot for indexing/local access;
  the full pool lives in `conv:sources_pool` and is loaded at turn start.
- Loading happens in `ContextBrowser.load_timeline` (`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/browser.py`).

## Storage Layout (Blob Store)

See: `docs/sdk/storage/sdk-store-README.md`

```
<kdcube>/cb/tenants/<tenant>/projects/<project>/conversation/<role>/<user_id>/<conversation_id>/<turn_id>/
  artifact-<ts>-<id>-turn.log.json
  artifact-<ts>-<id>-conv.timeline.v1.json
  artifact-<ts>-<id>-conv:sources_pool.json
  artifact-<ts>-<id>-conv.artifacts.stream.json
  (conv.thinking.stream is no longer persisted; it is synthesized during fetch)
  <attachment files...>

<kdcube>/cb/tenants/<tenant>/projects/<project>/executions/<user_id>/<conversation_id>/<turn_id>/<exec_id>/
  out.zip
  pkg.zip

<kdcube>/accounting/<tenant>/project/<YYYY.MM.DD>/<service_name>/<bundle_id>/
  cb|<user_id>|<conversation_id>|<turn_id>|answer.generator.regular|<timestamp>.json
```

## Where These Are Written
- Core workflow artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py`
- Streaming artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- Turn log + reactions: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py`
- Memory artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/memory/conv_memories.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/memory/buckets.py`

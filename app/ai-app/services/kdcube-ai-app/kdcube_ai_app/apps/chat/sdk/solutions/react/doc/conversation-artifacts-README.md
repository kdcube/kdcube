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
- Save API: `context/retrieval/ctx_rag.py`
- Core workflow writers: `solutions/chatbot/base_workflow.py`
- Streaming artifacts persistence: `solutions/chatbot/entrypoint.py`

## Artifact Table

| Artifact kind                               | Stored blob     | Indexed | Tags (base)                                                                 | When stored                                   | Embedding                        | Description |
|---------------------------------------------|-----------------|---------|------------------------------------------------------------------------------|------------------------------------------------|----------------------------------|-------------|
| `conv.timeline.v1`                          | Yes             | Yes     | `artifact:conv.timeline.v1`, `turn:<turn_id>`                               | End of turn (persist timeline).               | Yes (compact summary text)       | Full timeline payload (blocks + sources_pool + metadata). |
| `turn.log` (`artifact:turn.log`)            | Yes             | No      | `kind:turn.log`, `artifact:turn.log`, `turn:<turn_id>`                      | End of turn when `TurnLog` is persisted.      | No                               | Minimal turn log: blocks produced this turn (JSON payload). |
| `turn.log.reaction`                         | Yes             | No      | `artifact:turn.log.reaction`, `turn:<turn_id>`, `origin:<user|machine>`     | When feedback is added.                       | No                               | Feedback / reaction linked to a turn. |
| `conv.range.summary`                        | No (index‑only) | Yes     | `artifact:conv.range.summary`, `turn:<turn_id>`                              | When context compaction runs.                 | Yes (summary text)               | Summary for a range of turns. |
| `conv.thinking.stream`                      | No (synthesized) | No      | `artifact:conv.thinking.stream`, `turn:<turn_id>`                            | Fetch (from turn log timeline).                | No                               | Thinking items reconstructed from `react.thinking` blocks in turn log. |
| `conv.artifacts.stream`                     | Yes             | No      | `artifact:conv.artifacts.stream`, `turn:<turn_id>`, `conversation`, `stream` | End of turn (stream aggregation).             | No                               | Aggregated canvas/tool stream blocks. |
| `conv.timeline_text.stream`                 | Yes             | No      | `artifact:conv.timeline_text.stream`, `turn:<turn_id>`, `conversation`, `stream` | End of turn (stream aggregation).         | No                               | Aggregated timeline text blocks. |

## Notes
- Display artifacts (`kind=display`) are **not** emitted as `artifact:assistant.file`.
  They are surfaced through stream artifacts (timeline/artifacts streams).
- Turn log blocks are stored and used by ContextBrowser; they are not
  UI artifacts in the fetch payload.
- User attachments and produced files are hosted separately (rn/hosted_uri) and referenced
  via block metadata; they are not standalone conversation artifacts here.

## Storage Layout (Blob Store)

See: `storage/sdk-store-README.md`

```
<kdcube>/cb/tenants/<tenant>/projects/<project>/conversation/<role>/<user_id>/<conversation_id>/<turn_id>/
  artifact-<ts>-<id>-turn.log.json
  artifact-<ts>-<id>-conv.timeline.v1.json
  artifact-<ts>-<id>-conv.artifacts.stream.json
  (conv.thinking.stream is no longer persisted; it is synthesized during fetch)
  <attachment files...>

<kdcube>/cb/tenants/<tenant>/projects/<project>/executions/privileged/<user_id>/<conversation_id>/<turn_id>/<exec_id>/
  out.zip
  pkg.zip

<kdcube>/accounting/<tenant>/project/<YYYY.MM.DD>/<service_name>/<bundle_id>/
  cb|<user_id>|<conversation_id>|<turn_id>|answer.generator.regular|<timestamp>.json
```

## Where These Are Written
- Core workflow artifacts: `solutions/chatbot/base_workflow.py`
- Streaming artifacts: `solutions/chatbot/entrypoint.py`
- Turn log + reactions: `context/retrieval/ctx_rag.py`
- Memory artifacts: `context/memory/conv_memories.py`, `context/memory/buckets.py`

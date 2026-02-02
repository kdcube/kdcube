# Conversation Artifacts (Chat SDK)

This document lists the **artifacts currently persisted in the conversation store/index**. It focuses on artifacts written via `ContextRAGClient.save_artifact(...)` or `ContextRAGClient.save_turn_log_as_artifact(...)` and the memory helpers that call `upsert_artifact(...)`.

Notes:
- `content_str` is the text stored in the index row (`conv_messages.text`).
- `index_only=True` means **no blob** is written; the index row stores `hosted_uri="index_only"`.
- `store_only=True` means **no index row** is written (not used by the artifacts listed below).
- `embedding` is **always caller‑supplied**; the store does not compute embeddings internally.

Reference implementations:
- Save API: [`ctx_rag.py`](../../../context/retrieval/ctx_rag.py)
- Core workflow writers: [`base_workflow.py`](../../../solutions/chatbot/base_workflow.py)
- Streaming artifacts persistence: [`entrypoint.py`](../../../solutions/chatbot/entrypoint.py)
- Memory helpers: [`conv_memories.py`](../../../context/memory/conv_memories.py), [`buckets.py`](../../../context/memory/buckets.py)

## Artifact Table

| Artifact kind | Stored blob | Indexed | Tags (base) | When stored | Embedding | Description |
|---|---|---|---|---|---|---|
| `turn.log` (`artifact:turn.log`) | Yes | Yes | `kind:turn.log`, `artifact:turn.log`, `turn:<turn_id>`, `track:<track_id>` | End of turn when `TurnLog` is persisted. | No | Full turn log (markdown + payload). Source of many derived UI elements. |
| `turn.log.reaction` (`artifact:turn.log.reaction`) | Yes | Yes | `artifact:turn.log.reaction`, `turn:<turn_id>`, `origin:<user|machine>`, `track:<track_id>` | When feedback is added (user or system). | No | Feedback / reaction linked to a turn. |
| `turn.fingerprint.v1` (`artifact:turn.fingerprint.v1`) | No (index‑only) | Yes | `artifact:turn.fingerprint.v1`, plus optional `conv.start`, `assistant_signal`, `assistant_signal:<key>` | After ctx‑reconciler; always per turn. | No | Compact JSON fingerprint used by memory subsystem. |
| `conv.user_shortcuts` | Yes | Yes | `artifact:conv.user_shortcuts`, `turn:<turn_id>`, `track:<track_id>` | When `user_shortcuts` are produced. | No | Follow‑up suggestions saved as an artifact. |
| `conv.clarification_questions` | Yes | Yes | `artifact:conv.clarification_questions`, `turn:<turn_id>`, `track:<track_id>` | When clarification questions are produced. | No | Clarification questions saved as an artifact. |
| `user.attachment` | Yes | Yes | `artifact:user.attachment`, `turn:<turn_id>`, `track:<track_id>` | When attachment summaries are persisted. | Yes (summary text) | Attachment summary + metadata; index text is summary‑biased. |
| `project.log` | No (index‑only) | Yes | `artifact:project.log`, `turn:<turn_id>`, `track:<track_id>`, `slot:project_log` | When a project log deliverable exists. | No | Standalone project log (markdown). |
| `solver.program.presentation` | No (index‑only) | Yes | `artifact:solver.program.presentation`, `turn:<turn_id>`, `track:<track_id>`, `resource:<rid>` (optional) | When solver produces a program presentation. | Yes (presentation text) | Program presentation markdown for retrieval / UI. |
| `solver.failure` | No (index‑only) | Yes | `artifact:solver.failure`, `turn:<turn_id>`, `track:<track_id>` | When solver failure presentation exists. | Yes (failure text) | Failure summary markdown for debugging / UI. |
| `conv.thinking.stream` | Yes | Yes | `artifact:conv.thinking.stream`, `turn:<turn_id>`, `track:<track_id>`, `conversation`, `stream` | End of turn (stream aggregation). | No | Aggregated thinking stream blocks (full + compact index view). |
| `conv.artifacts.stream` | Yes | Yes | `artifact:conv.artifacts.stream`, `turn:<turn_id>`, `track:<track_id>`, `conversation`, `stream`, `canvas` | End of turn (stream aggregation). | No | Aggregated tool/canvas blocks (full + compact index view). |
| `conv.timeline_text.stream` | Yes | Yes | `artifact:conv.timeline_text.stream`, `turn:<turn_id>`, `track:<track_id>`, `conversation`, `stream`, `timeline_text` | End of turn (stream aggregation). | No | Aggregated timeline text blocks (full + compact index view). |
| `conversation.active_set.v1` | No (index‑only) | Yes | `artifact:conversation.active_set.v1`, `conv.state:conversation_state_v1` | When active set is updated. | No | Memory subsystem active set pointer (selection metadata). |
| `conversation.memory.bucket.v1` | Yes | Yes | `artifact:conversation.memory.bucket.v1`, `mem:bucket:<bucket_id>` | When memory buckets are upserted. | No | Long‑term memory bucket record. |

## Notes on Index Text and Embeddings

- Index text (`content_str`) is intentionally compact and is used by search (`ConvIndex`).
- Embeddings are computed **by the caller** (e.g., attachment summary, program presentation) and passed to `save_artifact(...)`.
- Index‑only artifacts still may carry embeddings in the index row (when provided).

## Storage Layout (Blob Store)

See the SDK storage layout doc for full details:
- [`sdk-store-README.md`](../../../storage/sdk-store-README.md)

Conversation + attachments + executions + accounting (schematic):

```
<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/conversation/<user_role>/<user_id>/<conversation_id>/<turn_id>/
  artifact-<ts>-<id>-turn.log.json
  artifact-<ts>-<id>-perf-steps.json
  artifact-<ts>-<id>-conv.user_shortcuts.json
  artifact-<ts>-<id>-conv.artifacts.stream.json
  artifact-<ts>-<id>-conv.thinking.stream.json
  <attachment files...>

<kdcube storage path>/cb/tenants/<tenant>/projects/<project>/executions/privileged/<user_id>/<conversation_id>/<turn_id>/<exec_id>/
  out.zip
  pkg.zip

<kdcube storage path>/accounting/<tenant>/project/<YYYY.MM.DD>/<service_name>/<bundle_id>/
  cb|<user_id>|<conversation_id>|<turn_id>|answer.generator.regular|<timestamp>.json

<kdcube storage path>/analytics/<tenant>/project/accounting/{daily|weekly|monthly}/
```

## Where These Are Written

- Core workflow artifacts: [`base_workflow.py`](../../../solutions/chatbot/base_workflow.py)
- Streaming artifacts (thinking/canvas/timeline): [`entrypoint.py`](../../../solutions/chatbot/entrypoint.py)
- Turn log and reactions: [`ctx_rag.py`](../../../context/retrieval/ctx_rag.py)
- Memory artifacts (active set + buckets):
  - [`conv_memories.py`](../../../context/memory/conv_memories.py)
  - [`buckets.py`](../../../context/memory/buckets.py)

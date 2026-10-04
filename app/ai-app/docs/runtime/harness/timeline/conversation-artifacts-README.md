---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-artifacts-README.md
title: "Harness Conversation Artifacts"
summary: "Timeline, TurnLog, source-pool, feedback, event/stream, and searchable transcript projections persisted for agent conversations, with their blob, index-row, and embedding contracts kept distinct."
tags: ["runtime", "harness", "timeline", "artifacts", "conversation"]
updated_at: 2026-08-22
keywords: ["ContextRAGClient", "save_artifact", "save_turn_log_artifact", "conversation store", "minimal turn transcript rows", "conv.artifacts.events", "projection:minimal.turn.log"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-retention-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-view-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-summary-contributions-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/artifact-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
---
# Harness Conversation Artifacts

This document lists the durable artifacts and content-row projections persisted
for agent conversations. It focuses on artifacts written via
`ContextRAGClient.save_artifact(...)` or
`ContextRAGClient.save_turn_log_as_artifact(...)`, plus the searchable role rows
derived from a framework-neutral minimal TurnLog.

Notes:
- `content_str` is the text stored in the index row (`conv_messages.text`).
- An **index row** is a `conv_messages` row. It may support lexical/trigram
  retrieval without carrying an embedding.
- **Embedding** states whether that row normally carries a vector. A best-effort
  embedding can be absent while the text row remains searchable by exact/fuzzy
  text.
- `index_only=True` means **no blob** is written; the index row stores `hosted_uri="index_only"`.
- `store_only=True` means **no index row** is written (not used by artifacts listed below).
- Embeddings are caller‑supplied; the store does not compute embeddings.

Reference implementations:
- Save API: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/conversation/ctx_rag.py`
- Framework-neutral builders and recording state: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/conversation/record.py`
- Core workflow writers: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py`
- Streaming artifacts persistence: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`

## Artifact And Projection Table

| Kind / projection | Stored blob | Index row | Embedding | Tags (base) | When stored | Responsibility |
| --- | --- | --- | --- | --- | --- | --- |
| `conv.timeline.v1` | Yes | Yes | No | `artifact:conv.timeline.v1`, `turn:<turn_id>` | End of a recorded turn. | Conversation registration and, for the ReAct Agent, the progressive conversation-level block/source projection. Its compact index metadata powers list/title/recency. |
| `conv:sources_pool` | Yes | Yes | No | `artifact:conv:sources_pool`, `turn:<turn_id>` | ReAct Agent timeline persistence. | Authoritative progressive source pool. Its index text is a compact source projection. |
| `turn.log` (`artifact:turn.log`) | Yes | Yes | No | `kind:turn.log`, `artifact:turn.log`, `turn:<turn_id>` | Whenever a `TurnLog` is persisted. | Per-turn reload envelope. Its index text is compact accounting metadata rather than transcript text. |
| Minimal transcript role rows | No (`index_only`) | Yes | Best effort | `chat:user` or `chat:assistant`, `turn:<turn_id>`, `projection:minimal.turn.log`; optional summary also carries `chat:summary`, `kind:working.summary`, `summary_scope:turn` | Alongside every minimal TurnLog. | One row per folded user submission, an optional explicitly contributed hosted-agent summary, and the final assistant completion. Semantic, lexical, and trigram topic discovery use these rows; lexical/trigram remain available when embedding fails. |
| ReAct Agent semantic role rows | No (`index_only`) | Yes | Yes where model service succeeds | Role/kind tags such as `chat:user`, `chat:assistant`, `kind:working.summary`, `kind:react.note` | ReAct Agent finalization and compaction. | Prompt/completion content plus richer supported attachment, summary, anchor, and selected-note projections. |
| `turn.log.reaction` | Yes | Yes | No | `artifact:turn.log.reaction`, `turn:<turn_id>`, `origin:<user|machine>` | When feedback is added. | Feedback/reaction linked to a turn. |
| `conv.range.summary` | No (`index_only`) | Yes | Best effort | `artifact:conv.range.summary`, `turn:<turn_id>` | When context compaction runs. | Searchable summary for a range of turns. |
| `conv.artifacts.events` | Yes | Yes | No | `artifact:conv.artifacts.events`, `turn:<turn_id>`, `conversation`, `events` | Turn post-run persistence. | Full recorded chat-event payloads selected for reload, including economics/timing and framework-neutral conversation objects such as steps, citations, and follow-ups. |
| `conv.artifacts.stream` | Yes | Yes | No | `artifact:conv.artifacts.stream`, `turn:<turn_id>`, `conversation`, `stream` | End-of-turn stream aggregation. | Aggregated canvas/tool/subsystem deltas replayed as completed client projections. |
| `conv.timeline_text.stream` | Yes | Yes | No | `artifact:conv.timeline_text.stream`, `turn:<turn_id>`, `conversation`, `stream` | End-of-turn stream aggregation. | Aggregated timeline-text blocks. |
| `conv.thinking.stream` | No (synthesized) | No | No | `artifact:conv.thinking.stream`, `turn:<turn_id>` | Fetch from TurnLog blocks. | Thinking items reconstructed one-for-one and in recorded order from `react.thinking` blocks. |

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
- TurnLog existence and replay ownership are separate per-turn signals. A
  minimal log owns message/file reload and its searchable role projection;
  dynamic event/stream artifacts remain active. A rich ReAct Agent log owns those
  block projections and suppresses the duplicate framework-neutral exporters.
- Fetch reconstruction can emit multiple `chat:user` and multiple `chat:assistant` artifacts
  from a single turn. Opening prompts, preserved followups/steers, and each visible assistant
  completion are indexed separately in conversation history.
- User attachments and produced files are hosted separately (rn/hosted_uri) and referenced
  via block metadata; they are not standalone conversation artifacts here.
- Feedback is persisted as `artifact:turn.log.reaction` and mirrored into the **turn log payload**
  (`turn_log.feedbacks[]` and `turn_log.entries[]`) inside `artifact:turn.log`.
  When cache is cold, ReAct Agent v2 injects `turn.feedback` blocks into the timeline and those
  blocks are persisted inside `conv.timeline.v1`.
- ReAct Agent v2 refreshes feedback by querying **latest reaction per turn** (SQL `DISTINCT ON`),
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
- Minimal hosted turns additionally write separate `role=user` and
  `role=assistant` rows tagged `projection:minimal.turn.log`. Their text comes
  from the same saved TurnLog blocks, so topic discovery and reload share turn
  identity without making the artifact row itself a transcript index. A hosted
  agent with the configured `record_turn_summary` tool may add one
  `conv.working.summary` block and corresponding anchored assistant row; see
  [Hosted Agent Turn Summary Contributions](turn-summary-contributions-README.md).
- The timeline artifact payload includes the current full `sources_pool` so logical source reads and
  exec `fetch_ctx` can recover fetched source content. The indexed text remains compact, and the
  authoritative progressive pool is also persisted as `conv:sources_pool` and loaded at turn start.
- The ReAct Agent adapter loads these artifacts through
  `ContextBrowser.load_timeline`
  (`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/browser.py`).
  Conversation APIs use the shared harness payload/turn-view modules directly.

## Storage Layout (Blob Store)

See: `docs/sdk/storage/sdk-store-README.md`

```
<kdcube>/cb/tenants/<tenant>/projects/<project>/conversation/<role>/<user_id>/<conversation_id>/<turn_id>/
  artifact-<ts>-<id>-turn.log.json
  artifact-<ts>-<id>-conv.timeline.v1.json
  artifact-<ts>-<id>-conv:sources_pool.json
  artifact-<ts>-<id>-conv.artifacts.events.json
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
- Minimal TurnLog, transcript rows, timeline registration, and recording kind: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/conversation/{record.py,ctx_rag.py}`
- Framework-neutral event artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- Streaming artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- Turn log + reactions: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/conversation/ctx_rag.py`
- Memory artifacts: `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/memory/conv_memories.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/context/memory/buckets.py`

# SSE Events (Chat)

This document describes the **SSE protocol** used by the chat system, including connection lifecycle events, the common event envelope, and all out‑of‑the‑box event types emitted by ingress + processor + bundles.

It is intended for:
- Frontend developers integrating the chat UI.
- Adapter developers (Telegram/Slack/etc.) who need to consume the stream.
- Anyone building a sniffer/aggregator for chat activity.

It reflects the current server implementation (ingress + processor split) and the default client implementation in `ChatService.ts`.

**Important**
- The SSE **event name** (e.g., `chat_step`) is **not** the same as `env.type` inside the JSON payload.
- The SSE event name is a transport‑level route. The payload `type` is the semantic event type.
- Bundles may emit **custom event types**, so clients must treat unknown types as generic `chat_step` events unless they explicitly support them.

---

**Endpoints**

All endpoints are relative to the chat base URL (or behind your proxy prefix):

- `GET /sse/stream`  
  Opens the SSE stream. This is a long‑lived connection.

- `POST /sse/chat`  
  Enqueues a chat request (user text, attachments, etc.).

- `POST /sse/conv_status.get`  
  Returns the conversation status and emits a `conv_status` event (also available on the stream).

---

**SSE Framing**

Each message is an SSE frame:

```
event: chat_step
data: { ...json... }

```

The stream also emits a comment keepalive every few seconds:

```
: keepalive

```

---

**Connection‑Level Events**

These events are **not** part of the chat envelope. They are transport signals from ingress.

| SSE event         | Purpose                                       | Payload (shape)                                                        |
|-------------------|-----------------------------------------------|------------------------------------------------------------------------|
| `ready`           | Stream is open and authenticated.             | `{ timestamp, session_id, user_type, stream_id?, tenant?, project? }`  |
| `server_shutdown` | Server is draining. Clients should reconnect. | `{ timestamp, reason: "draining", session_id, stream_id? }`            |

---

**Common Envelope (for chat events)**

All chat events (including `chat_start`, `chat_step`, `chat_delta`, `chat_complete`, `chat_error`, `chat_service`, `conv_status`) use a common envelope with a **semantic type** and context metadata.

Base envelope shape:

```json
{
  "type": "chat.step",
  "timestamp": "2026-02-26T21:14:05.267Z",
  "ts": 1700000000000,
  "service": {
    "request_id": "...",
    "tenant": "...",
    "project": "...",
    "user": "...",
    "user_type": "registered"
  },
  "conversation": {
    "session_id": "...",
    "conversation_id": "...",
    "turn_id": "..."
  },
  "event": {
    "agent": "...",
    "step": "...",
    "status": "started|running|completed|error|skipped",
    "title": "...",
    "markdown": "..."
  },
  "data": { ... },
  "delta": { ... },
  "route": "chat_service",
  "extra": { ... }
}
```

Notes:
- `timestamp` is ISO8601. `ts` is milliseconds since epoch.
- `event.markdown` is optional; when present, the server already formatted the content.
- `route` is used mainly by service‑level events (`chat_service`).
- `extra` is used primarily by `chat.delta` (artifact metadata).

---

**SSE Event Names (Transport Routes)**

The server emits these **SSE event names**:

| SSE event       | Payload `type`                                  | Purpose                                       |
|-----------------|-------------------------------------------------|-----------------------------------------------|
| `chat_start`    | `chat.start`                                    | Turn started.                                 |
| `chat_step`     | `chat.step` **or any custom type**              | Structured step update.                       |
| `chat_delta`    | `chat.delta`                                    | Streaming chunks (answer/thinking/artifacts). |
| `chat_complete` | `chat.complete`                                 | Turn completed.                               |
| `chat_error`    | `chat.error`                                    | Turn failed.                                  |
| `chat_service`  | `chat.service` or `gateway.*` or `rate_limit.*` | Service‑level events.                         |
| `conv_status`   | `conv.status`                                   | Conversation status snapshot.                 |

Important:
- Many semantic event types ride on the `chat_step` route. Always inspect `env.type`.

---

**Core Event Types**

These are emitted by the default workflow and are stable across bundles.

| `env.type`      | Route           | Meaning                      | Key fields                                                      |
|-----------------|-----------------|------------------------------|-----------------------------------------------------------------|
| `chat.start`    | `chat_start`    | Turn accepted.               | `data.message`, `data.queue_stats`                              |
| `chat.step`     | `chat_step`     | Generic step status.         | `event.step`, `event.status`, `event.title`, `data`             |
| `chat.delta`    | `chat_delta`    | Stream chunk.                | `delta.text`, `delta.index`, `delta.marker`, `delta.completed?` |
| `chat.complete` | `chat_complete` | Final answer.                | `data.final_answer`, `data.followups?`, `data.selected_model?`  |
| `chat.error`    | `chat_error`    | Turn error.                  | `data.error`                                                    |
| `chat.service`  | `chat_service`  | Service‑level event.         | `event.step`, `data`                                            |
| `conv.status`   | `conv_status`   | Conversation state snapshot. | `data.state`, `data.updated_at`, `data.current_turn_id?`        |

---

**Known `chat_step` Semantic Types**

These are produced by the base workflow. Clients should treat them as `chat_step` events and interpret `env.type` + `env.event`:

| `env.type`                         | Typical `event.step`  | Meaning                                | Notes                                                                                                                            |
|------------------------------------|-----------------------|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| `chat.conversation.title`          | `conversation_title`  | Conversation title update.             | `data.title`                                                                                                                     |
| `chat.conversation.accepted`       | `chat.user.message`   | User message accepted.                 | `data.text`, `data.chars`                                                                                                        |
| `chat.conversation.turn.completed` | `plan.done`           | Turn finished.                         | `data.elapsed_ms`, `event.status`                                                                                                |
| `chat.turn.summary`                | `turn.summary`        | Timing summary.                        | `event.markdown`, `data.elapsed_ms`                                                                                              |
| `chat.followups`                   | `followups`           | Suggested follow‑ups.                  | `data.items[]`                                                                                                                   |
| `accounting.usage`                 | `accounting`          | Cost breakdown.                        | `data.breakdown`, `data.cost_total_usd`, `event.markdown`                                                                        |
| `solver.react.decision`            | `react(<n>).decision` | ReAct decision node.                   | `data` is full decision JSON                                                                                                     |
| `chat.step`                        | varies                | Internal steps (persist, graph, etc.). | `event.step` values include `conversation.persist.user_message`, `conversation.persist.assistant_message`, `context.graph`, etc. |

---

**Service‑Level Events (`chat_service`)**

These events are emitted when rate‑limits or gateway checks trigger, or for global service status.

### Rate‑limit and funding events
Emitted from the economic entrypoint and delivered on `chat_service` route.

Known `env.type` values:
- `rate_limit.warning`
- `rate_limit.denied`
- `rate_limit.no_funding`
- `rate_limit.project_exhausted`
- `rate_limit.subscription_exhausted`
- `rate_limit.post_run_exceeded`

Typical payload (`data.rate_limit` contains the snapshot):

```json
{
  "type": "rate_limit.denied",
  "event": {
    "step": "rate_limit",
    "status": "error",
    "agent": "bundle.rate_limiter",
    "title": "Rate limit exceeded"
  },
  "data": {
    "rate_limit": {
      "messages_remaining": 0,
      "retry_after_hours": 2,
      "snapshot": { ... }
    },
    "bundle_id": "...",
    "user_type": "registered"
  }
}
```

### Gateway rejections
Ingress may emit gateway errors **before** enqueueing a task:

Known `env.type` values:
- `gateway.rate_limit`
- `gateway.backpressure`
- `gateway.circuit_breaker`
- `gateway.error`

These are also delivered on `chat_service` with:

```json
{
  "event": {"step":"gateway","status":"error","agent":"gateway"},
  "data": {"message":"...","http_status":429,"endpoint":"/sse/chat"}
}
```

---

**Streaming Deltas (`chat.delta`)**

`chat.delta` is the primary mechanism for streaming text and artifacts.  
Each chunk has:

```json
"delta": {
  "text": "...",
  "marker": "answer",
  "index": 12,
  "completed": false
}
```

Additional metadata is in `extra`:

```json
"extra": {
  "format": "markdown|text|html|json",
  "artifact_name": "...",
  "title": "...",
  "sub_type": "...",
  "execution_id": "...",
  "search_id": "...",
  "language": "python"
}
```

### Delta markers

| Marker          | Meaning                    | Typical usage                 |
|-----------------|----------------------------|-------------------------------|
| `answer`        | Assistant response stream  | main answer text              |
| `thinking`      | Reasoning stream           | internal analysis or plans    |
| `canvas`        | Artifact stream            | documents, rendered content   |
| `timeline_text` | Timeline stream            | timeline panel / activity log |
| `subsystem`     | Structured JSON payloads   | widgets + tools               |
| `tool`          | legacy alias               | historical web‑search UI      |

### Canvas / artifact streaming
Canvas chunks are usually emitted with:
- `marker = canvas`
- `extra.format` (markdown/html/json)
- `extra.artifact_name` (stable ID for UI grouping)

### Subsystem streaming (`marker = subsystem`)
Subsystem streams emit **JSON payloads** inside `delta.text` and are distinguished by `extra.sub_type`.

Known subsystem sub‑types:

| `extra.sub_type`              | Description               | Payload (in `delta.text`)         |
|-------------------------------|---------------------------|-----------------------------------|
| `conversation.turn.status`    |  Turn work status updates | `{ status, timestamp }`           |
| `web_search.filtered_results` | Web search results        | `{ results, objective, queries }` |
| `web_search.html_view`        | Web search report         | HTML string                       |
| `web_fetch.results`           | URL fetch output          | JSON payload                      |
| `code_exec.objective`         | Code execution objective  | text                              |
| `code_exec.program.name`      | Executable name           | text                              |
| `code_exec.code`              | Code stream               | code text                         |
| `code_exec.contract`          | Execution contract        | JSON array of files/outputs       |
| `code_exec.status`            | Execution status          | `{ status, timings, error? }`     |

---

**Conversation Status (`conv_status`)**

This event is emitted by `/sse/conv_status.get` and mirrors server‑side state:

```json
{
  "type": "conv.status",
  "event": {"step": "conv.state", "status": "idle|in_progress|error|created"},
  "data": {
    "state": "idle|in_progress|error|created",
    "updated_at": "...",
    "current_turn_id": "turn_...",
    "completion": "..."  // optional
  }
}
```

---

**Client Integration (EventSource)**

The reference client binds to SSE events as follows:

```ts
const es = new EventSource(`${base}/sse/stream?...`);

es.addEventListener("ready", onReady);

es.addEventListener("chat_start", onChatStart);
es.addEventListener("chat_step", onChatStep);
es.addEventListener("chat_delta", onChatDelta);
es.addEventListener("chat_complete", onChatComplete);
es.addEventListener("chat_error", onChatError);
es.addEventListener("chat_service", onChatService);
es.addEventListener("conv_status", onConvStatus);
```

The client should:
- Parse JSON from `e.data`.
- Route by `env.type` for semantic handling.
- Use `delta.marker` to fan out to UI channels.
- Respect `delta.completed` to close streams per channel/artifact.
- Reconnect on `server_shutdown`.

See implementation:  
`ChatService.ts` in your UI repo.

---

**Extensibility**

Bundles can emit new event types via `comm.event(...)` and `comm.service_event(...)`.  
If your client does not recognize a type:
- Treat it as a `chat_step` payload.
- Use `event.step`, `event.status`, and `event.markdown` for display.

Custom event filtering can be applied per bundle (see `sdk/comm/event_filter.py`).

---

**Quick Reference**

- SSE route name = transport channel (`chat_step`, `chat_delta`, etc.)
- Payload `type` = semantic event type (e.g., `chat.turn.summary`, `rate_limit.denied`)
- `marker` = streaming channel hint (`answer`, `thinking`, `canvas`, `subsystem`, ...)
- `extra` = artifact metadata

---

**Appendix: Auto‑Generated Catalog**

This appendix is generated from the codebase so it stays in sync with new events.

Run:

```bash
python kdcube-ai-app/app/ai-app/docs/clients/scripts/gen_sse_event_catalog.py --write
```

<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_START -->
**Generated Event Catalog (static scan)**

SSE route names:
- `chat_complete`
- `chat_delta`
- `chat_error`
- `chat_service`
- `chat_start`
- `chat_step`
- `conv_status`
- `ready`
- `server_shutdown`

Event `type` values:
- `accounting.usage`
- `analytics.rate_limit.post_run_exceeded`
- `assistant.completion`
- `chat.attachments`
- `chat.citations`
- `chat.complete`
- `chat.delta`
- `chat.error`
- `chat.exec_report`
- `chat.exec_timeline`
- `chat.files`
- `chat.followups`
- `chat.start`
- `chat.step`
- `checkbox`
- `conv.range.summary`
- `economics.user_underfunded_absorbed`
- `inline`
- `rate_limit.ai_services_quota`
- `rate_limit.attachment_failure`
- `rate_limit.lane_switch`
- `rate_limit.warning`
- `react.tool.call`
- `react.tool.result`
- `solver.react.decision`
- `turn.feedback`
- `turn.header`
- `user.attachment`
- `user.attachment.meta`
- `user.attachment.text`
- `user.prompt`

Delta markers:
- `answer`
- `canvas`
- `subsystem`
- `thinking`
- `timeline_text`
- `tool`

Subsystem `sub_type` values:
- `code_exec.contract`
- `code_exec.objective`
- `code_exec.program.name`
- `code_exec.status`
- `web_search`
- `web_search.filtered_results`
- `web_search.html_view`
<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_END -->

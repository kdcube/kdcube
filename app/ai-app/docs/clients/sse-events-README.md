---
id: ks:docs/clients/sse-events-README.md
title: "SSE Events"
summary: "SSE protocol reference: envelope, lifecycle events, continuation acknowledgements, and full event catalog from ingress, proc, and bundles."
tags: ["clients", "sse", "protocol", "events", "streaming", "chat"]
keywords: ["event envelope", "chat_step", "delta", "server_shutdown", "conv_status", "rate_limit", "backpressure", "bundle events", "followup", "steer", "continuation"]
see_also:
  - ks:docs/clients/README.md
  - ks:docs/clients/client-communication-README.md
  - ks:docs/clients/frontend-awareness-on-service-state-README.md
  - ks:docs/service/comm/comm-system.md
  - ks:docs/arch/architecture-long.md
  - ks:docs/economics/rate-limit-simulation-playbook.md
---
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

**`POST /sse/chat` synchronous acknowledgement**

`POST /sse/chat` returns JSON immediately, before the turn necessarily starts on proc.

Known `status` values:

| `status` | Meaning |
| --- | --- |
| `processing_started` | A regular turn was admitted to the normal proc ready queue. |
| `followup_accepted` | The conversation was already busy; the message was accepted into the shared per-conversation external event source as a followup. |
| `steer_accepted` | The conversation was already busy; the message was accepted into the shared per-conversation external event source as a steer/control message. |

Important:

- `followup_accepted` / `steer_accepted` do **not** mean that a new proc turn started immediately.
- They mean the message is accepted into ordered shared storage for that conversation.
- A live React turn may consume it while still running.
- A consumed `followup` stays on the current turn and can affect the next decision boundary.
- A consumed `steer` is a control interrupt and stops the current turn at the next safe runtime checkpoint.
- If no live turn consumes it, proc may later promote it into the normal ready queue after the current turn ends.

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
| `chat_service`  | `chat.service` or `gateway.*` or `rate_limit.*` or `queue.*` | Service‑level events.                         |
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
| `chat.error`    | `chat_error`    | Turn error.                  | `data.error`, `data.error_type?`, `data.reason?`, `data.task_id?` |
| `chat.service`  | `chat_service`  | Service‑level event.         | `event.step`, `data`                                            |
| `conv.status`   | `conv_status`   | Conversation state snapshot. | `data.state`, `data.updated_at`, `data.current_turn_id?`, `data.completion?` such as `queued_next` or `interrupted` |

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
- `rate_limit.warning` — quota is approaching its limit (request still allowed)
- `rate_limit.denied` — request blocked before execution (quota or concurrency limit exceeded)
- `rate_limit.no_funding` — no funding source available for this user type
- `rate_limit.project_exhausted` — project budget exhausted
- `rate_limit.subscription_exhausted` — subscription balance exhausted
- `rate_limit.post_run_exceeded` — request completed but actual token usage exceeded the limit (next requests will be blocked)
- `rate_limit.lane_switch` — informational; user silently switched from plan lane to paid lane


**`data.rate_limit` shape** (present on `warning`, `denied`, `post_run_exceeded`):

```json
{
  "messages_remaining": 0,
  "total_token_remaining": 0,
  "usage_percentage": 100.0,
  "retry_after_sec": 3247,
  "retry_after_hours": 1,
  "retry_scope": "hour | day | month | total",
  "reset_text": "tomorrow at 8:42 PM",
  "user_message": "You've reached your usage limit. Your quota resets tomorrow at 8:42 PM.",
  "notification_type": "error | warning | info",
  "violations": ["tokens_per_hour"],
  "limits": { "tokens_per_hour": 18000, "tokens_per_day": 333333 },
  "remaining": { "tokens_per_hour": 0, "tokens_per_day": 12000 },
  "snapshot": { ... }
}
```

Notes:
- `reset_text` — human-readable reset time formatted in the user's timezone (e.g. `"today at 9:32 PM"`). Generated server-side using `comm_context.user.timezone`.
- `user_message` — complete human-readable message ready for display. Clients should prefer this over constructing their own message. Falls back to client-side construction if absent.
- `notification_type` — intended display severity. `rate_limit.denied` defaults to `"error"`, `rate_limit.post_run_exceeded` defaults to `"warning"`.
- `retry_after_sec` — seconds until quota resets. More precise than `retry_after_hours`.

**Full `rate_limit.denied` envelope example:**

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
    "reason": "tokens_per_hour",
    "bundle_id": "...",
    "user_type": "free",
    "lane": "deny",
    "rate_limit": {
      "messages_remaining": 5,
      "total_token_remaining": 0,
      "retry_after_sec": 3247,
      "retry_after_hours": 1,
      "retry_scope": "hour",
      "reset_text": "today at 9:32 PM",
      "user_message": "You've reached your usage limit. Your quota resets today at 9:32 PM.",
      "notification_type": "error",
      "violations": ["tokens_per_hour"],
      "snapshot": { ... }
    }
  }
}
```

**Special case — `quota_lock_timeout`:**

When a Redis quota lock cannot be acquired within 5 seconds, `user_message` is placed directly on `data` (not inside `data.rate_limit`), since no rate_limit snapshot is available at that point:

```json
{
  "type": "rate_limit.denied",
  "data": {
    "reason": "quota_lock_timeout",
    "user_message": "Too many requests are being processed right now. Please try again in a moment.",
    "lane": "deny"
  }
}
```
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

### Continuation accepted while conversation is busy

Ingress may accept a new message for a conversation that is already `in_progress` and store it in the per-conversation shared external event source instead of rejecting it.

Known `env.type` value:
- `queue.continuation.accepted`

Typical payload:

```json
{
  "type": "queue.continuation.accepted",
  "event": {
    "step": "queue.continuation",
    "status": "completed",
    "agent": "ingress",
    "title": "Continuation accepted"
  },
  "data": {
    "message_kind": "followup",
    "active_turn_id": "turn_active",
    "queued_turn_id": "turn_next",
    "task_id": "task_123",
    "continuation_queue_size": 2,
    "continuation_message_id": "cont_abc123"
  }
}
```

Meaning:

- the active turn is still running
- this message was accepted into the ordered conversation external-event source
- it is not yet a new `chat.start`
- a live React turn may consume it during the active turn
- otherwise proc will later promote the next accepted event into the normal ready queue

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

### Queued-next contract

If the active bundle does not consume accepted external events itself, proc may promote the oldest pending event into the normal ready queue after the current turn completes. In that case the client may see:

- `conv_status` where:
  - `data.state = "in_progress"`
  - `data.completion = "queued_next"`

Meaning:

- the previous turn finished
- the next continuation message for the same conversation has been promoted into the normal proc queue
- the next turn may be claimed by the same proc worker or by a different one
- `queued_next` is not a failure and not an interruption signal

Example:

```json
{
  "type": "conv.status",
  "event": {"step": "conv.state", "status": "in_progress"},
  "data": {
    "state": "in_progress",
    "updated_at": "2026-03-16T14:02:11.884Z",
    "current_turn_id": "turn_next",
    "completion": "queued_next"
  }
}
```

### Interrupted turn contract

If proc started a turn but lost the worker before the turn completed, the server does not auto-replay that request. Instead it signals interruption to the client with:

- `conv_status` where:
  - `data.state = "error"`
  - `data.completion = "interrupted"`
- `chat_error` where:
  - `data.error_type = "turn_interrupted"`
  - `data.reason` is a machine-readable cause such as `worker_lost_after_start`
  - `data.task_id` identifies the interrupted task

Example `conv_status`:

```json
{
  "type": "conv.status",
  "event": {"step": "conv.state", "status": "error"},
  "data": {
    "state": "error",
    "updated_at": "2026-03-16T13:42:19.331Z",
    "current_turn_id": "turn_123",
    "completion": "interrupted"
  }
}
```

Example `chat_error`:

```json
{
  "type": "chat.error",
  "data": {
    "error": "Turn interrupted before completion (worker_lost_after_start).",
    "error_type": "turn_interrupted",
    "reason": "worker_lost_after_start",
    "task_id": "task_123"
  }
}
```

Client guidance:
- Keep any `chat_delta` content already rendered.
- Mark the active turn as interrupted/failed.
- Offer manual retry/resubmit if appropriate.
- Do not assume the backend will replay the request automatically.

Client guidance for continuation acceptance:
- Treat `followup_accepted` / `steer_accepted` as admission acknowledgements, not as turn-start confirmations.
- Keep the UI bound to the same conversation; do not assume a second parallel turn started.
- Use `queue.continuation.accepted` to show that the message is queued behind or alongside the active turn.
- If a later `conv_status` arrives with `completion = "queued_next"`, interpret that as "the next continuation has been promoted for normal processing".

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
- Treat `chat_error.error_type = "turn_interrupted"` plus `conv_status.data.completion = "interrupted"` as a terminal interrupted-turn state, not as a reconnect instruction.

See implementation:  
`ChatService.ts` in your UI repo.

---

**Extensibility**

Bundles can emit new event types via `comm.event(...)` and `comm.service_event(...)`.  
If your client does not recognize a type:
- Treat it as a `chat_step` payload.
- Use `event.step`, `event.status`, and `event.markdown` for display.

Custom event filtering can be applied per bundle (see `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/comm/event_filter.py`).

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

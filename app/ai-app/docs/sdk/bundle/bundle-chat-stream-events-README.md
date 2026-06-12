---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
title: "Bundle Chat Stream Events"
summary: "Reference catalog for chat stream events visible to bundle-facing clients: shared event envelope and lifecycle across SSE and Socket.IO, plus continuation acknowledgements, rate limits, backpressure, and bundle-emitted events."
tags: ["sdk", "bundle", "sse", "socketio", "protocol", "events", "streaming", "chat"]
keywords: ["chat stream event catalog", "socketio event catalog", "sse event catalog", "client visible event lifecycle", "continuation acknowledgements", "chat step events", "rate limit events", "backpressure events", "bundle emitted events", "stream protocol reference"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-frontend-awareness-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-long.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/rate-limit-simulation-playbook.md
---
# Bundle Chat Stream Events

This document describes the **shared chat stream event protocol** used by the chat system.

It covers:

- the semantic event envelope shared by:
  - SSE
  - Socket.IO
- the synchronous chat-send acknowledgements
- the default event families emitted by ingress + processor + bundles
- the places where SSE framing differs from Socket.IO event delivery

It is intended for:
- Frontend developers integrating the chat UI.
- Adapter developers (Telegram/Slack/etc.) who need to consume the stream.
- Anyone building a sniffer/aggregator for chat activity.

For bundle code that records selected comm events and sends bounded batches to
a sink, use
[Bundle Event Recording And Sinks](bundle-event-recording-and-sinks-README.md).

It reflects the current server implementation (ingress + processor split) and the default client implementation in `ChatService.ts`.

**Important**
- The transport route name (for example `chat_step`) is **not** the same as `env.type` inside the JSON payload.
- The route name is transport-level routing. The payload `type` is the semantic event type.
- Bundles may emit **custom event types**, so clients must treat unknown types as generic `chat_step` events unless they explicitly support them.
- In the processor request path, successful `ChatCommunicator` emissions also
  refresh the active task idle watchdog. Long-running SDK internals may refresh
  the same watchdog through an internal activity hook without emitting a
  synthetic stream event.

---

## Transport scope

The same semantic event envelope is delivered over two browser transports:

- **SSE**
  - `GET /sse/stream`
  - `POST /sse/chat`
  - `POST /sse/conv_status.get`
- **Socket.IO**
  - `connect`
  - `chat_message`
  - server-emitted `ready`, `chat_start`, `chat_step`, `chat_delta`, `chat_compaction`, `chat_complete`, `chat_error`, `chat_service`, `conv_status`

The main difference is:

- SSE uses wire framing with `event:` and `data:`
- Socket.IO uses named events carrying the same semantic JSON envelopes

For connection/auth/send details, use:

- [bundle-client-communication-README.md](bundle-client-communication-README.md)

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
- The ack's `turn_id` is the fallback queued turn id for that submission, not proof that the UI should render a new turn.
- `target_turn_id` is the client/user's intended turn. It is advisory and may be stale.
- `active_turn_id` is the server-observed active turn at ingress time. It is the preferred same-turn render target when `live_owner_detected !== false`.
- `event_id` and `external_event_sequence` identify the durable external event.
- A live React turn may consume it while still running.
- A consumed `followup` stays on the current turn and can affect the next decision boundary.
- A consumed `steer` is a control interrupt. Engineering first tries to cancel the active generation or cancellable tool phase, then React gets a short finalize phase on that same turn.
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

Socket.IO does not use SSE framing.
It emits named events carrying the same envelope payloads.

---

**Connection‑Level Events**

These events are **not** part of the chat envelope. They are transport signals from ingress.

| Stream event      | Purpose                                       | Payload (shape)                                                        |
|-------------------|-----------------------------------------------|------------------------------------------------------------------------|
| `ready`           | Stream is open and authenticated.             | `{ timestamp, session_id, user_type, stream_id?, tenant?, project? }`  |
| `server_shutdown` | Server is draining. Clients should reconnect. | `{ timestamp, reason: "draining", session_id, stream_id? }`            |

---

**Common Envelope (for chat events)**

All chat events (including `chat_start`, `chat_step`, `chat_delta`, `chat_compaction`, `chat_complete`, `chat_error`, `chat_service`, `conv_status`) use a common envelope with a **semantic type** and context metadata.

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

**Transport Event Names (Routes)**

The server emits these transport event names:

| Transport event | Payload `type`                                  | Purpose                                       |
|-----------------|-------------------------------------------------|-----------------------------------------------|
| `chat_start`    | `chat.start`                                    | Turn started.                                 |
| `chat_step`     | `chat.step` **or any custom type**              | Structured step update.                       |
| `chat_delta`    | `chat.delta`                                    | Streaming chunks (answer/thinking/artifacts). |
| `chat_compaction` | `chat.compaction`                             | ReAct context compaction lifecycle.           |
| `chat_complete` | `chat.complete`                                 | Turn completed.                               |
| `chat_error`    | `chat.error`                                    | Turn failed.                                  |
| `chat_service`  | `chat.service` or `gateway.*` or `rate_limit.*` or `queue.*` | Service‑level events.                         |
| `conv_status`   | `conv.status`                                   | Conversation status snapshot.                 |

Important:
- Many semantic event types ride on the `chat_step` route. Always inspect `env.type`.
- `chat.compaction` has its own `chat_compaction` route so browser clients and adapters can render compaction start/completion without guessing from generic progress.
- The same route names are used for both SSE and Socket.IO.

---

**Core Event Types**

These are emitted by the default workflow and are stable across bundles.

| `env.type`      | Route           | Meaning                      | Key fields                                                      |
|-----------------|-----------------|------------------------------|-----------------------------------------------------------------|
| `chat.start`    | `chat_start`    | Turn accepted.               | `data.message`, `data.queue_stats`                              |
| `chat.step`     | `chat_step`     | Generic step status.         | `event.step`, `event.status`, `event.title`, `data`             |
| `chat.delta`    | `chat_delta`    | Stream chunk.                | `delta.text`, `delta.index`, `delta.marker`, `delta.completed?` |
| `chat.compaction` | `chat_compaction` | Context compaction progress. | `event.status`, `data.kind`, `data.compaction_id`, `data.before_tokens`, `data.after_tokens`, `data.compacted_tokens` |
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

**Conversation Title (`conversation_title`)**

The backend auto-names a conversation **while the turn is still running** — usually
right after the first user message of a new conversation. It announces the name as
a step event so the client can update the conversation header live, instead of
waiting until the turn completes.

- It rides the `chat_step` route (some deployments also echo it on `chat_delta`),
  so **match on `event.step`, not on `env.type`**.
- `env.type = "chat.conversation.title"`, `event.step = "conversation_title"`,
  `event.status = "completed"`.
- The name is in `data.title`.

Envelope:

```json
{
  "type": "chat.conversation.title",
  "route": "chat_step",
  "conversation": { "conversation_id": "conv_123", "turn_id": "turn_123" },
  "event": { "step": "conversation_title", "status": "completed", "title": "Conversation named" },
  "data": { "title": "Inspecting a zip and generating an Excel report" }
}
```

Client guidance:

- When `event.step === "conversation_title"`, set the conversation title from
  `data.title` **immediately**, and do not render it as a visible timeline/step
  entry.
- Apply it live; do **not** wait for `chat_complete` or for a conversations-list
  refresh to surface the name. The stored `conversation_title` on the conversation
  record (and the list endpoint) are a fallback for clients that reload history,
  not the primary signal during a live turn. Treating the list refresh as the only
  source is the common bug that leaves the header on its placeholder
  ("Untitled conversation") until the turn ends.
- A brand-new conversation has no name yet; show a placeholder until this event
  arrives.

---

**ReAct Compaction Events (`chat_compaction`)**

Long ReAct turns can hit a context budget while they are still running. When the runtime actually starts or completes a compaction pass, it emits `env.type = "chat.compaction"` on the `chat_compaction` route.

This is a progress event, not a turn-completion event. Clients should append it to the visible activity timeline or progress card and keep listening for later `chat_delta`, `chat_step`, `chat_complete`, or `chat_error` events.

Common payload fields:

| Field | Meaning |
| --- | --- |
| `event.status` | `started`, `completed`, `skipped`, or `error`. |
| `data.kind` | Compaction scope, for example `history`, `history_with_split_turn`, or `current_turn_prefix`. |
| `data.compaction_id` | Stable identifier for pairing start/completion events from one compaction pass. |
| `data.trigger_reason` | Why the pass started when known, for example `render_token_limit` or `forced`. |
| `data.input_tokens_estimate` / `data.threshold_tokens` / `data.max_tokens` | Trigger-side model input estimate and configured budget. The estimate includes system/instruction text and rendered timeline content; `threshold_tokens` is the configured `max_tokens` value. |
| `data.before_tokens` / `data.after_tokens` | Estimated visible context before and after compaction when known. |
| `data.compacted_tokens` | Estimated tokens hidden behind the compacted memory/checkpoint when known. |
| `data.before_visible_blocks` / `data.after_visible_blocks` | Diagnostic visible timeline block count before and after compaction when known. Token pressure is the compaction trigger. |
| `data.compacted_visible_blocks` | Diagnostic visible blocks removed from the rendered prompt when known. |
| `data.current_turn` / `data.split_turn` | Whether the compaction touched the in-progress turn prefix. |
| `data.reason` | Present for `skipped` or `error` cases. |

If a candidate history compaction does not reduce model-visible tokens, the
runtime reports `status = "skipped"` with
`data.reason = "no_visible_token_reduction"` and leaves the timeline unchanged.
Clients should render that as a quiet status line rather than a successful
compaction, even if the candidate would have reduced block count.

Example:

```json
{
  "type": "chat.compaction",
  "route": "chat_compaction",
  "conversation": { "conversation_id": "conv_123", "turn_id": "turn_123" },
  "event": {
    "step": "context.compaction",
    "status": "completed",
    "title": "Context compaction completed",
    "markdown": "Context compaction completed (compacted ~12,400 tokens; current turn prefix)."
  },
  "data": {
    "compaction_id": "cmp_turn_123_1",
    "kind": "current_turn_prefix",
    "before_tokens": 95609,
    "after_tokens": 48122,
    "compacted_tokens": 47487,
    "current_turn": true
  }
}
```

Adapters such as Telegram can render this as a short status line inside the in-progress card. Browser clients should bind the `chat_compaction` route explicitly.

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
    "target_turn_id": "turn_visible_to_client",
    "queued_turn_id": "turn_next",
    "task_id": "task_123",
    "continuation_message_id": "cont_abc123",
    "external_event_sequence": 42,
    "live_owner_detected": false
  }
}
```

Meaning:

- the active turn is still running
- this message was accepted into the ordered conversation external-event source
- `queued_turn_id` / ack `turn_id` is a fallback task id, not a visible turn-start confirmation
- `continuation_message_id` / ack `event_id` is the durable external event id and should be used for client dedupe when present
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

`delta.marker` is an open string, not a closed enum. Platform clients know the
markers below; custom markers are allowed but need explicit client support.

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
- Do not render a new turn from the synchronous ack alone.
- Render an immediate same-turn followup bubble only when `live_owner_detected !== false`; attach it to `active_turn_id || target_turn_id`.
- Dedupe immediate continuation bubbles by `event_id || queued_turn_id || turn_id`.
- Use `queue.continuation.accepted` to show that the message is stored behind or alongside the active turn.
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

**Socket.IO client integration**

Socket.IO clients subscribe to the same route names as named socket events:

```ts
socket.on("ready", onReady);
socket.on("chat_start", onChatStart);
socket.on("chat_step", onChatStep);
socket.on("chat_delta", onChatDelta);
socket.on("chat_complete", onChatComplete);
socket.on("chat_error", onChatError);
socket.on("chat_service", onChatService);
socket.on("conv_status", onConvStatus);
```

The handler logic should be the same:

- parse the envelope
- route by `env.type`
- use `delta.marker` for channel-specific UI handling

Only the transport framing is different.

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
python kdcube-ai-app/app/ai-app/docs/sdk/bundle/scripts/gen_sse_event_catalog.py --write
```

<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_START -->
**Generated Event Catalog (static scan)**

SSE route names:
- `chat_compaction`
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
- `assistant.completion.attempt`
- `chat.attachments`
- `chat.citations`
- `chat.compaction`
- `chat.complete`
- `chat.conversation.accepted`
- `chat.conversation.title`
- `chat.conversation.turn.completed`
- `chat.delta`
- `chat.error`
- `chat.exec_report`
- `chat.exec_timeline`
- `chat.files`
- `chat.followups`
- `chat.start`
- `chat.step`
- `chat.turn.summary`
- `conv.range.summary`
- `conv.status`
- `conv.working.summary`
- `economics.user_underfunded_absorbed`
- `queue.continuation.accepted`
- `queue.enqueue_rejected`
- `rate_limit.ai_services_quota`
- `rate_limit.attachment_failure`
- `rate_limit.lane_switch`
- `rate_limit.warning`
- `react.current_turn.compaction_checkpoint`
- `react.decision.raw`
- `react.exit`
- `react.note`
- `react.notes`
- `react.notice`
- `react.plan`
- `react.plan.ack`
- `react.plan.active`
- `react.plan.history`
- `react.pruned.turn_status`
- `react.round.start`
- `react.rounds.compacted`
- `react.skill.read`
- `react.state`
- `react.thinking`
- `react.tool.call`
- `react.tool.code`
- `react.tool.result`
- `react.turn.finalize`
- `react.workspace.checkout`
- `react.workspace.publish`
- `solver.react.action`
- `solver.react.decision`
- `timeline.external.accepted`
- `timeline.external.steer.cancel_requested`
- `timeline.external.steer.interrupted`
- `turn.feedback`
- `turn.header`
- `user.attachment`
- `user.attachment.meta`
- `user.attachment.text`
- `user.followup`
- `user.prompt`

Delta markers:
- `answer`
- `canvas`
- `subsystem`
- `thinking`
- `timeline_text`

Subsystem `sub_type` values:
- `code_exec.contract`
- `code_exec.objective`
- `code_exec.program.name`
- `code_exec.status`
- `web_search`
- `web_search.filtered_results`
- `web_search.html_view`
<!-- AUTO-GENERATED: SSE_EVENT_CATALOG_END -->

---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
title: "Comm"
summary: "Entry point for communication transports and integrations."
tags: ["service", "comm", "transports", "sse", "socketio"]
keywords: ["SSE", "Socket.IO", "REST", "relay", "transports", "data bus"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-recording-event-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/telemetry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-firewall-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
---
# Communication Integrations (External + Internal)

This README is the entry point for **communication integrations**:

- **External transports**: HTTP (REST), SSE, Socket.IO
- **Auth/session handling**: token sources, anonymous vs registered, role-based policy
- **Attachments**: REST/SSE multipart and Socket.IO binary frames
- **Internal relay**: Redis Pub/Sub fan-out (`ServiceCommunicator` + `ChatRelayCommunicator`)
- **Tenant/project SSE events**: opt-in project-scoped service updates for
  compact cross-session UI refreshes
- **Data Bus**: durable, bundle-scoped inbound message path for
  non-conversation domain state changes, such as collaborative board patches or
  issue updates, routed through separate Redis Streams instead of the chat turn
  queue
- **Comm recording and event sinks**: optional recording of selected comm
  envelopes and bounded batch dispatch to telemetry or other configured sinks
- **Runtime context crossing**: when comm-backed execution crosses into tools,
  subprocesses, or ISO runtime, the platform uses the portable context room
  documented in [Cross-Runtime Context](../../runtime/cross-runtime-context-README.md)

If you are implementing a UI, API client, or a new transport, start here.

For how conversation `external_events[]` and Data Bus `messages[]` fit
together, read
[Conversation Event Bus And Data Bus](conversation-event-bus-and-data-bus-README.md).
For routing keys and partitioning, read
[Bus Routing And Partitioning](bus-routing-and-partitioning-README.md).

---

## 1) External transports (client-facing)

### A) REST (non-streaming)

- Use standard HTTP requests for non-streaming endpoints (e.g. profile, admin, KB, etc.).
- Auth is accepted via headers or cookies (see **Auth & token sources** below).

> Note: Chat streaming is handled via **SSE** or **Socket.IO**. REST is primarily used for non-streaming APIs.

### B) SSE (stream + send)

**Stream**
- Endpoint: `/sse/stream`
- Purpose: open a server-to-client stream for async events.
- Key params:
  - `stream_id` (client-provided unique id)
  - `user_session_id` (optional; reuse an existing session)
  - `bearer_token`, `id_token` (optional; can upgrade anonymous)
  - `tenant`, `project` (optional tenant/project override)
  - `project_events=true` (optional subscription to tenant/project-level
    service events)

**Send**
- Endpoint: `/sse/chat`
- Purpose: enqueue a user message + attachments.
- Body:
  - JSON (application/json) **or**
  - multipart/form-data for attachments

**Code references**
- SSE transport: [sse/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py)
- SSE relay: [CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md](CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md)

### C) Socket.IO (stream + send)

**Connect**
- Event: `connect` (Socket.IO handshake)
- Auth payload may include:
  - `user_session_id`
  - `bearer_token`
  - `id_token`
  - `tenant`, `project`

**Send**
- Event: `chat_message`
- Payload: one JSON event submission object with top-level `external_events[]`
  and optional metadata, followed by optional binary frames for attachments.
  Do not wrap this object under a nested `message` key.

**Code reference**
- Socket.IO transport: [socketio/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py)

---

## 2) Auth & token sources (all transports)

The gateway/auth adapters accept tokens from multiple sources, so clients can choose the best fit.

### Access token (auth)
- Header: `Authorization: Bearer <access_token>`
- Cookie: `__Secure-LATC` (configurable via `AUTH_TOKEN_COOKIE_NAME`)
- SSE query param: `bearer_token`
- Socket.IO auth payload: `bearer_token`

### ID token
- Header: `X-ID-Token` (configurable via `ID_TOKEN_HEADER_NAME`)
- Cookie: `__Secure-LITC` (configurable via `ID_TOKEN_COOKIE_NAME`)
- SSE query param: `id_token`
- Socket.IO auth payload: `id_token`

### Precedence
1) Explicit transport payload (headers or Socket.IO auth payload)
2) Query params (SSE only)
3) Cookies (fallback)

**Server entrypoints**
- Gateway adapter: [middleware/gateway.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/gateway.py)
- Auth adapter: [middleware/auth.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/auth.py)
- Socket/SSE helpers: [middleware/token_extract.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/middleware/token_extract.py)

---

## 3) Session handling & role-based policy

### Anonymous vs registered
- Sessions start as **anonymous** unless upgraded by tokens.
- SSE `/stream` and WS `connect` both perform optional token upgrades.
- You can enforce a hard block for anonymous users via:
  - `CHAT_SSE_REJECT_ANONYMOUS=1`
  - `CHAT_WS_REJECT_ANONYMOUS=1`

### Session upgrade
- If `bearer_token` and/or `id_token` are present, the session is upgraded to:
  - `registered` or `privileged` depending on roles.

**Upgrade implementation**
- [ingress/ingress_core.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/ingress_core.py)

---

## 4) Attachments

### SSE / REST (multipart)
- `/sse/chat` supports `multipart/form-data`:
  - `event_submission`: JSON string with `external_events[]`
  - `files`: binary file parts, ordered to match `event.user.attachment.*`
    entries

### Socket.IO (binary frames)
- `chat_message` payload:
  - first argument is the event submission with `external_events[]`
  - the event submission is the top-level Socket.IO argument; nested
    `{ "message": ... }` wrappers are not accepted
  - binary frames follow the JSON payload, one per `event.user.attachment.*`
    event

**Code references**
- SSE attachments: [sse/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py)
- Socket.IO attachments: [socketio/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py)

---

## 5) Internal relay (Redis)

All transports subscribe to the same internal event bus. Normal chat and
operation replies use **session-scoped** Redis channels. SSE clients may also
opt into a separate tenant/project channel for compact project-level updates.

### Components
- **Service relay**: `ServiceCommunicator` (low-level Pub/Sub)
- **Chat relay**: `ChatRelayCommunicator` (session channels + refcounting)
- **Chat communicator**: producer API for bundles

### Why this matters
- Per-session Redis channels prevent every server from receiving all events.
- The relay subscribes only when at least one active connection for that session exists.
- Tenant/project channels are opt-in and are intended for compact, debounced
  service updates such as dashboard snapshots. They are not raw telemetry or
  log streams.
- The same producer-facing `ChatCommunicator` path can optionally record
  selected, privacy-filtered envelopes and dispatch bounded batches to telemetry
  or other configured sinks.

**Docs**
- System overview: [comm-system.md](comm-system.md)
- SSE relay deep dive: [CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md](CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md)

### Data Bus boundary

The relay above is for client-visible comm envelopes: chat output, direct
operation replies, session broadcast, and compact project updates.

The **Data Bus** is a different path. It accepts durable bundle-domain
messages from clients or services, writes them to bundle-scoped Redis Streams,
and lets the bundle handle them with registered handlers. Use it when the
message changes bundle state and must be processed even if no browser is
currently listening. Do not model those messages as chat turns or
`external_events[]` unless the bundle explicitly bridges the result into a
conversation later.

Data Bus producers can be browser Socket.IO clients or server-side bundle
runtimes. Bundle entrypoints, tools, and trusted isolated/generated-code
runtimes use `comm.data_bus.publish(...)` or `comm_ctx.data_bus_publish(...)`
to write into the same stream and handler path.

Runtime entry points:

- SDK contract and decorator re-export:
  [sdk/data_bus.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/data_bus.py)
- Runtime types/streams/worker:
  [sdk/runtime/data_bus/](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/data_bus)
- Socket.IO package handler:
  [socketio/data_bus/publish.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/data_bus/publish.py)

Socket.IO `data_bus.publish` and HTTP `POST /sse/data_bus.publish` accept a
package with `bundle_id` and `messages[]`; accepted messages are written to:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
```

It does not call `/sse/chat`, `chat_message`, `process_chat_message`, or the
conversation external-event machinery.

See [data-bus-README.md](data-bus-README.md).

---

## 6) Event families (what the client receives)

Standard streaming events:
- `chat_start`
- `chat_step`
- `chat_delta`
- `chat_complete`
- `chat_error`

Conversation state:
- `conv_status`

Typed UI cards:
- carried as `chat_step` with `env.type` (e.g. `chat.followups`)

## 6.1) Delta markers (bundle authors)

Use `chat.delta` with a marker to control how the client renders the stream.
Keep it simple unless you own the client UI.

Recommended markers:
- `thinking` — side-channel thoughts
- `answer` — main assistant answer

Supported by default in the platform:
- `subsystem` — subsystem/widget stream
- `canvas` — inline artifact/canvas stream
- `timeline_text` — compact timeline entries

Custom markers are allowed, but the client must know how to render them.
See [comm-system.md](comm-system.md) for the envelope details.

### Client-visible payload patterns

The platform already has well-understood rendering behavior for these shapes:

| Pattern | Transport shape | Typical use |
| --- | --- | --- |
| Main answer | `chat.delta` + `marker="answer"` | assistant answer text |
| Thinking/progress text | `chat.delta` + `marker="thinking"` | transient thought/progress stream |
| Subsystem JSON | `chat.delta` + `marker="subsystem"` + `extra.sub_type` | tool/widget-specific panels |
| Canvas artifact | `chat.delta` + `marker="canvas"` + `extra.format`/`artifact_name` | inline rendered artifact/content |
| Timeline entry | `chat.delta` + `marker="timeline_text"` | compact visible activity entries |
| Custom typed event | `chat_step` route with custom `env.type` | domain-specific semantic event |

For the client-facing contract and examples, see:
- [bundle-client-communication-README.md](../../sdk/bundle/bundle-client-communication-README.md)
- [bundle-chat-stream-events-README.md](../../sdk/bundle/bundle-chat-stream-events-README.md)

### Which method owns which concept

| Method | Main purpose | `marker` | `broadcast` |
| --- | --- | --- | --- |
| `comm.step(...)` | progress/status event | no | not exposed by the helper |
| `comm.delta(...)` | stream chunk | yes | not exposed by the helper |
| `comm.event(...)` | custom typed event | no | yes |
| `comm.service_event(...)` | service-level event | no | yes, session-scoped |
| `comm.project_event(...)` | tenant/project service event over SSE opt-in channel | no | project-scoped |
| `comm.emit(...)` | low-level socket route control | N/A | yes |

Rule of thumb:

- if you are asking “which marker should I use?”, you almost certainly want `comm.delta(...)`
- if you are asking “should this be broadcast?”, you probably want `comm.event(...)`,
  `comm.service_event(...)`, or the low-level `comm.emit(...)`
- if you need all connected SSE clients for the same tenant/project to receive
  a compact update, use `comm.project_event(...)` and require clients to open
  `/sse/stream` with `project_events=true`

---

## 7) Bundle‑level outbound firewall

Bundles can define an **event filter** (`IEventFilter`) that acts as an outbound firewall.
It sees the event metadata + user/session details and can **suppress** events before
they reach Redis/SSE/Socket.IO.

See: [docs/sdk/bundle/bundle-firewall-README.md](../../sdk/bundle/bundle-firewall-README.md)

### Examples (per marker)

```python
# thinking
await self.comm.delta(text="Working it out…", index=0, marker="thinking", agent="gate")

# answer
await self.comm.delta(text="Here is the answer.", index=0, marker="answer", agent="answer.generator")

# subsystem (widget stream)
await self.comm.delta(text='{"status":"running"}', index=0, marker="subsystem", agent="tool.exec")

# canvas (inline artifact stream)
await self.comm.delta(text='{"type":"chart","data":{...}}', index=0, marker="canvas", agent="viz")

# timeline_text (compact timeline entries)
await self.comm.delta(text="Loaded 3 prior turns", index=0, marker="timeline_text", agent="orchestrator")
```

Custom typed event example:

```python
await self.comm.event(
    type="bundle.preferences.updated",
    step="preferences.updated",
    status="completed",
    title="Preferences updated",
    data={"keys": ["city", "diet"]},
    agent="preferences",
    broadcast=True,
)
```

---

## 8) Quick client integration checklist

1) Choose **SSE** or **Socket.IO** for streaming.
2) Provide tokens via header or cookie; for SSE you may also use query params.
3) Open stream (`/sse/stream`) or connect Socket.IO.
4) Send messages via `/sse/chat` or `chat_message` event.
5) Listen for standard events (`chat_start`, `chat_step`, `chat_delta`, `chat_complete`).

---

## 9) Producer API (bundles)

If you are a bundle author, see:
- [comm-system.md](comm-system.md) (producer API + filters)
- [comm-recording-event-sinks-README.md](comm-recording-event-sinks-README.md) (recording selected comm envelopes and dispatching batches to event sinks)
- [emitters.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/comm/emitters.py) and
  [agentic_app (proc web_app.py)](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py)

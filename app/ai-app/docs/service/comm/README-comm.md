---
id: ks:docs/service/comm/README-comm.md
title: "Comm"
summary: "Entry point for communication transports and integrations."
tags: ["service", "comm", "transports", "sse", "socketio"]
keywords: ["SSE", "Socket.IO", "REST", "relay", "transports"]
see_also:
  - ks:docs/service/comm/CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/sdk/bundle/bundle-firewall-README.md
  - ks:docs/clients/client-communication-README.md
  - ks:docs/clients/sse-events-README.md
---
# Communication Integrations (External + Internal)

This README is the entry point for **communication integrations**:

- **External transports**: HTTP (REST), SSE, Socket.IO
- **Auth/session handling**: token sources, anonymous vs registered, role-based policy
- **Attachments**: REST/SSE multipart and Socket.IO binary frames
- **Internal relay**: Redis Pub/Sub fan-out (`ServiceCommunicator` + `ChatRelayCommunicator`)

If you are implementing a UI, API client, or a new transport, start here.

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
- Payload: JSON (message + metadata) + optional binary frames for attachments

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
- [ingress/chat_core.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/chat_core.py)

---

## 4) Attachments

### SSE / REST (multipart)
- `/sse/chat` supports `multipart/form-data`:
  - `message`: JSON string
  - `attachment_meta`: JSON string (array)
  - `files`: binary file parts

### Socket.IO (binary frames)
- `chat_message` payload:
  - `attachment_meta`: array of `{filename, mime, ...}`
  - binary frames follow the JSON payload (one per attachment)

**Code references**
- SSE attachments: [sse/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py)
- Socket.IO attachments: [socketio/chat.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/chat.py)

---

## 5) Internal relay (Redis)

All transports subscribe to the same internal event bus via **session-scoped** Redis channels.

### Components
- **Service relay**: `ServiceCommunicator` (low-level Pub/Sub)
- **Chat relay**: `ChatRelayCommunicator` (session channels + refcounting)
- **Chat communicator**: producer API for bundles

### Why this matters
- Per-session Redis channels prevent every server from receiving all events.
- The relay subscribes only when at least one active connection for that session exists.

**Docs**
- System overview: [comm-system.md](comm-system.md)
- SSE relay deep dive: [CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md](CHAT-RELAY-SESSION-SUBSCR-SSE-SOCKETIO-FUNOUT.README.md)

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
- [docs/clients/client-communication-README.md](../../clients/client-communication-README.md)
- [docs/clients/sse-events-README.md](../../clients/sse-events-README.md)

---

## 7) Bundle‑level outbound firewall

Bundles can define an **event filter** (`IEventFilter`) that acts as an outbound firewall.
It sees the event metadata + user/session details and can **suppress** events before
they reach Redis/SSE/Socket.IO.

See: [docs/sdk/bundle/bundle-firewall-README.md](../../sdk/bundle/bundle-firewall-README.md)

### Examples (per marker)

```python
from kdcube_ai_app.apps.chat.sdk.comm.emitters import AIBEmitters
emit = AIBEmitters(self.comm)

# thinking
await emit.delta(text="Working it out…", index=0, marker="thinking", agent="gate")

# answer
await emit.delta(text="Here is the answer.", index=0, marker="answer", agent="answer.generator")

# subsystem (widget stream)
await emit.delta(text='{"status":"running"}', index=0, marker="subsystem", agent="tool.exec")

# canvas (inline artifact stream)
await emit.delta(text='{"type":"chart","data":{...}}', index=0, marker="canvas", agent="viz")

# timeline_text (compact timeline entries)
await emit.delta(text="Loaded 3 prior turns", index=0, marker="timeline_text", agent="orchestrator")
```

Custom typed event example:

```python
await emit.event(
    type="bundle.preferences.updated",
    step="preferences.updated",
    status="completed",
    title="Preferences updated",
    data={"keys": ["city", "diet"]},
    agent="preferences",
)
```

---

## 7) Quick client integration checklist

1) Choose **SSE** or **Socket.IO** for streaming.
2) Provide tokens via header or cookie; for SSE you may also use query params.
3) Open stream (`/sse/stream`) or connect Socket.IO.
4) Send messages via `/sse/chat` or `chat_message` event.
5) Listen for standard events (`chat_start`, `chat_step`, `chat_delta`, `chat_complete`).

---

## 8) Producer API (bundles)

If you are a bundle author, see:
- [comm-system.md](comm-system.md) (producer API + filters)
- [emitters.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/comm/emitters.py) and
  [agentic_app (proc web_app.py)](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py)

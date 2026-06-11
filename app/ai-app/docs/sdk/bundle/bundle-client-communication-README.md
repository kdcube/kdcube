---
id: ks:docs/sdk/bundle/bundle-client-communication-README.md
title: "Bundle Client Communication"
summary: "Browser-to-bundle communication contract across widget iframes, integrations, REST, SSE, Socket.IO, and Data Bus: auth propagation, headers, cookies, stream and session identifiers, and peer targeting."
tags: ["sdk", "bundle", "transport", "auth", "headers", "cookies", "sse", "socketio", "rest", "integrations", "data-bus"]
keywords: ["browser to bundle transport", "widget iframe communication", "integration request headers", "data bus publish", "auth token forwarding", "session and stream identifiers", "peer targeting", "sse and socket communication", "bundle client request contract"]
updated_at: 2026-06-06
see_also:
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/bundle/bundle-client-ui-README.md
  - ks:docs/sdk/bundle/bundle-chat-stream-events-README.md
  - ks:docs/sdk/bundle/bundle-frontend-awareness-README.md
  - ks:docs/sdk/bundle/bundle-events-README.md
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/service/auth/auth-README.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/bus-routing-and-partitioning-README.md
  - ks:docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - ks:docs/service/comm/data-bus-README.md
---
# Bundle Client Communication

This document is the browser/UI contract for talking to the platform over:

- REST
- SSE
- Socket.IO
- proc integrations (`/api/integrations/*`)

It focuses on what a bundle-facing client can send, what the server accepts, and what the client should expect back.

This page is intentionally browser/UI-oriented.

For provider/client contracts that can be exposed through API, MCP, Data Bus,
or local adapters, use
[Namespace Services: Providers](../namespace-services/providers-README.md).

It does not define bundle-served MCP routes or the full bundle transport map.
Use:

- [bundle-transports-README.md](bundle-transports-README.md) for the overall inbound/outbound surface map
- [bundle-platform-integration-README.md](bundle-platform-integration-README.md) for the exact `@mcp(...)` contract
- [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md) for the shared event catalog seen after admission

## 1. Transport Overview

| Transport | Typical use | Notes |
| --- | --- | --- |
| REST | non-streaming APIs | Standard headers/cookies auth. |
| SSE | one-way server-to-client event stream plus `POST /sse/chat` send path | `stream_id` is required on the stream. |
| Socket.IO | bidirectional chat and event delivery | Socket `sid` acts as the peer stream id. |
| Proc integrations | bundle widgets, bundle REST operations, custom frontend ↔ bundle APIs | Supports the same auth context plus peer targeting through a header. |

## 2. Supported Request Headers

These headers are accepted as part of the public client contract.

| Header | Purpose | Configurable |
| --- | --- | --- |
| `Authorization: Bearer <access_token>` | Access token for REST/SSE/integrations auth | No |
| `X-ID-Token` | ID token header | Yes, via `ID_TOKEN_HEADER_NAME` |
| `KDC-Stream-ID` | Connected peer identifier carried on REST requests when the client wants server-side events targeted back to that exact peer | Yes, via `STREAM_ID_HEADER_NAME` |
| `X-User-Timezone` | User timezone for server-formatted messages and context | Yes, via `USER_TIMEZONE_HEADER_NAME` |
| `X-User-UTC-Offset` | User UTC offset in minutes | Yes, via `USER_UTC_OFFSET_MIN_HEADER_NAME` |
| `User-Session-ID` | Reuse/verify an existing authenticated user session | No |

Current default values for configurable headers:

| Env var | Default header name |
| --- | --- |
| `ID_TOKEN_HEADER_NAME` | `X-ID-Token` |
| `STREAM_ID_HEADER_NAME` | `KDC-Stream-ID` |
| `USER_TIMEZONE_HEADER_NAME` | `X-User-Timezone` |
| `USER_UTC_OFFSET_MIN_HEADER_NAME` | `X-User-UTC-Offset` |

## 3. Supported Cookies

| Cookie | Purpose | Configurable |
| --- | --- | --- |
| `__Secure-LATC` | Access token cookie fallback | Yes, via `AUTH_TOKEN_COOKIE_NAME` |
| `__Secure-LITC` | ID token cookie fallback | Yes, via `ID_TOKEN_COOKIE_NAME` |

Current default values:

| Env var | Default cookie name |
| --- | --- |
| `AUTH_TOKEN_COOKIE_NAME` | `__Secure-LATC` |
| `ID_TOKEN_COOKIE_NAME` | `__Secure-LITC` |

## 4. Auth Resolution Order

The server resolves auth in this order:

1. explicit transport payload
   REST/integrations/SSE-send headers, or Socket.IO auth payload
2. SSE query params
3. cookies

Practical meaning:

- if you send `Authorization` and `X-ID-Token`, those win
- if SSE cannot set headers, `bearer_token` and `id_token` query params are accepted
- cookies are fallback transport, not the preferred explicit contract

## 5. SSE Contract

### Open stream

`GET /sse/stream`

Supported query params:

| Query param | Required | Purpose |
| --- | --- | --- |
| `stream_id` | Yes | Client-provided identifier for the connected peer |
| `user_session_id` | No | Reuse an existing session owned by the authenticated user |
| `bearer_token` | No | Access token fallback when headers are unavailable |
| `id_token` | No | ID token fallback when headers are unavailable |
| `tenant` | No | Override tenant for the stream |
| `project` | No | Override project for the stream |
| `project_events` | No | Set to `true` to receive tenant/project-level service events on this SSE stream |

`stream_id` is the peer identifier later used for direct-delivery semantics.
`project_events=true` is opt-in and should be used only by clients that need
compact project-level updates, such as dashboards or landing-page status
widgets.

After the stream opens, clients should subscribe to the shared chat event catalog. In addition to the main lifecycle routes (`chat_start`, `chat_step`, `chat_delta`, `chat_complete`, `chat_error`, `chat_service`, `conv_status`), ReAct clients should also handle `chat_compaction`. That route carries `env.type = "chat.compaction"` and marks context compaction start/completion while a long turn is still running.

### Send chat request

`POST /sse/chat`

The stream-side client should keep using the same session and peer identity it established on `/sse/stream`.

### Conversation status

`POST /sse/conv_status.get`

Clients should use the same stream/session pairing they established for chat.

## 6. Socket.IO Contract

### Connect auth payload

Use the Socket.IO root namespace `/` and the normal path `/socket.io`:

```ts
const manager = new Manager(baseUrl, {
  path: "/socket.io",
  transports: ["websocket"],
  withCredentials: true,
});

const socket = manager.socket("/", { auth });
```

Do not pass an empty namespace string. The server can accept the underlying
Engine.IO websocket while the browser-side Socket.IO client never reaches the
application-level `connect` state.

The Socket.IO `connect` auth payload may include:

| Auth field | Purpose |
| --- | --- |
| `user_session_id` | Existing session to attach to |
| `bearer_token` | Access token |
| `id_token` | ID token |
| `tenant` | Tenant override |
| `project` | Project override |
| `bundle_id` | Bundle scope for bundle-issued federated Data Bus tokens |
| `federated_token` | Short-lived token issued by a bundle for app-specific clients |

The Socket.IO connection `sid` is the peer stream identifier for direct delivery.

### Send chat request

Socket.IO clients send chat requests through the `chat_message` event.

The logical request contract is the same as `POST /sse/chat`.
The first Socket.IO argument is the event submission object itself, with
top-level `external_events[]`. Do not send a nested `{ "message": ... }`
wrapper.

Socket.IO clients should bind all shared server event routes, including `chat_compaction`. Compaction events are progress/status events; they do not complete the turn and should be appended to the same activity timeline or progress card as other in-progress updates.

---

## 7. Data Bus Contract

Use Data Bus publish for durable bundle-domain messages that are not chat
turns, such as collaborative board patches or issue updates.

Browser clients may publish through:

- Socket.IO `data_bus.publish`;
- HTTP `POST /sse/data_bus.publish?stream_id=<open-stream-id>` while listening
  on `/sse/stream`.

Both routes accept the same package and write the same normalized message to
the same bundle Data Bus Redis Stream. The SSE stream itself is still
server-to-client only; the POST is the inbound half.

For how conversation `external_events[]` and Data Bus `messages[]` fit
together, read
[Conversation Event Bus And Data Bus](../../service/comm/conversation-event-bus-and-data-bus-README.md).
For the compact routing and partitioning map, read
[Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md).

There are two supported auth paths:

- platform-authenticated widgets/main views connect with the normal runtime
  session and token material;
- app-specific clients first call a bundle endpoint, the bundle validates that
  upstream context and issues a short-lived federated Data Bus token, and the
  client connects Socket.IO with `federated_token`.

Request shape for both browser transports:

```json
{
  "schema": "kdcube.data_bus.ingress.v1",
  "bundle_id": "example-collab@1-0",
  "messages": [
    {
      "message_id": "dbmsg_2026-06-07-10-20-30-123456789",
      "subject": "example.document.patch",
      "object_ref": "document-123",
      "idempotency_key": "client-op-01HX",
      "payload": {
        "base_revision": 17,
        "operations": []
      }
    }
  ]
}
```

The Socket.IO ack or HTTP response confirms that accepted messages were written
to the Data Bus Redis Stream. It does not prove that a bundle handler exists or
that the domain mutation succeeded. Handler completion, unknown-subject
failures, handler access failures, and domain conflicts are proc-side results
and may arrive later as a `chat_service` event when the bundle handler/runtime
uses `ctx.reply.*`.

Bundle handler registration:

```python
from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler

@data_bus_handler(
    subject="example.document.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_document_patch(self, ctx, message):
    ...
```

Data Bus is separate from `chat_message`, `/sse/chat`, `external_events[]`, and
ReAct timelines. A bundle that wants a handled domain message to become visible
to an agent must explicitly bridge it into conversation ingress.

Routing keys:

| Client intent | Field |
| --- | --- |
| Send conversation context to a named internal agent | `target.agent_id` or `external_events[].agent_id` |
| Route durable bundle-domain work to a handler | `messages[].subject` |
| Serialize durable work for one object | `messages[].object_ref` with handler `partition_by="object_ref"` |

Ingress owns socket auth, federated token scope, JSON bounds, actor/reply
normalization, and stream admission. Proc owns bundle manifest loading,
bundle/handler visibility, partition locking, and handler invocation.

See [Data Bus](../../service/comm/data-bus-README.md) and
[Bundle Federated Auth For Data Bus](auth-bundle-federated-README.md).

Server-side bundle code and trusted tools can publish the same durable messages
without a browser socket by using `comm.data_bus.publish(...)` or
`comm.data_bus.publish_and_wait(...)`; isolated/generated-code runtimes use
`comm_ctx.data_bus_publish(...)` or `comm_ctx.data_bus_publish_and_wait(...)`.
Use that producer path when a tool must mutate bundle-owned state through the
same `@data_bus_handler(...)` pipeline as a browser action. See
[Data Bus: Producer APIs From Bundle Runtimes](../../service/comm/data-bus-README.md#producer-apis-from-bundle-runtimes)
and [Bundle Runtime](bundle-runtime-README.md#publishing-to-data-bus-from-tools-and-entrypoints).

---

## 8. Shared Chat Send Contract

This is the missing common contract between:

- `POST /sse/chat`
- Socket.IO `chat_message`

Both transports send the same logical chat request.

### Logical message shape

Current request fields:

```json
{
  "message": "Hello",
  "chat_history": [],
  "project": "demo-project",
  "tenant": "demo-tenant",
  "turn_id": "turn_123",
  "conversation_id": "conv_123",
  "bundle_id": "my.bundle@1-0",
  "message_kind": "regular|followup|steer",
  "continuation_kind": "regular|followup|steer",
  "active_turn_id": "turn_current",
  "target_turn_id": "turn_current",
  "steer": true,
  "followup": true
}
```

Important:

- `message_kind` / `continuation_kind` are current routing semantics
- authored UI/application events use `external_events[]` and
  top-level `target`; see [Bundle Events](bundle-events-README.md)
- `turn_id` on the request is a client correlation hint; the server allocates or confirms the authoritative task/turn id in the acknowledgement
- for continuations, `target_turn_id` is the user/client intent and `active_turn_id` is the client's best known active turn; neither field is authoritative without server state
- attachments are represented as `event.user.attachment.*` entries in
  `external_events[]`; multipart carries only the corresponding bytes

### Attachments on SSE

For `POST /sse/chat` with attachments, the request is multipart:

- form field `event_submission`
  - JSON string containing the event submission with `external_events[]`
- repeated `files`
  - binary attachment bodies, in the same order as `event.user.attachment.*`
    events

Without attachments, `POST /sse/chat` may be plain JSON.

### Attachments on Socket.IO

For Socket.IO `chat_message`, the first argument is the event submission:

```json
{
  "conversation_id": "optional-existing-conversation-id",
  "external_events": [
    {
      "type": "event.user.attachment.file",
      "event_source_id": "chat.attachment",
      "payload": {
        "mime": "text/plain",
        "event": { "filename": "a.txt", "file_index": 0 }
      }
    }
  ]
}
```

This object is passed directly to `chat_message`; it is not wrapped under
`message`.

Binary attachment buffers are sent as additional event arguments in event order.

### Synchronous acknowledgement

Both send paths return an immediate acknowledgement before the turn necessarily starts.

Known `status` values:

| `status` | Meaning |
| --- | --- |
| `processing_started` | A regular turn was admitted to the normal proc ready queue. |
| `followup_accepted` | The conversation was busy; the message was accepted into the shared external event source as a followup. |
| `steer_accepted` | The conversation was busy; the message was accepted into the shared external event source as a steer/control event. |

Continuation acknowledgements may include these fields:

| Field | Meaning |
| --- | --- |
| `turn_id` | Server-side task/turn id for the submitted request. For accepted continuations this is the fallback queued turn id if the live owner does not consume the event. |
| `active_turn_id` | Server-observed active turn at ingress time. Prefer this for immediate same-turn UI rendering. |
| `target_turn_id` | Client/user intended turn. This is advisory metadata and can be stale. |
| `queued_turn_id` | Explicit alias for the fallback queued turn id carried in `turn_id`. |
| `event_id` | Durable external event id in the conversation event source. Use it as the best dedupe key for optimistic continuation bubbles. |
| `external_event_sequence` | Ordered sequence in the per-conversation external event source. |
| `live_owner_detected` | `true` when ingress saw a live owner lease for `active_turn_id`; `false` means the event was stored but should not be rendered as already consumed by the live turn. |

Client rules:

- send `followup` / `steer` intent and optionally send `target_turn_id`
- treat `followup_accepted` / `steer_accepted` as admission only
- do not create a new visible turn on `followup_accepted` / `steer_accepted`
- for an immediate optimistic followup bubble, require `live_owner_detected !== false`, attach it to `active_turn_id || target_turn_id`, and dedupe with `event_id || queued_turn_id || turn_id`
- if the live owner closes before consumption, wait for the later `chat_start`; proc will promote the stored event once as a normal turn

Use:

- [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)

for the event semantics after acceptance.

Important stream routes after acceptance:

| Route | Use |
| --- | --- |
| `chat_start` | Turn began processing. |
| `chat_step` | Structured progress, decisions, tool updates, custom bundle events. |
| `chat_delta` | Streaming answer/thinking/artifact chunks. |
| `chat_compaction` | ReAct context compaction started/completed/skipped during a long turn. |
| `chat_complete` | Final answer and followups. |
| `chat_error` | Turn failed. |
| `chat_service` | Gateway, queue, and rate-limit events. |
| `conv_status` | Conversation state snapshot. |

## 9. Integrations and Bundle REST Calls

This is the relevant contract for:

- bundle widgets
- custom bundle frontends
- any client calling `/api/integrations/*`

### Auth on integrations REST

The integrations layer accepts the same auth/timezone context as normal HTTP requests:

- `Authorization`
- configured ID token header
- configured timezone header
- configured UTC offset header
- cookies as fallback

For browser/widget cases where setting headers is inconvenient, the middleware also accepts these query params on `/api/integrations/*` and injects them into headers before gateway processing:

| Query param | Injected header |
| --- | --- |
| `bearer_token` | `Authorization: Bearer ...` |
| `id_token` | configured ID token header |
| `user_timezone` | configured timezone header |
| `user_utc_offset_min` | configured UTC offset header |

### Peer-targeted communicator delivery from REST

If the client wants a REST-triggered bundle operation to emit events back to one exact already-connected peer, it must send the configured stream-id header:

```http
KDC-Stream-ID: <connected-peer-stream-id>
```

Here, `KDC-Stream-ID` means the request header whose default name is `KDC-Stream-ID` and whose value is the identifier of the already-connected SSE or Socket.IO peer.

Behavior:

- header present:
  server maps it into communicator target peer id, so bundle-side emits can target that one connected client
- header absent:
  communicator emits remain session-scoped broadcast

Session-scoped broadcast means:

- all connected peers on that session receive the event
- if no peer is listening for that session, nobody receives it

### Non-chat bundle events over the shared stream

The SSE and Socket.IO streams are not limited to chat turns. A bundle UI can
reuse the same authenticated stream for peer-to-peer or session-broadcast
events from a bundle operation, widget call, MCP route, or background-triggered
bundle code as long as the call is executed with a bound communicator context.

Use this shape when the UI does **not** want to start a chat turn but still
wants live events from bundle code.

Client-side SSE:

```ts
const streamId = crypto.randomUUID();

const streamUrl = new URL(`${baseUrl}/sse/stream`);
streamUrl.searchParams.set("user_session_id", sessionId);
streamUrl.searchParams.set("stream_id", streamId);
streamUrl.searchParams.set("tenant", tenant);
streamUrl.searchParams.set("project", project);

const events = new EventSource(streamUrl.toString(), { withCredentials: true });

events.addEventListener("ready", event => {
  console.log("stream ready", JSON.parse(event.data));
});

events.addEventListener("chat_service", event => {
  const envelope = JSON.parse(event.data);
  if (envelope.type === "bundle.job.progress") {
    renderProgress(envelope.data);
  }
});
```

Client-side Socket.IO:

```ts
const socket = io(baseUrl, {
  path: "/socket.io",
  auth: {
    user_session_id: sessionId,
    tenant,
    project,
    bearer_token: accessToken,
    id_token: idToken,
  },
});

socket.on("chat_service", envelope => {
  if (envelope.type === "bundle.job.progress") {
    renderProgress(envelope.data);
  }
});
```

Then call the bundle operation. For a direct reply to the current browser peer,
send the connected peer id as `KDC-Stream-ID`:

```ts
await fetch(
  `${baseUrl}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/run_job`,
  {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "KDC-Stream-ID": streamId, // for Socket.IO use socket.id
    },
    body: JSON.stringify({ data: { job_id: "job-1" } }),
  },
);
```

Bundle-side operation:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_comm
from kdcube_ai_app.infra.plugin.bundle_loader import api

@api(alias="run_job", route="operations", user_types=("registered",))
async def run_job(self, job_id: str, **kwargs):
    comm = get_current_comm()

    if comm is not None:
        await comm.service_event(
            type="bundle.job.progress",
            step="job",
            status="running",
            title="Job running",
            data={"job_id": job_id, "pct": 25},
            broadcast=False,
        )

    # Do the work here.

    if comm is not None:
        await comm.service_event(
            type="bundle.job.completed",
            step="job",
            status="completed",
            title="Job completed",
            data={"job_id": job_id},
            broadcast=True,
        )

    return {"ok": True, "job_id": job_id}
```

Delivery semantics:

- `broadcast=False` targets the peer from `KDC-Stream-ID` when it was provided;
  otherwise it falls back to the current session route.
- `broadcast=True` sends to all connected SSE/Socket.IO peers in the same
  authenticated session.
- use namespaced semantic event types such as `bundle.job.progress`,
  `memory.snapshot.completed`, or `admin.import.failed`.

### Tenant/project SSE broadcast

When a bundle needs to update all connected SSE clients in the same
tenant/project, use `comm.project_event(...)`. This is a separate primitive from
`broadcast=True`:

- `broadcast=True` means all peers in the current user session.
- `comm.project_event(...)` means all SSE clients that explicitly subscribed to
  tenant/project events for the same tenant/project.

Client-side SSE opt-in:

```ts
const streamId = crypto.randomUUID();

const streamUrl = new URL(`${baseUrl}/sse/stream`);
streamUrl.searchParams.set("user_session_id", sessionId);
streamUrl.searchParams.set("stream_id", streamId);
streamUrl.searchParams.set("tenant", tenant);
streamUrl.searchParams.set("project", project);
streamUrl.searchParams.set("project_events", "true");

const events = new EventSource(streamUrl.toString(), { withCredentials: true });

events.addEventListener("chat_service", event => {
  const envelope = JSON.parse(event.data);
  if (envelope.type === "my.bundle.snapshot") {
    applySnapshot(envelope.data);
  }
});
```

Bundle-side publisher:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_comm

async def publish_snapshot(self, snapshot: dict):
    comm = get_current_comm() or self.comm
    if comm is None:
        return

    await comm.project_event(
        type="my.bundle.snapshot",
        step="snapshot",
        status="completed",
        title="Snapshot updated",
        data={"snapshot": snapshot},
        auto_markdown=False,
    )
```

Rules:

- project events currently target SSE clients, not Socket.IO clients
- clients must opt in with `project_events=true`
- the event is carried on the existing `chat_service` route; route by
  `envelope.type`
- payloads must be compact, bounded, and already safe for all viewers in the
  tenant/project
- do not publish raw telemetry events, logs, prompts, answers, or unbounded
  result lists through this path
- debounce project broadcasts when the source changes frequently

## 10. Response Headers Clients Should Use

| Header | Meaning |
| --- | --- |
| `X-User-Type` | Resolved user type for the request |
| `X-Session-ID` | Server session id for the request |
| `Retry-After` | Retry hint on `429` and some `503` responses |

Clients should:

- keep `X-Session-ID` stable when they intend to reuse the same session
- honor `Retry-After` when rate-limited or backpressured

## 11. Supported Streaming Payload Patterns

The transport is generic, but there are a few payload styles the platform
already understands and renders consistently.

`delta.marker` is an open string. The markers below are built in; custom
markers are allowed but need explicit client support.

### A) Main answer / thinking text

Use `chat.delta` with marker `answer` or `thinking`.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "Here is the answer.",
    "index": 0,
    "marker": "answer"
  }
}
```

### B) Structured subsystem payloads

Use `chat.delta` with marker `subsystem` when the client should route the
payload to a specialized widget/tool panel.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "{\"status\":\"running\",\"progress\":42}",
    "index": 0,
    "marker": "subsystem"
  },
  "extra": {
    "sub_type": "code_exec.status",
    "format": "json",
    "artifact_name": "code_exec.status"
  }
}
```

### C) Canvas-style artifact stream

Use `chat.delta` with marker `canvas` for inline artifact/canvas content such as
HTML, JSON, or managed structured payloads.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "{\"type\":\"chart\",\"data\":{\"points\":[1,2,3]}}",
    "index": 0,
    "marker": "canvas"
  },
  "extra": {
    "format": "json",
    "artifact_name": "canvas.chart.v1",
    "title": "Chart"
  }
}
```

Hosted files use the `chat.files` event family rather than canvas chunks.
Those files may be produced by built-in rendering tools, strict custom tool
results (`ret.artifact_type: "files"`), or trusted catalog tools that call
`bundle_tool_context.host_files(...)`.

For tool-side hosting, `host_files(...)` must run in a prepared tool context
with tenant, project, user id, conversation id, turn id, conversation storage,
and a hosting-capable `ToolSubsystem`. That context is prepared by
`BaseWorkflow.build_react(...)` in normal flows and `bootstrap_bind_all(...)` in
isolated execution.

### D) Compact timeline text

Use `chat.delta` with marker `timeline_text` for short human-readable entries.

Example:

```json
{
  "type": "chat.delta",
  "delta": {
    "text": "Loaded 3 prior turns",
    "index": 0,
    "marker": "timeline_text"
  }
}
```

### E) Custom typed events

If the bundle wants a custom non-delta semantic event, it can emit a typed
event that still travels over the standard streaming transport.

Example:

```json
{
  "type": "bundle.preferences.updated",
  "timestamp": "2026-04-01T10:00:00Z",
  "event": {
    "agent": "preferences",
    "step": "preferences.updated",
    "status": "completed",
    "title": "Preferences updated"
  },
  "data": {
    "keys": ["city", "diet"]
  }
}
```

Client rule:

- built-in markers and event families are rendered by platform clients
- custom markers or custom event types are allowed, but a client must
  explicitly support them to do anything more than generic display/logging

See:

- [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)
- [README-comm.md](../../service/comm/README-comm.md)

## 12. Typical Browser Patterns

### Standard app

1. open SSE with `stream_id`
2. send chat via `POST /sse/chat`
3. receive streamed events on that stream

### Standard app with Socket.IO

1. connect Socket.IO with auth payload
2. send chat via `chat_message`
3. receive the same semantic event envelopes on socket events such as:
   - `chat_start`
   - `chat_step`
   - `chat_delta`
   - `chat_complete`

### Widget or custom bundle frontend

1. get or reuse connected peer id from the host app
2. call `/api/integrations/*`
3. include the configured stream-id header if bundle-side communicator emits should go only to that peer

### Cookie-based proxylogin deployment

1. browser keeps token cookies
2. requests omit explicit auth headers
3. server falls back to configured auth cookies

## 13. What To Read Next

- shared chat stream event catalog:
  [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md)
- reconnect, draining, retry, and multi-tab behavior:
  [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md)
- server-side auth transport details:
  [auth-README.md](../../service/auth/auth-README.md)

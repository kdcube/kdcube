---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
title: "Data Bus"
summary: "Runtime contract for the bundle-scoped Data Bus: durable non-conversation messages, handler registration, ordering, and comm fanout boundary."
status: active
tags: ["service", "comm", "data-bus", "socketio", "sse", "redis-streams", "bundle-runtime"]
updated_at: 2026-06-07
keywords:
  [
    "data bus",
    "bundle data messages",
    "redis streams",
    "socket.io",
    "object ordering",
    "document patch",
    "domain state",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
---
# Data Bus

The **Data Bus** is the service path for durable, bundle-scoped
messages that are not chat turns.

It is intended for bundle-domain state changes and state signals such as:

- collaborative document patches;
- issue updates;
- wizard or snapshot persistence notifications;
- domain object comments and annotations;
- cross-widget coordination events;
- background service messages that a bundle owns.

It reuses the authenticated transport layer where practical, especially
Socket.IO, but it must not be confused with the conversation bus.

## Message Paths And Ownership

KDCube currently has several message paths. They have different semantics:

| Path | Scope | Durable processing | Typical use |
| --- | --- | --- | --- |
| Conversation ingress | conversation + turn | yes, via the chat task queue | user prompt, followup, steer, chat attachments, `external_events[]` that should enter the timeline |
| Comm relay | user session or tenant/project | no, transient Pub/Sub fanout | chat output, direct operation replies, compact UI refreshes |
| Data Bus | tenant/project/bundle and optional object | yes, via Redis Streams | bundle-owned state mutations and domain messages |
| Telemetry/event sinks | environment-defined | sink-defined | diagnostics, metrics, audit, analytics |

The Data Bus is for messages that the bundle should handle even when no chat
turn is running and no active browser listener exists.

Use the conversation path when a message should enter a chat turn or timeline.
Use the comm relay when code already running inside a request, job, or handler
wants to send a transient update to a connected peer/session. Use the Data Bus
when a bundle owns durable state and wants platform-managed admission, retry,
partition locking, and handler execution.

For how the conversation event bus and the Data Bus fit together, including
when to bridge between them, see
[Conversation Event Bus And Data Bus](conversation-event-bus-and-data-bus-README.md).
For the compact routing and partitioning contract, see
[Bus Routing And Partitioning](bus-routing-and-partitioning-README.md).

## Ownership Boundary

The Data Bus path has one strict ownership boundary:

```text
browser/widget
  -> optional federated token-claim operation
  -> Socket.IO data_bus.publish or HTTP POST /sse/data_bus.publish
  -> ingress authenticates, normalizes, and enqueues
  -> proc Data Bus worker loads bundle manifest
  -> proc enforces bundle/handler visibility and invokes @data_bus_handler
```

Ingress owns transport concerns: socket authentication, tenant/project/session
normalization, payload bounds, federated Data Bus token verification, and
stream admission.

Proc owns bundle execution concerns: loading bundle code, discovering
`@data_bus_handler(...)`, applying effective bundle props, enforcing
bundle/handler visibility, acquiring partition locks, and calling handler code.

Ingress does not import bundle modules. That is intentional because ingress
should not require bundle execution dependencies and should not decide handler
visibility from code that proc owns.

## Transport Shapes

For browser clients, Data Bus can be published through either:

- Socket.IO `data_bus.publish` over the already-authenticated Socket.IO
  connection;
- HTTP `POST /sse/data_bus.publish?stream_id=<open-stream-id>` while the client
  listens on `/sse/stream`.

Both transports produce the same normalized `DataBusMessage` and write to the
same bundle Redis Stream. The package is separate from `chat_message` and
`/sse/chat`. Ingress routes it to Data Bus Redis Streams, not to the chat
conversation queue.

Socket.IO clients must use the root namespace `/`:

```ts
const socket = manager.socket("/", { auth });
```

Use the normal Socket.IO path `/socket.io`. Passing an empty namespace string
can leave the Engine.IO websocket connected while the Socket.IO client never
receives the application-level `connect` event.

Example client package:

```json
{
  "schema": "kdcube.data_bus.ingress.v1",
  "bundle_id": "example-collab@1-0",
  "messages": [
    {
      "message_id": "dbmsg_2026-06-07-10-20-30-123456789",
      "subject": "example.document.patch",
      "object_ref": "document-123",
      "idempotency_key": "client-op-7e4f",
      "payload": {
        "base_revision": 17,
        "operations": [
          {
            "op": "update_item",
            "item_id": "item-17",
            "set": {
              "description": "User-entered note."
            }
          }
        ]
      }
    }
  ]
}
```

`messages[]` is plural from the start. A client can send a batch of related
messages when it needs the server to observe their order as submitted.

SSE mode is not client-to-server over the event stream itself. SSE remains
server-to-client. The inbound publish is the companion HTTP POST:

```ts
await fetch(`${baseUrl}/sse/data_bus.publish?stream_id=${encodeURIComponent(streamId)}`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(package),
})
```

Handler replies still arrive as `chat_service` events on the existing SSE
stream, targeted by the same `stream_id` when present.

Choose the transport before publishing. If a client publishes a mutation over
Socket.IO, receives an ack, and then loses the socket while waiting for the
result, retrying the same mutation through SSE can duplicate work unless the
message carries a stable `message_id` and `idempotency_key` and the handler is
idempotent.

### Platform-Authenticated Clients

A client running inside the platform uses the normal authenticated browser
session. It connects Socket.IO with the session and token material supplied by
runtime config:

```json
{
  "tenant": "tenant-a",
  "project": "project-a",
  "user_session_id": "<session-id>",
  "bearer_token": "<optional-access-token>",
  "id_token": "<optional-id-token>"
}
```

Cookies are still accepted as fallback by the gateway, but explicit auth in the
Socket.IO auth payload is the preferred browser contract when the widget has
runtime config.

### Federated Clients

Clients that do not have a platform browser session can connect to Data Bus
through a standardized federated token.

The preferred shared-identity path is Connection Hub's
`federated_data_bus_claim` operation. Connection Hub validates promoted
upstream auth context, resolves identity links when present, creates or
refreshes the actor `UserSession`, stores projected authority on that session,
and returns a short-lived Data Bus token.

A bundle-owned public claim endpoint may also issue the same token shape when
the bundle owns a custom authority. It must validate upstream proof and build
the correct `UserSession` authority before issuing the token. Ingress does not
run bundle-local provider verification.

The client then connects to Socket.IO namespace `/` with:

```json
{
  "tenant": "tenant-a",
  "project": "project-a",
  "bundle_id": "<token-bundle-scope>",
  "federated_token": "<short-lived-token>"
}
```

Socket.IO verifies token integrity, bundle scope, Redis registration, and the
backing session before accepting the connection. The token body stays minimal:
identity provenance, roles, and permissions live on the backing `UserSession`,
not inside the token. After admission, `data_bus.publish` uses the same
normalized actor/reply metadata as ordinary platform-authenticated sockets.

Use the full bundle recipe in
[Federated Data Bus Session Tokens](../../sdk/bundle/auth-bundle-federated-README.md).

## Core Envelope

Each message is normalized by ingress before it is written to the stream.

Canonical fields:

| Field | Meaning |
| --- | --- |
| `message_id` | Stable message id generated by client or ingress. Ingress defaults to `dbmsg_<UTC timestamp>`. |
| `tenant` / `project` | Runtime scope resolved by ingress. |
| `bundle_id` | Bundle that owns the handler. |
| `subject` | Domain event or command name, such as `example.document.patch`. |
| `object_ref` | Optional opaque object partition key, such as `document-123`. Data Bus does not resolve this value. |
| `idempotency_key` | Required for mutations; used to dedupe retries. |
| `actor` | Authenticated user/session/principal summary. |
| `payload` | Bundle-defined structured data. |
| `created_at` | Ingress timestamp. |
| `reply` | Optional connected peer/session info for status events back to the UI. |
| `trace` | Optional request/stream ids for diagnostics. |

The client controls only the bundle-owned payload and message intent fields.
Ingress attaches `actor`, `reply`, `trace`, tenant/project, and timestamps from
the authenticated socket context.

## Routing And Partitioning

Data Bus routing is subject based:

```text
messages[].subject -> @data_bus_handler(subject="...")
```

Data Bus stream ownership is bundle based:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
```

Object serialization is optional and explicit:

```text
messages[].object_ref
  + @data_bus_handler(partition_by="object_ref", ordering="serial_per_partition")
  -> one active handler for tenant/project/bundle/subject/object_ref
```

Use conversation `target.agent_id` only for the conversation event bus. Data Bus
does not select handlers by `agent_id`; use `subject` for handler routing and
`object_ref` for object partitioning.

## Stream Layout

Stream naming is bundle scoped:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
kdcube:data-bus:{tenant}:{project}:{bundle_id}:results
kdcube:data-bus:{tenant}:{project}:{bundle_id}:dlq
```

If strict per-object FIFO becomes required, add object streams or a runtime
scheduler:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:object:{object_ref}:messages
```

The current implementation uses a bundle stream plus runtime per-object locks.
The guarantee is serial active handler execution per partition, not strict FIFO.

## Ordering And Concurrency

Redis Streams preserve append order in a stream. Redis consumer groups ensure a
message is claimed by one consumer at a time.

They do **not** by themselves guarantee that two messages for the same canvas,
issue, or other object will never be handled concurrently by different workers.

The SDK runtime must own this guarantee when a handler requests it:

```python
@data_bus_handler(
    subject="example.document.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_document_patch(ctx, message):
    ...
```

For `serial_per_partition`, the runtime:

1. derive the partition key from `object_ref`;
2. acquire a short-lived Redis lock for that partition before calling the
   handler;
3. extend or release the lock safely while the message is in progress;
4. retry or requeue when the lock is unavailable;
5. still require storage-level idempotency and optimistic concurrency checks.

Important: `serial_per_partition` means no concurrent handler execution for the
same partition. It is not strict FIFO when messages are retried, claimed late,
or moved through a dead-letter path. Strict FIFO is a separate ordering mode,
for example a future `ordering="fifo_per_partition"`.

For collaborative objects such as board state, the durable storage remains the
authority. A patch must include `base_revision`, and the storage layer must
reject stale updates or return a conflict.

## Handler Registration

Bundles must not start ad-hoc Redis consumers. The SDK exposes a bundle-facing
registration API and the runtime manages lifecycle, retries, and shutdown.

Example decorator:

```python
from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler

@data_bus_handler(
    subject="example.document.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_document_patch(ctx, message):
    result = await ctx.bundle.document_store.apply_patch(
        actor=message.actor,
        object_ref=message.object_ref,
        idempotency_key=message.idempotency_key,
        payload=message.payload,
    )
    await ctx.reply.ok(result)
```

Runtime code:

- decorator metadata is collected in the bundle interface manifest;
- Socket.IO ingress writes accepted packages to the bundle stream;
- the processor-owned Data Bus runtime reconciles bundle manifests, enforces
  effective bundle and handler visibility, and starts one managed worker per
  bundle with registered handlers.

The bundle manifest exposes registered subjects as `data_bus_handlers`.

## Producer APIs From Bundle Runtimes

Browser clients are not the only Data Bus producers. Bundle code that is
already running in proc, and trusted tools running in isolated execution, can
publish the same durable messages through the current communicator.

This does not merge the Data Bus with normal chat/service events. The
communicator is a convenience facade with separate methods:

- `comm.service_event(...)`, `comm.project_event(...)`, `comm.delta(...)`, and
  related chat methods publish transient UI/conversation relay events.
- `comm.data_bus.publish(...)` and `comm.data_bus.publish_and_wait(...)` write
  durable Data Bus records to the bundle stream and do not create conversation
  `external_events[]`, timeline blocks, or ReAct turns by themselves.
- A Data Bus handler may reply to the client through normal relay fanout after
  applying the durable mutation. That reply is a notification/result, not the
  durable message itself.

Use this when a tool or bundle operation must route a state mutation through
the same `@data_bus_handler(...)` path as the browser. For example, a ReAct tool
that patches a collaborative document should not bypass the document handler just
because it is running server-side.

Entrypoint or tool code with access to the current communicator can call:

```python
comm = get_current_comm() or self.comm

ack = await comm.data_bus.publish(
    bundle_id="example-docs@1-0",
    subject="example.document.patch",
    object_ref="document-123",
    idempotency_key="tool-op-2026-06-07-10-20-30-123456789",
    message_id="dbmsg_2026-06-07-10-20-30-123456789",
    reply=True,
    payload={
        "base_revision": 17,
        "operations": [
            {"op": "update_item", "item_id": "item-17", "set": {"title": "Review"}}
        ],
    },
)
```

For command-like tool calls that need the applied result before returning to
the model, use `publish_and_wait(...)`:

```python
result = await comm.data_bus.publish_and_wait(
    bundle_id="example-docs@1-0",
    subject="example.document.patch",
    object_ref="document-123",
    idempotency_key="tool-op-2026-06-07-10-20-30-123456789",
    message_id="dbmsg_2026-06-07-10-20-30-123456789",
    reply=True,
    timeout_ms=20_000,
    payload={"base_revision": 17, "operations": []},
)
```

Inside generated code or trusted isolated runtimes, use the request-local
helpers:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    data_bus_publish,
    data_bus_publish_and_wait,
)

result = await data_bus_publish_and_wait(
    bundle_id="example-docs@1-0",
    subject="example.document.patch",
    object_ref="document-123",
    idempotency_key="tool-op-2026-06-07-10-20-30-123456789",
    message_id="dbmsg_2026-06-07-10-20-30-123456789",
    reply=True,
    payload={"base_revision": 17, "operations": []},
)
```

`reply=True` means the publisher copies reply metadata from the current
request, if any, so the later handler result can be delivered to the connected
peer/session. It is optional. The durable mutation must not depend on a live
peer.

The SDK default `message_id` is timestamp-readable (`dbmsg_<UTC timestamp>`),
but bundle code should still pass explicit timestamp ids for idempotent domain
mutations. Avoid random ids for persisted domain objects; use semantic prefixes
such as `ticket_` or a bundle-owned prefix that makes the object kind obvious
in logs and timelines.

## Replies And Client Updates

Data Bus processing is durable. Client updates are a separate delivery concern.

A handler can reply through comm when a connected peer or session is present:

```python
await ctx.reply.ok({
    "type": "example.document.patch.applied",
    "document": result.document_summary,
})
```

The runtime publishes replies through the existing comm relay, using the
`reply` metadata captured at ingress. If no peer is connected, the data change
still persists and the UI can observe it later through normal fetch/refresh.

## Conversation Bridge

The Data Bus stays outside ReAct timelines by default.

To wake or inform an agent from a handled Data Bus message, the bundle creates
an explicit conversation submission with `external_events[]` through the
conversation ingress path. That submission then has conversation/turn semantics
and may produce `ev:` event paths, timeline blocks, summaries, and compaction
records.

This keeps bundle state traffic separate from conversation history while still
allowing deliberate bridges.

## Usage Pattern

For browser widgets, the practical pattern is:

```text
client creates mutation message
  -> socket.emit("data_bus.publish", package, ack_handler)
     or POST /sse/data_bus.publish?stream_id=...
  -> ack says accepted/partial/rejected by ingress for stream admission
  -> handler later calls ctx.reply.ok/conflict/error
  -> UI receives chat_service status event for the same session/peer
  -> UI fetches durable state if it needs the full object
```

The ack is not the durable object result and does not prove that a handler
exists. It confirms stream admission. Handler absence, handler visibility
failure, partition conflicts, and domain validation failures are proc-side
results.

The handler result should be compact. If the client needs full state after a
successful mutation, it should call the bundle's normal read API.

## Runtime Processing

The runtime consumes the bundle stream with a Redis consumer group:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:handlers
```

The group is created lazily by the processor-owned Data Bus runtime when a
bundle with `@data_bus_handler(...)` methods is active.

Processing flow:

1. `XREADGROUP` reads a message from the bundle stream.
2. The runtime decodes and validates the normalized record.
3. The runtime loads the active bundle manifest and effective bundle props.
4. The runtime verifies the bundle is enabled and visible to the actor.
5. The runtime finds the registered handler by `subject`.
6. The runtime verifies handler `user_types` / `roles` visibility.
7. If no handler exists or access is denied, the runtime writes a failure result and
   acknowledges the stream item.
8. If the handler uses `serial_per_partition`, the runtime acquires the
   partition token lock before invoking bundle code.
9. The handler mutates bundle-owned durable storage.
10. The runtime writes a result record and emits an optional reply when reply
   metadata exists.
11. The runtime acknowledges the stream item after the durable mutation and
   result handling path completes.
12. Retryable failures remain pending or are requeued according to the runtime
   retry policy.
13. Non-retryable failures or exhausted retries go to the DLQ.

Suggested result record shape:

```json
{
  "schema": "kdcube.data_bus.result.v1",
  "message_id": "dbmsg_2026-06-07-10-20-30-123456789",
  "status": "ok",
  "subject": "example.document.patch",
  "object_ref": "document-123",
  "data": {
    "revision": 18
  },
  "processed_at": "2026-06-05T00:00:02Z"
}
```

Retention is operational policy:

- message stream: trim after acknowledgement plus a bounded operational window;
- result stream: short retention for debugging and near-term reconnects;
- DLQ stream: longer retention and alertable.

Exact retention values should be configurable by deployment.

## Security

Ingress must:

- resolve tenant/project from the authenticated platform context;
- verify token/session integrity for platform-authenticated sockets;
- verify federated token scope and backing session when a federated token is
  used;
- verify that the target bundle exists and is enabled in the active registry;
- reject client-supplied tenant/project/actor overrides;
- attach actor and reply metadata from the authenticated connection;
- cap JSON payload size;
- reject unexpected binary data in the JSON package;
- avoid logging user-authored payload bodies.

Proc must:

- load bundle manifests and handler metadata;
- apply effective bundle props;
- enforce bundle `allowed_roles` and handler `user_types` / `roles`;
- reject unknown subjects;
- enforce handler idempotency and partition policy before invoking bundle code;
- write result/DLQ records for handler admission or execution failures.

Handlers must still perform domain authorization. For example, a board handler
must verify that the actor can read or mutate the selected board.

## Observability

Data Bus ingress logs package and message receipt, plus accepted stream ids, as
metadata:

```text
[data_bus.publish] received package tenant=... project=... bundle=... messages=...
[data_bus.publish] received message tenant=... project=... bundle=... subject=... object_ref=... message_id=...
[data_bus.publish] accepted message tenant=... project=... bundle=... subject=... object_ref=... message_id=... stream_id=...
```

Bundle handlers and storage layers should separately log durable facts such as
revision or object creation:

```text
[domain.revision] created object_ref=document-123 revision=18 ref=...
```

Do not log user-authored payload bodies in ingress or storage metadata logs.

## Collaborative Object Example

In a bundle with a collaborative object:

- creating or updating an item is a Data Bus message;
- uploading or hosting bytes may involve a bundle-specific storage API, then a
  Data Bus message that records the durable state change;
- moving or annotating an item is a Data Bus message if the bundle chooses to
  persist that change immediately;
- selecting an item as agent context is a conversation submission, not a Data
  Bus message, because the user is asking the assistant to use that context.

## Tests And Regression Expectations

Core tests should cover:

- envelope validation;
- handler decorator registration and subject lookup;
- idempotency policy validation;
- lock acquire/release token safety;
- retry and DLQ transitions;
- Socket.IO `data_bus.publish` writing accepted messages to the bundle stream;
- Socket.IO clients using namespace `/`;
- ingress not importing bundle modules or handler manifests;
- worker consumption and handler invocation;
- proc-side unknown-subject and handler-visibility rejection;
- handler replies reaching the connected peer/session through comm;
- disconnected clients still relying on durable state reads;
- two messages for the same `object_ref` not running concurrently when the
  handler requests `serial_per_partition`;
- stale `base_revision` returning conflict;
- federated token/session rejection at ingress.

Regression tests should prove:

- Socket.IO `chat_message` still routes only to conversation ingress;
- `/sse/chat` still expects conversation `external_events[]`;
- `comm.service_event(...)`, `comm.project_event(...)`, SSE, and Socket.IO
  fanout remain unchanged;
- ReAct timeline behavior changes only when a bundle explicitly bridges a Data
  Bus result into conversation events.

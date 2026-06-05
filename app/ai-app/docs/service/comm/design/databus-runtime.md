---
id: ks:docs/service/comm/design/databus-runtime.md
title: "Data Bus Runtime Handoff"
summary: "Implementation handoff for the SDK/runtime Data Bus: ingress package, Redis Streams, handler registry, ordering, idempotency, replies, and tests."
status: active
tags: ["service", "comm", "data-bus", "design", "runtime", "handoff"]
keywords:
  [
    "data bus runtime",
    "redis streams",
    "socket.io data bus",
    "bundle handlers",
    "serial per partition",
    "idempotency",
    "canvas patch",
  ]
see_also:
  - ks:docs/service/comm/data-bus-README.md
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/comm-system.md
---
# Data Bus Runtime Handoff

This document is the runtime reference for the first Data Bus implementation.

Read first:

- [Data Bus](../data-bus-README.md)
- [Comm System](../comm-system.md)
- [Bundle Client Communication](../../../sdk/bundle/bundle-client-communication-README.md)

## Problem

Bundles need a way to receive durable domain messages that are not chat turns.

Concrete task-tracker examples:

- a user creates a text card on a canvas;
- a user uploads an attachment onto a canvas;
- a user edits a canvas card description or comment;
- a user applies or rejects an assistant suggestion;
- a service updates an issue status;
- a widget persists a snapshot or domain-object change.

These messages should not go to the conversation queue just because the browser
uses the same Socket.IO connection as chat.

They also should not be modeled as `comm.service_event(...)`, because comm
events are outbound/transient. Data Bus messages are inbound/durable.

## Goals

1. Add a bundle-scoped durable message bus.
2. Accept plural message batches from Socket.IO and, later, REST if needed.
3. Route accepted messages into Redis Streams separate from chat queues.
4. Let bundles register handlers by `subject`.
5. Support handler lifecycle through the platform runtime, not ad-hoc bundle
   tasks.
6. Support idempotency and retry.
7. Support optional per-object serialization for collaborative objects.
8. Send optional status/reply events back through existing comm delivery.
9. Keep Data Bus messages outside ReAct timelines unless a bundle explicitly
   bridges them into conversation ingress.

## Non-Goals

- Do not replace chat `external_events[]`.
- Do not write `ev:` records by default.
- Do not create a conversation turn unless the bundle explicitly submits one.
- Do not use the chat task queue for Data Bus messages.
- Do not use `comm.project_event(...)` as durable storage.
- Do not guarantee strict FIFO in the first pass unless the runtime explicitly
  implements per-object streams or a scheduler.

## Naming

Use "Data Bus" in docs and user-facing architecture.

Use `data_bus` for Python modules and APIs.

Use `kdcube.data_bus.*` for transport schema names and emitted status types.

Avoid "canvas socket" or "canvas bus". Canvas is only the first consumer.

## Architecture

```text
browser/widget
  |
  | Socket.IO event: data_bus.publish
  v
socketio ingress
  |
  | validate auth, tenant/project, bundle visibility
  | normalize messages[]
  v
DataBusIngress
  |
  | XADD
  v
Redis Streams
  kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
  |
  | XREADGROUP
  v
DataBusRuntimeWorker
  |
  | handler lookup by subject
  | optional partition lock by object_ref
  | idempotency check
  v
bundle handler
  |
  | durable storage mutation
  | optional ctx.reply.*
  v
comm relay
  |
  v
connected browser peers
```

## Implemented Runtime Slice

The first runtime slice is implemented in these modules:

| Layer | Module |
| --- | --- |
| SDK public import | `kdcube_ai_app.apps.chat.sdk.data_bus` |
| SDK types and result/reply contract | `kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types` |
| Redis stream helpers | `kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream` |
| Partition token locks | `kdcube_ai_app.apps.chat.sdk.runtime.data_bus.locks` |
| Processor-owned workers | `kdcube_ai_app.apps.chat.sdk.runtime.data_bus.worker` |
| Socket.IO ingress package | `kdcube_ai_app.apps.chat.ingress.socketio.data_bus.publish` |
| Bundle decorator/manifest metadata | `kdcube_ai_app.infra.plugin.bundle_loader` |

The processor runtime reconciles bundle manifests from the active registry and
starts one managed Data Bus worker per bundle that declares
`@data_bus_handler(...)` methods. Bundles do not create Redis consumers.

The Socket.IO ingress event is `data_bus.publish`. Its ack confirms stream
acceptance only. Handler completion is recorded on the result stream and may be
sent to the connected peer/session through `ctx.reply.*`.

## Transport Contract

Add a Socket.IO event:

```text
data_bus.publish
```

Request payload:

```json
{
  "schema": "kdcube.data_bus.ingress.v1",
  "bundle_id": "task-tracker@1-0",
  "messages": [
    {
      "message_id": "dbmsg_01HX...",
      "subject": "task_tracker.canvas.patch",
      "object_ref": "canvas:main",
      "idempotency_key": "client-op-01HX...",
      "payload": {
        "base_revision": 17,
        "operations": []
      },
      "client": {
        "widget_id": "task_tracker_main",
        "component": "canvas"
      }
    }
  ]
}
```

Rules:

- `messages` is required and plural.
- `bundle_id` is required at the package level.
- `subject` is required per message.
- `payload` must be JSON-serializable.
- mutations must include `idempotency_key`.
- `object_ref` is optional generally, required for handlers that request
  `partition_by="object_ref"`.
- binary files should not be embedded. Use normal upload/hosting paths and put
  refs in the payload.

Response behavior:

- Socket.IO ack should only confirm acceptance into the Data Bus stream.
- Handler success/failure should be sent later through `data_bus.result` or a
  domain-specific comm event.

Acceptance ack example:

```json
{
  "status": "accepted",
  "accepted": [
    {
      "message_id": "dbmsg_01HX...",
      "stream_id": "1717600000000-0"
    }
  ]
}
```

## Normalized Stream Record

Ingress writes a normalized record. Do not rely on raw client fields for auth
or routing.

```json
{
  "schema": "kdcube.data_bus.message.v1",
  "message_id": "dbmsg_01HX...",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "bundle_id": "task-tracker@1-0",
  "subject": "task_tracker.canvas.patch",
  "object_ref": "canvas:main",
  "idempotency_key": "client-op-01HX...",
  "actor": {
    "user_id": "02e...",
    "user_type": "registered",
    "session_id": "..."
  },
  "payload": {
    "base_revision": 17,
    "operations": []
  },
  "reply": {
    "transport": "socketio",
    "session_id": "...",
    "socket_id": "..."
  },
  "trace": {
    "request_id": "...",
    "client_message_index": 0
  },
  "created_at": "2026-06-05T00:00:00Z"
}
```

Use JSON in the Redis Stream field first:

```text
XADD kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages * json <record-json>
```

That keeps the first pass simple and avoids schema drift across fields.

## Redis Streams

Minimum streams:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
kdcube:data-bus:{tenant}:{project}:{bundle_id}:results
kdcube:data-bus:{tenant}:{project}:{bundle_id}:dlq
```

Consumer group:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:handlers
```

The runtime should create the group lazily with `MKSTREAM` on bundle load or
worker startup.

Retention:

- messages stream: trim by age/count after ack plus operational retention;
- results stream: short retention, useful for debugging and disconnected
  clients that reconnect soon;
- DLQ stream: longer retention, alertable.

Exact retention values should be configurable.

## Handler Registration API

Add an SDK module, for example:

```text
kdcube_ai_app.apps.chat.sdk.data_bus
```

Public decorator:

```python
@data_bus_handler(
    subject="task_tracker.canvas.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
    user_types=("registered", "privileged"),
)
async def handle_canvas_patch(ctx: DataBusContext, message: DataBusMessage) -> DataBusResult:
    ...
```

Required runtime objects:

```python
class DataBusMessage:
    message_id: str
    tenant: str
    project: str
    bundle_id: str
    subject: str
    object_ref: str | None
    idempotency_key: str | None
    actor: dict
    payload: dict
    reply: dict | None
    trace: dict
    created_at: str

class DataBusContext:
    tenant: str
    project: str
    bundle_id: str
    actor: dict
    comm: ChatCommunicator | None
    reply: DataBusReply
```

`DataBusReply` should support:

```python
await ctx.reply.accepted(data=None)
await ctx.reply.ok(data)
await ctx.reply.conflict(data)
await ctx.reply.error(code, message, details=None)
await ctx.reply.event(type, data, broadcast=False)
```

`ctx.reply.*` emits through comm when a connected reply target exists. It must
not be the source of durable truth.

## Bundle Discovery

Use the same lifecycle as other bundle capabilities:

1. bundle loader imports entrypoint/modules;
2. decorator registrations are collected in a bundle-local registry;
3. runtime resolves which subjects the bundle handles;
4. runtime starts/stops Data Bus consumers with the bundle process lifecycle.

Do not ask bundles to create raw Redis clients or background loops directly.

## Ordering Guarantees

### What Redis gives

Redis Streams give append order for a stream and consumer groups assign a
message to one consumer at a time.

Redis Streams do not guarantee that two messages with the same `object_ref`
will not be processed concurrently when multiple consumers are active.

### Runtime guarantee for `serial_per_partition`

For handlers with:

```python
partition_by="object_ref"
ordering="serial_per_partition"
```

the Data Bus runtime must:

1. compute a partition key:

   ```text
   {tenant}:{project}:{bundle_id}:{subject}:{object_ref}
   ```

2. acquire a Redis lock before invoking the handler:

   ```text
   kdcube:data-bus:lock:{hash(partition-key)}
   ```

3. use a token value and TTL;
4. renew the lock while the handler is active if needed;
5. release only if token matches;
6. if lock is unavailable, requeue/defer without running the handler.

This guarantees one active handler execution per partition, assuming all
workers use the SDK runtime.

It does not guarantee strict FIFO. If strict FIFO is required, implement a
later `fifo_per_partition` mode with either:

- one stream per object partition; or
- a scheduler that claims only the next eligible message per partition.

### Storage still owns correctness

For collaborative objects, runtime serialization is not enough. Storage must
also enforce:

- idempotency by `(tenant, project, bundle_id, idempotency_key)`;
- optimistic concurrency by `base_revision`;
- conflict result when a patch is stale.

Canvas patch example:

```json
{
  "base_revision": 17,
  "operations": [
    {
      "op": "update_card",
      "card_id": "U1",
      "set": {
        "description": "Clarified by user."
      }
    }
  ]
}
```

If current revision is not 17, the handler should return conflict and include
enough data for the UI to refresh/reconcile.

## Retry, Ack, And DLQ

Processing loop:

1. `XREADGROUP` message.
2. Decode and validate.
3. Find handler.
4. If no handler, write a result/DLQ record and `XACK`.
5. If handler has partition lock settings, acquire lock.
6. Run handler with timeout.
7. On success, write result, emit reply if possible, `XACK`.
8. On conflict, write conflict result, emit reply if possible, `XACK`.
9. On retryable failure, leave pending or requeue with retry metadata.
10. On max retries, write DLQ and `XACK`.

Suggested result record:

```json
{
  "schema": "kdcube.data_bus.result.v1",
  "message_id": "dbmsg_01HX...",
  "status": "ok",
  "subject": "task_tracker.canvas.patch",
  "object_ref": "canvas:main",
  "data": {
    "revision": 18
  },
  "processed_at": "2026-06-05T00:00:02Z"
}
```

## Security

Ingress must:

- resolve tenant/project from the authenticated platform context;
- verify that the bundle exists and is visible to the user;
- verify that the subject is allowed for the user type and route;
- reject client-supplied actor/tenant/project overrides;
- cap payload size;
- reject unexpected binary data in the JSON package;
- attach session/socket reply metadata from the authenticated connection.

Handlers should still perform object-level authorization. For example, a canvas
handler must verify that the actor can read/write that canvas.

## Interaction With ReAct

Data Bus messages are bundle state traffic. They do not automatically become:

- `ar:` authored replicas;
- `fi:` files;
- `ev:` timeline event paths;
- ReAct timeline blocks;
- ANNOUNCE entries.

If the bundle wants the agent to see something, it must use the existing
conversation ingress path and submit `external_events[]` for a selected
conversation. That is the bridge from bundle state to conversation history.

Task-tracker example:

- user edits text on canvas -> Data Bus only;
- user pins canvas card into chat and sends a prompt -> conversation ingress
  with authored prompt and focused context events;
- agent calls `canvas.patch` -> bundle operation/tool updates canvas storage
  and emits UI event; if the patch should also be represented in the timeline,
  the ReAct tool result/event-source policy handles that separately.

## First Implementation Slices

### Slice 1: SDK types and registry

Add:

- `DataBusMessage`
- `DataBusContext`
- `DataBusResult`
- `data_bus_handler(...)`
- in-memory registry keyed by bundle id and subject
- unit tests for registration and validation

### Slice 2: Socket.IO ingress package

Add:

- `data_bus.publish` Socket.IO handler;
- auth/session reuse from current Socket.IO chat connect path;
- bundle visibility check;
- package validation;
- stream `XADD`;
- Socket.IO ack with accepted/rejected message ids.

Do not route to `process_chat_message`.

### Slice 3: Redis Streams runtime worker

Add:

- bundle-scoped consumer group creation;
- `XREADGROUP` loop;
- handler lookup;
- result stream and DLQ writes;
- retry policy;
- shutdown hooks.

### Slice 4: Partition serialization

Add:

- Redis token locks for `serial_per_partition`;
- tests with two workers/messages for same object;
- stale lock recovery;
- clear docs that this is serial execution, not strict FIFO.

### Slice 5: Reply bridge

Add:

- `ctx.reply.*`;
- comm emission to direct peer/session using `reply` metadata;
- `data_bus.result` default event shape;
- tests for connected and disconnected clients.

### Slice 6: Task-tracker pilot

Wire task-tracker canvas mutation subjects:

- `task_tracker.canvas.patch`;
- `task_tracker.canvas.card.create_text`;
- `task_tracker.canvas.card.attach_file`;
- `task_tracker.canvas.card.comment`;
- `task_tracker.canvas.suggestion.apply`;
- `task_tracker.canvas.suggestion.reject`.

The pilot should use Data Bus for browser-originated canvas state changes, while
chat-focused context remains conversation ingress.

## Tests Required

Unit:

- envelope validation;
- handler decorator registration;
- subject lookup;
- idempotency policy validation;
- lock acquire/release token safety;
- retry and DLQ transitions.

Integration:

- Socket.IO `data_bus.publish` writes to stream;
- a worker consumes and invokes the bundle handler;
- handler reply reaches the same connected peer through comm;
- disconnected client still gets durable state persisted;
- two messages for same `object_ref` do not run concurrently;
- stale `base_revision` returns conflict;
- unauthorized subject is rejected.

Regression:

- `chat_message` still routes only to conversation ingress;
- `/sse/chat` still expects conversation `external_events[]`;
- `comm.service_event(...)` behavior is unchanged;
- project SSE subscriptions are unchanged.

## Open Decisions

1. Whether REST should also support Data Bus publishing in the first release.
2. Whether strict FIFO needs to be first-class now or only after the pilot.
3. Retention defaults for messages/results/DLQ.
4. Exact route for bundle handler discovery: decorator-only first or descriptor
   plus decorator from day one.
5. Whether result streams are queryable by clients or only operational debug.

## Handoff Summary

Implement a new durable Data Bus subsystem alongside existing chat ingress and
comm relay. Reuse authentication and Socket.IO connection context, but keep
routing, Redis streams, handler lifecycle, and semantics separate from chat.

The most important correctness point is concurrency: consumer groups alone do
not serialize messages for the same object. The SDK runtime must provide
per-partition execution locks when a handler requests `serial_per_partition`,
and storage must still enforce idempotency and `base_revision` conflicts.

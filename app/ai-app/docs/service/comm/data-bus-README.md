---
id: ks:docs/service/comm/data-bus-README.md
title: "Data Bus"
summary: "Runtime contract for the bundle-scoped Data Bus: durable non-conversation messages, handler registration, ordering, and comm fanout boundary."
status: active
tags: ["service", "comm", "data-bus", "socketio", "redis-streams", "bundle-runtime"]
keywords:
  [
    "data bus",
    "bundle data messages",
    "redis streams",
    "socket.io",
    "object ordering",
    "board patch",
    "domain state",
  ]
see_also:
  - ks:docs/service/comm/README-comm.md
  - ks:docs/service/comm/comm-system.md
  - ks:docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/auth-bundle-federated-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
---
# Data Bus

The **Data Bus** is the service path for durable, bundle-scoped
messages that are not chat turns.

It is intended for bundle-domain state changes and state signals such as:

- collaborative board patches;
- issue updates;
- wizard or snapshot persistence notifications;
- domain object comments and annotations;
- cross-widget coordination events;
- background service messages that a bundle owns.

It reuses the authenticated transport layer where practical, especially
Socket.IO, but it must not be confused with the conversation bus.

## Boundary

KDCube currently has several message paths. They have different semantics:

| Path | Scope | Durable processing | Typical use |
| --- | --- | --- | --- |
| Conversation ingress | conversation + turn | yes, via the chat task queue | user prompt, followup, steer, chat attachments, `external_events[]` that should enter the timeline |
| Comm relay | user session or tenant/project | no, transient Pub/Sub fanout | chat output, direct operation replies, compact UI refreshes |
| Data Bus | tenant/project/bundle and optional object | yes, via Redis Streams | bundle-owned state mutations and domain messages |
| Telemetry/event sinks | environment-defined | sink-defined | diagnostics, metrics, audit, analytics |

The Data Bus is for messages that the bundle should handle even when no chat
turn is running and no active browser listener exists.

For the full distinction between the conversation event bus and the Data Bus,
including when to bridge between them, see
[Conversation Event Bus And Data Bus](conversation-event-bus-and-data-bus-README.md).

## What It Is Not

The Data Bus is not:

- a chat turn;
- `external_events[]`;
- a timeline write by default;
- an `ev:` artifact path by default;
- a replacement for `comm.service_event(...)` or `comm.project_event(...)`;
- a raw log or telemetry stream.

A bundle may explicitly bridge a handled Data Bus message into a conversation
later, for example by emitting conversation `external_events[]` or adding a
timeline block through an existing ReAct event-source policy. That bridge is a
bundle decision, not a Data Bus default.

## Transport Shape

For browser clients, the implemented transport is a **Data Bus Socket.IO**
package over the already-authenticated Socket.IO connection.

The package is separate from `chat_message`. Ingress routes it to Data Bus
Redis Streams, not to the chat conversation queue.

Example client package:

```json
{
  "schema": "kdcube.data_bus.ingress.v1",
  "bundle_id": "example-collab@1-0",
  "messages": [
    {
      "message_id": "dbmsg_01HX...",
      "subject": "example.board.patch",
      "object_ref": "board:main",
      "idempotency_key": "client-op-7e4f",
      "payload": {
        "base_revision": 17,
        "operations": [
          {
            "op": "update_card",
            "card_id": "U1",
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

### Federated Clients

Clients that do not have a platform browser session can publish to Data Bus
through a bundle-issued federated token. The bundle exposes a public
`federated_token_claim` operation, validates the upstream identity itself, and
calls `issue_federated_data_bus_token(...)`. The client then connects to
Socket.IO with:

```json
{
  "tenant": "tenant-a",
  "project": "project-a",
  "bundle_id": "example-collab@1-0",
  "federated_token": "<short-lived-token>"
}
```

Socket.IO verifies the token, scope, Redis registration, and backing session
before accepting the connection. After that, `data_bus.publish` uses the same
normalized actor/reply metadata as ordinary platform-authenticated sockets.

Use the full bundle recipe in
[Bundle Federated Auth For Data Bus](../../sdk/bundle/auth-bundle-federated-README.md).

## Core Envelope

Each message is normalized by ingress before it is written to the stream.

Canonical fields:

| Field | Meaning |
| --- | --- |
| `message_id` | Stable message id generated by client or ingress. |
| `tenant` / `project` | Runtime scope resolved by ingress. |
| `bundle_id` | Bundle that owns the handler. |
| `subject` | Domain event or command name, such as `example.board.patch`. |
| `object_ref` | Optional object partition key, such as `board:main` or `issue:BUG-123`. |
| `idempotency_key` | Required for mutations; used to dedupe retries. |
| `actor` | Authenticated user/session/principal summary. |
| `payload` | Bundle-defined structured data. |
| `created_at` | Ingress timestamp. |
| `reply` | Optional connected peer/session info for status events back to the UI. |
| `trace` | Optional request/stream ids for diagnostics. |

The payload is bundle-owned. Platform fields are routing, auth, idempotency,
and observability metadata.

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
    subject="example.board.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_board_patch(ctx, message):
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
    subject="example.board.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_board_patch(ctx, message):
    result = await ctx.bundle.board_store.apply_patch(
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
- the processor-owned Data Bus runtime reconciles bundle manifests and starts
  one managed worker per bundle with registered handlers.

The bundle manifest exposes registered subjects as `data_bus_handlers`.

## Replies And Client Updates

Data Bus processing is durable. Client updates are a separate delivery concern.

A handler can reply through comm when a connected peer or session is present:

```python
await ctx.reply.ok({
    "type": "example.board.patch.applied",
    "board": result.board_summary,
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
  -> ack says accepted/partial/rejected by ingress
  -> handler later calls ctx.reply.ok/conflict/error
  -> UI receives chat_service status event for the same session/peer
  -> UI fetches durable state if it needs the full object
```

The ack is not the durable object result. It only confirms stream admission.

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
3. The runtime finds the registered handler by `subject`.
4. If no handler exists, the runtime writes a failure result or DLQ record and
   acknowledges the stream item.
5. If the handler uses `serial_per_partition`, the runtime acquires the
   partition token lock before invoking bundle code.
6. The handler mutates bundle-owned durable storage.
7. The runtime writes a result record and emits an optional reply when reply
   metadata exists.
8. The runtime acknowledges the stream item after the durable mutation and
   result handling path completes.
9. Retryable failures remain pending or are requeued according to the runtime
   retry policy.
10. Non-retryable failures or exhausted retries go to the DLQ.

Suggested result record shape:

```json
{
  "schema": "kdcube.data_bus.result.v1",
  "message_id": "dbmsg_01HX...",
  "status": "ok",
  "subject": "example.board.patch",
  "object_ref": "board:main",
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
- verify that the target bundle exists and is visible to the caller;
- verify that the target subject is registered and visible to the caller;
- reject client-supplied tenant/project/actor overrides;
- attach actor and reply metadata from the authenticated connection;
- cap JSON payload size;
- reject unexpected binary data in the JSON package;
- avoid logging user-authored payload bodies.

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
[domain.revision] created object_ref=board:main revision=18 ref=...
```

Do not log user-authored payload bodies in ingress or storage metadata logs.

## Board-Like Bundle Example

In a bundle with a collaborative board:

- creating a user text card on the board is a Data Bus message;
- uploading a file to the board is a Data Bus message plus bundle hosting;
- dragging a card on the board is a Data Bus patch if the bundle chooses to
  persist placement immediately;
- focusing a board card into chat is a conversation submission because the
  user is asking the assistant to use that context;
- chat paperclip attachments remain conversation `fi:` attachments;
- board-hosted user text and user attachments remain bundle `ext:` refs even
  when focused into chat.

## Tests And Regression Expectations

Core tests should cover:

- envelope validation;
- handler decorator registration and subject lookup;
- idempotency policy validation;
- lock acquire/release token safety;
- retry and DLQ transitions;
- Socket.IO `data_bus.publish` writing accepted messages to the bundle stream;
- worker consumption and handler invocation;
- handler replies reaching the connected peer/session through comm;
- disconnected clients still relying on durable state reads;
- two messages for the same `object_ref` not running concurrently when the
  handler requests `serial_per_partition`;
- stale `base_revision` returning conflict;
- unauthorized subject rejection.

Regression tests should prove:

- Socket.IO `chat_message` still routes only to conversation ingress;
- `/sse/chat` still expects conversation `external_events[]`;
- `comm.service_event(...)`, `comm.project_event(...)`, SSE, and Socket.IO
  fanout remain unchanged;
- ReAct timeline behavior changes only when a bundle explicitly bridges a Data
  Bus result into conversation events.

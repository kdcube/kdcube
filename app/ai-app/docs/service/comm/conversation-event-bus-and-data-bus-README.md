---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
title: "Conversation Event Bus And Data Bus"
summary: "How conversation external events and the bundle-scoped Data Bus fit together, including transport shape, routing, persistence, ordering, replies, and bridge patterns."
status: active
tags: ["service", "comm", "conversation-events", "data-bus", "socketio", "redis-streams", "bundle-runtime"]
keywords:
  [
    "conversation event bus",
    "external_events",
    "data bus",
    "bundle data messages",
    "socket.io data_bus.publish",
    "chat_message",
    "react timeline",
    "bundle state mutation",
    "per object ordering",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
---
# Conversation Event Bus And Data Bus

KDCube has two durable inbound message paths. They can share browser transport,
while each path keeps its own routing, partitioning, and handler contract.

Use this page when deciding whether a browser/widget action should become
conversation context for an agent, or should mutate bundle-owned domain state.

## Two Buses

| Bus | Scope | Ingress event | Durable route | Consumer | Typical payload |
| --- | --- | --- | --- | --- | --- |
| Conversation event bus | conversation + turn | `/sse/chat` or Socket.IO `chat_message` with `external_events[]` | chat task queue and conversation external event store | chat workflow / ReAct owner | user prompt, chat attachment, followup, steer, selected context, snapshot event intended for timeline |
| Data Bus | tenant + project + bundle, optionally object-partitioned | Socket.IO `data_bus.publish` or HTTP `POST /sse/data_bus.publish` with `messages[]` | bundle Data Bus Redis Stream | processor-owned `@data_bus_handler(...)` worker | board patch, issue edit, object comment, widget persistence signal |

The same browser can keep one Socket.IO connection and use both events. That is
transport reuse; routing still follows the selected bus contract.

For the compact routing and partitioning contract, including `agent_id`,
`subject`, `object_ref`, and handler examples, read
[Bus Routing And Partitioning](bus-routing-and-partitioning-README.md).

## Routing Snapshot

```text
conversation event:
  target.agent_id -> tenant/project/user/conversation/agent_id lane
  -> @on_reactive_event run(...)

data bus message:
  messages[].subject -> bundle Data Bus stream
  -> optional object_ref partition lock
  -> @data_bus_handler(ctx, message)
```

## Conversation Event Bus

Conversation events are accepted in the scope of a conversation and turn. They
are intended to become part of the agent-visible history, directly or through
event-source policies.

```text
browser/chat client
  -> chat_message or /sse/chat
  -> external_events[]
  -> conversation external event source
  -> current turn or queued turn
  -> timeline blocks / announce / summaries / compaction
  -> ReAct agent
```

Use this bus when the user action should influence the current or next agent
turn.

Examples:

- user prompt authored in chat;
- chat attachment uploaded with a prompt;
- followup or steer while a turn is open;
- focused context dragged into chat for this request;
- read-only snapshot context that should be visible to ReAct.

The bundle may define event-source policies that decide which accepted events
produce timeline blocks, announce records, or compaction projections.

## Data Bus

Data Bus messages are bundle-owned domain messages. They are durable even if no
chat turn is running and no browser remains connected.

```text
widget/custom UI
  -> Socket.IO data_bus.publish or POST /sse/data_bus.publish
  -> ingress validates bundle + subject + actor visibility
  -> kdcube:data-bus:{tenant}:{project}:{bundle}:messages
  -> processor-owned @data_bus_handler(...)
  -> bundle storage/API update
  -> optional ctx.reply.* through comm relay
```

Use this bus when the bundle must process a state change independently from
conversation history.

Examples:

- patching a collaborative board;
- updating an issue;
- adding a comment to a domain object;
- persisting a widget selection or object annotation;
- asking a bundle service to refresh materialized state.

Data Bus messages do not enter ReAct timelines by default. If a handled Data
Bus message should wake or inform an agent, the bundle explicitly submits
conversation `external_events[]` after or during handling.

## Browser Transport Pattern

One browser Socket.IO connection may carry both contracts:

```ts
socket.emit("chat_message", {
  conversation_id,
  external_events: [
    {
      type: "event.user.prompt",
      payload: { mime: "text/plain", event: { text: "Review selected cards" } },
      reactive: true,
    },
  ],
})

socket.emit("data_bus.publish", {
  schema: "kdcube.data_bus.ingress.v1",
  bundle_id: runtime.bundleId,
  messages: [
    {
      message_id: "dbmsg_...",
      subject: "example.document.patch",
      object_ref: "document-123",
      idempotency_key: "client-op-...",
      payload: {
        base_revision: 12,
        operations: [{ op: "update_item", item_id: "U1", set: { note: "..." } }],
      },
      client: { surface: "document-editor" },
    },
  ],
})
```

The `data_bus.publish` ack means the message was accepted into the stream. It
does not mean the handler already applied the change. Handler completion,
conflict, and error notifications are sent later through the comm relay when
the handler calls `ctx.reply.*`.

With an SSE client, the same Data Bus package is sent by HTTP POST while
`/sse/stream` remains open for replies:

```ts
await fetch(`${baseUrl}/sse/data_bus.publish?stream_id=${encodeURIComponent(streamId)}`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(dataBusPackage),
})
```

This is still Data Bus, not `/sse/chat`. The stream carries handler replies;
the POST carries the inbound durable message.

## Bundle Handler Pattern

Bundles register handled subjects. The runtime owns consumers, retries,
shutdown, and per-partition execution.

```python
from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler

@data_bus_handler(
    subject="example.document.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_document_patch(self, ctx, message):
    result = await self.document_store.apply_patch(
        actor=message.actor,
        object_ref=message.object_ref,
        idempotency_key=message.idempotency_key,
        payload=message.payload,
    )
    if result.get("conflict"):
        await ctx.reply.conflict(result)
        return
    await ctx.reply.ok(result)
```

For collaborative objects, the message payload should include a base revision.
The handler should reject stale revisions or return a conflict payload that the
UI can reconcile.

## Ordering And Idempotency

`ordering="serial_per_partition"` means the runtime must not execute two
handlers for the same partition at the same time. It is not a substitute for
storage-level checks.

Bundle storage remains the authority:

- require `idempotency_key` for mutations;
- persist processed mutation ids when needed;
- compare `base_revision` with the current revision;
- create a new revision only after a valid mutation;
- return conflict details when the base revision is stale.

## Observability

The platform logs Data Bus receipt and stream acceptance at ingress. Logs should
include metadata such as tenant, project, bundle, subject, object ref,
message id, stream id, and socket id. They must not dump user payloads.

Bundle storage should log durable revision or object creation facts, again as
metadata. For example:

```text
[domain.revision] created object_ref=document-123 revision=13 actor=...
```

These two logs let operators distinguish:

1. client submitted the message;
2. ingress accepted it into the stream;
3. bundle handler created durable state.

## Bridge To Conversation

A Data Bus handler can optionally bridge into the conversation event bus:

```text
data_bus handler applies domain change
  -> bundle decides this should wake/inform agent
  -> bundle submits conversation external_events[]
  -> ReAct sees timeline/announce according to event-source policies
```

Do this deliberately. Most domain state mutations should remain outside the
conversation timeline unless the user explicitly asked the agent to react.

## Bundle Builder Checklist

- Choose conversation `external_events[]` when the action is agent context.
- Choose Data Bus when the action mutates bundle state.
- Keep Data Bus payloads structured and bundle-owned.
- Include `object_ref` for object-scoped mutations.
- Include `idempotency_key` for mutations.
- Treat `data_bus.publish` ack as stream admission only.
- Listen for `chat_service` replies when the UI needs handler status.
- Keep durable state readable through normal bundle APIs after reconnect.
- Bridge to conversation events only when an agent should see or act on it.

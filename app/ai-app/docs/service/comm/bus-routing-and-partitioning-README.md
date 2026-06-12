---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
title: "Bus Routing And Partitioning"
summary: "Compact routing and partitioning contract for KDCube conversation event lanes and bundle Data Bus streams."
status: active
tags: ["service", "comm", "conversation-events", "data-bus", "routing", "partitioning", "bundle-runtime"]
updated_at: 2026-06-06
keywords:
  [
    "conversation event lane",
    "agent_id routing",
    "data bus subject",
    "object_ref partitioning",
    "on_reactive_event",
    "data_bus_handler",
    "redis streams",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
---
# Bus Routing And Partitioning

KDCube exposes two durable data-flow surfaces for bundles. They can share
browser transport, but they route different work.

## Surface Map

| Surface | Purpose | Route key | Partition key | Bundle handler |
| --- | --- | --- | --- | --- |
| Conversation event bus | Agent-visible conversation context and reactive events | `agent_id` | `tenant + project + user_id + conversation_id + agent_id` | one `@on_reactive_event` method, usually `run(...)` |
| Data Bus | Bundle-owned state messages and domain mutations | `subject` | bundle stream; optionally `object_ref` for serialized object work | one `@data_bus_handler(...)` per subject |

## Conversation Event Bus

Use this surface when the event should become context for the current or next
agent turn.

```text
client / widget / chat
  -> chat_message or /sse/chat
  -> target.agent_id + external_events[]
  -> conversation event lane
       tenant/project/user/conversation/agent_id
  -> live owner consumes, or proc promotes later
  -> @on_reactive_event run(...), then bundle dispatches internally
  -> ReAct / timeline / artifacts / answer
```

Abilities:

| Ability | Contract |
| --- | --- |
| Target a named agent | Send `target.agent_id` or event-level `external_events[].agent_id`. |
| Keep event order | Events are retained and sequenced inside the selected agent lane. |
| Continue a live turn | Followup/steer events can be consumed by the current turn owner. |
| Recover when no owner consumes | Proc promotes retained events from the same lane into a ready queue wakeup. |
| Materialize context | Event-source policies decide timeline blocks, announce data, summaries, and refs. |

Preferred package shape:

```json
{
  "conversation_id": "conv_123",
  "target": {
    "agent_id": "example.reviewer"
  },
  "external_events": [
    {
      "type": "event.external",
      "event_source_id": "example.review.requested",
      "reactive": true,
      "payload": {
        "mime": "application/json",
        "event": {
          "selection": "card:A1"
        }
      }
    }
  ]
}
```

Use one target `agent_id` for one submitted package. If a UI needs two internal
agents to react, submit two explicit packages so each lane keeps independent
ordering and promotion.

Inside the bundle entrypoint:

```python
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context
from kdcube_ai_app.infra.plugin.bundle_loader import on_reactive_event


class Entrypoint:
    @on_reactive_event
    async def run(self, **params) -> Dict[str, Any]:
        events = params.get("external_events") or []
        agent_id = next(
            (
                event.get("agent_id")
                for event in events
                if isinstance(event, dict) and event.get("agent_id")
            ),
            None,
        )
        if not agent_id:
            ctx = get_current_request_context()
            agent_id = getattr(getattr(ctx, "event", None), "agent_id", None)
        agent_id = agent_id or "default.react.agent"

        if agent_id == "example.reviewer":
            return await self.reviewer.run(**params)
        if agent_id == "example.writer":
            return await self.writer.run(**params)
        return await self.default_agent.run(**params)
```

Redis lane shape:

```text
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:seq:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}:event:{event_id}
```

## Data Bus

Use this surface when the bundle owns a durable state change or domain message.

```text
widget / app-specific client / service
  -> Socket.IO data_bus.publish or POST /sse/data_bus.publish
bundle tool / isolated runtime
  -> comm.data_bus.publish(...) or comm_ctx.data_bus_publish(...)
both routes
  -> bundle_id + messages[].subject
  -> kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
  -> proc worker claims message
  -> handler selected by subject
  -> optional object_ref partition lock
  -> @data_bus_handler(ctx, message)
  -> bundle storage/API mutation
  -> optional ctx.reply.* to connected peer/session
```

Abilities:

| Ability | Contract |
| --- | --- |
| Route to a bundle handler | Use `messages[].subject`; the manifest maps subject to method. |
| Serialize object work | Use `partition_by="object_ref"` and `ordering="serial_per_partition"`. |
| Deduplicate mutations | Require and persist `idempotency_key` for write operations. |
| Survive disconnects | The message is durable even when no browser remains connected. |
| Report status to a peer | Handler calls `ctx.reply.ok/conflict/error` when reply metadata exists. |
| Bridge to an agent | Handler submits conversation `external_events[]` only when an agent should react. |

Server-side bundle producers use the same message semantics as browser
producers. Tools should use this route when the mutation must pass through the
bundle's Data Bus handler, idempotency, object partitioning, and reply path.
See [Data Bus](data-bus-README.md#producer-apis-from-bundle-runtimes).

Client package for either Socket.IO `data_bus.publish` or HTTP
`POST /sse/data_bus.publish`:

```json
{
  "schema": "kdcube.data_bus.ingress.v1",
  "bundle_id": "example-docs@1-0",
  "messages": [
    {
      "message_id": "dbmsg_2026-06-07-10-20-30-123456789",
      "subject": "example.document.patch",
      "object_ref": "document-123",
      "idempotency_key": "client-op-01",
      "payload": {
        "base_revision": 17,
        "operations": [
          {"op": "update_item", "item_id": "item-17", "set": {"title": "Review"}}
        ]
      }
    }
  ]
}
```

Bundle handler:

```python
from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler


class Entrypoint:
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
        await ctx.reply.ok({"revision": result.revision})
        return {"status": "ok", "data": {"revision": result.revision}}
```

Redis stream shape:

```text
kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages
kdcube:data-bus:{tenant}:{project}:{bundle_id}:results
kdcube:data-bus:{tenant}:{project}:{bundle_id}:dlq
```

For `serial_per_partition`, the active handler partition is:

```text
tenant:project:bundle_id:subject:object_ref
```

## Choosing The Surface

| User action | Surface |
| --- | --- |
| Send a prompt or attachment to the assistant | Conversation event bus |
| Follow up or steer while a turn is running | Conversation event bus |
| Ask a named internal agent to review a selected object | Conversation event bus with `target.agent_id` |
| Save a board/card/issue/document patch | Data Bus |
| Persist widget UI state or annotations | Data Bus |
| Mutate state and then ask an agent to react | Data Bus first, then explicit bridge to conversation event bus |

## Bundle Interface Checklist

- Define stable `agent_id` values for internal agents that receive conversation
  events.
- Define stable Data Bus `subject` values for durable bundle-domain messages.
- Use one `@on_reactive_event` entrypoint and dispatch to internal agents by
  `agent_id`.
- Use one `@data_bus_handler(...)` per handled subject.
- Use `object_ref` when messages target a durable object.
- Use idempotency keys for mutations.
- Document the accepted `agent_id`, `subject`, `object_ref`, payload, and reply
  shapes in the bundle's own `interface/README.md`.

---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
title: "Ingress Event Inception"
summary: "How chat ingress accepts built-in user events and authored domain events before they enter the ordered event lane, ready queue, Redis external-event stream, or ReAct."
tags: ["arch", "ingress", "events", "chat", "react", "followup", "steer", "external-events"]
keywords:
  [
    "event inception",
    "external_event",
    "followup",
    "steer",
    "conversation busy",
    "reactive event",
    "iteration credit",
    "redis stream",
    "conversation timeline",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-chat-stream-events-README.md
---

# Ingress Event Inception

This document describes how a client-side action becomes a platform event at
chat ingress. It is the ingress-level map; ReAct-specific folding is documented
in [Shared Timeline Event Bus for Steer and Followup](../../sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md).

## Conversation Bus And Data Bus Boundary

Chat ingress accepts conversation events. These are routed through `/sse/chat`
or Socket.IO `chat_message` with top-level `external_events[]`, then into the
conversation external-event lane and ReAct workflow path.

Data Bus ingress accepts bundle-owned domain messages. These are routed through
Socket.IO `data_bus.publish` or `POST /sse/data_bus.publish` with top-level
`messages[]`, then into bundle-scoped Data Bus Redis Streams and
`@data_bus_handler(...)` workers.

These paths can share the same browser transport, auth session, and comm reply
relay. They do not share durable routes:

| Path | Transport event | Durable route | Runtime consumer |
| --- | --- | --- | --- |
| Conversation bus | `/sse/chat`, Socket.IO `chat_message` | conversation external-event lane + proc wakeup | chat workflow / ReAct owner |
| Data Bus | Socket.IO `data_bus.publish`, `POST /sse/data_bus.publish` | bundle Data Bus stream | proc-owned `@data_bus_handler(...)` worker |

Use the Data Bus for bundle state mutation that should not enter
`external_events[]`, the conversation timeline, or ReAct unless a bundle
explicitly bridges it.

## Event Types And Lane Kinds

Ingress accepts one event model. User prompt, attachment, followup, and steer
are built-in external event types. The existing chat wire fields are authoring
shortcuts; `external_events[]` is the authored domain/UI event form.

| Semantic event type | Client shape | Operational lane kind | Runtime meaning |
|---|---|---|---|
| `event.user.prompt` | `message.message` with no busy conversation | `message` | Publish the accepted event to the lane and enqueue a reactive lane wakeup. |
| `event.user.attachment.*` | message attachments / attachment metadata | same parent lane kind | Hosted/materialized attachment occurrence associated with prompt/followup. |
| `event.user.followup` | `message_kind` / `continuation_kind` / `followup=true` | `followup` | Additional same-conversation user input while a turn may be active. |
| `event.user.steer` | `message_kind` / `continuation_kind` / `steer=true` | `steer` | Control/reorientation event. ReAct treats it as interrupt-like when supported. |
| `event.external`, `event.snapshot`, `event.canvas`, or custom `event.*` | `external_events[]` | `external_event` | Structured product/domain event, e.g. wizard save/request-assistance. |

`payload.target` is bundle-level routing metadata. It can identify a bundle
agent, surface, story, or local flow, but it does not own platform turn routing.
Platform turn routing stays in message-level `active_turn_id` and
`target_turn_id`.

## Authored Domain Event Envelope

```json
{
  "payload": {
    "target": {
      "agent_id": "invoice_wizard",
      "story_id": "invoice:inv-123"
    },
    "external_events": [{
      "type": "event.external",
      "event_source_id": "invoice_intake.wizard.assistance.requested",
      "story_id": "invoice:inv-123",
      "reactive": true,
      "payload": {
        "mime": "application/json",
        "event": {
          "snapshot_ref": "ext:task-tracker/inv_123/invoice-draft.yaml",
          "request": "Review this draft and suggest the next useful step."
        }
      }
    }]
  },
  "active_turn_id": "turn_...",
  "target_turn_id": "turn_..."
}
```

Rules:

- `external_events[].reactive` is the occurrence-level reactive flag
  visible to ingress.
- `external_events[].payload.iteration_credit` is the occurrence-level
  iteration-credit override when the event is consumed live. The runtime cap
  still applies.
- If `reactive` is absent or false, the event does not wake ReAct.
  Reactive events are expensive because they run the agent, so the effective
  reactive decision must be present on the transported occurrence.
- Server-side event source declarations can still define authoring defaults,
  for example `event_source_declaration(..., reactive=True, iteration_credit=2)`,
  but producers/helpers must materialize that default into the occurrence before
  ingress receives it.
- The `external_events[]` object is preserved as-is in the Redis external-event
  lane payload and in the stored `ExternalEventPayload.request.payload` inside
  that lane event's `task_payload`.

## Inception Matrix

| Conversation state | Event family | Ingress action | ReAct wake/credit |
|---|---|---|---|
| Idle/new | `event.user.prompt` | Set conversation `in_progress`, host attachments as `event.user.attachment.*`, append `kind=message` to the event lane, enqueue `ExternalEventLaneWakeup`. | Normal task budget. |
| Busy | `event.user.followup` | Append to per-conversation external-event source with `kind=followup`; the active reader may consume live, otherwise post-save handoff can queue the next lane wake. | Reactive by default; grants bounded live iteration credit. |
| Busy | `event.user.steer` | Append to per-conversation external-event source with `kind=steer`; active owner may interrupt/finalize. | No iteration credit; control path. |
| Idle | Authored domain event, no ingress-visible `reactive=true` | Set/verify conversation row as `idle`, append `kind=external_event` to per-conversation Redis external-event source, return `external_event_recorded`. | No wake, no credit. |
| Busy | Authored domain event, no `reactive=true` | Append `kind=external_event` to per-conversation Redis external-event source, return `external_event_accepted`. | No wake credit. Active owner can still invoke callbacks and policy production if it drains the stream. |
| Idle | Authored domain event, ingress-visible `reactive=true` | Set conversation `in_progress`, append `kind=external_event` to the event lane, enqueue `ExternalEventLaneWakeup`. | Normal task budget. |
| Busy | Authored domain event, `reactive=true` | Append `kind=external_event` to per-conversation Redis external-event source, return `external_event_accepted`. | May grant bounded live iteration credit from the source default or occurrence credit. |

## Redis External-Event Source

The external-event source is a Redis-backed ordered lane. Its identity card is:

```text
tenant + project + user_id + conversation_id + agent_id
```

Current keys include `user_id` and `agent_id` when the payload carries event
metadata:

```text
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:seq:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}:event:{event_id}
```

Payloads produced before this protocol widening can still be read from the
older tenant/project/conversation key during rollout. New traffic should target
the scoped lane. The stream sequence is per lane.

Current retention:

```text
CHAT_EXTERNAL_EVENTS_STREAM_MAX_ENTRIES       default 1024
CHAT_EXTERNAL_EVENTS_STREAM_RETENTION_SECONDS default 7 days
```

This is retained operational state, not durable conversation/artifact storage.
If an event must survive Redis retention independently of ReAct folding, the
event or its payload must also be materialized into conversation storage or a
bundle/application artifact. That durable external-event history is a separate
storage slice and is not completed by the current transport implementation.

The ready queue is not the event body for lane-backed starts. It carries an
`ExternalEventLaneWakeup`, which points at the accepted lane occurrence by
`event_id` and `sequence`. Processor resolves that pointer back to the lane
event's stored `task_payload` before invoking the bundle.

## Concurrency Model

Ingress never mutates the active ReAct timeline directly.

For busy conversations:

1. Ingress reads server conversation state.
2. It records `active_turn_id_at_ingress`.
3. It appends the event to the Redis external-event source with a monotonic
   per-lane sequence and an event-envelope timestamp.
4. If the batch contains a reactive event, lane publish and wake enqueue happen
   atomically. Rejection leaves no accepted lane event behind.
5. Proc resolves the wake and uses lane state `T` to decide whether this wake
   schedules a consumer or is ignored as duplicate/stale.
6. The ContextBrowser reader drains events from the Redis source and folds them
   into its in-memory timeline only while `T.handler.status == open`.
7. The ReAct close gate closes the handler only after it has rendered all lane
   events accepted by the reader.
8. After artifacts persist, ContextBrowser performs post-save handoff for any
   unconsumed reactive lane work and then releases `T.consumer`.

This prevents two writers from mutating one in-memory timeline. Redis ordering
prevents accepted external events from being unordered relative to each other;
lane state `T` coordinates the race between ingress wakeups, proc scheduling,
reader acceptance, and handler close.

For idle non-reactive authored events, the current implementation records only
to the Redis external-event source and leaves the conversation state idle. That
is useful for low-latency event admission, but it is not sufficient as permanent
business history.

## ReAct Iteration Credit

Live reactive events can extend the active turn's effective loop ceiling:

```text
effective_max_iterations = base_max_iterations + reactive_iteration_credit
```

Credit is bounded by `reactive_event_iteration_credit_cap`. For authored
external events, the occurrence must be explicitly reactive:

```json
{
  "routing": {
    "reactive": true
  }
}
```

After the occurrence is explicitly reactive, the server-side event source
declaration can define default credit:

```python
event_source_declaration(
    event_source_id="my_app.wizard.assistance.requested",
    reactive=True,
    iteration_credit=2,
)
```

An occurrence can override that credit inside:

```json
{
  "routing": {
    "reactive": true,
    "iteration_credit": 2
  }
}
```

The runtime still clamps this through its configured cap. `steer` remains a
control path and does not mint iteration credit.

## Operator Service Events

Ingress emits operator-visible service events under the `event` namespace:

```text
event.external.recorded
event.external.accepted
```

These service events are observability signals. They are not the durable event
record itself.

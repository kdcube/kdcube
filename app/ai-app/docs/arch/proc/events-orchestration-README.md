---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
title: "Proc Events Orchestration"
summary: "Processor-side orchestration for external events: ready-queue wakeups, lane payload resolution, lane-state scheduling, and boundaries with ReAct timeline folding."
status: active
tags: ["arch", "proc", "events", "external-events", "redis", "processor", "react"]
keywords:
  [
    "proc events orchestration",
    "ExternalEventPayload",
    "ExternalEventLaneWakeup",
    "event lane",
    "ready queue wakeup",
    "processor event resolution",
    "conversation event lane state",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/processor-arch-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
---

# Proc Events Orchestration

This note explains the processor side of the current external-event flow. It is
the queue and invocation map; event semantics and ReAct rendering policies live
in the SDK event-source docs.

## Current Shape

```text
Client
  |
  v
Ingress
  validates request
  builds ExternalEventPayload
  publishes lane-backed event to Redis event lane
  queues ExternalEventLaneWakeup for reactive lane-backed starts
  |
  v
Redis ready queue
  contains a processor wakeup, not the lane-backed request body
  |
  v
Processor
  claims queue item
  if item is ExternalEventLaneWakeup:
    read event_lane.event_id from Redis event lane
    rebuild ExternalEventPayload from event.task_payload
    schedule/ignore the wake under the conversation lane state table T
    attach bundle_call_context.event_lane_wakeup
  else:
    validate item directly as ExternalEventPayload
  build communicator/accounting/runtime context
  invoke bundle @on_reactive_event
  |
  v
Bundle / ReAct
  folds lane events into the timeline before model rendering
```

The important boundary is:

- the ready queue wakes proc;
- the Redis event lane owns the ordered event body;
- lane state `T` synchronizes proc, the handler, and the reader;
- the bundle/ReAct runtime owns timeline folding.

## Queue Item Kinds

| Queue item | Meaning |
|---|---|
| `ExternalEventPayload` | Ordinary processor payload that already contains the event body. |
| `ExternalEventLaneWakeup` | Pointer to a lane event. It carries lane identity and queue context, but no `request`. |

`ExternalEventLaneWakeup` is intentionally small. It contains:

```text
event_lane.tenant
event_lane.project
event_lane.user_id
event_lane.conversation_id
event_lane.agent_id
event_lane.event_id
event_lane.sequence
```

Processor resolves it by calling the external-event source for that lane and
reading `event.task_payload`. Before invoking the bundle for a wake, proc asks
the event-bus orchestrator to schedule the conversation lane consumer from the wake
timestamp. Duplicate, stale, or already-covered wakeups are ignored without
mutating the lane event.

## Why The Queue Does Not Carry Request

For lane-backed starts, the accepted event already exists in the ordered lane.
If the ready queue also carried a copy of `request`, there would be two sources
of truth:

- queue order and payload copy;
- lane order and lane payload.

The current rule avoids that split:

```text
queue item = wakeup
lane event = ordered occurrence + task_payload
```

This is why BaseWorkflow/ReAct reads prompt and attachment blocks from the lane
fold, not from the wakeup object.

## Processor Resolution

When proc claims work:

1. parse the raw queue item;
2. if it is a wakeup, build the lane source from `wakeup.event_lane`;
3. read the lane event by `event_id`;
4. validate `event.task_payload` as `ExternalEventPayload`;
5. patch the event occurrence fields from the lane event;
6. call `schedule_consumer_from_wake(...)` under lane state `T`;
7. add `bundle_call_context.event_lane_wakeup` for diagnostics;
8. invoke the bundle through the normal reactive-entrypoint path when the wake
   schedules work.

The resolved payload is still a full `ExternalEventPayload`, so communicator,
economics, runtime context, and non-ReAct workflows can use the same processor
execution machinery.

## Lane State, Live Reader, And Handoff

Busy-conversation `followup`, `steer`, and authored `external_event` records are
published to the same event lane.

The live ReAct handler opens the lane state for its turn. The ContextBrowser
reader drains the lane and folds accepted events into the in-memory timeline
only while `T.handler.status == open`. Accepting lane events and advancing
`T.last_processed_*` happens under the same short state lock.

When ReAct has a candidate final answer, its close gate compares the last event
timestamp it rendered with `T.last_processed_event_timestamp`. If the reader
accepted newer timeline material, the handler stays open and ReAct continues
from the updated timeline. After the handler closes and turn artifacts persist,
ContextBrowser performs the post-save handoff: unconsumed reactive lane work is
woken through `EventLaneWakePublisher`.

That handoff queues an `ExternalEventLaneWakeup`, not a request copy. The next
processor task therefore goes through the same resolution path as an initial
lane-backed reactive start. Proc does not scan the lane after task completion.

`steer` is a live control event. Stale steer events do not start a later turn
after the target turn has expired.

## Non-Reactive Idle Events

An idle authored `external_event` with no ingress-visible
`routing.reactive=true` is recorded in the event lane and no proc wakeup is
queued. This keeps admission cheap and avoids waking the model for events that
do not request work.

That path is retained operational state only. Durable idle event-history
materialization is a separate pending feature.

## What Proc Does Not Own

Proc does not own:

- event-source policy declarations;
- timeline block production details;
- ReAct projection/announce/compaction behavior;
- persistent product storage for idle non-reactive events;
- final model-visible rendering.

Those live in the SDK event-source subsystem, ReAct `ContextBrowser`, and
bundle/application code.

## Relationship To Scheduler Design

The current implementation still uses the shipped processor ready queues. The
event lane now supplies per-conversation/per-agent ordering for lane-backed
events, but it is not yet the full conversation-native scheduler.

The forward-looking scheduler design is:

- [Design: Conversation Scheduler With Redis Streams](design/conversation-scheduler-streams-README.md)

That design makes conversation ownership the scheduler unit. The current
events-orchestration path is a smaller step: ordered lane events, lane state
`T`, processor wakeups, reader folding, and post-save handoff.

## Simulator

The end-to-end event-bus simulator lives next to the focused tests:

- [event-bus-simulator-README.md](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/event-bus-simulator-README.md)
- [test_event_bus_state.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/test_event_bus_state.py)

The simulator names each method after the production site it represents:
ingress, wake publication, proc wake resolution, BaseWorkflow/ContextBrowser
handler setup, ContextBrowser reader drain, ReAct close gate, artifact
persistence, and post-save handoff.

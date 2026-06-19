---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-orchestrator-README.md
title: "Conversation Event Bus Orchestrator"
summary: "Design for the conversation external-event bus orchestrator and its shared Redis synchronization table."
status: draft
tags: ["service", "comm", "conversation-event-bus", "external-events", "react", "redis"]
updated_at: 2026-06-19
keywords:
  [
    "conversation event bus orchestrator",
    "external event lane",
    "react followup",
    "event lane state",
    "wake queue",
    "sync table",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
---
# Conversation Event Bus Orchestrator

The conversation event bus carries accepted conversation-scoped
`external_events[]` batches from ingress to the ReAct runtime. The
orchestrator coordinates the lane through one shared synchronization record
`T`.

`T` is stored beside the conversation external-event lane:

```text
<conversation-external-event-lane-key>:state
<conversation-external-event-lane-key>:state:lock
```

The lane identity is:

```text
tenant + project + user_id + conversation_id + agent_id
```

## State

The table stays intentionally small:

```text
T.handler.turn_id
T.handler.status                         open | closed
T.handler.status_at

T.last_processed_event_timestamp
T.last_processed_reactive_event_timestamp

T.consumer.status                        active | scheduled | none
T.consumer.status_at
```

`T.last_processed_event_timestamp` is the lane processed cursor for all event
types. `T.last_processed_reactive_event_timestamp` is the same cursor for
reactive events only. Both values come from event-envelope timestamps. They
are not Redis Stream ids, sequence numbers, or wall-clock timestamps.

`T.consumer.status_at` is a real lane-local acknowledgement timestamp written
by proc or the Reader/Consumer. It is not derived from processor heartbeat.

## Parties

| Party | Responsibility |
| --- | --- |
| Ingress | Normalize one `external_events[]` batch, prepare lane records, and accept the batch. Reactive batches are accepted only through one atomic Redis operation that writes the lane records and enqueues one wake. |
| Stream | Store accepted lane events. |
| Wake queue | Carry a nudge for the lane, including the first reactive event timestamp. |
| Proc | Read a wake and, under `lock(T)`, decide whether to set `T.consumer.status = scheduled`. |
| BaseWorkflow / ContextBrowser handler setup | When the turn runtime is constructed and before timeline load, write `T.handler.turn_id`, `T.handler.status = open`, and `T.handler.status_at`. |
| ReAct handler close gate | Close the handler only if the candidate answer saw all processed lane events. |
| Reader/Consumer | Start after handler open, set `T.consumer.status = active`, accept lane events into the live turn path while `T.handler.status == open`, and update `T.last_processed_*`. |
| Turn finalization | Persist turn artifacts after the ReAct handler has closed. |
| ContextBrowser post-save handoff | After persistence and before releasing the Reader/Consumer, inspect lane/table state and publish a wake through `EventLaneWakePublisher` when reactive work remains. |

## Flow

```text
Client / widget / API
  sends one external_events[] batch
        |
        v
Ingress
  normalize event ids, timestamps, logical paths, agent_id
  stamp one batch_id on every event in this user action
  prepare lane records
  if batch has reactive event:
    atomically:
      publish prepared records to Stream
      enqueue one Wake Queue item for the first reactive event
    if wake enqueue is rejected:
      reject the request
      no lane event is visible
  else:
    publish prepared records to Stream
        |
        v
Stream
  durable accepted events
        |
        v
Wake Queue
  lane nudge, not semantic event selection
        |
        v
Proc
  reads wake item
  lock(T)
    if wake event timestamp <= T.last_processed_reactive_event_timestamp:
      ignore stale wake
    else if T.consumer.status == active and active acknowledgement is fresh:
      leave for active Consumer
    else if T.consumer.status == scheduled and scheduled acknowledgement is fresh:
      leave for scheduled Consumer
    else:
      set T.consumer.status = scheduled
      set T.consumer.status_at = now
      schedule processor turn
  unlock(T)
        |
        v
BaseWorkflow / ContextBrowser Handler Setup
  lock(T)
    set T.handler.turn_id = handler runtime turn id
    set T.handler.status = open
    set T.handler.status_at = now
  unlock(T)
        |
        v
Reader / Consumer Activation
  lock(T)
    if T.handler.status == open:
      set T.consumer.status = active
      set T.consumer.status_at = now
    else:
      do not start drain
  unlock(T)
        |
        v
Initial Lane Drain / Live Lane Drain
  fetch Stream entries outside lock(T)
  for accepted entries:
    lock(T)
      if T.handler.status == open:
        contribute entries to the live turn path
        mark the source consumed for accepted entries
        update T.last_processed_event_timestamp
        update T.last_processed_reactive_event_timestamp for reactive entries
        set T.consumer.status = active
        set T.consumer.status_at = now
      else:
        leave fetched entries unconsumed in the lane
    unlock(T)
        |
        v
ReAct Handler Close Gate
  ReAct has a candidate answer
  handler_processed_event_timestamp =
    max event timestamp in the last timeline snapshot ReAct used
    to produce that candidate answer

  lock(T)
    if handler_processed_event_timestamp < T.last_processed_event_timestamp:
      leave T.handler.status = open
      ReAct reads the updated timeline and continues
    else:
      set T.handler.status = closed
      set T.handler.status_at = now
      snapshot T.last_processed_event_timestamp
      snapshot T.last_processed_reactive_event_timestamp
  unlock(T)
        |
        v
Turn Finalization
  persist turn log, timeline, stream artifacts
        |
        v
ContextBrowser Post-save Handoff
  inspect Stream after the timeline cursor
  if unprocessed reactive events remain:
    EventLaneWakePublisher publishes one wake
        |
        v
Reader / Consumer Release
  set T.consumer.status = none
  set T.consumer.status_at = now
```

Ingress does not decide whether the conversation is idle or busy. It accepts
the batch into the lane and wakes proc when reactive work exists. The lane and
`T` decide scheduling and reader acceptance.

Reactive ingress has one transaction boundary: the lane write and wake enqueue
stand or fall together. If the processor queue rejects the wake because of
backpressure or an enqueue error, ingress rejects the request and does not
write any event from that batch to the lane. Queue mechanics must not mark an
already accepted lane event failed.

The queue admission script receives capacity as a numeric snapshot. It does
not discover process heartbeat keys. Process heartbeat writers maintain a
service-scoped capacity index, and admission reads that bounded index before
running the Redis script.

The wake queue does not choose which lane event is processed. It wakes the
lane. Under the conversation lock, the Reader and ReAct runtime use the lane
state and event timestamps to decide what can be accepted.

## Freshness

Proc freshness checks use explicit inputs:

```text
active_is_fresh =
  T.consumer.status == active
  and now - T.consumer.status_at <= event_bus.consumer.active_ttl_ms

scheduled_is_fresh =
  T.consumer.status == scheduled
  and now - T.consumer.status_at <= event_bus.consumer.scheduled_ttl_ms
```

`now` must come from the same clock source used by the state table. The first
implementation uses Redis-backed state and may use process UTC timestamps
until Redis `TIME` is wired.

## Probe-Based Wakeup

There are two separate channels:

```text
Conversation event lane
  Redis stream of accepted external events for one
  tenant + project + user_id + conversation_id + agent_id lane.
  It contains user events and small control events.

Processor wake queue
  Processor-ready queue item saying "this lane has reactive work".
  It is not itself in the conversation event lane.
```

The handler and the consumer are the same ReAct runtime owner seen from two
angles:

```text
Handler
  owns the turn timeline and close gate.

Consumer
  reads the conversation event lane for that same turn.
```

If the handler is open but its worker died, `T.handler.status = open` can remain
behind. A later turn must distinguish these two cases:

```text
live open handler
  the existing turn is still reading the lane; do not steal it

stale open handler
  the existing turn is gone or no longer reading the lane; reclaim it
```

`T.handler.status_at` does not prove liveness. It only says when the handler
state was written. The live signal is lane consumption:

```text
T.consumer.status_at
  fallback "last known consumer acknowledgement" timestamp

probe ack
  immediate proof that the currently open consumer can still read this lane
```

The probe protocol is intentionally minimal. When a new turn sees a different
open handler, it appends one control event to the same conversation event lane:

```json
{
  "kind": "probe",
  "probe_id": "probe_abc123",
  "for_turn": "turn-A"
}
```

The current open consumer reads the same stream in order. If it is alive and
the probe is for its own turn, it writes one short-lived ack key:

```text
SETEX <conversation-event-lane-key>:probe:probe_abc123 30 "turn-A"
```

That ack means exactly this:

```text
turn-A read this lane through probe_abc123
```

It does not say that any specific user event has been rendered, answered, or
persisted. It only proves the open consumer is still alive on this lane. The
consumer already owns the normal rule for reading and applying all lane events
before and around the probe.

Probe events are control events:

- they are stored in the same lane so the live consumer can see them;
- they are not rendered into the ReAct timeline;
- they are not reactive;
- they are not promotable into their own processor turn;
- they do not advance `T.last_processed_event_timestamp`;
- they do not advance `T.last_processed_reactive_event_timestamp`;
- they may advance the local Redis stream cursor in the reading consumer.

### Healthy Flow

```text
Components:
  C  = client/widget/API
  I  = ingress
  L  = conversation event lane
  Q  = processor wake queue
  P  = processor
  T  = event-lane state table
  R  = ReAct handler + consumer for one turn

Time ->

C        I        L        Q        P        T        R
|        |        |        |        |        |        |
| E1 --->|        |        |        |        |        |
|        | write E1        |        |        |        |
|        |------->|        |        |        |        |
|        | enqueue wake(E1)|        |        |        |
|        |---------------->|        |        |        |
|        |        |        | wake -->        |        |
|        |        |        |        | lock(T)         |
|        |        |        |        | set consumer=scheduled
|        |        |        |        |------->|        |
|        |        |        |        |        | open handler turn-A
|        |        |        |        |        |<-------|
|        |        |        |        |        | consumer=active
|        |        |        |        |        |<-------|
|        |        | read E1 in turn-A        |        |
|        |        |<-----------------------------------|
|        |        |        |        |        | last_processed=E1
|        |        |        |        |        |<-------|
|        |        |        |        |        | close handler turn-A
|        |        |        |        |        |<-------|
|        |        |        |        |        | consumer=none
|        |        |        |        |        |<-------|
```

### Stale-Open Problem Without Probe

This is the failure mode the reclaim logic addresses:

```text
Time ->

L        Q        P        T                         R(turn-A)       R(turn-B)
|        |        |        |                         |               |
| E1     |        |        |                         |               |
|------->| wake   |        |                         |               |
|        |------->| set scheduled                    |               |
|        |        |-------->                         |               |
|        |        |        | open handler=turn-A     |               |
|        |        |        |<------------------------|               |
|        |        |        | consumer=active         |               |
|        |        |        |<------------------------|               |
|        |        |        |                         | crash/reload  |
|        |        |        | handler remains open    X               |
| E2     |        |        |                         |               |
|------->| wake   |        |                         |               |
|        |------->| set scheduled, build turn-B      |               |
|        |        |        |                         |               | open_handler
|        |        |        | sees open turn-A        |               |
|        |        |        | handler_status_at exists|               |
|        |        |        |                         |               |
```

If the runtime treats `handler_status_at` as liveness, turn-B may defer forever
to a handler that no longer exists. That blocks the lane even though E2 is
durably accepted and needs a new turn.

### Fixed Flow With Probe

```text
Time ->

L                  Q        P        T                         R(turn-A)       R(turn-B)
|                  |        |        |                         |               |
| E1               |        |        |                         |               |
|----------------->| wake   |        |                         |               |
|                  |------->| set scheduled                    |               |
|                  |        |------->|                         |               |
|                  |        |        | open handler=turn-A     |               |
|                  |        |        |<------------------------|               |
|                  |        |        | consumer=active         |               |
|                  |        |        |<------------------------|               |
|                  |        |        |                         | crash/reload  |
|                  |        |        | handler remains open    X               |
| E2               |        |        |                         |               |
|----------------->| wake   |        |                         |               |
|                  |------->| set scheduled, build turn-B      |               |
|                  |        |        |                         |               | open_handler
|                  |        |        | sees open turn-A        |               |
| probe(for A)     |        |        |                         |               |
|<-------------------------------------------------------------|               |
|                  |        |        | wait for ack            |               |
|                  |        |        | no ack before timeout   |               |
|                  |        |        | reclaim handler=turn-B  |               |
|                  |        |        |<---------------------------------------|
|                  |        |        | consumer=active         |               |
|                  |        |        |<---------------------------------------|
| read E2          |        |        |                         |               |
|<-------------------------------------------------------------------------|
|                  |        |        | last_processed=E2       |               |
|                  |        |        |<---------------------------------------|
```

If turn-A is alive, the flow stops earlier:

```text
L                  T                         R(turn-A)       R(turn-B)
|                  |                         |               |
| probe(for A)     |                         |               |
|<-------------------------------------------| open_handler  |
|                  |                         |               |
|                  | ack key = turn-A        |               |
|                  |<------------------------|               |
|                  |                         | sees ack       |
|                  |                         |               |
|                  | keep handler=turn-A     | defer turn-B   |
```

In this case turn-B does not steal the lane. Turn-A remains responsible for
consuming the lane and updating the turn timeline.

### Fallback When Probe Is Unavailable

Some isolated tests or older source implementations may not expose probe
methods. In that case `open_handler()` falls back to the consumer timestamp:

```text
defer if:
  T.consumer.status in {active, scheduled}
  and T.consumer.status_at is fresh

reclaim if:
  T.consumer.status_at is missing, malformed, or stale
```

The fallback still does not use `T.handler.status_at` as liveness.

### Ordering Rule

Normal close-gate ordering uses event-envelope timestamps plus event ids for
same-timestamp disambiguation. It must not infer ordering from turn ids.

Probe read-through uses Redis stream order only for the immediate liveness
question: if a consumer reads the lane through the probe and writes the ack,
the consumer is alive on that lane.

## Tested SDK Primitive

The current isolated SDK/runtime primitive is:

```python
orchestrator = ConversationEventBusOrchestrator(table=table)

await orchestrator.schedule_consumer_from_wake(
    wake_event_timestamp="2026-06-10T10:00:00Z",
)

await orchestrator.open_handler(turn_id=turn_id)
await orchestrator.mark_consumer_active(turn_id=turn_id)

await orchestrator.accept_events_for_open_handler(
    events,
    turn_id=turn_id,
    accept=accept_under_lock,
)

await orchestrator.try_close_handler(
    turn_id=turn_id,
    handler_processed_event_timestamp=handler_processed_event_timestamp,
)

await orchestrator.mark_consumer_none(turn_id=turn_id)
```

Wake publication is a separate primitive:

```python
from kdcube_ai_app.apps.chat.sdk.events.event_bus import (
    EventLaneWakePublisher,
    RedisEventLaneWakeEnqueuer,
)

publisher = EventLaneWakePublisher(
    RedisEventLaneWakeEnqueuer(redis=redis, tenant=tenant, project=project)
)
await publisher.publish_for_event(
    payload=payload,
    event=event,
    tenant=tenant,
    project=project,
    user_id=user_id,
    conversation_id=conversation_id,
    agent_id=agent_id,
    reason="reactive_event",
)
```

The Redis enqueuer writes the wake to the normal processor ready queue for the
tenant/project/user type. The orchestrator does not know the chat queue
implementation, and proc must not scan the lane after task completion to
manufacture post-completion wakeups.

The standalone tests cover:

- stale wake suppression;
- fresh active/scheduled consumer suppression;
- stale active consumer rescheduling;
- full wake -> scheduled -> handler open -> consumer active -> accept -> close path;
- close rejection when ReAct did not see a newly accepted lane event;
- reader rejection after handler close;
- reader/close lock ordering when the Reader has a new event in hand.

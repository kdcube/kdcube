---
id: repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-orchestrator-README.md
title: "Conversation Event Bus Orchestrator"
summary: "Current conversation external-event bus orchestrator and its shared Redis synchronization table."
status: active
tags: ["service", "comm", "conversation-event-bus", "external-events", "react", "redis"]
updated_at: 2026-06-25
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
      leave for scheduled start reservation
    else:
      set T.consumer.status = scheduled
      set T.consumer.status_at = now
      schedule processor turn
  unlock(T)
        |
        v
BaseWorkflow / ContextBrowser Handler Setup
  lock(T)
    if another handler is open and its active consumer acknowledgement is fresh:
      raise ExternalEventLaneTurnSuperseded
    if another handler is open but has no fresh active consumer:
      reclaim it
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

Proc wake scheduling and handler-owner reclaim use related but different
freshness checks.

Proc wake scheduling uses explicit inputs:

```text
active_is_fresh =
  T.consumer.status == active
  and now - T.consumer.status_at <= event_bus.consumer.active_ttl_ms

scheduled_is_fresh =
  T.consumer.status == scheduled
  and now - T.consumer.status_at <= event_bus.consumer.scheduled_ttl_ms
```

Here `scheduled` is a start reservation: proc has already decided that a turn
should load/open the handler, so duplicate wakes should not start additional
competing turns during that short bundle-load / entrypoint window.

Handler-owner reclaim is stricter:

```text
owner_is_live =
  T.consumer.status == active
  and now - T.consumer.status_at <= event_bus.consumer.active_ttl_ms
```

`scheduled` is not handler liveness. A later user event can schedule a new wake
while an old handler is still stuck open. If `scheduled` were treated as proof
that the old handler is alive, the new turn would be rejected as superseded
before it could reclaim the stale owner.

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
  last known consumer acknowledgement timestamp
```

The handler and consumer are the same runtime owner, but
`T.consumer.status_at` answers the liveness question only when the status is
`active`:

```text
fresh active consumer_status_at
  an owner recently acknowledged the lane; defer to it at handler open

missing or stale consumer_status_at
  no owner has acknowledged the lane recently; reclaim it

fresh scheduled consumer_status_at
  suppresses duplicate proc wake scheduling only; it does not protect an open
  handler from reclaim
```

The reclaim window is a recovery timeout, not a correctness boundary. A live
but starved old turn can still be reclaimed. That is acceptable only if the
old turn detects the ownership switch before it commits output. The ReAct
runtime therefore fences the old turn in two places:

```text
consumer/listener fence
  when the live consumer refreshes the owner lease, acknowledges consumption,
  or accepts lane events, it verifies that T.handler.turn_id is still its turn.

finish fence
  before answer emission and turn persistence, BaseWorkflow asks the
  ContextBrowser to verify that T.handler.turn_id is still this turn.
```

If either fence sees a newer owner, it raises
`ExternalEventLaneTurnSuperseded`. That exception lands in the normal turn
exception path, which removes the current turn from the index. Because the
finish fence runs before committed answer emission and persistence, the
superseded turn does not save a partial or stale turn. The newer owner reads
the last committed turn from the index and proceeds.

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

### Stale-Open Recovery

This is the failure mode the reclaim logic addresses. Turn-A was the owner,
but its worker died or stopped acknowledging the lane. A later wake must not
defer forever to `T.handler.status = open`.

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
|        |        |        | no fresh active consumer|               |
|        |        |        | reclaim handler=turn-B  |               |
|        |        |        |<---------------------------------------|
|        |        |        | consumer=active         |               |
|        |        |        |<---------------------------------------|
| read E2|        |        |                         |               |
|<-------------------------------------------------------------------------|
|        |        |        | last_processed=E2       |               |
|        |        |        |<---------------------------------------|
```

If the runtime treats `handler_status_at` as liveness, turn-B may defer forever
to a handler that no longer exists. That blocks the lane even though E2 is
durably accepted and needs a new turn.

### Live-But-Starved Race

This is the important corner case. Turn-A is not dead, but it is starved long
enough for its consumer acknowledgement to become stale. Turn-B is allowed to
reclaim. Correctness depends on turn-A detecting that it no longer owns the
lane before it saves anything.

```text
Time ->

T                         R(turn-A)                         R(turn-B)
|                         |                                 |
| handler=turn-A          |                                 |
| consumer active         |                                 |
|<------------------------|                                 |
|                         | busy/starved, no consumer ack   |
| consumer_status_at stale|                                 |
|                         |                                 | open_handler
| handler=turn-B          |                                 | reclaim succeeds
|<----------------------------------------------------------|
|                         | resumes                         |
|                         | refresh owner or ack consumer   |
|                         | sees handler=turn-B             |
|                         | raises ExternalEventLaneTurnSuperseded
|                         | normal turn exception handler   |
|                         | delete_turn(index_only)         |
|                         | no committed answer/persist     |
```

The same exception can also be raised at `finish_turn`. That covers a case
where the old turn did not touch the lane again after it was reclaimed but is
about to emit or persist its final output.

### Ordering Rule

Normal close-gate ordering uses event-envelope timestamps plus event ids for
same-timestamp disambiguation. It must not infer ordering from turn ids.

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
- fresh active consumer wake suppression;
- fresh scheduled duplicate-wake suppression;
- stale-open handler reclaim even when a later wake freshly marked
  `T.consumer.status = scheduled`;
- stale active consumer rescheduling;
- full wake -> scheduled -> handler open -> consumer active -> accept -> close path;
- close rejection when ReAct did not see a newly accepted lane event;
- reader rejection after handler close;
- reader/close lock ordering when the Reader has a new event in hand;
- browser-side superseded-turn detection when a running turn sees a newer
  handler owner.

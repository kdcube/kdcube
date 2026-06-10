---
id: ks:docs/sdk/events/conversation-event-lane-state-README.md
title: "Conversation Event Lane State"
summary: "SDK/runtime reference for the Redis state record that synchronizes conversation external-event ingress, readers, handlers, and wake handoff."
status: draft
tags: ["sdk", "events", "external-events", "redis", "react", "synchronization"]
updated_at: 2026-06-10
keywords:
  [
    "conversation event lane state",
    "external event bus state",
    "handler status",
    "reactive event wake",
    "Redis key",
    "event timestamp",
  ]
see_also:
  - ks:docs/service/comm/conversation-event-bus-orchestrator-README.md
  - ks:docs/sdk/events/external-events-journey-and-handling-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
---
# Conversation Event Lane State

The conversation event lane state is the synchronization record for one
conversation/agent external-event lane. The first implementation stores it as
JSON in Redis and updates it under a short Redis lock.

```text
<conversation-external-event-lane-key>:state
<conversation-external-event-lane-key>:state:lock
```

The lane identity is:

```text
tenant + project + user_id + conversation_id + agent_id
```

## Fields

```text
T.handler.turn_id
T.handler.status                         open | closed
T.handler.status_at

T.last_processed_event_timestamp
T.last_processed_reactive_event_timestamp

T.consumer.status                        active | scheduled | none
T.consumer.status_at
```

Field meanings:

| Field | Writer | Meaning |
| --- | --- | --- |
| `T.handler.turn_id` | BaseWorkflow / ContextBrowser handler setup | Runtime turn id of the currently open/last closed handler. |
| `T.handler.status` | BaseWorkflow / ContextBrowser handler setup and ReAct close gate | `open` while the handler can accept lane events into this turn; `closed` after the close gate succeeds. |
| `T.handler.status_at` | Same writer as `T.handler.status` | Latest acknowledgement timestamp for `T.handler.status`. |
| `T.last_processed_event_timestamp` | Reader/Consumer while holding `lock(T)` | Maximum event-envelope timestamp accepted into the live turn path for any event type. |
| `T.last_processed_reactive_event_timestamp` | Reader/Consumer while holding `lock(T)` | Maximum event-envelope timestamp accepted into the live turn path for reactive events. |
| `T.consumer.status` | Proc and Reader/Consumer while holding `lock(T)` | `scheduled`, `active`, or `none`. |
| `T.consumer.status_at` | Same writer as `T.consumer.status`, and Reader acknowledgements | Latest real lane-local acknowledgement timestamp for `T.consumer.status`. |

`T.last_processed_*` values come from event-envelope timestamps. They are not
Redis Stream ids, internal sequence numbers, current timeline timestamps, or
wall-clock `now`.

## State Rules

Ingress prepares event batches before they are visible in the lane. For a
reactive batch, ingress accepts the batch only through an atomic Redis
operation that:

```text
publish prepared lane records
enqueue one wake for the first reactive event
```

If that atomic operation is rejected, no lane event from the batch exists and
the client receives a rejection. Ingress does not write `T.handler.*`,
`T.consumer.*`, or `T.last_processed_*`.

Proc reads wake items. Under `lock(T)`, proc sets:

```text
T.consumer.status = scheduled
T.consumer.status_at = now
```

only when the wake is not stale and no fresh active/scheduled Consumer is
already responsible for the lane.

BaseWorkflow / ContextBrowser handler setup sets:

```text
T.handler.turn_id = handler runtime turn id
T.handler.status = open
T.handler.status_at = now
```

Reader/Consumer activation sets:

```text
T.consumer.status = active
T.consumer.status_at = now
```

only when `T.handler.status == open`.

Reader/Consumer acceptance of lane entries is atomic with state updates:

```text
lock(T)
  if T.handler.status == open:
    contribute entries to the live turn path
    mark accepted source entries consumed
    update T.last_processed_event_timestamp
    update T.last_processed_reactive_event_timestamp
    set T.consumer.status = active
    set T.consumer.status_at = now
  else:
    leave fetched entries unconsumed in the lane
unlock(T)
```

ReAct close gate compares a value from ReAct with a value from `T`:

```text
handler_processed_event_timestamp =
  max event timestamp in the last timeline snapshot ReAct used
  to produce its candidate answer

if handler_processed_event_timestamp < T.last_processed_event_timestamp:
  keep T.handler.status = open
  ReAct continues
else:
  set T.handler.status = closed
  set T.handler.status_at = now
```

Turn finalization runs after the handler is closed. After artifacts persist,
ContextBrowser publishes one wake when unprocessed reactive lane work remains.
Then the Reader/Consumer is released:

```text
T.consumer.status = none
T.consumer.status_at = now
```

## Freshness

Freshness is calculated from table values and configured TTLs:

```text
active_is_fresh =
  T.consumer.status == active
  and now - T.consumer.status_at <= event_bus.consumer.active_ttl_ms

scheduled_is_fresh =
  T.consumer.status == scheduled
  and now - T.consumer.status_at <= event_bus.consumer.scheduled_ttl_ms
```

`T.consumer.status_at` is written by the lane Consumer or proc. Processor
heartbeat can help diagnose long-running work, but it does not replace this
lane-local acknowledgement.

## SDK Primitive

The isolated state/orchestrator primitive lives under:

```text
kdcube_ai_app.apps.chat.sdk.events.event_bus
```

It provides:

```python
from kdcube_ai_app.apps.chat.sdk.events.event_bus import (
    ConversationEventBusOrchestrator,
    RedisEventLaneStateTable,
)

table = RedisEventLaneStateTable(redis=redis, state_key=state_key)
orchestrator = ConversationEventBusOrchestrator(table=table)
```

Core operations:

| Operation | Writer role |
| --- | --- |
| `schedule_consumer_from_wake(wake_event_timestamp=...)` | Proc after reading a wake item. |
| `open_handler(turn_id=...)` | BaseWorkflow / ContextBrowser handler setup before timeline load. |
| `mark_consumer_active(turn_id=...)` | Reader/Consumer activation or active acknowledgement. |
| `accept_events_for_open_handler(events, turn_id=..., accept=...)` | Reader/Consumer lane drain. |
| `try_close_handler(turn_id=..., handler_processed_event_timestamp=...)` | ReAct handler close gate. |
| `mark_consumer_none()` | Reader/Consumer release after turn finalization. |

Wake publication lives next to the state primitive. For initial reactive
ingress the publisher is used inside the atomic lane-publish/queue-enqueue
operation. For post-save handoff, the event already exists in the lane, so the
publisher only enqueues the wake:

```python
from kdcube_ai_app.apps.chat.sdk.events.event_bus import (
    EventLaneWakePublisher,
    RedisEventLaneWakeEnqueuer,
)

publisher = EventLaneWakePublisher(
    RedisEventLaneWakeEnqueuer(redis=redis, tenant=tenant, project=project)
)
result = await publisher.publish_for_event(
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
tenant/project/user type. Proc resolves wake items and schedules/ignores them;
it does not scan the lane after task completion.

Focused tests:

```text
app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/test_event_bus_state.py
```

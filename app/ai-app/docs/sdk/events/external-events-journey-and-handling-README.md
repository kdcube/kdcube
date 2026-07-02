---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
title: "External Events Journey And Handling"
summary: "Current end-to-end journey for conversation-scoped external events: ingress admission, Redis lane ordering, bundle callbacks, policy-gated timeline sharing, ready-queue wakeups, processor resolution, and ReAct folding."
status: active
tags: ["sdk", "events", "external-events", "processor", "react", "timeline", "redis"]
updated_at: 2026-06-25
keywords:
  [
    "external event journey",
    "event lane",
    "agent_id",
    "id_card",
    "ExternalEventPayload",
    "ExternalEventLaneWakeup",
    "live owner",
    "conversation event lane",
    "event timeline",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-orchestrator-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
---

# External Events Journey And Handling

This document describes the current event path from client action to processor
execution, bundle event callbacks, and policy-gated ReAct timeline sharing.

The compact semantic protocol is in
[External Events](external-events-README.md). Ingress classification is in
[Ingress Event Inception](../../arch/ingress/events-inception-README.md).
Processor responsibilities are summarized in
[Proc Events Orchestration](../../arch/proc/events-orchestration-README.md).
The canonical accepted event envelope and concrete snapshot/file/selection
examples are in [External Event Envelope](external-event-envelope-README.md).
When debugging where a turn id or lane cursor came from, use
[Event Ingress To React Turn](event-ingress-to-react-turn-README.md); this
journey page stays at the protocol-flow level.

## Identities

Every accepted lane event belongs to one lane identity:

```text
tenant + project + user_id + conversation_id + agent_id
```

When the client does not name an agent, the platform uses:

```text
default.react.agent
```

The lane identity is separate from event semantics:

| Identity | Meaning |
|---|---|
| `id_card` | Tenant/project/user/conversation/agent lane and timeline scope. |
| `event_source_id` | Semantic event-source and policy key. |
| `event_id` | One accepted occurrence. Assigned by the event lane. |
| `sequence` | Monotonic ordering number inside one lane. Assigned by Redis. |

For tool-backed events inside ReAct, `tool_id` is equivalent to
`event_source_id` and `tool_call_id` is equivalent to `event_id`.

## Diagram Terminology

The diagrams below use short labels. They are not new abstractions:

| Term | Meaning |
|---|---|
| `E[]` | One ordered `external_events[]` batch authored by a client, widget, webhook, or API caller. |
| Accepted event | One event occurrence after ingress validation/normalization. It has platform event identity and can be stored in the lane. |
| Lane `L` | The ordered conversation/agent event stream for one `id_card`. Today this is Redis. A Kafka implementation would still need the same per-lane order and retained event lookup contract. |
| Wake | A small queue item saying "there is reactive work in lane `L`". It is not the event body; it points back to an accepted lane occurrence. |
| Queue `Q` | The processor ready/wake queue. It schedules work; it does not define event order. |
| State table `T` | Shared event-lane coordination state: open handler owner, consumer status/freshness, and processed cursors. Today this is Redis-backed state. |
| Handler | The turn that currently owns the lane for folding events into a ReAct turn. It is logical ownership, not a process. |
| Consumer | The runtime loop that reads lane events and folds/materializes them. In the current ReAct runtime this is the `ContextBrowser` event reader. |
| `consumer_status_at` | The consumer heartbeat or scheduling timestamp. With `consumer_status=active`, it proves the lane reader has recently acknowledged that it is alive. With `consumer_status=scheduled`, it is only a short-lived start reservation. Diagrams may write this as `T.consumer.status_at`. |
| Fresh active consumer | An active consumer whose `consumer_status_at` is inside the configured freshness window. A fresh active consumer means another turn must not reclaim the same open handler. |
| Scheduled start reservation | `T.consumer.status=scheduled` with a fresh timestamp. This suppresses duplicate proc wake starts while an app is loading or entering the turn, but it is not handler liveness. |
| Stale wake | A wake whose referenced reactive event is already covered by `T.last_processed_reactive_event_timestamp`. The wake is obsolete and can be ignored. |
| Defer | Do not start an additional processor turn because a fresh active consumer or fresh scheduled start reservation already covers this wake. The accepted event remains in `L`. |
| Reclaim | Take ownership from an open handler whose consumer is missing or stale. The older turn must later detect supersession and discard its output. |
| Superseded turn | A turn that started earlier but later sees that another turn owns the lane. It raises `ExternalEventLaneTurnSuperseded` and follows the normal turn-exception rollback path. |

## Current Journey

Compact version:

```text
external_events[] from client/widget/webhook/API
  -> chat-ingress
       validates request, resolves lane identity, appends accepted events to L
       and enqueues wake Q only when reactive work exists
  -> chat-proc
       dequeues wake, checks T, then:
         stale wake                 -> ignore
         fresh active consumer      -> defer
         fresh scheduled start      -> defer duplicate wake
         no fresh consumer/start    -> T.consumer = scheduled
  -> bundle-load/on-message fence
       resolve/load bundle, bind request context, invoke bundle turn entrypoint
  -> ReAct ContextBrowser
       open handler in T for this turn; reclaim stale open handler if there is
       no fresh active consumer heartbeat
       heartbeat consumer_status_at while still owner
       read L after timeline cursor
       accept_events_for_open_handler(...)
       fold events into TL only if still owner
  -> ReAct loop
       consume TL
  -> finish_turn
       assert still owner before answer/turn persistence
       owner changed -> ExternalEventLaneTurnSuperseded -> rollback/no stale commit
```

```text
Legend
  E[] = external_events batch from client/webhook/widget/API
  L   = Redis conversation event lane stream
  Q   = processor ready/wake queue
  T   = event-lane state table
  R   = ReAct runtime / ContextBrowser
  TL  = ReAct turn timeline

Client / Webhook / Widget / API
        |
        | 1. sends request with one ordered E[] batch
        v
chat-ingress process
        |
        | 2. validates auth/session/conversation
        | 3. resolves lane identity:
        |      tenant + project + user_id + conversation_id + agent_id
        | 4. normalizes event ids, timestamps, logical ev: paths,
        |    semantic event_source_id, and task_payload
        |
        +-------------------------------+
        |                               |
        v                               v
Redis/shared coordination          Redis/shared coordination
Lane Stream L                      Processor Queue Q
append accepted lane records       enqueue wake only when the accepted
with event_id + sequence           batch has reactive work
        |                               |
        |                               v
        |                         chat-proc process
        |                         processor worker
        |                               |
        |                               | 5. dequeues wake
        |                               | 6. checks T:
        |                               |    - stale wake? ignore wake
        |                               |    - fresh active consumer? defer to owner
        |                               |    - fresh scheduled start? defer duplicate wake
        |                               |    - no fresh consumer/start? schedule turn
        |                               v
        |                         Redis/shared coordination
        |                         T.consumer = scheduled
        |                               |
        |                               v
        |                         chat-proc process
        |                         load/resolve the bundle instance
        |                         then invoke its on-message turn entrypoint
        |                         (for ReAct bundles: @on_message / workflow run)
        |                               |
        v                               v
chat-proc process
ReAct Runtime / ContextBrowser R <-------+
        |
        | 7. opens lane handler for this turn
        |    if T already names another owner:
        |      raise ExternalEventLaneTurnSuperseded
        v
Redis/shared coordination
T.handler = open, handler_turn_id = current turn
        |
        | 8. starts lane consumer and acknowledges ownership
        |    by refreshing T.consumer_status_at
        v
T.consumer = active, consumer_status_at = now
        |
        | 9. reads accepted events from L after the timeline cursor
        |    then calls accept_events_for_open_handler(...)
        |    before folding/materializing them
        |    if handler_turn_id changed:
        |      raise ExternalEventLaneTurnSuperseded
        v
ContextBrowser block production under current-owner acceptance
        |
        | event kind/source decides block shape:
        | - event.user.prompt     -> user.prompt
        | - event.user.followup   -> user.followup
        | - event.user.steer      -> user.steer
        | - event.external/canvas -> policy-produced blocks or no blocks
        v
Timeline TL
        |
        | 10. contributed blocks become visible to this in-flight ReAct turn
        |     and lane processed cursors advance only for the current owner
        v
ReAct agent loop consumes updated TL
        |
        | 11. before final answer/turn persistence,
        |     finish_turn checks ownership again
        |     if this turn was superseded:
        |       normal exception rollback; no stale committed output
```

There is one event protocol: `ExternalEventPayload`. The ready queue may carry
a small `ExternalEventLaneWakeup`, but that wakeup is only a pointer to the
accepted lane occurrence. It is not the event body.

Processor wake decisions have precise meanings:

| Decision | Meaning | What happens to the lane event |
|---|---|---|
| Ignore stale wake | The wake points at reactive work whose timestamp is already covered by `T.last_processed_reactive_event_timestamp`. The wake item is obsolete. | Nothing new is scheduled; the lane cursor already proves the reactive work was handled. |
| Defer to fresh active consumer | The wake is not obsolete, but an active consumer heartbeat is fresh. Starting another turn would create a competing owner. | The accepted event remains in `L`; the active owner is responsible for reading and folding it. |
| Defer duplicate scheduled wake | Proc already marked this lane as scheduled recently. Starting another proc task during app load / entrypoint handoff would create a duplicate starter. | The accepted event remains in `L`; the scheduled starter will open the handler and read the lane, or a later turn can reclaim if no active heartbeat appears. |
| Schedule turn | No fresh active consumer or fresh scheduled start reservation covers the lane. | Proc marks `T.consumer = scheduled`, loads/resolves the app instance, invokes the app turn entrypoint, and that turn opens the handler and consumes from `L`. |

In short: "ignore" means the wake itself is obsolete; "defer" means the wake
is still valid but either a live active consumer or a short scheduled start
reservation already covers this processor wake.

The bundle load/invoke step is an important runtime fence. Proc has decided a
turn should run, but the lane is not consumed until the bundle instance is
loaded/resolved and its on-message turn entrypoint starts the ReAct runtime.
For non-singleton bundles, bundle loading can take observable time in this
window. That is why the state table first records `T.consumer = scheduled`:
another wake should not start a competing turn while this bundle-load and
entrypoint-invocation boundary is in progress.

`scheduled` stops duplicate wake starts; it does not prove the old handler is
alive. When the scheduled turn reaches `open_handler(...)`, stale-open reclaim
uses only a fresh `active` consumer heartbeat as the owner-liveness signal.

The ContextBrowser side is also fenced for idempotence. A turn may be delayed,
then another turn may reclaim the lane. The delayed turn is allowed to resume
briefly, but it must not commit stale output. `ContextBrowser` therefore
checks current ownership at the moments where stale work would otherwise
matter:

| Moment | Check | If ownership changed |
|---|---|---|
| Handler open | `T.handler_turn_id` must be this turn after `open_handler(...)`. | Raise `ExternalEventLaneTurnSuperseded`. |
| Consumer acknowledgement | Consumer heartbeat updates are guarded by handler owner. | The stale turn cannot refresh liveness for a different owner. |
| Initial and live event fold | `accept_events_for_open_handler(...)` requires `T.handler_turn_id` to still be this turn before calling the fold/materialization callback and advancing processed cursors. | Raise `ExternalEventLaneTurnSuperseded` on handler mismatch. |
| Finish turn | `finish_turn` calls the event-lane current-owner assertion before answer emission and final turn persistence. | Raise `ExternalEventLaneTurnSuperseded`; the normal turn exception path abandons this turn. |

This is the idempotence rule: a lane event is folded and cursor-advanced only
for the turn that still owns the lane, and a superseded turn does not publish
its answer or final turn record. Any partial local/in-memory materialization
from the stale turn is abandoned with that turn; the next owner continues from
the last committed conversation index.

## Process And Fence Map

This journey crosses several runtime fences. The fences are the parts that
must stay explicit if the implementation changes from Redis to Kafka, or from
in-process bundle loading to a remote bundle runner.

| Fence | Current process/runtime | Shared state touched | Why it is a fence |
|---|---|---|---|
| Client submit | Browser, widget iframe/webview, webhook sender, or API caller | None yet | The platform has not accepted the event. The caller's turn id or target turn is intent, not authority. |
| Ingress admission | `chat-ingress` process | Lane `L`, queue `Q` | Auth/session/conversation are validated; event identity, lane identity, and stored `task_payload` are normalized; the accepted event is appended to `L`; reactive work may enqueue a wake in `Q`. |
| Wake scheduling | Shared queue plus `chat-proc` processor worker | Queue `Q`, state table `T` | The wake is converted from "there is work" into "this worker may start or defer a turn". The event body is still read from `L`, not from `Q`. |
| Lane ownership decision | `chat-proc` processor worker | State table `T` | Proc checks processed cursors and wake-start reservations. It ignores obsolete wakes, defers to a fresh active consumer or fresh scheduled start, or marks `T.consumer = scheduled` before loading the app. |
| Bundle load / turn entrypoint | `chat-proc` process today; future remote bundle runner would own the same contract | Bundle registry/cache, request context, state table `T` | KDCube resolves/loads the bundle instance and invokes the bundle reactive/message turn entrypoint. For ReAct chat bundles this enters the on-message workflow run path (`@on_message` / workflow run); a lane wake can arrive through the resolved reactive-event wrapper before that on-message path starts. Non-singleton bundles can spend real time here. |
| Handler open | ReAct runtime inside the bundle turn | State table `T` | The turn declares itself the lane handler. If the state table names a different handler, this turn is superseded and must abort. From this point, another turn must not fold the same lane unless this handler's consumer becomes stale and is reclaimed. |
| Consumer acknowledgement | ReAct `ContextBrowser` event reader | State table `T` | The lane reader refreshes `consumer_status_at` only while it still matches the current handler. A fresh active acknowledgement is the liveness signal used to decide whether `open_handler` should defer or reclaim. |
| Lane read and block production | ReAct `ContextBrowser` plus event-source/block-production policies | Lane `L`, timeline `TL`, state table `T` | Accepted events are read in lane order, wrapped by `accept_events_for_open_handler(...)`, and materialized into zero or more ReAct timeline blocks only for the current owner. Event-source policy decides visibility; the bus event and timeline block are separate concepts. |
| Turn commit | BaseWorkflow/ReAct turn finish path | Conversation index, turn log, timeline storage | Only a non-superseded turn may persist answer/timeline/index updates. If ownership changed, `ExternalEventLaneTurnSuperseded` reaches the normal exception rollback path and stale output is not committed. |

The current implementation runs the bundle and ReAct runtime in `chat-proc`.
That is an implementation detail, not a semantic requirement. A remote bundle
runtime would still need the same fences: accepted event storage, wake
scheduling, lane ownership, consumer heartbeat, ordered lane read, and
superseded-turn rollback before persistence.

## Event Bus Processing Schematic

The external-event lane is a conversation/agent event bus first. ReAct timeline
materialization is one consumer outcome, not the definition of the bus.

```text
client submits one ordered batch
  external_events[]:
    - event.external   task_tracker.task.file.uploaded
    - event.canvas     task_tracker.canvas.state
    - event.snapshot   task_tracker.canvas.snapshot
    - event.user.prompt  chat message from the active surface reactive=true
        |
        v
ingress accepts each occurrence
  assigns/normalizes event_id, logical ev: path, target agent_id, reactive flag
  publishes occurrences to the id_card Redis lane in order
        |
        v
bundle/runtime event reader drains lane in sequence
  calls bundle/workflow event callback for each retained event
        |
        +-- bundle callback may:
        |     host or rehost bytes in bundle/third-party storage
        |     call a product API
        |     update bundle DB/cache/index
        |     validate permissions for the referenced story/object
        |     ignore the event after observing it
        |
        v
ReAct block-production policy gate
  for each accepted event source:
    - produce one or more timeline blocks
    - produce a snapshot/canvas/ref block
    - produce only bounded metadata/refs
    - produce no blocks with react.block_production.no_timeline
        |
        v
only produced blocks are appended to the ReAct timeline
  timeline_projection / announce_production / compaction_projection
  operate only on those blocks, not on every bus event
```

## Lane Ownership And Recovery

The Redis lane stream stores the accepted event records. A separate event-lane
state table coordinates which turn currently owns the lane:

```text
T.handler.turn_id
T.handler.status                 open | closed
T.handler.status_at

T.consumer.status                active | scheduled | none
T.consumer.status_at

T.last_processed_event_timestamp
T.last_processed_reactive_event_timestamp
```

`T.handler.status_at` is not a liveness signal. It only says when handler
state was last written. The handler-liveness signal is a fresh
`T.consumer.status_at` written by an active reader/consumer as an
acknowledgement that it is still consuming this lane.

```text
fresh active consumer_status_at
  do not steal the handler

missing or stale consumer_status_at
  reclaim the lane for a new turn

fresh scheduled consumer_status_at
  do not start another duplicate proc wake, but do not treat this as handler
  liveness at open_handler
```

The processor wake is only a nudge. It does not own event ordering and does
not decide which event belongs to which turn. The lane state table and the
ReAct `ContextBrowser` decide whether a live owner should keep consuming or a
new turn should reclaim the lane.

### Stale Open Handler

```text
Turn A opens lane
  T.handler_turn_id = turn-A
  T.consumer_status = active
  T.consumer_status_at = fresh
        |
        | worker dies, reloads, or stops acknowledging
        v
T.handler_status remains open
T.consumer_status_at becomes stale
        |
        | new reactive event arrives and wake is queued
        v
Turn B opens lane
  sees open handler turn-A
  sees stale consumer_status_at
  reclaims:
    T.handler_turn_id = turn-B
```

### Live But Delayed Consumer

A turn can be alive but delayed long enough for `consumer_status_at` to become
stale. Reclaiming is still allowed, but the older turn must not commit stale
output after a newer owner takes over.

```text
Turn A is delayed
        |
        | Turn B reclaims the lane
        v
T.handler_turn_id = turn-B
        |
        | Turn A resumes
        v
Turn A checks lane ownership during consumer ack / event accept / finish
        |
        v
sees owner turn-B
        |
        v
raises ExternalEventLaneTurnSuperseded
        |
        v
normal turn exception path:
  delete_turn(index_only)
  no stale committed answer or turn persistence
```

`ExternalEventLaneTurnSuperseded` is intentionally handled by the normal turn
exception path. The important property is that the stale turn is discarded as
a wrong turn, while the newer owner continues from the last committed turn in
the index.

This means bundles can use the same SSE/Socket.IO-backed lane for explicit
conversation events. An event can be useful to the bundle even when it should
not be shared with ReAct. For example, a widget can submit a telemetry-like
save boundary, the bundle callback can store or forward it, and the event
source can bind `react.block_production.no_timeline` so the occurrence advances
the lane cursor without creating durable ReAct blocks.

The structural default is still useful for ordinary generic/domain, snapshot,
and canvas events. When no source-specific block-production policy is
registered for those event types, the produced ReAct block uses the event's
`ev:` path. Built-in user events have their own defaults that project to
`user.prompt`, `user.attachment.*`, `user.followup`, and `user.steer`. A
registered source can override either default, including by intentionally
producing zero timeline blocks.

## Event Classes And Outcomes

| Event class | Reactive? | Typical bundle callback | Timeline outcome |
|---|---:|---|---|
| `event.user.prompt` | Yes | Optional raw event callback; BaseWorkflow persists/indexes prompt projection blocks later. | Built-in projection emits `user.prompt`. |
| `event.user.attachment.*` | Yes, follows parent prompt/followup event | Optional raw event callback; hosting/materialization may run before ReAct render. | Built-in projection emits `user.attachment.*`. |
| `event.user.followup` | Yes, active turn only | Optional raw event callback. | Built-in projection emits `user.followup`. |
| `event.user.steer` | Active control | Optional raw event callback. | Built-in projection emits `user.steer` / control path. |
| Generic bundle/domain event | Depends on occurrence | Validate story access, maybe persist request metadata, call APIs, update bundle state. | Source policy usually emits `event.external` summary/ref blocks, or no blocks for bus-only events. |
| Snapshot projection | Usually no | Store or refresh snapshot payload/ref. | Source policy may emit `event.snapshot`; projection/announce decide visibility. |
| Canvas state revision | Usually no | Store latest shared canvas JSON/revision. | Source policy may emit `event.canvas`; later projection can keep only latest/visible summary. |
| Host/process-only event | Usually no | Host bytes, call API, update bundle storage, or audit. | `react.block_production.no_timeline` produces no ReAct blocks. |

The last row is important: "sent through the event bus" and "stored on the
ReAct timeline" are separate decisions. The bus preserves order and gives the
bundle a callback point. The block-production policy decides whether the event
also becomes model-visible or recoverable ReAct timeline material.

When a policy does share file-related material with ReAct, it may share only
metadata and refs. File rows such as `artifact_rows`, `declared_file_items`, or
`hosted_artifacts` preserve logical paths and hosted refs; they do not require
the event source to inline the file body. ReAct can use the visible `fi:` ref
with `react.read` when it needs text content, or `react.pull` when an owner
namespace ref such as `nmsp:`, `cnv:`, or `mem:` must be materialized first.
Named-service namespaces should materialize through provider `object.get` and
owner `block.produce`; custom owner namespaces may use a registered rehoster.
Immediate bounded previews are source-owned and opt-in through explicit
`text_preview`.

## Payloads

`ExternalEventPayload` is the top-level ingress-to-processor event envelope. It
is not necessarily chat and not necessarily a task. All authored events enter
as `external_events[]`; each accepted item becomes one lane
occurrence with an event envelope described in
[External Event Envelope](external-event-envelope-README.md):

```text
ExternalEventPayload.event.kind
ExternalEventPayload.event.agent_id
ExternalEventPayload.event.event_source_id
ExternalEventPayload.event.event_id
ExternalEventPayload.event.sequence
ExternalEventPayload.event.reactive
```

`ExternalEventLaneWakeup` contains the service/routing/user context needed by
the processor queue machinery, plus:

```text
ExternalEventLaneWakeup.event_lane.tenant
ExternalEventLaneWakeup.event_lane.project
ExternalEventLaneWakeup.event_lane.user_id
ExternalEventLaneWakeup.event_lane.conversation_id
ExternalEventLaneWakeup.event_lane.agent_id
ExternalEventLaneWakeup.event_lane.event_id
ExternalEventLaneWakeup.event_lane.sequence
```

It intentionally does **not** contain `request`. The prompt, attachments,
followup text, steer text, or generic/domain event payload are recovered from the lane
event's stored `task_payload`.

## Built-In User Events

User prompt, attachment, followup, and steer are not a separate semantic
category. They are built-in external event types. New clients should author
them directly in `external_events[]`:

```text
chat prompt                  -> type event.user.prompt
chat attachments             -> type event.user.attachment.*
open-turn followup           -> type event.user.followup, continuation=true
open-turn steer              -> type event.user.steer, continuation=true
```

The Redis lane kind may still be `message`, `followup`, or `steer` as an
operational scheduling label. The accepted event type is the semantic type
above. The current ReAct lane-to-timeline fold projects those built-in event
types into existing renderer block shapes: `user.prompt`, `user.attachment.*`,
`user.followup`, and `user.steer`. Producers should author the semantic event
type in `external_events[]`.

This conversion is important because prompt blocks are the inputs later handled
by normal BaseWorkflow persistence and indexing, including
`persist_turn_prompt_entries()`.

## Lane Kinds And Event Types

The lane kind is an operational/scheduling field. The accepted event `type` is
the semantic event shape. Current built-in/default projections are:

| Lane event kind | Accepted event type | Current timeline projection |
|---|---|---|
| `message` / retained `regular` | `event.user.prompt` plus `event.user.attachment.*` for attachments | `user.prompt` plus optional `user.attachment.*` |
| `followup` | `event.user.followup` plus `event.user.attachment.*` for attachments | `user.followup` plus optional external attachment blocks |
| `steer` | `event.user.steer` | `user.steer` |
| `external_event` | Generic lane label for accepted semantic types such as `event.external`, `event.snapshot`, `event.canvas`, or `event.user.prompt` when submitted through the plural event batch | Policy-produced blocks, built-in user projections, or no blocks with `react.block_production.no_timeline` |

> Live consumers MUST branch on the semantic event `type`, not the lane `kind`. In-flight
> events submitted through the plural batch arrive with lane `kind = external_event`, so the
> real type (`event.user.steer` / `event.user.followup`) lives only in `payload.event.type`.
> `react.on_external_event` recovers it with `live_events.recover_semantic_event_type` before
> deciding steer-interrupt / iteration-credit; keying off the lane `kind` would silently drop a
> live "stop". See [Steer and Followup](../agents/react/shared-timeline-event-bus-steer-followup-README.md).

When the ReAct event-source pipeline is enabled, folded blocks are stamped with
event identity:

```json
{
  "meta": {
    "event_source_id": "react.message",
    "event_id": "m...",
    "sequence": 12
  }
}
```

Tool blocks may keep using the existing tool-call identity instead of
duplicating the same fields on every durable block.

## Live Owner Path

For a live ReAct turn, `ContextBrowser` registers external-event hooks and owns
the timeline. The Redis wait loop lives in:

```text
sdk/solutions/react/events/listener.py
```

The loop owns transport mechanics only:

1. acquire/refresh the owner lease
2. acknowledge the lane consumer by refreshing `T.consumer_status_at` while
   this turn is still the handler
3. read lane events after the current cursor
4. hand the batch to `ContextBrowser`
5. `ContextBrowser` calls `accept_events_for_open_handler(...)`
6. only if the state table still names this turn as handler, call the supplied
   fold/materialization callback
7. advance processed cursors only after the accepted events were applied

The loop and `ContextBrowser` also detect owner loss. If lease refresh is
rejected, the owner lease changes, or the event-lane table reports another
handler turn, the runtime marks the current turn as superseded by storing an
`ExternalEventLaneTurnSuperseded` error. That signal is raised immediately at
the current event-read/fold boundary when possible and is always rechecked at
`finish_turn` before answer emission and persistence.

This makes late execution idempotent at the turn boundary. A delayed old turn
can wake back up after a newer turn has reclaimed the lane, but it cannot use
that late wakeup to publish a final answer or make the stale turn the committed
conversation head. It raises `ExternalEventLaneTurnSuperseded` and follows the
same exception cleanup path as a wrong turn.

Event-source policy semantics stay outside the Redis listener.

The hook/callback boundary is before timeline representation policy. A bundle
can observe raw accepted events and run side effects even when the event source
later produces no ReAct blocks. Conversely, ReAct projection, ANNOUNCE, and
compaction policies never need to inspect every raw bus event; they work with
the durable blocks that block-production chose to append.

## Promotion Path

If a busy-conversation event is not consumed by a live owner, proc may promote
the oldest promotable retained event after the current turn completes.

Promotion now queues an `ExternalEventLaneWakeup`, not a copy of the request
body. The processor resolves that wakeup back to the lane event before invoking
the bundle.

`steer` is not promoted after the active turn expires; it is a live control
event.

## Non-Reactive Idle Events

An accepted event with no `reactive=true` does not wake ReAct.
In the current implementation it is retained in the Redis event lane and the
conversation remains idle.

That lane is retained operational state:

```text
CHAT_EXTERNAL_EVENTS_STREAM_MAX_ENTRIES       default 1024
CHAT_EXTERNAL_EVENTS_STREAM_RETENTION_SECONDS default 7 days
```

It is not permanent business history. If an idle non-reactive event must be a
durable product fact independent of a later ReAct turn, the bundle callback or
platform must store it in durable bundle/product storage. The later ReAct fold
may still choose to represent it on the timeline, or a source policy may choose
`react.block_production.no_timeline` and keep it bus-only from ReAct's
perspective.

## Ordering

For one `id_card`, Redis lane `sequence` is the event order. The ready queue is
a wake-up channel and must not be treated as the ordering source.

Current ordering boundaries:

- accepted lane events are ordered per `id_card`
- a live ReAct owner folds events from the lane cursor in order
- processor resolves lane wakeups by event id before bundle invocation
- full durable out-of-turn timeline materialization for idle non-reactive events
  is not yet implemented

Because the current durable ReAct timeline is still persisted by turns,
out-of-turn non-reactive events can be retained in Redis before any later turn
folds them. The planned durable event-history work should make that storage
explicit instead of pretending Redis retention is permanent conversation
history.

## Boundaries

| Component | Owns |
|---|---|
| Ingress | Auth/session validation, event classification, agent target resolution, lane publish, wakeup enqueue, service ack. |
| Redis event lane | Per-`id_card` event order, retained event payload, sequence, event lookup, owner lease, promotion claim metadata. |
| Event-lane state table | Handler owner, consumer status/freshness, processed cursors, stale-open reclaim coordination. |
| Ready queue | Processor wakeups and ordinary proc scheduling. It does not store the lane-backed event body. |
| Processor | Queue claim, wakeup resolution, consumer scheduling from wakes, communicator/accounting context, bundle invocation, promotion after unconsumed busy events. |
| ReAct ContextBrowser | Handler open/close, consumer acknowledgement, lane-to-timeline folding before first render and during live turns, superseded-turn detection. |
| Bundle/workflow event callbacks | Raw accepted-event side effects such as hosting, API calls, permission checks, storage updates, or ignoring events. |
| Event-source subsystem | Source declaration and policy lookup. It does not own transport or processor queueing. |
| Event-source readers | Namespace-owner hooks used by runtime/policy code to resolve canonical refs such as `mem:` or `cnv:`. Exact model-facing content is imported through `react.pull`; named-service namespaces use provider `object.get` / `block.produce`, while custom owner namespaces may use a registered rehoster. They are not external-event transport and do not consume the Redis event lane. |
| ReAct block-production policies | Decide which accepted events become durable ReAct blocks and which are consumed without timeline blocks. |

## Implementation Status

| Capability | Status |
|---|---|
| `ExternalEventPayload` as top-level envelope | Implemented. |
| `agent_id` in lane scope and RuntimeCtx | Implemented. |
| Redis external-event lane sequence and owner lease | Implemented. |
| Event-lane state table for handler/consumer coordination | Implemented. |
| Ready-queue `ExternalEventLaneWakeup` | Implemented for lane-backed reactive starts and promoted retained events. |
| Wakeup without request body | Implemented; proc resolves event body from lane `task_payload`. |
| Lane-backed ReAct turn input | Implemented; ContextBrowser folds lane events before first model render. |
| Stale-open reclaim using fresh active `consumer_status_at` | Implemented. |
| Superseded-turn fencing before stale persistence | Implemented. |
| BaseWorkflow duplicate prompt/attachment guard | Implemented. |
| Workflow raw event callback | Implemented as `BaseWorkflow.on_external_event_received(...)`; default no-op. |
| Durable idle non-reactive event-history materialization | Pending. |
| Full conversation-native scheduler | Design only; see proc scheduler design docs. |

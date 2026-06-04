---
id: ks:docs/sdk/events/external-events-journey-and-handling-README.md
title: "External Events Journey And Handling"
summary: "Current end-to-end journey for conversation-scoped external events: ingress admission, Redis lane ordering, bundle callbacks, policy-gated timeline sharing, ready-queue wakeups, processor resolution, and ReAct folding."
status: draft
tags: ["sdk", "events", "external-events", "processor", "react", "timeline", "redis"]
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
  - ks:docs/sdk/events/external-event-envelope-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - ks:docs/arch/ingress/events-inception-README.md
  - ks:docs/arch/proc/events-orchestration-README.md
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

## Current Journey

```text
Client / widget / API
  sends user message, attachment, followup, steer, or authored external events
  authored event submissions use external_events[]
        |
        v
Ingress
  validates auth/session/conversation
  resolves agent_id from payload.target.agent_id or default.react.agent
  builds ExternalEventPayload
  publishes the accepted occurrence to the Redis event lane when the event is
  lane-backed
        |
        +-- idle reactive event
        |     queue ExternalEventLaneWakeup
        |     wakeup points at the lane event; it does not carry request data
        |
        +-- busy followup / steer / external events
        |     retain in the same lane for the live owner
        |     if not consumed live, proc can promote the retained event later
        |
        +-- idle non-reactive authored external events
              retain in the lane and return; no model wake
        |
        v
Redis event lane
  assigns event_id + sequence
  stores the occurrence payload and stored task_payload while retained
        |
        v
Processor ready queue
  carries either an ordinary ExternalEventPayload or an ExternalEventLaneWakeup
        |
        v
Processor
  if queue item is ExternalEventLaneWakeup:
    reads the lane event by event_id
    reconstructs ExternalEventPayload from event.task_payload
    annotates bundle_call_context.event_lane_wakeup
  invokes bundle @on_reactive_event with the resolved payload
        |
        v
ReAct ContextBrowser / BaseWorkflow
  folds lane events into timeline before first model render
  live listener drains later lane events while the turn owns the lane
  BaseWorkflow skips duplicate inline prompt/attachment contribution when the
  lane reader already materialized current-turn user input
```

There is one event protocol: `ExternalEventPayload`. The ready queue may carry
a small `ExternalEventLaneWakeup`, but that wakeup is only a pointer to the
accepted lane occurrence. It is not the event body.

## Event Bus Processing Schematic

The external-event lane is a conversation/agent event bus first. ReAct timeline
materialization is one consumer outcome, not the definition of the bus.

```text
client submits one ordered batch
  external_events[]:
    - event.external   task_tracker.task.file.uploaded
    - event.canvas     task_tracker.canvas.state
    - event.snapshot   task_tracker.canvas.snapshot
    - event.external   task_tracker.canvas.review.requested reactive=true
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

This means bundles can use the same SSE/Socket.IO-backed lane for all explicit
domain events. An event can be useful to the bundle even when it should not be
shared with ReAct. For example, a widget can submit a telemetry-like save
boundary, the bundle callback can store or forward it, and the event source can
bind `react.block_production.no_timeline` so the occurrence advances the lane
cursor without creating durable ReAct blocks.

The default is still useful for ordinary authored events: if no source-specific
block-production policy is registered, ReAct emits one event block at the
event's `ev:` path. A registered source can override that default, including by
intentionally producing zero timeline blocks.

## Event Classes And Outcomes

| Event class | Reactive? | Typical bundle callback | Timeline outcome |
|---|---:|---|---|
| `event.user.prompt` | Yes | Optional raw event callback; BaseWorkflow persists/indexes prompt projection blocks later. | Compatibility projection currently emits `user.prompt`. |
| `event.user.attachment.*` | Yes, follows parent prompt/followup event | Optional raw event callback; hosting/materialization may run before ReAct render. | Compatibility projection currently emits `user.attachment.*`. |
| `event.user.followup` | Yes, active turn only | Optional raw event callback. | Compatibility projection currently emits `user.followup`. |
| `event.user.steer` | Active control | Optional raw event callback. | Compatibility projection currently emits `user.steer` / control path. |
| Explicit review request | Usually yes | Validate story access, maybe persist request metadata. | Source policy usually emits `event.external` summary/ref blocks. |
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
with `react.read` when it needs text content, or `react.pull` when a custom
namespace ref such as `ext:` must be rehosted first. Immediate bounded previews
are source-owned and opt-in through explicit `text_preview`.

## Payloads

`ExternalEventPayload` is the top-level ingress-to-processor event envelope. It
is not necessarily chat and not necessarily a task. Authored UI/domain events
enter as `external_events[]`; each accepted item becomes one lane
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
followup text, steer text, or authored event payload are recovered from the lane
event's stored `task_payload`.

## Built-In User Events

User prompt, attachment, followup, and steer are not a separate semantic
category. They are built-in external events. The older transport fields
`request.message`, message attachments, and continuation kind are authoring
shortcuts that ingress normalizes into event metadata:

```text
request.message              -> type event.user.prompt
message attachments          -> type event.user.attachment.*
continuation_kind=followup   -> type event.user.followup
continuation_kind=steer      -> type event.user.steer
```

The Redis lane kind may still be `message`, `followup`, or `steer` for
compatibility and scheduling. The accepted event type is the semantic type
above. The current ReAct lane-to-timeline fold projects those built-in event
types into existing renderer block shapes: `user.prompt`, `user.attachment.*`,
`user.followup`, and `user.steer`.

During rollout, retained lane records may still have `kind=regular`. The fold
normalizes those retained records exactly like `event.user.prompt`; new
producers should use `message` as the lane kind and `event.user.prompt` as the
accepted type.

This conversion is important because prompt blocks are the inputs later handled
by normal BaseWorkflow persistence and indexing, including
`persist_turn_prompt_entries()`.

## Lane Kinds And Event Types

The lane kind is an operational/scheduling field. The accepted event `type` is
the semantic event shape. Current compatibility projections are:

| Lane event kind | Accepted event type | Current timeline projection |
|---|---|---|
| `message` / retained `regular` | `event.user.prompt` plus `event.user.attachment.*` for attachments | `user.prompt` plus optional `user.attachment.*` |
| `followup` | `event.user.followup` plus `event.user.attachment.*` for attachments | `user.followup` plus optional external attachment blocks |
| `steer` | `event.user.steer` | `user.steer` |
| `external_event` | Accepted event `type`, such as `event.external`, `event.snapshot`, or `event.canvas` | Policy-produced blocks, or no blocks with `react.block_production.no_timeline` |

When the ReAct event-source pipeline is enabled, folded blocks are stamped with
event identity:

```json
{
  "meta": {
    "event_source_id": "chat.message",
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
2. read lane events after the current cursor
3. call the supplied fold/materialization callback
4. mark consumed events only after they were applied

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

An authored external event with no accepted `reactive=true` does not wake ReAct.
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
| Ready queue | Processor wakeups and ordinary proc scheduling. It does not store the lane-backed event body. |
| Processor | Queue claim, wakeup resolution, communicator/accounting context, bundle invocation, promotion after unconsumed busy events. |
| ReAct ContextBrowser | Lane-to-timeline folding before first render and during live turns. |
| Bundle/workflow event callbacks | Raw accepted-event side effects such as hosting, API calls, permission checks, storage updates, or ignoring events. |
| Event-source subsystem | Source declaration and policy lookup. It does not own transport or processor queueing. |
| ReAct block-production policies | Decide which accepted events become durable ReAct blocks and which are consumed without timeline blocks. |

## Implementation Status

| Capability | Status |
|---|---|
| `ExternalEventPayload` as top-level envelope | Implemented. |
| `agent_id` in lane scope and RuntimeCtx | Implemented. |
| Redis external-event lane sequence and owner lease | Implemented. |
| Ready-queue `ExternalEventLaneWakeup` | Implemented for lane-backed reactive starts and promoted retained events. |
| Wakeup without request body | Implemented; proc resolves event body from lane `task_payload`. |
| Lane-backed ReAct turn input | Implemented; ContextBrowser folds lane events before first model render. |
| BaseWorkflow duplicate prompt/attachment guard | Implemented. |
| Workflow raw event callback | Implemented as `BaseWorkflow.on_external_event_received(...)`; default no-op. |
| Durable idle non-reactive event-history materialization | Pending. |
| Full conversation-native scheduler | Design only; see proc scheduler design docs. |

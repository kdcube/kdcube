---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
title: "Event Ingress To React Turn"
summary: "Concrete field-level journey from browser, Socket.IO, webhook, Telegram, or backend event submission into the Redis conversation event lane, the ReAct runtime turn, timeline materialization, close gate, and persistence."
status: active
tags: ["sdk", "events", "ingress", "external-events", "react", "turns", "telegram", "socketio", "sse"]
updated_at: 2026-06-18
keywords:
  [
    "event ingress to react turn",
    "ExternalEventPayload.routing.turn_id",
    "RuntimeCtx.turn_id",
    "active_turn_id_at_ingress",
    "target_turn_id",
    "owner_turn_id",
    "handler_turn_id",
    "event lane close gate",
    "telegram resubmitter",
    "chat submitter",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
---
# Event Ingress To React Turn

This page answers one narrow question:

```text
When an external event becomes a ReAct turn, where do the turn ids,
event ids, lane state values, rendered timeline blocks, and persisted rows
come from?
```

The answer is field-level. There is no hidden source of turn identity. The
effective ReAct turn id is the `ExternalEventPayload.routing.turn_id` carried by
the ready-queue wake into processor and then into `RuntimeCtx.turn_id`.

## One Page Model

```text
Browser / Socket.IO / Telegram webhook / backend submitter
  builds message_data and external_events[]
  mints message_data.turn_id when it is entering chat ingress
        |
        v
process_chat_message(...)
  validates auth and conversation
  creates ExternalEventPayload
  copies message_data.turn_id -> ExternalEventPayload.routing.turn_id
  stamps accepted events with event_id, timestamp, batch_id, logical_path
        |
        v
Redis conversation event lane
  stores ordered accepted event occurrences
  stores task_payload.routing.turn_id for the wake package
        |
        v
ready queue wake
  wakes proc only; it is not the semantic ordering authority
        |
        v
proc @on_reactive_event
  reconstructs ExternalEventPayload from event.task_payload
  starts the ReAct workflow for event.task_payload.routing.turn_id
        |
        v
BaseWorkflow / ContextBrowser / ReAct
  RuntimeCtx.turn_id = ExternalEventPayload.routing.turn_id
  materializes accepted lane events into timeline blocks stamped with that id
  renders model context from the timeline
  commits the last rendered event cursor to the timeline
        |
        v
close gate
  compares the rendered timeline cursor with lane state
  closes only when ReAct has rendered through the latest accepted event
        |
        v
turn finalization
  persists turn.log, conv.timeline.v1, artifacts, and conv_messages rows
  from timeline blocks, not from raw request payloads
```

## Field Origins

| Field | First writer | Meaning |
| --- | --- | --- |
| `message_data.turn_id` | Chat ingress caller. SSE/Socket.IO/Telegram submitter mint it with `new_turn_id()`. | Candidate processing turn id for this submission. |
| `ExternalEventPayload.routing.turn_id` | `process_chat_message(...)` copies `message_data.turn_id`. | The effective queued/runtime turn id. This becomes `RuntimeCtx.turn_id` when proc handles the wake. |
| `event.task_payload.routing.turn_id` | Atomic lane publish/wakeup stores the `ExternalEventPayload` on the wake payload. | The turn id proc uses to reconstruct the ReAct input package. |
| `RuntimeCtx.turn_id` | BaseWorkflow/ReAct runtime from `ExternalEventPayload.routing.turn_id`. | The ReAct turn id used to stamp current-turn timeline blocks, tool artifacts, and persisted turn artifacts. |
| `target_turn_id` | Client or caller, when provided. | User-visible target. Advisory only. It may be stale. |
| `active_turn_id_at_ingress` | Ingress reads conversation state. | Server-observed active turn at the moment the event was accepted. |
| `owner_turn_id` | Live owner lease lookup, when present. | ReAct handler that is currently allowed to consume the lane live. |
| `handler_turn_id` | ContextBrowser opens/closes lane handler state. | The runtime turn id of the open ReAct handler in the lane state record. |
| `event_id` | Ingress/lane acceptance when absent. | One accepted event occurrence. |
| `batch_id` | Ingress stamps one when absent. | One submission group. If any event in a batch is followup semantics, the batch is treated as one followup group by policy. |
| `sequence` | Redis event lane. | Monotonic order inside the conversation/agent lane. |
| `event.timestamp` | Accepted event envelope timestamp. | Lane freshness and close-gate comparison value. It is not derived from the turn id string. |
| `event.logical_path` | Ingress event acceptance. | `ev:` path for the accepted event occurrence. |
| `origin.*` metadata on blocks | ContextBrowser materialization. | Previous active/owner/target turn context. It is provenance, not the block's runtime turn id. |

The rule is:

```text
current ReAct turn id = RuntimeCtx.turn_id
RuntimeCtx.turn_id = ExternalEventPayload.routing.turn_id
ExternalEventPayload.routing.turn_id = message_data.turn_id accepted by ingress
```

`active_turn_id_at_ingress`, `owner_turn_id`, and `target_turn_id` explain how
the event arrived. They do not override `RuntimeCtx.turn_id` for blocks produced
inside the effective ReAct turn.

## Transport Lines

### SSE Browser Chat

```text
client POST /sse/chat
  message, attachments, external_events[]
        |
        v
ingress/sse/chat.py
  turn_id = new_turn_id()
  message_data["turn_id"] = turn_id
        |
        v
process_chat_message(..., IngressConfig(transport="sse"))
```

The SSE acknowledgement returns the fields the UI needs:

| Ack field | Meaning |
| --- | --- |
| `turn_id` | Same value as `queued_turn_id`; the fallback processing turn id for this submission. |
| `queued_turn_id` | `ExternalEventPayload.routing.turn_id`. |
| `target_turn_id` | User/client target, if provided. |
| `active_turn_id` | Server-observed active turn at ingress time. |
| `live_owner_detected` | Whether a live owner was observed for same-turn folding. |
| `event_id` / `external_event_sequence` | Accepted lane occurrence identity. |

### Socket.IO Browser Chat

```text
client emits chat_message
        |
        v
ingress/socketio/chat.py
  turn_id = new_turn_id()
  message_data["turn_id"] = turn_id
        |
        v
process_chat_message(..., IngressConfig(transport="socket"))
```

The same acknowledgement semantics as SSE apply.

### Telegram Webhook Through Submitter

The normal Telegram path does not run ReAct directly inside the webhook.

```text
Telegram webhook
        |
        v
handle_webhook(...)
        |
        v
telegram user_admin.submit_react_turn(...)
  turn_id = new_turn_id()
  builds message_data.external_events[]
  creates IngressConfig(transport="telegram")
        |
        v
entrypoint.chat_submitter.submit(...)
        |
        v
process_chat_message(...)
        |
        v
Redis lane + ready queue + proc
```

`handle_webhook(...)` calls `submit_react_turn(...)` first. When the submitter
accepts the request, the webhook has queued conversation work and should not
also run ReAct inline. Later, when processor runs the queued turn, the bundle
can wrap its runner with `run_with_queued_telegram_delivery(...)` so progress
and final delivery are sent to Telegram.

### Telegram Inline Fallback

Inline fallback is only for runtimes where the injected chat submitter is not
available:

```text
handle_webhook(...)
  submit_react_turn(...) returns None
        |
        v
run_react_turn(...)
        |
        v
deliver_react_turn_to_telegram(...)
```

This is a fallback execution path. It must bind a runtime turn explicitly and
then reduce the returned ReAct timeline/turn log for delivery. It is not the
normal Redis lane wake path.

### Backend Jobs And Other Webhooks

Backend jobs and custom webhooks have two valid patterns:

| Pattern | Use when | Requirement |
| --- | --- | --- |
| Submitted conversation event | The event should participate in a chat conversation, same timeline, same close gate, same persistence. | Call the platform chat submitter / ingress core and send `external_events[]`. |
| Inline isolated ReAct run | The work is a private job or one-off run without browser same-turn semantics. | Bind the runtime context and consume the returned timeline/turn log explicitly. |

A bundle should not push directly to ready queues or invent lane state writes.
The lane and wake package are platform-owned.

## Idle, Busy, And Non-Reactive Outcomes

`process_chat_message(...)` determines how the accepted batch is routed.

| Conversation state and event type | Platform action | Result |
| --- | --- | --- |
| Idle and batch contains a reactive event | Publish events to lane and enqueue one wake atomically. | New processing turn for `routing.turn_id`. |
| Busy and live owner can consume | Publish events to lane as continuation. | Current owner may fold events into active ReAct turn. |
| Busy and live owner is gone or finalizing | Publish events to lane as continuation. | Processor/promotion must eventually wake the lane; the ready queue is only a wakeup. |
| Non-reactive batch | Publish lane event without model wake. | Available to policies, widgets, or future timeline reads; no ReAct turn is started by the event alone. |

The Redis lane order is authoritative. Queue order is not.

## Accept, Materialize, Render, Close

`accept()` has a precise meaning in the event lane:

```text
accept event from lane
  -> materialize it into timeline blocks for the open handler
  -> update lane state last_processed_event_timestamp/event_id
```

It does not mean:

- the event is already indexed into `conv_messages`
- the event was already sent to a user
- the event's raw payload was directly persisted as a chat row

The close gate uses two values:

```text
Lane state:
  T.last_processed_event_timestamp
  T.last_processed_event_id

Timeline render cursor:
  timeline.last_rendered_event_cursor.timestamp
  timeline.last_rendered_event_cursor.event_id
```

The render cursor is committed when prompt rendering succeeds. It means:

```text
ReAct rendered timeline content produced from events up to this event cursor.
```

The model may have seen the event as a compacted block instead of raw text. That
still counts as processed for close-gate purposes because the event contributed
to the rendered prompt. The cursor is stored on the timeline object so in-turn
compaction does not lose the fact that the event was processed.

Close-gate decision:

```text
if rendered_cursor is older than T.last_processed_event_timestamp/event_id:
  keep handler open
  run another ReAct round
else:
  close handler
  allow turn finalization
```

If the cursor is computed from visible post-policy text only, it can miss an
accepted source event that was folded or compacted. The durable unit is the
timeline event cursor, not a derived text timestamp.

## Persistence Boundary

Finalization persists from the turn timeline and turn log:

```text
timeline blocks
  -> turn.log artifact
  -> conv.timeline.v1 artifact
  -> conv_messages rows for indexable user/assistant/artifact blocks
```

The persistence layer should not invent user rows from raw request payloads.
User rows come from materialized `user.prompt`, `user.followup`, and related
timeline blocks. Assistant rows come from `assistant.completion` blocks.

Multiple assistant completions can exist in one turn. This is valid when
same-turn followups keep extending the turn. Reducers and adapters must decide
whether to send all legal completions, send only the latest, or apply a
transport-specific policy. They should not treat `state["final_answer"]` as the
authoritative output stream.

## Diagnostic Trace

When debugging a turn, follow these records in order.

| Evidence | What it proves |
| --- | --- |
| Transport log, for example `[sse_chat]` or Socket.IO ack | The caller minted or received a `turn_id` and submitted a batch. |
| `[external_events.publish.atomic]` | The batch entered the Redis event lane and got event ids/sequences. |
| Wake payload `event.task_payload.routing.turn_id` | The processing turn id that proc will use. |
| `[external_events.owner.acquire]` / handler open logs | Which runtime turn owns the live handler. |
| `turn.log` `turn.header` | The effective ReAct turn that actually ran. |
| `turn.log` user blocks | Whether user prompt/followup events were materialized for that turn. |
| `turn.log` assistant blocks | What legal completions ReAct produced. |
| Lane-state close-gate logs | Whether the rendered cursor caught up to accepted lane events. |
| `conv_messages` rows | What finalization indexed from timeline blocks. |

If `turn.log` contains a user block but `conv_messages` has no `role='user'`
row for that turn, investigate indexing/finalization. If both are missing,
investigate materialization/policy before indexing.

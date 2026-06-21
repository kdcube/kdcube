---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-understand-conversation-events-and-react-turns-README.md
title: "How To Understand Conversation Events And ReAct Turns"
summary: "Tier 1 architecture view for conversation external events, processor wakeups, bundle load/on-message fences, ReAct ContextBrowser consumption, timeline materialization, and stale-owner recovery."
tags: ["sdk", "bundle", "tier-1", "events", "react", "processor", "architecture"]
keywords:
  [
    "conversation external events",
    "external event journey",
    "react turn",
    "event lane",
    "processor wake",
    "bundle load fence",
    "on_message fence",
    "ContextBrowser",
    "consumer_status_at",
    "stale wake",
    "fresh consumer",
    "superseded turn",
  ]
updated_at: 2026-06-20
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-orchestrator-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/client-transport-protocols-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
---
# How To Understand Conversation Events And ReAct Turns

Read this before changing any bundle, client, widget, webhook, or SDK subsystem
that submits conversation `external_events[]`, followups, steers, snapshots,
story/canvas events, or event-source policies.

This is a Tier 1 architecture view. It is intentionally sharp and short. The
full source of truth is
[External Events Journey And Handling](../../events/external-events-journey-and-handling-README.md).

## Non-Negotiable Model

KDCube conversation events are not direct calls into a bundle.

They cross these process/runtime fences:

```text
client / widget / webhook / API
  -> chat-ingress process
  -> shared conversation event lane + processor wake queue
  -> chat-proc processor worker
  -> bundle load / bundle turn entrypoint
  -> ReAct ContextBrowser event consumer
  -> ReAct timeline blocks
  -> turn commit or rollback
```

Each arrow is a real boundary. Do not collapse them mentally when debugging or
designing a bundle.

## Process Map

```text
Client / Widget / Webhook / API
  sends external_events[]
        |
        v
chat-ingress process
  authenticates, validates session/conversation, resolves tenant/project/user/
  conversation/agent lane identity, normalizes event ids and task_payload
        |
        +-----------------------------+
        |                             |
        v                             v
shared event lane L              shared processor queue Q
  ordered accepted events          wake pointer only
  event_id + sequence              no event body authority
        |                             |
        |                             v
        |                       chat-proc process
        |                         processor dequeues wake,
        |                         checks lane state T,
        |                         ignores / defers / schedules
        |                             |
        |                             v
        |                       bundle load fence
        |                         resolve/load bundle instance,
        |                         bind request context,
        |                         invoke bundle reactive/message
        |                         turn entrypoint
        |                             |
        v                             v
ReAct runtime / ContextBrowser <------+
  opens handler, refreshes consumer heartbeat,
  reads lane L, materializes accepted events into blocks
        |
        v
ReAct timeline
  model-visible blocks only when event-source policy produces them
        |
        v
turn finish
  commit only if this turn still owns the lane;
  otherwise raise superseded-turn error and rollback normally
```

## Terms To Use Precisely

| Term | Exact meaning |
|---|---|
| Event batch | The caller-authored `external_events[]` array. The batch is ordered by the caller. |
| Accepted event | One event after ingress accepts and normalizes it. Accepted events have platform identity. |
| Lane | The ordered conversation/agent event stream for one tenant/project/user/conversation/agent lane identity. Today it is Redis-backed. |
| Wake | A queue pointer saying reactive work exists in the lane. It is not the event body and is not the event ordering source. |
| Processor queue | Scheduling transport for proc workers. It must not be treated as the canonical event stream. |
| Lane state table `T` | Shared state for handler owner, consumer status/freshness, and processed event cursors. |
| Handler | The turn that currently owns the lane. Ownership is logical; it is not the same as a process. |
| Consumer | The event reader that consumes the lane for a turn. In ReAct turns this is the `ContextBrowser` event reader. |
| Bundle turn entrypoint | The discovered bundle reactive/message entrypoint invoked by proc for the queued wake. In SDK `BaseEntrypoint` chat bundles this is normally the `run()` surface decorated as `@on_reactive_event`; that surface enters the on-message workflow run path for ReAct. |
| `consumer_status_at` | Consumer heartbeat. This is the liveness signal. |
| `handler_status_at` | Handler state write timestamp. This is not liveness. |
| Stale wake | A wake whose reactive event is already covered by `T.last_processed_reactive_event_timestamp`. It is obsolete. |
| Defer | Do not start a new turn because `consumer_status_at` is fresh for an active or scheduled consumer. |
| Reclaim | Start a new owner when an open handler has no fresh consumer. |
| Superseded turn | An older turn that resumes after another owner took the lane. It must rollback, not commit stale output. |

## Wake Decisions

When `chat-proc` dequeues a lane wake, there are only three valid decisions:

| Decision | Meaning | Result |
|---|---|---|
| Ignore stale wake | The wake points to reactive work already covered by `T.last_processed_reactive_event_timestamp`. | Nothing starts. The lane cursor proves the work was handled. |
| Defer to fresh consumer | The wake is valid, but `consumer_status_at` is fresh for a scheduled or active consumer. | Nothing new starts. The event remains in the lane for the current owner. |
| Schedule turn | No fresh consumer owns the lane. | Proc marks `T.consumer = scheduled`, then crosses the bundle load/turn entrypoint fence. |

This distinction is load-bearing:

- "ignore" means the wake itself is obsolete
- "defer" means the wake is valid, but another consumer is already responsible
- "schedule" means proc is allowed to begin the bundle turn path

## Bundle Load / On-Message Fence

The bundle load step is not a detail.

After proc schedules a turn:

1. the bundle instance is resolved or loaded
2. request context is bound
3. proc invokes the discovered bundle turn entrypoint
4. in SDK `BaseEntrypoint` chat bundles, that is normally the
   `@on_reactive_event` `run()` surface entering the on-message workflow run
   path
5. only then does the ReAct runtime create/use the `ContextBrowser` consumer

For non-singleton bundles, step 1 can take observable time. During that window,
`T.consumer = scheduled` is the signal that another wake should defer instead
of starting a competing turn.

If KDCube later moves bundle execution to a remote runner, this same fence
still exists. The remote runner would own the load/invoke side of the fence,
but the event-lane contract does not disappear.

## Timeline Is A Projection, Not The Bus

An accepted event can be consumed without becoming model-visible.

```text
accepted lane event
  -> optional bundle callback / side effect
  -> event-source block-production policy
  -> zero, one, or many ReAct timeline blocks
```

Use `react.block_production.no_timeline` when a bus event should be consumed
or side-effected but should not produce ReAct timeline blocks.

## Recovery Rule

Use `consumer_status_at` to decide liveness.

```text
fresh consumer_status_at
  -> defer to current owner

missing/stale consumer_status_at
  -> new owner may reclaim
```

If the older owner later resumes, it must detect that the lane owner changed
and raise `ExternalEventLaneTurnSuperseded`. That error goes through the normal
turn exception rollback path. The stale turn must not commit answer, timeline,
or index updates.

## Replacement Contracts

These contracts survive implementation changes:

| If replaced | Contract that must remain |
|---|---|
| Redis lane replaced by Kafka | Per-lane order, retained event lookup by event id/sequence, processed cursors, owner/consumer coordination. |
| Redis queue replaced by another scheduler | Queue remains a wake/scheduling signal, not the canonical event stream. |
| In-process bundle load replaced by remote bundle execution | Bundle load/turn entrypoint remains a fence; request context, ownership, and superseded-turn rollback must cross that boundary explicitly. |
| ReAct ContextBrowser replaced by another consumer | The consumer must heartbeat, read lane events in order, materialize according to policy, and stop committing after supersession. |

## Builder Rules

- Clients and widgets send intent through `external_events[]`; they are not the
  authority for the active turn.
- Bundles do not write directly to the lane state table.
- Bundle code defines event-source ids, policies, callbacks, and product
  storage; platform ingress and proc own transport, scheduling, and lane
  ownership.
- Do not use the processor queue as event ordering.
- Do not use `handler_status_at` as liveness.
- Do not assume `followup_accepted` or `steer_accepted` means a new turn
  started. It means the event was admitted.
- Before changing client continuation UX, read
  [Client Transport Protocols](../../../service/comm/client-transport-protocols-README.md) and
  [Chat Stream Events](../../solutions/chat/chat-stream-events-README.md).
- Before changing event policies or timeline projection, read
  [Bundle Events](../bundle-events-README.md) and
  [External Events Journey And Handling](../../events/external-events-journey-and-handling-README.md).

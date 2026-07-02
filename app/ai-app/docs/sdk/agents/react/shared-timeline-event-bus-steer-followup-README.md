---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
title: "Shared Timeline Event Bus for Steer and Followup"
summary: "How active React turns consume followup and steer events through the shared conversation event bus while preserving turn ownership and fallback continuation execution."
tags: ["sdk", "agents", "react", "timeline", "steer", "followup", "continuations", "redis"]
keywords:
  [
    "shared timeline",
    "steer",
    "followup",
    "continuation mailbox",
    "react timeline ownership",
    "conversation event bus",
    "timeline listener",
    "turn ownership",
    "external events",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-browser-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/why/memory-architecture-README.md
---

# Shared Timeline Event Bus for Steer and Followup

## 0. Implementation Status

This design is now implemented in the current React runtime with these concrete semantics:

For the exact origin of `target_turn_id`, `active_turn_id_at_ingress`,
`owner_turn_id`, `queued_turn_id`, `RuntimeCtx.turn_id`, and the close-gate
cursor, use [Event Ingress To React Turn](../../events/event-ingress-to-react-turn-README.md).
This page explains the followup/steer behavior after those fields exist.

- ingress appends busy-turn `followup` / `steer` into one Redis-backed shared conversation event source
- the active React turn acquires a fenced owner lease and listens to that source live
- the event-lane orchestrator treats only a fresh `active` consumer acknowledgement as live handler ownership; `scheduled` is a proc start reservation and does not protect a stale open handler from reclaim
- `followup` is folded into the current turn and can trigger another decision round before completion
- a consumed live `followup` also mints extra iteration credit for that same turn, capped by runtime configuration, so additive followups do not exhaust the original fixed loop budget
- if the user already saw a completion before a new `followup` arrived, that visible completion remains part of the same turn history; later completions are additive, not replacement-only
- `steer` is folded into the current turn and acts as a control interrupt
- a consumed steer first interrupts the active generation or cancellable tool phase when possible
- the live interrupt classifies `steer`/`followup` from the **semantic** event type
  (`payload.event.type`, e.g. `event.user.steer`), NOT the transport lane `kind` — which is
  uniformly `external_event` for in-flight events (see [Lane Kinds And Event Types](../../events/external-events-journey-and-handling-README.md#lane-kinds-and-event-types)).
  `runtime.on_external_event` recovers the semantic type via
  `live_events.recover_semantic_event_type` before branching, so a steer that arrives on the wire
  enveloped as `external_event` still fires `_interrupt_active_phase_for_steer()` (→ `task.cancel()`,
  which the isolated exec runtime turns into a container/subprocess kill) and is denied reactive
  iteration credit. A "stop" is a `steer` with empty text + `explicit=True`; there is no separate
  stop event type.
- steer interrupts are de-duplicated by sequence: `on_external_event` fires the live interrupt only
  for a steer whose sequence is newer than `_last_handled_steer_seq` (the highest steer already folded
  into a finalize). A re-read / re-delivered steer therefore cannot cancel a LATER generation — e.g.
  one a subsequent `followup` just started. (The checkpoint path `_apply_steer_interrupt_if_requested`
  uses the same `_latest_steer_seq_seen` vs `_last_handled_steer_seq` fence.)
- React then re-enters with the steer already on the current turn timeline and gets a short bounded finalize window
- if no live owner consumes the event, processor promotes it from that same retained source into a normal scheduled turn

Current boundary:
- the shared external-event source is Redis-backed retained operational state,
  not permanent conversation/artifact storage
- retention is bounded per tenant/project/conversation stream by
  `CHAT_EXTERNAL_EVENTS_STREAM_MAX_ENTRIES` and
  `CHAT_EXTERNAL_EVENTS_STREAM_RETENTION_SECONDS`
- after React folds an event, the folded blocks and cursor become part of the
  normal persisted timeline; idle non-reactive authored events that never open
  or reach a React turn currently remain only in the Redis external-event source
- steer interruption is immediate for the active decision phase task
- steer interruption is immediate for cancellable exec/tool phases that already honor task cancellation
- a fully blocking tool that does not cooperate with cancellation can still delay final stop until its await boundary
- authored `external_event` inception is documented in
  [Ingress Event Inception](../../arch/ingress/events-inception-README.md);
  this article focuses on React consumption of the shared event source

Implemented flow:

```text
Client
  sends followup/steer intent
  optional target_turn_id = user's visible target
        |
        v
Ingress
  reads server conversation state
  active_turn_id_at_ingress = server active turn
  owner_turn_id = live React owner lease, when present
  writes ConversationExternalEvent into the Redis external-event source
        |
        +--> HTTP/Socket ack
        |      status = followup_accepted / steer_accepted
        |      event_id = external event id
        |      queued_turn_id = fallback task turn id
        |      live_owner_detected = owner_turn_id == active_turn_id_at_ingress
        |
        v
Live React owner
  accepts only by owner_turn_id / active_turn_id_at_ingress
  folds event into current turn
  marks event consumed
        |
        +--> if owner closes before consume
                proc claims unconsumed event
                promotes task_payload once as a normal ready-queue turn
```

## 1. Problem

Today React owns the timeline correctly for the duration of the turn:

- the bundle entrypoint creates `ReactSolverV2`
- the solver creates `ContextBrowser`
- `ContextBrowser.load_timeline()` materializes an in-memory `Timeline`
- all further `contribute(...)` calls are local to that in-process owner
- the timeline is only persisted later, typically at turn finalization

This is fine for normal single-turn ownership, but it creates a gap:

- while the turn is running, the authoritative timeline state is **local**
- external events such as `steer` and `followup` can arrive in ingress / processor
- those events are accepted at the conversation level, but they are **not reachable by the active timeline owner as timeline contributions**

So the current system has:

- a good **turn owner**
- a good **continuation queue**
- but no good **shared live contribution lane into the currently materialized timeline**

This document defines that lane.

## 2. Current Behavior

### Timeline ownership

The React engineering layer creates the browser and the timeline:

- `build_react(...)` creates `ReactSolverV2`
- `ContextBrowser.load_timeline()` loads persisted timeline artifacts
- after that, contributions are local:
  - `ContextBrowser.contribute(...)`
  - `Timeline.contribute(...)`

This means:

- timeline mutation is intentionally owned by the active React instance
- no outside component can safely mutate that in-memory timeline directly

That ownership model is correct and should remain.

### Continuation behavior

Ingress already handles busy conversations:

- if a conversation is already `in_progress`, ingress converts the message into a continuation
- current kinds are `regular`, `followup`, `steer`
- continuations are published into the shared per-conversation external event source
- the live React owner can now consume them through the active timeline listener
- processor promotes the next unconsumed continuation after the current task completes

This is now both:
- a shared live timeline contribution model for active React turns
- and a fallback continuation scheduling model when no live owner consumes the event

### What is missing

The original gaps this design addressed were:

- a shared Redis-backed event source for timeline contributions during an active turn
- an active-turn listener lease that says “this turn can accept live contributions now”
- a formal hook such as `on_external_event(...)` in the React engineering layer
- a rule for how to handle events that arrive:
  - during the turn
  - after the turn
  - against an older turn while a newer turn is already active

## 3. Design Goals

We want all of the following:

1. Keep **turn-local ownership** of the mutable in-memory timeline.
2. Allow `steer` / `followup` to be accepted **during** a running turn.
3. Make those events replayable while retained in Redis if the owner process dies before timeline persist.
4. Invoke React-side hooks when such events are accepted.
5. Preserve the existing continuation model as the **fallback execution path**.
6. Generalize later to other external event kinds without redesigning the core again.

## 4. Non-Goals

This design does **not** try to do these things yet:

- let arbitrary bundle methods directly mutate the active timeline
- replace the normal conversation task queue entirely
- invent a second “summary agent” that interprets events separately from React

Events are accepted live and can have engineering-layer effects before the next normal reasoning boundary:

- they become visible to the current turn
- they trigger hooks
- `followup` affects the next decision boundary on the same turn
- `followup` can extend the turn's effective iteration ceiling through live reactive-event credit
- `steer` can cancel the active generation/tool phase first and only then hand React a bounded finalize phase

## 5. Core Decision

The right design is:

- keep the **timeline owner local**
- make external contributions go through a **shared Redis-backed conversation event source**
- let the active owner **listen** and apply those events live
- fall back to normal continuation task promotion when no live owner exists

Important consequence:

- the `Timeline` object itself should remain mostly pure data + persistence
- the **listener belongs in the React engineering layer around the timeline**, not inside `Timeline` itself

So, logically, “the timeline starts listening when created” is true, but physically the listener is owned by:

- `ContextBrowser` for lifecycle and active timeline ownership
- `sdk/solutions/react/events/listener.py` for the Redis wait loop and owner
  lease refresh/release helpers

not by the `Timeline` data class itself.

## 6. Shared Timeline Model

The shared timeline is **not** one mutable blob in Redis.

It is composed of three cooperating pieces:

1. **Persisted base timeline**
   - the normal `conv.timeline.v1` artifact
   - loaded by `ContextBrowser.load_timeline()`

2. **Shared external event log**
   - a append-only Redis conversation event stream
   - contains `steer`, `followup`, and future external events

3. **Local active materialization**
   - the current in-memory `Timeline` instance
   - owned by the active React turn
   - updated live by the listener when new external events arrive

This means:

- the base timeline is the checkpointed history
- the external event log is the shared live delta lane
- the active timeline is the current materialized view

That is the right notion of “shared timeline”.

## 7. Canonical Event Source

The canonical source for `steer` / `followup` should become a **shared conversation event log**, not a destructive mailbox list.

Recommended abstraction:

- `ConversationExternalEventSource`

Canonical envelope shape:

```python
{
  "event_id": "evt_...",
  "sequence": 123,
  "kind": "followup" | "steer" | "external",
  "created_at": 1775861000.123,
  "conversation_id": "...",
  "target_turn_id": "turn_A" | None,
  "active_turn_id_at_ingress": "turn_B" | None,
  "owner_turn_id": "turn_B" | None,
  "explicit": True | False,
  "source": "ingress.sse" | "ingress.socket" | "system" | "webhook",
  "text": "...",
  "payload": {...},
  "task_payload": {...} | None
}
```

Notes:

- `target_turn_id` is the user’s intended target if they supplied one; it is preserved as advisory metadata
- `active_turn_id_at_ingress` is what ingress observed as currently active from server conversation state
- `owner_turn_id` is the live owner lease turn id observed by ingress, when a React owner exists
- `task_payload` is present when proc can promote the event into a normal fallback turn if no live owner consumes it
- `text` is the user-visible message content when relevant
- `payload` carries structured event fields

### Why not keep the Redis list mailbox as the main truth?

Because a destructive queue is wrong for a shared-timeline contribution model:

- once popped, the event is no longer available for replay
- live consumption and crash recovery become fragile
- “accepted live but not yet folded into persisted timeline” becomes hard to reason about

We need an append-only log semantics, not only a queue semantics.

## 8. Turn Listener Lease

When a React timeline is materialized for a running turn, the React engineering layer should register a short-lived lease in Redis.

Recommended key:

- `chat:react:timeline-owner:{tenant}:{project}:{conversation_id}`

Value:

```json
{
  "turn_id": "turn_123",
  "bundle_id": "my.bundle@1-0",
  "instance_id": "...",
  "process_id": 12345,
  "listener_id": "uuid",
  "started_at": "...",
  "updated_at": "..."
}
```

Semantics:

- TTL around 10 minutes
- heartbeat every few seconds while the turn is alive
- removed on normal shutdown / turn finish
- overwritten when a newer turn becomes the active owner

Purpose:

- lets ingress/processor know whether a live owner exists
- avoids routing live events into nowhere
- establishes which turn currently owns live timeline contributions

## 9. Live Delivery Model

### Recommended rule

When `steer` / `followup` arrives:

1. append it to the shared conversation event log
2. check the active turn listener lease
3. if the lease matches the currently active turn, notify that owner
4. the owner reads new events from the log and applies them to the in-memory timeline
5. if no owner exists, keep the event pending for normal continuation execution

Important:

- the event is always written to Redis first
- the wake-up signal is only an optimization
- Pub/Sub alone is not enough

### Wake-up channel

For efficient live delivery, use a lightweight publish channel in addition to the Redis event source:

- `chat:react:timeline-wake:{tenant}:{project}:{conversation_id}`

The wake event should contain only:

- conversation id
- latest sequence
- maybe active turn id observed at ingress

The owner then drains from the Redis event source.

## 10. Where the Listener Lives

The listener should be owned by `ContextBrowser` or a closely related helper, not by the raw `Timeline` class.

Recommended shape:

- `ContextBrowser.start_external_event_listener(...)`
- `ContextBrowser.stop_external_event_listener()`
- `ContextBrowser.add_external_event_hook(callback)`

Lifecycle:

1. `load_timeline()` materializes the timeline
2. React runtime decides the turn is active
3. browser starts the listener lease + wake subscriber
4. on incoming event:
   - read from Redis event source
   - normalize to timeline blocks
   - `timeline.contribute_async(...)`
   - `timeline.write_local()`
   - invoke hooks

This preserves the current ownership boundary:

- only the owner mutates the local timeline
- external systems publish events, not blocks

## 11. Timeline Block Model

External events should be written into the timeline as explicit blocks, not hidden as side metadata.

Recommended block families:

- `user.followup`
- `user.steer`
- later: `event.external`

Recommended block example:

```python
{
  "type": "user.followup",
  "author": "user",
  "turn_id": active_turn_id,
  "ts": "...",
  "mime": "application/json",
  "path": "ar:turn_123.external.followup.mabc123",
  "text": "{\"message\": \"also include the legal cases\", ...}",
  "meta": {
    "event_kind": "followup",
    "event_id": "evt_...",
    "sequence": 123,
    "target_turn_id": "turn_122",
    "active_turn_id_at_ingress": "turn_123",
    "explicit": true
  }
}
```

If the event carries attachments, their canonical paths are message-owned:

- `fi:<turn_id>.external.followup.attachments/<message_id>/<filename>`

Transport rule:

- the live event carries only hosted reference metadata for attachments
- the receiver hydrates readable text/PDF/image content from hosting when folding the event into the timeline

Rendering rule:

- these should render as real user-originated continuation input, not as invisible transport noise
- the agent should be able to see “a followup arrived during the turn” or “a steer arrived during the turn”

So the model-facing render should be something like:

```text
[FOLLOWUP DURING TURN]
also include the legal cases
```

or

```text
[STEER DURING TURN]
change direction: focus on audit scope, not remediation
```

## 12. Hook Contract

After the event is accepted into the timeline, the React engineering layer should receive a hook.

Recommended contract:

```python
await on_external_event(
    type="followup" | "steer" | "external",
    event=envelope,
    blocks=[...],
)
```

The default subscriber should be the React runtime layer.

Typical uses:

- record that external guidance arrived
- surface an announce/update block if desired
- mark runtime state as “external input pending”
- let the next round boundary react to it

Important:

- the hook runs **after** the event is accepted into the timeline
- the timeline is the primary truth
- the hook is a reaction, not the primary storage mechanism

## 13. Safe React Semantics

`steer` and `followup` should not mean “interrupt absolutely everything immediately.”

V1 semantics should be:

- accept live
- append to timeline
- invoke hook
- let the next safe React boundary observe it

Safe boundaries include:

- after current tool result arrives
- before the next decision call
- before final exit/answer emission, if still possible

This is much safer than attempting arbitrary mid-call cancellation.

## 14. Late and Out-of-Order Events

We need a clear rule here.

### Recommended rule

- **conversation targeting is hard**
- **turn targeting is soft**

Meaning:

- the event definitely belongs to this conversation
- the provided `target_turn_id` is advisory history
- the actual accepting owner is decided by server state: `owner_turn_id` and `active_turn_id_at_ingress`
- React hook filtering must not use `target_turn_id` as authority; it only uses `owner_turn_id` / `active_turn_id_at_ingress`, and remains permissive for legacy/system events that do not carry either field

Examples:

1. Event arrives while turn A is active
   - lease says owner is turn A
   - turn A accepts it live

2. Event targets turn A, but turn B is already active
   - turn B is the live owner
   - event is added to turn B with metadata saying it targeted turn A

3. Event is accepted while busy, but the owner closes before it is folded
   - the event remains in the retained Redis source with its `task_payload`
   - proc later claims and promotes it once into the normal ready queue
   - the UI must wait for the later `chat_start`; the original ack was only admission

3. Event arrives after turn A ended and no live owner exists
   - event remains pending in the shared event log
   - normal continuation execution picks it up as the next task

This avoids dead targeting of closed turns.

## 15. Fallback Execution Path

If no live owner exists, the event must still be handled.

The correct fallback is:

- create or promote a continuation task through the normal processor queue
- the new task loads timeline + pending shared events
- React continues from there

This is the correct use of the existing processor lifecycle.

### Why not `bundle.@event(type="steer")` / `bundle.@event(type="followup")`?

This is the wrong abstraction for v1.

Reasons:

- `steer` / `followup` are conversation-runtime semantics, not business-domain bundle events
- they belong to the React turn lifecycle
- routing them to arbitrary bundle methods would split the behavior between:
  - live accepted events
  - replayed continuation tasks
  - bundle custom event code
- that would create protocol drift and broken-phone behavior

So for `steer` / `followup`:

- do **not** use bundle `@event(...)` as the primary design
- keep them owned by the conversation runtime

A generic bundle event system may still be valuable later for webhooks or business signals, but not as the base model for steer/followup.

## 16. Persistence / Folding Model

The Redis external-event source is retained independently of the timeline
artifact, but it is not permanent conversation storage.

The persisted timeline should track the highest external-event sequence already folded in.

Recommended payload addition:

```json
{
  "last_external_event_seq": 123
}
```

Meaning:

- base timeline artifact contains all event contributions up to that sequence
- later materialization can replay events with higher sequence values

This gives us crash recovery within Redis retention:

- if the owner applied events locally but died before persist, the events are still in the Redis source
- next materialization replays them

Events that must remain available after Redis retention must also be persisted
as durable conversation storage or bundle/application artifacts.

## 17. Recommended Rollout

### Phase 1

Introduce the abstraction and listener model:

- new shared event log abstraction
- turn-owner lease
- browser-owned listener
- external event blocks
- `on_external_event(...)` hook

Handle only:

- `followup`
- `steer`

### Phase 2

Wire fallback promotion cleanly:

- if no owner exists, processor promotes pending event-log items to normal continuation tasks
- current queue behavior remains user-visible compatible
- during migration, the old Redis mailbox can survive as an implementation detail behind the new abstraction, but it should stop being the conceptual source of truth

### Phase 3

Generalize:

- arbitrary external event kinds
- system-originated timeline contributions
- optional richer interruption policies

## 18. Concrete Wiring Points

### Ingress

Current files:

- `apps/chat/ingress/sse/chat.py`
- `apps/chat/ingress/chat_core.py`

Needed behavior:

- classify busy message into `followup` / `steer`
- append canonical external event envelope into shared event log
- check active owner lease
- if live owner exists, publish wake notification
- otherwise rely on continuation task promotion

### Processor

Current file:

- `apps/chat/processor.py`

Needed behavior:

- after turn completion, promote pending external events when no live owner handled them
- maintain compatibility with current continuation execution path
- eventually stop thinking in terms of a destructive mailbox as the canonical truth

### React browser / timeline

Current files:

- `apps/chat/sdk/solutions/react/browser.py`
- `apps/chat/sdk/solutions/react/timeline.py`

Needed behavior:

- browser owns listener lifecycle
- timeline stays the storage/persistence model
- browser translates external event envelopes into timeline blocks
- browser invokes external-event hooks
- persisted timeline tracks folded external-event watermark

## 19. Proposed API Surface

Not implementation-final, but directionally:

```python
class ContextBrowser:
    async def start_external_event_listener(self) -> None: ...
    async def stop_external_event_listener(self) -> None: ...
    def add_external_event_hook(self, cb) -> None: ...
```

```python
class ReactSolverV2:
    async def on_external_event(self, *, type: str, event: dict, blocks: list[dict]) -> None: ...
```

```python
class ConversationExternalEventSource:
    async def publish_event(...): ...
    async def read_since(...): ...
    async def mark_folded(...): ...
```

## 20. Final Recommendation

The correct design is:

- **keep local turn ownership**
- **add a shared Redis-backed conversation event source**
- **register an active-turn listener lease**
- **let the React owner accept events live into its timeline**
- **persist a folded-event watermark with the timeline**
- **use normal continuation task execution only as fallback**

And specifically:

- do **not** route steer/followup through bundle `@event(...)` methods in v1
- do **not** put Redis/network listener logic directly into `Timeline`
- do **not** rely only on Pub/Sub or only on a destructive mailbox list

The right mental model is:

- the active React turn owns the local timeline
- the conversation owns the Redis external event source
- the shared timeline is the combination of both

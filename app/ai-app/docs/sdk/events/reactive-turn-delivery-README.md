---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/events/reactive-turn-delivery-README.md
title: "Reactive Turn Delivery"
summary: "Framework-neutral contract for how one external event becomes one turn of any agent: the @on_reactive_event door, per-conversation serialization, the lane consumer reservation, the two consumption models (ReAct folds mid-turn vs run-to-completion one-event-per-turn), and the reactive-lane finalize invariant that keeps followups from being lost."
status: active
tags: ["sdk", "events", "external-events", "turns", "react", "run-to-completion", "followup", "steer", "ordering"]
updated_at: 2026-07-13
keywords:
  [
    "reactive turn delivery",
    "on_reactive_event door",
    "run() execute_core",
    "conversation event lane wakeup",
    "consumer reservation scheduled active none",
    "per-conversation serialization",
    "run-to-completion turn",
    "reactive_lane finalize",
    "followup promotion",
    "scheduled_consumer_fresh",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/dataflow/connect-agentic-loop-to-ordered-delivery-README.md
---
# Reactive Turn Delivery

This page answers one question, for **any** agent — ReAct, a ported graph, a
bespoke loop:

```text
How does an external event become exactly one turn of my agent,
in arrival order, with no turn lost and no two turns of the same
conversation running at once?
```

The delivery contract is the same for every agent. What differs is only *when*
an agent releases the lane it was handed — and that single difference is what
this page makes explicit, because getting it wrong silently drops the next turn.

For the Redis state fields this page refers to (`T.consumer.status`,
`T.handler.status`, the reactive cursor), see the
[Conversation Event Lane State](./conversation-event-lane-state-README.md)
reference — this page does not restate them. For the ReAct-specific,
field-level transport journey, see
[Event Ingress To React Turn](./event-ingress-to-react-turn-README.md).

## One-page model

```text
external event (prompt / followup / steer)
        │  written to the conversation event lane (ordered log)
        │  + one wakeup enqueued for the first reactive event   [atomic]
        ▼
   processor claims the wakeup
        │  acquires the per-conversation lock  ── serialization point
        │  reserves the lane consumer:  T.consumer.status = scheduled
        ▼
   run()   ── the @on_reactive_event door (shared by every agent)
        │
        ▼
   execute_core(state, thread_id, params)   ── your agent runs the turn
        │
        ▼
   turn ends  →  the lane consumer reservation is released
                 →  any event queued during the turn is re-woken (next turn)
```

The wakeup is the *only* thing that runs an agent. `run()` is the shared
`@on_reactive_event` door on the app base (`BaseEntrypoint.run` /
`BaseEntrypointWithEconomics.run`); it calls your `execute_core`, which reads the
triggering event out of `state`/`params` and produces the turn.

## Ordered, serialized delivery

Turns of one conversation are **serialized** by a per-conversation lock. A second
event that arrives while a turn is running does **not** start a concurrent
`execute_core`:

```text
Event 1 → wakeup → lock acquired → run()/execute_core (turn 1 running)
Event 2 arrives now → wakeup enqueued → processor tries to claim it
        → cannot acquire the conversation lock → REQUEUES (waits)
turn 1 ends → reservation released → lock released
        → Event 2's wakeup is claimed → run()/execute_core (turn 2, in order)
```

- **Same conversation:** one turn at a time, in arrival order. The lock holds
  across processor workers, so two workers cannot run two turns of the same
  conversation at once.
- **Different conversations:** run in parallel (independent locks).

This is a platform guarantee — an agent does not implement it. What an agent
*is* responsible for is releasing the lane consumer it was handed, so the waiting
event can promote. That is the next section.

## The lane consumer reservation

When the processor dispatches a wakeup it **reserves** the lane consumer
(`T.consumer.status = scheduled`) before the turn runs. The reservation exists so
the platform knows a turn is responsible for this lane. Whoever holds it must
release it (`→ none`) when the turn is done. If it is left reserved, the next
turn's wakeup — claimed inside the reservation's freshness TTL — is refused as
`scheduled_consumer_fresh` and **silently dropped**: the next turn never runs.

Releasing the reservation is where the two agent models diverge.

## Two consumption models

### 1. ReAct — a live consumer that folds mid-turn

A ReAct `execute_core` drives a `BaseWorkflow`, which **opens the lane handler**
(`T.handler.status = open`), marks the consumer `active`, and reads the lane
*during* the turn. A followup that lands mid-turn is folded into the running turn
at a decision boundary. At the close gate the workflow **releases** the consumer
(`→ none`), advances the reactive cursor past what it consumed, and re-wakes
anything still unconsumed. ReAct owns its lane lifecycle end-to-end, inside its
own workflow. See
[Event Ingress To React Turn](./event-ingress-to-react-turn-README.md).

### 2. Run-to-completion — one event, one turn

A run-to-completion `execute_core` (a ported graph, a bespoke loop) runs
start→finish and does **not** watch the lane. It consumes exactly the triggering
event; a followup that lands mid-turn is *not* folded. Because it never opens the
handler, it never releases the reservation on its own — so the platform releases
it *for* the agent, from the door, after the turn. That is the finalize
invariant below.

The net behavior for a run-to-completion agent: **one event → one turn**,
strictly serialized, in order, with the next event or queued followup promoted to
its own fresh turn the instant the current turn ends.

## The finalize invariant (`reactive_lane`)

The shared door (`run()`) finalizes the reactive-event lane after `execute_core`
returns — on success **and** error, skipped only on cancel (which stays on the
inflight-recovery path). It lives in a dedicated module,
`chatbot/reactive_lane.py`, and the door makes a single thin call to it. The
finalize is a **state-conditional, idempotent invariant — never an agent-type
branch**:

```text
already_released = T.consumer.status == "none"
own_accounted    = the turn's own event is consumed / past the reactive cursor

if already_released and own_accounted:
    return                       # no-op

# otherwise a run-to-completion turn left the reservation dangling:
release the consumer (→ none)
mark the turn's own event consumed        # exactly-once
re-wake promotable reactive work          # queued followup → next turn
expire an unconsumed event.user.steer     # active-turn control, never future work
```

The `already_released and own_accounted` state is *exactly* what a ReAct turn's
`BaseWorkflow` leaves behind — so a ReAct turn is inert here **by state, with no
`if react` check**. A run-to-completion turn left the reservation `scheduled`, so
the predicate is false and the door releases it. The finalize reuses only the
existing lane primitives (the same exactly-once `mark_consumed_up_to` a
`BaseWorkflow` uses, `mark_consumer_none`, and the wake re-publish); it adds no
new orchestrator behavior and touches nothing on the ReAct path.

## Followup and steer, per model

`accepts_followup` / `accepts_steer` are per-agent capability declarations. They
change what the composer *offers*, not what is delivered:

- **ReAct (accepts both):** a mid-turn followup is folded into the running turn; a
  steer cancels and finalizes it.
- **Run-to-completion (declares both false):** a mid-turn message is queued and
  **promoted to the next turn** by the finalize re-wake — it is not folded into
  the running turn. This is the "Queue for next turn" composer state. No agent
  code is required for the promotion; the door does it.

`event.user.steer` is never included in that promotion. It controls only the
turn that was active when ingress accepted it; if that turn does not consume
the control before closing, the control expires.

An agent that *can* consume mid-turn integrates the ReAct-style handler
(open/close, `mark_consumed_up_to`) inside its own `execute_core`; see the recipe.

## The failure mode this prevents

Before the finalize existed, a run-to-completion turn left the reservation
`scheduled`. The next turn's wakeup, arriving inside the reservation's TTL, was
dropped as `scheduled_consumer_fresh` — the turn "completed" in the UI but the
*next* message never reached `execute_core` (nothing in the processor log). It
self-recovered only after the TTL went stale, which read as intermittent
"second/third turn hangs." The finalize releases the reservation at turn end, so
the next turn promotes immediately.

## Boundary

This page is the framework-neutral delivery contract. It does not cover the
transport-level field origins (see the ReAct ingress page), the lane state fields
(see the lane-state reference), or how to build a specific agent (see the
recipe). The one rule every non-ReAct agent must respect is implicit and handled
for you: **a turn releases the lane it was handed.** The `run()` door guarantees
it; you implement `execute_core` and get ordered, serialized, exactly-once
delivery for free.

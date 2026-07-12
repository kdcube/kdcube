---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/work-with-subagents-README.md
title: "Work With Subagents"
summary: "The charter-scoped subagent contract: react.delegate spawns a child conversation, react.contribute and subagent.* lane events carry results back."
tags: ["sdk", "agents", "react", "subagents", "delegation"]
keywords: ["react.delegate", "react.contribute", "charter", "agent_alias", "helper alias", "strength class", "subagent.contribution", "subagent.converged", "subagent.failed", "fork", "child conversation", "visibility", "subagent thread"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/timeline/fork-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-cache-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/compaction-README.md
---
# Work With Subagents

A subagent is a full ReAct agent that works a scoped assignment in its OWN
conversation, opened from a parent turn. The child is a first-class turn
submitted for fair scheduling on the cluster — the same paradigm as async
webhook ingress — so gates, throttling, per-user capacity, and accounting
apply to it exactly as to any submitted turn; five fanned-out subagents are
five turns on the queue, fairly interleaved. The parent stays interactive
and fully unpinned: delegate is a kickoff, and the parent may finish its
turn with children still running. The child runs under the same
tenant/project/user, inherits the parent's tool and skill configuration,
and reports back through the parent conversation's event lane; its terminal
report is promotable, so a finished parent picks the outcome up as a
continuation turn.

## The Contract In One Pass

```
PARENT CONVERSATION                    CHILD CONVERSATION (fresh id)

turn ...                               (seeded at delegate time)
  react.delegate(charter) ----------->   [fork: working summaries +
  fork marker block on timeline           parent's in-progress turn]
  ... parent keeps working,             [SUBAGENT CHARTER] event with a
  finishes whenever it is done ...        task payload — its promotion IS
                                          the kickoff: the child turn is
                                          fair-scheduled like any
                                          reactive-event turn
  <---- subagent.contribution events    react rounds under the charter
        (live fold or next-turn           budget; react.contribute sends
        context)                          partial results back
  <---- subagent.converged / .failed    child persists (completion blocks,
        with a task payload: a live       workspace, timeline), THEN
        parent turn folds it; an idle     authors the terminal event
        parent lane promotes it into
        a continuation turn that
        responds to the user
```

## Availability And The User's Decision

Delegation follows the capabilities model (ceiling → pick → enforcement):
the admin sets availability and the default, the user decides use. In the
agent's config block declare:

```yaml
react:
  agents:
    main:
      subagents:
        allowed: true
        models:                 # helper aliases the agent delegates to
          strong_agent:
            provider: anthropic
            model: claude-sonnet-4-6
            class: strong       # regular | strong | strongest
            caption: deep reasoning and synthesis
          fast_agent:
            provider: anthropic
            model: claude-haiku-4-5-20251001
            class: regular
            caption: quick focused work
        model: strong_agent     # default alias when a charter names none
        max_rounds: 8           # round budget for every delegated assignment
```

`allowed: true` puts the ability in the agent's pickable inventory,
**default ON** for users. Delegation can raise the quality of hard tasks,
and every delegation is additional model spend on the user's account — the
user pays, so the user decides: the capability picker exposes the toggle
(`agent_capabilities` carries the `subagents` inventory entry with exactly
that trade-off as its description; `agent_selection_update` stores the
choice as `subagents: true` in the deny map). It is the same principle as
the per-user model pick — the admin declares what is allowed, the user picks
their own price/quality point. See
[Per-User Agent Capabilities](../../solutions/user-settings/capabilities-README.md).

A turn where delegation is on (offered and not denied) carries
`react.delegate` in the tool catalog and the delegation guidance in the
instructions; every other turn's catalog and instructions are assembled
without them. With the flag absent (or `allowed: false`) the ability is
outside the inventory entirely — users cannot enable it. The bundle-level
`react.subagents:` block keeps serving the shared defaults (the `models`
alias map, the default alias, `max_rounds`, `visibility`), and a bundle-level
`react.subagents.allowed: true` offers it for the bundle's agents as a
group; the per-agent declaration decides when both are present. A bare
`subagents: true` and the `enabled:` key stay accepted as shorthand for
`allowed: true`.

The `models` map defines **helper aliases**: the label (`strong_agent`,
`fast_agent`, whatever the admin chooses) is the vocabulary the agent's
`agent_alias` argument speaks; the `provider`/`model` mapping behind each
alias stays the admin's. Each entry may carry a `class` (one of `regular <
strong < strongest`, the strength vocabulary the delegating agent reads) and
a `caption` (one line on what the helper is good for). Two aliases ship as
built-in defaults and are present even with an empty `models:` map —
`fast_agent` (class `regular`, anthropic `claude-haiku-4-5-20251001`,
"quick focused work") and `strong_agent` (class `strong`, anthropic
`claude-sonnet-4-6`, "deep reasoning and synthesis"); admin entries merge
over them (admin wins), and `strongest_agent` belongs to the vocabulary but
ships unconfigured. An alias that is not configured resolves to the
smartest configured one by class order — so `strongest_agent` without an
admin entry runs as `strong_agent`. Alias models are the admin's delegation
choices — independent of the user-pickable `supported_models` list.

## Spawn: react.delegate

`react.delegate` is available on turns where delegation is on. Arguments:

- `charter` — the assignment prompt, a single string. The delegating agent
  writes it self-contained: the goal and what to send back (deliverables,
  contribution expectations) belong in the prompt text. The subagent cannot
  ask the user or the parent questions.
- `agent_alias` (optional) — which helper runs the assignment: an alias
  from the agent's alias map (admin `subagents.models` entries over the
  shipped defaults). An alias-less charter runs on the configured default
  alias (`subagents.model`); with no default configured the child inherits
  the parent's role models. An unconfigured alias resolves to the smartest
  configured one, and a direct model name from the admin-allowed
  `supported_models` list keeps resolving — the spawn never fails on
  naming.

The round budget is config's business, never the model's: every delegated
assignment runs on `subagents.max_rounds` (default 8, hard cap 30). It IS
the child's iteration budget; reactive iteration credit is disabled on the
child, so nothing extends it.

The tool doc the agent reads is **static and cache-pure**: identical for
every user, free of model and provider names, part of the byte-stable
cached system instruction. The situational half lives in the per-round
announce block's `[DELEGATION]` section (parents only; a subagent gets no
section):

- the agent's own identity in the same alias vocabulary as the helper list
  (`you are: fast_agent [regular]`), resolved by matching its effective
  strong-decision model (the user's pick already applied) against the alias
  map — so comparing itself to a helper is a direct read; unmatched means
  the line is simply absent;
- the helper aliases, one line each: alias, class, caption (from config or
  the shipped defaults);
- the live delegations on this conversation's timeline, one line each:
  charter caption, the alias/class the helper runs as, and its status
  (`running` / `contributed N` / `converged` / `failed`), derived from the
  fork markers and folded `subagent.*` events. Lifecycle: an unresolved
  delegation renders every round — it is a live obligation; a terminal one
  renders only during the turn its completion folded, after which the
  outcome lives on as ordinary timeline history.

The agent therefore judges delegation by aliases, classes, and captions it
reads fresh every round, while the cached instruction stays identical
across users and configurations.

The tool returns immediately with a launch ticket
(`child_conversation_id`, `child_conversation_ref`, `child_turn_id`,
`status: scheduled`) and records a `react.subagent.fork` marker block on the
parent timeline -- the parent model's durable knowledge of what it spawned,
with the charter caption, the helper alias/class it runs as, and the
budget. At turn end the marker blocks become
structured `forks: [{child_conversation_id, charter_goal, forked_at}]`
descriptors on the parent turn's stored record (the turn log), and the child
conversation's stored timeline carries the matching
`forked_from: {conversation_id, turn_id}` backref -- both sides queryable, so
conversation fetch can reconstruct the fork relationship on reload.

Delegate itself does exactly the work that needs the parent's in-memory,
not-yet-persisted timeline, then returns:

1. the fork projection is persisted as the child conversation's seed
   timeline (durable, as-of delegate time -- the seed travels via the
   persist, never via the queue payload, so queue-time staleness is correct
   by construction);
2. the `[SUBAGENT CHARTER]` event is authored onto the child's lane WITH a
   task payload, atomically with the processor wakeup through the gateway's
   admission (the same atomic enqueue chat ingress uses), under a session
   derived from the parent's user identity with the delegate source marker.
   The promotion is the kickoff: the child's turn runs through the same
   admission, gates, and fair scheduling as any submitted turn, on whatever
   worker claims it.

Guards: a subagent cannot spawn subagents (the tool refuses with
`delegate_depth_limit`, and the child's catalog carries no `react.delegate`).
A backpressure rejection fails the delegate with the gateway's reason
(`delegate_queue_saturated`) and removes the seed -- a rejected delegate
leaves no child state. A spawn failure surfaces as the tool result
(`delegate_spawn_failed`), and a child that dies later authors
`subagent.failed` -- failures are always authored, never silent.

## What The Child Opens With

The child conversation starts from a fork: a projection copy of the parent
conversation's working summaries plus the parent's in-progress turn, with
file refs conversation-qualified so they stay pullable across conversations.
The fork mechanics (ordering, ref rewriting, persistence) are the timeline
fork primitive -- see
[fork-README.md](../../solutions/timeline/fork-README.md).

After the fork, the charter arrives as an authored event on the child's own
event lane (`payload.event.type = subagent.charter`, author
`agent:conv_<parent id>/<parent turn>`, targeted at the child turn id minted
at delegate time). When the promoted child turn runs, the ordinary timeline
load finds the seed as prior history and the external-event fold
materializes the charter inside the turn. The charter text carries the
assignment prompt as the parent wrote it plus the budget and the reporting
mechanics; it is the child's task, while the forked blocks above it are
context.

The child turn itself runs with the charter's overrides: `max_rounds` IS the
iteration budget (reactive iteration credit is off, so nothing extends it),
the depth guard and parent lane address are wired for `react.contribute`,
and the child's session/actor derive from the parent's user identity with
the delegate source marker (`react.subagent.delegate`) -- authored by the
agent, owned by the same user.

## Report Back: react.contribute And subagent.* Events

The child's catalog carries `react.contribute(report, refs)`:

- `report`: text written for the delegating agent.
- `refs`: logical paths from the CHILD conversation, delivered
  conversation-qualified. File refs (`conv:fi:conv_<child id>.turn_...`)
  are the working currency: the parent's `react.pull` resolves them
  directly, which is why a charter prompt asks for its deliverables as
  files. Other namespaces ride along as provenance.

Each contribute call authors one `subagent.contribution` event into the
parent conversation's lane, through the same inception primitive consent
grants use: transport kind `external_event`, semantic type nested in
`payload.event.type`, `task_payload=None` and `reactive: false` -- passive by
construction, so a contribution can never start a turn or buy the parent
iteration credit. A LIVE parent turn folds it mid-flight through the lane
watcher (fold totality renders it and advances the cursor); an idle parent
receives it as context at its next turn.

At child completion — AFTER the child's end-of-turn persistence (completion
blocks, workspace, timeline), so every ref the report names is pullable —
the runtime authors one terminal event:

- `subagent.converged` -- carries the child's final answer.
- `subagent.failed` -- carries the failure reason (budget exhausted without
  an answer, or an exception).

The terminal events carry a task payload: the parent's continuation turn
(fold the completion, pull the deliverable refs, respond to the user). Two
consumption modes, exactly once:

- a LIVE parent turn folds the event through the lane watcher — fold
  totality records the consumption, and the promoter acks the wakeup;
- an idle parent lane promotes it — the continuation turn is fair-scheduled
  like any reactive-event turn and delivers the outcome to the user.

A completion is never lost: the lane publish is unconditional, and the
wakeup then goes through the gateway's admission like every turn. A wakeup
the admission declines leaves the completion resting in the lane, where the
parent's next turn folds it — degraded liveness, zero loss.

`reactive: false` stays universal across every `subagent.*` event: none of
them buys iteration credit inside a live turn. Promotability-when-idle is
the separate axis, and the completions (plus the charter, as the child's
inception) carry it; contributions stay passive.

## Visibility: silent | thread

The agent's config block declares how much of the child's live stream the
user sees:

```yaml
react:
  agents:
    main:
      subagents:
        enabled: true
        visibility: thread    # silent (default) | thread
```

**`silent`** (the default) shows the user exactly three things from a
subagent: the fork marker on the parent timeline, the contribution events,
and the terminal converged/failed event -- all as parent-conversation
context. The child's own streaming (thinking deltas, tool events, canvas
writes) is filtered at the child communicator's emission choke point. This
is the default because it works with every deployed chat widget: a client
that renders one conversation stream receives one conversation stream. The
child conversation itself is fully persisted (timeline, workspace
artifacts, final answer), so its work is inspectable by ref and by
conversation id after the fact.

**`thread`** streams the child live, as a thread inside the parent
conversation, for clients that render threads. Every child emission --
deltas, steps, events, the turn's chat.start/chat.complete boundaries -- is
delivered to the PARENT conversation's channel (the user's existing socket;
the relay carries it from whatever worker runs the child), keeps the
CHILD's event identity (`conversation.conversation_id` / `turn_id`), and
carries a top-level stamp:

```json
"subagent": {
  "child_conversation_id": "sub_...",
  "forked_from_conversation_id": "conv_...",
  "forked_from_turn_id": "turn_...",
  "charter_goal": "..."
}
```

The client multiplexes on the stamp: stamped events render as a
collapsible subagent thread anchored under the fork turn, with the same
delta/step/event treatment as a regular turn -- it IS a regular turn, drawn
indented. The same stamp shape rides inside the structured facts of every
`subagent.*` lane event (charter, contribution, converged/failed), so the
thread's milestones anchor without text parsing, and conversation fetch
rebuilds the same threads on reload (the fetch contract is in
[fork-README.md](../../solutions/timeline/fork-README.md)).

Visibility is resolved at delegate time from the delegating agent's config
and travels with the assignment, so the child applies the declared policy
on whatever worker claims its turn.

## Cost

Every subagent round is a full decision-model call billed like the parent's
own rounds, and the child starts with a cold prompt cache (the fork copies
context bytes; it cannot copy cache state -- see
[micro-agents-and-cache-README.md](micro-agents-and-cache-README.md)
for why any separate agent call has its own cache story). Child spend is
accounted under the CHILD conversation for the same user and app, tagged
with accounting agent/component `react.subagent` and a `subagent` metadata
block naming the parent conversation, turn, and charter goal -- so subagent
spend is separable in the ledgers. Charter discipline follows: one
well-chartered subagent beats several small ones, and quick work belongs in
the parent's own rounds.

## Choosing The Delegation Weight

Three delegation instruments, ordered by weight:

1. `llm_tools.generate_content_llm` -- one separate model call that returns
   its artifact inline to the calling turn. The degenerate no-fork case: no
   child conversation, no charter, no lane events. Right for a single
   generation with a precise handoff.
2. In-turn micro-agent calls -- separate model calls launched from within a
   turn whose results fold back into the same timeline. Their prompt-shape
   and cache economics are the subject of
   [micro-agents-and-cache-README.md](micro-agents-and-cache-README.md);
   everything that doc says about envelopes, handoff cost, and cache
   prefixes applies to every instrument on this list.
3. `react.delegate` -- this document: a chartered child CONVERSATION with its
   own timeline, workspace, budget, and lane-event reporting, running in
   parallel with the parent. Right for self-contained work worth its own
   budget and persistence.

The boundary: instruments 1-2 live and die inside the parent's turn;
instrument 3 outlives the turn, holds durable state of its own, and reports
through the conversation event lane.

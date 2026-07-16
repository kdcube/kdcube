---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/hosted-agent-conversation-README.md
title: "The Conversation For Any Agent"
summary: "How KDCube maintains the conversation for ANY hosted agent — KDCube's own ReAct or a wrapped external framework (LangGraph, LangChain, a raw loop). The model: two memories with two owners — the agent's working memory (its checkpointer/store, what the model sees this turn) and the platform conversation record (what the chat component lists, reloads, searches, titles). Details the record's per-turn artifacts (turn.log blocks, conv.timeline.v1, conv.artifacts.events, conv.artifacts.stream, conversation files as conv:fi: links), the two write doors (the React workflow's rich finish_turn vs. the framework-neutral fallbacks in the app base, all homed in solutions/conversation/record.py), the read side (list, reload replay, downloads, external view, search), cost/time restoration, the first-turn title and its identity + role-binding contracts, durable checkpointer keying, and why client-sent chat_history is a hint."
tags: ["sdk", "solutions", "conversation", "hosting", "langgraph", "port", "checkpointer", "turn-recorder", "conversation-record", "reload", "turn-log"]
updated_at: 2026-07-16
keywords:
  [
    "hosted agent conversation",
    "conversation record any agent",
    "conversation continuity",
    "conversation reload",
    "turn log blocks",
    "assistant.completion",
    "record_minimal_turn_log_if_absent",
    "record_error_turn_log_if_absent",
    "record_conversation_timeline",
    "conv.timeline.v1",
    "conv.artifacts.events",
    "conv.artifacts.stream",
    "persist_stream_artifacts",
    "solutions/conversation/record.py",
    "turn_log_was_recorded",
    "conversation title first turn",
    "chat.conversation.title",
    "scene_object_action",
    "durable checkpointer",
    "thread_id conversation_id",
    "chat_history hint",
    "conv:fi: conversation files",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/port-your-solution-to-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-component-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/reactive-turn-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/dataflow/connect-agentic-loop-to-ordered-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/search-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
---
# The Conversation For Any Agent

A user talks to an app: they ask, get an answer, come back tomorrow, reopen the
thread, and expect both the screen and the agent to remember. KDCube maintains
that conversation the same way for **any** agent — its own ReAct workflow or a
wrapped external framework (a LangGraph graph, a `create_agent`, a raw loop)
serving turns through `execute_core`. This page is the reference for that
machinery: what the durable record consists of, who writes each piece, how the
client reads it back, and what the agent's own memory must do to agree with it.

The step-by-step porting walkthrough lives in the
[port recipe](../../../recipes/kdcube_for_agents/port-your-solution-to-kdcube-README.md);
this page owns the model the recipe points to.

## Two memories, two owners

| Memory | Owner | Holds | Powers |
| --- | --- | --- | --- |
| The agent's **working memory** | your app (framework-native) | the messages the model sees THIS turn — prior turns restored from the agent's checkpointer/store | the model's in-context recall ("repeat what you said before") |
| The platform **conversation record** | KDCube (framework-neutral) | durable per-turn artifacts, per conversation | the chat component's list, reload, search, downloads, and title |

The two are separate stores. The conversation record is what the user sees on
screen after a reload; the working memory is what the model sees inside a turn.
An app keeps its own working memory (its store is internal and unchanged) and
lets the platform own the record. Both must reflect the same conversation.

## What the record consists of, per turn

The write side of the record has ONE home:
`kdcube_ai_app/apps/chat/sdk/solutions/conversation/record.py` — the payload
builders, the per-turn signals, and the persist calls described below all live
there (the old `sdk/runtime/turn_recording.py` path re-exports from it). A turn
leaves up to four artifacts plus its files:

**1. The turn log** (`kind: turn.log`, tag `artifact:turn.log`). The turn's
transcript as an ordered block list `{ts, blocks, blocks_count}`:

- `chat:user` — the user's message (rebuilds the user bubble on reload);
- user attachment meta blocks — filename/mime/size plus the `conv:fi:` link
  (rebuilds the attachment cards; the link is pullable later);
- `assistant.step` — progress steps;
- assistant file blocks — files the agent produced, again as `conv:fi:` links
  (rebuilds the file cards, powers Download);
- `assistant.completion` — the final answer (rebuilds the assistant bubble).

The React workflow writes a RICH turn log at `finish_turn` (full timeline:
thinking, canvas, sources_pool, and more). A framework-neutral turn gets the
MINIMAL log above from the fallback recorder — same block shapes, so the shared
reload reader (`Timeline.build_turn_view`) treats both alike.

**2. The conversation timeline** (`conv.timeline.v1`). The per-conversation
registration artifact. The conversation **list** is built from this artifact and
only this artifact (`list_conversations`) — a turn log alone does not make a
conversation appear. It is (re)written on every recorded turn by
`record_conversation_timeline` and carries the conversation title forward once
one exists.

**3. Dynamic chat events** (`conv.artifacts.events`). Full-payload copies of
the turn's emitted chat events — citations, steps, follow-ups, `accounting.usage`
(cost), `chat.turn.summary` (elapsed time) — captured from the communicator's
recording (`comm.export_recorded_events`) and saved by `_save_events_artifact`.
On reload the client re-emits them, so the reloaded turn shows the same
citations, follow-ups, cost, and timing it showed live.

**4. Stream artifacts** (`conv.artifacts.stream`). The turn's canvas / tool /
subsystem **delta streams** (the code-exec panel is the flagship case),
aggregated by the communicator per (turn, agent, marker, artifact) and persisted
by `persist_stream_artifacts` via `build_stream_artifact_payload`. On reload the
client replays each row as a synthetic completed `chat.delta`, rebuilding the
exec panel / canvas exactly as streamed. A turn that streamed nothing persistable
writes no artifact.

**Files.** Conversation files are durable as **conversation links**, not bytes
in messages: user uploads live at `conv:fi:…user.attachments/<name>`, files the
agent produces are hosted into the conversation's storage (the same edge as user
attachments) and addressed as `conv:fi:conv_<cid>.turn_<id>.files/<name>`. The
turn-workspace contract built on these links — nothing auto-read, pull/read by
link, workspace empty every turn — is the port recipe's "distributed turn
workspace" section.

## Who writes it: one contract, two doors

Recording is idempotent per turn, coordinated by a per-turn signal:
`reset_turn_log_recorded()` at turn start, `mark_turn_log_recorded()` by whoever
persists a turn log, `turn_log_was_recorded()` consulted by the fallbacks. The
signal is a **mutable dict on a ContextVar** — writers mutate the shared object,
so a log written in a sibling asyncio task (React's finalize) is still visible
to the door that checks it. `save_turn_log_as_artifact` marks it automatically.

**Door 1 — the React workflow.** Writes its own rich turn log at `finish_turn`,
persists the events artifact in its `post_run_hook`, and persists stream
artifacts itself. The framework-neutral fallbacks below see the marked signal
and stay inert — React turns are never double-recorded.

**Door 2 — any other framework.** The app base (`BaseEntrypoint.run`, which
every entrypoint inherits) wraps `execute_core` with the framework-neutral
sequence:

1. reset the per-turn signals;
2. run `execute_core` (your framework, your graph, your loop);
3. `post_run_hook` — a bundle that wants cost/time and panel replay on reload
   calls `_save_events_artifact(state=...)` and
   `_persist_stream_artifacts_fallback(state=...)` here (both inert when the
   turn already recorded);
4. `_record_turn_log_fallback` — when nothing recorded a turn log this turn and
   the turn produced a `final_answer`, records the minimal log via
   `record_minimal_turn_log_if_absent`. It recovers the turn's inputs/outputs
   best-effort: user prompt + attachments from `state["external_events"]`,
   produced files from `state["hosted_files"]` / `result["files"]`, the
   first-turn title from `result["conversation_title"]`. Recording never fails
   the turn.

**The failure sibling.** A turn that raises without surfacing its own failure
gets `_surface_turn_failure`: a user-visible `chat.error` plus
`record_error_turn_log_if_absent`, so the failed turn saves and **reloads as an
error turn** instead of vanishing. The same signal discipline applies
(`turn_error_was_surfaced`): a framework that surfaced its own error keeps the
backstop inert.

The delivery seam that triggers all of this — the reactive-event lane, the
wakeup, ordered delivery — is
[reactive turn delivery](../../events/reactive-turn-delivery-README.md). One
delivery fact matters to recording: a turn's attachments ride **sibling lane
events** of the ingress batch, so a run-to-completion door folds the batch
before reading the turn's inputs (port recipe, "turn-batch fold") — otherwise
the recorded turn (and the agent) sees the prompt only.

## How the client reads it back

- **List** — from `conv.timeline.v1` only (title, recency).
- **Reload** — the ingress fetch materializes `chat:user` / `chat:assistant`
  bubbles, attachment and file cards from the turn-log blocks; re-emits the
  `conv.artifacts.events` rows (citations, follow-ups, cost, time); replays
  `conv.artifacts.stream` rows as synthetic completed `chat.delta` envelopes
  (exec panel, canvas). Contract:
  [chat component communication](../chat/chat-component-communication-README.md)
  ("Stored Conversation Reload").
- **Download** — a file card's Download resolves through the
  `scene_object_action` operation: the app delegates a `conv:fi:` ref to
  `resolve_event_ref_action`, which answers with a `download_url`. An app that
  hosts files must serve this op or the card renders with no working Download.
  Contract: [chat widget solution](../chat/chat-widget-solution-README.md).
- **External/agent view** — `view.py::build_conversation_timeline` distills the
  same artifacts into a compact, chronologically-interleaved timeline (messages,
  files as `conv:fi:` refs, artifacts, sources) served over `object.get
  conv:conversation:<id>` for MCP/named-service consumers.
- **Search** — conversation search runs over the same turn logs
  (`api.py`: search → turn-log materialization → rich hits); see
  [conversation search](./search-README.md).

## Turn economics and timing on reload

The per-turn cost (`$`) and elapsed time the chat component shows are part of
the record, not just live badges. The economics door emits `accounting.usage`
(`cost_total_usd`) and `chat.turn.summary` (`elapsed_ms`) as chat events; they
persist through the `conv.artifacts.events` artifact (`_save_events_artifact`)
and re-surface on reload. The React workflow does this in its own
`post_run_hook`; any other door adds the same call (and, having no timeline of
its own, emits `chat.turn.summary` itself) or the reloaded turn shows no cost or
time. Match the platform event shapes — do not hand-roll a parallel economics
format.

## The conversation title

A new conversation earns a short auto-title on its **first turn**. "New" is a
framework-neutral signal: the conversation has **no prior recorded turn log**
(the current turn's log is written after the turn body runs). Generate, emit,
persist:

- Generate: `generate_conversation_title(...)`
  (`sdk/tools/backends/summary/conversation_title.py`).
- Emit: `emit_conversation_title_event(...)` streams `chat.conversation.title`
  through the turn's `comm`; the chat component applies it to the header live
  and only when the event's `conversation_id` matches the open conversation
  (the event is broadcast to all of the user's surfaces).
- Persist: return it on the turn result (`result["conversation_title"]`) — the
  recorder writes it onto the `conv.timeline.v1` the list reads.

**The identity contract.** The user the recorder writes under, the user the
list reads under, and the user the is-new probe reads under must be the **same**
`(user, conversation)`. Under economics, the recording user is the
projected-authority user (`state["economics_user"]`), which can differ from raw
actor keys — derive the is-new probe's user the same way the recorder does, or
a genuinely new conversation reads as "not new" and never gets a title.

**The role-binding contract.** The title generates on the responsible agent's
answer role, but it runs OUTSIDE the turn's model-pick overlay (which is scoped
to the active agent's turn). Bind that role in base `config.role_models` too, or
the title call resolves to no model and the conversation stays "Untitled".

## The agent's working memory (the part you wire)

The model remembers only what you feed it or what its store restores. KDCube
hands the turn body the **current** turn input; **prior turns come from the
agent's own memory**. For a LangGraph agent that memory is its **checkpointer**,
and three things make it durable and correct:

1. **Key `thread_id` by the platform `conversation_id`.** LangGraph restores a
   thread's messages by `thread_id`; use the conversation id (e.g.
   `f"{user_id}:{conversation_id}"`), never the session id. A session id changes
   per browser session, so keying by it opens a fresh, empty thread on reload.
2. **Make the store durable.** An in-memory saver lives only inside one process:
   its history is wiped on restart and absent for any conversation created in an
   earlier process (the "this appears to be the start of our conversation"
   symptom on a reloaded thread). A Postgres checkpointer (`AsyncPostgresSaver`)
   persists across restarts and workers — KDCube is distributed and a turn may
   land on any worker, which is also why the graph is rebuilt per turn rather
   than cached in-process.
3. **Declare the dependency, and fall back LOUD.** The durable saver needs
   `langgraph-checkpoint-postgres` (pinned compatible with the platform's
   `langgraph-checkpoint`) plus `psycopg[binary]` v3, declared in
   `requirements-chat-processor.txt` and `requirements-chat.txt`. Degrading to
   an in-memory saver is acceptable **only if the fallback logs at WARNING** — a
   silent fallback turns a missing dependency into invisible memory loss.

If you would rather the agent mirror exactly what is on screen, the alternative
is to reconstruct prior turns server-side from the conversation record each turn
and feed them into the agent's inputs — the platform record as the single source
of truth, at the cost of a reconstruction step.

## What the client sends is a hint, not the memory

The chat widget includes a `chat_history` on submit. Treat it as a convenience
signal, not the source of truth:

- it carries **user messages only** — no assistant text — so it cannot answer
  "repeat what you said";
- it is **empty on a continuation** (a follow-up or steer into an in-flight
  conversation).

Durable continuity comes from the agent's own store (or the server-side
reconstruction above), not from `chat_history`.

## Follow-up and steer

Follow-up and steer are the same conversation, so they carry the same
`conversation_id` → the same `thread_id` → the agent's store restores the prior
turns, and the same record accumulates the new turns. Getting the two memories
right — durable store keyed by conversation id, plus the platform record — is
what makes follow-up, steer, and reload all "just work" for any hosted agent.

## Checklist

- [ ] `thread_id` is keyed by the platform `conversation_id`, not the session id.
- [ ] The agent's store is durable (Postgres), with the dependency declared in the processor/chat requirements; any fallback logs at WARNING.
- [ ] The door folds the turn's ingress batch before reading inputs (attachments visible to agent AND record).
- [ ] `post_run_hook` calls `_save_events_artifact` and `_persist_stream_artifacts_fallback` (cost, time, citations, follow-ups, exec panel survive reload).
- [ ] Produced files surface on `state["hosted_files"]` / `result["files"]`, and the app serves `scene_object_action` so Download resolves.
- [ ] The is-new probe, the recorder, and the list agree on the same `(user, conversation)`.
- [ ] The first-turn title is generated, emitted (`chat.conversation.title`), returned on the turn result — and its role is bound in base `config.role_models`.
- [ ] A raising turn surfaces: `chat.error` + an error turn log (the backstop covers frameworks that don't surface their own).
- [ ] Verified live: a fresh conversation lists with a title; reloads with the user bubble, attachments, files, cost/time, and any exec panel; and — after a process restart — a follow-up still sees prior turns.

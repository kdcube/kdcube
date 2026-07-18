---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/timeline/fork-README.md
title: "Timeline Fork"
summary: "The fork primitive: seeding a new conversation with a projection copy of another conversation's working summaries and in-progress turn."
tags: ["sdk", "solutions", "timeline", "fork", "subagents"]
keywords: ["fork", "projection", "working summary", "range summary", "conv:fi:", "conversation-qualified refs", "subagent.charter"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/work-with-subagents-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/compaction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
---
# Timeline Fork

A fork seeds a NEW conversation with a projection copy of an existing one:
the source conversation's working summaries plus its in-progress turn become
the new conversation's pre-existing history. The copy is by value -- two
timelines share no state afterward; each persists and compacts on its own.
The primary consumer is the subagent spawn
([work-with-subagents-README.md](../../agents/react/work-with-subagents-README.md)),
and the primitive itself is agnostic of who reads the fork.

Code: `kdcube_ai_app/apps/chat/sdk/solutions/react/subagents/fork.py`
(`build_fork_projection`, `qualify_file_refs`, `build_fork_marker_block`).

## The Projection

`build_fork_projection` assembles the seed blocks in this order:

1. The source conversation's latest `conv.range.summary` (when it has
   compacted). It comes FIRST because the timeline persist window starts at
   the newest range summary -- a block placed before it would be sliced away
   on the first persist.
2. A fork header block (`subagent.fork.header`) naming the source
   conversation and turn, and stating the ref rules below in model-facing
   words.
3. Every `conv.working.summary` block of the source conversation, deduped
   by path, in original order -- exactly the durable per-turn digests the
   compaction machinery keeps as blocks.
4. The source's current-turn blocks, verbatim: the prompt, tool calls and
   results, notes, attachments -- whatever the source agent could see of its
   in-progress turn.

The seed is persisted as the new conversation's timeline artifact
(`conv.timeline.v1`) BEFORE the new conversation's first turn, so the
ordinary `load_timeline` path finds it as prior history, sets the
current-turn offset after it, and appends the new turn header -- no special
load mode exists. The persisted timeline carries a queryable
`forked_from: {conversation_id, turn_id}` backref (also stamped as a
`forked_from:<conversation id>` index tag), and the source turn's stored
record carries the matching `forks` descriptors -- both directions of the
fork relationship live in the store, where conversation fetch reads them.

## Client Reconstruction: The Fetch Contract

`POST /{tenant}/{project}/{conversation_id}/fetch` surfaces both directions
of the stored relationship, so a client rebuilds fork threads on reload:

- **Source side:** each turn that forked carries
  `forks: [{child_conversation_id, charter_goal, agent_title, forked_at}]` on
  its turn entry in the fetch response.
- **Forked side:** the forked conversation's fetch response carries a
  top-level `forked_from: {conversation_id, turn_id}`.
- **One endpoint:** the forked conversation belongs to the same
  tenant/project/user and the same app, so the client fetches it through
  the SAME endpoint with the same auth and inlines it as the thread
  anchored at the fork turn.
- **Lists:** conversation list rows (`GET /{tenant}/{project}`) carry the
  same `forked_from` marker on forked conversations, so clients group them
  under their source conversation.

Reload and live streaming agree because both render the same source: the
forked conversation's own turns, anchored by the fork refs (live threading
is the subagent visibility contract in
[work-with-subagents-README.md](../../agents/react/work-with-subagents-README.md)).

## Ref Semantics

Every conversation-scoped ref the runtime emits is conversation-qualified at
birth (`conv:fi:conv_<id>.turn_x.files/a.md`, `conv:ar:conv_<id>.turn_x...`),
so refs are absolute identities and a fork copy needs no re-interpretation.
The copy step applies one idempotent rewrite, `qualify_file_refs`: refs in the
block's `path`, `refs`, and `meta.path` fields AND refs mentioned inside the
block's text gain the SOURCE conversation's scope segment when they lack one
(blocks persisted before qualification-at-birth). Refs that already carry a
segment — the source's own, or an earlier fork's — are untouched, so
provenance survives chained forks.

Resolution in the child:

- `conv:fi:` refs name workspace files in the source conversation; that is
  the standard cross-conversation form `react.pull` resolves from the child.
- `conv:ar:` / `conv:tc:` / `conv:ws:` / `conv:su:` refs are
  timeline-resident: the qualified path resolves within the timeline that
  holds the block — here, the child's own copy. The segment records where the
  block came from; cross-conversation resolution for these kinds is a
  possible follow-up, not a current capability.

## The Charter As First Event

The fork carries context; the assignment arrives separately, as the new
conversation's first authored event. At delegate time the spawner publishes
it onto the new conversation's own event lane (transport kind
`external_event`, semantic type `subagent.charter` nested in
`payload.event.type`, author `agent:conv_<source id>/<source turn>`,
targeted at the new conversation's first turn) WITH a task payload — the
charter's promotion is what schedules and runs that first turn. When the
promoted turn runs, the ordinary timeline load finds the seed as prior
history and the external-event fold materializes the charter inside the
turn. Keeping charter and fork separate preserves the reading order the
child needs -- history first, task last -- and gives the charter the full
event-lane provenance (sequence, author, timestamp) instead of being one
more copied block.

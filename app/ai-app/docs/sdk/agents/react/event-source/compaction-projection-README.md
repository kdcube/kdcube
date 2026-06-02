---
id: ks:docs/sdk/agents/react/event-source/compaction-projection-README.md
title: "Compaction Projection Phase"
summary: "ReAct event-source phase for preparing selected timeline blocks for summarization, preservation, and recovery."
tags: ["sdk", "agents", "react", "event-source", "compaction"]
keywords: ["compaction_projection", "conv.range.summary", "preservation", "recovery refs", "snapshot refs"]
see_also:
  - ks:docs/sdk/agents/react/compaction-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
---
# Compaction Projection Phase

`compaction_projection` runs on already-produced blocks selected for
compaction/summarization. Policies can decide how a source should be exposed to
the summarizer, which refs must survive, and which block families should be
hidden or replaced before compaction text is built.

The target is a mutable block list. Policies mutate blocks inline.

## Current Policies

| Policy ID | Behavior |
|---|---|
| `react.compaction_projection.identity` | Leaves blocks unchanged. |
| `react.compaction_projection.hide_by_segment` | Same implementation as timeline hide-by-segment, applied in the compaction phase. |

## Open Work

Some preservation logic is still hardcoded, especially followup/steer and
tool-round preservation. The desired direction is to move that behavior into
event-source policies so custom event sources can define their own compaction
shape without editing ReAct core.

Compaction policies should preserve durable recovery anchors. For story
snapshots and cross-conversation recovery, leave explicit refs such as
`fi:<turn>.snapshots/...` or `fi:conv_<conversation_id>.<turn>.snapshots/...`
visible enough for later `react.read`, `react.pull`, or `react.checkout`.


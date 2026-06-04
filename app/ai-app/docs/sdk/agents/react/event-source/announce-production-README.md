---
id: ks:docs/sdk/agents/react/event-source/announce-production-README.md
title: "Announce Production Phase"
summary: "ReAct event-source phase for producing non-durable ANNOUNCE tail material from event sources and story materializers."
tags: ["sdk", "agents", "react", "event-source", "announce"]
keywords: ["announce_production", "ANNOUNCE", "tail material", "snapshot materialization", "story state"]
see_also:
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/block-production-README.md
---
# Announce Production Phase

`announce_production` is the phase for non-durable ReAct tail material. During
render, ReAct asks registered event sources for ANNOUNCE blocks and appends the
result after cache markers.

ANNOUNCE is not timeline history. It is computed for the current decision render
and appended after cache markers. If ANNOUNCE needs current story state, the
harness should call a registered source/materializer that reads authoritative
state and returns or writes a snapshot artifact/ref.

## Target Shape

The target is a mutable list of ANNOUNCE tail blocks. Policies receive
access to the full timeline through context because a source may decide whether
to announce based on recency, story focus, last occurrence, or snapshot state.

## Snapshot Rule

Snapshots are not reconstructed by replaying the timeline. Timeline blocks are
event history and refs. Snapshot materialization is a source/tool responsibility
and should write a ref such as:

```text
fi:<turn_id>.snapshots/<name>
fi:conv_<conversation_id>.<turn_id>.snapshots/<name>
```

The `conv_...` segment means the referenced artifact belongs to another
conversation. Current-conversation refs normally omit that segment.

## Runtime Contract

- ANNOUNCE production runs from `Timeline._append_tail_blocks()` when
  `include_announce=True`.
- The policy target is not persisted. It is a render-tail block list.
- The policy receives a cloned timeline context. It must treat that context as
  read-only and append only ANNOUNCE blocks to the target.
- Cache markers are selected before the tail is appended, so ANNOUNCE blocks are
  non-cached.
- The render token probe uses the same ANNOUNCE production path as the final
  render, so large ANNOUNCE blocks can still trigger compaction.

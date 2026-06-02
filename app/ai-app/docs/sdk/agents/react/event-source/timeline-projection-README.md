---
id: ks:docs/sdk/agents/react/event-source/timeline-projection-README.md
title: "Timeline Projection Phase"
summary: "ReAct event-source phase for mutating produced timeline blocks before visible render and cache marker assignment."
tags: ["sdk", "agents", "react", "event-source", "timeline-projection"]
keywords: ["timeline_projection", "hidden", "replacement_text", "cache markers", "timeline segment"]
see_also:
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
---
# Timeline Projection Phase

`timeline_projection` runs on already-produced timeline blocks before they are
rendered into model-visible messages and before cache markers are assigned.

The target is the mutable timeline block list. Policies may hide, replace, or
annotate blocks inline. They must not create a new durable timeline history.

## Segment Marks

For cold-cache and TTL-pruning paths, the caller temporarily annotates blocks:

```text
meta._react_timeline_segment = current | intact_recent | recent | old | compacted
```

Policies can use that mark to decide how much to expose. The caller removes the
temporary mark after policies run. Other policy changes, such as `hidden` or
`replacement_text`, remain.

## Current Policies

| Policy ID | Behavior |
|---|---|
| `react.timeline_projection.identity` | Leaves blocks unchanged. |
| `react.timeline_projection.hide_by_segment` | Hides matching event-source blocks when their temporary segment is one of the configured segments and can write replacement text. |

## Cache Rule

Timeline projection must complete before cache marker assignment. ANNOUNCE and
sources tail material is appended after cache markers, so projection policies
should not depend on tail blocks being present.


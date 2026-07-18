---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/timeline-projection-README.md
title: "Timeline Projection Phase"
summary: "ReAct event-source phase for mutating produced timeline blocks before visible render and cache marker assignment."
tags: ["sdk", "agents", "react", "event-source", "timeline-projection"]
keywords: ["timeline_projection", "hidden", "replacement_text", "cache markers", "timeline segment"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/provider-projection-README.md
---
# ReAct Timeline Projection Phase

`timeline_projection` runs on already-produced timeline blocks before they are
rendered into model-visible messages and before cache markers are assigned.

This phase belongs to the ReAct adapter. It shapes ReAct model input and may
use ReAct-only concepts such as cache segments, compaction, and ANNOUNCE. Do
not confuse it with the shared
[provider projection contract](../../../../runtime/harness/timeline/provider-projection-README.md),
which lets a provider project only its own blocks for any compatible agent or
client surface.

The render path passes a phase-local mutable copy of the visible timeline
blocks. Policies may hide, replace, summarize, or annotate that view inline.
Those render-time mutations do not write back to timeline storage. Durable TTL
pruning is separate and still runs through the session/cache pruning path.

This phase is after transport. It does not read Redis lanes, resolve
`ExternalEventLaneWakeup`, or decide whether an event should start a processor
task; those responsibilities belong to ingress/proc and the live lane reader.

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
| `react.timeline_projection.event_default` | Renders `event.external` JSON bodies as compact event facts instead of raw JSON. |
| `react.timeline_projection.snapshot_default` | Renders `event.snapshot` JSON bodies as compact read-only snapshot facts. |
| `react.timeline_projection.canvas_default` | Renders `event.canvas` JSON bodies as compact collaborative-board facts. |

The same structural render defaults also exist for `compaction_projection`, so
summarizers receive compact event facts rather than raw event JSON.

If an event source has no registered render policy, the SDK applies a structural
fallback for `event.external`, `event.snapshot`, and `event.canvas` blocks that
are still JSON at render time.

## Relationship To ANNOUNCE

Timeline projection mutates the prompt-visible view of persisted blocks.
ANNOUNCE production appends non-durable tail material after cache markers.

Owner policies can use both phases for the same object. For canvas,
timeline projection renders a compact `[CANVAS TOOL RESULT]` or
`[CANVAS STATE]` fact, while ANNOUNCE renders the spatial board map and legend
when the board is still inside its retention window. The projection should
include enough recovery text for the model to pull/read the object again after
ANNOUNCE expires.

## Cache Rule

Timeline projection must complete before cache marker assignment. ANNOUNCE and
sources tail material is appended after cache markers, so projection policies
should not depend on tail blocks being present.

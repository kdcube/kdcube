---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
title: "Agent Harness Timeline"
summary: "Framework-neutral timeline identity, persistence, projection, turn-log, and client-view contracts."
tags: ["runtime", "harness", "timeline", "turn-log", "projection", "conversation"]
updated_at: 2026-07-18
keywords:
  [
    "conv.timeline.v1",
    "timeline blocks",
    "turn log",
    "turn view",
    "event identity",
    "provider rendering",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/turn-view-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/provider-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/timeline-README.md
---
# Agent Harness Timeline

The harness timeline is the framework-neutral contract for ordered conversation
blocks and their persisted/client projections.

```text
accepted events and agent output
              |
              v
       ordered timeline blocks
          |             |
          v             v
 conv.timeline.v1   per-turn TurnLog
          |             |
          +------v------+
             turn view
       chat entries, files,
       artifacts, citations,
       followups, thinking
```

ReAct builds a rich live timeline around its rounds. Other agent adapters may
produce a smaller block set. Once blocks are accepted, persistence and client
reconstruction use the same harness contracts.

## Owned Contracts

| Module | Contract |
| --- | --- |
| `identity.py` | Stable event source/id fields on timeline blocks and matching helpers. |
| `payload.py` | `conv.timeline.v1`, sources-pool inclusion, turn IDs, external-event cursor, cache metadata, and fork metadata. |
| `turn_log.py` | Per-turn ordered block log, feedback/index summary, and reconstruction input. |
| `turn_view.py` | Framework-neutral projection of persisted blocks into user prompts, assistant completions, attachments, files, citations, followups, clarifications, thinking, and timeline text. |
| `projection.py` | Safe application of namespace-provider rendering patches to provider-owned blocks only. |

## Persisted Timeline Payload

The canonical payload kind is `conv.timeline.v1`:

```json
{
  "version": 1,
  "blocks": [],
  "sources_pool": [],
  "turn_ids": [],
  "conversation_title": "",
  "conversation_started_at": "",
  "last_external_event_id": "",
  "last_external_event_seq": null,
  "last_rendered_event_cursor": {},
  "agent_selection_snapshot": null,
  "forked_from": null
}
```

The timeline is conversation-level. A turn log is a per-turn slice. Neither is
the Event Bus queue, communicator stream, or workspace filesystem.

## Framework Boundary

Shared:

- block identity and ordered persistence;
- timeline payload parsing/building;
- turn-log storage shape;
- client turn-view reconstruction;
- namespace ownership checks for provider render patches.

ReAct-specific:

- decision rounds and output-channel protocol;
- ANNOUNCE, plan, compaction, cache points, and memory beacons;
- live steer/followup folding policy;
- ReAct tool-call block production and model-facing render layout.

A run-to-completion adapter may consume external events only at turn start and
still persist the resulting blocks through this timeline contract. That does
not imply ReAct's live mid-turn folding behavior.

## Canonical Reading

- [Turn Log](turn-log-README.md)
- [Turn View](turn-view-README.md)
- [Conversation Artifacts](conversation-artifacts-README.md)
- [Provider Projection](provider-projection-README.md)
- [ReAct Timeline Adapter](../../../sdk/agents/react/timeline-README.md)

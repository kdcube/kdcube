---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
title: "Agent Harness Runtime"
summary: "Framework-neutral event, timeline, and workspace contracts shared by KDCube agent adapters."
tags: ["runtime", "harness", "agents", "events", "timeline", "workspace"]
updated_at: 2026-07-18
keywords:
  [
    "agent harness",
    "agentic runtime",
    "framework adapter",
    "ReAct",
    "LangGraph",
    "timeline",
    "turn workspace",
    "event ref resolver",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/structure-README.md
---
# Agent Harness Runtime

The agent harness is the framework-neutral runtime layer between KDCube's
classical runtime services and concrete agent implementations.

```text
KDCube runtime services
  identity, storage, event lane, communicator, accounting, isolated execution
                              |
                              v
                    runtime/harness
                events | timeline | workspace
                              |
                 +------------+------------+
                 |                         |
                 v                         v
             ReAct adapter          ported-agent adapters
                                      (for example LangGraph)
```

ReAct is a consumer of this layer, not its owner. Conversation APIs, chat and
canvas integrations, and ported agents also use parts of the same contracts.
This prevents every agent framework from inventing a different ref grammar,
turn-log format, artifact layout, or file-download resolver.

## Scope Map

| Scope | Owns | Does not own |
| --- | --- | --- |
| [Events](events/README.md) | Canonical event/object-ref resolution and byte/action dispatch for shared refs. | Event Bus scheduling, framework decisions, or namespace-provider policy. |
| [Timeline](timeline/README.md) | Block identity, persisted timeline payloads, turn logs, client turn views, and provider-owned render projection. | ReAct rounds, prompt compaction, model protocol, or strategic governance. |
| [Workspace](workspace/README.md) | Conversation ref grammar, distributed turn paths, artifact records, change detection, and byte materialization. | A framework's model-facing tool names or decision policy. |

The scopes deliberately remain separate. A file can exist in the workspace
without being a timeline artifact. A timeline block can reference an object
whose bytes are resolved by the events scope. The umbrella package therefore
does not flatten all APIs into one namespace.

## Shared Contract Versus Adapter Policy

The harness defines stable data and runtime boundaries:

- conversation-owned refs use `conv:<family>:` and carry
  `conv_<conversation_id>` as the owner segment inside the ref body;
- persisted timelines and turn logs are ordered block streams;
- a client turn view is reconstructed from persisted blocks rather than from
  framework-private in-memory state;
- workspace paths are `OUTPUT_DIR`-relative and use `git/projects/`,
  `files/`, `git/snapshots/`, `attachments/`, and `external/` by meaning;
- ref strings are locators, while tenant/project/user/authority come from the
  trusted runtime context;
- byte resolution and materialization happen in trusted runtime code.

An adapter decides how to use those contracts:

| Adapter concern | Example |
| --- | --- |
| Model protocol | ReAct channels and actions; a ported graph's own state transitions. |
| Scheduling semantics | ReAct can fold eligible events during a live turn; a run-to-completion graph may consume a batch only at turn start. |
| Model tools | `react.pull`, `react.read`, and `react.checkout` are ReAct-facing tools over shared workspace/resolver primitives. |
| Prompt projection | ReAct ANNOUNCE, compaction, context cache, and round layout remain ReAct-owned. |
| Tool/result rendering | The common timeline projection validates provider-owned patches; each adapter decides when to invoke it and how to expose the result. |

Do not document a framework policy as a harness guarantee merely because its
implementation imports a harness helper.

## Current Consumers

| Consumer | Shared harness use |
| --- | --- |
| ReAct v2/v3 | All three scopes, plus ReAct-owned rounds, tools, prompts, cache, and online governance. |
| Ported LangGraph example | Turn workspace materialization, canonical file refs, byte resolution, and the common code-exec artifact layout. |
| Conversation solution | Timeline payload parsing, turn-log persistence, turn-view reconstruction, and canonical ref presentation. |
| Chat/canvas surfaces | Canonical `conv:fi:` refs and generic object download/action resolution. |
| Namespace providers | Owner refs can be materialized through registered rehosters without teaching the harness each provider's storage layout. |

## Implementation Map

```text
kdcube_ai_app/apps/chat/sdk/runtime/harness/
  events/
    resolver.py
  timeline/
    identity.py
    payload.py
    projection.py
    turn_log.py
    turn_view.py
  workspace/
    artifacts.py
    layout.py
    pull.py
    references.py
```

Framework code belongs outside this tree. ReAct-specific implementations live
under `sdk/solutions/react`; the worked ported-agent adapter lives under the
`ported-langgraph-agents@2026-07-13` example app.

## Reading Order

1. [Events](events/README.md) for ref resolution and ownership.
2. [Timeline](timeline/README.md) for persisted block and client-view contracts.
3. [Workspace](workspace/README.md) for logical refs, paths, and materialization.
4. [ReAct Structure](../../sdk/agents/react/structure-README.md) only when the
   concrete ReAct adapter is relevant.

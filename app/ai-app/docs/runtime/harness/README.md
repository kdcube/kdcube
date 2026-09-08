---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
title: "Agent Harness Runtime"
summary: "Keep user conversations, files, isolated workspaces, and execution evidence stable while Native, LangGraph, Claude Code, or custom agent implementations change around them."
tags: ["runtime", "harness", "agents", "events", "timeline", "workspace", "conversation-search"]
updated_at: 2026-09-08
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
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/structure-README.md
  - repo:kdcube-ai-app/agents/README.md
---
# Agent Harness Runtime

The agent harness lets different agent implementations use the same durable
conversation, file, workspace, event, and accounting contracts. An agent can
be replaced without making its users lose history or making every adapter
invent another artifact format and security boundary.

This layer exists to separate a useful agent product from one particular model
loop. Teams can change an agent implementation while users keep their history
and files, tools keep the same enforcement boundary, and operators keep one
account of what ran and what it cost.

The included **KDCube Native ReAct agent** is called the **Native agent** below.

```text
KDCube runtime services
  identity, storage, event lane, communicator, accounting, isolated code execution
                              |
                              v
                    runtime/harness
                events | timeline | workspace
                              |
                 +------------+------------+
                 |                         |
                 v                         v
  Native agent adapter                 ported-agent adapters
                                      (for example LangGraph)
```

The Native agent is a consumer of this layer, not its owner. Conversation
APIs, chat and canvas integrations, and
ported agents also use parts of the same contracts. This prevents every agent
framework from inventing a different ref grammar, turn-log format, artifact
layout, or file-download resolver.

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
| Scheduling semantics | ReAct can fold eligible events during a live turn; a foreign runtime folds one read-only whole-pending snapshot at turn start and may watch control afterward. |
| Model tools | `react.pull`, LangGraph `pull_files`, and foreign-runtime local MCP `pull` are adapter bindings over the same materializer; checkout follows the same pattern. |
| Prompt projection | ReAct ANNOUNCE, compaction, context cache, and round layout remain ReAct-owned. |
| Tool/result rendering | The common timeline projection validates provider-owned patches; each adapter decides when to invoke it and how to expose the result. |

Do not document a framework policy as a harness guarantee merely because its
implementation imports a harness helper.

## Current Consumers

| Consumer | Shared harness use |
| --- | --- |
| Native agent v2/v3 | All three scopes, plus ReAct-owned rounds, tools, prompts, cache, and online governance. |
| Ported LangGraph example | Current-event framing, shared pull and checkout, canonical file refs, and the common code-exec artifact layout. |
| Foreign-runtime wrappers | Shared pull/checkout can be exposed through local MCP; a trusted wrapper may also bind explicit conversation-file publication. `harness-claude-demo@1-0` is the focused public implementation. |
| Conversation solution | Timeline payload parsing, turn-log persistence, turn-view reconstruction, and canonical ref presentation. |
| Direct Native agent, LangGraph, and Claude examples | Accounted durable turns plus user-scoped search of current and earlier conversations; framework tool names call one conversation search engine. |
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
    checkout.py
    context_resolver.py
    hosting.py
    layout.py
    materialization.py
    pull.py
    references.py
```

Framework code belongs outside this tree. ReAct-specific implementations live
under `sdk/solutions/react`; the worked ported-agent adapter lives under the
`ported-langgraph-agents@2026-07-13` example app.

## Direct SDK host

The repository's [`agents/`](../../../../../agents/README.md) directory contains
three direct SDK programs for the Native agent, a LangGraph agent,
and Claude Code. Each
uses `DirectAgentHarness` with explicit identity, Redis, Postgres, and storage
configuration. The facade binds one accounted turn, writes the common KDCube
conversation record, and verifies that each Postgres row points to a
materializable storage payload. The concrete adapter retains ownership of its
loop and private continuation. Reusable direct-host descriptor, model,
configuration, setup, and console-evidence helpers live under
`sdk/runtime/direct_hosting`; they are SDK APIs rather than a fourth example.

Redis holds the short-lived per-turn accounting mirror. All three use the
KDCube conversation tables in Postgres and durable payload storage. LangGraph
additionally uses its own Postgres checkpoint tables for graph continuation.
The examples run the same web-research and PDF/XLSX task, then verify that the
same user can recover it from another conversation. They expose real
`ChatCommunicator` events in the terminal. Their shared direct-channel
contract also supports a continuing terminal session and a process-local
Telegram webhook. Telegram update parsing, attachment hydration, and final
turn-log delivery reuse the integration SDK; each concrete adapter supplies
the callback that runs one durable turn.

The copy-and-run contract, including YAML-selected tools and skills and the
optional isolated-execution image, is
[Run The Agent Harness From Python](../../recipes/quickstart/run-agent-harness-from-python-README.md).
The model-facing profile, enabled-capability teaching, skill delivery, and
administrator customization order are owned by
[Direct Agent Instruction Profiles](direct-agent-instruction-profiles-README.md).

## Remaining Source Extraction

The contract boundary is established, but the source extraction is not
complete. Common runtime and non-ReAct callers still import several mixed
modules under `sdk/solutions/react`. Do not move those files wholesale: extract
the shared primitive and leave the ReAct policy in the ReAct adapter.
ReAct-specific terms in this inventory are described in
[ReAct Structure](../../sdk/agents/react/structure-README.md) and
[Round Generation Feedback](../../sdk/agents/react/round-generation-feedback-README.md).

| Current ReAct location | Shared part to extract | Part that stays ReAct-specific |
| --- | --- | --- |
| `solution_workspace.py` | Conversation file hosting, timeline rehosting, and execution-snapshot workspace assembly into `runtime/harness/workspace/`. | ReAct-facing read/pull behavior and ReAct timeline integration. |
| `workspace.py` | Remaining generic code-path discovery and logical/physical path hydration into `runtime/harness/workspace/`; shared source resolution and checkout are already extracted. | ReAct tool semantics and ANNOUNCE, the uncached per-round runtime-state block shown to the model. |
| `artifacts.py` and `artifact_analysis.py` | Generic artifact metadata, error normalization, summary preparation, and file materialization. | `ReactArtifactView`, ReAct's artifact presentation model, and ReAct tool-result block layout. |
| `timeline.py` | Artifact lookup, source-pool selectors that choose citation/source blocks, and other block-only readers into `runtime/harness/timeline/`. | The live ReAct `Timeline`, plans, context compaction, memory-reminder blocks, and ANNOUNCE layout. |
| `events/policies/`, `events/exploration.py`, and generic parts of `events/artifact_production.py` | Provider/tool event-source policy, which decides how source output enters the record, and safe block projection into the common event/timeline subsystem. | Native agent `react.*` event ids, live-listener ownership, bounded extra-round credit for folded events, and ReAct round folding. |
| `proto.py` | A small framework-neutral execution/runtime context under `sdk/runtime/`. | `ReactResult`, `ReactStateSnapshot`, ReAct cache/session policy, and subagent state. |
| `decision_prompt.py` | The wrapper that inserts administrator-owned instructions at their required priority into shared instruction helpers. | ReAct decision protocol and prompt composition. |

Two dependencies need decoupling rather than relocation:

- conversation search should use its common search backend without importing
  the full ReAct `ContextBrowser`;
- generic canvas/file/rendering helpers should use common content, artifact,
  and event-policy utilities instead of importing `react.tools.common` or
  `react.events`.

One worked-adapter activation gap is separate from source extraction:
`ported-langgraph-agents@2026-07-13/platform/code_exec.py` currently creates
its isolated-execution tool subsystem with empty `raw_tool_specs` and
`mcp_tool_specs`. Its `run_python` therefore demonstrates isolated computation
and conversation file hosting, not nested KDCube/MCP tool calls. Enabling the
shared supervisor tool bridge requires the adapter to export its narrowed,
execution-enabled tool specs. The LangGraph-specific wrapper stays in the app;
the runtime tool bridge stays under `sdk/runtime` and `sdk/tools`.

New common runtime, conversation, tool, canvas, or ported-agent code should not
add another `sdk.solutions.react` import. Extract or call the corresponding
harness/runtime primitive instead.

## Reading Order

1. [Events](events/README.md) for ref resolution and ownership.
2. [Timeline](timeline/README.md) for persisted block and client-view contracts.
3. [Workspace](workspace/README.md) for logical refs, paths, and materialization.
4. [ReAct Structure](../../sdk/agents/react/structure-README.md) only when the
   concrete ReAct adapter is relevant.

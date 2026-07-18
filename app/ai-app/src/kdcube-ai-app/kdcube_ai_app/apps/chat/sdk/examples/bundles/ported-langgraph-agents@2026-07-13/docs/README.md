---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/README.md
title: "Ported LangGraph Agents Design"
summary: "Design overview of ported-langgraph-agents@2026-07-13: ONE KDCube app hosting TWO preserved LangGraph agents dispatched by agent_id through a single execute_core, with per-agent build/input/role/stream specs, model pickers, and scoped shared storage."
status: active
tags: ["app", "ported-langgraph-agents", "langgraph", "platform", "port", "chat", "multi-agent", "design"]
---

# Ported LangGraph Agents Design

`ported-langgraph-agents@2026-07-13` is the **"one app, many agents"** worked
instance of the KDCube port recipe. It hosts **two** independently maintainable
LangGraph solution packages behind a **single `execute_core`** that dispatches on
**`agent_id`**:

- `lg-solution` — the rich research graph (KB retrieval + per-user pgvector memory
  + a nested subagent). Linear shape with a **dedicated answer node**.
- `lg-react` — a `langchain.agents.create_agent` ReAct agent (plain tools + MCP + a
  code-exec tool; `SummarizationMiddleware` for context). A ReAct loop with a
  looping **`model` node** and no dedicated answer node.

The most visible shape-specific seam is streaming, but each `AgentSpec` owns the
agent's build function, input mapper, model role, and stream adapter. Identity,
storage, economics, capabilities, and the conversation record are shared glue.

## Owners

```text
this app owns
  - the dispatcher                 (execute_core resolves agent_id -> the right graph)
  - the per-turn graph binding     (current model/tool choices captured each turn)
  - the two stream adapters        (dedicated-answer-node + looping-model-node)
  - the multi-tenant + multi-agent identity gate
  - the storage edge               (ONE shared schema + agent_id column on pg_pool)
  - the two scene chat widgets + scene_object_action (file download)

this app consumes (does not own)
  - the KDCube chat surface        (the reactive event that starts a turn)
  - the platform conversation record (framework-neutral turn log; the app writes none)
  - comm_ctx streaming primitives  (step / delta / complete)
  - the reusable chat component + the reusable Telegram SDK

this app does NOT own
  - the chat UI, the KDCube conversation store, any KDCube-managed durable state,
    Telegram protocol mechanics
```

## The dispatcher (the core move)

```text
1. resolve     entrypoint.py     agent_id = normalize(state.agent_id) or default;
                                 spec = AGENTS[agent_id]  (unknown -> default)
2. graph       entrypoint.py     await _build_graph(agent_id, disabled_tools=...):
                                 bound this turn; reuses the opened checkpointer
3. inputs      spec.build_inputs the agent's own input shape + run_config(thread_id)
4. stream      spec.stream       the agent's OWN adapter -> comm_ctx.step/delta/complete
5. model pick  capabilities.py   resolve_turn_role_models(self, state, agent_id) for
                                 the ACTIVE agent, bound onto role_models for the turn
```

The `AGENTS` registry is a dict of `AgentSpec(agent_id, role, build_graph, stream,
build_inputs)`. Adding an agent is adding a spec — `execute_core` never branches on
agent_id.

## The two stream adapters (the shape difference)

- `platform/stream_solution.py` — lg-solution has a **dedicated answer node**, so
  the adapter streams that node's model tokens as the answer and surfaces the other
  nodes (retrieve/plan/delegate) as steps.
- `platform/stream_prebuilt.py` — lg-react's **`model` node LOOPS** (once per
  tool-decision cycle) with no answer node, so the adapter streams ONLY the final
  model turn (visible content + no tool-call chunk) and surfaces each `tools` run
  as a step.

A different framework or agent shape supplies a new `AgentSpec`: build function,
input mapping, model role, and stream adapter. The dispatcher, identity mapper,
storage boundary, economics, and conversation integration remain shared.

## Statelessness

Each graph captures current model/tool choices, so `_build_graph` creates a fresh
bound graph every turn. An immutable compile artifact could be cached per worker in
another design, but never as conversation continuity. Only the checkpointer
CONNECTION is opened once per agent and reused. Per-turn state lives only in shared
Postgres keyed by `thread_id`; nothing per-turn lives on `self`. So any processor
worker can serve any turn for either agent (regression-tested:
`tests/test_storage_pg_target.py`, `tests/test_dispatch.py`).

## The persistence split (per agent)

Each agent keeps its **own** store — routed onto KDCube's shared Postgres into ONE
per-tenant/project schema (`kdcube_{tenant}_{project}`) with bundle-prefixed tables
(`ported_langgraph_agents_memories`/`_kb`), the two agents separated by the
**`agent_id` column** (a per-agent schema would pollute Postgres); its own
`DATABASE_URL` when standalone, an in-memory saver when unreachable. KDCube
separately owns the **conversation record** (framework-neutral turn log + comm events
materialized on reload, the same for either agent). Full ownership matrix
+ the injection point: [storage/README.md](storage/README.md).

## Dependency direction

```text
entrypoint.py ─imports→ solution/lg_solution/, solution/lg_prebuilt/, platform/
solution/*    ─imports→ nothing in KDCube   (never depends back on the platform)
platform/*    ─ depend only on comm_ctx / platform state / SDK adapters
```

Neither solution package imports KDCube. Only the platform glue and `entrypoint.py`
know both worlds, and they import `solution/` package-relative.

## Model selection (Capabilities widget), per agent

Each agent declares the generic `simple_model_pick` provider under
`surfaces.as_consumer.agents.<agent>`, naming its own answer role and model list.
The platform serves the `agent_capabilities` / `agent_selection_update` ops
(inherited from `BaseEntrypoint`). How a pick is APPLIED is the one
framework-specific piece: `entrypoint.py` (via `platform/capabilities.py`) resolves
the saved pick for the **active** agent and binds it onto
`bundle_call_context.role_models` around that agent's graph run, so the router
overlays it on `get_client("<agent>.answer")` for that turn only. A pick for one
agent never cross-applies to the other. The selection record is platform-owned
(`UserAgentSelectionStore`, keyed per user/bundle/agent/conversation); the app only
reads through it.

## Dependencies

The union of both agents' dependency sets is pinned in
[../requirements.txt](../requirements.txt) (langgraph, langgraph-checkpoint-postgres,
langchain-core, langchain-openai, optional langchain-anthropic, psycopg v3,
pgvector, pydantic). Most already ship with the chat processor; the deltas a full
deploy adds are the Postgres checkpointer and psycopg v3 — supply them either on
the processor requirements or via the `@venv` contract. Neither agent hard-requires
more than langgraph to run: both degrade cleanly without the extras (in-memory
saver) and without a provider key (offline stub).

## Links

- App overview: [../README.md](../README.md)
- Architecture: [arch/README.md](arch/README.md)
- Human interface contract: [../interface/README.md](../interface/README.md)
- Machine contract: [../interface/ported-langgraph-agents.openapi.yaml](../interface/ported-langgraph-agents.openapi.yaml)
- Storage map: [storage/README.md](storage/README.md)
- Package change history: [journal/README.md](journal/README.md)
- Config template: [../config/bundles.template.yaml](../config/bundles.template.yaml)
- Secrets template: [../config/bundles.secrets.template.yaml](../config/bundles.secrets.template.yaml)

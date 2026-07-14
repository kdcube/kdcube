---
id: ported-langgraph-agents@2026-07-13/docs/arch
title: "Ported LangGraph Agents — Architecture"
summary: "ASCII architecture of the ported-langgraph-agents app: ONE execute_core dispatching on agent_id to TWO vendored LangGraph agents, each with its own stream adapter + storage schema; the surfaces, integrations, request/data flow, and the folder-to-responsibility map."
status: active
tags: [arch, ported-langgraph-agents, langgraph, multi-agent, diagram]
---
# Ported LangGraph Agents — Architecture

ONE KDCube app hosting TWO ported LangGraph agents (vendored under `solution/`),
dispatched by `agent_id` through a single `execute_core`. This page maps the whole
app: its **dispatch**, its **surfaces**, its **integrations**, the
**request/data flow**, and **where's what**.

---

## 1. Where the app sits in KDCube

```text
                            ┌──────────────────────── KDCube platform ────────────────────────┐
  client / channel          │                                                                 │
  ───────────────           │   ingress ──▶ processor (orchestrator) ──▶ BaseEntrypoint.run() │
  chat component  ──────────┼──▶ /sse/chat        routes a turn;            │  binds accounting │
  telegram        ────────────▶ webhook           never touches turn logs   │  + comm context   │
                            │      (public @api, Telegram SDK)              ▼                   │
                            │                                        execute_core(state, ...)   │
                            │                              DISPATCH on agent_id ─▶ one of two   │
                            │                              vendored LangGraph agents runs here  │
                            │   live stream  ◀── comm_ctx.delta/step/complete ◀── stream adapter │
                            └─────────────────────────────────────────────────────────────────┘
```

The **processor is only an orchestrator**. Everything app-specific happens from
`execute_core` inward — including the agent_id dispatch.

---

## 2. The dispatch (the core of this app)

```text
 execute_core(state, thread_id, params)
   agent_id = normalize(state["agent_id"]) or DEFAULT_AGENT_ID   (unknown -> default)
   spec     = AGENTS[agent_id]

   AGENTS = {
     "lg-solution": AgentSpec(role="lg-solution.answer",
                              build_graph=_build_solution_graph,
                              stream=_stream_solution,   ← dedicated-answer-node adapter
                              build_inputs=_solution_inputs),
     "lg-react": AgentSpec(role="lg-react.answer",
                              build_graph=_build_prebuilt_graph,
                              stream=_stream_prebuilt,   ← looping model-node adapter
                              build_inputs=_prebuilt_inputs),
   }

   graph = _build_graph(agent_id, disabled_tools)   REBUILT this turn (no cache; reuses the checkpointer)
   inputs, run_config = spec.build_inputs(question, turn_identity(state, agent_id))
   role_models = resolve_turn_role_models(self, state, agent_id)   (active agent's pick)
   answer = spec.stream(graph, inputs, run_config)   → comm_ctx.step/delta/complete
   state["final_answer"] = answer
```

Teaching point: **different agent shapes → different stream adapters, selected by
`agent_id`.** Adding an agent is adding a spec; `execute_core` never branches.

---

## 3. Surfaces (what can drive a turn)

```text
 surface                         config / decorator                       status
 ─────────────────────────────   ──────────────────────────────────────   ────────
 reactive chat turn              surfaces.as_provider.bundle.default_chat   [now]
   user message + agent_id -> execute_core -> the chosen agent's streamed answer
 telegram webhook                @api(route="public") + Telegram SDK        [now]
   POST /public/telegram_webhook; Telegram message -> execute_core (DEFAULT agent)
   -> answer delivered back over the Bot API
```

Both ingresses drive the **same** `execute_core`. The webhook drives the default
agent (`lg-solution`); the reactive chat turn dispatches on whatever `agent_id` the
turn carries. Plus two scene chat widgets + `scene_object_action` (file download);
no MCP server, Data Bus handler, cron, or background job. The
OpenAPI `paths` map carries the one HTTP path (the webhook); both surfaces are also
under `x-kdcube-surfaces`.

---

## 4. Integrations (what the app talks to)

```text
 the app  ─────────────────────────────────────────────────────────────────────

   ├─▶ KDCube model service (ModelServiceBase)              [now]  ACCOUNTED
   │     lg-solution: routed via sdk/frameworks/langchain (chat + guarded embeddings)
   │     lg-react: KDCubeChatModel bound to lg-react.answer (offline -> stub)
   │
   ├─▶ economic enforcement                                 [now]  ENFORCED
   │     BaseEntrypointWithEconomics — turn-level budget preflight + rate limiter
   │     wrap every turn (either agent); lg-solution's retrieval/memory embeddings
   │     additionally route through the economics-guarded search facade per embed.
   │
   ├─▶ KDCube conversation record                           [now]  PLATFORM-OWNED
   │     run() records a minimal turn log (framework-neutral, same for either agent)
   │
   ├─▶ Capabilities model picker (PER AGENT)                [now]  PLATFORM-OWNED
   │     each agent declares simple_model_pick on its own role; the app applies the
   │     ACTIVE agent's pick per turn via bundle_call_context.role_models
   │
   ├─▶ lg-react tools seam (plain | mcp | both)          [now]
   │     plain vendored LangChain tools and/or KDCube-served MCP tools (tools_mcp.py)
   │
   ├─▶ the chat component + the Telegram SDK (reusable)     [now]
   │
   └─▶ each agent's OWN store, on KDCube's SHARED Postgres  [now]  pg_pool
         ONE schema kdcube_{tenant}_{project}; tables ported_langgraph_agents_memories/_kb
         agents separated by the agent_id COLUMN (no per-agent schema)
         lg-solution: per-user memory + KB + checkpointer; lg-react: checkpointer
```

**Storage before → after** (full table: [../storage/README.md](../storage/README.md)):

```text
 data kind                        local (before)        hosted (KDCube backend)
 ──────────────────────────────   ───────────────────   ─────────────────────────────
 lg-solution memory + KB          own PG                pg_pool, tables ..._memories/_kb, agent_id col
 lg-solution checkpointer         own PG                pg_pool, kdcube_{tenant}_{project}
 lg-react checkpointer            own PG                pg_pool, kdcube_{tenant}_{project}
 conversation record              (none)                platform conversation record
```

The ONLY selection is the injection point — no runtime toggle: `pg_pool` present →
KDCube shared Postgres + `schema_for_scope()` (HOSTED); absent → the agent's own
`DATABASE_URL` (LOCAL/poc); unreachable → empty recall + a MemorySaver (OFFLINE).
`pg_pool` is asyncpg while the agents use psycopg v3, so `platform/pg_target.py`
derives a psycopg DSN from the same `get_settings()` PG* fields the pool is built
from. The `agent_id` COLUMN + the identity gate's `agent_id` fold are what keep the
two agents' state from ever mixing — one shared schema, never one per agent.

---

## 5. Where's what (folder → responsibility)

```text
 ported-langgraph-agents@2026-07-13/
   entrypoint.py         composition root: LGPortedAgentsBundle(BaseEntrypointWithEconomics);
                         execute_core DISPATCHES on agent_id over the AGENTS registry;
                         REBUILDS the graph per turn; telegram_webhook + scene widgets
                         + scene_object_action @api
   platform/             the shared KDCube integration
     identity.py           platform identity + agent_id -> each agent's per-user keys
     pg_target.py          storage edge -> pg_pool, ONE shared schema + agent_id column
     stream_solution.py    dedicated-answer-node astream_events(v2) -> comm_ctx
     stream_prebuilt.py    looping model-node astream_events(v2) -> comm_ctx
     capabilities.py       per-turn, per-agent model pick (role_models overlay)
     tool_pick.py          lg-react's tool inventory (admin ∩ user-enabled)
     tools_mcp.py          lg-react's MCP tools seam
     code_exec.py          lg-react's code-execution tool (files hosted to the conversation)
     telegram.py           the shared ingress (thin wiring over the Telegram SDK)
   solution/
     lg_solution/          the research graph (vendored; retrieve->plan->[delegate]->answer)
     lg_prebuilt/          the create_agent ReAct agent (vendored; model<->tools loop)
   config/               bundles.template.yaml + bundles.secrets.template.yaml
   interface/            interface README + OpenAPI (x-kdcube-surfaces)
   docs/                 README (design), arch (this), storage, journal
   tests/                offline contract + dispatch tests

 shared SDK building block (reused, not owned here):
   kdcube_ai_app/apps/chat/sdk/frameworks/langchain/   KDCube <-> LangChain bridge
```

---

## 6. One turn, end to end

```text
 user message (+ agent_id)
   │
   ▼
 run()  economics preflight (turn-level): budget + rate-limit ENFORCED
   ▼
 execute_core(state, thread_id, params)
   │  agent_id -> spec = AGENTS[agent_id]  (unknown -> default lg-solution)
   │  external_events_text(state) -> question
   │  turn_identity(state, agent_id) -> user_id (t:p:AGENT:user), thread_id
   ▼
 spec.stream( _build_graph(agent_id, disabled_tools).astream_events(inputs, {thread_id}, v2) )
   lg-solution: retrieve -> plan -> [delegate] -> answer   (dedicated answer node)
   lg-react: model <-> tools (loops); final model turn is the answer
   │  every event ▶ the agent's stream adapter ▶ comm_ctx.delta/step/complete ▶ chat UI
   ▼
 state["final_answer"] = answer
   ▼
 run() records a minimal turn log -> conversation list/reload work (either agent)
```

A Telegram-originated turn is the same flow with a different front door: the webhook
(`platform/telegram.py` → the Telegram SDK) verifies the secret, resolves the
sender to `telegram_<id>`, and drives the DEFAULT agent's `execute_core`; the SDK
renders `final_answer` back over the Bot API.

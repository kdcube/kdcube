---
id: ported-langgraph-agents@2026-07-13/agents
title: "Ported LangGraph Agents Builder-Agent Onboarding"
summary: "How to change ported-langgraph-agents@2026-07-13 — ONE KDCube app hosting TWO preserved LangGraph agents dispatched by agent_id through a single execute_core — while keeping explicit solution/platform seams and preserving dispatcher, isolation, and surface contracts."
status: active
tags: ["agents", "builder", "onboarding", "ported-langgraph-agents", "langgraph", "platform", "port", "chat", "multi-agent"]
see_also:
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/README.md"
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/README.md"
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/arch/README.md"
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/storage/README.md"
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/interface/README.md"
  - "repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/config/bundles.template.yaml"
  - "ks:docs/sdk/bundle/build/how-to-write-bundle-README.md"
  - "ks:docs/sdk/solutions/chat/chat-stream-events-README.md"
---

# Ported LangGraph Agents Builder-Agent Onboarding

This is the builder-agent landing page for `ported-langgraph-agents@2026-07-13`.

The app hosts **TWO** independently maintainable LangGraph solution packages under
`solution/` behind **one** `execute_core` that dispatches on `agent_id`. The KDCube
side is a thin, **shared** platform integration; deliberate async, configuration,
model-injection, package-import, and prompt-composition seams are explicit:

```text
solution/
  lg_solution/     the research graph (retrieve -> plan -> [delegate] -> answer).
                   Preserved solution. Has a DEDICATED answer node.
  lg_prebuilt/     the langchain.agents.create_agent ReAct agent (model <-> tools loop).
                   Preserved solution. Has a LOOPING `model` node, no answer node.
platform/          the shared integration glue:
  identity.py        platform identity + agent_id -> each agent's per-user keys
  pg_target.py       storage edge -> KDCube pg_pool, ONE shared schema + agent_id column
  stream_solution.py the dedicated-answer-node astream_events(v2) loop -> comm_ctx
  stream_prebuilt.py the looping model-node astream_events(v2) loop -> comm_ctx
  capabilities.py    per-turn, per-agent model pick (role_models overlay)
  tool_pick.py       lg-react's tool inventory (admin ∩ user-enabled, per turn)
  tools_mcp.py       lg-react's MCP tools seam
  code_exec.py       lg-react's code-execution tool (files hosted into the conversation)
  telegram.py        the shared ingress (drives the DEFAULT agent)
entrypoint.py      execute_core DISPATCHES on agent_id over the AGENTS registry;
                   BUILDS the bound graph per turn; serves telegram_webhook + the two
                   scene chat widgets + scene_object_action (file download)
```

The app owns ingresses that drive the SAME turn: the reactive chat turn (which
dispatches on `agent_id`) and the Telegram Bot API webhook (which drives the
default agent). It serves two scene chat widgets (`chat_lg_solution` /
`chat_lg_react`) and the `scene_object_action` file-download operation; no MCP
server, Data Bus handler, cron, or background job.

## Read First

- [README.md](README.md)
- [docs/README.md](docs/README.md)
- [docs/arch/README.md](docs/arch/README.md)
- [docs/storage/README.md](docs/storage/README.md)
- [docs/journal/README.md](docs/journal/README.md)
- [interface/README.md](interface/README.md)
- [config/bundles.template.yaml](config/bundles.template.yaml)
- [entrypoint.py](entrypoint.py), [platform/identity.py](platform/identity.py),
  [platform/stream_solution.py](platform/stream_solution.py),
  [platform/stream_prebuilt.py](platform/stream_prebuilt.py)

Add a dated journal entry for every round that changes the dispatcher, the agent
registry, the identity/agent fold, the storage scope/schema, a stream adapter, the
surfaces, config keys, or the dependency posture.

## Product Shape

```text
KDCube chat surface (reactive event)  ─┐  agent_id in state
Telegram webhook (public @api) ────────┤  (webhook -> DEFAULT agent)
  platform/telegram.py -> Telegram SDK ┘
  -> BaseEntrypointWithEconomics.run() -> execute_core(state, thread_id, params)
       agent_id = normalize(state.agent_id) or DEFAULT_AGENT_ID   [entrypoint.py]
       spec = AGENTS[agent_id]                                    (else default)
       -> platform/identity.turn_identity(state, agent_id)  -> TurnIdentity
       -> await _build_graph(agent_id, disabled_tools=...)   BOUND this turn
       -> spec.build_inputs(question, ident) -> inputs, run_config(thread_id)
       -> spec.stream(graph, inputs, run_config)  -> the agent's OWN stream adapter
            -> comm_ctx.step/delta/complete
  -> state["final_answer"]  -> platform conversation record (framework-neutral)
```

## Implementation Rules

- `solution/lg_solution/` and `solution/lg_prebuilt/` remain independently
  maintainable framework/domain packages. Prefer host behavior in `platform/`, but
  make a solution change when a safe async, configuration, model, import, or prompt
  seam genuinely belongs there. Keep it small, documented, and synchronized with
  the standalone source; never hide a solution fork inside runtime mutation.
- The dispatch is by `agent_id`. Keep the `AGENTS` registry the single source of
  truth: each `AgentSpec` carries `build_graph`, `stream`, `build_inputs`, and
  `role`. To add or change an agent, add/adjust a spec — do not branch on agent_id
  inside `execute_core`. Unknown/blank agent_id falls back to `DEFAULT_AGENT_ID`;
  keep that fallback.
- This app's graph captures the current model/tool choices, so `_build_graph` creates
  a fresh bound graph every turn and no turn-bound graph is cached on the entrypoint.
  An immutable compile artifact could be a per-worker optimization in another design,
  but it is never conversation state. Only the checkpointer CONNECTION is opened once
  per agent (`_open_checkpointer`) and reused.
- One stream adapter PER agent shape. `stream_solution.py` streams the DEDICATED
  answer node's tokens; `stream_prebuilt.py` streams only the FINAL turn of the
  LOOPING agent node. A new agent shape adds its own adapter and a spec — never
  overload one adapter with both shapes.
- The identity gate folds BOTH the platform identity AND the active `agent_id`
  into `user_id`, and scopes `thread_id` by it. This is the multi-agent safety
  invariant: the two agents' per-user memories must never mix. Keep it explicit
  and testable, not inlined into `execute_core`.
- Storage is ONE shared schema `kdcube_{tenant}_{project}` (`schema_for_scope`) with
  bundle-prefixed tables (`ported_langgraph_agents_memories`/`_kb`); the two agents
  are isolated by the `agent_id` COLUMN (+ the identity fold), NOT a per-agent schema
  (that would pollute Postgres). Never `CREATE EXTENSION` — the platform provides it.
  On KDCube's shared Postgres when hosted (pg_pool present), the agent's own
  `DATABASE_URL` when standalone, an in-memory saver when unreachable. Keep the
  fallback chain and the driver bridge (asyncpg pool present = hosted signal; psycopg
  DSN derived from `get_settings()` PG* fields).
- Bundle-local imports are package-relative (`from .solution.lg_solution...`,
  `from .platform.identity import ...`). Never add a `try/except ImportError`
  fallback to a top-level import. Both solution packages are already
  package-relative internally; keep them that way.
- Do not write KDCube conversation records from the app. Set
  `state["final_answer"]`; the platform records the turn for either agent.
- The Capabilities model picker is active PER AGENT (declared, not adapter code):
  each `surfaces.as_consumer.agents.<agent>` carries `capability_provider:
  simple_model_pick` + `capabilities.models` on that agent's own role. Apply the
  pick per turn the LangGraph way — `resolve_turn_role_models(self, state,
  agent_id)` for the ACTIVE agent, bound onto `bundle_call_context.role_models`.
  Never reach for `runtime_ctx.agent_role_models` (that is ReAct's seam). Fail
  open: any error/absent store → empty overlay → the router's default.
- Keep the Telegram side thin: the webhook only routes + renders for the default
  agent. All protocol mechanics belong to the reusable Telegram SDK; the
  bundle-side wiring is `platform/telegram.py`. Do not duplicate agent logic into
  the webhook; do not add a second delivery loop.
- Economic ENFORCEMENT stays split: (1) turn-level — the class derives
  `BaseEntrypointWithEconomics` (do not re-derive `BaseEntrypoint`); (2) per-call —
  lg-solution's embeddings go through `_solution_embedding_service()` →
  `search_model_service(flow=...)`, passed as a PROVIDER callable (re-resolved per
  embed). Economics config is descriptor-seeded at deploy; invent no quota keys.

## Configuration And Storage

Config ([config/bundles.template.yaml](config/bundles.template.yaml)):

- `surfaces.as_provider.bundle.default_chat: true` — the reactive chat surface.
- `surfaces.as_consumer.default_agent: lg-solution` + `agents.lg-solution` /
  `agents.lg-react`, each a `simple_model_pick` provider on its own role.
- `agents.lg-react.additional_instructions` — the administrator's customization,
  appended LAST in lg-react's system prompt inside the SDK's admin-customization
  HARD-OVERRIDE envelope (the same convention ReAct agents honor). The full
  system-prompt composition — block order, the named-services block, and where
  tool docs live — is
  [docs/agents/lg-react-system-prompt.md](docs/agents/lg-react-system-prompt.md).
- `tools.mode` — lg-react's tools seam (plain | mcp | both).
- `enabled.api.public.telegram_webhook.POST` + `integrations.telegram.default`.

Agents' own env (read directly; not KDCube config keys): `LG_MEMORY_BACKEND`
(lg-solution), `LG_PREBUILT_PROVIDER` / `LG_PREBUILT_CTX_TOKENS` (lg-react).
Secrets ([config/bundles.secrets.template.yaml](config/bundles.secrets.template.yaml)):
`OPENAI_API_KEY`, `DATABASE_URL`, the Telegram integration secrets.

Storage ownership (full matrix in [docs/storage/README.md](docs/storage/README.md)):
each agent's own store uses KDCube's shared Postgres in the one tenant/project
schema, with app-prefixed tables and `agent_id` row scope. The platform owns the
conversation record. Graph instances are rebuilt for one turn and discarded;
only checkpointer connections are process-local and reused. The SDK-shaped
Telegram user registry is the one app-local store.

## Keep In Sync

```text
entrypoint.py (BUNDLE_ID, DEFAULT_AGENT_ID, the AGENTS registry, roles, schemas,
               the telegram_webhook @api)
  == config/bundles.template.yaml (default_agent + agents.<agent> pickers/roles;
                                   tools.mode; telegram enable + integration)
  == platform/identity.py (turn_identity folds agent_id) + pg_target.py (agent_schema)
  == platform/capabilities.py (resolve_turn_role_models(ep, state, agent_id))
  == platform/stream_solution.py + stream_prebuilt.py (the two adapters)
  == interface/README.md + interface/ported-langgraph-agents.openapi.yaml
  == README.md + docs/README.md + docs/arch/README.md + docs/storage/README.md
  == docs/agents/lg-react-system-prompt.md (system-prompt block order + config keys)
  == docs/integrations/admin-integrational-homework.md
  == tests/ (dispatch, identity, storage, capabilities, both stream adapters,
             tools_mcp, telegram, interface contract) + the dated journal entry
```

Invariant: everything must agree that the id is
`ported-langgraph-agents@2026-07-13`, that TWO agents are dispatched by `agent_id`
through one `execute_core`, and that the surfaces are the reactive chat turn +
the Telegram webhook. No user credentials/state in any descriptor; Telegram
secrets are placeholders only. No `__pycache__`, `.pytest_cache`, or built output.

## Validate

```bash
cd app/ai-app/src/kdcube-ai-app

# 1. syntax of the glue + both preserved solution packages
python -m py_compile \
  "kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/entrypoint.py" \
  kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/platform/*.py

# 2. offline contract + dispatch tests (no DB, no API key)
python -m pytest -q \
  "kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/tests"
```

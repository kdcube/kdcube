---
id: ported-langgraph-agents@2026-07-13
title: "Ported LangGraph Agents"
summary: "ONE KDCube app that hosts TWO ported LangGraph agents (a custom research graph and a langchain.agents.create_agent ReAct agent), both vendored unchanged, dispatched by agent_id through a single execute_core. The 'one app, many agents' worked instance of the KDCube port recipe."
status: active
tags: ["app", "ported-langgraph-agents", "langgraph", "langchain", "platform", "port", "chat", "multi-agent", "telegram"]
module: entrypoint
singleton: false
primary_surfaces:
  - "reactive chat turn (surfaces.as_provider.bundle, default_chat) -> execute_core dispatches on agent_id"
  - "two scene chat widgets (chat_lg_solution / chat_lg_react) + a two-pane scene"
  - "telegram webhook (@api route=public, alias=telegram_webhook) -> same execute_core, default agent"
---

# ported-langgraph-agents — ONE app, MANY agents

This app is the **"one app, many agents"** worked instance of the KDCube port
recipe. It hosts **two** standalone LangGraph/LangChain agents — vendored
**unchanged** — behind a **single `execute_core`** that dispatches on **`agent_id`**:

| agent_id | what it is | shape | stream adapter |
| --- | --- | --- | --- |
| `lg-solution` | a rich research graph (KB retrieval + per-user pgvector memory + a nested subagent) — the "before" is [`../../poc/lg-solution`](../../poc/lg-solution) | linear, with a **dedicated answer node** | `platform/stream_solution.py` |
| `lg-react` | a `langchain.agents.create_agent` ReAct agent (plain tools + MCP + a code-exec tool; `SummarizationMiddleware` for context) — the "before" is [`../../poc/lg-prebuilt-agent`](../../poc/lg-prebuilt-agent) | ReAct loop, with a looping **`model` node** (no answer node) | `platform/stream_prebuilt.py` |

The teaching point: **different agent shapes → different stream adapters, selected
by `agent_id`.** Everything else — identity, storage, capabilities, economics, the
conversation record, file download, the Telegram ingress — is shared platform glue.

> `lg-react` was migrated off the deprecated `langgraph.prebuilt.create_react_agent`
> to `langchain.agents.create_agent`; its context management is the first-party
> `SummarizationMiddleware` (runs in its own `before_model` node). Some vendored
> `solution/lg_prebuilt/*` comments still name `create_react_agent` — the compiled
> graph in `agent.py` uses `create_agent`, whose loop node is named `model`.

## The two sides: `solution/` and `platform/`

```
ported-langgraph-agents@2026-07-13/
  solution/
    lg_solution/       ← the research graph (vendored UNCHANGED from poc/lg-solution)
    lg_prebuilt/       ← the create_agent ReAct agent (vendored from poc/lg-prebuilt-agent)
  platform/            ← the KDCube integration (shared across both agents):
    identity.py          platform identity + agent_id → each agent's per-user keys
    pg_target.py         storage edge → KDCube pg_pool, ONE shared schema + agent_id column
    stream_solution.py   dedicated-answer-node event stream → comm_ctx
    stream_prebuilt.py   looping model-node event stream → comm_ctx
    capabilities.py      per-turn, per-agent model pick (role_models overlay)
    tool_pick.py         lg-react's tool inventory (admin ∩ user-enabled, per turn)
    tools_mcp.py         lg-react's MCP tools seam
    code_exec.py         lg-react's code-execution tool: files hosted into the conversation,
                         the live exec widget (solutions/widgets/exec.py), and CLASSIFIED
                         errors to the model (runtime/retryable vs program)
    telegram.py          the shared Telegram ingress (drives the default agent)
  entrypoint.py        ← composition root: execute_core DISPATCHES on agent_id
```

Both `solution/` subpackages never import KDCube; only the `platform/` glue and
`entrypoint.py` know both worlds.

## The dispatcher — the graph is rebuilt every turn (scaled serving)

`execute_core` is a dispatcher over a per-agent registry:

```text
agent_id (state) ─normalize→ AGENTS[agent_id]  (else the default, lg-solution)
  AgentSpec: build_graph · stream · build_inputs · role · schema
    _build_graph(agent_id, disabled_tools)   ← REBUILT this turn (NOT cached)
    build_inputs(question, ident) → the agent's own input + run_config(thread_id)
    stream(graph, inputs, run_config) → the agent's OWN stream adapter → comm_ctx
  role_models = resolve_turn_role_models(self, state, agent_id)   (the active agent's pick)
  state["final_answer"] = <the streamed answer>
```

**No in-process graph cache.** KDCube is distributed — a turn can land on any
processor worker/machine — so the graph is **rebuilt per reactive event** from
rebuildable state; nothing agent-specific is cached on the long-lived entrypoint
object. The only reused handle is the **checkpointer connection** (opened once per
agent, like a pool), because it is a connection, not rebuildable per-turn state.
This is the "scaled serving" principle — see
[Port Your Solution To A KDCube App](../../../../../../../../../docs/recipes/kdcube_for_agents/port-your-solution-to-kdcube-README.md) and the
`_build_graph` / `_open_checkpointer` docstrings in `entrypoint.py`.

## Storage — ONE shared schema, agents separated by a column

Each agent's own store (lg-solution's memory + KB + checkpointer; lg-react's
checkpointer) is routed onto KDCube's **shared Postgres** (`self.pg_pool`) — into
**one per-tenant/project schema** with **bundle-prefixed tables**, NOT a per-agent
schema:

```text
  schema   kdcube_{tenant}_{project}                    (schema_for_scope)
  tables   ported_langgraph_agents_memories
           ported_langgraph_agents_kb
  scope    every row keyed by (tenant, project, bundle_id, agent_id, user_id)
```

The two agents share the same tables and stay apart via the **`agent_id` column**
(plus the identity gate folding `agent_id` into the per-user key) — their state can
never mix. Tables are provisioned idempotently in `on_bundle_load` with
`CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`; the bundle **never** runs
`CREATE EXTENSION` (the platform's PostgresSetup provides `vector`/`pg_trgm`). This
is the canonical KDCube storage pattern (mirrors `task-tracker`, `ConvIndex`,
`UserMemoryStore`) — a per-bundle/per-agent/per-version schema is an anti-pattern.
Standalone (no `pg_pool`) each agent keeps its own `DATABASE_URL`; unreachable →
empty recall + an in-memory checkpointer. Full map:
[docs/storage/README.md](docs/storage/README.md).

## The conversation record — reload without a React timeline

A React bundle writes a rich turn log from its timeline; a run-to-completion
`execute_core` writes none. KDCube fills the gap for **any** framework: after the
turn, the platform records a **minimal turn log** so the conversation list/reload
work. This app only sets `state["final_answer"]` (+ `state["hosted_files"]` from
code-exec) — the platform reconstructs the reloadable turn from:

- **the turn log** — user prompt + user attachments + hosted files + the answer;
- **the events artifact** — the dynamic objects the turn emitted **through comm**
  (citations, steps, follow-ups), captured full-payload and **replayed** on reload
  so the client renders them exactly as it did live.

The rule: reload content comes from **comm + the turn log**, not from runtime
`state`. Nothing framework-specific is required of the ported agent.

## Surfaces

- **the reactive chat turn** (`surfaces.as_provider.bundle`, `default_chat: true`) —
  each user message dispatches on `agent_id` and streams the chosen agent's answer;
- **two scene chat widgets** — `chat_lg_solution` / `chat_lg_react`, each a dedicated
  `@ui_widget` on the entrypoint (agent-bound via `VITE_CHAT_AGENT_ID`), mounted
  side-by-side in a two-pane scene (`ui/scene/`);
- **file download** — the app serves `scene_object_action` so a chat file card's
  Download resolves its `conv:fi:` file through the platform file resolver (a bundle
  that hosts files MUST serve this; without it Download has no endpoint);
- **the Telegram Bot API webhook** (`@api(route="public", alias="telegram_webhook")`)
  — a Telegram message drives the same turn for the **default agent**.

No MCP server, Data Bus handler, cron, or background job. See
[interface/README.md](interface/README.md),
[interface/ported-langgraph-agents.openapi.yaml](interface/ported-langgraph-agents.openapi.yaml),
and [docs/integrations/admin-integrational-homework.md](docs/integrations/admin-integrational-homework.md).

## Model selection (Capabilities widget) + the title

The chat model picker is active **per agent**: each agent declares the generic
`simple_model_pick` provider in config (`surfaces.as_consumer.agents.<agent>`),
naming its own answer role (`lg-solution.answer` / `lg-react.answer`) and model
list. `execute_core` resolves the pick for the **active** agent and overlays it onto
`bundle_call_context.role_models` around that agent's graph run — so a pick for one
agent never cross-applies to the other.

The answer roles are ALSO bound in **base `config.role_models`**. That is required:
the conversation **title** is generated (first turn) using the responsible agent's
answer role, and it runs OUTSIDE the per-turn pick overlay (and the pick is only
wired for the active agent) — without the base binding the role resolves to no
model and the title comes back empty. See [docs/README.md](docs/README.md).

## Code execution (lg-react)

Declaring the `code_exec` tool connection gives `lg-react` a `run_python` tool that
runs the model's Python in the platform's isolated runtime (`get_exec_workspace_root()`).
It is wired to behave like the built-in React code tool (`platform/code_exec.py` +
`code_exec_tool.py`):

- **A declared output contract** (like the real exec tool). `run_python` takes the
  same params — `code` + `contract` (+ `prog_name`, `timeout_s`) — so the model can
  PLAN the files it will produce; with a contract the code runs verbatim through the
  platform contract runner (`run_exec_tool`) and only contracted files are hosted.
  With NO contract it falls back to side-effects (plain relative paths auto-hosted).
- **Files** the code produces are hosted into the conversation like attachments, so
  they reload and Download through `scene_object_action`.
- **A live exec widget** — the reusable `solutions/widgets/exec.py` streamer emits the
  `code_exec.*` panel (program name → the contract → code → status
  `gen → exec → done|error`), so the chat renders the same exec panel React shows. The
  widget is built for exactly these inputs (program name, contract, code). No client change.
- **Classified errors** reach the model so it reacts correctly: a **runtime/sandbox**
  failure (`sandbox_execution_failed`) is a platform problem it may RETRY; a **program**
  error is its own code to fix. Both are surfaced in the tool's text result, so a
  failure is never silent. Contract: `docs/exec/exec-logging-error-propagation-README.md`.
- **Available packages** are appended to the tool description
  (`build_packages_installed_block()` — the same block the React exec tool surfaces),
  so the model writes imports that actually resolve.

## Degradation

With no provider key each agent runs its deterministic stub; with no reachable
Postgres each degrades to empty retrieval + an in-memory checkpointer. Both stream
either way — offline turns emit a single answer delta instead of a token stream.

## Both agents are run-to-completion

Each agent runs a turn to completion and does **not** consume in-turn
followups/steers; a followup sent mid-turn is promoted to the next turn (the
queued-next contract). In-turn followup/steer is an advanced reactive-port tier
neither agent implements — the correct default for a ported LangGraph agent.

## Links

- Config: [config/bundles.template.yaml](config/bundles.template.yaml)
- Interface: [interface/README.md](interface/README.md)
- Design index: [docs/README.md](docs/README.md)
- Architecture: [docs/arch/README.md](docs/arch/README.md)
- Storage map: [docs/storage/README.md](docs/storage/README.md)
- Package journal: [docs/journal/README.md](docs/journal/README.md)
- Builder-agent guide: [AGENTS.md](AGENTS.md)
- Dependencies: [requirements.txt](requirements.txt)

## The generic procedure

This app follows the port procedure a coding agent executes against an arbitrary
Python project. The executable steps are in
[Port Your Solution To A KDCube App](../../../../../../../../../docs/recipes/kdcube_for_agents/port-your-solution-to-kdcube-README.md);
this app is the "one app, many agents" worked instance those steps point to.

# Ported LangGraph Agents Interface

`ported-langgraph-agents@2026-07-13` hosts **two** ported LangGraph agents
(`lg-solution` = a research graph with a dedicated answer node; `lg-react` = a
`langchain.agents.create_agent` ReAct agent with a looping `model` node), preserved
under `solution/` with explicit integration seams, dispatched by `agent_id` through
a **single `execute_core`**. It
exposes **two ingresses that drive that same turn**:

1. **the reactive chat turn** — a browser message on the KDCube chat surface,
   dispatched on `agent_id` to the right agent;
2. **the Telegram Bot API webhook** — `POST /public/telegram_webhook`, a Telegram
   message routed into the same turn (the **default agent**) and answered back over
   the Bot API.

The machine-readable contract is
[ported-langgraph-agents.openapi.yaml](ported-langgraph-agents.openapi.yaml) (the
webhook under `paths`, both surfaces under `x-kdcube-surfaces`); this README is the
human contract.

## Surface 1: the reactive chat turn (agent_id dispatch)

The KDCube chat surface delivers each user message as a reactive event. The base
entrypoint's `run()` calls `execute_core(state, thread_id, params)`, which resolves
the active agent and runs it:

```text
turn in
  user text  ->  state["external_events"]  ->  external_events_text(...)
  agent_id   ->  state["agent_id"]  ->  AGENTS[agent_id]  (else the default, lg-solution)

turn out (streamed live to the reusable chat component via comm_ctx, through the
          ACTIVE agent's OWN stream adapter)
  graph node start / tool run   ->  comm_ctx.step(name, "running")
  answer tokens                 ->  comm_ctx.delta(token, marker="answer")
  graph node / tool end         ->  comm_ctx.step(name, "completed")
  end of turn                   ->  comm_ctx.complete(data={"final_answer": ...})
                                    and state["final_answer"] = <answer>
```

- `lg-solution` streams the tokens of its **dedicated answer node**
  (`platform/stream_solution.py`).
- `lg-react` streams only the **final turn of its looping agent node** and
  surfaces each `tools` run as a step (`platform/stream_prebuilt.py`).

`state["final_answer"]` is the platform's canonical answer; the platform-owned
conversation record persists it so chat list / fetch / reload work for either agent.

Economic enforcement is a **cross-cutting guard on this same chat turn**, not a new
surface: the turn-level budget preflight + rate limiter (from
`BaseEntrypointWithEconomics`) run inside `run()` before `execute_core`, and a
per-call budget check guards lg-solution's retrieval/memory embeddings. A denial
surfaces on the stream as a `rate_limit.*` service event, not a separate endpoint.

## Surface 2: the Telegram Bot API webhook

A public HTTP webhook that drives the **same** turn for the **default agent**. It is
declared once as `@api(route="public", method="POST", alias="telegram_webhook")` and
delegates every mechanic to the reusable Telegram SDK
(`kdcube_ai_app.apps.chat.sdk.integrations.telegram`); the bundle-side wiring is the
thin `platform/telegram.py`.

```text
Telegram Bot API  --POST /public/telegram_webhook?integration_id=telegram.default-->
  SDK verifies X-Telegram-Bot-Api-Secret-Token  (missing/wrong -> 401)
  SDK claims update_id (dedupe) + resolves the Telegram sender
    -> platform identity user = telegram_<telegram_user_id>
  SDK drives the SAME turn (execute_core) for the DEFAULT agent
  identity.py isolates telegram_<id>'s memory (folded with the active agent id)
  SDK renders the answer + streamed progress back over the Bot API
```

`route="public"` only means the route is reachable without platform auth. The
**trust boundary is the Telegram webhook secret**, verified by the SDK before any
work. The webhook only routes and renders; the turn (`execute_core`) is unchanged
and shared by both surfaces.

External setup (creating the bot, the token, the webhook secret, and calling
`setWebhook`) happens outside the code — see
[../docs/integrations/admin-integrational-homework.md](../docs/integrations/admin-integrational-homework.md).

Beyond the chat turn + Telegram webhook, the app declares two scene chat widgets
(`@ui_widget` `chat_lg_solution` / `chat_lg_react`, mounted in `ui/scene/`) and the
`scene_object_action` file-download operation. No MCP server (`@mcp`), no Data Bus
handler, cron, or background job.

## The isolation boundary (per agent)

One deployment is bound to one tenant/project, while the same worker process can
serve many users concurrently and hosts both agents. `identity.py` maps the bound
platform identity + the ACTIVE `agent_id` onto each agent's per-user keys:

```text
platform state + agent_id        agent key
  tenant + project + AGENT + user  ->  user_id   = "{tenant}:{project}:{agent}:{user}"
  user_id + conversation           ->  thread_id = "{user_id}:{conversation}"
```

The same raw user id under lg-solution and lg-react resolves to DIFFERENT keys,
so the two agents' memories never mix. Anonymous callers fall back to `fingerprint`,
then `"anonymous"`; a blank agent id folds to `default`.

## The storage boundary (per agent)

Each agent keeps its **own** store — routed onto KDCube's shared Postgres into ONE
per-tenant/project schema with bundle-prefixed tables, the two agents separated by
the `agent_id` column (no per-agent schema); its own `DATABASE_URL` standalone. The
platform owns the **conversation record** (the app writes none). See [../docs/storage/README.md](../docs/storage/README.md).

## Config and secret keys that control this app

Non-secret deploy config (see [../config/bundles.template.yaml](../config/bundles.template.yaml)):

- `surfaces.as_provider.bundle.default_chat` — the reactive chat surface (surface 1).
- `surfaces.as_consumer.default_agent` (`lg-solution`) + `agents.lg-solution` /
  `agents.lg-react` — each declares a `simple_model_pick` picker on its own role.
- `tools.mode` — lg-react's tools seam (plain | mcp | both).
- `enabled.api.public.telegram_webhook.POST` — enables surface 2.
- `integrations.telegram.default` — the Telegram integration (`enabled: false` by
  default).

Deploy secret keys (see [../config/bundles.secrets.template.yaml](../config/bundles.secrets.template.yaml)):

- `OPENAI_API_KEY` — the agents' model. Absent → the offline stub.
- `DATABASE_URL` — the agents' own Postgres, used on the standalone path.
- `integrations.telegram.default.definition.bot_token` / `webhook_secret` — the Bot
  API credential + the shared secret the SDK verifies.

The chat component's Capabilities model picker is active per agent. The
`agent_capabilities` / `agent_selection_update` operations are inherited platform
ops (served by `BaseEntrypoint`), not declared in this app's manifest; the app turns
the picker on by declaring `simple_model_pick` under each agent. A user's
per-conversation pick is applied per turn for the ACTIVE agent by binding its answer
role onto `bundle_call_context.role_models`. See [../docs/README.md](../docs/README.md).

## Runtime notes

- Each graph captures current model/tool choices, so `_build_graph` creates a fresh
  bound graph per turn; only the checkpointer connection is opened once per agent and
  reused. Every turn is keyed per (agent, user, conversation), so any worker serves
  any turn safely. An immutable compile cache, if introduced, is not continuity.
- The app hard-requires only `langgraph`, which the processor environment already
  provides. Each agent degrades if the optional Postgres checkpointer / `psycopg` v3
  are absent. For a hardened deploy that pins the full dependency set, use the
  `@venv` contract with [../requirements.txt](../requirements.txt).

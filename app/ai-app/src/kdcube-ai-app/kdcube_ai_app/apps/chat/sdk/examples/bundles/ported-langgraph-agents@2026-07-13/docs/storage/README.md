---
id: kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/storage/README.md
title: "Ported LangGraph Agents Storage Map"
summary: "The non-hosted → hosted storage transition for ported-langgraph-agents@2026-07-13: each preserved agent's own Postgres store (memory/KB/checkpointer) is routed onto KDCube's SHARED Postgres (pg_pool) into ONE per-tenant/project schema with bundle-prefixed tables and explicit agent_id scope."
status: active
tags: ["ported-langgraph-agents", "storage", "postgres", "pgvector", "langgraph", "checkpointer", "pg_pool", "stateless", "multi-agent", "platform"]
---

# Ported LangGraph Agents Storage Map

Each standalone agent (the "before") keeps every mutable byte in its OWN Postgres,
on one machine. Hosted by KDCube, the SAME process serves many users across many
tenants concurrently, hosts BOTH agents, and any worker may serve any turn. So the
storage transition is: route each agent's own store onto KDCube's SHARED Postgres
(`self.pg_pool`'s database), into **one per-tenant/project schema** with
**bundle-prefixed tables**, agents kept apart by an **`agent_id` column** and keyed
per (user, conversation). Each agent then holds nothing per-turn in-process — it is
stateless and distributed-safe, and the two agents' state can never mix.

The domain graph remains solution-owned. This document covers the DB EDGE: which
Postgres and schema the nodes/checkpointer use, plus the required row/key scope.

## One shared schema, agents separated by a column

This is the canonical KDCube storage pattern (mirrors `task-tracker`, `ConvIndex`,
`UserMemoryStore`). A per-agent / per-bundle / per-version schema is an
**anti-pattern** — it pollutes Postgres.

```text
  schema   kdcube_{tenant}_{project}                    (schema_for_scope)
  tables   ported_langgraph_agents_memories             (lg-solution recall)
           ported_langgraph_agents_kb                   (lg-solution KB)
           + LangGraph checkpointer tables              (both agents)
  scope    every row keyed by (tenant, project, bundle_id, agent_id, user_id)
```

`platform/pg_target.schema_for_scope(tenant, project)` computes the single schema
name (unsafe chars folded, `kdcube_` prefix). Two mechanisms keep the agents
isolated: the **`agent_id` column** (every write carries it, every read filters on
it) AND the identity gate folding `agent_id` into `user_id`/`thread_id` (key-level
separation). Tables are created idempotently in `on_bundle_load` with
`CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`; the bundle **never** runs
`CREATE EXTENSION` — the platform's PostgresSetup provides `vector`/`pg_trgm`/`pgcrypto`.

## Before → after (by data kind)

| Data kind | Local (before — poc / standalone) | Hosted (KDCube backend) |
| --- | --- | --- |
| lg-solution working/episodic recall — pgvector `memories` | its own Postgres (`DATABASE_URL`) | `pg_pool`, table `ported_langgraph_agents_memories`, `WHERE agent_id='lg-solution'` |
| lg-solution knowledge base — seeded `kb_documents` | its own Postgres (`DATABASE_URL`) | `pg_pool`, table `ported_langgraph_agents_kb` (uniqueness `(tenant,project,bundle_id,agent_id,title)`) |
| lg-solution LangGraph checkpointer | its own Postgres (`DATABASE_URL`) | `pg_pool`, checkpointer tables in `kdcube_{tenant}_{project}`, `thread_id` scoped per user+agent |
| lg-react LangGraph checkpointer | its own Postgres (`DATABASE_URL`) | `pg_pool`, same schema, `thread_id` scoped per user+agent |
| Conversation record — framework-neutral turn log + events | none | the platform conversation record (see below — either agent) |
| Ephemeral cache | in-process | KDCube KV cache (`kv_cache`, from the base entrypoint) |

The worked app builds a fresh graph bound to the turn's model/tool choices; only the
**checkpointer connection** is opened once per agent and reused. An immutable compile
cache, if introduced, is not conversation continuity.

## The injection point (the whole selection)

There is no runtime toggle. The ONLY selection is whether the bundle hands an agent
a KDCube Postgres connection:

```text
  pg_pool present  ->  KDCube shared Postgres DSN + schema_for_scope()   (HOSTED)
  else             ->  the agent's own DATABASE_URL                      (LOCAL / poc)
  DB unreachable   ->  empty recall + a MemorySaver checkpointer         (OFFLINE)
```

`resolve_solution_pg(pg_pool, own_url, schema)` per agent ensures the schema and
hands that agent a `Config` whose `database_url` targets the resolved store;
`_open_checkpointer(agent_id, url)` opens once on the same DSN and is reused across
per-turn graph builds. Memory and the checkpointer flip together. The fallback
chain keeps an offline / bare-local run working.

**Driver bridge.** KDCube's `pg_pool` is an *asyncpg* pool; the agents and LangGraph
use *psycopg v3*. So the pool object is never handed across the driver boundary.
`platform/pg_target.py` derives a psycopg/libpq DSN from the SAME platform settings
`get_pg_pool()` builds the pool from (`get_settings()` `PG*` fields + SSL). The
pool's PRESENCE is the hosted signal; the settings are the durable connection bridge.

**Search path.** The DSN carries a libpq `options=-c search_path=<schema>,public`
so every store lands in the shared `kdcube_{tenant}_{project}` schema, isolated from
platform tables in `public`; `public` stays on the path so the shared `vector`
extension type resolves. Agent separation is by the `agent_id` COLUMN inside that
schema, not by a separate schema per agent.

## Isolation gate

Every store is partitioned by `platform/identity.py`, which folds the ACTIVE
`agent_id` into the keys:

```text
platform state + agent_id        agent key
  tenant + project + AGENT + user  ->  user_id   = "{tenant}:{project}:{agent}:{user}"
  user_id + conversation           ->  thread_id = "{user_id}:{conversation}"
```

The fold into `user_id` makes single-machine agent code safe for many concurrent
users AND keeps the two agents' per-user stores apart: the same raw user id under
lg-solution and lg-react resolves to different keys. Anonymous callers fall back to
`fingerprint`, then `"anonymous"`.

## The conversation record (reload)

KDCube separately owns the reloadable conversation record — framework-neutral, the
app writes no React timeline. After the turn the platform records a **minimal turn
log** (user prompt + attachments + hosted files + `final_answer`) and an **events
artifact** materializing the dynamic objects the turn emitted through comm (citations,
steps, follow-ups). The app only sets `state["final_answer"]` (+ `state["hosted_files"]`
for code-exec output). Reload content comes from **comm + the turn log**, not runtime
`state`. Hosted files download through the `scene_object_action` operation the bundle
serves.

## The map-by-data-kind rule

- **A conversation / turn log** → the platform **conversation record**. Both agents.
- **Durable user facts / preferences** → the **`mem` named service**. Neither agent
  uses it: lg-solution's `memories` are its own working recall, not durable
  cross-app user facts.
- **An agent's OWN working store** (private recall, KB, framework checkpoints) → the
  agent's OWN **bundle-prefixed tables** on KDCube's shared Postgres (`pg_pool`),
  scoped by the `agent_id` column.

## Ownership matrix

| Object | Owner | Storage (hosted) | Keyed by | Notes |
| --- | --- | --- | --- | --- |
| lg-solution working/episodic memory | the agent | `pg_pool`, `ported_langgraph_agents_memories` | `(tenant,project,bundle_id,agent_id,user_id)` | The agent's OWN working store (not `mem`). Unreachable DB → empty recall. |
| lg-solution knowledge base | the agent | `pg_pool`, `ported_langgraph_agents_kb` | `(…,agent_id,title)` unique | Seeded corpus; shared across users. |
| lg-solution / lg-react checkpoints | the agent (framework) | `pg_pool`, `kdcube_{tenant}_{project}` | `thread_id` (user + agent) | Unreachable DB → an in-memory `MemorySaver`. |
| Checkpointer connection | this app (process-local) | process memory | per process, per agent | A CONNECTION, opened once and reused across turn-bound graph builds. |
| Conversation record (turn log + events) | the platform | platform conversation store | platform conversation id | Framework-neutral; the app sets `state["final_answer"]` / `hosted_files`. |
| User agent selection (model pick) | the platform | KDCube control-plane Postgres (`UserAgentSelectionStore`) | `(user_id, bundle_id, agent_id, conversation_id)` | Read-through; resolved per turn for the ACTIVE agent. |
| Economics / budget state | the platform | KDCube control-plane Postgres + Redis | tenant/project/user subject | Read-through; seeded at deploy from `economics.yaml`. |

## Statelessness invariant

Nothing per-turn lives in-process. The turn-bound graph is built from shared state;
the only graph-related thing held on the entrypoint instance is the
checkpointer **connection** (opened once per agent). Every mutable byte is in shared
Postgres keyed by (agent, user, conversation). So **any processor worker can serve
any turn for either agent** (regression-tested: `tests/test_storage_pg_target.py`,
`tests/test_dispatch.py`).

## Secrets

Two deployment secrets (see
[../../config/bundles.secrets.template.yaml](../../config/bundles.secrets.template.yaml)):

- provider API key — the agents' model. Absent → the deterministic offline stub.
- `DATABASE_URL` — the agents' own Postgres, used ONLY on the standalone path (no
  `pg_pool`). Hosted, each agent's store lives on KDCube's shared Postgres via
  `pg_pool` + platform `POSTGRES_*` settings. Absent / unreachable on the local path
  → empty retrieval + an in-memory checkpointer.

No user credentials and no user state belong in any descriptor template.

## Retention, backup, cleanup

Hosted, each agent's store lives in KDCube's shared `kdcube_{tenant}_{project}`
schema, so it follows the platform's database operations (backup, vacuum, retention).
On the standalone path each agent's own Postgres remains the operational concern. The
conversation record follows the platform's own retention policy.

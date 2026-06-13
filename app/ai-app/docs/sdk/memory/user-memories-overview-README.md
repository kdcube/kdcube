---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
title: "User Memories Overview"
summary: "Overview of SDK cross-conversation user memory: durable Postgres storage, event-backed updates, search modes, visibility, tools, compact agent rendering, and serving hotsets."
tags: ["sdk", "memory", "user-memory", "postgres", "pgvector", "bundle-tools"]
keywords: ["cross-conversation memory", "user_memory_entries", "user_memory_events", "user_memory_aliases", "MemorySignal", "MemoryScope", "hotset", "semantic memory search", "me memory id", "memory rendering"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/how-react-remembers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-operational-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-react-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-reconcilation-README.md
---
# SDK Cross-Conversation Memory

This module defines the SDK-level user memory store for durable context that
survives across conversations. It is intentionally separate from any one bundle:
bundles expose tools and UI, while the SDK memory layer owns the persistence,
ranking, and concurrency rules.

Code lives in:

```text
kdcube_ai_app/apps/chat/sdk/context/memory/
```

For bundle mounting details, read
[Bundle Subsystem Integration](../bundle/bundle-subsystem-integration-README.md).
Memory is a reusable subsystem: inheriting `MemoryEntrypointMixin` or
`BaseEntrypointWithMemory` is necessary but not sufficient. The consuming
bundle must also configure memory enablement, widget/static UI, visibility,
tools, announce, storage/schema, and tests.

## Goals

- Store durable user memory in PostgreSQL, not in per-turn scratch files.
- Support tiers: confirmed high-value memories stay hot, lower-confidence
  memories remain searchable.
- Support semantic, keyword, label, recency, update-count, and confirmation
  signals in one search API.
- Allow concurrent conversations for the same user without duplicate storms.
- Let background jobs read memory while keeping writes restricted to approved
  conversational surfaces or service APIs.
- Expose only user-visible memories to canvas or user-facing UI.

## Bundle Configuration

Memory is opt-in per bundle. A full safe development configuration is:

```yaml
config:
  memory:
    enabled: true

    announce:
      enabled: true
      limit: 8
      scope_filter: current_bundle
      timeout_seconds: 1.5

    tools:
      enabled: false
      allow_write: false
      default_scope_filter: current_bundle
      embedding_enabled: true
      embedding_timeout_seconds: 3.0

    widget:
      enabled: true
      allow_write: true
      default_scope_filter: current_bundle
      allow_all_user_memories: true
      ensure_schema: true
      limit: 30
      search_min_relevance_score: 0.58

  ui:
    widgets:
      memories:
        enabled: true
```

`memory.enabled` is the master switch. `announce.enabled` injects a bounded
hotset into ReAct turn preparation. `widget.enabled` exposes the user-facing
memory widget. Keep `tools.allow_write=false` until explicit conversational
memory-write policy is enabled.

Allowed scope filters are:

```text
current_bundle
all_user_memories
global_only
current_bundle_or_global
```

When a bundle inherits `MemoryEntrypointMixin`, the memory widget source folder
and build command are provided by defaults; descriptors usually only need
`ui.widgets.memories.enabled=true`.

## Storage Model

Postgres is authoritative. The platform provisioning scripts install the
tables, and the module uses `pgvector` in version 1. Redis is not part of the
memory correctness path; use it only for optional cross-replica locks around
maintenance jobs.

The store creates/uses these tables in the tenant/project schema:

```text
<schema>.user_memory_entries
<schema>.user_memory_events
<schema>.user_memory_aliases
<schema>.user_memory_maintenance_artifacts
<schema>.user_bundle_props
```

`user_memory_entries` is the current state of each memory.
`user_memory_events` is the append-only evidence trail: confirmations,
refinements, contradictions, edits, retirements, and agent observations.
`user_memory_aliases` stores stable lookup hooks such as labels and keywords.
`user_memory_maintenance_artifacts` indexes user-visible memory snapshots and
reconciliation jobs whose larger artifacts live in bundle storage.
`user_bundle_props` stores user-scoped settings. Memory uses this generic table
with `subsystem='memory'`, `bundle_id='*'`, and `key='preferences'` for the
cross-bundle user preference that enables or disables memory use.

These names intentionally do not reuse the older `<schema>.user_memory`
`fact/strength/tags` table. That table can continue to exist until we decide on
an explicit migration.

Provisioning is done by:

```text
kdcube_ai_app/ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql
kdcube_ai_app/ops/deployment/sql/chatbot/drop-kdcube-proj-schema.sql
```

The caller passes a `MemoryScope`:

```python
MemoryScope(
    tenant="demo-tenant",
    project="demo-project",
    user_id=user_id,
    bundle_id="task-and-memo-app@1-0",
)
```

The store accepts an existing processor `pg_pool`:

```python
store = UserMemoryStore(pg_pool=request.app.state.pg_pool, tenant=tenant, project=project)
await store.ensure_schema()
```

## User Control

The memory widget exposes a global "Use my memory" preference. This is not
bundle-local: when the user disables memory, SDK memory tools and announce
hotset loading stop reading and writing memory for that user across all
bundles in the tenant/project schema.

The control UI remains available while memory is disabled so the user can
inspect, export, delete, or re-enable saved notes. The preference record is:

```text
user_bundle_props
  user_id=<current user>
  bundle_id="*"
  subsystem="memory"
  key="preferences"
  value_json={
    "memory_enabled": false,
    "updated_by": "user",
    "metadata": {}
  }
```

Single-note delete and filtered bulk delete are hard deletes. They remove the
memory row and rely on `ON DELETE CASCADE` to remove related event and alias
rows. Use `retire` only when preserving an inactive memory is desired.

## Writes

Use `record_signal()` for both create and update.

```python
await store.record_signal(
    scope=scope,
    signal=MemorySignal(
        memory="User prefers concise technical answers.",
        context="Repeatedly asked to avoid fluffy explanations.",
        kind="preference",
        event_type="confirmation",
        originator="user",
        labels=["communication-style"],
        keywords=["concise", "technical"],
        confidence=0.9,
        importance=0.8,
        visibility="user",
        source={
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "bundle_id": scope.bundle_id,
        },
    ),
)
```

The write path:

```text
MemorySignal
   |
   v
normalize labels / keywords / visibility
   |
   v
find row by match_memory_id or canonical_key
   |
   v
optional conservative candidate match
   |
   v
SELECT ... FOR UPDATE
   |
   v
append user_memory_events row
   |
   v
recompute scores, tier, revision
```

Concurrent identical writes converge through the unique canonical key:

```text
(tenant, project, user_id, canonical_key) WHERE merged_into_id IS NULL
```

## Tiers

Tier is computed from confidence, importance, freshness, confirmation rate, and
update count.

```text
Tier 1: active, repeatedly confirmed, high salience
Tier 2: useful but not fully established
Tier 3: weak, new, contradicted, or low-salience
Tier 4: retired or merged
```

Tier is a serving hint, not a permission flag. Lower-tier memories remain
searchable.

## Search Modes

`MemorySearchRequest.mode` supports:

```text
hybrid          semantic + text + labels + salience + recency
recent          most recently updated memories
recent_created  newest memories
recent_events   latest evidence events
important       highest importance/salience
confirmed       strongest confirmation rate/count
hotset          tier/salience order for compact prompt injection
```

Examples:

```python
await store.search(MemorySearchRequest(scope=scope, query="email preferences", mode="hybrid"))
await store.search(MemorySearchRequest(scope=scope, mode="recent", limit=10))
await store.get_hotset(scope=scope, limit=8)
```

For "last N memories", use the explicit helpers:

```python
await store.list_recent_memories(scope=scope, limit=10)
await store.list_recent_memories(scope=scope, limit=10, created=True)
await store.list_recent_events(scope=scope, limit=20)
```

Hybrid search is a two-branch recall plus scoring pipeline:

```text
query text + optional query embedding
  |
  +-- lexical branch:
  |     normalized meaningful query terms
  |     PostgreSQL text search over memory/context/labels/keywords
  |
  +-- semantic branch:
        pgvector cosine similarity over memory embeddings
  |
  v
union + de-duplicate candidates
  |
  v
rank_candidate()
  semantic + lexical text + label/keyword match
  + salience + importance + confidence + freshness + confirmation
```

The lexical branch intentionally drops filler words such as `which`, `i`,
`the`, and `where`, so a query like `cities which i visit` searches on
meaningful terms such as `cities` and `visit`.

If query embeddings are unavailable, search falls back to lexical recall plus
quality ranking. Quality ranking still uses tier, pinned status, confidence,
salience, importance, freshness, and updated time as sort signals. If a memory
has no embedding, it can still match through text, labels, and keywords.

`search_min_relevance_score` prevents unrelated memories from being returned
just because the database is small. Keep the default conservative in widgets,
then tune per product once real usage data is available.

## Labels And Keywords

Use both.

Labels are stable facets used for filtering and UI chips:

```text
communication-style, delivery-channel, product-scope
```

Keywords are looser retrieval hooks and aliases:

```text
telegram, brief, invoice, crm, wuppertal
```

Search supports both labels and keywords, and also full text over memory,
context, labels, and keywords.

## Visibility

Visibility controls UI exposure:

```text
user / owner / public  -> visible_to_user=true
private / internal     -> visible_to_user=false
```

Canvas and user-facing memory widgets should request only visible rows:

```python
MemorySearchRequest(scope=scope, mode="important", visible_to_user=True)
```

Private/internal rows may still be available to the assistant when the bundle
policy allows it.

User edits should become events, not silent overwrites. A canvas edit can call
`record_signal(event_type="user_edit", match_memory_id=...)`; a user
confirmation can call `confirm_memory(...)`. A user delete action should call
the hard-delete API so the memory row and related event/alias rows are removed.
Use `retire_memory(...)` only for a status transition where the inactive memory
should remain in storage for explicit historical inspection.

The service/API layer can live in a bundle base entrypoint: widgets and API
endpoints call this SDK store with the authenticated user scope and the
processor `pg_pool`. The memory store is intentionally not a REST service by
itself.

## Background Jobs

Background jobs should normally receive read-only tools:

```text
search, recent, hotset: allowed
record_signal, update_status, retire: not allowed
```

This prevents unattended jobs from reshaping long-term user memory. If a job
discovers a durable signal, it should return evidence to the conversational
surface or a service-owned reconciliation endpoint.

## Reusable Tools

The SDK ships a reusable Semantic Kernel-compatible tool module:

```python
from kdcube_ai_app.apps.chat.sdk.context.memory import make_user_memory_tools

memory_tools = make_user_memory_tools(
    scope_provider=current_bundle_scope,
    store_factory=lambda sc: UserMemoryStore(
        pg_pool=sc["pg_pool"],
        tenant=sc["tenant"],
        project=sc["project"],
    ),
    allow_write=True,
)
```

For job agents:

```python
job_memory_tools = make_user_memory_tools(
    scope_provider=current_job_scope,
    store_factory=store_factory,
    allow_write=False,
)
```

Bundles normally include the portable module in
`surfaces.as_consumer.agents.<agent>.tools`:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - name: memory
            kind: python
            module: kdcube_ai_app.apps.chat.sdk.context.memory.tools
            alias: memory
            allowed:
              - search_memory
              - recent_memories
              - read_memory
              - record_memory
              - confirm_memory
              - retire_memory
```

This exposes the callable tools with ids:

```text
memory.search_memory
memory.recent_memories
memory.record_memory
memory.confirm_memory
memory.retire_memory
```

The module-level functions bind to the current bundle request context at call
time. They resolve tenant, project, user id, bundle id, conversation id, turn
id, `pg_pool`, and the configured memory tool options from the runtime.
`make_user_memory_tools(...)` is the explicit construction path for jobs or
custom bundle code that already has its own scope provider.

Tool availability is gated by bundle config:

```yaml
config:
  memory:
    enabled: true
    tools:
      enabled: true
      allow_write: false
      default_scope_filter: current_bundle
      embedding_enabled: true
```

`memory.enabled` and `memory.tools.enabled` must both be true. Write tools also
require `memory.tools.allow_write=true`.

Provided functions:

| Tool | Purpose | Result shape |
| --- | --- | --- |
| `memory.search_memory` | Search durable cross-conversation user memory by hybrid/text/labels/keywords/recency modes. | `{ok:true, memories:[...], count:n}` or `{ok:true, events:[...], count:n}` for `mode="recent_events"` |
| `memory.recent_memories` | Return recent durable memories for the configured scope. | `{ok:true, memories:[...], count:n}` |
| `memory.record_memory` | Create or refine durable memory from a signal. | `{ok:true, memory:{...}}` |
| `memory.confirm_memory` | Confirm an existing memory by id. | `{ok:true, memory:{...}}` |
| `memory.retire_memory` | Retire an existing memory by id. | `{ok:true, memory:{...}}` |

Errors use:

```json
{"ok": false, "error": "memory_tools_disabled", "message": "..."}
```

The ReAct event-source pipeline treats these as structured JSON tool results.
They do not produce source-pool rows, hosted files, or snapshot artifacts by
default; they use the shared structured-result block-production policies so
their timeline blocks match ordinary external tool results.

## Announce Hotset

The ReAct announce should not dump the full memory database. Use a compact
hotset:

```python
hot = await store.get_hotset(scope=scope, limit=8)
```

Good announce shape:

```text
[USER MEMORY HOTSET]
  - me:mem_abc tier=1 conf=0.91 updated=2026-05-10 labels=style
    User prefers concise technical answers.
  - me:mem_def tier=1 conf=0.86 updated=2026-05-08 labels=delivery
    User prefers Telegram for short reports.
```

The hotset should include only high-salience active memories and must stay
small. Do not include event history, long context, source JSON, full score
breakdowns, or conversation provenance in the announce.

## Agent-Facing Render Contract

Memory signals shown to a loop agent must be sharp and compact. The agent needs
enough information to improve the answer and decide whether to create a new
memory, confirm an existing memory, or refine an existing memory. It should not
receive the full event trail by default.

Default search results and default `me:<memory_id>` reads should expose:

```text
id
memory
context, short/capped for search and full current context for explicit me:<id>
kind
visibility
status
labels
keywords
tier
confidence_score
importance_score
salience_score
confirmation_count
contradiction_count
updated_at
last_confirmed_at
revision
```

The announce hotset is smaller than search results. It should usually show only
`id`, `memory`, `tier`, a compact confidence/salience signal, labels, and an
updated timestamp.

Search results may include short context and count details so the agent can
choose between update and create:

```text
me:mem_abc
memory: User prefers concise technical answers.
context: Especially for engineering/debugging tasks.
labels: style, engineering
keywords: concise, technical
status: active visibility: user tier: 1
confidence: 0.91 importance: 0.72 salience: 0.88
confirmations: 4 contradictions: 0
updated: 2026-05-10 last_confirmed: 2026-05-10 revision: 5
```

Evidence events are lazy. They must not be included in default search results,
hotsets, or default `me:<id>` renders. Expose them only through an explicit
evidence view, for example:

```text
me:mem_abc?view=evidence
memory.get_evidence(id="mem_abc", limit=5)
```

The agent should request evidence only when provenance or conflict resolution is
actually needed: explaining why a memory exists, deciding between near
duplicates, resolving contradictions, retiring a memory, or materially rewriting
an important memory.

Recommended update flow for ReAct-style agents:

```text
1. Search existing memories by semantic/text/labels/keywords.
2. If a close memory exists:
     - confirm_memory(id) when the new signal simply reinforces it
     - record_memory(match_memory_id=id, event_type=refinement/user_edit/agent_observation)
       when the current memory should change
3. If no close memory exists, create a new memory.
4. Read evidence only for destructive, conflicting, or provenance-sensitive changes.
```

## Reconciliation

The normal write path is incremental. Reconciliation is a separate maintenance
operation for duplicate cleanup and semantic merges:

```text
candidate duplicates -> review/merge -> merged_into_id -> event trail retained
```

Do not run broad reconciliation inside every chat turn.

A good first scheduling model is similar to compaction: the turn may opportunely
request reconciliation, but a cross-replica lock ensures that only one worker
runs it for a user/bundle window. The job should be bounded and skip quickly if
another worker already owns the lease.

The reconciler agent and application design are documented in
[user-memories-reconcilation-README.md](user-memories-reconcilation-README.md).

---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-operational-README.md
title: "User Memories Operational Model"
summary: "Operational structure of SDK user memories: Postgres tables, event provenance, manual and conversational creation flows, query collection, compact rendering, and update maintenance."
tags: ["sdk", "memory", "operations", "postgres", "pgvector", "visibility", "provenance"]
keywords: ["memory structure", "memory provenance", "manual memory", "conversation_id", "turn_id", "memory events", "memory query collection", "memory updates", "render memory", "memory evidence"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-react-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-reconcilation-README.md
---
# User Memories Operational Model

This page shows how user memory is physically structured, how it is collected
for search/serving, and how it is maintained when new evidence arrives.

## Storage Structure

Postgres is authoritative. The SDK memory module writes three v1 tables in the
tenant/project schema.

```text
<schema>
  |
  +-- user_memory_entries
  |     one row per current memory
  |     aggregate state: memory text, status, tier, labels, scores, embedding
  |
  +-- user_memory_events
  |     append-only evidence trail
  |     many rows per memory entry
  |
  +-- user_memory_aliases
  |     labels / keywords / alternate retrieval hooks
  |
  +-- user_memory_maintenance_artifacts
  |     user-visible snapshot/reconciliation job index
  |
  +-- user_bundle_props
        generic user-scoped settings, including memory preferences
```

The older `<schema>.user_memory` table is a legacy primitive table with
`fact/strength/tags`. It is not the v1 operational memory store.

## Entry Row

`user_memory_entries` is the fast serving row.

```text
user_memory_entries
  id
  tenant, project, user_id, bundle_id
  canonical_key
  memory
  context
  kind
  status
  visibility, visible_to_user
  labels[], keywords[]
  search_text, search_tsv
  embedding VECTOR(1536), embedding_model
  evidence_count, update_count
  confirmation_count, contradiction_count
  positive_weight, negative_weight
  confidence_score, importance_score
  freshness_score, salience_score
  confirmation_rate, tier
  created_at, updated_at, last_event_at, last_confirmed_at
  source JSONB, metadata JSONB
  revision
  merged_into_id
```

This row is derived state. It can be served directly, but its history lives in
events.

## Event Rows

`user_memory_events` stores where the memory signal came from and why the
aggregate row changed.

```text
user_memory_entries.id
    |
    | 1:N
    v
user_memory_events
  id
  memory_id
  tenant, project, user_id, bundle_id
  conversation_id
  turn_id
  event_type
  signal_text
  context
  originator
  confidence, importance
  labels[], keywords[]
  source JSONB
  metadata JSONB
  created_at
```

For memories that emerge from a chat turn, `conversation_id` and `turn_id` are
stored on the event. The aggregate entry also keeps `source JSONB`, but the
event table is the audit trail because one memory may be supported by many
turns and conversations.

## Alias Rows

`user_memory_aliases` stores retrieval hooks.

```text
user_memory_aliases
  memory_id
  alias_type: label | keyword | future alias types
  value
  weight
  created_at
```

Labels are stable facets. Keywords are looser retrieval hooks.

## User Memory Preference

The user can disable memory globally for themselves. This is stored in the
generic user settings table, not in a memory-specific table:

```text
user_bundle_props
  user_id
  bundle_id="*"
  subsystem="memory"
  key="preferences"
  value_json.memory_enabled=false
```

The `subsystem` column is present so cross-system records in
`user_bundle_props` remain distinguishable. Existing deployments are upgraded
with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS subsystem TEXT NOT NULL DEFAULT
'bundle'`.

When `memory_enabled=false`:

```text
agent/tool memory search     blocked
agent/tool recent memories   blocked
agent/tool record memory     blocked
announce hotset              empty, marked disabled
widget inspect/export/delete allowed
widget create/edit/reconcile blocked until re-enabled
```

This preference is intentionally cross-bundle. A user asking the system not to
use their memory must not depend on which bundle provided the UI.

## Conversational Creation

When a memory emerges from a conversation, the tool/service passes a
`MemorySignal` with turn provenance.

```text
conversation turn
  |
  | user/assistant evidence
  v
MemorySignal
  memory="User prefers concise technical answers"
  event_type="confirmation" | "agent_observation" | "refinement" | ...
  source={
    conversation_id,
    turn_id,
    bundle_id
  }
  |
  v
record_signal()
  |
  +-- normalize labels/keywords/visibility
  +-- find existing row by match_memory_id/canonical_key
  +-- SELECT ... FOR UPDATE
  +-- append user_memory_events row with conversation_id/turn_id
  +-- recompute entry scores/tier/revision
  v
user_memory_entries + user_memory_events + user_memory_aliases
```

The event keeps the exact turn where the signal appeared. Later turns add more
events to the same entry instead of duplicating the memory.

## Manual User Creation

A user may create memory manually from a settings page, canvas, or bundle UI.
That memory is valid even when no conversation or turn exists.

```text
user memory UI / API
  |
  v
MemorySignal
  memory="Always send weekly reports to Telegram"
  event_type="user_edit" or "manual_create"
  originator="user"
  source={
    origin: "manual",
    surface: "memory-ui",
    bundle_id
  }
  conversation_id omitted
  turn_id omitted
  |
  v
record_signal()
```

Operational rule:

```text
conversation_id = "" when not tied to a conversation
turn_id         = "" when not tied to a turn
source JSONB    must explain the manual surface/origin
```

Manual rows still have tenant/project/user/bundle scope, labels, visibility, and
events. They can be confirmed, edited, retired, and reconciled like any other
memory.

Manual memory authoring should help the user create searchable, actionable
records:

```text
memory    compact trigger first, then the durable fact/preference/decision/rule; include the condition here
context   why this exists: provenance, motivation, examples, and disambiguation only
kind      compact type such as preference, fact, workflow_rule, communication_style
labels    stable categories for filtering and grouping
keywords  concrete words, synonyms, names, and likely future query terms
pinned    user override that promotes an active memory to tier 1
```

Authoring rule:

```text
memory = compact trigger first + rule
context = why this exists / provenance / examples only
```

Neutral example:

```text
memory:
  For engineering explanations, start with the practical impact before
  implementation details.

context:
  Created because prior summaries buried the user-visible impact. Examples:
  debugging notes, code reviews, implementation summaries, and release
  explanations.

kind:
  communication_style

labels:
  communication-style, technical-explanations

keywords:
  impact, implementation, debugging, code review, release notes
```

## Query Collection

A query collects candidates from the aggregate row first.

```text
MemorySearchRequest
  scope: tenant/project/user/bundle
  mode: hybrid | recent | important | confirmed | hotset | ...
  query text
  optional query embedding
  labels / keywords
  visibility filter
  status filter
  |
  v
SQL candidate fetch
  WHERE tenant/project/user
    AND status/visibility filters
    AND optional labels && requested labels
    AND optional keywords && requested keywords
  |
  +-- lexical branch when query text exists:
  |     normalized meaningful terms
  |     PostgreSQL text rank over search_text
  |
  +-- semantic branch when query_embedding exists:
        pgvector cosine similarity over embedding
  |
  v
union + de-duplicate candidates
  |
  v
Python rank blend
  semantic + text + labels
  + salience + importance + confidence + freshness + confirmation
  |
  v
threshold weak matches when query/labels/keywords were supplied
  |
  v
sort by score, tier, pinned, confidence, salience, importance, freshness, updated_at
  |
  v
MemorySearchResult[]
```

`recent_events` is the exception: it reads `user_memory_events` because the user
asked for the latest evidence events, not the current memory rows.

Widget list endpoints return `count`, `limit`, `offset`, and `has_more` for
pagination. For non-semantic filters the count is exact. Semantic search is
currently stateless: each page recomputes the query embedding and candidate
ranking. This avoids server-side cursor state for now, but a future stable
large-result workflow should persist a search result id/cursor instead of
recomputing each page.

## User Deletion

User delete is hard delete. The single-memory delete endpoint removes the
selected `user_memory_entries` row; the database cascades related
`user_memory_events` and `user_memory_aliases` rows.

Filtered bulk delete uses the same search criteria as export and then deletes
matching ids with explicit confirmation. It exists because user-controlled
memory is not complete unless a user can remove saved memory and the supporting
events.

Retire still exists as a maintenance/status action. It is not the same as user
delete: retired memories may remain in storage and can be included by explicit
status filters.

This is intentionally similar to the KB retrieval pattern:

```text
BM25/text recall + ANN recall -> candidate union -> score/rank -> optional threshold
```

Memory search does not currently run cross-encoder reranking. It uses memory
specific quality signals instead: confidence, importance, salience, freshness,
confirmation rate, tier, and pinned status.

## Rendering A Specific Memory

Rendering `me:<memory_id>` is a fast read path. It should use
`user_memory_entries` as the source of truth for the current memory state.
Reconstructing a memory by replaying all events is not part of the normal
render path.

Default render flow:

```text
me:<memory_id>
  |
  +-- parse memory id
  +-- derive authenticated scope: tenant/project/user_id/bundle policy
  +-- authorize visibility:
  |     canvas/user UI -> visible_to_user=true
  |     agent/tool     -> bundle policy may allow private/internal
  |
  +-- SELECT one aggregate row from user_memory_entries
  +-- render compact current state
```

Main query shape:

```sql
SELECT *
FROM user_memory_entries
WHERE id = $1
  AND tenant = $2
  AND project = $3
  AND user_id = $4
  AND merged_into_id IS NULL;
```

The default render should include the current memory row fields that help an
agent or user understand and safely update the memory:

```text
id
memory
context
kind
visibility, visible_to_user
status
labels[], keywords[]
tier
confidence_score, importance_score, salience_score
confirmation_count, contradiction_count
updated_at, last_confirmed_at
revision
merged_into_id when present
```

It should not include event history by default. Evidence is a separate explicit
view because events can be token-heavy and are usually not required to answer
the user or decide whether to confirm/update a memory.

Explicit evidence view:

```text
me:<memory_id>?view=evidence
memory.get_evidence(id=<memory_id>, limit=5)
```

Evidence query shape:

```sql
SELECT *
FROM user_memory_events
WHERE memory_id = $1
ORDER BY created_at DESC
LIMIT $2;
```

Use evidence only for provenance-sensitive operations: explaining why a memory
exists, resolving contradictions, reviewing near duplicates, retiring a memory,
or materially rewriting an important memory. Keep the event limit small and
bounded.

If a memory is merged, render should report the merge and point to
`me:<merged_into_id>` instead of silently hiding the old id.

## Announce Hotset Query

The announce hotset must also use only `user_memory_entries`. It is an indexed,
bounded query and should not call embeddings, search the timeline, or read
events.

Query shape:

```sql
SELECT id, memory, kind, labels, keywords,
       tier, salience_score, importance_score,
       confirmation_rate, confirmation_count,
       contradiction_count, updated_at, last_confirmed_at
FROM user_memory_entries
WHERE tenant = $1
  AND project = $2
  AND user_id = $3
  AND status = 'active'
  AND merged_into_id IS NULL
ORDER BY tier ASC,
         salience_score DESC,
         importance_score DESC,
         updated_at DESC
LIMIT $4;
```

Operational expectation: this should be a small indexed `LIMIT N` query. It
should normally complete in milliseconds on a healthy Postgres path and should
have a short timeout because it runs in the hot path of turn preparation.

Recommended index shape:

```sql
(tenant, project, user_id, status, tier,
 salience_score DESC, importance_score DESC, updated_at DESC)
```

## Maintenance Updates

All updates should produce events.

```text
confirm_memory(id)
  -> event_type=confirmation
  -> confirmation_count++, confidence/salience recalculated

record_memory(match_memory_id=id, event_type=user_edit)
  -> event_type=user_edit
  -> memory/context may change
  -> revision++

record_memory(match_memory_id=id, event_type=contradiction)
  -> negative_weight++, contradiction_count++
  -> confidence/tier may drop

retire_memory(id)
  -> event_type=retired
  -> status=retired
  -> tier=4
```

The aggregate row is the result of the event history plus scoring rules. Do not
silently overwrite a memory without adding an event.

Reconciliation uses the same aggregate rows. The LLM-facing reconciler packet is
bounded and defaults to current row state only:

```text
id, memory, bounded context, kind, visibility, status
labels, keywords, tier, aggregate scores
confirmation/update/contradiction counts
updated_at, last_confirmed_at, revision
```

Raw evidence events are not part of the default reconciliation packet. Fetch a
small explicit evidence view only for conflict-sensitive review.

## Concurrency

Multiple conversations for the same user can run at the same time.

```text
worker A                         worker B
  |                                |
  | record same memory             | record same memory
  v                                v
canonical_key unique index:
  (tenant, project, user_id, canonical_key)
  WHERE merged_into_id IS NULL
  |
  v
one row wins, the other appends evidence to the locked row
```

The database transaction and `SELECT ... FOR UPDATE` are the correctness layer.
Redis is not required for normal writes.

## Visibility

```text
visibility: user | owner | public  -> visible_to_user=true
visibility: private | internal     -> visible_to_user=false
```

User-facing widgets and canvas surfaces must filter `visible_to_user=true`.
Assistant tools may read private/internal rows only when bundle policy allows it.

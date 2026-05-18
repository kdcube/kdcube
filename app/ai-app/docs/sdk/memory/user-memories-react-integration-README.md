---
id: ks:docs/sdk/memory/user-memories-react-integration-README.md
title: "User Memories ReAct Integration"
summary: "How SDK user memories integrate with bundle configuration, the memory widget, ReAct announce hotsets, hybrid search, explicit reads, and future write tools."
tags: ["sdk", "memory", "react", "announce", "widget", "hybrid-search", "rendering"]
keywords: ["memory announce", "memory widget", "memory hotset", "hybrid memory search", "me memory id", "react memory integration", "memory rendering"]
see_also:
  - ks:docs/sdk/memory/how-react-remembers-README.md
  - ks:docs/sdk/memory/user-memories-overview-README.md
  - ks:docs/sdk/memory/user-memories-operational-README.md
  - ks:docs/sdk/memory/user-memories-reconcilation-README.md
---
# User Memories ReAct Integration

This page describes how durable user memories surface inside chat bundles,
especially ReAct bundles. It replaces ad hoc journal notes with a stable SDK
contract.

## Integration Flow

```text
bundle config
  |
  +-- memory.enabled
  |     master switch
  |
  +-- memory.widget.enabled
  |     user-facing CRUD surface for memories
  |
  +-- memory.announce.enabled
  |     inject compact memory hotset into ReAct announce
  |
  +-- memory.tools.enabled
        future read/search/write tools for agents
```

Current safe phase:

```text
user creates/edits memories in widget
  |
  v
Postgres user_memory_entries/events/aliases
  |
  +-- widget search/list/detail
  |
  +-- ReAct announce hotset, if enabled
```

In this phase, the user widget is the primary write surface. ReAct can see
memories through announce when configured, but conversational write tools should
remain disabled until memory-write policy is explicitly enabled.

## Full Bundle Config

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

Scope filters:

```text
current_bundle
all_user_memories
global_only
current_bundle_or_global
```

The widget should default to `current_bundle`, with an optional user-controlled
switch to `all_user_memories` when `allow_all_user_memories=true`.

## Memory Widget

The memory widget is a user-facing surface over durable memories. It should show
only memories that are safe for the user to inspect:

```text
visible_to_user = true
status default = active
scope default = current_bundle
```

The widget supports:

```text
list/search memories
view details and compact evidence
enable/disable memory use for the current user across bundles
create memory
edit memory
confirm memory
delete memory and related events
retire memory when preserving inactive rows is desired
pin/unpin memory
filter by scope/status/tags/keywords
paginate results
```

Disabling memory is a user-global preference. The widget still lets the user
inspect, export, delete, and re-enable saved notes, but create/edit/pin/confirm,
reconciliation, snapshots, announce hotset, and SDK memory tools are blocked
while `memory_enabled=false`.

User-authored memory fields:

```text
memory    compact trigger first, then the durable fact/preference/decision/rule; include the condition here
context   why this exists: provenance, motivation, examples, and disambiguation only
kind      compact type
labels    stable categories for grouping/filtering
keywords  concrete search triggers, aliases, names, and likely future terms
pinned    user override that promotes an active memory to tier 1
```

Neutral example:

```text
memory:
  For engineering explanations, start with the practical impact before
  implementation details.

context:
  Created because prior summaries buried the user-visible impact. Examples:
  debugging notes, code reviews, and implementation recaps.

kind:
  communication_style

labels:
  communication-style, technical-explanations

keywords:
  impact, implementation, debugging, code review, release notes

pinned:
  yes
```

Important authoring rule:

```text
memory = compact trigger first + rule
context = why this exists / provenance / examples only
```

## Hybrid Search

Memory search uses hybrid retrieval when query text is provided:

```text
query text + optional embedding
  |
  +-- lexical recall:
  |     normalized meaningful terms
  |     PostgreSQL text search over memory/context/labels/keywords
  |
  +-- semantic recall:
        pgvector cosine similarity over memory embeddings
  |
  v
union + de-duplicate candidates
  |
  v
rank candidate
```

The ranking blend includes:

```text
semantic similarity
lexical/text match
label/keyword match
salience
importance
confidence
freshness/recency
confirmation rate
```

Then results are sorted by:

```text
score
tier
pinned
confidence
salience
importance
freshness
updated_at
```

If embeddings are unavailable, search falls back to lexical recall plus the
same memory-quality ranking signals. Lexical recall drops filler words so a
query like:

```text
cities which i visit
```

searches meaningful terms such as:

```text
cities, visit
```

This lets relevant memories match without allowing unrelated one-row databases
to return every memory. `widget.search_min_relevance_score` is the final weak
match guard.

Widget pagination is stateless. The server accepts `limit` and `offset` and
returns `count` and `has_more`. Exact count is available for normal filters.
Semantic pages currently recompute the query embedding and ranking per request;
if a workflow requires a stable large semantic result set, add a server-side
search cursor/result id.

## Announce Hotset

When enabled, ReAct receives a small memory block during turn preparation. It is
durable user context, not chat history.

If the user disables memory use, announce returns an empty hotset and records a
non-fatal disabled marker. The turn continues without durable memory context.

Example shape:

```text
[USER MEMORY HOTSET]
  enabled: true
  policy: read-only durable user memory; current user message and visible turn context override memory if they conflict.
  use: consult these only when relevant; do not restate them unless they affect the answer.
  format: memory text carries the trigger+rule; context is why/provenance/examples only.
  scope_filter: current_bundle
  memories: showing 1 of 1
  - me:mem_abc123 bundle=example@1-0 tier=1 pinned=true confidence=0.95 salience=0.88 updated=2026-05-15T14:20:00Z labels=[communication-style, technical-explanations]
    For engineering explanations, start with the practical impact before implementation details.
    context=Created because prior summaries buried the user-visible impact. Examples: debugging notes, code reviews, implementation recaps.
```

Announce rendering rules:

```text
memory text is capped
context is capped
only a few labels are shown
keywords are not normally shown in announce
events/evidence are not shown
source JSON is not shown
```

Keywords remain important because they are used for search and ranking, even
when they are not rendered in the announce hotset.

## Explicit Memory Reads

Planned ReAct behavior:

```json
{
  "tool_id": "react.read",
  "params": {
    "paths": ["me:mem_abc123"]
  }
}
```

Expected render:

```text
[USER MEMORY]
path: me:mem_abc123
id: mem_abc123
bundle_id: example@1-0
kind: communication_style
status: active
tier: 1
pinned: true
confidence: 0.95
salience: 0.88
importance: 0.9
updated_at: 2026-05-15T14:20:00Z

memory:
When explaining a technical change, start with the practical impact before implementation details.

context:
Applies to debugging notes, code reviews, and implementation summaries.

tags:
communication-style, technical-explanations

keywords:
impact, implementation, debugging, code review, release notes

evidence:
events=2 confirmations=1 contradictions=0
```

`me:<id>` should return the full current memory record, not just text. Evidence
history should be a separate explicit path:

```text
me:mem_abc123.events
```

Discovery should not use `react.read`. If the memory is not already visible in
announce, the agent should use memory search first. Once it has concrete memory
ids, it can read specific ids in full.

Implementation work for `me:`:

```text
add me: to react.read docs and dedup prefixes
add runtime_ctx.memory_read_fn
resolve me paths in handle_react_read
enforce authenticated user, visibility, and configured memory scope
render compact current state by default
render bounded evidence only through explicit events/evidence view
```

## Agent Policy

ReAct instruction block should be conditional on `memory.enabled=true`.

Recommended wording:

```text
Memory is durable user context, not turn history.
Use memory only when it helps the current task.
Current user instructions override memory.
Visible turn context overrides stale or conflicting memory.
Use react.memsearch for prior conversation/timeline recovery.
Use memory search for durable user-visible facts, preferences, decisions, anchors, specs, milestones, and state.
Do not write memory unless the user explicitly asks, or memory-write policy allows it.
```

Current recommended rollout:

```text
1. read-only announce hotset
2. read-only memory search/read tools
3. explicit user-driven writes
4. implicit agent observations
5. reconciler proposals with transactional application
```

## Reconciliation Boundary

Normal turns should not run broad memory cleanup. Reconciliation is a bounded
maintenance process for duplicates, stale weak memories, and contradictions.

The reconciler should propose actions, not mutate directly. Application remains
transactional and auditable in service code.

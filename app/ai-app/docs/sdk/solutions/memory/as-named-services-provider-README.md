---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/as-named-services-provider-README.md
title: "Durable Memory as a Named-Service Provider"
summary: "The `mem` named service: durable user memory exposed as a schema-bearing named-service realm — its read and write operations, its memory-record object kind, the ontologic mutation dialect (update_strategy, {add,remove} deltas, dedup_key, authoritative edits), and how the provider derives authorization from its realm rather than from platform roles."
status: draft
tags: ["sdk", "solutions", "memory", "named-service-provider", "mem", "memory-realm", "ontologic-tools"]
updated_at: 2026-06-26
keywords:
  [
    "mem namespace",
    "durable user memory",
    "memory.record",
    "MemoryNamedServiceProvider",
    "memory named service",
    "memory realm",
    "user-memories app",
    "upsert_object memory",
    "update_strategy",
    "add remove delta",
    "dedup_key",
    "authoritative edit",
    "named_services.search_objects",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/ontologic-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/as-named-service-provider-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
---
# Durable Memory as a Named-Service Provider

Durable user memory is one of the user's **memory realms** — the facts,
preferences, and decisions worth keeping across conversations. The `mem`
named-service provider makes that realm operable the same way `cnv` exposes
context boards and conversation search exposes past conversations. When you need
to recall what the user saved or told you, `mem` is the realm you read; when
something is worth keeping long-term, it is the realm you write.

The provider lives at
`sdk/context/memory/named_service.py` (`MemoryNamedServiceProvider`), backed by
the durable store in `sdk/context/memory/store.py`. It registers under the `mem`
namespace, owns the `memory.record` object kind, and answers to the canonical ref
`mem:record:<memory_id>` (older `me:<id>` and `mem:<id>` refs are accepted as
aliases).

The realm owner is the **`user-memories@2026-06-26` app** — the app that
service-provides `mem`. A consuming app connects to the provider as a pure
consumer; it does not own or embed the store. The provider's published intro
(`MEMORY_NAMESPACE_INTRO`) carries the realm framing into every roster that lists
it: durable user memory, searched and read for relevant context, written when
something should be kept, alongside the user's context boards (`cnv`) and past
conversations.

Registration and cross-process discovery follow the same path as every other
provider — the decorator metadata on `MemoryNamedServiceProvider` lets the
discovery registry pick it up. See the discovery doc.

## The object: `memory.record`

The public object is the durable memory record:

```text
object_kind: memory.record
canonical ref: mem:record:<memory_id>
search scopes: mem, mem:record
```

The primary field is `memory` (the concise saved note); `context` records why
the memory exists or when it applies. `kind` is an open vocabulary with known
values (`fact`, `preference`, `decision`, `constraint`, `communication_style`,
`anchor`, `spec`, `milestone`, `state`). `labels` and `keywords` are facets used
for filtering, retrieval, and UI chips. Evidence-derived signals — status,
confidence, importance, salience, tier, confirmation/contradiction counts — ride
along on the record for ranking and display. Read
`named_services.object_schema(object_kind="memory.record")` for the concrete
field contract and search filters.

Memory **events** (the append-only evidence trail) are related data, not a
named-service object kind. A reader that needs provenance requests the parent
record with `include=["events"]`; the returned events are embedded, not openable
or searchable `mem:event` objects.

## Operations

The provider offers the standard ontologic operations over the realm. Reads:

| Operation | What it does |
| --- | --- |
| `object.search` | Hybrid lexical/semantic search over memory text, context, labels, and keywords. Filters include `mode`, `origin`, `labels`, `keywords`, `kind`, `status`, and the scoring knobs. |
| `object.list` | Browse recent records (defaults to `mode=recent`). |
| `object.get` | Fetch one record by ref; `include=["events"]` adds embedded provenance. |
| `provider.about` | The realm catalog: kinds, search scopes, action vocabulary, and a schema hint. |
| `object.schema` | The exact `memory.record` contract — fields, defaults, and search filters. |

Writes:

| Operation | What it does |
| --- | --- |
| `object.upsert` | Create a new memory, or — with an `object_ref` — edit an existing one. |
| `object.delete` | Hard-delete a record (cascades events/aliases), or `mode=retire`/`archive` for a status transition. |
| `object.action` (`confirm`) | Add a confirming evidence event, raising salience/confidence. |
| `object.action` (`retire`) | Retire a record by reference. |

The model-callable surface composes through the generic named-service tools, for
example:

```text
named_services.search_objects(namespace="mem", query="email preferences",
                              filters={"mode": "hybrid"})
named_services.upsert_object(namespace="mem", object_json={"memory": "<note>"})
named_services.object_action(namespace="mem", object_ref="mem:record:<id>",
                             action="confirm")
```

`object.get` is implemented (pull policies, UI resolvers, and canvas/chat object
actions need it) but a consuming app does not have to expose it as a
model-callable tool; the preferred exact-read path is `react.pull(["mem:record:<id>"])`.

## The mutation dialect

Writing memory is "satisfy the `memory.record` schema," and it follows the same
ontologic mutation rules as every other realm. The single source for that
dialect is the ontologic-tools doc; the points that matter for `mem`:

- **`update_strategy` on `labels`/`keywords`.** Both collections are
  `replace`-strategy: a bare list on update swaps the whole list (send the full
  set), and omitting the field entirely preserves the existing one.
- **The `{add, remove}` delta.** Either collection also accepts a
  `{"add": [...], "remove": [...]}` delta for incremental edits — drop one label
  without re-sending the list. Removes apply before adds.
- **`dedup_key`.** A stable dedupe/canonical key (`canonical_key`) lets concurrent
  identical writes converge on one record instead of spawning duplicates.

The **authoritative-edit** rule is the memory-specific point worth stating
plainly: when an `upsert_object` on an existing record carries an explicit
`memory` text, that text **becomes the canonical note** — an authoritative edit
wins. A passive observation (a write with no explicit memory text, or a
reinforcing signal) does not clobber a curated note. New records start as an
observation; an edit to an existing record is promoted to canonical text. This
is why a deliberate "update this memory to say X" reliably replaces the note,
while background reinforcement only strengthens it.

## Authorization derives from the realm, not platform roles

The provider no longer reads the legacy `memory.tools.allow_write` config block.
It **always offers its write operations** as part of the realm surface; whether a
given consumer may call them is decided by that consumer's allow-list (which
operations it connects), not by the provider withholding writes.

Authorization is **not** platform roles or user types. A named-service provider
derives its authz from the underlying realm it owns. For the current local
transport there is **no enforcement** at the provider boundary: the operation set
is the realm's, and the identity travelling with a call is the seam that
authorization will key on. Today that identity is the session id; the direction
is to pass the full user context to the provider, so a provider that owns a
per-user realm can authorize each call against the caller's identity. The model
is "the realm owns its operations; identity rides with the call; the provider
authorizes against its realm" — local transport simply hasn't switched on the
enforcement step yet.

## Evidence revisions are a user concern

Applying or dropping individual evidence entries on a memory is a **user (widget)**
operation, exposed on the memories-widget API (`memories_widget_evidence_apply`
and `memories_widget_evidence_delete`), not on the agent's named-service surface.
The agent's write surface is upsert-only (plus `confirm`/`retire`); it adds
evidence by recording signals, never by hand-editing the evidence ledger. For the
user-facing evidence and revision UI, see the
[memory widget solution](memory-widget-solution-README.md).

## Calling it

Once an app service-provides `mem`, it is operable through the standard
named-service tools, and the realm shows up in a connected agent's roster with
the published intro. The mutation dialect, the nine generic tools, and the
schema `tools` block are documented once in the
[ontologic tools](../../namespace-services/ontologic-tools-README.md) doc; the
storage, search modes, and scoring internals of the realm are in the
[user memories overview](../../memory/user-memories-overview-README.md).

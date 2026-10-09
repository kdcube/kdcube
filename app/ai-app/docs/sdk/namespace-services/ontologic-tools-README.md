---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/ontologic-tools-README.md
title: "Namespace Services: Ontology-Guided Tools"
summary: "How generic named-service tools operate a provider realm through a lightweight operational ontology and schema-declared affordances."
status: current
tags: ["sdk", "namespace-services", "ontology-guided-tools", "agents", "schema", "affordance"]
updated_at: 2026-10-02
keywords:
  [
    "operational ontology",
    "ontology-guided tools",
    "ontologic tools",
    "schema satisfaction",
    "affordance",
    "object schema",
    "provider about",
    "domain selector",
    "provider-bounded search",
    "provider translation",
    "upsert_object",
    "object_action",
    "update strategy",
    "realm",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/object-ref-presentation-and-actions-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-service-README.md
---
# Namespace Services: Ontology-Guided Tools

A named-service provider publishes a lightweight **operational ontology** for
its realm: the object kinds, identities, relationships, and constraints that
define what exists. A small set of generic model-facing tools operates over
that model. Provider-declared selectors, bounded actions, claims, request and
result contracts, and errors form the **operational affordance layer** that
tells an agent how it may use those objects.

A realm owner explicitly publishes a provider; an integrating app configures
which operations its agents may call; the agent then works the realm through
the same generic operators regardless of domain.
Publication lifecycle details belong to
[Discovery Registry](discovery-README.md), not this tool-model page.

This page is the conceptual surface: how the realm model, affordance layer, and
generic tools compose. It does not restate per-operation detail or consumer
wiring:

- per-operation provider contract (request/response fields, search scopes,
  streamed reads, block production): [Providers](providers-README.md);
- how operations become model-callable tools, `surfaces.as_consumer`
  allow-lists, `tool_traits`, catalog rendering, and the config→tool mapping
  table: [Named Service Tools](../tools/named-services-tools-README.md);
- the runtime context boundary (who owns what across the call):
  [Clients](clients-README.md);
- the realm participation contract:
  [Ecosystem Component](../solutions/ecosystem-component/ecosystem-component-README.md).

## The Operational Ontology

A realm's operational ontology describes its **schema-bearing object kinds**,
their identities, their relationships, and the constraints that make an object
valid. The agent learns the provider's domain model through this vocabulary and
operates it through schema-declared selectors and operations:

```text
realm operation =
    choose a declared object kind
  + resolve or construct an object
  + perform a schema-declared read, mutation, or bounded action
```

Every callable use case ("file a bug", "attach a report", "open the editor") is
attached to a declared object kind or provider capability. It becomes a read,
mutation, or bounded action with a documented input and result contract.
`object_action` is a generic operator, not an unrestricted provider-API proxy:
the action name and payload must be declared by the provider and allowed by the
consumer configuration and current request policy.

Satisfaction is **recursive**. A schema may require a typed link to another
kind. To provide that link the agent reads the linked kind's schema, resolves or
creates an object of that kind, and supplies its `object_ref`. Required-input
resolution therefore walks the kind graph by reading schemas, not by guessing
URIs.

The ontology and affordance layer are related but different:

```text
operational ontology =
    object kinds + identities/refs + typed relationships + constraints

operational affordance layer =
    selectors + declared operations + input/result contracts
  + claims + errors + delivery choices
```

The schema's `tools` block maps each declared operation to one generic operator
and its arguments. Only published operations can be invoked through the
named-service surface. This keeps the model-facing catalog stable while each
realm retains its own vocabulary and policy.

### A Schema Is the Realm's Language

An object schema connects the realm's domain model to its executable interface:

- **object kinds, refs, and relations** define things a user recognizes and how
  they connect;
- **selectors** explain how to find the intended thing;
- **operations and actions** say what can happen to it;
- **fields and claims** define the exact bounded request;
- **results and errors** make the outcome and the next valid step visible.

Here, meaning is **domain meaning**, not a promise of semantic search over the
provider's objects. A realm can expose semantic object search when its provider
supplies it. Otherwise it exposes the provider-backed search and explicit
predicates it can evaluate over a bounded provider response. KDCube does not
build an index over the provider's object space.

Capability discovery is a different search surface. `object_schema(query=...)`
searches the provider's schema declarations: catalog labels, descriptions,
keywords, object kinds, and operation ids. Those declarations are app code,
not user/provider objects, so KDCube can index them in shared bundle storage.
The request may ask for lexical, semantic, or hybrid capability search; the
response reports the effective mode when semantic search is unavailable.

Provider endpoint names, raw request bodies, and call ordering stay behind the
provider. Opaque provider ids can be returned as handles, but the user should
not have to supply one. The adapter resolves a request such as "the second tab"
from document structure. It can resolve "my comment containing payment terms"
from provider-returned comment fields. A broader meaning-based match is
available only when the provider declares semantic search.

### A Large Provider Does Not Become 100 Top-Level Tools

Suppose a provider exposes 100 API endpoints. The provider adapter publishes a
domain model and a bounded action vocabulary rather than copying that endpoint
inventory into the agent's tool catalog:

- implementation endpoints can remain private adapter steps;
- several endpoint calls can implement one user-meaningful action;
- the provider publishes only the operations intended for agents;
- genuinely distinct agent actions remain distinct and separately authorized.

The adapter performs that domain reduction; the projector does not guess it.
If all 100 endpoints represent 100 intentionally distinct agent effects, the
provider still declares 100 action ids. It places them in a recursive catalog
of domain-owned subareas rather than one flat response. A catalog node exposes
only its child summaries and direct operations; a capability query returns a
short ranked set; the operation view discloses one selected payload contract.

A scalable provider keeps the agent's top-level catalog at nine generic
operators. `provider_about` supplies a shallow map of the realm, and
`object_schema` supplies the contract for the relevant kind or concrete ref.
That focused schema maps the required domain operation to a generic operator
such as `object_action`:

```text
agent catalog
  nine generic named-service tools
        |
        v
provider_about / object_schema()
  documents | editing | discussion | exports
        |
        v
object_schema(schema_path="/discussion")
  list | reply | resolve | reopen
        |
        v
object_schema(object_kind="docs.comment_thread",
              schema_operation="object.action:reply_comment")
        |
        v
object_action(action="reply", selector=...)
        |
        v
provider adapter
  comments.list -> resolve thread -> comments.reply
```

For a projection-enabled provider, the shared dispatcher applies the focus to
the provider's complete declared schema. The agent reads it progressively:

```text
object_schema(namespace=...)                         root catalog node
object_schema(..., schema_path="/discussion")        one nested catalog node
object_schema(..., query="reply to a comment",       ranked capabilities
              search_mode="hybrid")
object_schema(namespace=..., object_kind=...)        kind + operation summaries
object_schema(..., schema_operation="object.search") exact object-search contract
object_schema(..., schema_operation="object.action:reply")
                                                     exact action contract
```

The provider supplies a projection index that assigns refs, selectors,
operations, and actions to object kinds and may arrange those operations in a
catalog tree of arbitrary depth. The runtime validates complete coverage and
returns only the selected node or contract. Capability search results carry
the `catalog_path`, `object_kind`, and `schema_operation` needed for exact
expansion. `schema_view="full"` remains available for an explicit broad
inspection. Providers without a projection index keep their existing
full-schema behavior.

The provider-owning bundle prepares its projected capability catalog during
bundle load. With a bound embedding service it persists a compact shared
hybrid index in a timestamped generation whose hash covers the declaration and
embedding profile. Concurrent loaders resolve one shared generation. Its
default file-backed FAISS view is derived from declarations, FTS rows, and
cached vectors held in SQLite; the publishing bundle can explicitly select the
in-memory brute-force fallback. A successful newest-generation load retains
that generation and its immediate predecessor and removes older file families.
The first capability search heals retention once when bundle preload was
already marked complete. Without an embedding service, lexical matching runs
directly over the projection. Consumer agents query this provider-owned
surface; they do not build the index. The catalog contains only
provider-declared capabilities and never mirrors provider objects. The storage
and descriptor contract is owned by
[Namespace Services: Providers](providers-README.md).

A complete document realm can therefore present this domain model and
operational vocabulary:

```text
docs
  documents
  tabs
  comment threads
  replies
  exports

operations
  find / read / copy / export a document
  add / rename / delete / read a tab
  edit a selected tab
  list / create / reply / edit / delete comments
  resolve / reopen a comment thread
```

That does not imply one generic provider call per line. A request such as
"reply to my unresolved comment about payment terms" may require the adapter
to page through the selected document's comments, resolve the matching thread,
perform the provider mutation, and return the updated thread. A separate
request such as "append this to the second tab" resolves that tab from the
document structure. The agent sees one bounded domain action; the provider
owns the call sequence.

```text
user language
  "my unresolved comment about payment terms"
        |
        v
schema-declared selector + action
  docs.comment_thread + author=me + resolved=false + text_contains + reply
        |
        v
provider adapter
  resolve provider ids -> perform supported calls -> normalize result
        |
        v
provider API
```

The `selector` shape in this example is conceptual. A provider can declare
supported selection through search filters, an action payload, or another
schema-owned field. Named services do not impose one universal selector JSON
envelope; they require the provider to describe the supported shape and its
ambiguity behavior.

The schema is also a capability boundary. It advertises only behavior the
configured provider can perform. A preview-only or unavailable provider
feature is omitted or marked unavailable, and an attempted unsupported action
returns a precise capability error. The adapter must not silently replace a
more specific operation with a weaker one.

**Google Docs status:** the current named-service adapter resolves tabs by
title, literal title fragment, 1-based position, or hierarchy. It attaches a
new comment to quoted text or a table cell, and resolves one comment by literal
text, the commented text, author (`me` is supported), resolved state, scope, or
position. Exact provider ids remain available for callers that already hold
them. Ambiguous selectors return bounded candidates. Provider-side translation
and capability reporting are
specified in
[Providers](providers-README.md#domain-operations-and-provider-translation).
The shipped Docs, Sheets, Mail, and Slack adapters declare projection indexes
and recursive domain catalogs. Their `provider_about` and selector-free
`object_schema` responses carry the root node; path, query, kind, and
exact-operation requests reveal the next required contract without exposing
unrelated action payloads.

## The Tool Surface

The shipped model-facing surface contains nine generic operators. They are
domain-free; the realm supplies meaning through `provider_about` and
`object_schema`.

| Tool | Role in the surface |
| --- | --- |
| `provider_about` | The realm catalog: kinds, scopes, action vocabulary, and a query playbook. Read first when the rendered scope hints are not enough. |
| `object_schema` | Browse or search the provider's recursive capability catalog, inspect one kind, expand one exact operation, or explicitly request the full schema. |
| `list_objects` | Browse a collection with pagination. |
| `search_objects` | Find objects by query within a provider-declared scope. |
| `get_object` | Fetch one object by ref (live realm state). |
| `upsert_object` | Create or modify one object that satisfies a kind's schema. On update, a collection field accepts either a bare list (set/append per `update_strategy`) or a `{add, remove}` delta; objects use `patch`/`replace`; scalars are replace (set if provided, preserved if omitted). |
| `delete_object` | Destroy one object itself (the file/record everywhere it is used). Not a list-editing tool — to take an item off a list use that field's `{remove}` delta. |
| `object_action` | Run a schema-declared bounded action on an object (`preview`, `open`, `download`, or a provider-defined action). |
| `host_file` | Host a runtime file/ref into the realm and get back a realm-owned file ref to cite via `upsert_object`. |

Nine generic tools do not mean nine domain actions. They are the stable
invocation grammar. The provider schema supplies the domain action names and
contracts, and policy remains exact per published operation.

These nine tools and the schema `tools` block are **shipped**. For their exact
parameters and the config that exposes each one, see
[Named Service Tools](../tools/named-services-tools-README.md). The
`object_ref` opacity rule and provider-owned actions are in
[Object Refs, Presentation, And Actions](object-ref-presentation-and-actions-README.md).

Provider results stay client-neutral. They describe the object, its size and
shape, available inline data, cursors/ranges, and any signed artifact URL. They
do not prescribe client-local tools. A ReAct consumer may inspect a local
artifact with ReAct tools; an external MCP client may download the signed URL
and use its own file facilities. Mail, Slack, and Sheets follow this boundary.

### How They Compose

The tools form one navigation path from "I know nothing about this realm" to "I
mutated it correctly". Some client surfaces also expose
`provider_capabilities` between `provider_about` and `object_schema`; it reports
the provider-declared operations available in that deployment without changing
the progressive catalog/kind/operation model:

```text
provider_about /          discover the realm's root capability catalog
object_schema()
      v
object_schema(path)       browse one nested domain catalog
      |                   or
object_schema(query)      search capability declarations
      |
      v
object_schema(operation)  expand one exact executable contract
      v
list_objects /            find or fetch the concrete objects I will read or link
search_objects /
get_object
      |
      v
upsert_object /           operate the object: create/modify, delete, or run an
delete_object /           action. The schema told me which tool and which args.
object_action
```

The path is **root -> browse/search capabilities -> exact operation ->
find/operate objects**. The agent reads only the part of the realm required for
its next call. Kind inspection remains available when the task starts from a
known object kind or ref. An explicit full view is available for broad
inspection, but it is not the default for projection-enabled providers.

The same returned `object_ref` can also become a local agent artifact when the
ReAct consumer declares a provider-backed pull policy for that namespace. The
tools still discover the object; `react.pull` then asks the owner for exact
bytes and returns a local `conv:fi:` ref for `react.read`. The `sheets`
namespace is a worked example: search returns a `sheets:` ref, and pull
materializes a JSON spreadsheet snapshot. External MCP clients do not receive
these `react.*` tools. A turnless get can return a signed URL for the complete
snapshot, including beside selected A1 values returned inline. See
[ReAct Object Materialization](react-object-materialization-README.md).

### The Schema's `tools` Block Maps Affordances To Operators

The schema does not just describe fields — it literally names, per op, **which
tool to call and which args are required/optional**. This is the worked example
from the real tasks realm (`task.issue`):

```python
"tools": {
    "list":   {"tool": "named_services.list_objects",
               "required": {"namespace": "task"}},
    "search": {"tool": "named_services.search_objects",
               "required": {"namespace": "task:issue", "query": "<text>"}},
    "get":    {"tool": "named_services.get_object",
               "required": {"namespace": "task", "object_ref": "task:issue:<issue_id>"}},
    "create": {"tool": "named_services.upsert_object",
               "required": {"namespace": "task", "object_json": {"title": "<title>"}},
               "optional_object_json": ["description", "state", "assignee",
                                        "tags", "attrs", "attachments", "attachment_refs"]},
    "update": {"tool": "named_services.upsert_object",
               "required": {"namespace": "task", "object_ref": "task:issue:<issue_id>",
                            "object_json": {"title": "<new title>"}},
               "optional_object_json": ["description", "state", "assignee",
                                        "tags", "attrs", "attachments", "attachment_refs"]},
    "delete": {"tool": "named_services.delete_object",
               "required": {"namespace": "task", "object_ref": "task:issue:<issue_id>"}},
    "host_file": {"tool": "named_services.host_file",
                  "required": {"namespace": "task", "object_ref": "task:issue:<issue_id>",
                               "file_ref": "conv:fi:turn_<id>.files/<path> or a local runtime file path"},
                  "optional": ["filename", "mime", "description"],
                  "returns": "task:issue:attachment:<issue_id>/attachments/<attachment_id>/v<version>/<filename>"},
    "attach_hosted_refs": {"tool": "named_services.upsert_object",
                           "description": "Cite task-owned hosted attachment refs on the issue after host_file returns them.",
                           "required": {"namespace": "task", "object_ref": "task:issue:<issue_id>",
                                        "object_json": {"attachment_refs": [{
                                            "ref": "task:issue:attachment:<issue_id>/attachments/<attachment_id>/v<version>/<filename>",
                                            "filename": "<filename>", "mime": "<mime>"}]}}},
},
```

Reading this top to bottom reveals the executable affordance: the agent learns
that creating an issue is
`upsert_object(namespace="task", object_json={title})`, that updating adds
`object_ref`, that a file becomes a citation in two steps (`host_file` then
`upsert_object` with the returned ref), and that `attachment_refs` is a typed
link to the `task.attachment` kind. The generic operators and the schema's
domain vocabulary form the complete model-facing surface.

### Per-Field `update_strategy`

A collection (array) field in `upsert_object` accepts **either** a bare list
**or** a `{ "add": [...], "remove": [...] }` delta. `update_strategy` tells the
agent what the bare-list form does:

- **Arrays:** `append` (the bare list is added to the existing list) or
  `replace` (the bare list swaps the whole list). The `{add, remove}` delta is
  always an incremental edit regardless of strategy.
- **Objects:** `patch` (set the provided keys, keep the rest) or `replace`
  (overwrite the whole object).
- **Scalars** have no strategy — a provided value is set (replace), an omitted
  one is preserved.

Read the strategy before a bare-list update: it is the difference between
adding to a field and silently overwriting it. Omit a field to leave it
unchanged.

#### The `{add, remove}` delta

The delta form edits a list incrementally: removes are applied first, then adds.

- `add` appends the listed item(s).
- `remove` removes matching item(s) — by value for value-lists, by ref or
  `dedup_key` for ref-lists.

| Goal | What to send |
| --- | --- |
| Set / overwrite the whole list | a bare list (field is `replace`, or send the full intended value) |
| Add or remove some items | a `{add, remove}` delta |
| Replace one item | `add` it with a matching `dedup_key` (see below) |

#### `dedup_key` (per-parent supersede)

An `append` collection field may also declare a `dedup_key`. Adding an item
(bare or via `add`) whose key matches an existing one **within the same parent
object** **replaces/supersedes** it. So "replace one item" is just "add it again
with the same key" — there is no add-then-delete dance. Example: a task's
`attachments` keyed by `filename` — re-host the same `filename` and the new
version supersedes the old one on that issue.

#### Removing a collection item

Removal is **one way: the field's `{remove: [...]}` delta** — list the item(s)
to drop (by value for value-lists; by ref or `dedup_key` for ref-lists). For a
ref-list (e.g. `attachments`), `{remove}` **detaches** the item from this parent
object; a shared underlying object is preserved.

`delete_object` is **not** a list-editing tool. It **destroys the object
itself** — the underlying file/record everywhere it is used — and is a separate,
rarer operation, never the way to take an item off a list.

These bare-list / `{add, remove}` / `dedup_key` / removal defaults are also
part of the shared named-service agent guidance. The ReAct harness injects that
guidance directly; other agent integrations can expose the same provider
contract and tools without changing their semantics.

`update_strategy`, `dedup_key`, the `{add, remove}` delta, these removal rules,
progressive schema projection, recursive catalog browsing, and capability
search are **shipped**.

## Teach It To Humans Too

The schema teaches the MODEL; the same spec now also teaches the USER: the
capability picker renders the realm's declared presentation (purpose,
works-with, human labels per operation/action, object-kind one-liners) as a
service card the user understands and narrows. A realm is well-modeled when
both readers pass: the agent can work it from `about`/`object_schema`, and a
user can read the card and control it. Declarations in
[Providers — The Presentation Layer](providers-README.md); rendering in
[Per-User Agent Capabilities](../solutions/user-settings/capabilities-README.md).

## Provider Presentation Refinements

These conventions tighten the surface without adding domain tools.

### `about` as the human-oriented entry point

`provider.about` is realm-filled, so the realm owner can make it the agent's
entry point. Its projected schema already carries the root capability catalog.
Recommended presentation content adds:

- a concise purpose and vocabulary for the root catalog nodes;
- a **query playbook**: per common intent, a scope + filter template + example
  object query, and a short "how to query this realm" note.

This makes `about -> object_schema` a deterministic drill-down: the root names
the parts, and the agent browses a path, searches capability declarations, or
expands one exact operation. The same convention is stated for the consumer
side in
[Named Service Tools](../tools/named-services-tools-README.md) and
[Clients](clients-README.md).

### Deeper relationship projections (proposed)

Catalog, kind, exact-operation, and full views are shipped. Field subsets and
relationship traversal depth are possible future refinements for providers
whose one operation contract still contains a large linked-object graph. They
do not change the current rule: the exact operation contract is the normal
unit disclosed before a call.

---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/ontologic-tools-README.md
title: "Namespace Services: Ontologic Tools"
summary: "The named-service ontologic tools as one coherent model-facing surface for operating any realm by satisfying schemas."
status: design
tags: ["sdk", "namespace-services", "ontologic-tools", "react", "schema", "affordance"]
updated_at: 2026-06-25
keywords:
  [
    "ontologic tools",
    "schema satisfaction",
    "affordance",
    "object schema",
    "provider about",
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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/object-ref-presentation-and-actions-README.md
---
# Namespace Services: Ontologic Tools

The named-service **ontologic tools** are the single model-facing interface for
operating any realm. A realm owner registers a provider; an integrating app
configures which operations its agents may call; the agent then works one
realm through the same small, generic tool set regardless of domain.

This page is the conceptual surface: how the tools compose and why "operate a
realm" reduces to "satisfy a schema". It does not restate per-operation detail
or consumer wiring:

- per-operation provider contract (request/response fields, search scopes,
  streamed reads, block production): [Providers](providers-README.md);
- how operations become model-callable tools, `surfaces.as_consumer`
  allow-lists, `tool_traits`, catalog rendering, and the config→tool mapping
  table: [Named Service Tools](../tools/named-services-tools-README.md);
- the runtime context boundary (who owns what across the call):
  [Clients](clients-README.md);
- the realm participation contract:
  [Ecosystem Component](../solutions/ecosystem-component/ecosystem-component-README.md).

## The Ontologic Model

A realm is **schema-bearing objects**. The agent does not learn a bespoke API
per realm. It operates by **satisfying schemas**:

```text
to act in a realm = to create or modify an object that satisfies its schema
```

There is no generic "call" and no free-floating "use case". A use case ("file a
bug", "attach a report", "open the editor") is always one of: create an object,
modify an object, or run a schema-declared action on an object. The schema is
the contract; satisfying it is the operation.

Satisfaction is **recursive**. A schema may require a typed link to another
kind. To provide that link the agent reads the linked kind's schema, resolves or
creates an object of that kind, and supplies its `object_ref`. Required-input
resolution therefore walks the kind graph by reading schemas, not by guessing
URIs.

The **affordance** of a realm — everything the agent can do — is exactly:

```text
affordance =
    the catalog of kinds (what objects exist)
  + each schema's typed links (how kinds connect)
  + each schema's `tools` block (which tool + args satisfy each op per kind)
```

Nothing else is callable. If it is not a kind, a link, or a declared op on a
kind, it is not part of the realm's surface. This keeps the model's world small:
it only ever inspects schemas and satisfies them.

## The Tool Surface

The shipped ontologic tools are nine generic operations. They are domain-free;
the realm fills in meaning through `about` and `object.schema`.

| Tool | Role in the surface |
| --- | --- |
| `provider_about` | The realm catalog: kinds, scopes, action vocabulary, and a query playbook. Read first when the rendered scope hints are not enough. |
| `object_schema` | The exact contract for one kind/scope: fields, typed links, search filters, and the `tools` block that names the tool + args per op. |
| `list_objects` | Browse a collection with pagination. |
| `search_objects` | Find objects by query within a provider-declared scope. |
| `get_object` | Fetch one object by ref (live realm state). |
| `upsert_object` | Create or modify one object that satisfies a kind's schema. On update, check each non-scalar field's `update_strategy`: for `replace` send the full intended value; for `append`/`patch` send only the delta. Scalars are replace (set if provided, preserved if omitted). |
| `delete_object` | Delete or archive one object. |
| `object_action` | Run a schema-declared bounded action on an object (`preview`, `open`, `download`, or a provider-defined action). |
| `host_file` | Host a runtime file/ref into the realm and get back a realm-owned file ref to cite via `upsert_object`. |

These nine tools and the schema `tools` block are **shipped**. For their exact
parameters and the config that exposes each one, see
[Named Service Tools](../tools/named-services-tools-README.md). The
`object_ref` opacity rule and provider-owned actions are in
[Object Refs, Presentation, And Actions](object-ref-presentation-and-actions-README.md).

### How They Compose

The tools form one navigation path from "I know nothing about this realm" to "I
mutated it correctly":

```text
provider_about            discover the realm: kinds, scopes, action vocabulary
      |                   (catalog — what is here)
      v
object_schema(kind/scope) get the exact contract for the kind I need to satisfy
      |                   (by part — fields, typed links, the `tools` block)
      v
list_objects /            find or fetch the concrete objects I will read or link
search_objects /
get_object
      |
      v
upsert_object /           satisfy the schema: create/modify, delete, or run an
delete_object /           action. The schema told me which tool and which args.
object_action
```

The path is **catalog → schema (by part) → find → satisfy**. The agent reads
the realm down to the kind it must satisfy, never the whole realm at once.

### The Schema's `tools` Block Is the Affordance Surface

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
                               "file_ref": "fi:turn_<id>.files/<path> or a local runtime file path"},
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

Reading this top to bottom is the affordance: the agent learns that creating an
issue is `upsert_object(namespace="task", object_json={title})`, that updating
adds `object_ref`, that a file becomes a citation in two steps (`host_file` then
`upsert_object` with the returned ref), and that `attachment_refs` is a typed
link to the `task.attachment` kind. No domain-specific tool was invented; the
generic tools plus the schema's `tools` block are the whole surface.

### Per-Field `update_strategy`

Collection fields declare an `update_strategy` that tells the agent what
*providing* the field does:

- **Arrays:** `append` (add to the existing list) or `replace` (swap the whole
  list).
- **Objects:** `patch` (set the provided keys, keep the rest) or `replace`
  (overwrite the whole object).
- **Scalars** have no strategy — a provided value is set (replace), an omitted
  one is preserved.

Read the strategy before an update: it is the difference between adding to a
field and silently overwriting it.

## Improvements (PROPOSED — not shipped)

These tighten the surface without adding domain tools. They are conventions and
extensions, not currently shipped behavior.

### `about` as a navigable top-level catalog + query playbook

`provider.about` is realm-filled, so the realm owner can make it the agent's
entry point. **Recommended convention** (content guidance, not a new op):

- a **top-level catalog**: namespaces, a shallow list of kinds/scopes, and the
  action vocabulary — the kinds/scopes it lists are exactly the selectors the
  agent passes to a focused `object_schema`;
- a **query playbook**: per common intent, a scope + filter template + example
  query, and a short "how to query this realm" note.

This makes `about → object_schema` a deterministic drill-down: the catalog names
the parts, and the agent fetches one part at a time. The same convention is
stated for the consumer side in
[Named Service Tools](../tools/named-services-tools-README.md) and
[Clients](clients-README.md); it lives in `about` content, not in generic code.

### `object_schema` projection selectors

For a large realm, reading a whole schema is wasteful. **Proposed extension**:
projection selectors on `object_schema` — `kind` / `scope` / a field subset /
traversal `depth` — so the agent fetches exactly the slice it needs (e.g. just
the create-required fields of one kind, or one level of typed links). These are
proposed params, **not current params**; today the agent fetches by kind/scope
and reads the returned contract.

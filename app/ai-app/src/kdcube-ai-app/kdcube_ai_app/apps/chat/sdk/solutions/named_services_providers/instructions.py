from __future__ import annotations


NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS = """
[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]
Named services expose bridges to external namespace refs. A namespace service explains and operates the ref grammar, searchable scopes, object schemas, stories, attachments, events, actions, and mutations it supports; the ultimate owner of a ref may be another app, storage system, integration, or a system not visible to this agent. You may see namespace refs in conversation events, canvas drops, pins, or prior tool results. Such a ref is a HANDLE to an external namespace object, not a current-turn file path. The `named_services.*` tools operate on configured namespaces: discover namespace semantics, search/list refs, inspect schemas, mutate namespace objects, and host ReAct files into namespace refs.

The tool catalog is authoritative. A `named_services.*` tool may be used for the namespaces listed in that tool's `namespaces applicable` scope. For search, prefer provider search scopes rendered under that tool when present; the namespace argument is the search scope, so a scoped namespace searches objects in that provider-declared object space. If the rendered scope list or semantics are not enough, call `provider_about`.

ReAct starts each turn with a sparse local workspace. It can directly use only current-turn `conv:su:`, `conv:so:`, `conv:fi:`, and `conv:ar:` refs that were produced in this turn or explicitly materialized in this turn. Any other namespace ref is only a handle until it is materialized.

When a namespace ref or request appears, pick the visible path that fits the goal:

1. Inspect the concrete object content -> `react.pull(paths=[<ref>])` materializes the namespace ref into this turn's local `conv:fi:` workspace, then `react.read(<conv:fi:...>)` for deeper or ranged reading. This applies even when the object is JSON or markdown, not only binary files — the resolver chooses the materialized representation and MIME.
   - If `react.pull` fails (namespace not configured, access denied, or the resolver returns an error), surface that error and work only from what is visible in the event payload / tool results.

2. Understand an unfamiliar namespace -> `named_services.provider_about(namespace=...)`. It gives purpose, searchable ref scopes, refs/stories/attachments, and domain language.

3. Know exact object fields or search filters -> `named_services.object_schema(namespace=..., object_kind=... or object_ref=...)`. It gives the object body shape, search filter contract (`ret.extra.schema.search.filters`), and concrete tool recipes when available.

4. Discover objects when no exact ref is in hand -> `named_services.search_objects(namespace=..., query=...)` for text/semantic lookup, or `named_services.list_objects(namespace=..., ...)` for bounded browsing/pagination. Respect cursor/limit; avoid broad scans unless the user asks.

5. Run a provider verb on an object (send, forward, download, upload, ...) -> `named_services.object_action`. An action payload is ENCODED BY ITS PROVIDER: each namespace defines its own keys, value shapes, and file-carrying conventions, and no general API pattern or other namespace predicts them. Before your first action on a namespace in a turn, read its contract and build the payload only from keys that contract names:
   - `named_services.object_schema(namespace=...)` carries each action's payload keys, recipes, and object semantics;
   - `named_services.provider_about(namespace=...)` carries the same guidance for namespaces that serve no schema.
   After a state-changing action, read the result back and confirm it matches what you asked — counts, recipients, ids. A mismatch is a failure to investigate, not a success to report.

6. Create/update or delete (only when the tool is visible and scoped to that namespace) -> `named_services.upsert_object` for create/update, `named_services.delete_object` for delete/archive. After a mutation, treat the returned ref/revision/body as the source of truth.
   - Mutating collection (array) fields on a namespace object: a collection field in `upsert_object` accepts EITHER a bare list or a `{ "add": [...], "remove": [...] }` delta. These delta semantics are platform-level and hold across namespaces:
     - Bare list -> set/append per the field's `update_strategy`: `replace` overwrites the whole list with what you send; `append` adds the item(s) you send. Omit the field to leave it unchanged.
     - Delta `{add, remove}` -> incremental edit applied as removes first, then adds. `add` appends item(s); `remove` removes matching item(s) (by value for value-lists; by ref or `dedup_key` for ref-lists).
     - Replace ONE item -> add it with a matching `dedup_key` (e.g. an attachment keyed by filename); the new item supersedes the old one with that key. Do NOT add-then-delete.
     - Remove ONE item -> use the field's `{remove: [...]}` delta. This is the only way to take an item off a list. `delete_object` is NOT a list tool: it destroys the underlying object itself (e.g. the file everywhere it is used) and is never used to edit a list.
     - The field's exact `update_strategy`/`dedup_key`, and which fields exist at all, come from `named_services.object_schema` — read it before your first upsert on a namespace in a turn.

7. Send a ReAct/runtime file INTO a namespace service — the reverse of pull -> `named_services.host_file(namespace=..., object_ref=..., file_ref=<conv:fi:...>, ...)`. `react.pull` brings an external namespace ref into ReAct as a `conv:fi:` artifact; `host_file` sends your `conv:fi:`/runtime file to the namespace service so it creates or registers a namespace file ref. Hosting a file does NOT attach or cite it on a domain object. If the object schema supports attachments or file links, call `host_file` first, then cite the returned namespace ref in a separate `named_services.upsert_object` call according to that schema.

8. If a namespace/ref is visible but no pull path applies and no `named_services.*` tool lists that namespace, explain what is visible from the event payload and state that the runtime has not exposed a resolver/tool for deeper access.
""".strip()


__all__ = ["NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS"]

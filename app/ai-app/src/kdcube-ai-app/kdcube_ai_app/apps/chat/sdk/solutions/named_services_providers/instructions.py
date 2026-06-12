from __future__ import annotations


NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS = """
[NAMED-SERVICE TOOLS — EXTERNAL NAMESPACE BRIDGES]
`named_services.*` tools are runtime bridges from you to namespace-owned systems that live OUTSIDE your ReAct workspace. A namespace (for example `task` or `memo`) is owned by an external provider that holds its objects, stories, attachments, and events. You may see refs from those systems — like `task:...` or `memo:...` — in conversation events, canvas drops, pins, or prior tool results. Such a ref is a HANDLE into the external owner system, not a file in your workspace.

The tool catalog is authoritative. A `named_services.*` tool may be used ONLY for the namespaces listed in that tool's `namespaces applicable` scope; pass one of those as the `namespace` argument. Call each tool by its concrete tool id and parameters, e.g. `named_services.search_objects(namespace="task", query="...")`.

When an external namespace ref or request appears, pick the visible path that fits the goal:

1. Inspect the concrete object content -> `react.pull(paths=[<ref>])` materializes the external ref into an `fi:` artifact, then `react.read(<fi:...>)` for deeper or ranged reading. This applies even when the object is JSON or markdown, not only binary files — the provider chooses the materialized representation and MIME.
   - If `react.pull` fails (namespace not configured, access denied, or the provider returns an error), surface that error and work only from what is visible in the event payload / tool results.

2. Understand the namespace/service itself -> `named_services.provider_about(namespace=...)`. Use it as onboarding: what this namespace is, what object kinds it owns, what refs/stories/attachments it exposes, and what domain language to use.

3. Know exact object fields before a structured write -> `named_services.object_schema(namespace=..., object_kind=... or object_ref=...)`. It gives the object body shape and may include concrete tool recipes by tool id. Follow its field names and payload shape exactly.

4. Discover objects when no exact ref is in hand -> `named_services.search_objects(namespace=..., query=...)` for text/semantic lookup, or `named_services.list_objects(namespace=..., ...)` for bounded browsing/pagination. Respect cursor/limit; avoid broad scans unless the user asks.

5. Create/update or delete (only when the tool is visible and scoped to that namespace) -> `named_services.upsert_object` for create/update, `named_services.delete_object` for delete/archive. After a mutation, treat the returned ref/revision/body as the source of truth.

6. Send a ReAct/runtime file INTO a provider — the reverse of pull -> `named_services.host_file(namespace=..., object_ref=..., file_ref=<fi:...>, ...)`. `react.pull` brings a provider-owned ref into ReAct as an `fi:` artifact; `host_file` sends your `fi:`/runtime file to the provider so it creates a provider-owned file ref. Hosting a file does NOT attach or cite it on a domain object. If the object schema supports attachments or file links, call `host_file` first, then cite the returned provider ref in a separate `named_services.upsert_object` call according to that schema.

7. If a namespace/ref is visible but no pull path applies and no `named_services.*` tool lists that namespace, explain what is visible from the event payload and state that the runtime has not exposed a resolver/tool for deeper access.
""".strip()


__all__ = ["NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS"]

from __future__ import annotations


NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS = """
[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]
Named services expose bridges to external namespace refs. A namespace service explains and operates the ref grammar, searchable scopes, object schemas, stories, attachments, events, actions, and mutations it supports; the ultimate owner of a ref may be another app, storage system, integration, or a system not visible to this agent. You may see namespace refs in conversation events, canvas drops, pins, or prior tool results. Such a ref is a HANDLE to an external namespace object, not a current-turn file path. The `named_services.*` tools operate on configured namespaces: discover namespace semantics, search/list refs, inspect schemas, mutate namespace objects, and host ReAct files into namespace refs.

The tool catalog is authoritative. A `named_services.*` tool may be used for the namespaces listed in that tool's `namespaces applicable` scope. For search, prefer provider search scopes rendered under that tool when present; the namespace argument is the search scope, so a scoped namespace searches objects in that provider-declared object space. If the rendered scope list or semantics are not enough, call `provider_about`.

ReAct starts each turn with a sparse local workspace. It can directly use only current-turn `conv:su:`, `conv:so:`, `conv:fi:`, and `conv:ar:` refs that were produced in this turn or explicitly materialized in this turn. Any other namespace ref is only a handle until it is materialized.

When a namespace ref or request appears, pick the visible path that fits the goal:

1. Inspect the concrete object content -> `react.pull(paths=[<ref>])` materializes the namespace ref into this turn's local `conv:fi:` workspace, then `react.read(<conv:fi:...>)` for deeper or ranged reading. This applies even when the object is JSON or markdown, not only binary files — the resolver chooses the materialized representation and MIME.
   - A plain pull is for YOUR use; the user receives no file from it. When the user should get the pulled file itself as a download, pull that ONE exact file ref with `share=true`. When the file goes onward to a service (mail/slack attachment, `host_file`), keep the plain pull and pass the returned `logical_path`/`physical_path` to that action's file field.
   - If `react.pull` fails (namespace not configured, access denied, or the resolver returns an error), surface that error and work only from what is visible in the event payload / tool results.

2. Understand an unfamiliar namespace -> `named_services.provider_about(namespace=...)`. It gives purpose, searchable ref scopes, refs/stories/attachments, and domain language.

3. Know exact object fields or search filters -> `named_services.object_schema(namespace=..., object_kind=... or object_ref=...)`. It gives the object body shape, search filter contract (`ret.extra.schema.search.filters`), and concrete tool recipes when available. It is also the contract source for actions and upserts (see 5 and 6).

4. Discover objects when no exact ref is in hand -> `named_services.search_objects(namespace=..., query=...)` for text/semantic lookup, or `named_services.list_objects(namespace=..., ...)` for bounded browsing/pagination. Respect cursor/limit; avoid broad scans unless the user asks.
   Accounts: a namespace whose `object_schema` shows an `account_id` field (in the object body or in `ret.extra.schema.search.filters`) serves several connected accounts at once. Working with a particular account is two steps: LIST the namespace's connected accounts — each row pairs the machine half (`account_id`, `ref`) with the human half (`label`, plus the raw address / workspace / display name) — then TARGET the chosen account by passing that row's `account_id` (or `ref`) in the filter or payload key the schema names. Speak to the user only in the human name; when the request is about one particular account and the user has not named it, ask, listing the candidates by their human labels. Pass ids only from list rows — an id is opaque and comes from the table, never from memory or construction. A request that spans accounts ("search all my mail") may stay un-targeted; the platform fans out across the eligible accounts. A result that asks for an account choice carries the same candidate rows — resolve by the human label and resend with that `account_id`.

5. Run a provider verb on an object (send, forward, download, upload, ...) -> `named_services.object_action`. Before your FIRST `object_action` or `upsert_object` on a namespace in a conversation, read `named_services.object_schema(namespace=...)` for it — an action is a realm-defined named protocol: the schema names each action's exact payload keys, value shapes, and file forms, and no general API pattern or other namespace predicts them. The platform holds this order: an action or upsert sent before that namespace's contract has been read in this conversation returns a protocol notice naming the schema call to make — read the contract, then retry with the documented arguments. Build the payload only from keys the contract names:
   - `named_services.object_schema(namespace=...)` carries each action's payload keys, recipes, and object semantics;
   - `named_services.provider_about(namespace=...)` carries the same guidance for namespaces that serve no schema.
   Files travel in action payloads BY REF: put the file's workspace path (a `conv:fi:` logical path or the physical path a pull/exec returned) in the payload key the contract names for paths (e.g. `attachment_paths`, `file_path`), and the service reads the bytes itself. Keys carrying encoded content exist for clients that hold raw bytes outside a chat turn; inside a turn the path form is the correct one, and file content stays out of payload fields and out of your visible context.
   Files coming BACK ride the same by-ref rule: when an action result says a file was delivered as a file card (`download.encoding: "chat"`), the user already sees that card in the chat — mention it in words. Links you share with the user travel the same governed way; a download URL typed by hand into a message arrives broken, so no URL is ever constructed, guessed, or re-typed from memory.
   After a state-changing action, read the result back and confirm it matches what you asked — counts, recipients, ids. A mismatch is a failure to investigate, not a success to report.

6. Create/update or delete (only when the tool is visible and scoped to that namespace) -> `named_services.upsert_object` for create/update, `named_services.delete_object` for delete/archive. The contract-first rule in 5 covers `upsert_object`: read the namespace's `object_schema` before your first upsert in a conversation — it names which fields exist, each field's shape, and its `update_strategy`. After a mutation, treat the returned ref/revision/body as the source of truth.
   - Mutating collection (array) fields on a namespace object: a collection field in `upsert_object` accepts EITHER a bare list or a `{ "add": [...], "remove": [...] }` delta. These delta semantics are platform-level and hold across namespaces:
     - Bare list -> set/append per the field's `update_strategy`: `replace` overwrites the whole list with what you send; `append` adds the item(s) you send. Omit the field to leave it unchanged.
     - Delta `{add, remove}` -> incremental edit applied as removes first, then adds. `add` appends item(s); `remove` removes matching item(s) (by value for value-lists; by ref or `dedup_key` for ref-lists).
     - Replace ONE item -> add it with a matching `dedup_key` (e.g. an attachment keyed by filename); the new item supersedes the old one with that key. Do NOT add-then-delete.
     - Remove ONE item -> use the field's `{remove: [...]}` delta. This is the only way to take an item off a list. `delete_object` is NOT a list tool: it destroys the underlying object itself (e.g. the file everywhere it is used) and is never used to edit a list.
     - The field's exact `update_strategy`/`dedup_key`, and which fields exist at all, come from `named_services.object_schema`.

7. Send a ReAct/runtime file INTO a namespace service — the reverse of pull -> `named_services.host_file(namespace=..., object_ref=..., file_ref=<conv:fi:...>, ...)`. `react.pull` brings an external namespace ref into ReAct as a `conv:fi:` artifact; `host_file` sends your `conv:fi:`/runtime file to the namespace service so it creates or registers a namespace file ref. `file_ref` is the ref/path itself — the platform moves the bytes. Hosting a file does NOT attach or cite it on a domain object. If the object schema supports attachments or file links, call `host_file` first, then cite the returned namespace ref in a separate `named_services.upsert_object` call according to that schema.

8. If a namespace/ref is visible but no pull path applies and no `named_services.*` tool lists that namespace, explain what is visible from the event payload and state that the runtime has not exposed a resolver/tool for deeper access.
""".strip()


#: The KDCube named-services MCP door's tool naming (the surface served by
#: `kdcube-services` at `/public/mcp/named_services`). Pass as ``operations``
#: to :func:`named_services_bridge_instructions` so the teaching block names
#: the EXACT tools the agent has bound — a model does not reliably map
#: "read `object_schema`" onto a tool named `named_services_schema` on its own.
NAMED_SERVICES_MCP_DOOR_TOOL_NAMES: dict[str, str] = {
    "services_list": "named_services_list",
    "about": "named_services_about",
    "capabilities": "named_services_capabilities",
    "schema": "named_services_schema",
    "search": "named_services_search",
    "get": "named_services_get",
    "action": "named_services_action",
    "upsert": "named_services_upsert",
    "delete": "named_services_delete",
    "host": "named_services_host_file",
}

_BRIDGE_DEFAULT_OPERATIONS: dict[str, str] = {
    "about": "provider_about",
    "schema": "object_schema",
    "search": "search_objects",
    "list_objects": "list_objects",
    "action": "object_action",
    "upsert": "upsert_object",
    "delete": "delete_object",
    "host": "host_file",
}


def named_services_bridge_instructions(
    *,
    pull_tool: str = "pull_files",
    read_tool: str = "read_file",
    operations: dict[str, str] | None = None,
) -> str:
    """The transport-neutral named-services teaching block for bridged agents.

    For agents that reach named services through a bound tool surface (MCP,
    LangChain bindings, or any non-ReAct harness). ``operations`` maps
    operation roles to the agent's ACTUAL bound tool names (e.g.
    :data:`NAMED_SERVICES_MCP_DOOR_TOOL_NAMES` for the KDCube door) so the
    block teaches the exact names the model can call; without a mapping it
    teaches by bare operation name. Optional roles (``services_list``,
    ``capabilities``, ``get``, ``list_objects``) shape their sentences only
    when present. ``pull_tool``/``read_tool`` name the agent's own
    file-materialization tools (empty ``read_tool`` drops the read hint;
    empty ``pull_tool`` drops the materialization step).

    On this surface NOTHING enforces contract-first for the model (the ReAct
    relay's protocol notice does not exist here), so the block states that
    ownership plainly instead of promising a safety net.
    """
    ops = dict(_BRIDGE_DEFAULT_OPERATIONS)
    ops.update({k: str(v).strip() for k, v in (operations or {}).items() if str(v or "").strip()})
    about, schema = ops["about"], ops["schema"]
    search, action = ops["search"], ops["action"]
    upsert, delete, host = ops["upsert"], ops["delete"], ops["host"]
    services_list = ops.get("services_list", "")
    capabilities = ops.get("capabilities", "")
    get = ops.get("get", "")
    list_objects = ops.get("list_objects", "")

    if operations:
        tool_names = [t for t in (
            services_list, about, capabilities, schema, search, get,
            list_objects, action, upsert, delete, host,
        ) if t]
        vocabulary = (
            "Your bound tools for this surface: "
            + ", ".join(f"`{t}`" for t in tool_names)
            + ". The `namespace` argument selects the realm; use namespace values exactly as the service lists them."
        )
    else:
        vocabulary = (
            "Your bound tools carry the named-service operations. Match them by OPERATION NAME — "
            f"`{about}`, `{schema}`, `{search}`, `{list_objects or 'list_objects'}`, `{action}`, `{upsert}`, `{delete}`, `{host}` — "
            "whatever exact tool naming your binding uses. The `namespace` argument selects the realm; "
            "the tool's own documentation states which namespaces it applies to."
        )

    steps: list[str] = []
    if services_list:
        steps.append(
            f"1. Know what is served -> `{services_list}`. It is the source of truth for the namespaces this "
            "connection actually serves and their granted operations; when the user or the roster names a "
            "namespace you have not confirmed, list first."
        )
    steps.append(
        f"{'2' if services_list else '1'}. Understand an unfamiliar namespace -> `{about}(namespace=...)`. "
        "It gives purpose, searchable ref scopes, refs/stories/attachments, and domain language."
        + (f" `{capabilities}(namespace=...)` lists the provider-declared operations and object behaviors." if capabilities else "")
    )
    n = 3 if services_list else 2
    steps.append(
        f"{n}. Know exact object fields, search filters, action payloads -> `{schema}(namespace=..., object_kind=...)`. "
        "It is THE contract source for searches, actions, and upserts — including the search filter contract "
        "(`ret.extra.schema.search.filters`)."
    )
    find_tail = f", or `{list_objects}(namespace=..., ...)` for bounded browsing/pagination" if list_objects else ""
    get_tail = f" `{get}` reads one object when its exact ref is in hand." if get else ""
    list_op = f"`{list_objects}`" if list_objects else "the namespace's list operation"
    steps.append(
        f"{n+1}. Discover objects when no exact ref is in hand -> `{search}(namespace=..., query=...)` for "
        f"text/semantic lookup{find_tail}. Each namespace declares its OWN filters — read them from `{schema}` "
        f"before searching.{get_tail} Respect cursor/limit; avoid broad scans unless the user asks.\n"
        f"   Accounts: a namespace whose `{schema}` shows an `account_id` field (in the object body or in "
        "`ret.extra.schema.search.filters`) serves several connected accounts at once. Working with a particular "
        f"account is two steps: LIST the namespace's connected accounts with {list_op} — each row pairs the machine "
        "half (`account_id`, `ref`) with the human half (`label`, plus the raw address / workspace / display name) — "
        "then TARGET the chosen account by passing that row's `account_id` (or `ref`) in the filter or payload key "
        "the schema names. Speak to the user only in the human name; when the request is about one particular "
        "account and the user has not named it, ask, listing the candidates by their human labels. Pass ids only "
        "from list rows — an id is opaque and comes from the table, never from memory or construction. A request "
        "that spans accounts may stay un-targeted; the platform fans out across the eligible accounts."
    )
    steps.append(
        f"{n+2}. Run a provider verb on an object (send, forward, download, upload, ...) -> `{action}`. "
        f"CONTRACT FIRST: before your FIRST `{action}` or `{upsert}` on a namespace in a conversation, read that "
        f"namespace's `{schema}` — an action is a realm-defined named protocol: the schema names each action's exact "
        "payload keys, value shapes, and file forms, and no general API pattern or other namespace predicts them. "
        "NOTHING checks this order for you on this connection: an action sent without the contract produces payloads "
        "the service rejects — or, worse, accepts with the wrong meaning. Owning this order is on you: read the "
        "contract, then act, and build the payload only from keys the contract names.\n"
        "   Files travel in action payloads BY REF: put the file's reference (its conversation file link, or the "
        "local path your file tools report) in the payload key the contract names for paths, and the service reads "
        "the bytes itself. Inside a turn the ref/path form is the correct one; file content stays out of payload fields.\n"
        "   After a state-changing action, read the result back and confirm it matches what you asked — counts, "
        "recipients, ids. A mismatch is a failure to investigate, not a success to report."
    )
    steps.append(
        f"{n+3}. Create/update or delete (only when the operation is exposed for that namespace) -> `{upsert}` for "
        f"create/update, `{delete}` for delete/archive. The contract-first rule above covers `{upsert}`: the schema "
        "names which fields exist, each field's shape, and its `update_strategy`. After a mutation, treat the "
        "returned ref/revision/body as the source of truth.\n"
        f"   - Collection (array) fields in `{upsert}` accept EITHER a bare list or a `{{ \"add\": [...], \"remove\": [...] }}` "
        "delta; these semantics hold across namespaces:\n"
        "     - Bare list -> set/append per the field's `update_strategy`: `replace` overwrites the whole list; "
        "`append` adds the item(s). Omit the field to leave it unchanged.\n"
        "     - Delta `{add, remove}` -> incremental edit applied as removes first, then adds (`remove` matches by "
        "value for value-lists; by ref or `dedup_key` for ref-lists).\n"
        "     - Replace ONE item -> add it with a matching `dedup_key`; the new item supersedes the old. Do NOT add-then-delete.\n"
        f"     - Remove ONE item -> the field's `{{remove: [...]}}` delta — the only way to take an item off a list. "
        f"`{delete}` is NOT a list tool: it destroys the underlying object itself everywhere it is used."
    )
    steps.append(
        f"{n+4}. Send a local/workspace file INTO a namespace service -> `{host}(namespace=..., object_ref=..., "
        "file_ref=...)`. `file_ref` is the file's reference/path itself — the platform moves the bytes. Hosting a "
        f"file does NOT attach or cite it on a domain object: if the object schema supports attachments or file "
        f"links, `{host}` first, then cite the returned namespace ref in a separate `{upsert}` per that schema."
    )
    pull = str(pull_tool or "").strip()
    if pull:
        read_hint = f" (view it with `{read_tool}`)" if str(read_tool or "").strip() else ""
        steps.append(
            f"{n+5}. Inspect a namespace object's content locally -> materialize it by its reference with `{pull}`, "
            f"then read/process the local file{read_hint}. A plain materialization is for YOUR use; sending the file "
            "onward to a service goes through the contract-named payload key."
        )
    steps.append(
        f"{n+5 if not pull else n+6}. A call reporting missing grants or a forbidden operation names what needs "
        "consent — explain it to the user and relay any reason and connection link the error carries. When the "
        "result asks for an account choice, it carries the same account rows as the list — resolve by the human "
        "label (asking the user when they have not named the account) and resend with that `account_id`. Retrying "
        "blindly changes nothing."
    )
    steps.append(
        f"{n+6 if not pull else n+7}. If a namespace/ref is visible but no bound tool lists that namespace, explain "
        "what is visible from the event/tool payload and state that the runtime has not exposed deeper access."
    )

    return (
        "[NAMED SERVICES — NAMESPACE OBJECT OPERATIONS]\n"
        "Named services expose bridges to external namespace refs. A namespace service explains and operates the "
        "ref grammar, searchable scopes, object schemas, stories, attachments, events, actions, and mutations it "
        "supports; the ultimate owner of a ref may be another app, storage system, integration, or a system not "
        "visible to this agent. A namespace ref you see in conversation events, prior tool results, or user "
        "messages is a HANDLE to an external namespace object, not a local file.\n\n"
        + vocabulary
        + "\n\nHow to work a namespace:\n\n"
        + "\n\n".join(steps)
    )


__all__ = [
    "NAMED_SERVICES_MCP_DOOR_TOOL_NAMES",
    "NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS",
    "named_services_bridge_instructions",
]

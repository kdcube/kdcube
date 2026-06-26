---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/as-named-service-provider-README.md
title: "Conversation Search as a Named-Service Provider"
summary: "Conversation search as a (future) read-only named-service provider over the `conv` namespace: what it searches in the conversation memory realm, its conversation vs user scopes, and the explicit calling context (user_id, conversation_id, bundle_id, tenant/project-derived schema) the API requires so a public/site API can search a user's conversations by setting that context. Defined-but-not-yet-connected."
status: draft
tags: ["sdk", "solutions", "conversation", "named-service-provider", "search", "memory-realm", "conv"]
updated_at: 2026-06-26
keywords:
  [
    "conversation search",
    "conv namespace",
    "ConversationSearchContext",
    "run_conversation_search",
    "ConversationSearchNamedServiceProvider",
    "explicit calling context",
    "memory realm",
    "memsearch",
    "tenant project schema",
    "scope conversation user",
    "named_services.search_objects",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/search_service/search-model-service-README.md
---
# Conversation Search as a Named-Service Provider

Conversations are one of the user's **memory realms** — what was actually said
in chat, this conversation or across earlier ones. The `conv` named-service
provider makes that realm searchable the same way `mem` exposes durable
memories and `cnv` exposes context boards. It is **read-only**: conversations
are recovered, never written, through this namespace.

The provider lives at
`sdk/solutions/conversation/named_service.py`, backed by the orchestration in
`sdk/solutions/conversation/api.py`. The same API already powers the ReAct
`react.memsearch` tool, which is now a thin caller over it.

> Status: **defined-but-not-yet-connected.** The provider exists and is
> coherent, but it is intentionally not wired into any app/bundle yet. A bundle
> that service-provides `conv` registers it exactly like the memory provider —
> the decorator metadata lets the discovery registry pick it up.

## What it searches

The realm is *what was said* — three content types, all from the user's own
conversations:

| Content | What it is |
| --- | --- |
| User | The user's prompts and follow-ups. |
| Assistant | The assistant's replies and working summaries. |
| Attachment | The user's **uploaded** attachments, by their indexed summaries. |

Bot-produced files are **not** part of this realm — only the user's uploads are.
Results come back as turn-level recovery handles (paths the caller can read or
pull), not as the raw conversation transcript.

## Scopes

A search names which conversations it spans:

- **`conversation`** (default) — only the current conversation.
- **`user`** — the same user's other conversations as well, for
  cross-conversation recall ("last week we talked about…").

## The explicit calling context

The critical design point is that **identity is set by the caller**, not read
off ambient runtime state. `run_conversation_search(...)` takes a
`ConversationSearchContext` and a search backend, and reads no contextvars. This
is what lets a future public/site API search a user's conversations by *setting*
the context explicitly from request auth.

| Field | Role | Where the ReAct path gets it |
| --- | --- | --- |
| `user_id` | The user whose conversations are searched. Always a hard isolation filter. | `runtime_ctx.user_id` |
| `conversation_id` | The current conversation. Confines a `conversation`-scope search; anchors cross-conversation ref labeling in `user` scope. | `runtime_ctx.conversation_id` |
| `turn_id` | The current turn. Not a search filter — metadata callers use to label produced blocks. | `runtime_ctx.turn_id` |
| `bundle_id` | The app/bundle scope. The index filters by it when present. | `runtime_ctx.bundle_id` |
| `tenant` / `project` / `schema` | Provenance only. | `runtime_ctx.tenant` / `runtime_ctx.project` |

### Tenant/project isolation is the schema, not a filter

Tenant and project are **not** `WHERE` filters. Isolation is the Postgres
**schema name**, which is *derived from* tenant + project when the search
backend is constructed. The API does not select the schema — the backend handed
to it is already bound to one. The context carries `tenant`/`project`/`schema`
as provenance so a caller can build (or select) the right backend and the right
identity together.

So a public/site API supplies two seams to the provider:

- a **context factory** — turns the request's `NamedServiceContext` (request
  auth) into a `ConversationSearchContext`, and
- a **search backend factory** — yields a backend bound to the caller's
  tenant/project-derived schema.

The ReAct tool fills the same seams from the runtime: it builds the context with
`ConversationSearchContext.from_runtime_ctx(...)` and passes the live context
browser as the backend.

## Calling it

Once a bundle service-provides `conv`, it is searchable through the standard
named-service tools (see the named-services tools doc):

```text
named_services.search_objects(
  namespace="conv",
  query="<topic>",
  filters={"targets": ["user", "assistant", "attachment"], "scope": "user"},
)
```

Empty `query` with `ordinal` or a `from`/`to` window does a deterministic
catalog lookup instead of topic search.

Registration and cross-process discovery follow the same path as every other
provider — see the discovery registry doc.

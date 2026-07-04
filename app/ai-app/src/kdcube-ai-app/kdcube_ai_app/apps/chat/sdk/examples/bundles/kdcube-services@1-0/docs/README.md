---
id: kdcube-services@1-0/docs
title: "KDCube Services Design"
summary: "Design notes for the built-in KDCube services app."
status: active
tags: ["design", "mcp", "delegated-credentials", "connection-hub"]
---

# KDCube Services Design

## Role

`kdcube-services@1-0` is the place for KDCube-owned product surfaces that should
be exposed as normal bundle/proc surfaces.

It exists because external clients such as Claude should not call accidental
root platform endpoints. They should connect to a concrete service resource,
consent to concrete tools, and then call the proc-served bundle MCP endpoint.

## Boundary

Connection Hub owns:

- OAuth protocol adapter;
- dynamic/public client registration;
- consent UI;
- delegated credential storage;
- selected tool/grant records;
- authority/delegation metadata.

`kdcube-services@1-0` owns:

- KDCube service surfaces;
- tool schemas and descriptions;
- service-specific product logic;
- service-specific docs.

The proc MCP bridge owns:

- reading `surfaces.as_provider.mcp.<alias>.auth`;
- validating delegated credentials;
- checking the called tool against selected consent;
- checking required grants before dispatch.

## Modularity

Use this shape for every service family:

```text
services/<family>/
  __init__.py
  <operation>.py

surfaces/mcp/<family>.py
  build_<family>_mcp_app(...)

entrypoint.py
  @mcp(alias="<family>", auth_config="surfaces.as_provider.mcp.<family>.auth")
```

Use stateless streamable HTTP for FastMCP adapters served through the proc
bridge. The bridge invokes the bundle surface per request, so the MCP session
cannot depend on a process-local FastMCP session manager surviving between
`initialize` and `tools/list`.

Do not add service logic to `entrypoint.py`. Do not add per-tool authorization
checks inside service modules unless that check is truly domain-specific and
cannot be expressed as authority/grants in the descriptor.

## Current Implementation

The first service families are `conversations` and `named_services`.

```text
public/mcp/conversations
  -> conversations_export                         (bundle: MCP tool schema only)
  -> export_current_user_conversations            (SDK: sdk/solutions/conversation/mcp_export.py)
  -> ConversationExportService                    (SDK: sdk/solutions/conversation/export.py)
  -> ConversationReadService                      (SDK: sdk/solutions/conversation/read.py)
  -> pooled conversation materialization port
```

Conversation domain logic is **SDK-owned**. The direct MCP surface is a thin
transport adapter: it registers the `conversations_export` tool and calls
`sdk/solutions/conversation/mcp_export.py`, which wraps the SDK
`ConversationExportService` over the same `ConversationReadService` used by the
`conv` named service. There is no control-plane router/app-state dependency in
this path.

The default descriptor makes `conversations:read` delegable only by
`kdcube:role:super-admin`. That is policy, not bundle identity. Future user
services can be added to this bundle with less privileged delegability.

```text
public/mcp/named_services
  -> named_services_* tools
  -> NamedServicesMcpBridge
  -> Connection Hub grant-record namespace policy
  -> NamedServiceEndpoint
  -> owner bundle named_services() registry
```

Connection Hub resource metadata exposes named-service namespaces as consentable
boundaries. The outer MCP tools require `named_services:use`. The inner
namespace policy can require namespace-specific grants such as `memories:read`,
`memories:write`, `tasks:write`, or `canvas:write`; that policy is persisted
into the delegated credential grant record. This is intentionally two-layered:

```text
MCP resource consent
  -> can this external client use named-services tools at all?

namespace operation policy
  -> can this external client use mem/object.search or mem/object.get?
```

If an inner grant is missing, the MCP tool returns a structured
`delegated_consent_required` payload. That is the right provider-boundary
signal, but it is not equivalent to an HTTP OAuth challenge. Existing MCP
clients may not automatically restart consent from a tool result. For reliable
Claude flows, advertise the grants the resource will likely need during initial
Connection Hub consent.

### Conversation named service (`conv`)

This bundle registers the SDK-owned conversation provider
(`sdk/solutions/conversation/named_service.py`) in its `named_services()`
registry and publishes it through Redis discovery on `on_bundle_load`, so `conv`
is reachable through the `named_services` MCP surface. The provider is
SDK-owned; the bundle only wires a read/export service bound per request to the
caller's tenant/project.

```text
public/mcp/named_services
  -> conv namespace
  -> ConversationSearchNamedServiceProvider (SDK)
  -> ConversationReadService (SDK read/export facade)
```

Operations and scope:

- `object.list` — conversation summaries; `object.get` — one full conversation
  (`conv:conversation:<id>`); `object.export` — the `conversations_export`
  record family, user-scoped; `object.search` — search the conversation memory
  realm (turn-level recovery), backed by the SDK search backend over the shared
  search plumbing, bound per request to the caller's tenant/project.
- Default scope is the caller's own conversations. A selected-user scope
  (`filters.scope = {mode: "user", user_id}`) is the admin path.

Boundary policy (grant vocabulary) — enforced by Connection Hub / the managed
boundary, not by provider code. Suggested `named_services` namespace block for
the delegated credential grant record:

```yaml
named_services:
  namespaces:
    conv:
      label: Conversations
      authority_id: delegated_client
      tools:
        named_services_list:   {grants: [conversations:read]}
        named_services_get:    {grants: [conversations:read]}
        named_services_search: {grants: [conversations:read]}
        named_services_call:                       # object.export rides the generic caller
          operations:
            object.export:     {grants: [conversations:export]}
      # Selected-user (admin) scope additionally requires:
      #   conversations:read:any_user / conversations:export:any_user
```

Full tenant/project all-user bulk export is deliberately not a `conv.object.*`
operation; it stays a separate admin operation. The direct `conversations_export`
MCP tool remains available as the immediate migration path while `conv` matures.

## Why Not Root `/mcp`

KDCube MCP endpoints are bundle surfaces served by chat-proc. A root platform
`/mcp` endpoint combines protocol/auth/product logic in the wrong runtime and
cannot scale to multiple apps with different grants. The canonical shape is:

```text
Connection Hub OAuth consent
  -> delegated credential
  -> bundle/proc MCP resource
  -> service-specific tool dispatch
```

This keeps consent resource-specific and lets different bundles define their
own managed MCP surfaces.

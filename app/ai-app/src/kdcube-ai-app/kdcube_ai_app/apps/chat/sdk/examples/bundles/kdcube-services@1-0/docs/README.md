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

Do not add service logic to `entrypoint.py`. Do not add per-tool authorization
checks inside service modules unless that check is truly domain-specific and
cannot be expressed as authority/grants in the descriptor.

## Current Implementation

The first service family is `conversations`.

```text
public/mcp/conversations
  -> conversations_export
  -> ConversationExportService
  -> ControlPlaneDataSource
  -> conversation browser/store
```

The default descriptor makes `conversations:read` delegable only by
`kdcube:role:super-admin`. That is policy, not bundle identity. Future user
services can be added to this bundle with less privileged delegability.

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

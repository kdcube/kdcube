---
id: ks:docs/sdk/namespace-services/README.md
title: "Namespace Services"
summary: "Index and mental model for namespace-owning service providers, clients, object resolution, and bundle-to-bundle integration."
status: design
tags: ["sdk", "namespace-services", "providers", "clients", "resolvers", "bundles"]
updated_at: 2026-06-11
keywords:
  [
    "namespace services",
    "named service provider",
    "namespace owner",
    "object_ref",
    "service client",
    "object resolver",
    "bundle integration",
  ]
see_also:
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/namespace-services/clients-README.md
  - ks:docs/sdk/namespace-services/integration-README.md
  - ks:docs/sdk/events/namespaces-README.md
  - ks:docs/sdk/solutions/scene/scene-surface-registry-README.md
---
# Namespace Services

Namespace services are the SDK pattern for one bundle or subsystem to own a
semantic namespace and let other bundles, widgets, agents, jobs, and external
clients use that namespace without copying owner logic.

The first concrete use case is object resolution:

```text
task:issues/issue_123
  |
  | owned by task-tracker
  v
TaskIssueNamedServiceProvider
  |
  +-- object.search
  +-- object.get
  +-- object.action(open)
  +-- object.upsert / delete when allowed
```

The same provider can be consumed by canvas pins, chat context chips,
model-callable tools, API operations, MCP clients, Data Bus handlers, and
scheduled jobs. The client surface is transport-neutral; each runtime chooses
the adapter that fits the call path.

## Documents

| Document | Purpose |
| --- | --- |
| [Providers](providers-README.md) | Provider contract, operation vocabulary, auth context, and transport adapters. |
| [Clients](clients-README.md) | Client config, tool exposure, current resolver behavior, and client ids. |
| [Integration](integration-README.md) | Visual provider-host/client-bundle flow using task-tracker and versatile. |
| [Logical Reference Namespaces](../events/namespaces-README.md) | Foundational rules for `task:`, `mem:`, `cnv:`, `fi:`, `ks:`, and other refs. |

## Current Scope

The current implementation is generic enough for configured provider/client
integration:

- a provider bundle exposes a `named_service` API operation backed by a local
  `NamedServiceRegistry`;
- a client bundle configures `named_services.namespaces.<namespace>.provider`;
- canvas/chat object actions use a reusable resolver adapter;
- model-callable tools can be enabled per `clients.<client_id>.tools`;
- calls use the request-bound local operation bridge when inside the same
  KDCube runtime, preserving current tenant/project/user context.

The current implementation is not yet a global service discovery service. A
client bundle must be configured with the namespace provider it should call.
MCP and Data Bus are part of the provider capabilities vocabulary, but the
generic platform adapters are still integration work.

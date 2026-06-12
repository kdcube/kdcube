---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
title: "Namespace Services"
summary: "Index and mental model for namespace-owning service providers, clients, object resolution, and bundle-to-bundle integration."
status: design
tags: ["sdk", "namespace-services", "providers", "clients", "resolvers", "bundles"]
updated_at: 2026-06-12
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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
---
# Namespace Services

Namespace services are the SDK pattern for one bundle or subsystem to own a
semantic namespace and let other bundles, widgets, agents, jobs, and external
clients use that namespace without copying owner logic.

The first concrete use case is object resolution:

```text
task:issue:issue_123
  |
  | owned by task-tracker
  v
TaskIssueNamedServiceProvider
  |
  +-- object.search
  +-- object.get
  +-- object.schema
  +-- object.host_file when callers need provider-owned file refs
  +-- object.resolve for cheap URI-to-capabilities/metadata resolution
  +-- object.action(open/preview)
  +-- object.upsert / delete when allowed
  +-- block.produce / block.render when the namespace owns ReAct projection
```

Namespace parsing has one platform rule:

```text
task:issue:issue_123
│    └──── provider-owned owner key/subnamespace (`task:issue`)
└──────── routing namespace (`task`)
```

The host uses only the routing namespace to find the provider. The provider's
`event.resolve`, `object.get`, `object.schema`, and related operations decide
what the owner key and remaining URI tail mean.

The same provider can be consumed by canvas pins, chat context chips,
model-callable tools, API operations, MCP clients, Data Bus handlers, and
scheduled jobs. The client surface is transport-neutral; each runtime chooses
the adapter that fits the call path.

## At A Glance

Two bundles, one configured edge, no shared code:

```text
  PROVIDER bundle (owns task:)            CONSUMER bundle (shows task: refs)
 ┌────────────────────────────┐         ┌─────────────────────────────────┐
 │ NamedServiceProvider       │         │ canvas pin / chat chip / tool   │
 │   object.search/get/action │         │   object_ref "task:issue:42"   │
 │            ▲               │         │            │                    │
 │ named_services() registry │◀────────│  NamedServiceCanvasObjectResolver│
 │ @api("named_service") API │ endpoint│  (built from config, no API code)│
 └────────────────────────────┘         └─────────────────────────────────┘
        owner logic lives here       in-runtime bridge keeps tenant/user/project

  Provider registers in Named Service Discovery when loaded.
  Consumer config names the namespace and client policy; provider location is
  normally resolved from the Redis-backed discovery table at call time.
```

## Two Resolution Tiers

A canvas/chat surface resolves a pinned `object_ref` through one of two tiers,
chosen by namespace prefix:

| Tier | Namespaces | Resolver | Knows |
| --- | --- | --- | --- |
| Owned-concrete | `cnv:` `conv:` `mem:` (the surface's own) | the surface's own resolver | kind, preview, open — no cross-bundle call |
| Foreign-generic | another bundle's (`task:`) | `NamedServiceCanvasObjectResolver` | nothing local — opaque `object_ref`, capabilities from config, owner answers over the bridge |

The foreign-generic tier is **additive**: it registers after the concrete
resolvers and only fires for namespaces listed in the consumer bundle's
`surfaces.as_consumer` resolver config (empty by default), so it never shadows
owned namespaces. It replaces what used to be a hard "registered elsewhere"
handoff with a live cross-bundle call — strictly more reach for foreign refs,
zero change to owned-pin semantics.

## Documents

| Document | Purpose |
| --- | --- |
| [Providers](providers-README.md) | Provider contract, operation vocabulary, auth context, and transport adapters. |
| [Clients](clients-README.md) | Client config, tool exposure, current resolver behavior, and client ids. |
| [Integration](integration-README.md) | Visual provider-host/client-bundle flow using task-tracker and versatile. |
| [Logical Reference Namespaces](../events/namespaces-README.md) | Foundational rules for `task:`, `mem:`, `cnv:`, `fi:`, and other refs. |

## Current Scope

The current implementation is generic enough for provider/client integration
through Named Service Discovery:

- a provider bundle exposes a `named_services()` registry object and may also
  expose a `named_service` API operation backed by that registry;
- a provider bundle registers its providers into Redis-backed Named Service
  Discovery after its local prerequisites are ready;
- a client bundle configures `surfaces.as_consumer` for the model tools,
  event-source/pull policies, and resolver surfaces allowed to use that
  namespace;
- canvas/chat object actions use a reusable resolver adapter;
- namespace artifact refs can be materialized by `react.pull`; the backend
  rehoster calls the provider's `object.get` with `response_mode: stream`,
  receives a normal named-service response plus byte chunks, and writes those
  chunks into the ReAct `fi:` workspace under the current auth context;
- configured namespaces can register ReAct event sources such as
  `named_services.task`; URI-to-event-source routing calls the provider's
  lightweight `event.resolve` function, then block production delegates to the
  provider's `block.produce` operation;
- model-callable tools can be enabled per
  `surfaces.as_consumer.agents.<agent>.tools`;
- model clients can call `provider.about` and `object.schema` before they
  create/update/delete an object, so entity shape stays with the provider;
- model clients can call `host_file` when configured to create a provider-owned
  file ref from an agent/runtime artifact, then cite that ref through a
  provider-declared object mutation;
- canvas/chat clients only enable namespace resolution; concrete resolver
  actions remain provider decisions;
- same-KDCube calls prefer the `bundle_registry` transport, which calls the
  owning bundle's registry object directly under the current tenant/project/user
  context;
- provider discovery scope is part of the platform cross-runtime context room,
  so tools can resolve provider records after subprocess or ISO bootstrap
  without passing a live Redis object through the tool registry;
- `bundle_operation` remains available when the owner exposes only the API
  facade or when the call path intentionally wants the operation envelope.

Named Service Discovery is a provider index, not a one-namespace/one-bundle
map. More than one bundle may register providers for the same namespace. Each
provider advertises the operations, refs, and object kinds it supports; the
runtime chooses the provider per request.

MCP and Data Bus are part of the provider capabilities vocabulary, but the
generic platform adapters are still integration work.

> **Naming.** "Namespace services" is the concept. The SDK module that
> implements it is `kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers`
> (`NamedServiceProvider`, `NamedServiceRegistry`, `NamedServiceClient`,
> `NamedServiceCanvasObjectResolver`). They are the same thing, not two layers.

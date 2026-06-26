---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
title: "Namespace Services"
summary: "Index and mental model for namespace-owning service providers, clients, object resolution, and app-to-app integration."
status: current
tags: ["sdk", "namespace-services", "providers", "clients", "resolvers", "apps"]
updated_at: 2026-06-23
keywords:
  [
    "namespace services",
    "named service provider",
    "namespace owner",
    "object_ref",
    "service client",
    "object resolver",
    "app integration",
    "agentic network",
    "realm",
    "cron",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/ontologic-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/object-ref-presentation-and-actions-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-policy-bridge-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
---
# Namespace Services

Namespace services are one integration plane in the broader KDCube component
ecosystem. Apps and services can also interact through REST/API operations,
MCP tools/resources, Event Bus, Data Bus, scene surface commands, or direct
runtime composition, including cron and scheduled jobs.

The named-services plane exists for provider-owned object realms: one app or
subsystem owns a semantic namespace and lets other apps, widgets, agents,
jobs, and external clients use that namespace without copying owner logic. This
is the plane that gives ReAct generic exploration/exploitation tools, lets UI
surfaces open/preview/drop objects, and keeps provenance when context from one
realm is reused in another.

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
  +-- block.produce when the namespace owns ReAct read projection
  +-- block.render for optional prompt-render patches or explicit render clients
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

The same provider can be consumed by canvas cards, chat context chips,
model-callable tools, API operations, MCP clients, Data Bus handlers, and
scheduled jobs. The client surface is transport-neutral; each runtime chooses
the adapter that fits the call path.

## At A Glance

Two apps, one configured edge, no shared code:

```text
  PROVIDER app (owns task:)               CONSUMER app (shows task: refs)
 ┌────────────────────────────┐         ┌─────────────────────────────────┐
 │ NamedServiceProvider       │         │ canvas card / chat chip / tool  │
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

## Resolution Tiers

A canvas/chat surface resolves a pinned `object_ref` by asking the resolver
router for the owner. In practice there are two implementation tiers:

| Tier | Namespaces | Resolver | Knows |
| --- | --- | --- | --- |
| Owner-local concrete | Namespaces owned by the same subsystem, such as `cnv:` inside Pinboard/Canvas or `conv:` inside Chat | the namespace owner's resolver | local owner state and local UI actions |
| Named-service generic | configured provider namespaces such as `task:`, `mem:`, or a future app namespace | named-service tool, resolver, and event-source adapters | opaque `object_ref`; provider supplies capabilities, actions, pull/read/render behavior |

The tier is an implementation detail of the owner/adapter, not behavior that a
generic surface infers. For example, a memory viewer can open `mem:` through
its local owner adapter, while ReAct materializes and reads the same `mem:`
object through the named-service provider. The shared identity remains the
canonical `object_ref`.

The named-service generic tier is additive. It registers after concrete
resolvers and fires for namespaces listed in the consumer app's
`surfaces.as_consumer` config. Local surfaces keep their own direct handlers,
and provider-backed refs gain a live cross-app/object-owner bridge.

## Documents

| Document | Purpose |
| --- | --- |
| [Components Ecosystem Architecture](../solutions/ecosystem-component/components-ecosystem-README.md) | End-to-end architecture map for provider apps, consumer apps, scene, ReAct, Pinboard, chat, events, data bus, and config. |
| [Ontologic Tools](ontologic-tools-README.md) | The model-facing tool surface for operating any realm by satisfying schemas: how the nine tools compose, the schema `tools` block as affordance, and the per-field `update` strategy. |
| [Providers](providers-README.md) | Provider contract, operation vocabulary, auth context, and transport adapters. |
| [Clients](clients-README.md) | Client config, tool exposure, current resolver behavior, and client ids. |
| [Integration](integration-README.md) | Visual provider-host/client-app flow using task-tracker and versatile. |
| [Discovery Registry](discovery-README.md) | The per-tenant/project Redis discovery table: the provider record, key schema, TTL/expiry, the registration write path, and the rule that reads go through the discovery module. |
| [Object Refs, Presentation, And Actions](object-ref-presentation-and-actions-README.md) | Canonical UI/provider boundary: `object_ref` is opaque to components, visual identity comes from `namespace_presentation_config`, and actions come from provider resolvers. |
| [ReAct Object Materialization](react-object-materialization-README.md) | Runtime-boundary diagram for `react.pull`, streamed `object.get`, `react.read`, owner `block.produce`, and prompt rendering. |
| [ReAct Object Policy Bridge](react-object-policy-bridge-README.md) | Owner policy contract for namespace rehosters, event-source routing, block production, render hooks, and `original_object_stats`. |
| [Logical Reference Namespaces](../events/namespaces-README.md) | Foundational rules for `task:`, `mem:`, `cnv:`, `fi:`, and other refs. |

## Current Scope

The current implementation is generic enough for provider/client integration
through Named Service Discovery:

- a provider app exposes a `named_services()` registry object and may also
  expose a `named_service` API operation backed by that registry;
- a provider app registers its providers into Redis-backed Named Service
  Discovery after its local prerequisites are ready, including provider
  `search_scopes` when it exposes multiple searchable object spaces;
- a client app configures `surfaces.as_consumer` for the model tools,
  event-source/pull policies, and resolver surfaces allowed to use that
  namespace;
- ReAct named-service search tools render provider-declared search scopes from
  discovery/config in the tool catalog, and `object.search` emits a generic
  `named_service.search_results` artifact so capable clients can render
  clickable/draggable result rows;
- canvas/chat object actions use a reusable resolver adapter;
- namespace artifact refs can be materialized by `react.pull`; the backend
  rehoster calls the provider's `object.get` with `response_mode: stream`,
  receives a normal named-service response plus byte chunks, and writes those
  chunks into the ReAct `fi:` workspace under the current auth context. The
  materialized artifact keeps the original namespace URI as `object_ref`, so
  later `react.read(fi:...)` blocks can still be routed to
  namespace-specific rendering or block-production policy. That routing is
  traceable through `react.read.owner_projection` logs and falls back to
  generic text only when no owner block is produced;
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
  owning app's registry object directly under the current tenant/project/user
  context;
- provider discovery scope is part of the platform cross-runtime context room,
  so tools can resolve provider records after subprocess or ISO bootstrap
  without passing a live Redis object through the tool registry;
- `bundle_operation` remains available when the owner exposes only the API
  facade or when the call path intentionally wants the operation envelope.

Named Service Discovery is a provider index, not a one-namespace/one-app
map. More than one app may register providers for the same namespace. Each
provider advertises the operations, refs, and object kinds it supports; the
runtime chooses the provider per request.

MCP and Data Bus are part of the provider capabilities vocabulary, but the
generic platform adapters are still integration work.

> **Naming.** "Namespace services" is the concept. The SDK module that
> implements it is `kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers`
> (`NamedServiceProvider`, `NamedServiceRegistry`, `NamedServiceClient`,
> `NamedServiceCanvasObjectResolver`). They are the same thing, not two layers.

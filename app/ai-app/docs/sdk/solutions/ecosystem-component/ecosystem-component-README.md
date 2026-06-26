---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
title: "Ecosystem Component Contract"
summary: "How a reusable domain participant joins KDCube's interaction network, and how provider-owned object refs, named services, event claims, ReAct materialization, scene surfaces, and pinboard integration make that participant agent/UI interoperable."
status: current
tags: ["sdk", "solutions", "ecosystem-component", "named-services", "scene", "canvas", "react", "events"]
updated_at: 2026-06-23
keywords:
  [
    "ecosystem component",
    "ecosystem participant",
    "interaction network",
    "realm",
    "new namespace",
    "object_ref",
    "named service provider",
    "api",
    "mcp",
    "cron",
    "scheduled jobs",
    "provenance",
    "scene component",
    "pinboard integration",
    "react materialization",
    "namespace presentation",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/object-ref-presentation-and-actions-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/generic-scene-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
---
# Ecosystem Component Contract

An ecosystem component is a reusable domain participant in KDCube's events and
actions network. It can be a standalone service, API, MCP server, backend
provider, UI widget, scene surface, agent tool provider, scheduler, or an app
that combines several of those roles.

Components do not have to interact with each other. When they do interact, they
can use several planes:

```text
KDCube interaction ecosystem
  API / REST operations
  MCP tools and resources
  Event Bus / SSE
  Data Bus commands/results
  cron / scheduled jobs
  named services
  scene surface commands
  chat/context events
```

Named services are the standard object/action/context plane for making a domain
realm agent-manipulable and UI-composable. Through named services, a provider
can give ReAct exploration and exploitation tools for a remote realm, expose UI
actions for that realm's objects, and let users attach context with provenance
when data from one realm becomes evidence or building material in another.

The target contract for that named-service-enabled path is:

```text
domain owner
  owns refs, schema, actions, events, render policy, widgets
        |
        v
KDCube ecosystem
  can search, pin, open, attach, materialize, render, and subscribe
  without hardcoding the domain
```

For the full end-to-end map of providers, consumer apps, scene, ReAct,
Pinboard, chat, Event Bus, Data Bus, and config layers, read
[Components Ecosystem Architecture](components-ecosystem-README.md). This page
is the implementation contract for one participating component.

## Core Rule

```text
object_ref is the universal handle.
Provider owns semantics.
Surfaces stay generic.
Users keep provenance when object context crosses realms.
```

Scene, canvas, chat, and generic widgets pass the full `object_ref`. They do
not parse URI tails to decide behavior. The provider/router may select an owner
resolver, and the owner provider parses the URI grammar it owns.

This document focuses on the provider-backed contract because that is the path
that makes a realm usable by ReAct, Pinboard, Chat, and Scene in a generic way.
For a purely standalone component, only the relevant API/MCP/event/UI subset is
needed.

## Required Owner Surface

A component that owns a namespace should provide these backend contracts.

| Contract | Required when | Purpose |
| --- | --- | --- |
| `provider.about` | Always | Explain the provider, namespace, supported operations, and object families. |
| `provider.capabilities` | Always | Describe available operations and integration modes. |
| `object.schema` | Mutable or agent-created objects | Tell ReAct and clients how to create/update/delete objects. |
| `object.resolve` | UI cards/chips/pins need metadata/actions | Cheap `object_ref -> metadata/capabilities/actions`; no large body reads. |
| `object.action` | UI open/preview/download/rehost | Provider-owned effects, including `ui_event.target_surface`. |
| `object.get` | ReAct/materializers need exact content | Stream or return a provider-owned object representation. |
| `object.search` / `object.list` | Search/list surfaces exist | Provider-owned query and result shape. |
| `object.upsert` / `object.delete` | Objects are mutable | Provider-owned mutation, validation, revision/conflict policy, events. |
| `event.resolve` | Events or ReAct reads carry provider refs | Lightweight `object_ref -> event source/policy identity`. |
| `block.produce` | ReAct should see provider objects | Provider-owned model-facing blocks. |
| `block.render` | Prompt rendering needs custom projection | Optional render patches or explicit rendered representations. |
| namespace presentation config | Any UI renders refs | Labels/colors/icons keyed by `namespace` and optional `object_kind`. |

The provider also registers with Named Service Discovery after it has storage,
indexes, and required credentials ready.

## Object Identity And Presentation

Every object crossing component boundaries should carry:

```json
{
  "object_ref": "acme:ticket:ticket_123",
  "namespace": "acme",
  "object_kind": "acme:ticket",
  "title": "Ticket 123",
  "summary": "Short preview"
}
```

Only `object_ref` is identity. `namespace` and `object_kind` are presentation
lookup keys and schema hints. They must not decide behavior. Missing
presentation keys are allowed; generic UIs render a neutral unknown style until
`object.resolve` returns metadata.

Visual identity comes from:

```text
POST /api/integrations/bundles/<tenant>/<project>/<app>/public/namespace_presentation_config
```

Do not put namespace icon/color tables into scene, canvas, or chat code.

## Backend Integration

```text
provider app startup
  build NamedServiceRegistry
  register provider for namespace(s)
  expose named_services()
  optionally expose @api(alias="named_service")
  register ReAct event sources / block policies
  register namespace rehoster or provider object.get stream
```

Consumer app configuration enables the provider; it does not copy provider
logic:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - id: named_services
            kind: named_service
            namespaces:
              acme:
                allowed:
                  - provider.about
                  - object.search
                  - object.schema
                  - object.get
                  - object.upsert
        event_sources:
          - kind: named_service
            namespace: acme
            discovery:
              mode: service_discovery
            policies:
              block_production:
                mode: provider
                operation: block.produce
              pull:
                mode: provider
                operation: object.get
    ui:
      canvas:
        resolvers:
          - kind: named_service
            namespace: acme
            enabled: true
            discovery:
              mode: service_discovery
            allowed: [object.resolve, object.action]
```

## ReAct Integration

ReAct should not receive one-off domain tools just to read objects. It uses the
named-service/materialization path:

```text
react.pull(paths=["acme:ticket:ticket_123"])
  -> provider object.get(response_mode=stream)
  -> workspace fi: artifact
  -> react.read(fi:...) preserves original object_ref
  -> event.resolve / block.produce
  -> model-visible owner blocks
```

For mutable providers, model-callable `named_services.upsert_object` is enabled
only when client policy allows mutation and the provider exposes
`object.schema` plus `object.upsert`.

The provider schema is the realm contract for agent manipulation. It tells the
agent what can be searched, inspected, created, updated, deleted, or used as
evidence. The tool catalog should not imply that payload fields such as
`kind`/`object_kind` control routing; routing is driven by the configured
namespace/provider and the full `object_ref`.

For canvas boards, use `cnv:<board>` as the live pull/read/upsert target.
`cnv:<board>@<revision>` is a fixed revision reference for read-only
inspection or diagnostics.

## Scene Integration

A scene mounts surfaces and routes commands. It does not own object semantics.

The component should expose:

- a route or widget package;
- a `surface_ref` / `target_surface` contract;
- a generic `kdcube.surface.command` handler;
- optional context drag source/target support;
- optional event subscription claims;
- optional compatibility declarations for drops/open effects.

Scene command envelope:

```json
{
  "type": "kdcube.surface.command",
  "target_surface": "acme.ticket.viewer",
  "action": "open",
  "object_ref": "acme:ticket:ticket_123",
  "context": {
    "ref": "acme:ticket:ticket_123"
  }
}
```

For provider-backed open, the scene calls `object.action(open, object_ref,
requestedTargetSurface)` and dispatches the provider-returned
`ui_event.target_surface`.

## Pinboard Integration

Pinboard is a neutral canvas surface.

It owns:

- board id, revision, layout, selection, comments, descriptions, trash state;
- canvas-owned objects under `cnv:`;
- pin search over the user's board snapshot.

It does not own:

- task, memory, conversation, file, or future provider semantics;
- provider colors/icons/actions;
- open/download/preview behavior for foreign refs.

Pinning flow:

```text
drop object_ref on pinboard
  -> board stores opaque object_ref + source display cache
  -> board indexes the pin snapshot
  -> board drawer asks object.resolve for capabilities/metadata
  -> user action calls object.action
```

If no resolver is available, the pin remains valid but unresolved.

The important user-facing guarantee is provenance: a card or chat context item
should show which realm/object supplied the material and should keep the
canonical `object_ref` available for later read/open/action flows.

## Event Integration

Components declare event interest once; transport is selected by runtime
config.

```text
component owns event interest
transport owns delivery
scene owns composition
```

In scene mode the component posts a subscription claim and the scene relays
matching Event Bus envelopes for that runtime/data scope. In standalone mode
the component can use the same claim through its own SSE transport.

Do not put fallback subscriptions in the scene to hide missing component
claims. If a claim is missing, diagnostics should show whether the component
did not send it, the scene rejected it, or the alias/runtime did not match.

Auth state is a separate host→surface announcement, not a subscription claim:
the host broadcasts `kdcube-auth-changed` on every session transition and the
component re-derives its auth-dependent state from it (a component that mounts
while the visitor is anonymous must react when the visitor signs in later). See
the [scene auth contract](../scene/scene-auth-README.md).

## Frontend Requirements

An ecosystem UI component should:

1. accept host/runtime config through an explicit config handshake;
2. treat auth as inferred, not carried — read authenticated state from the
   host's `kdcube-auth-changed` announcement and re-derive auth-dependent UI on
   it, probing `/profile` only when authenticated and only for the privileges it
   needs (never to discover sign-in); see the
   [scene auth contract](../scene/scene-auth-README.md);
3. emit canonical context drag payloads with `object_ref`;
4. handle `kdcube.surface.command` for its declared target surfaces;
5. render namespace visuals from the provided presentation config;
6. claim event subscriptions when it uses scene transport;
7. support standalone transport mode when applicable;
8. log command receipt, event claims, resolver failures, and rejected actions.

## Anti-Patterns

Do not:

- parse provider URI grammar in scene, canvas, chat, or generic widget code;
- make `kind` decide open/download/preview behavior;
- put namespace colors/icons in component code when presentation config exists;
- create fake handoff resolvers for namespaces owned elsewhere;
- expose provider mutation without `object.schema`;
- advertise ReAct tools that are not configured/discovered providers;
- use host config to teach a remote component which events it semantically
  understands;
- use profile calls to decide whether unauthenticated users are authorized.

Do:

- register an owner provider;
- pass full `object_ref`;
- use provider resolvers for actions;
- use provider block policies for ReAct;
- use component subscription claims for live events;
- let scene/canvas/chat remain transport and presentation surfaces.

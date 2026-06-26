---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
title: "Components Ecosystem Architecture"
summary: "End-to-end architecture map for KDCube ecosystem participants: apps, services, provider realms, consumer apps, scene composition, ReAct, chat, pinboard, events, data bus, cron, API/MCP, and namespace presentation."
status: current
tags: ["sdk", "solutions", "ecosystem-component", "architecture", "scene", "react", "pinboard", "named-services", "events"]
updated_at: 2026-06-23
keywords:
  [
    "components ecosystem",
    "ecosystem architecture",
    "ecosystem participant",
    "interaction network",
    "realm",
    "scene map",
    "cron",
    "scheduler",
    "named services",
    "api",
    "mcp",
    "react agent",
    "pinboard",
    "provenance",
    "namespace presentation",
    "event bus",
    "data bus",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/architecture-of-what-you-build-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/components/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/object-ref-presentation-and-actions-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/generic-scene-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-component-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
---
# Components Ecosystem Architecture

This document is the map for KDCube's component ecosystem: the events and
actions network where apps, services, widgets, agents, and jobs can interact
without copying each other's private domain logic.

An ecosystem component is a reusable domain participant. It may be a standalone
service, a UI widget, a provider app, a consumer app, an MCP surface, an API
surface, or any combination of those. Components can interact by consuming each
other's REST APIs, MCP tools/resources, Event Bus messages, Data Bus commands,
cron/job outputs, named services, or UI surface commands. They can also be useful without
interacting with any other component.

Named services are one important plane of this ecosystem: the provider-owned
object/action/context plane. They are the standard way to let a ReAct agent
explore or manipulate a remote domain realm, let UI surfaces open/preview/drop
objects from that realm, and preserve user-visible provenance for context that
travels between realms.

Use it before reading the detailed contracts. The details live in the linked
named-services, scene, canvas, chat, and events docs.

## One Sentence

```text
A component joins the KDCube interaction network through one or more planes:
API, MCP, Event Bus, Data Bus, cron/jobs, named services, and/or scene
surfaces.

When the component wants agent/UI interoperability, it exposes provider-owned
refs, named-service operations, event/block policies, and surface contracts.
```

## Full Architecture Map

```text
KDCube Runtime / Tenant / Project
|
+-- Provider Apps
|     |
|     +-- Task app
|     |     owns: task:issue:..., task:attachment:...
|     |     exposes:
|     |       - named_services()
|     |       - provider.about / provider.capabilities
|     |       - object.schema / search / get / upsert / delete
|     |       - object.resolve / object.action(open|preview|download)
|     |       - event.resolve
|     |       - block.produce / block.render
|     |       - namespace_presentation_config entries
|     |       - UI surfaces: task list, task editor
|     |
|     +-- Memory app/module
|     |     owns: mem:record:...
|     |     exposes the same provider surface:
|     |       search/get/upsert/resolve/action/block.produce
|     |       UI surface: memory viewer/list/editor
|     |
|     +-- Chat/ReAct artifact layer
|     |     owns: conv:..., fi:...
|     |     exposes:
|     |       conversation open/load actions
|     |       artifact preview/download/materialization
|     |
|     +-- Canvas module
|           owns: cnv:<board>, cnv:<board>@<rev>, cnv:... canvas objects
|           exposes:
|             board/card upsert/search/schema
|             object.resolve/action for cnv-owned objects
|             canvas event/block policies
|
+-- Named Service Discovery
|     |
|     +-- provider records keyed by tenant/project
|     +-- namespace -> one or more provider candidates
|     +-- operations/object_kinds/search_scopes advertised by owner
|     +-- clients resolve provider at call time
|
+-- Other Interaction Planes
|     |
|     +-- REST/API operations
|     |     direct app-to-app or host-to-app calls
|     |
|     +-- MCP tools/resources
|     |     tool/resource surfaces for agents or external clients
|     |
|     +-- Event Bus / SSE
|     |     service events and component subscription claims
|     |
|     +-- Data Bus
|     |     durable non-chat commands and result events
|     |
|     +-- Cron / Scheduled Jobs
|           due scans, background jobs, snapshots, maintenance
|
+-- Consumer App / Composition App
|     |
|     +-- backend config: surfaces.as_consumer
|     |     |
|     |     +-- agents.main.tools
|     |     |     enables model-callable named_services.* operations
|     |     |
|     |     +-- agents.main.event_sources
|     |     |     enables provider object.get / event.resolve / block.produce
|     |     |
|     |     +-- ui.canvas.resolvers
|     |           enables provider object.resolve/action for Pinboard cards
|     |
|     +-- frontend config: scene profile
|           |
|           +-- runtimes
|           |     demo -> origin + tenant + project + auth provider
|           |     dev  -> origin + tenant + project + auth provider
|           |
|           +-- components
|                 chat     -> app + route + target_surfaces + commands
|                 pinboard -> app + route + target_surfaces + data mode
|                 memory   -> app + route + target_surfaces + event mode
|                 task     -> app + route + target_surfaces + event mode
|                 stats    -> app + route + runtime/data scope
|
+-- Browser Scene Host
      |
      +-- loads scene profile
      +-- authenticates/gates surfaces
      +-- fetches namespace_presentation_config
      +-- mounts configured surfaces
      +-- maintains surface registry
      +-- routes kdcube.surface.command by target_surface
      +-- brokers context drag/drop
      +-- relays Event Bus envelopes only for component claims
      +-- keeps Data Bus routing scoped by runtime/data scope when owned
```

## Runtime Scene Map

```text
                          +-----------------------------+
                          |        Scene Host           |
                          |  config + auth + registry   |
                          +--------------+--------------+
                                         |
             +---------------------------+---------------------------+
             |                           |                           |
             v                           v                           v
   +-------------------+       +-------------------+       +-------------------+
   | Chat Surface      |       | Pinboard Surface  |       | Domain Surfaces   |
   | target_surface:   |       | target_surface:   |       | task editor/list  |
   | sdk.chat.context  |       | sdk.canvas.pinboard|      | memory viewer     |
   +---------+---------+       +---------+---------+       +---------+---------+
             |                           |                           |
             | context chips             | pins/cards                | local UI
             | object_ref                | object_ref                | object_ref
             |                           |                           |
             +-------------+-------------+-------------+-------------+
                           |
                           v
               +-------------------------+
               | object_ref stays opaque |
               +------------+------------+
                            |
                            v
                +------------------------+
                | Provider Resolver      |
                | object.resolve/action  |
                +-----------+------------+
                            |
                            v
       +----------------------------------------------+
       | provider returns capabilities/actions/ui_event |
       +--------------------+-------------------------+
                            |
                            v
                   Scene dispatches:
                   kdcube.surface.command
                   target_surface = provider result
```

The scene may highlight candidate targets from configured/component-declared
compatibility, but it does not infer open/download behavior. The provider owns
the action result.

## ReAct + Provider + Pinboard Map

```text
User asks agent about selected/pinned objects
|
v
Chat submits external_events[]
  - user prompt
  - context chips
  - canvas focus or board ref
        |
        v
ReAct turn
  |
  +-- tool catalog
  |     named_services.* enabled by surfaces.as_consumer.agents.main.tools
  |
  +-- materialization
  |     react.pull(paths=["mem:record:..."])
  |       -> provider object.get(response_mode=stream)
  |       -> workspace fi: artifact
  |       -> preserve original object_ref in metadata
  |
  +-- read/projection
  |     react.read(fi:...)
  |       -> event.resolve(original object_ref)
  |       -> provider block.produce
  |       -> model-visible blocks
  |
  +-- mutations
        named_services.upsert_object(namespace="task"|"mem"|"cnv", ...)
          -> provider object.schema validates shape
          -> provider object.upsert mutates durable owner state
          -> provider emits events/snapshots as appropriate
          -> scene/widget subscribers update through their claims

Pinboard path
  drop object_ref on board
    -> cnv provider stores card layout + display cache
    -> Pinboard asks owner object.resolve for card affordances
    -> user click open/download/preview
    -> Pinboard asks owner object.action
    -> scene routes provider ui_event by target_surface
```

## Config Layers

```text
backend app config
  surfaces.as_consumer
    agents.<agent>.tools
    agents.<agent>.event_sources
    ui.canvas.resolvers
    namespace provider client policy

provider app config
  namespace providers
  schemas
  object kinds
  search scopes
  namespace presentation
  event/block policies

frontend scene config
  runtimes
  components
  routes
  surface aliases
  target surfaces
  mount mode
  command names
  transport mode
  auth gates

component runtime config
  hostedByScene
  liveEventsTransport
  liveDataTransport
  namespaceStyles
  tenant/project/app
  auth/session readiness
```

No config layer should smuggle provider behavior into an unrelated component.
The backend provider owns semantics; the scene/frontend config owns
composition.

Auth/session readiness is host-announced, not component-owned: the host
broadcasts `kdcube-auth-changed` on every session transition and components
react to it (rather than carrying credentials or polling identity endpoints).
See the [scene auth contract](../scene/scene-auth-README.md).

## Event And Data Planes

```text
Conversation Event Lane
  /sse/chat external_events[]
  user prompts, attachments, context chips, canvas focus events
  feeds ReAct turns and timeline/block policies

Event Bus / SSE
  service events such as accounting.usage, task_tracker.task.changed,
  kdcube.stats.snapshot
  delivered to components that claim subscriptions
  scene may relay per runtime/data scope

Data Bus
  durable non-chat domain commands
  e.g. canvas board patch/upsert messages
  routed by subject/partition
  may emit result events, but not automatically ReAct turns

Cron / Scheduled Jobs
  recurring due scans and background jobs
  may publish events, update provider state, or refresh snapshots
  should hand off long work to @on_job rather than doing it in the tick

Named Services
  provider-owned object/action/context plane
  search/get/upsert/resolve/action/schema/block.produce
  gives agents exploration/exploitation tools for remote realms
  gives UI surfaces open/preview/drop actions for realm objects
  preserves provenance when realm data becomes context/evidence/material
  used by ReAct tools, Pinboard, scene open flows, jobs, APIs
```

## Connecting A Realm To Agent And UI

This checklist is for the named-services path. It is how a domain realm becomes
agent-manipulable and context-pluggable: the agent can search/read/create/update
objects through strategic tools, UI surfaces can open/drop those objects, and the
end user can see where attached context came from.

It is not the whole definition of an ecosystem component. A component may first
join KDCube only as an API, MCP server, standalone service, scheduler, Event Bus
publisher, or UI surface. Use this path when the realm should participate in
generic ReAct, Pinboard, Chat, and Scene workflows.

```text
1. Choose namespace
     acme:

2. Define refs
     acme:ticket:<id>
     acme:attachment:<ticket>/<file>

3. Implement provider
     provider.about
     object.schema
     object.search/get/upsert/delete
     object.resolve/action
     event.resolve
     block.produce

4. Register provider
     named_services()
     Named Service Discovery
     optional @api(alias="named_service")

5. Add presentation
     namespace_styles.acme
     namespace_styles.acme:ticket
     namespace_styles.acme:attachment

6. Add UI surface
     route
     target_surface
     kdcube.surface.command handler
     context drag payloads
     event subscription claims

7. Enable consumer config
     agents.main.tools
     agents.main.event_sources
     ui.canvas.resolvers

8. Add scene config
     runtime
     component alias
     route
     target_surfaces
     mount/readiness
     transport modes

9. Verify flows
     search provider objects
     drag result to Pinboard
     open pin from Pinboard
     attach object to chat
     react.pull/read object
     upsert/delete if mutable
     receive live update event
     verify context provenance is visible to the user
```

## Current Reference Components

| Component | Namespace / surface | Expected ecosystem role |
| --- | --- | --- |
| Chat | `conv:`, `fi:`, `sdk.chat.context` | Conversation UI, context attach/open, ReAct turn entry, artifact refs. |
| Pinboard / Canvas | `cnv:`, `sdk.canvas.pinboard` | Neutral board, card layout/comments, opaque object pins, canvas-owned objects. |
| Memory | `mem:record:...`, `sdk.memory.viewer` | Provider-owned memory search/read/upsert/open/render. |
| Task Tracker | `task:issue:...`, `task:attachment:...` | Provider-owned task object search/upsert/open/download/render. |
| Stats / Usage | service events, usage surfaces | Component-claimed subscriptions and refresh commands. |
| News | app-owned objects/events | Preview/search/event component using the same event transport pattern. |

## What The Visual Scene Should Eventually Show

A scene map can be generated from the same config and discovery facts:

```text
Nodes:
  runtimes
  apps
  mounted surfaces
  provider namespaces
  named-service operations
  event subscriptions
  data-bus subjects
  ReAct tools/event sources

Edges:
  component -> runtime
  surface -> target_surface
  namespace -> provider app
  consumer -> provider operation
  component -> event claim
  pinboard -> object.resolve/action
  ReAct -> object.get/block.produce/upsert
```

The scene should present this as a live architecture map, but the data source
must be the same ecosystem contracts documented here, not a separate hardcoded
diagram.

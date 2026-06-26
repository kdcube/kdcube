---
id: docs/sdk/solutions/ecosystem-component/event-subscription-and-transport-README.md
title: "Ecosystem Component Event Subscription And Transport"
summary: "Shared event subscription contract for reusable KDCube components, with scene, widget-owned SSE, and future data-bus transports as interchangeable delivery mechanisms."
status: design
tags: ["sdk", "solutions", "ecosystem-component", "events", "scene", "sse", "data-bus", "components-core"]
updated_at: 2026-06-23
keywords:
  [
    "ecosystem component events",
    "component event subscription",
    "liveEventsTransport",
    "scene transport",
    "widget SSE",
    "component owned subscription claim",
    "components-core events"
  ]
see_also:
  - docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - docs/sdk/solutions/ecosystem-component/ecosystem-component-README.md
  - docs/sdk/solutions/scene/generic-scene-contract-README.md
  - docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - docs/sdk/solutions/scene/scene-composition-README.md
  - src/kdcube-ai-app/npm/packages/components-core/src/events
  - src/kdcube-ai-app/npm/packages/components-core/src/scene
---
# Ecosystem Component Event Subscription And Transport

This document defines the browser-side event contract for KDCube ecosystem
components. A component declares the events it understands once. The selected
transport decides how those events reach the component.

The shared implementation lives in:

```text
src/kdcube-ai-app/npm/packages/components-core/src/events
```

The scene-specific transport adapter lives with the generic event layer:

```text
src/kdcube-ai-app/npm/packages/components-core/src/events/sceneTransport.ts
```

The scene package keeps a compatibility export at
`src/kdcube-ai-app/npm/packages/components-core/src/scene/subscriptions.ts`.
Scene hosts can import that file, but the dependency direction is:

```text
events -> no scene dependency
scene  -> consumes events
```

## Core Rule

```text
Component owns event interest.
Transport owns delivery.
Scene owns composition.
```

This keeps event semantics out of the scene host. A stats component knows that
it wants `kdcube.stats.snapshot`. A task component knows that it wants
`task_tracker.task.changed`. A usage card knows that it wants
`accounting.usage`. The host scene knows where the component is mounted, which
runtime serves it, and which delivery transport the component should use in
that profile.

## Boundary Diagram

```text
component code
  declares: event ids, channels, debounce, handler/forward command
        |
        v
@kdcube/components-core/events
  normalizes subscription claim
  selects configured transport
        |
        +----------------------+----------------------+----------------------+
        |                      |                      |                      |
        v                      v                      v
scene transport           widget-owned SSE        future data-bus transport
postMessage claim         EventSource owned       subject/partition client
to parent host            by component            owned by scene or widget
        |                      |                      |
        v                      v                      v
component handler       component handler       component handler
```

The component handler remains the same across transports.

## Common Layer Architecture

The reusable layer has three responsibilities:

```text
@kdcube/components-core/events
  |
  +-- subscription contract
  |     - normalize event names/channels/source
  |     - normalize liveEventsTransport
  |     - validate empty claims
  |     - expose one component-facing subscribe() API
  |
  +-- transport adapters
  |     - scene adapter: post claim to parent scene
  |     - sse adapter: component-owned EventSource (next extraction)
  |     - data-bus adapter: subject/partition delivery (future)
  |
  +-- lifecycle
        - declare
        - bind
        - unsubscribe
        - log selected transport
```

The scene package then consumes the same claim shape:

```text
@kdcube/components-core/scene
  |
  +-- host surface registry
  +-- runtime-aware Event Bus relay
  +-- context drag/drop broker
  +-- surface command dispatcher
  +-- receives kdcube-scene-subscribe claims from components

@kdcube/components-core/scene/subscriptions.ts
  |
  +-- re-exports scene transport helpers from @kdcube/components-core/events
```

### Embedded In A Scene

```text
browser page / scene host
  kdcube.config.json profile
    runtimes.demo -> origin + tenant + project
    components.usage_card -> runtime=demo, liveEventsTransport=scene
          |
          v
  scene mounts iframe
          |
          v
  usage-card component boots
          |
          v
  component runtime config says:
      hostedByScene=true
      liveEventsTransport=scene
          |
          v
  @kdcube/components-core/events
      component declares:
        events=['accounting.usage']
        channels=['chat_service', 'message']
        forward={ type:'kdcube.surface.command', action:'refresh' }
          |
          v
  scene transport adapter posts to parent:
      { type:'kdcube-scene-subscribe', widget:'usage_card', ... }
          |
          v
  @kdcube/components-core/scene in host
      records claim under surface alias
      opens/reuses EventSource for runtime demo
          |
          v
  backend Event Bus emits accounting.usage
          |
          v
  scene relay matches claim
          |
          v
  scene posts iframe message:
      { type:'kdcube.surface.command',
        target_surface:'sdk.usage.card',
        action:'refresh',
        scene_event:{ type:'accounting.usage', ... } }
          |
          v
  usage-card handles command and pulls budget-breakdown
```

The component does not know how many other runtimes are mounted in the page.
The scene knows the runtime scope because it mounted the component from that
runtime.

### Standalone Component

```text
browser page opens widget directly
  /api/integrations/bundles/{tenant}/{project}/{app}/public/widgets/usage
          |
          v
  usage-card or stats component boots
          |
          v
  component runtime config says:
      hostedByScene=false
      liveEventsTransport=sse
          |
          v
  @kdcube/components-core/events
      component declares the same event interest
          |
          v
  sse transport adapter opens EventSource:
      /sse/stream?tenant={tenant}&project={project}&project_events=true
          |
          v
  backend Event Bus emits matching event
          |
          v
  component-owned transport matches claim
          |
          v
  component handler runs directly
```

The same event claim is used. Only delivery changes.

### Mixed Runtime Scene

```text
website scene
  runtimes.demo -> https://demo.kdcube.tech, tenant=demo, project=demo
  runtimes.dev  -> https://dev.kdcube.tech,  tenant=demo, project=demo-march

  chat component  -> runtime=demo, liveEventsTransport=scene
  task component  -> runtime=demo, liveEventsTransport=scene
  stats component -> runtime=dev,  liveEventsTransport=sse
  news component  -> runtime=dev,  liveEventsTransport=none or sse
```

In this profile the scene can own the demo Event Bus while the stats component
keeps its own dev SSE stream. The component subscription contract remains the
same in both cases.

## Subscription Claim

A component declares a claim with this shape:

```ts
{
  id: 'usage-card-accounting-refresh',
  source: 'sse',
  events: ['accounting.usage'],
  channels: ['chat_service', 'message'],
  forward: {
    type: 'kdcube.surface.command',
    target_surface: 'sdk.usage.card',
    action: 'refresh'
  },
  reason: 'accounting.usage',
  debounceMs: 800
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable component-local subscription id. |
| `source` | Logical source family, currently usually `sse`. |
| `events` | Canonical event identities inside the envelope. |
| `channels` | Browser/EventSource channel names that may carry the envelope. |
| `forwardType` | Optional browser `postMessage.type` when the scene forwards an event. |
| `forward` | Optional command payload used by the scene transport. |
| `reason` | Human-readable trigger reason used in logs and forwarded messages. |
| `includeEnvelope` | Include the original backend envelope in the forwarded message. |
| `debounceMs` | Scene-side debounce before forwarding a matched event. |

The claim is declarative. It does not say whether the component opens SSE or
uses a parent scene.

## Transport Selection

The component receives runtime config from the host or platform. The relevant
field is:

```json
{
  "liveEventsTransport": "scene"
}
```

Supported values:

| Value | Behavior |
| --- | --- |
| `scene` | Component posts its claim to the parent scene. The scene owns the Event Bus connection for the component runtime and forwards matched events. |
| `sse` | Component opens its own EventSource and applies the same event interest locally. |
| `none` | Component runs without live event delivery. Manual refresh or HTTP polling-free user actions still work. |

Aliases such as `host`, `parent`, `self`, `widget`, `off`, and `disabled` are
normalized by `@kdcube/components-core/events`.

## Scene Transport

When `liveEventsTransport = "scene"`, the component posts:

```json
{
  "type": "kdcube-scene-subscribe",
  "widget": "usage_card",
  "subscriptions": [
    {
      "id": "usage-card-accounting-refresh",
      "source": "sse",
      "events": ["accounting.usage"],
      "channels": ["chat_service", "message"],
      "forward": {
        "type": "kdcube.surface.command",
        "target_surface": "sdk.usage.card",
        "action": "refresh"
      },
      "reason": "accounting.usage",
      "debounceMs": 800
    }
  ]
}
```

On unmount it posts:

```json
{
  "type": "kdcube-scene-unsubscribe",
  "widget": "usage_card"
}
```

The scene records the claim under the mounted component alias. When a matching
backend event arrives from the runtime Event Bus, the scene forwards the
configured browser message to the component iframe.

### Host-pushed auth state

Not every host→surface message is a claimed subscription. Auth state is pushed
by the host *without* a claim: on every session transition the host broadcasts
`kdcube-auth-changed` (a `postMessage` to surface iframes and a window event for
in-page surfaces). A component reacts to it to re-derive auth-dependent state —
it does not subscribe for it, and it must not poll an identity endpoint to
discover sign-in. See the
[scene auth contract](../scene/scene-auth-README.md).

## Widget-Owned SSE

When `liveEventsTransport = "sse"`, the component keeps its current direct
EventSource behavior. This mode is needed for components mounted from a
different runtime than the main scene Event Bus, and for standalone use.

Example from the landing site:

```text
scene host runs on kdcube.tech
chat/canvas/task components use demo runtime
stats/news components may use dev runtime
stats can use own SSE to dev while chat uses scene relay to demo
```

The event interest still belongs to the component. Only the event source is
different.

## Component Implementation Pattern

Framework-neutral code:

```ts
import {
  createComponentEventClient,
  createSceneEventTransport,
} from '@kdcube/components-core/events'

const events = createComponentEventClient({
  component: 'usage_card',
  transportMode: config.liveEventsTransport,
  transports: {
    scene: createSceneEventTransport({ logger: console }),
    sse: widgetOwnedSseTransport,
  },
  logger: console,
})

const dispose = events.subscribe([
  {
    id: 'usage-card-accounting-refresh',
    source: 'sse',
    events: ['accounting.usage'],
    channels: ['chat_service', 'message'],
    forward: {
      type: 'kdcube.surface.command',
      target_surface: 'sdk.usage.card',
      action: 'refresh',
    },
    reason: 'accounting.usage',
    debounceMs: 800,
  },
])
```

React components call `dispose()` during effect cleanup.

## Current Compatibility Refactor

The first compatibility-preserving refactor keeps existing behavior:

| Component | Current event interest | Default preserved transport | Shared layer usage |
| --- | --- | --- | --- |
| Usage card | `accounting.usage` -> refresh budget breakdown | `scene` only when configured by host; otherwise no live subscription | Scene subscription claim is built through `components-core/events`. |
| Stats usage widget | `kdcube.stats.snapshot` -> apply pushed snapshot | `sse` by default; `scene` when configured | Scene claim is built through `components-core/events`; own SSE remains widget-owned. |
| News preview | `kdcube.news.pipeline_event` -> reload preview after pipeline completion | `none` by default; `scene` or `sse` when configured | Scene claim is built through `components-core/events`; own SSE remains widget-owned. |
| Task list and task editor | `task_tracker.task.changed` -> refresh open task/list state | scene when embedded plus existing standalone SSE path | Scene claim is built through `components-core/events`; standalone SSE remains widget-owned. |
| Chat | Chat turn stream and per-chat accounting events | Existing chat stream | No transport change in this refactor. |
| Canvas pinboard | Canvas patch/data-bus updates | Existing canvas data-bus socket | No transport change until scene Data Bus routing is implemented. |

This step removes duplicated scene subscribe/unsubscribe message builders while
preserving each component's live-event behavior.

## Future Extraction

The next layer is a reusable SSE transport in `components-core/events` or
`components-core/transport`. That transport should accept:

```ts
{
  baseUrl,
  tenant,
  project,
  projectEvents,
  credentials,
  eventTypes,
  channels,
  onEnvelope
}
```

The component should still pass its event claim once. The transport should
translate that claim into EventSource listeners and local matching.

The Data Bus transport should use the same client shape after scene and widget
data-bus ownership rules are finalized.

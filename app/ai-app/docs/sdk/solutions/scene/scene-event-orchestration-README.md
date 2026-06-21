---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-event-orchestration-README.md
title: "Scene Event Orchestration"
summary: "Concrete contract for host-side scene event routing: one scene-level event stream, widget subscription claims, self-owned stream opt-out, and event-bus/data-bus boundaries."
status: draft
tags: ["sdk", "solutions", "scene", "events", "event-bus", "data-bus", "widgets", "postmessage", "sse"]
updated_at: 2026-06-19
keywords:
  [
    "scene event orchestration",
    "kdcube-scene-subscribe",
    "kdcube-scene-unsubscribe",
    "scene event bus",
    "widget event subscriptions",
    "usage card accounting.usage",
    "stats snapshot",
    "task changed event",
    "event bus data bus boundary"
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/surface-layering-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/npm/packages/components-core/src/scene/eventBus.ts
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/npm/packages/components-core/src/scene/subscriptions.ts
---
# Scene Event Orchestration

A scene can contain widgets from several apps. Some widgets open their own live
stream. Other widgets are passive iframes and need the scene host to relay live
events to them. This document defines the common contract for that relay.

This document covers persistent runtime events. Cross-surface drag/drop uses the
scene overlay control plane in
[Cross-Surface Context Drag](cross-surface-context-drag-README.md); those
messages are transient browser UI messages, not Event Bus subscriptions.

The rule:

```text
If a widget is configured to own a live stream for an event family, it does not
claim that event family from the scene host.

If a widget does not own that stream and needs those events, it sends a
scene subscription claim to the host.
```

This avoids two failure modes:

- duplicate live streams for the same event family;
- silent widgets that wait for a host event the host was never asked to relay.

## Boundaries

| Boundary | Owner | Concrete responsibility |
| --- | --- | --- |
| Event production | Runtime/app backend | Emits `accounting.usage`, app domain events, stats snapshots, and similar service events. |
| Event Bus stream | Platform runtime | Provides the session/project SSE stream such as `/sse/stream`. |
| Data Bus stream | Platform runtime | Provides subject/partition style delivery, currently used by canvas pinboard patches. |
| Scene host | Browser page/component | Opens at most one host-owned Event Bus listener per runtime scope it brokers, keeps a subscriber registry, and forwards matching events to iframes. |
| Widget | Iframe/component | Declares a scene subscription only when it is not configured to open its own stream for that event family. |

Scene planes:

```text
Runtime Event Bus/Data Bus
  long-lived stream delivery from backend/runtime to widgets
  examples: accounting.usage, kdcube.stats.snapshot, canvas patch subjects

Drag/drop overlay control plane
  short-lived browser UI state owned by the scene host
  examples: kdcube-context-drag-start/end, transparent target overlays
```

The Event Bus and Data Bus are separate surfaces:

```text
Event Bus
  transport: SSE
  current match keys: event envelope type + browser event channel
  examples: accounting.usage, task_tracker.task.changed, kdcube.stats.snapshot

Data Bus
  transport: Socket.IO today
  current match keys: subject/partition-like payload fields owned by the data bus
  examples: canvas patch events consumed by pinboard
```

Event identity and browser transport names are separate:

```text
canonical event identity
  lives in the event envelope and in scene_event.type
  may use the producer namespace shape, for example task:event:task-changed

browser transport message name
  lives in postMessage.type, and in any DOM-style local event alias
  uses dash-case, for example kdcube-task-tracker-task-changed
```

The scene matches on canonical event identity. The forwarded browser message can
use a dash-case alias while still carrying the canonical identity in
`scene_event.type`. If a future widget dispatches a real DOM `CustomEvent`, the
same rule applies: the DOM event name is a dash-case transport alias; the
canonical event identity is payload data.

The scene relay described here is for Event Bus messages. Data Bus relay can use
the same claim pattern later, but it must preserve Data Bus subjects/partitions
instead of flattening them into Event Bus channel names.

A runtime scope is the tuple:

```text
origin | tenant | project
```

A single-runtime scene normally has one Event Bus relay. A mixed-runtime scene
may have one relay per runtime scope.

The scene must support multiple KDCube runtimes when the active profile allows
them. A subscription is therefore not only:

```text
event type + channel
```

It is:

```text
bus + runtime scope + optional data scope + event type + channel/subject
```

If a widget does not specify a runtime in its subscription claim, the scene
uses the runtime of the surface that registered the claim. If a widget is
configured to show data for another tenant/project, the claim can include that
data scope and the event payload must also carry a matching scope for delivery.

Example:

```text
chat, memory, pinboard
  runtime scope: https://demo.kdcube.tech | demo | demo

stats
  route runtime scope: https://dev.kdcube.tech | demo | demo-march
  displayed data scope: demo | demo

scene event relays
  Event Bus relay for demo/demo only if a demo-hosted widget claims scene relay
  Event Bus relay for dev/demo-march only if a dev-hosted widget claims scene relay
```

Widgets that own their own SSE stream, such as a stats widget configured with
`liveEventsTransport="sse"`, do not require a scene relay for that event
family. The scene still passes runtime and data-scope config to the widget so
the widget can connect to the correct runtime itself.

The host profile can choose the transport mode for a component. The exact
event names, channels, and actions are component/app-owned. In scene transport
mode, the component posts a `kdcube-scene-subscribe` claim after it receives
host config; the scene only scopes, opens/reuses the runtime relay, matches,
debounces, and forwards events according to that claim.

## Runtime Shape

```text
Runtime Event Bus
  /sse/stream
      |
      v
Scene host EventSource
  parses browser SSE event + JSON envelope
      |
      v
SceneEventBus
  normalize: { source, channel, type, runtime, dataScope, envelope, ts }
  match: source + runtime scope + optional data scope + channel + type
  debounce: optional per subscription
      |
      +--> iframe: usage_card
      |      postMessage({ type: "kdcube-usage-card-refresh", ... })
      |
      +--> iframe: task list
      |      postMessage({ type: "kdcube-task-tracker-task-changed", ... })
      |
      +--> iframe: stats usage widget, only if configured liveEventsTransport="scene"
             postMessage({ type: "kdcube-stats-snapshot", envelope, ... })
```

The scene does not infer widget needs from namespace, color, or UI placement.
Widgets declare their needs explicitly.

## Subscription Handshake

The widget sends this message to its parent scene:

```json
{
  "type": "kdcube-scene-subscribe",
  "widget": "usage_card",
  "subscriptions": [
    {
      "id": "usage-card-accounting-refresh",
      "source": "sse",
      "runtime": "demo",
      "dataScope": { "tenant": "demo", "project": "demo" },
      "events": ["accounting.usage"],
      "channels": ["chat_service", "chat_step", "accounting.usage", "message"],
      "forwardType": "kdcube-usage-card-refresh",
      "reason": "accounting.usage",
      "debounceMs": 800
    }
  ]
}
```

The fields mean:

| Field | Meaning |
| --- | --- |
| `widget` | Scene alias for the target iframe. |
| `id` | Stable subscription id scoped by widget. |
| `source` | Event source family. Today `sse` means the Event Bus SSE relay. |
| `runtime` | Optional scene runtime alias. If absent, the component runtime is used. |
| `dataScope` | Optional tenant/project scope for the data carried by the event. Use when route runtime and displayed data scope differ. |
| `events` | Canonical envelope event identities to match, for example `accounting.usage` or future namespace-shaped values such as `task:event:task-changed`. |
| `channels` | Browser SSE event names to match, for example `chat_service`. |
| `forwardType` | Dash-case `postMessage.type` the widget expects. This is a transport alias, not the canonical event identity. |
| `reason` | Diagnostic reason copied into the forwarded message. |
| `includeEnvelope` | Whether the original envelope is included in the forwarded message. |
| `debounceMs` | Optional host-side debounce for high-frequency events. |

The widget sends this on unmount:

```json
{
  "type": "kdcube-scene-unsubscribe",
  "widget": "usage_card"
}
```

The shared helper lives in:

```text
app/ai-app/src/kdcube-ai-app/npm/packages/components-core/src/scene/eventBus.ts
app/ai-app/src/kdcube-ai-app/npm/packages/components-core/src/scene/subscriptions.ts
```

`eventBus.ts` is the host-side matcher/dispatcher. `subscriptions.ts` builds the
`kdcube-scene-subscribe` and `kdcube-scene-unsubscribe` messages without tying
the widget to React or to a specific host page.

## Transport Mode

Widgets that can run both standalone and inside a brokered scene need an
explicit live-event mode:

```text
liveEventsTransport = "sse"   -> widget opens its own EventSource
liveEventsTransport = "scene" -> widget sends a scene subscription claim
liveEventsTransport = "none"  -> widget does neither
```

The default must match the existing widget behavior. If a widget already owns an
SSE stream, the default is `sse`. If it has no live stream and only reacts to
host pings, the default is `scene` or an explicit host subscription in the
widget.

## Plain-Script Host Example

A non-React host can use the same scene contract through a small host-side
broker. The concrete file names are host-owned; the reusable shape is:

```text
host event bus module
  create({ getAliases, defaultSubscriptions, isReady, post, queue })
  register(alias, subscriptions)
  unregister(alias)
  publish({ source, channel, type, envelope, ts })

host scene module
  opens one scene-level EventSource per runtime scope
  parses incoming SSE events
  calls sceneEventBus.publish(...)
  queues forwarded messages until iframe config handshake is complete
```

The host can keep backward-compatible defaults for older widgets. For example,
`usage_card` receives a default `accounting.usage` subscription only until the
current usage-card build registers its own explicit claim. Once the iframe sends
`kdcube-scene-subscribe`, the explicit widget claim replaces the host default.

## Example Widget Ownership Map

This table shows the intended ownership pattern for a host scene. Private app
widgets are named by role, not by source path.

| Surface / widget | Event Bus ownership | Data Bus ownership | Scene claim | Events listened for | Notes |
| --- | --- | --- | --- | --- | --- |
| Chat widget | Self | None | No | Chat turn stream and chat service events for that chat instance | The chat stream is widget-owned. Scene may attach contexts to chat, but chat does not claim its own stream from the scene. |
| Usage card | Scene | None | Yes | `accounting.usage` | The card has no SSE transport. It receives `kdcube-usage-card-refresh` and repulls budget data. |
| Stats usage widget | Self by default; scene when configured | None | Conditional | `kdcube.stats.snapshot` | `liveEventsTransport="sse"` opens widget SSE. `liveEventsTransport="scene"` claims host relay. |
| News preview widget | Self by default; scene when configured | None | Conditional | `kdcube.news.pipeline_event` | `liveEventsTransport` selects widget SSE vs host relay. |
| Full/admin news widgets | Self, only while the admin stream is visible | None | No by default | `kdcube.news.pipeline_event` | The stream is opened lazily in the admin view. |
| Pinboard / canvas board | None | Self | No | Canvas patch events | The board owns its Data Bus Socket.IO subscription. Event Bus scene claims are not used for canvas patches. |
| Telegram miniapp example | None | Self | No | Its configured Data Bus/service stream | The miniapp owns its Socket.IO/Data Bus client. It should not claim scene Event Bus delivery for the same stream. |
| Memory widget | None currently | None | None currently | No current memory-record event family in the landing scene | Memory opens objects through scene commands and local API calls. A future `memory.record.changed` event can use this same claim pattern when the producer exists. |
| Task list widget | Scene | None | Yes | `task_tracker.task.changed` | The task app is private. The widget has no task-change SSE of its own. |
| Task wizard/editor | Scene for task-change events; self for its internal assistant chat | None | Yes for task changes | `task_tracker.task.changed`; internal wizard chat stream is self-owned | Scene relays task-change events. The wizard's own assistant stream is separate and remains widget-owned. |

The current task event name is a compatibility event name. The platform-wide
direction is URI-shaped event identity owned by the producer namespace, for
example `task:event:task-changed`. That rename is a separate producer/consumer
migration.

## Required Traces

The host should log these stages:

```text
[kdc-scene] scene subscriber registered
[kdc-scene] scene event received
[kdc-scene] scene event dispatched
[kdc-scene] scene event had no subscribers
```

The widget should log the event it receives when the event causes visible work.
For example, the usage card logs `refresh requested` and acknowledges the host
with `kdcube-usage-card-refresh-ack`.

These traces make it possible to answer four questions from the browser console:

1. Did the widget claim the event?
2. Did the scene receive the runtime event?
3. Did the scene dispatch to that widget?
4. Did the widget perform its local refresh/update?

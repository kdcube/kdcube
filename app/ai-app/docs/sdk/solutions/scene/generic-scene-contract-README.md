---
id: docs/sdk/solutions/scene/generic-scene-contract-README.md
title: "Generic Scene Contract"
summary: "Gold-standard target design for a config-driven scene: surfaces, namespace presentation, context drag, event relay, generic surface commands, and widget responsibilities."
status: design
tags: ["sdk", "solutions", "scene", "components", "widgets", "configuration", "surface-command", "event-bus", "data-bus", "named-services"]
updated_at: 2026-06-21
keywords:
  [
    "generic scene contract",
    "scene configuration",
    "surface command",
    "kdcube.surface.command",
    "target_surface",
    "scene subscriptions",
    "context drag",
    "namespace presentation",
    "component contract",
    "app scene recipe"
  ]
see_also:
  - docs/sdk/solutions/scene/scene-composition-README.md
  - docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - docs/sdk/solutions/scene/cross-surface-context-drag-README.md
  - docs/sdk/solutions/scene/scene-surface-registry-README.md
  - docs/sdk/solutions/scene/surface-layering-README.md
  - src/kdcube-ai-app/npm/packages/components-core/src/scene
---
# Generic Scene Contract

This document is the target design for KDCube scenes. It is intentionally more
strict than the current landing-page implementation. Use it as the gold
standard when refactoring the website scene, when extracting scene logic into
`@kdcube/components-core/scene`, and when writing app recipes for scene
composition.

The target scene is a config-driven browser composition layer. It connects UI
surfaces from several apps without embedding memory, task, canvas, chat,
analytics, or news semantics in the host page.

The central rule:

```text
Scene core routes opaque object refs and runtime events.
Providers and surfaces own object semantics and local UI behavior.
```

The scene's generic command envelope is:

```json
{
  "type": "kdcube.surface.command",
  "target_surface": "task_tracker.issue_editor",
  "action": "open",
  "object_ref": "task:issue:ticket_123",
  "context": {
    "ref": "task:issue:ticket_123",
    "label": "Cancel membership"
  }
}
```

Every specific widget command is a widget-side concern. A task editor may
translate this envelope into its local state shape. A memory viewer may
translate it into a focus call. A chat widget may translate an `attach` action
into context chips. The scene core still emits the same envelope.

## Terms

| Term | Meaning |
| --- | --- |
| App | A deployable KDCube capability. The platform still uses the internal word `bundle` in routes and descriptors; public docs should describe it as an app. |
| Scene | A host page/component that composes multiple surfaces into one workspace. |
| Surface | A UI participant in the scene: iframe widget, in-page component, floating editor, board, chat panel, analytics card, or preview. |
| Surface alias | Host-local name used to mount and address a surface instance, for example `chat`, `pinboard`, or `usage_card`. |
| Target surface | Provider-owned logical surface id, for example `sdk.memory.viewer` or `task_tracker.issue_editor`. |
| Object ref | Canonical URI owned by a namespace, for example `mem:record:...`, `task:issue:...`, `fi:...`, or `cnv:...`. |
| Root namespace | First segment before `:`, for example `mem`, `task`, `fi`, or `cnv`. |
| Namespace provider | Backend owner of object semantics. It resolves object actions and can return UI events. |
| Scene transport | Browser-local communication between host and surfaces, normally `postMessage` for iframes or callbacks for in-page components. |
| Event Bus | Runtime service-event stream, normally SSE. |
| Data Bus | Runtime data stream with subject/partition semantics, currently used by canvas-style live state. |

## Boundary Diagram

```text
                         app / website configuration
                                      |
                                      v
                         generic scene runtime
              mount surfaces, keep registry, relay events,
              broker context drag, dispatch surface commands
                                      |
             +------------------------+------------------------+
             |                        |                        |
             v                        v                        v
        chat surface            canvas surface            task surface
    local chat contract       local board contract      local editor/list contract
             |                        |                        |
             +------------ object refs stay opaque ------------+
                                      |
                                      v
                           namespace providers
             object.action(open), object.get, block.produce/render
```

The scene knows aliases, routes, target-surface ids, accepted namespaces,
subscriptions, readiness policies, and presentation config. It does not derive
local entity ids from object refs. The object URI is passed intact to the
surface and to the provider.

## Scene Configuration

A scene is configured, not edited per component. The configuration should be
loadable by a website page, by an app-owned main view, and by the future
`@kdcube/components-core/scene` package.

Target shape:

```json
{
  "profile": "demo",
  "runtimes": {
    "demo": {
      "origin": "https://demo.kdcube.tech",
      "tenant": "demo",
      "project": "demo",
      "auth": {
        "required": true,
        "provider": "cognito-demo"
      }
    },
    "dev": {
      "origin": "https://dev.kdcube.tech",
      "tenant": "demo",
      "project": "demo-march",
      "auth": {
        "required": false
      }
    }
  },
  "namespacePresentation": {
    "runtimeAlias": "demo",
    "appAlias": "versatile",
    "endpoint": "public/namespace_presentation_config",
    "delivery": "config-handshake"
  },
  "surfaces": {
    "chat": {
      "surface_ref": "website.chat",
      "target_surfaces": ["sdk.chat.context"],
      "runtimeAlias": "demo",
      "appAlias": "versatile",
      "route": "public/widgets/versatile_chat",
      "mount": { "mode": "inline" },
      "authRequired": true,
      "ready": { "type": "message", "messageType": "kdcube-chat-ready" },
      "commands": ["attach", "open", "focus"],
      "subscriptions": [],
      "contextDrag": { "source": true, "target": true }
    },
    "pinboard": {
      "surface_ref": "website.pinboard",
      "target_surfaces": ["sdk.canvas.pinboard"],
      "runtimeAlias": "demo",
      "appAlias": "versatile",
      "route": "public/widgets/pinboard",
      "mount": { "mode": "summoned", "rail": "pinboard" },
      "authRequired": true,
      "ready": { "type": "config-handshake" },
      "commands": ["pin", "open", "focus"],
      "dropTargets": [
        {
          "acceptsRootNamespaces": ["*"],
          "effect": "pin"
        }
      ],
      "subscriptions": [
        {
          "bus": "data-bus",
          "transport": "self",
          "subjects": ["canvas.patch"]
        }
      ],
      "contextDrag": { "source": true, "target": true }
    },
    "task_editor": {
      "surface_ref": "website.task_editor",
      "target_surfaces": ["task_tracker.issue_editor"],
      "runtimeAlias": "demo",
      "appAlias": "taskTracker",
      "route": "public/widgets/task_tracker_wizard?host_controls=1",
      "mount": { "mode": "summoned", "rail": "tasks" },
      "authRequired": true,
      "ready": { "type": "message", "messageType": "kdcube-task-tracker-wizard-ready" },
      "commands": ["open", "create", "refresh", "close"],
      "dropTargets": [
        {
          "acceptsRootNamespaces": ["task"],
          "effect": "open",
          "requestedTargetSurface": "task_tracker.issue_editor"
        }
      ],
      "subscriptions": [
        {
          "bus": "event-bus",
          "transport": "scene",
          "events": ["task_tracker.task.changed"]
        }
      ],
      "contextDrag": { "source": false, "target": true }
    }
  }
}
```

### Runtime Config

| Field | Meaning |
| --- | --- |
| `runtimes.<alias>.origin` | Browser origin serving app endpoints and widgets. |
| `runtimes.<alias>.tenant` / `project` | Data scope used by that runtime. |
| `runtimes.<alias>.auth` | Authentication provider and whether authenticated-only surfaces may mount. |
| `appAlias` | Scene-local app handle used to form widget routes. Internally this maps to a bundle id. |

A mixed-runtime scene can mount some surfaces from `demo.kdcube.tech` and other
surfaces from `dev.kdcube.tech`. Each surface declares its runtime. The scene
opens one Event Bus relay per runtime scope only when at least one surface
claims scene-owned delivery for that scope.

### Surface Config

| Field | Meaning |
| --- | --- |
| `surface_ref` | Stable host-local identity for the mounted surface instance. |
| `target_surfaces` | Logical provider target ids that this surface can receive. |
| `runtimeAlias` / `appAlias` | Where the iframe/component is served from. |
| `route` | Route relative to the app's widget/public route root. |
| `mount` | Inline, docked, rail-summoned, modal, or overlay placement. |
| `authRequired` | Surface is visible/mountable only after scene auth reports an authenticated user. |
| `ready` | How the scene knows commands can be flushed. |
| `commands` | Generic actions the surface accepts. |
| `dropTargets` | Root namespaces and effects accepted by this surface. |
| `subscriptions` | Event Bus/Data Bus claims and transport ownership. |
| `contextDrag` | Whether the surface can be a drag source and/or drop target. |

Scene core should build surface registry entries from this data. Adding a new
surface should add configuration and a component-side command handler, not a new
`if (alias === "...")` branch in scene core.

### Namespace Presentation Config

Namespace presentation is the shared visual map keyed by root namespace:

```json
{
  "mem": { "label": "Memory", "color": "#16a34a" },
  "task": { "label": "Task", "color": "#2563eb" },
  "fi": { "label": "File", "color": "#ca8a04" },
  "cnv": { "label": "Canvas", "color": "#7c3aed" }
}
```

Consumers:

| Consumer | Use |
| --- | --- |
| Scene drag overlay | Colors matching target zones while an object is dragged. |
| Chat widget | Colors attached context chips and namespaced refs. |
| Canvas/pinboard | Colors pins and object cards by represented namespace. |
| Search/object widgets | Colors result chips/cards by represented namespace. |

The scene normally fetches namespace presentation once and passes it to embedded
surfaces through config handshake. A standalone component can fetch the same
public app endpoint directly when no host config arrives. That fallback is a
component mode, not the normal embedded scene path.

## Scene Functionality

The generic scene component should implement these capabilities.

| Capability | Scene responsibility | Component responsibility |
| --- | --- | --- |
| Config/profile loading | Resolve selected profile and runtime/app/surface declarations. | Accept config handshake or standalone config. |
| Auth gating | Show anonymous/public surfaces; reveal authenticated surfaces when host auth reports authenticated. | Treat auth material as runtime config, not as a polling signal. |
| Surface mounting | Mount inline, rail, modal, overlay, and summoned surfaces from config. | Render local UI and send readiness. |
| Readiness queue | Queue commands until surface readiness policy is satisfied. | Emit ready/closed messages or acknowledge config handshake. |
| Namespace presentation | Fetch once per configured provider and fan out to surfaces. | Render chips/cards/overlays from the supplied map. |
| Surface commands | Emit `kdcube.surface.command` with target/action/object_ref/context. | Interpret the command locally and call local APIs as needed. |
| Provider-backed open | Call `object.action(open, object_ref, requestedTargetSurface)` and route provider `ui_event.target_surface`. | Load/focus object after receiving command. |
| Context drag | Track active drag context, draw/drop overlays, resolve open/pin/attach effects. | Emit canonical context drag messages and accept generic commands. |
| Event relay | Maintain subscriber registry; open Event Bus relay per runtime scope when claimed. | Claim scene transport or own its stream based on config. |
| Data Bus relay | Preserve subject/partition boundaries when scene-owned Data Bus relay is configured. | Use self-owned Data Bus where configured. |
| Layering/windowing | Apply tiered z-index, focus, docking, rail, modal and close behavior from surface config. | Report close/save/delete states where relevant. |
| Diagnostics | Log config, subscription, event, command, readiness, and drop routing stages. | Log command receipt, refresh/update work, and command errors. |

## Runtime Messages

### Config Handshake

Scene participation is explicit. The embedding scene should mark iframe URLs or
bootstrap config with a flag such as:

```text
scene=1
```

or:

```json
{
  "scene": {
    "embedded": true,
    "configSource": "host"
  }
}
```

Component boot behavior:

| Boot mode | Component behavior |
| --- | --- |
| `scene.embedded=true` or URL `scene=1` | Send `CONFIG_REQUEST`, wait for host `CONFIG_RESPONSE`, then use host-provided runtime config. A bounded error is acceptable if the host never responds. |
| Standalone route/app page | Use route/bootstrap config and configured self transports. |
| Unknown mode | Use the component's explicit default. For app widgets intended to be embeddable, the route should make this explicit so the component does not poll/probe. |

This avoids profile polling and repeated unauthenticated probes. Authenticated
state arrives from host config or from standalone route config. A role-specific
backend call can run only after authenticated state is known.

Embedded surfaces request config from the host:

```json
{
  "type": "CONFIG_REQUEST",
  "identity": "TASK_TRACKER_WIZARD_WIDGET",
  "requestedFields": ["runtime", "auth", "namespaceStyles", "scene"]
}
```

The host replies with runtime data and scene participation data:

```json
{
  "type": "CONFIG_RESPONSE",
  "identity": "TASK_TRACKER_WIZARD_WIDGET",
  "config": {
    "baseUrl": "https://demo.kdcube.tech",
    "tenant": "demo",
    "project": "demo",
    "auth": { "accessToken": "..." },
    "namespaceStyles": {
      "task": { "label": "Task", "color": "#2563eb" }
    },
    "scene": {
      "embedded": true,
      "surface_ref": "website.task_editor",
      "target_surfaces": ["task_tracker.issue_editor"],
      "liveEventsTransport": "scene"
    }
  }
}
```

A component mounted standalone receives equivalent data from its own page route
or app config. The component decides transport mode from explicit config:

```text
liveEventsTransport = "scene" -> send scene subscription claim
liveEventsTransport = "sse"   -> open own Event Bus stream
liveEventsTransport = "none"  -> no live stream
```

### Surface Ready

Readiness message shape should be generic:

```json
{
  "type": "kdcube.surface.ready",
  "surface_ref": "website.task_editor",
  "target_surfaces": ["task_tracker.issue_editor"]
}
```

During migration, a scene can map older ready messages through configuration:

```json
{
  "ready": {
    "type": "message",
    "messageType": "kdcube-task-tracker-wizard-ready"
  }
}
```

The target state remains generic: the surface is ready and queued commands can
flush.

### Surface Command

Scene-to-surface command:

```json
{
  "type": "kdcube.surface.command",
  "surface_ref": "website.task_editor",
  "target_surface": "task_tracker.issue_editor",
  "action": "open",
  "object_ref": "task:issue:ticket_123",
  "context": {
    "ref": "task:issue:ticket_123",
    "label": "Cancel membership"
  },
  "source": {
    "surface_ref": "website.pinboard"
  }
}
```

Command fields:

| Field | Meaning |
| --- | --- |
| `type` | Always `kdcube.surface.command` for scene commands. |
| `surface_ref` | Concrete mounted surface instance that receives the command. |
| `target_surface` | Provider/logical target id. |
| `action` | Generic action such as `open`, `create`, `pin`, `attach`, `refresh`, `focus`, `close`. |
| `object_ref` | Canonical object URI when the action concerns an object. |
| `context` | Optional context-pin payload, kept intact. |
| `source` | Optional source metadata for diagnostics and policy. |

Surface-side handlers translate this command into the component's local state
updates, API calls, and view focus behavior.

### Context Drag

Source-to-scene drag start:

```json
{
  "type": "kdcube-context-drag-start",
  "source_surface_ref": "website.pinboard",
  "contexts": [
    {
      "ref": "task:issue:ticket_123",
      "label": "Cancel membership",
      "kind": "object.ref"
    }
  ]
}
```

Scene behavior:

```text
normalize contexts
  -> derive root namespace from context.ref
  -> activate configured drop targets
  -> apply namespace presentation style to overlays
  -> on drop, deliver configured effect:
       attach -> kdcube.surface.command(action="attach")
       pin    -> kdcube.surface.command(action="pin")
       open   -> provider object.action(open), then command(action="open")
```

The scene keeps only transient drag state. Object identity remains `context.ref`.

### Event Subscription

Widget-to-scene subscription claim:

```json
{
  "type": "kdcube-scene-subscribe",
  "surface_ref": "website.usage_card",
  "subscriptions": [
    {
      "id": "usage-card-accounting-refresh",
      "bus": "event-bus",
      "runtimeAlias": "demo",
      "events": ["accounting.usage"],
      "transport": "scene",
      "forward": {
        "type": "kdcube.surface.command",
        "action": "refresh"
      },
      "debounceMs": 800
    }
  ]
}
```

Scene behavior:

```text
subscription claim
  -> register surface subscriber
  -> ensure one relay per runtime scope
  -> receive Event Bus envelope
  -> match event type/channel
  -> debounce if configured
  -> dispatch kdcube.surface.command(action="refresh")
```

The same pattern should support Data Bus later with subject/partition matching:

```json
{
  "bus": "data-bus",
  "runtimeAlias": "demo",
  "subjects": ["canvas.patch"],
  "partitions": ["board:main"],
  "transport": "scene"
}
```

Data Bus forwarding must preserve subject and partition metadata.

## Provider-Backed Open Routing

Provider-backed open routing means the scene asks the namespace owner what the
default UI effect is for an object. The scene then routes only the provider's
response.

Flow:

```text
drop/open request
  object_ref = task:issue:ticket_123
  requestedTargetSurface = task_tracker.issue_editor
        |
        v
named-service object.action(open)
        |
        v
provider validates the full object_ref and requested surface
        |
        v
provider response:
  ui_event.target_surface = task_tracker.issue_editor
  ui_event.object_ref = task:issue:ticket_123
        |
        v
scene finds surface registered for target_surface
        |
        v
scene sends kdcube.surface.command(action="open")
```

This is how unknown-yet subsystems join the ecosystem. The scene can route a new
namespace when:

1. the provider resolves `object.action(open)`;
2. a scene surface is registered for the returned `target_surface`;
3. that surface implements the generic `kdcube.surface.command` contract.

## Component Contract

A scene-compatible component should implement this contract.

### Required Inputs

| Input | Meaning |
| --- | --- |
| Runtime config | Base URL, tenant, project, auth headers/tokens, app id. |
| Scene config | Whether embedded, surface ref, target surfaces, transport mode, namespace styles. |
| Generic commands | `kdcube.surface.command` messages or direct callback equivalent. |
| Namespace styles | Root-namespace presentation map. |

### Required Outputs

| Output | Meaning |
| --- | --- |
| Config request | For iframe widgets, ask the host for runtime config. |
| Ready status | Tell the host when queued commands can flush. |
| Context drag | Emit canonical context drag start/end when the component exposes draggable objects. |
| Subscription claim | When configured for scene transport, declare Event Bus/Data Bus needs. |
| Local lifecycle | Tell the host when the surface closes, deletes, saves, or becomes unavailable if that affects scene state. |

### Command Handling

Every component should have one scene command entrypoint:

```ts
function handleSceneSurfaceCommand(command: {
  type: "kdcube.surface.command"
  target_surface?: string
  action: string
  object_ref?: string
  context?: Record<string, unknown>
}): boolean
```

Example action meanings:

| Action | Meaning |
| --- | --- |
| `open` | Load/focus `object_ref` in the component's own UI. |
| `create` | Open the component's create flow. |
| `attach` | Attach `context` to the component's local draft/input. |
| `pin` | Add `context` to a board/canvas at optional coordinates. |
| `refresh` | Repull local backend state after a runtime event. |
| `focus` | Bring the component's current view to attention. |
| `close` | Close the component's current view or acknowledge host close. |

Components may expose richer local APIs internally. The scene contract remains
the generic command entrypoint.

### Standalone Mode

A component can run outside a scene. In standalone mode it receives config from
its own route or app page and opens its own live transports when configured:

```text
embedded scene + liveEventsTransport="scene"
  component sends subscription claim to parent

standalone route + liveEventsTransport="sse"
  component opens its own EventSource

standalone route + liveEventsTransport="none"
  component uses manual refresh / backend pulls only
```

The component should not infer authenticated state by polling `/profile`.
Authenticated state should arrive through host auth config, route-provided
runtime config, or a one-time authenticated app data call that is only made
after auth is known to be present.

## Scene Configurator Checklist

When composing a scene:

1. Declare every runtime scope used by the page.
2. Declare every app alias and widget/component route.
3. Declare every surface alias and its `target_surfaces`.
4. Declare mount mode, size, layer tier, and rail position.
5. Declare auth requirements for each surface.
6. Declare readiness policy for each iframe surface.
7. Declare accepted root namespaces and drop effects.
8. Declare Event Bus/Data Bus subscription ownership:
   - `scene` when the host relays;
   - `sse` or `data-bus-self` when the component owns the stream;
   - `none` when the component has no live stream.
9. Declare namespace presentation provider once.
10. Declare widget-to-host commands as `kdcube.surface.command` envelopes.

## Component Developer Checklist

When making a component scene-compatible:

1. Accept `CONFIG_RESPONSE` or an equivalent direct config object.
2. Expose one `handleSceneSurfaceCommand` entrypoint.
3. Preserve `object_ref` exactly as received.
4. Use local API/provider calls to load/focus/render object details.
5. Emit `kdcube.surface.ready` or configured ready alias.
6. Emit canonical context drag messages for draggable object refs.
7. Send scene subscription claims only when configured for scene transport.
8. Use supplied namespace presentation map for chips/cards.
9. Log command receipt, ignored command reason, refresh/update work, and errors.
10. Keep local command translation inside the component package or local wrapper.

## Components To Align

These are the concrete participants that should be aligned to this contract.
Private app source paths are intentionally omitted from this public SDK doc.

| Component / surface | Current role in demo scene | Target scene contract | Work to verify |
| --- | --- | --- | --- |
| Chat widget | Main assistant/chat surface; accepts attached contexts; emits accounting events and chat-local stream events. | Receives `kdcube.surface.command(action="attach"|"open")`; emits canonical context drag from chips/results. | Verify generic command handler, context attachment, conversation load, and namespace chip styling. |
| Canvas / pinboard | Board of pinned context objects; drag source and drop target; owns canvas live state. | Receives `kdcube.surface.command(action="pin"|"open"|"refresh")`; emits canonical context drag; consumes namespace styles; Data Bus ownership remains configured. | Verify pinning from chat/memory/task/file refs, cross-surface drag, namespace colors, and Data Bus self mode. |
| Memory search/list/viewer | Memory search and memory-owned object viewer. | Receives `kdcube.surface.command(action="open")` for `sdk.memory.viewer`; emits canonical drag for `mem:*`; consumes namespace styles. | Verify memory open from chat/canvas/list and no scene parsing of memory ids. |
| Task list | Task search/list surface. | Receives generic `open`, `refresh`, and possibly `create`; claims task change events when scene transport is configured. | Verify task changed event refreshes list and no scene-specific task message builder is needed. |
| Task editor/wizard | Task issue editor and creation surface. | Receives generic `open`, `create`, `refresh`, `close`; claims task change events when scene transport is configured. | Verify named-service task updates refresh an already-open editor, and delete closes/clears the surface through generic lifecycle. |
| Usage card | Budget/usage surface. | Claims `accounting.usage` over scene Event Bus and receives `kdcube.surface.command(action="refresh")`; standalone mode uses direct refresh only or configured stream. | Verify every accounting event reaching scene refreshes the card once after debounce. |
| Stats dashboard widget | Analytics/telemetry surface. | Uses `liveEventsTransport` config: self SSE when hosted from another runtime, scene relay when configured local. | Verify local profile supports both `scene` and `sse` modes and mixed-runtime profile uses self SSE. |
| News preview widget | Public/news preview surface. | Uses `liveEventsTransport` config; no profile polling in anonymous mode; admin/full view owns admin stream only when active. | Verify anonymous preview is quiet, scene mode claims events, and self mode opens stream only when configured. |
| Telegram miniapp example | Example external/miniapplike widget. | Declares its own event/data transport mode; uses generic config handshake if embedded. | Verify no duplicate stream when embedded in a scene. |

## Landing Scene Status

The website landing scene and versatile scene should stay aligned with this
table.

| Area | Status |
| --- | --- |
| Surface specs | Config drives aliases, routes, sizes, rails, auth visibility, readiness, commands, and drop targets. |
| Cross-surface commands | Host sends `kdcube.surface.command`; components interpret object refs locally. |
| Event relay | Surface subscription config claims events; event broker dispatches configured surface commands. |
| Usage-card readiness | Readiness is configured as a timeout policy; component readiness events can replace it. |
| Task/memory/chat routing | Config maps target surfaces to mounted aliases. |
| Namespace colors | Scene fetches namespace presentation once; client defaults are only fallback for standalone/unavailable config. |
| Widget subscriptions | Widgets can declare subscriptions; scene config can provide host defaults. |
| Host API | Public host API opens by target surface and generic command. |

## Diagnostics

The scene should produce enough console traces to answer these questions:

| Question | Trace |
| --- | --- |
| Which profile loaded? | `scene config loaded` with profile and runtimes. |
| Which surfaces mounted? | `surface registered/mounted` with alias, surface ref, target surfaces. |
| Was namespace presentation loaded? | `namespace presentation loaded` with namespace count and source. |
| Did a widget request config? | `config request received` with identity, alias, source frame. |
| Did a surface become ready? | `surface ready` with alias, surface ref, queued count flushed. |
| Did a widget claim events? | `scene subscriber registered` with bus, events, runtime scope. |
| Did the scene receive a runtime event? | `scene event received` with bus, event type, channel, runtime scope. |
| Did the event dispatch? | `scene event dispatched` with target surface/alias and command action. |
| Did a drag arm targets? | `context drag armed` with root namespaces and target aliases. |
| Did an object open route? | `surface command dispatched` with target surface, action, object ref, queued/dispatched. |
| Was a route impossible? | Explicit unavailable/error trace with missing config key or target surface. |

Widgets should acknowledge meaningful work:

```text
usage card: refresh requested -> refresh complete/error
task editor: command received -> issue loaded/refreshed/deleted
canvas: pin command received -> card added/update failed
chat: attach command received -> context chip added/rejected
```

## Implementation Order

This is the implementation sequence to make the current scene match the target
contract.

1. Move website scene surface declarations into scene config.
2. Replace host-side task/memory/chat command builders with the generic
   `kdcube.surface.command` envelope.
3. Add or verify `kdcube.surface.command` handlers in every participating
   widget.
4. Move readiness special cases to config and widget ready messages.
5. Move Event Bus relay behavior to declared subscriptions and widget claims.
6. Keep Data Bus ownership explicit per surface.
7. Make namespace presentation scene-owned while embedded, with standalone
   fallback only for standalone components.
8. Reassess website scene against the gap table above.
9. Convert the verified host logic into
   `@kdcube/components-core/scene`.
10. Fill component recipes with the now-working config, command, event, and
    transport examples.

## Acceptance Criteria

The scene is generic when all of the following are true:

| Criterion | Expected result |
| --- | --- |
| Add a new surface by config | The scene can mount/register it without editing scene core. |
| Add a new provider namespace | The scene can color, drag, pin, and provider-open it when namespace presentation and provider `object.action(open)` exist. |
| Drop object on owning surface | Scene calls provider and dispatches generic `open` command to returned `target_surface`. |
| Drop object on chat | Scene dispatches generic `attach` command; chat owns chip creation. |
| Drop object on canvas | Scene dispatches generic `pin` command; canvas owns card creation. |
| Runtime event reaches scene | Event broker matches declared subscriptions and dispatches generic `refresh` or configured command. |
| Widget runs standalone | Widget uses standalone config and configured self transport without scene assumptions. |
| Mixed-runtime scene | Each surface uses its configured runtime; Event Bus relays are scoped by runtime and opened only when claimed. |
| Namespace color changes server-side | Chat, overlay, and canvas update from the same namespace presentation map. |
| Task/memory IDs change shape | Scene behavior stays unchanged because scene passes opaque object refs. |

## What Remains Page-Specific

Some choices remain host/page configuration rather than reusable scene core:

| Page-specific concern | Reason |
| --- | --- |
| Landing page marketing text and layout bands | This belongs to the website page, not the scene runtime. |
| Which apps are shown in the demo scene | This is a profile/config decision. |
| Rail labels and icon artwork | This is host presentation, declared in config. |
| Exact panel sizes and responsive layout | This is host composition, declared in config and CSS tokens. |
| Component-local state payloads | Components translate `kdcube.surface.command` into their own local store/API calls. |

Everything else listed in this document should become reusable scene behavior or
surface-owned behavior.

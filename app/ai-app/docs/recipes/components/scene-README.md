# Recipe: Scene

A scene is a host page that composes app widgets into one working surface. It owns layout, frame mounting, cross-frame delivery, shared configuration, and event fan-out. App widgets still own their domain behavior.

Current platform code still uses the word `bundle` in many APIs. In this recipe, an app is the user-facing concept; a bundle id is the current internal app package identifier.

Read [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
first for the interaction planes. This recipe covers only the browser scene
plane.

## Runtime Shape

```text
browser tab
  scene host
    config loader
      selects profile by host/query
      resolves runtime origin, tenant, project, app ids
      merges scene defaults with profile overrides
    surface registry
      alias -> iframe route
      target_surface -> command contract
      widget alias -> event subscription claims
    event bridge
      one Event Bus stream per authenticated scene runtime
      per-widget subscription claims
      postMessage fan-out to subscribed iframes
    drag broker
      source widget emits canonical context drag
      scene overlays matching target surfaces
      scene routes drop to attach, pin, or provider-backed open
    iframes
      chat
      canvas/pinboard
      memory viewer/list
      task/list/editor
      stats/news/usage widgets
```

## Config Contract

The scene config is data, not hardcoded widget logic. A scene profile should declare:

```json
{
  "runtime": {
    "origin": "https://runtime.example",
    "tenant": "demo",
    "project": "demo",
    "apps": {
      "workspace": "workspace@2026-03-31-13-36",
      "stats": "kdcube.stats@2026-05-20-12-05"
    },
    "appRuntimes": {
      "stats": {
        "origin": "https://dev.example",
        "tenant": "demo",
        "project": "telemetry",
        "dataTenant": "demo",
        "dataProject": "demo"
      }
    }
  },
  "contextDropTargets": {
    "workspace": {
      "surfaceRef": "website.chat",
      "accepts": "context",
      "dropEffect": "attach",
      "targetSurface": "sdk.chat.context",
      "action": "attach"
    }
  },
  "widgetConfig": {
    "stats": { "liveEventsTransport": "sse" },
    "usage_card": { "liveEventsTransport": "scene" }
  },
  "surfaceCommandContracts": {
    "task.issue.editor": {
      "alias": "task_wizard",
      "targetSurfaces": ["task_tracker.issue_editor"],
      "action": "open"
    }
  },
  "viewCommandContracts": {
    "task_tracker_tasks": {
      "setViewType": "kdcube-set-view",
      "widget": "task_tracker_tasks"
    }
  }
}
```

`liveEventsTransport` is per widget and per profile. Use `scene` when the widget should receive Event Bus messages through the host. Use `sse` when the widget is intentionally connected to another runtime or must own its live stream.

An app-owned scene host declares the same routing server-side. An external
panel mounts another app's widget and maps target surfaces to per-surface
descriptors, including editor-surface routing for provider opens:

```yaml
external_panels:
  - id: task_panel
    bundle_id: task-tracker@1-0
    widget_alias: task_tracker_tasks
    widget_message_type: kdcube-task-tracker-widget-command
    open_message_types: [kdcube-task-tracker-open-issue, kdcube-task-tracker-create-issue]
    surfaces:
      task_tracker.issue_list:   { expanded: false, command: {action: refresh} }
      task_tracker.issue_editor: { expanded: true,  command_from_open: provider_surface_open }
```

With this shape, opening a task pin resolves to
`ui_event.target_surface = task_tracker.issue_editor` and the scene summons
the panel expanded with the issue loaded (the open payload is forwarded as
the widget command). Descriptor semantics:
[External Panels And Provider-Open Routing](../../sdk/solutions/scene/config/README.md#external-panels-and-provider-open-routing).

## Event Flow

```text
widget loads
  -> receives runtime config from iframe URL and/or host config message
  -> if liveEventsTransport == scene:
       postMessage(kdcube-scene-subscribe, filters)
  -> if liveEventsTransport == sse:
       opens its own runtime stream

server emits service event
  -> scene Event Bus stream receives event
  -> scene checks registered subscriptions
  -> scene postMessage(kdcube.surface.command or configured event envelope) to matching widgets
  -> widget handles event or refreshes its own backend snapshot
```

The scene should log subscription registration, stream connection, event receipt, and dispatch counts. A widget should log whether it selected `scene`, `sse`, or no live transport.

## Drag Flow

```text
source widget
  postMessage(kdcube-context-drag-start, { contexts: [{ ref: "mem:record:..." }] })

scene
  normalizes the full object_ref
  highlights configured candidate targets
  never parses mem/task/conv semantics

drop target
  chat     -> kdcube.surface.command target_surface=sdk.chat.context action=attach
  canvas   -> kdcube.surface.command target_surface=sdk.canvas.pinboard action=pin
  owner UI -> object.action(open) -> ui_event.target_surface -> kdcube.surface.command action=open
```

Presentation styling belongs to namespace presentation config or resolver
presentation metadata. Canvas, chat, and overlay rendering consume the same
configured colors/icons. Actions come from provider resolvers, not from local
namespace parsing.

## Current Gaps

- Some widgets can operate either with scene Event Bus delivery or their own SSE; the selected mode must be explicit in app/scene config.
- Data Bus forwarding uses the same subscription idea but still needs a first-class scene adapter.
- Website config may still use explicit selector patterns for transitional compatibility. Canonical open behavior is provider-owned `object.action(open, object_ref, target_surface)`.

## Related Docs

- [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
- [Component Recipes](./README.md)
- [Components Ecosystem Architecture](../../sdk/solutions/ecosystem-component/components-ecosystem-README.md)
- [Scene Composition](../../sdk/solutions/scene/scene-composition-README.md)
- [Scene Event Orchestration](../../sdk/solutions/scene/scene-event-orchestration-README.md)
- [Cross-Surface Context Drag](../../sdk/solutions/scene/cross-surface-context-drag-README.md)
- [Scene Surface Registry](../../sdk/solutions/scene/scene-surface-registry-README.md)
- [Event Bus And Data Bus](../../service/comm/conversation-event-bus-and-data-bus-README.md)

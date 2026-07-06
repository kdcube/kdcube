---
title: Versatile Scene SDK Components
kind: design-note
bundle_id: versatile@2026-03-31-13-36
updated_at: 2026-06-23
---

# Versatile Scene SDK Components

The active main view is `ui/scene`. It is a scene: an app-owned UX composition
layer that connects multiple subsystem surfaces into one workspace. It is not a
second custom chat implementation and it is not a canvas implementation.

Read these words first:

| Term | Meaning in this app |
| --- | --- |
| Scene host | `ui/scene/src/main.tsx`, the React page that lays out panels and mounts surfaces. |
| Surface | One UI participant in the scene: direct React component or iframe widget. |
| Direct component | Source imported into the scene bundle and rendered in the same React tree. |
| Iframe widget | Separately built UI surface mounted through a widget URL and controlled by `postMessage`. |
| Scene runtime | Headless routing helper from `@kdcube/components-core/scene`. It owns surface registry, dispatch, queueing, and readiness. |
| Surface registry | In-browser map built by the scene host with `sceneRuntime.registerSurface(...)`. |
| Provider/resolver | Backend owner of object semantics. It returns `ui_event.target_surface` for object opens. |

## Current Composition

The versatile scene uses both integration modes:

```text
ui/scene host page
  |
  +-- direct React component: CanvasBoard
  |     public import: @kdcube/components-react/canvas
  |     current source: sdk://solutions/canvas/ui/component
  |
  +-- iframe widget: versatile_chat
  |     source: sdk://solutions/chat/ui/widget
  |
  +-- iframe widget: memories
  |     source: sdk://context/memory/ui/widget/memories
  |
  +-- iframe widget: usage_card
  |     source: sdk://infra/economics/ui/widget/usage-card
  |
  +-- optional external iframe panel
        configured by surfaces.as_consumer.ui.scene.external_panels
```

Runtime connections:

```text
ui/scene host
  |
  +-- scene_surface_config operation
  |     reads surfaces.as_consumer.ui.scene
  |
  +-- namespace_presentation_config operation
  |     reads namespace_styles
  |
  +-- Data Bus subject canvas.patch
  |     carries ordered canvas revision writes
  |
  +-- Event Bus / service stream
  |     fan-out only for widget-declared scene subscriptions
  |
  +-- canvas_object_action operation
        delegates full object_ref actions to provider resolvers
```

Canvas is **not** inserted as an iframe in the main scene. It is imported and
rendered directly:

```ts
import { CanvasBoard } from "@kdcube/components-react/canvas"
```

Chat and memories are separate components too, but in this scene they are
integrated as iframe widgets. That is why they are not listed in
`ui/scene/vite.config.js`: the scene does not import their source. It builds
their widget URLs and communicates with them by config handshake and
`postMessage`.

## Component Package Direction

The long-term package shape is:

| Package family | Purpose |
| --- | --- |
| `@kdcube/components-core/*` | Headless implementations: stores, routing, runtime logic, protocol helpers. No React requirement. |
| `@kdcube/components-react/*` | React wrappers/components and reference React client implementations. |

The scene already uses that naming at the import boundary:

```ts
import { createSceneRuntime } from "@kdcube/components-core/scene"
import { CanvasBoard } from "@kdcube/components-react/canvas"
```

The app config materializes package sources with `npm://` shared sources:

```text
@kdcube/components-core/scene
  -> npm://components-core/src/scene

@kdcube/components-react/canvas
  -> npm://components-react/src/canvas
```

The scene also materializes `npm://components-core/src/canvas` and
`npm://components-core/src/events` because canvas ingress and scene event
claims are shared contracts. New app config should use `npm://` package
sources; any older `sdk://solutions/...` aliases are compatibility scaffolding.

## Config Shape

Compile-time shared sources are declared under `ui.main_view.shared_sources`.
Those are for code imported into the scene bundle:

```yaml
ui:
  main_view:
    src_folder: ui/scene
    shared_sources:
      components_core_scene:
        src_folder: npm://components-core/src/scene
        target: _shared/components-core/scene
      components_core_events:
        src_folder: npm://components-core/src/events
        target: _shared/components-core/events
      components_core_canvas:
        src_folder: npm://components-core/src/canvas
        target: _shared/components-core/canvas
      components_react_canvas:
        src_folder: npm://components-react/src/canvas
        target: _shared/components-react/canvas
```

Runtime iframe widgets are declared under `ui.widgets`. Those are built and
served separately:

```yaml
ui:
  widgets:
    versatile_chat:
      src_folder: sdk://solutions/chat/ui/widget
    memories:
      src_folder: sdk://context/memory/ui/widget/memories
    usage_card:
      src_folder: sdk://infra/economics/ui/widget/usage-card
    pinboard:
      src_folder: sdk://solutions/canvas/ui/widget/pinboard
      shared_sources:
        components_react_canvas:
          src_folder: npm://components-react/src/canvas
          target: _shared/components-react/canvas
        components_core_canvas:
          src_folder: npm://components-core/src/canvas
          target: _shared/components-core/canvas
```

The `pinboard` widget is the canvas-as-iframe path. It reuses the same canvas
React component, but hosts it inside its own widget. The versatile main scene
does not use that path for the primary canvas; it uses direct React embedding.

Scene surface composition is declared in server-side app config:

```yaml
surfaces:
  as_consumer:
    ui:
      scene:
        external_panels:
          - id: task_panel
            label: Tasks
            bundle_id: task-tracker@1-0
            widget_alias: task_tracker_tasks
            widget_message_type: kdcube-task-tracker-widget-command
            service_event_type: task_tracker.task.changed
            service_forward_message_type: kdcube-task-tracker-task-changed
            surfaces:
              task_tracker.issue_list:
                expanded: false
                command:
                  action: refresh
              task_tracker.issue_editor:
                expanded: true
                command_from_open: provider_surface_open
```

The browser scene reads this through `scene_surface_config`, mounts the iframe,
and registers each `target_surface` with `createSceneRuntime(...)`. The config
declares composition; object semantics still come from provider resolvers.

## How Chat Is Connected

The scene mounts chat as an iframe widget:

```text
scene host
  -> widgetUrl(ctx, "versatile_chat", chat params)
  -> <iframe src=...>
```

The scene passes chat-specific parameters in the widget URL:

| Parameter | Purpose |
| --- | --- |
| `chat_config_identity` | Lets the scene answer the correct config handshake. |
| `chat_event_prefix` | Names chat-emitted events for this host. |
| `chat_surface` | Identifies the chat surface to itself and the host. |
| `chat_canvas_state_event_source_id` | Event source id for whole-board context. |
| `chat_canvas_focus_event_source_id` | Event source id for focused cards/objects. |
| `chat_canvas_ingress_message` | Message type for dragging chat content into the board. |
| `chat_context_attach_message` | Message type for attaching context to chat. |
| `chat_context_focus_message` | Message type for focusing context into chat. |
| `chat_context_remove_message` | Message type emitted when user removes context. |

The scene keeps an iframe ref and posts commands to chat:

```text
scene host -> chat iframe postMessage
```

Chat also posts events back to the scene:

```text
chat iframe -> scene host postMessage
```

For example, when a chat context chip resolves an object-open action, chat sends
the resolver response to the scene. The scene dispatches it through the scene
runtime registry by `ui_event.target_surface`.

## Scene Event Claims

Widgets own their event subscriptions. The scene does not declare fallback
subscriptions for usage, stats, task tracker, or any other widget.

```text
widget iframe
  -> postMessage(kdcube-scene-subscribe, { alias, subscriptions })

ui/scene host
  -> createSceneEventBus.register(alias, subscriptions)
  -> subscribes only to channels required by registered claims

service event
  -> sceneEventBus.normalizeEvent("sse", ...)
  -> publish
  -> matching widget receives configured command/event envelope
```

If a widget does not update, logs should show whether the widget never sent a
claim, the scene rejected or misrouted the alias, or the provider event did not
match the claim.

## Scene Runtime Registry

The registry is built by the scene host at browser runtime. It is not a backend
registry and it is not stored centrally.

```text
scene host creates runtime
  -> const sceneRuntime = createSceneRuntime(...)

scene host creates surfaceRegistry
  -> "sdk.memory.viewer"
  -> "sdk.chat.viewer"
  -> configured external panel surfaces

scene host registers each entry
  -> sceneRuntime.registerSurface(targetSurface, registration)
```

Each registration contains host-owned UI mechanics:

| Adapter function | Meaning |
| --- | --- |
| `ensureOpen` | Open or reveal the panel/iframe/component. |
| `isReady` | Report whether the surface can receive a command now. |
| `postCommand` | Send the command by iframe `postMessage` or direct callback. |
| `commandFromOpen` | Convert provider `ui_event` into the target surface command. |

Example journey:

```text
chat context chip or canvas card
  object_ref = task:issue:ticket_123
        |
        v
backend provider/resolver returns:
  ui_event.target_surface = app.issue.viewer
        |
        v
sceneRuntime.dispatchSurfaceOpen(response, source)
        |
        v
registry finds app.issue.viewer
        |
        v
ensureOpen -> wait until ready -> postCommand
        |
        v
target iframe/component opens task:issue:ticket_123
```

The scene runtime does not know what `task:` means. The provider owns object
semantics. The target surface owns how it loads and renders the object.

## Canvas Integration

Canvas has two supported host shapes:

| Shape | Used by versatile main scene? | Description |
| --- | --- | --- |
| Direct React component | Yes | `CanvasBoard` is imported into `ui/scene` and rendered in the same React tree. |
| Iframe widget | Available, not primary | `pinboard` widget hosts `CanvasBoard` in an iframe and brokers events to its parent. |

The direct React path is used in the main scene because canvas is visually and
interactively central to the workspace. The scene owns the board placement,
drag/drop integration, Data Bus subscription, and chat/canvas coordination.

Canvas backend operations used by the scene:

| Operation | Purpose |
| --- | --- |
| `canvas_list` | List the current user's canvases for the scene story. |
| `canvas_read` | Read one canvas projection. |
| `canvas_attachment_upload` | Host local dropped files as canvas-owned attachments. |
| `canvas_object_action` | Call the resolver for a pinned object's full `object_ref`. |

Canvas mutations are published over Data Bus:

| Subject | Partition | Purpose |
| --- | --- | --- |
| `canvas.patch` | `object_ref` | Ordered canvas revision writes. |

Use generic canvas names. Do not prefix them with this bundle id. The scene is
demonstrating reusable SDK components, so the protocol names are `canvas.*`.

## Memory Widget Integration

The memory widget is mounted as an iframe. It owns memory UI behavior. The scene
only opens/reveals the iframe, sends view/open commands, tracks readiness, and
brokers context messages.

```text
memory widget drag
  -> kdcube-context-focus / kdcube-context-attach
  -> scene broker
  -> versatile_chat iframe
```

For object opens, the scene registers `sdk.memory.viewer`. The registration
maps provider open events into memory widget commands:

```text
ui_event.target_surface = sdk.memory.viewer
  -> ensure memory panel is open
  -> wait for memory widget readiness
  -> postMessage({ action: "open", object_ref, memory_id })
```

The scene does not call memory APIs directly to load the object.

## Object Resolvers And External Surfaces

The backend registers resolvers for object refs the scene can display:

| Namespace | Owner |
| --- | --- |
| `fi:` | ReAct event/artifact resolver |
| `cnv:` | canvas-owned object resolver |
| configured named-service namespaces, for example `mem:` or `task:` | named-service provider resolver |

The canvas card or chat context chip stores one canonical object ref. The
resolver owns preview, download, open, and rehost behavior.

The centralized backend resolver router may use the URI namespace as a private
dispatch key. The scene, canvas board, and chat widget pass the full
`object_ref`; they do not parse URI grammar to decide behavior.

Configured named-service resolvers are read from bundle props:

```yaml
surfaces:
  as_consumer:
    ui:
      canvas:
        resolvers:
        - kind: named_service
          namespace: mem
          allowed: [object.action]
        - kind: named_service
          namespace: task
          allowed: [object.action]
      scene:
        external_panels:
        - id: task_panel
          label: Tasks
          bundle_id: task-tracker@1-0
          widget_alias: task_tracker_tasks
          widget_message_type: task-widget-command
          surfaces:
            app.issue.viewer:
              expanded: true
              command_from_open: provider_surface_open
```

The SDK helper `register_configured_named_service_canvas_resolvers(...)`
registers configured named-service canvas resolvers into the canvas object
resolver registry. The same registry backs the scene canvas and the chat
widget object-action path through `canvas_object_action`.

Agent tool configuration controls model-callable tools. The `ui.canvas.resolvers`
list only enables generic canvas/chat resolution for that namespace. Canvas and
chat still send generic actions such as `open`, `preview`, `describe`, and
`capabilities` through the resolver; the owning provider decides whether each
action is valid.

For `open`, providers return a `ui_event.target_surface`. The scene maps that
surface through the scene runtime registry, opens the configured iframe or
component, and posts either the configured static command or the generic
provider surface command. The scene does not parse provider object refs or
hard-code provider widget ids.

## Connected Map

```text
server-side app config (bundles.yaml)
  |
  +-- ui.main_view.shared_sources
  |     -> npm://components-core/src/scene
  |     -> npm://components-core/src/canvas
  |     -> npm://components-core/src/events
  |     -> npm://components-react/src/canvas
  |
  +-- ui.widgets
  |     -> versatile_chat
  |     -> memories
  |     -> usage_card
  |     -> pinboard
  |
  +-- namespace_styles
  |     -> namespace_presentation_config
  |     -> CanvasBoard / chat chips / scene overlays
  |
  +-- surfaces.as_consumer.agents.main.tools
  |     -> named_services.* ReAct tools
  |
  +-- surfaces.as_consumer.agents.main.event_sources
  |     -> react.pull / block.produce for external refs
  |
  +-- surfaces.as_consumer.ui.canvas.resolvers
  |     -> canvas_object_action provider dispatch
  |
  +-- surfaces.as_consumer.ui.scene.external_panels
        -> scene surface registry target_surface entries

browser ui/scene
  |
  +-- mounts widgets / CanvasBoard
  +-- receives widget event claims
  +-- routes context drag and kdcube.canvas.ingress
  +-- calls canvas_object_action(open, object_ref, target_surface)
  +-- dispatches returned ui_event.target_surface to mounted surface
```

## Platform Docs

- `docs/sdk/npm/components-core/scene-README.md`
- `docs/sdk/npm/components-core/context-drag-README.md`
- `docs/sdk/npm/components-core/canvas-pin-board-README.md`
- `docs/sdk/npm/widget-integration-README.md`
- `docs/sdk/solutions/scene/config/README.md`
- `docs/sdk/solutions/scene/cross-surface-context-drag-README.md`
- `docs/sdk/solutions/canvas/pin-integration-README.md`
- `docs/sdk/namespace-services/README.md`

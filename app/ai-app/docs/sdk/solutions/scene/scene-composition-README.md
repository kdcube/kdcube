---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
title: "Scene Composition"
summary: "How an app assembles a host scene from configured UI surfaces, shared scene runtime sources, config handshakes, local message routing, and optional Data Bus subscriptions."
status: draft
tags: ["sdk", "solutions", "scene", "surface", "widget", "iframe", "composition", "data-bus", "postmessage"]
updated_at: 2026-06-17
keywords:
  [
    "scene composition",
    "host scene",
    "ui.main_view src_folder",
    "ui.main_view shared_sources",
    "components_core_scene",
    "ui.widgets alias",
    "CONFIG_REQUEST CONFIG_RESPONSE",
    "target_surface",
    "multi component scene",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/ui-components-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Scene Composition

Read these words first:

| Term | Meaning |
| --- | --- |
| App | The product/bundle that owns the user experience. |
| Scene | The app-owned UX composition layer: one page or widget shell that connects several UI parts. |
| Host | The browser page/component that runs the scene. |
| Surface | One UI part inside the scene: iframe widget, in-page component, external panel, or another view. |
| Source surface | The surface where the user starts an action. |
| Target surface | The surface that should react. |
| Scene runtime | A small routing helper used by the host: surface registry, dispatch, queueing, readiness. |
| Namespace provider / resolver | Backend owner of object meaning. It decides what an object ref means and which UI effect should happen. |

A scene is an app-owned UX composition layer. It lets a product connect several
subsystems into one coherent workspace without making each subsystem know about
all the others. Those surfaces are chosen by the app. They may be SDK widgets,
app widgets, in-page components, external iframe panels, or any combination of
those.

KDCube ships a reusable SDK scene runtime for this pattern. A product can use
that runtime, wrap it, or implement the same contract in its own scene. The
contract is generic: it does not require chat, canvas, memory, tasks, metrics,
or any other specific component. The host scene supplies the visual shell and
registers whichever surfaces it actually contains.

This doc explains the assembly pattern:

```text
app config
  -> host scene source
  -> shared scene runtime source
  -> configured surfaces
  -> runtime config handshake
  -> local surface commands
  -> optional Data Bus subscriptions
```

Object-open routing is covered separately in
[Scene Surface Registry](scene-surface-registry-README.md).
Live event routing between a scene host and iframe widgets is covered in
[Scene Event Orchestration](scene-event-orchestration-README.md).

High-level communication:

```text
User
  example: person clicks an object chip/card
  |
  v
Scene host page
  example: app main page or embedded app widget
  |
  | mounts + configures
  v
Surfaces in the scene
  example: issue viewer iframe, metrics panel, chat widget, board component
  |
  | local postMessage/callbacks for UI coordination
  v
Scene runtime registry
  example: createSceneRuntime().registerSurface("app.issue.viewer", ...)
  |
  | routes by target_surface
  v
Target surface
  example: "app.issue.viewer" receives { action: "open", object_ref: ... }

When object meaning is needed:

Source surface or host
  example: card/chip with object_ref "task:issue:ticket_123"
  -> app backend operation
     example: object.action({ action: "open", object_ref })
  -> namespace provider / resolver
     example: provider for namespace "task"
  -> response with ui_event.target_surface
     example: target_surface = "app.issue.viewer"
  -> scene runtime registry
     example: surface adapter is found and opened
  -> target surface command
     example: postMessage({ action: "open", object_ref, view: "expanded" })
```

## What The Host Owns

| Concern | Owner |
| --- | --- |
| Which surfaces are mounted | Scene host. |
| Where those surfaces render | Scene host. |
| Runtime config delivery | Scene host relay. |
| Local surface messages | Scene host broker. |
| Panel size, drag, z-order, shell CSS | Scene host. |
| Namespace visual styles | Scene host config, passed through to surfaces by root namespace. |
| Object identity and semantics | Namespace provider/resolver. |
| What a surface renders and how it behaves | The target surface. |

The host is a composition and transport layer. It does not read objects or
implement namespace behavior. It relays config and routes commands.
Namespace styles are presentation hints only. A scene may color `mem:*` cards
green and `task:*` cards blue, but that does not make the scene responsible for
memory or task semantics; object actions still go through the namespace
provider/resolver.

## Reusable Runtime

The reusable part of a scene lives in the SDK as a headless TypeScript runtime:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/scene/src
  index.ts      public exports
  runtime.ts    surface registry, object-open dispatch, CONFIG helpers
  types.ts      message, registration, and dispatch result types
```

The runtime has no React dependency and no page layout. It owns only the
orchestration kernel:

```text
surface message / resolver response
  -> normalize object-open request
  -> read Provider.ui_event.target_surface
  -> find host-registered surface
  -> ask the host adapter to open/focus/mount that surface
  -> queue and flush the surface command
```

For cross-surface drag/drop, the same runtime exports
`createContextDragBroker()`. The broker owns only transient browser drag state,
context normalization, root-namespace target matching, and provider-backed
`object.action(open)` for owning-surface drops. The host still owns overlay
geometry, panel chrome, and local `pin`/`attach` delivery effects.

A main app UI, a nested app widget, and an external website page can reuse this
runtime while keeping different markup, CSS, surfaces, and auth shell.

## Configure The Scene Source

The app declares the scene host as its `ui.main_view`. If the host imports the
shared scene runtime, add it as a shared source so the UI build receives the
runtime under a stable local import path.

```yaml
ui:
  main_view:
    src_folder: ui/scene
    shared_sources:
      components_core_scene:
        src_folder: sdk://solutions/scene
        target: _shared/components-core/scene
      components_react_canvas:
        src_folder: sdk://solutions/canvas/ui/component
        target: _shared/components-react/canvas
  widgets:
    issue_viewer:
      src_folder: ui/widgets/issue_viewer
    project_metrics:
      src_folder: ui/widgets/project_metrics
```

Inside the scene build, alias the runtime import to that shared source:

```ts
import { createSceneRuntime } from "@kdcube/components-core/scene"
import { CanvasBoard } from "@kdcube/components-react/canvas"
```

The concrete alias is host-build tooling. Conceptually,
`@kdcube/components-core/*` is for headless/runtime logic and
`@kdcube/components-react/*` is for React wrappers or React-hosted components.

## Declare App Surfaces

Every surface the scene can command needs two pieces:

1. A configured UI source or component mount.
2. A runtime surface registration.

For example, an app may expose an issue viewer widget:

```python
@ui_widget(alias="issue_viewer", **_widget_visibility("issue_viewer"))
def issue_viewer_widget(self, **kwargs):
    del kwargs
    return ["<div>Issue viewer is served from ui/widgets/issue_viewer after build.</div>"]
```

And then register the surface in the scene host:

```ts
const scene = createSceneRuntime()

scene.registerSurface("app.issue.viewer", {
  label: "issue viewer",
  ensureOpen: () => setIssuePanelOpen(true),
  isReady: () => issuePanelReady,
  postCommand: (command) =>
    postToIssueViewer({
      type: "app-surface-command",
      surface: "app.issue.viewer",
      ...command,
    }),
  commandFromOpen: ({ uiEvent }) => ({
    action: "open",
    object_ref: uiEvent.object_ref,
    view: uiEvent.mode || "expanded",
  }),
})
```

This is a concrete example, not a required component. If your scene does not
mount an issue viewer, do not register `app.issue.viewer`.

## Runtime Config Handshake

Embedded iframe surfaces usually boot without knowing the base URL, tenant,
project, app id, or auth material. On mount they ask the host for config; the
host replies.

```text
surface iframe                       scene host
   |  CONFIG_REQUEST  --------------> |
   |   { identity, requestedFields }  |
   |                                  | resolves runtime config
   |  <-------------- CONFIG_RESPONSE |
   |   { identity, config }           |
   v                                  |
 builds API URLs + auth headers
```

The host replies to the requesting surface by matching the request identity and
the frame/source that sent it. This is the standard widget contract from
[Bundle Widget Integration](../../bundle/bundle-widget-integration-README.md).

## Local Surface Messages

This section is an implementation boundary for any scene runtime, including the
SDK implementation KDCube ships. The generic runtime is only a typed
switchboard: it handles `target_surface`, registered surface adapters, queueing,
readiness, and dispatch results. Component-specific message names and payload
fields live in the host adapter and the target surface.

In practice:

```text
generic runtime:
  target_surface + SceneSurfaceRegistration + command queue

host adapter:
  maps generic command -> concrete postMessage/callback payload

target surface:
  owns the message name, fields, loading behavior, and visual result
```

So a scene may broker any local message types the included surfaces require,
but those messages stay scoped to the concrete surface contract. Adding a new
surface should add or configure a new surface adapter, not add component
knowledge to `sdk://solutions/scene`.

Typical categories:

| Message kind | Direction | Purpose |
| --- | --- | --- |
| `CONFIG_REQUEST` / `CONFIG_RESPONSE` | surface <-> host | Runtime config handshake. |
| view command | host -> surface | Switch compact/expanded/focused view. |
| object-open command | host -> surface | Tell a registered surface to open an object ref. |
| context/selection event | surface -> host or host -> surface | Move selected context between surfaces. |
| ingress event | surface -> host -> surface | Transfer dropped or selected content. |

The exact message names are app/surface-owned. The scene runtime only requires
the surface adapter functions registered with `createSceneRuntime()`.

## Cross-Surface Context Drag

The concrete contract, status table, and migration checklist live in
[Cross-Surface Context Drag](cross-surface-context-drag-README.md). This section
summarizes the host composition role.

Cross-surface drag/drop is scene work, not widget-to-widget special casing. A
source surface that lets the user drag an object ref must publish a canonical
context-drag message to the host scene when the drag starts, and a drag-end
message when it ends:

```json
{
  "type": "kdcube-context-drag-start",
  "source_surface_ref": "app.pinboard",
  "contexts": [
    {
      "ref": "acme:ticket:ticket_123",
      "label": "Upload fails after screenshot",
      "kind": "object.ref"
    }
  ]
}
```

The canonical payload is the context-pin envelope: `contexts` is always an
array, and each context's `ref` is the canonical object URI. The scene host may
store this current drag context only for the lifetime of the browser drag. It
uses that context to decide which mounted surface accepts the drop and which
object action to request.

Generic flow:

```text
source surface dragstart
  -> host scene records active contexts
  -> user drops over a compatible surface region
  -> host calls object.action(open, ref, requested target surface)
  -> namespace provider returns ui_event.target_surface
  -> scene surface registry dispatches the command
  -> target surface opens/focuses the object
```

Widget-local native drop handling is optional. It can be a convenience for a
single widget, but it is not the scene contract. The reliable generic path is
the scene broker path above. A widget that only parses browser `DataTransfer`
payloads is not enough for all cross-iframe scenes, because browsers do not
deliver native drag data consistently across nested frames.

For this path to work, every participant has a narrow responsibility:

| Participant | Required behavior |
| --- | --- |
| Source surface | Emit `kdcube-context-drag-start` / `kdcube-context-drag-end` with canonical `contexts`. |
| Scene host | Track the active context, match drop zones by root namespace, call the backend object action, and dispatch by `target_surface`. |
| Namespace provider | Resolve `open` for the full object URI and return the target surface UI event. |
| Target surface | Register with the scene and implement only its own local open/focus command. |

Namespace styling is the same map for every surface. It is keyed by the root
namespace (`mem`, `task`, `fi`, `cnv`, and so on) and should be passed by the
host scene to every surface that renders context chips/cards. It must not be
implemented separately by canvas, chat, or memory.

## Data Bus Subscriptions

Durable, ordered, cross-component state can flow over the Socket.IO Data Bus.
Use it for backend/runtime state, not for local focus commands.

```text
local focus/open:
  scene host -> mounted surface

durable/runtime state:
  surface -> app operation
  surface -> Data Bus subject
  processor/runtime -> Data Bus subject
```

The app decides which subjects matter. Examples:

```yaml
scene_subscriptions:
  - subject: app.issue.updated
    partition: object_ref
  - subject: accounting.usage
```

If a scene has no surface that needs these subjects, it should not subscribe.

## Object Opens

When a source surface asks to open an object, the source or backend calls the
namespace resolver. The resolver response contains `ui_event.target_surface`.
The scene then dispatches that response through the surface registry.

Example provider response for an app-owned namespace:

```json
{
  "ok": true,
  "object_ref": "task:issue:ticket_123",
  "ui_event": {
    "type": "kdcube.ui.object.open.requested",
    "target_surface": "app.issue.viewer",
    "object_ref": "task:issue:ticket_123",
    "mode": "expanded"
  }
}
```

The scene still does not know what a task issue is. It knows that
`app.issue.viewer` is registered, opens that surface, and passes the normalized
command to it. The target surface decides whether that means a read-only detail
view, an editor, a wizard, or something else.

The same composition rule applies when the source object started as a search
result or context chip. The object keeps one canonical `object_ref`; the scene
only preserves that identity, applies optional root-namespace presentation
styles, and routes the provider-declared UI event to the registered surface.

## Reference Implementation

The versatile app has a working host scene that imports the shared runtime and
registers concrete surfaces chosen by that app:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36
  ui/scene/src/main.tsx
  ui/scene/vite.config.js
  config/bundles.template.yaml
  entrypoint.py
```

Use it as an implementation reference for the mechanics: shared source wiring,
CONFIG relay, iframe refs, readiness, `createSceneRuntime()`, and
`registerSurface(...)`. Do not copy its surface list as the definition of what a
scene is.

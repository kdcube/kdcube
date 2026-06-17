---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
title: "Scene Surface Registry"
summary: "How a scene host routes resolver-owned object actions to registered UI surfaces without hardcoding namespace or widget semantics."
status: draft
tags: ["sdk", "solutions", "scene", "surfaces", "resolvers", "named-services"]
updated_at: 2026-06-17
keywords:
  [
    "scene surface registry",
    "target_surface",
    "object open",
    "surface command",
    "object_ref",
    "resolver response",
    "default_open_effect_action",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
---
# Scene Surface Registry

Read these words first:

| Term | Meaning |
| --- | --- |
| App | The product/bundle that owns the user experience. |
| Scene | The app-owned UX composition layer: one page or widget shell that connects several UI parts. |
| Host | The browser page/component that runs the scene. |
| Surface | One UI part inside the scene: iframe widget, in-page component, external panel, or another view. |
| Source surface | The surface where the user starts an action. |
| Target surface | The surface that should react. |
| Scene runtime | The host's routing helper: surface registry, dispatch, queueing, readiness. |
| Object ref | A stable handle owned by a namespace, for example `task:issue:ticket_123`. |
| Namespace | The owner prefix of an object ref, for example `task`, `memo`, `fi`, or an app-defined namespace. |
| Namespace provider / resolver | Backend owner of object meaning. It decides what an object ref means and which UI effect should happen. |
| `target_surface` | The provider's instruction to the scene: which registered UI surface should receive the open command. |
| Surface adapter | Host-side code that maps the generic scene command into the concrete message/callback the target surface understands. |

A scene is a UX composition layer that connects multiple subsystem surfaces into
one workspace. A surface can be an iframe widget, an in-page component, a
floating panel, or any other view the host can open and command.

The scene runtime contract is generic. KDCube ships an SDK implementation, but a
product can implement the same contract in its own scene. The generic runtime
must not know how to read a namespace object, open an app-specific entity,
download a file, or interpret a ref. It knows only this:

```text
resolver response -> target_surface -> registered host surface -> command
```

The concrete app decides which surfaces exist. A scene with no task UI should
not contain task behavior. A scene with no memory UI should not contain memory
behavior. The runtime stays the same.

High-level communication:

```text
User
  example: person clicks an object chip/card
  |
  v
Source surface
  example: card list, context chip, board card
  |
  | asks backend what "open this object_ref" means
  | example: object_ref = "task:issue:ticket_123"
  v
Namespace provider / resolver
  example: provider for namespace "task"
  |
  | returns ui_event.target_surface
  | example: target_surface = "app.issue.viewer"
  v
Scene host + scene runtime
  example: host page with createSceneRuntime()
  |
  | routes to registered surface adapter
  | example: adapter registered for "app.issue.viewer"
  v
Target surface
  example: issue viewer iframe or in-page details component
  |
  | loads/renders object using its own API contract
  | example: reads ticket_123 and displays details/editor
  v
User sees the object opened in the scene
```

## Ownership

| Concern | Owner |
| --- | --- |
| Object identity | The originating namespace, for example `fi:`, `cnv:`, or an app-owned namespace such as `task:`. |
| Object semantics | The namespace owner's named-service provider or resolver. |
| Open-effect declaration | The namespace owner, usually through `default_open_effect_action`. |
| Surface mounting and z-order | Scene host. |
| Local open/focus command | Scene surface registry plus host adapter. |
| Actual UI behavior | The target surface. |

The same object ref must keep the same identity as it moves between surfaces:

```text
task:issue:ticket_123
  -> source surface object_ref
  -> context chip object_ref
  -> board/card object_ref
  -> resolver action object_ref
```

The host may attach presentation metadata, but it must not rewrite the object
into a host-specific identity. Ownership stays with the namespace.

Visual identity follows the same rule. Namespace colors and badges are app-level
presentation metadata keyed by the root namespace (`mem`, `task`, `fi`, `cnv`,
and so on). A scene may pass that already-owned `namespaceStyles` map into
surfaces it mounts, but the metadata is not scene-owned and not canvas-owned.
The style affects presentation only; resolver dispatch still uses the canonical
object ref and the namespace provider still owns the object's behavior.

## Open Flow

When a user opens an object, the source surface asks the namespace owner for an
open effect. The provider returns a UI event naming the target scene surface.
The scene routes by `target_surface` and forwards a command.

Concrete example using an app-owned namespace:

```text
source surface
  object_ref = task:issue:ticket_123
        |
        v
object.action({ action: "open", object_ref: "task:issue:ticket_123" })
        |
        v
task namespace provider / resolver
        |
        v
{
  ok: true,
  object_ref: "task:issue:ticket_123",
  ui_event: {
    type: "kdcube.ui.object.open.requested",
    subject: "ui.object.open.requested",
    target_surface: "app.issue.viewer",
    object_ref: "task:issue:ticket_123",
    mode: "expanded"
  }
}
        |
        v
scene surface registry
        |
        v
registered surface receives:
{
  action: "open",
  object_ref: "task:issue:ticket_123",
  view: "expanded"
}
```

This example is intentionally about the contract, not about task behavior. The
scene does not know what an issue is. The provider knows which surface should
open it. The registered surface knows how to load and present it.

## Full Signaling Journey

The scene is a frontend orchestrator, but the object meaning lives behind the
namespace provider. A complete object-open journey has frontend and backend
parts:

```text
FRONTEND                                             BACKEND

User
 |
 | 1. clicks "open" on an object ref
 v
Source surface
 iframe/component
 |
 | 2. calls an app operation with the object action request
 |    { action: "open", object_ref: "task:issue:ticket_123" }
 v
App operation facade ------------------------------> Namespace provider
 (same app backend)                                  / resolver
 |                                                    |
 | 3. delegates by namespace                          | 4. resolves open effect
 |    object.action(...)                              |    owns task: semantics
 |                                                    v
 | <--------------------------------------------------+
 | 5. provider response
 |    {
 |      object_ref,
 |      ui_event: {
 |        target_surface: "app.issue.viewer",
 |        object_ref,
 |        mode: "expanded"
 |      }
 |    }
 v
Source surface
 |
 | 6. notifies parent scene
 |    postMessage({
 |      type: "kdcube-object-open",
 |      response,
 |      source
 |    })
 v
Scene host frontend
 |
 | 7. sceneRuntime.routeMessage(event)
 |      -> normalize response
 |      -> read ui_event.target_surface
 |      -> find registered surface adapter
 |      -> ensureOpen(request)
 |      -> commandFromOpen(request)
 |      -> queue if !isReady(request)
 |      -> postCommand(command, request)
 v
Target surface
 iframe/component
 |
 | 8. receives surface-owned command
 |    {
 |      type: "app-surface-command",
 |      action: "open",
 |      object_ref: "task:issue:ticket_123",
 |      view: "expanded"
 |    }
 |
 | 9. loads what it needs, using its own API contract
 v
Target surface operation --------------------------> Namespace provider
 |                                                    / app backend
 | <--------------------------------------------------+
 |
 v
Target surface renders the object
```

Some hosts can choose to perform steps 2-5 in the scene host instead of in the
source surface. In that case the host calls the app operation, receives the same
provider response, and then calls `scene.dispatchSurfaceOpen(response, source)`.
The registry path from step 7 onward is identical.

The important boundary is that the generic runtime only knows step 7. It does
not know what `task:` means, what endpoint the target surface calls, or what the
target surface renders.

The same boundary applies to search results and context pins. A result found in
chat, dragged to canvas, and then opened from the board should keep the same
`object_ref`; the source surface, scene host, and target surface may add local
presentation metadata, but none of them should replace the ref with a
surface-local identity.

## Drag Drop To A Target Surface

The implementation-oriented design is
[Cross-Surface Context Drag](cross-surface-context-drag-README.md). This section
shows how drag/drop maps onto the surface registry's object-open route.

Dropping an existing object onto one of its capable surfaces is just another
object-open route. It should not depend on the target widget knowing every
possible source widget's browser drag payload.

```text
FRONTEND                                             BACKEND

Source surface
 example: board card, search result, context chip
 |
 | dragstart:
 | postMessage({
 |   type: "kdcube-context-drag-start",
 |   contexts: [{ ref: "task:issue:ticket_123", ... }]
 | })
 v
Scene host
 |
 | active drag context = task:issue:ticket_123
 | drop target accepts root namespace "task"
 |
 | object.action(
 |   action="open",
 |   object_ref="task:issue:ticket_123",
 |   requested_target_surface="app.issue.viewer"
 | )
 v
App operation facade ------------------------------> Namespace provider
                                                     / resolver
                                                     |
                                                     | validates the full URI
                                                     | chooses/accepts target surface
                                                     v
Scene host <-----------------------------------------+
 |
 | resolver response with ui_event.target_surface
 | route through createSceneRuntime registry
 v
Target surface
 example: issue viewer receives { action: "open", object_ref: ... }
```

The `requested_target_surface` is a scene hint: "the user dropped the object on
this registered surface." The namespace provider still owns the decision. It
may return that target, another target, or an unavailable/error result. The
scene must route only the resolver-approved response.

Widget-local native drop handling can exist as a narrow optimization, but it is
not the generic contract. The generic contract is:

1. source surfaces emit canonical context drag messages;
2. the scene host tracks active drag context and compatible drop zones;
3. providers resolve `open` for the full object ref;
4. target surfaces implement their registered local open command.

## Registry Shape

The shared SDK runtime exposes the registry from:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/scene/src
```

A scene host registers each reachable surface with `createSceneRuntime()`:

```ts
import { createSceneRuntime } from "@kdcube/components-core/scene"

type SceneSurfaceRegistration = {
  label?: string
  ensureOpen?: (request: SceneSurfaceOpenRequest) => void
  isReady?: (request: SceneSurfaceOpenRequest) => boolean
  postCommand: (
    command: Record<string, unknown>,
    request: SceneSurfaceOpenRequest,
  ) => boolean
  commandFromOpen: (request: {
    targetSurface: string
    uiEvent: Record<string, unknown>
    response: Record<string, unknown>
    source?: Record<string, unknown>
  }) => Record<string, unknown> | null
}
```

Concrete host registration:

```ts
const scene = createSceneRuntime()

scene.registerSurface("app.issue.viewer", {
  label: "issue viewer",
  ensureOpen: () => setIssuePanelOpen(true),
  isReady: () => issuePanelReady,
  postCommand: (command) =>
    postToIssueSurface({
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

The dispatch helper is generic:

```text
resolver response
  -> read ui_event.target_surface
  -> find scene registry entry
  -> ensure surface is mounted/open
  -> queue command until surface is ready if needed
  -> post command to surface
```

Host-originated commands use the same queue and readiness path:

```ts
scene.queueSurfaceCommand("app.issue.viewer", {
  action: "create",
})
```

Use this when the scene itself has a UI affordance and still wants the target
surface to receive the command through the same `ensureOpen -> queue -> flush`
mechanics as resolver opens.

If no surface is registered, the host should keep the object intact and show a
clear UI notice:

```text
No widget surface is registered for app.issue.viewer.
```

The dispatch result is always bounded:

```ts
type SceneDispatchResult =
  | { ok: true; code: "dispatched" | "queued"; targetSurface: string; message: string }
  | {
      ok: false
      code:
        | "message_invalid"
        | "origin_not_allowed"
        | "message_source_not_registered"
        | "target_surface_missing"
        | "target_surface_unavailable"
        | "surface_command_unavailable"
        | "surface_command_rejected"
      message: string
      targetSurface?: string
    }
```

## Readiness

Mounted surfaces are asynchronous. A resolver action can happen before the
target iframe or component is ready. The scene host queues one pending command
per target surface and flushes it when `isReady` returns true.

For an iframe surface, readiness can be driven by any widget-owned status
message:

```json
{
  "type": "app-surface-status",
  "surface": "app.issue.viewer",
  "ready": true
}
```

The exact status payload is surface-owned. The scene only needs enough local
state to know whether `postCommand` can receive the pending command.

## Transport

Use `postMessage` or a direct component callback for same-page surface commands:

```text
scene host -> mounted surface
```

Use Data Bus or app operations for backend/runtime work:

```text
surface -> app operation
surface -> Data Bus subject
resolver -> backend object action
processor/runtime -> data/event publication
```

For example, a durable state patch can use Data Bus because it mutates shared
runtime state. Opening an already mounted detail surface is local UI routing, so
the scene should use the surface registry and the local surface transport.

If a resolver target is not mounted in the current page, the scene may mount it,
queue the command, and flush after readiness. If the scene cannot mount the
target, it should report `target_surface_unavailable`.

## Host Integration

Any host page follows the same model:

```text
source interaction
        |
        v
object_ref + action
        |
        v
resolver response with target_surface
        |
        v
scene surface registry
        |
        v
surface command
```

The provider/resolver owns the object effect declaration
(`default_open_effect_action`). The scene owns the reaction to a resolved
`open`: which panel opens, which surface receives the command, and how an
unavailable target is reported.

When a source surface is itself an iframe, the resolver `open` may reach the
host one hop further out: the iframe runs the provider-backed object action,
then forwards the resolver's `target_surface` to its parent by `postMessage`.
The host feeds that `target_surface` into the same registry. The routing
contract is unchanged; only the source-to-host transport differs.

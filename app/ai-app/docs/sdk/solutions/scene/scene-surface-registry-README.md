---
id: ks:docs/sdk/solutions/scene/scene-surface-registry-README.md
title: "Scene Surface Registry"
summary: "How a scene host composes iframe widgets, resolver-owned object actions, and local widget commands without hardcoding object semantics in canvas or chat."
status: draft
tags: ["sdk", "solutions", "scene", "widgets", "iframe", "resolvers", "canvas", "chat", "memory"]
updated_at: 2026-06-09
keywords:
  [
    "scene surface registry",
    "target_surface",
    "iframe widget composition",
    "canvas object open",
    "widget postMessage",
    "object_ref",
    "resolver response",
  ]
see_also:
  - ks:docs/sdk/solutions/scene/scene-composition-README.md
  - ks:docs/sdk/solutions/canvas/pin-integration-README.md
  - ks:docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - ks:docs/sdk/solutions/chat/chat-widget-solution-README.md
  - ks:docs/sdk/solutions/memory/memory-widget-solution-README.md
  - ks:docs/sdk/bundle/bundle-subsystem-integration-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---
# Scene Surface Registry

A scene is a host page that composes several SDK widgets into one workspace.
Examples are the versatile scene, the task-tracker app shell, and the future
landing-page scene in `website/index.html`.

The scene should not know how to read a memory, open a task, download a ReAct
artifact, or interpret a canvas object. It should know only which iframe widget
surface is mounted and how to deliver a command to that widget.

## Ownership

| Concern | Owner |
| --- | --- |
| Object identity | The originating namespace, for example `mem:`, `task:`, `fi:`, `cnv:`. |
| Object semantics | The namespace owner's resolver. |
| Canvas board layout | Canvas. |
| Chat event packaging | Chat widget. |
| Widget mounting and z-order | Scene host. |
| Local iframe focus/open command | Scene surface registry. |
| Actual widget behavior | The target widget. |

The same object ref must keep the same identity as it moves between widgets:

```text
mem:mem_123
  -> memory widget row
  -> canvas card object_ref
  -> chat context chip object_ref
  -> resolver action object_ref
```

Canvas and chat are not allowed to rewrite `mem:` into a canvas-specific or
chat-specific identity. They may attach presentation metadata, but ownership
stays with memory.

## Open Flow

When the user presses **Open** on a canvas card, the flow is:

```text
canvas card
  object_ref = mem:mem_123
        |
        v
canvas_object_action({ action: "open", object_ref: "mem:mem_123" })
        |
        v
memory resolver
        |
        v
{
  ok: true,
  object_ref: "mem:mem_123",
  ui_event: {
    type: "kdcube.ui.object.open.requested",
    subject: "ui.object.open.requested",
    target_surface: "sdk.memory.viewer",
    object_ref: "mem:mem_123",
    memory_id: "mem_123",
    mode: "focus"
  }
}
        |
        v
scene surface registry
        |
        v
mounted memory iframe receives:
{
  type: "kdcube-memory-widget-command",
  widget: "memories",
  action: "open",
  object_ref: "mem:mem_123",
  memory_id: "mem_123"
}
```

The scene routes by `target_surface`. The scene does not inspect memory storage
or call memory APIs directly. The memory widget receives the command and uses
its own API operations to load and focus the selected memory.

Opening an object must focus that object. For example, opening `mem:mem_123`
should not merely reveal the memories widget; it should put the widget into a
focused view for `mem_123`. The widget may show a local escape hatch such as
`Back to list`, but the requested object is the active target until the user
clears that focus.

## Registry Shape

A scene host should keep a registry like this:

```ts
type SceneSurfaceRegistration = {
  label: string
  ensureOpen: () => void
  postCommand: (command: Record<string, unknown>) => boolean
  commandFromOpen: (request: {
    targetSurface: string
    uiEvent: Record<string, unknown>
    response: Record<string, unknown>
    sourceCard?: Record<string, unknown>
  }) => Record<string, unknown> | null
}
```

Example registration:

```ts
const surfaces = {
  "sdk.memory.viewer": {
    label: "memory viewer",
    ensureOpen: () => setMemoryOpen(true),
    postCommand: (command) =>
      postToMemoryIframe({
        type: "kdcube-memory-widget-command",
        widget: "memories",
        ...command,
      }),
    commandFromOpen: ({ uiEvent }) => ({
      action: "open",
      object_ref: uiEvent.object_ref,
      memory_id: uiEvent.memory_id,
    }),
  },
}
```

The dispatch helper is generic:

```text
resolver response
  -> read ui_event.target_surface
  -> find scene registry entry
  -> ensure widget is mounted/open
  -> queue command until iframe is ready if needed
  -> post command to iframe
```

If no surface is registered, the host should keep the object intact and show a
clear UI notice:

```text
No widget surface is registered for sdk.memory.viewer.
```

## Iframe Readiness

Iframes are asynchronous. A resolver action can happen before the target widget
has loaded. The scene host must queue one pending command per target surface and
flush it when the widget reports readiness.

Minimum status message:

```json
{
  "type": "kdcube-memory-widget-status",
  "widget": "memories",
  "count": 5
}
```

The exact status payload is widget-owned. The scene only needs enough to know
that the iframe can receive a command.

## Data Bus Role

Data Bus is not the local iframe focus mechanism.

Use `postMessage` for same-page iframe commands:

```text
scene host -> mounted iframe widget
```

Use Data Bus or bundle operations for backend/runtime work:

```text
widget -> bundle operation
widget -> Data Bus subject
resolver -> backend object action
processor/runtime -> data/event publication
```

For example, a canvas patch can use Data Bus because it mutates durable bundle
state. Opening an already mounted memory widget is local UI routing, so the
scene should use the surface registry and `postMessage`.

If a future resolver target is not mounted in the current page, the scene may
mount it, queue the command, and flush after readiness. If the scene cannot mount
the target, it should report `target_surface_unavailable`.

## Website Landing Page

The future `website/index.html` scene should follow the same model:

```text
landing chip / canvas card / chat context
        |
        v
object_ref + action
        |
        v
resolver response with target_surface
        |
        v
website surface registry
        |
        v
iframe widget command
```

The landing page should not learn memory internals. It should mount iframe
widgets, register their surfaces, and dispatch resolver responses by
`target_surface`.

When the board itself is a standalone iframe (the `pinboard` widget) rather
than an in-React component, the resolver `open` reaches the host one hop
further out: the widget runs the `canvas_object_action` itself, then forwards
the resolver's `target_surface` to its parent as a `kdcube-pinboard-open`
postMessage. The host feeds that `target_surface` into this same registry — the
routing contract is unchanged, only the board-to-host transport is a
postMessage. See the host-broker contract in
[Scene Composition](scene-composition-README.md#the-canvas-board-as-a-standalone-widget).

## Current Implementation

The versatile scene implements the first local version of this registry:

```text
sdk.memory.viewer -> memory iframe
```

This replaces the older canvas action branch that checked for memory directly.
Additional surfaces should be added by registering another `target_surface`
entry rather than changing canvas or chat behavior.

The memory widget also implements focused object mode. A command like:

```json
{
  "type": "kdcube-memory-widget-command",
  "widget": "memories",
  "action": "open",
  "object_ref": "mem:mem_123",
  "memory_id": "mem_123"
}
```

loads the selected memory, filters the visible list to that memory, selects it,
and exposes `Back to list` to return to normal memory browsing.

The same focused-object behavior supports multiple ids:

```json
{
  "type": "kdcube-memory-widget-command",
  "widget": "memories",
  "action": "open",
  "object_refs": ["mem:mem_123", "mem:mem_456"]
}
```

Dropping one or more `mem:` canvas pins onto the memories widget follows the
same path. The widget extracts canonical `mem:` refs from generic context
payloads, focuses those ids, loads each memory, and shows only that focused set
until the user selects `Back to list`.

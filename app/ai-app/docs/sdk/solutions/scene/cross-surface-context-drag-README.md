---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
title: "Cross-Surface Context Drag"
summary: "Concrete design for generic object drag/drop between scene surfaces: source drag lifecycle, scene broker state, namespace-provider open resolution, target-surface command dispatch, and current migration gaps."
status: implementation
tags: ["sdk", "solutions", "scene", "drag-drop", "context-pin", "surfaces", "named-services", "canvas", "chat"]
updated_at: 2026-06-17
keywords:
  [
    "cross surface drag",
    "context drag broker",
    "kdcube-context-drag-start",
    "kdcube-context-drag-end",
    "context pin contract",
    "root namespace drop target",
    "requested target surface",
    "object action open",
    "target_surface",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/context-pin-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
---
# Cross-Surface Context Drag

This document defines the concrete contract for dragging an existing object
between surfaces in a scene.

This document covers transient drag/drop state. Long-lived Event Bus routing
from the runtime into widgets is covered in
[Scene Event Orchestration](scene-event-orchestration-README.md).

The design target:

```text
source surface emits canonical context drag
  -> scene host tracks active context
  -> scene matches drop targets by root namespace
  -> scene asks provider to resolve object.action(open, object_ref)
  -> provider returns ui_event.target_surface
  -> scene registry sends a local open command to the target surface
```

The goal is that chat, canvas, memory, task, provider widgets, search results,
and future surfaces interoperate without knowing each other's implementation.

## Terms

| Term | Meaning |
| --- | --- |
| Source surface | The UI surface where the drag starts: chat chip, search result, canvas card, memory row, task row, etc. |
| Target surface | The UI surface under the drop: memory viewer, issue list/editor, chat composer, canvas board, provider editor, etc. |
| Context item | One draggable object reference, using the context-pin contract. Its canonical URI is `ref`. |
| Root namespace | The first URI segment before `:`, for example `mem`, `task`, `fi`, `cnv`. Scoped refs such as `task:issue:...` still have root namespace `task`. |
| Active drag context | Temporary scene host state that exists only during one browser drag. |
| Requested target surface | A scene hint that the user dropped on a specific registered surface. The provider still decides whether this target is valid. |
| Drop overlay | Host-rendered transparent hit area over a compatible mounted surface, created only while a drag is active. |
| Rail pulse | Host-rendered hint on a summon button when a compatible target surface is available but not currently mounted/open. |

## Drag Overlay Control Plane

Cross-surface drag/drop is not a persistent Event Bus subscription. It is a
browser UI control plane owned by the scene host:

```text
source iframe / host chip
  postMessage: kdcube-context-drag-start
      |
      v
scene host context-drag broker
  normalize contexts
  derive root namespace from context.ref
  select compatible SceneDropTarget entries
      |
      +--> mounted target: draw transparent drop overlay over iframe rect
      |
      +--> unmounted target: pulse summon rail button
      |
      v
browser drop / child drag-end coordinates
      |
      v
scene host deliverDrop
  attach -> post context to chat
  pin    -> post context/ingress to canvas
  open   -> call provider object.action(open), then route ui_event.target_surface
```

The overlay is host UI, not a widget. It exists because browser native drag/drop
does not reliably cross nested iframe boundaries, especially cross-origin. The
host receives source drag messages, owns the active drag context, and builds
drop hit areas in the parent page coordinate space.

### Control-Plane Messages

| Message / action | Direction | Lifetime | Name format | Purpose |
| --- | --- | --- | --- | --- |
| `kdcube-context-drag-start` | source surface -> scene host | One browser drag | Dash-case transport name | Starts an active scene drag with canonical `contexts`. |
| `kdcube-context-drag-end` | source surface -> scene host | One browser drag | Dash-case transport name | Supplies final coordinates and lets the host resolve a drop when native `drop` does not reach the parent overlay. |
| `kdcube-canvas-ingress-drag-start/end` | source surface -> scene host | One browser drag | Dash-case transport name | Carries non-object ingress payloads, such as a user attachment/file payload, toward canvas pinning. |
| `object.action(open)` | scene host -> namespace provider | Drop handling | Namespace-service operation | Provider validates the full URI and returns the actual `ui_event.target_surface`. |
| Target command, for example `kdcube-memory-widget-command` | scene host -> target iframe | Drop handling | Dash-case transport name | Local browser command delivered after provider/open resolution. |

Dash-case names are browser transport names. Canonical object identity remains
inside the payload as `context.ref` / `object_ref`, for example
`task:issue:...`, `mem:record:...`, or future event identities such as
`task:event:task-changed` where they are payload values rather than DOM-style
event names.

### Overlay Target Map

| Target type | Host representation | Match key | Drop effect | Delivery |
| --- | --- | --- | --- | --- |
| Mounted chat | Transparent overlay over chat iframe | `*` | `attach` | `postMessage({ type: "kdcube.context.attach", context })`; conversation refs may load the conversation. |
| Mounted canvas/pinboard | Transparent overlay over pinboard iframe | `*` or ingress-only path | `pin` | `postMessage({ type: "kdcube-pinboard-drop-context", context, x, y })` or ingress payload equivalent. |
| Mounted memory surface | Transparent overlay over memory iframe | `mem` | `open` | Provider-backed open to `sdk.memory.viewer`, then local memory command. |
| Mounted task surface | Transparent overlay over task iframe | `task` | `open` | Provider-backed open to task target surface, then local task command. |
| Unmounted compatible surface | Rail pulse on summon button | Same root namespace match | Target-specific | Open/summon first, then deliver after readiness/short delay. |

Namespace color for the overlay comes from shared namespace presentation config,
keyed by root namespace. The overlay must not hardcode memory/task/file colors
by widget.

## Non-Goals

- The scene runtime must not know what a memory, task, ticket, file, or provider
  object is.
- A target widget must not parse every other source widget's native browser drag
  payload as the primary interop mechanism.
- Canvas must not be the owner of namespace styles or object semantics.
- Chat must not rewrite provider refs into chat-owned refs unless the object is
  actually chat-owned.

## Actor Responsibilities

| Actor | Required responsibilities |
| --- | --- |
| Source surface | Emits drag start/end messages with canonical `contexts`. May also set browser `DataTransfer` for local/native fallback. |
| Scene host | Keeps active drag context, shows/arms compatible drop zones, calls the object-action backend, dispatches resolver response through the surface registry. |
| Namespace provider | Owns URI semantics. Validates `object_ref`, resolves `open`, and returns `ui_event.target_surface` or an explicit error. |
| Scene surface registry | Routes only by resolver-approved `target_surface`; queues until the target is ready. |
| Target surface | Implements its own local open/focus command and loads the object through its own API. |

## Source Surface Contract

Every source surface that lets users drag existing object refs must emit these
messages to the scene host.

Start:

```json
{
  "type": "kdcube-context-drag-start",
  "source_surface_ref": "app.pinboard",
  "contexts": [
    {
      "ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
      "label": "Cancel membership — Judo and more",
      "kind": "object.ref",
      "namespace": "task",
      "summary": "Need to send the cancellation letter..."
    }
  ]
}
```

End:

```json
{
  "type": "kdcube-context-drag-end",
  "source_surface_ref": "app.pinboard"
}
```

Rules:

- `contexts` is always an array.
- `contexts[*].ref` is the canonical object URI. It is the only identity the
  scene and provider need.
- `namespace` is optional display metadata. If present, it must agree with the
  root namespace of `ref`. If absent, the scene derives root namespace from
  `ref`.
- `label`, `summary`, `kind`, and `mime` are presentation hints only.
- The source should preserve the same `ref` when the object moves through
  search results, chat context pins, canvas cards, and target surfaces.

## Scene Host Contract

The scene host owns the browser-level drop orchestration:

```ts
type ContextItem = {
  ref: string
  label?: string
  kind?: string
  namespace?: string
  summary?: string
  mime?: string
  [key: string]: unknown
}

type ActiveContextDrag = {
  sourceSurfaceRef?: string
  contexts: ContextItem[]
}

type SceneDropTarget = {
  surfaceRef: string
  targetSurface: string
  acceptsRootNamespaces: string[]
  dropEffect?: "open" | "attach" | "pin"
}
```

Required scene behavior:

1. On `kdcube-context-drag-start`, normalize all context items and store one
   active drag context.
2. Derive a candidate root namespace set from each item:
   - `item.namespace`, reduced to root namespace;
   - `item.ref`, reduced to root namespace;
   - optional compatibility fields only as aliases to `ref`, not as new
     identities.
3. Mark a drop target active when any root namespace intersects
   `acceptsRootNamespaces`.
4. On drop, choose the first active context item unless the target explicitly
   supports multi-object drops.
5. For a normal owning-surface drop, call the provider-backed object action:

```json
{
  "action": "open",
  "object_ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
  "target_surface": "task_tracker.issue_list"
}
```

6. Dispatch only the provider response. Do not invent the final target in the
   frontend if the provider rejects or changes it.
7. Clear active drag state on drop, drag-end, escape/cancel, or timeout.

The scene may keep temporary active drag state because browser drag state itself
is temporary UI state. It must not persist that state.

## Provider Contract

The namespace provider owns `open` semantics for the full URI. It receives the
full object ref and an optional requested target surface.

Example request:

```json
{
  "namespace": "task",
  "operation": "object.action",
  "object_ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
  "action": "open",
  "payload": {
    "target_surface": "task_tracker.issue_list"
  }
}
```

Example response:

```json
{
  "ok": true,
  "object_ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
  "ui_event": {
    "type": "kdcube.ui.object.open.requested",
    "subject": "ui.object.open.requested",
    "target_surface": "task_tracker.issue_list",
    "object_ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
    "mode": "focused"
  }
}
```

Provider rules:

- Validate against the full URI, not just the root namespace.
- Treat `target_surface` from the scene as a request, not a command.
- Return a `ui_event.target_surface` only when that effect is valid.
- Return an explicit error when the object cannot be opened or the requested
  target is not allowed.
- For scoped refs, keep the original ref intact. If opening an attachment should
  focus its parent issue, return the parent/focus details in `ui_event` without
  replacing the canonical attachment ref unless the provider explicitly says so.

## Target Surface Contract

A target surface does not need to know the drag source. It only implements the
local command the scene adapter sends after provider resolution.

Example target command:

```json
{
  "type": "task-tracker-command",
  "action": "open",
  "target_surface": "task_tracker.issue_list",
  "object_ref": "task:issue:ticket_2026-06-12-00-53-30-951095965",
  "mode": "focused"
}
```

Target rules:

- Load/focus the object using the surface's own API.
- Preserve unsaved work according to that surface's own policy.
- Report readiness to the scene if it is an iframe or lazy-mounted surface.
- Do not implement source-specific parsing as the primary interop path.

Widget-local native drops are allowed only as convenience fallback. For example,
a memory widget can accept a `mem:record:...` browser drop and focus it directly.
That path must not be the only way cross-surface drops work.

## Namespace Styles

Namespace styles are app/scene configuration, keyed by root namespace:

```json
{
  "mem": { "label": "Memory", "accent": "#16a34a" },
  "task": { "label": "Task", "accent": "#2563eb" },
  "fi": { "label": "File", "accent": "#64748b" },
  "cnv": { "label": "Canvas", "accent": "#7c3aed" }
}
```

Every surface that renders context chips/cards should receive the same map from
scene config. The style is not owned by canvas, chat, memory, or task. A scoped
ref such as `task:issue:attachment:...` uses style key `task`.

## SDK Runtime API

The generic pieces live in the SDK scene runtime and are also exported by
`@kdcube/components-core/scene`.

Reusable APIs:

```ts
export function normalizeContextDragMessage(input: unknown): ActiveContextDrag | null

export function rootNamespaceFromRef(ref: unknown): string

export function rootNamespaceCandidates(item: ContextItem): string[]

export function createContextDragBroker(options: {
  objectAction: (request: {
    action: "open"
    object_ref: string
    target_surface?: string
    context?: ContextItem
  }) => Promise<Record<string, unknown>>
  dispatchOpenResponse: (response: Record<string, unknown>, source?: Record<string, unknown>) => SceneDispatchResult
})
```

Minimal broker behavior:

```ts
broker.handleDragStart(message)
broker.handleDragEnd()
broker.accepts(target, contextItem)
broker.dropOnTarget(target)
broker.clear()
```

The runtime still does not know memory/task semantics. It only knows canonical
context drag messages, namespace matching, provider action invocation, and
surface dispatch.

Current consumers:

- The versatile scene imports `@kdcube/components-core/scene`, which currently
  resolves to the materialized SDK source during bundle builds.
- The website landing scene uses the same contract through its plain-script
  `KDCScene.createContextDragBroker()` adapter until the site consumes the ESM
  package directly.
- The npm package now exposes `@kdcube/components-core/scene` as a real subpath,
  so future hosts can import it without SDK source materialization.

## Current Implementation Status

| Area | Current state | Gap |
| --- | --- | --- |
| Chat/search result source surfaces | Emit or carry canonical context payloads in current paths. | Continue replacing remaining local helpers with package helpers as chat package becomes default. |
| Standalone pinboard source surface | Emits `kdcube-context-drag-start/end` when cards are dragged. | Needs to stay aligned when canvas moves into npm packages. |
| Embedded canvas board in versatile scene | `onDragCard` now feeds the scene context-drag broker. | Continue validating canvas-card -> owning-widget drops across mounted external panels. |
| Website landing scene | Uses the scene broker contract for context normalization, namespace matching, and provider-backed open drops. | Plain-script adapter remains until the website can import the ESM package. |
| Versatile scene | Uses `createSceneRuntime()` for target-surface dispatch and `createContextDragBroker()` for owning-surface drops. | Canvas pinning remains a host-provided `pin` effect, by design. |
| Memory widget | Has both host-command open and native drop parsing. | Native drop parser is convenience only; host-command open is the generic path. |
| Task widget | Has target-surface command handling for issue list/editor paths. | Needs reliable broker input from all source surfaces. |
| Providers | `mem` and `task` resolve `open` through named-service/canvas resolver paths. | New namespaces must advertise and implement `object.action open`. |
| Namespace styles | Config exists and is passed through scene/widget config to chat and canvas surfaces. | New surfaces must consume the shared namespace presentation map instead of inventing local colors. |

## Acceptance Matrix

The scene contract is complete only when these cases work without source-widget
specific code in the target widget:

| Source | Target | Expected result |
| --- | --- | --- |
| Chat named-service search result | Canvas | Pin proxy card with same `ref`. |
| Chat named-service search result | Owning widget | Open/focus object through provider `open`. |
| Canvas card | Chat | Attach context with same `ref`. |
| Canvas card | Owning widget | Open/focus object through provider `open`. |
| Memory row | Canvas | Pin `mem:record:...` proxy card. |
| Memory row | Memory widget | Open/focus memory through scene or local fallback. |
| Task issue row | Canvas | Pin `task:issue:...` proxy card. |
| Task issue card | Task widget | Open/focus issue in the task surface. |
| Task attachment ref | Task widget | Provider-defined behavior, such as open parent issue focused on attachment. |
| Unknown namespace ref | Canvas | Pin unresolved proxy card; object actions unavailable. |
| Unknown namespace ref | Owning widget | No target; scene reports unavailable. |

## Migration Steps

1. Keep the canonical context-pin contract as the single payload shape.
2. Move context drag normalization and namespace matching from concrete host
   pages into the SDK scene runtime.
3. Replace per-host drop handlers with runtime broker calls.
4. Ensure every source surface uses the same drag-start/end helper.
5. Ensure every target surface is registered with:
   - target surface id;
   - accepted root namespaces;
   - local command mapper;
   - readiness/queue policy.
6. Keep provider `open` as the only authority for final target-surface routing.
7. Add the acceptance matrix as browser/integration tests for both the website
   landing scene and the versatile scene.

## Failure Modes To Surface

| Failure | UI/result |
| --- | --- |
| Source did not emit drag-start | Drop zones do not arm; console/debug should identify missing source drag event. |
| Context has no canonical `ref` | Drop rejected as invalid context. |
| Target does not accept root namespace | Drop target stays inactive. |
| Provider not configured for namespace | Object action returns provider-not-found; scene shows unavailable action. |
| Provider rejects requested target | Scene routes provider response or shows provider error; frontend does not force the target. |
| Target surface not registered | Scene reports `target_surface_unavailable`. |
| Target surface not ready | Scene queues command until ready; if readiness never arrives, show bounded timeout/debug state. |

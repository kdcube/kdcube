---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
title: "Cross-Surface Context Drag"
summary: "Concrete design for generic object drag/drop between scene surfaces: source drag lifecycle, scene broker state, selector-based candidate targets, provider open resolution, target-surface command dispatch, and current migration gaps."
status: implementation
tags: ["sdk", "solutions", "scene", "drag-drop", "context-drag", "surfaces", "named-services", "canvas", "chat"]
updated_at: 2026-07-06
keywords:
  [
    "cross surface drag",
    "context drag broker",
    "kdcube-context-drag-start",
    "kdcube-context-drag-end",
    "context drag",
    "canvas ingress",
    "surface selector policy",
    "requested target surface",
    "object action open",
    "target_surface",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-event-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/context-drag-README.md
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
  -> scene matches drop targets by declarative selector policy
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
| Selector policy | Declarative compatibility rule such as `*`, `mem:*`, or `task:issue:*`. It marks candidate targets only; it does not decide provider behavior. |
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
  select compatible SceneDropTarget entries by selector policy
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

When `ui_event.target_surface` names an external-panel surface, its descriptor
decides the delivered command and window state — see
[External Panels And Provider-Open Routing](config/README.md#external-panels-and-provider-open-routing).

The overlay is host UI, not a widget. It exists because browser native drag/drop
does not reliably cross nested iframe boundaries, especially cross-origin. The
host receives source drag messages, owns the active drag context, and builds
drop hit areas in the parent page coordinate space.

### Control-Plane Messages

| Message / action | Direction | Lifetime | Name format | Purpose |
| --- | --- | --- | --- | --- |
| `kdcube-context-drag-start` | source surface -> scene host | One browser drag | Dash-case transport name | Starts an active scene drag with canonical `contexts`. |
| `kdcube-context-drag-end` | source surface -> scene host | One browser drag | Dash-case transport name | Supplies final coordinates and lets the host resolve a drop when native `drop` does not reach the parent overlay. |
| `kdcube.canvas.ingress.drag_start/end` | source surface -> scene host | One browser drag | Dotted scene protocol name | Carries a `kdcube.canvas.ingress` packet toward canvas pinning while preserving drag coordinates. |
| `object.action(open)` | scene host -> namespace provider | Drop handling | Namespace-service operation | Provider validates the full URI and returns the actual `ui_event.target_surface`. |
| Target command, for example `kdcube-memory-widget-command` | scene host -> target iframe | Drop handling | Dash-case transport name | Local browser command delivered after provider/open resolution. |

Dash-case names are browser transport names. Canonical object identity remains
inside the payload as `context.ref` / `object_ref`, for example
`task:issue:...`, `mem:record:...`, or future event identities such as
`task:event:task-changed` where they are payload values rather than DOM-style
event names.

## Canvas Ingress Packet

Canvas ingress is the canonical cross-surface packet for placing something on a
canvas. The message type is:

```text
kdcube.canvas.ingress
```

`ingress` means "this payload is entering the canvas from another surface". It
does not mean canvas owns the object. Canvas receives the packet, creates or
updates a card, and keeps the underlying object identity intact.

There are two distinct layers:

```text
canonical packet:
  kdcube.canvas.ingress

optional drag lifecycle wrapper:
  kdcube.canvas.ingress.drag_start
  kdcube.canvas.ingress.drag_end
```

The wrapper exists only so the browser scene can track pointer coordinates
across iframe boundaries. The canonical packet must be valid without the
wrapper, so a host can also deliver it directly.

### Object Ref Payload

Use this when the source already has a canonical object URI:

```json
{
  "type": "kdcube.canvas.ingress",
  "payload": {
    "object_ref": "conv:fi:conv_.../weather_cli.zip",
    "mime": "application/zip",
    "title": "weather_cli.zip",
    "filename": "weather_cli.zip",
    "preview": "Optional display preview",
    "presentation": {
      "label": "file",
      "namespace": "fi",
      "object_kind": "conv:fi:artifact"
    }
  }
}
```

Rules:

- `object_ref` is the identity. Canvas, scene, and target widgets must not
  derive behavior from a local parser of this URI.
- `presentation` is optional and cosmetic. It may provide label, namespace, or
  object-kind hints for immediate display while provider metadata is loading.
- Actions such as open, download, attach, or edit come from the resolver or
  provider response, not from `presentation.label` or card `kind`.

### Text Content Payload

Use this when the source has raw text, not a provider object URI:

```json
{
  "type": "kdcube.canvas.ingress",
  "payload": {
    "title": "Selection",
    "content": {
      "mime": "text/markdown",
      "text": "Selected text to place on the canvas."
    },
    "presentation": {
      "label": "provided.text",
      "object_kind": "canvas.card"
    }
  }
}
```

Rules:

- `payload.object_ref` means "pin this existing provider object".
- `payload.content.text` means "host this provided text on the canvas".
- If the text is placed on canvas, canvas hosts it as a canvas-owned object and
  assigns the resulting `cnv:` identity.
- `agent.text`, `user.text`, or `provided.text` are card/display labels after
  materialization. They are not cross-surface protocol message types.

### Source Stamping

The source widget composes only the object/text data it knows. In a scene, the
scene host stamps provenance from its surface registry:

```json
{
  "source": {
    "surface_ref": "website.chat",
    "component": "chat",
    "app": "workspace@2026-03-31-13-36",
    "runtime": "default"
  }
}
```

The source widget usually does not know `app`, `runtime`, or scene-local
surface aliases. For example, a chat file chip only knows the file ref, mime,
filename, and preview. The website scene reads the iframe/surface alias from
the mounted frame and looks up the configured component/app/runtime before
forwarding the packet to canvas.

### End-To-End Flow

```text
chat/file chip, memory row, search result, or another source
  creates:
    kdcube.canvas.ingress
    payload.object_ref OR payload.content.text
        |
        v
source drag helper
  wraps during pointer drag:
    kdcube.canvas.ingress.drag_start { ingress, x, y }
        |
        v
scene host
  validates ingress packet
  stamps payload.source from scene config / iframe attrs
  arms canvas drop overlay
        |
        v
pinboard/canvas surface
  receives:
    kdcube.canvas.ingress { payload, x, y }
  materializes a generic canvas card
  preserves object_ref for provider-owned objects
        |
        v
provider / resolver layer
  owns open/download/capability behavior for object_ref
```

Concrete current examples:

- Chat file drag builds the minimal `kdcube.canvas.ingress` packet in
  `components-react/src/chat/ui/support/fileDrag.ts`.
- Website scene receives the drag wrapper and stamps source metadata in
  `website/scene-context-drag.js`.
- Pinboard parses `data.ingress` and creates a generic card in
  `canvas/ui/widget/pinboard/src/App.tsx`.
- Workspace's embedded scene follows the same protocol in
  `examples/bundles/workspace.../ui/scene/src/main.tsx`.

No task/memory/file-specific card constructor is required for this path. A
canvas card is a generic container plus preserved `object_ref`; the provider
realm supplies object actions and richer metadata.

### Overlay Target Map

| Target type | Host representation | Match key | Drop effect | Delivery |
| --- | --- | --- | --- | --- |
| Mounted chat | Transparent overlay over chat iframe | `*` | `attach` | `postMessage({ type: "kdcube.context.attach", context })`; conversation refs may load the conversation. |
| Mounted canvas/pinboard | Transparent overlay over pinboard iframe | `*` or ingress-only path | `pin` | `kdcube.surface.command` with `action: "pin"`, `context`, and drop-point `x`/`y`, or ingress payload equivalent. |
| Mounted memory surface | Transparent overlay over memory iframe | `mem:*` | `open` | Provider-backed open to `sdk.memory.viewer` — the memory-ITEM editor surface (`memory_item` component), distinct from the `sdk.memory.list` surface the memories list registers. |
| Mounted task surface | Transparent overlay over task iframe | `task:*` or a narrower provider claim | `open` | Provider-backed open to task target surface, then local task command. |
| Unmounted compatible surface | Rail pulse on summon button | Same selector policy match | Target-specific | Open/summon first, then deliver after readiness/short delay. |

Namespace color for the overlay comes from shared namespace presentation config,
keyed by explicit `object_kind` or `namespace` metadata. The overlay must not
hardcode memory/task/file colors by widget.

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
- `namespace` and `object_kind` are optional display metadata. If absent, the
  scene uses neutral presentation until provider/source metadata is available.
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
  accepts: "*" | string[] | Record<"open" | "attach" | "pin" | "context", string[] | string>
  dropEffect?: "open" | "attach" | "pin"
}
```

Required scene behavior:

1. On `kdcube-context-drag-start`, normalize all context items and store one
   active drag context.
2. Evaluate the target's selector policy against each item's `ref`. Supported
   selectors are exact refs, `*`, and prefix wildcards such as `mem:*` or
   `task:issue:*`.
3. Mark a drop target active when any selector matches the `ref` for the
   target's configured effect.
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

### Drag-Source Pointer Events

While a context drag is active, hosts suppress pointer events on their iframes
so `dragover`/`drop` reach the host's overlay elements instead of vanishing
into the frames. The **drag source frame is the exception**: it must keep
`pointer-events: auto` for the whole drag.

The reason is in-surface drops. A canvas card reposition is a native browser
drag that starts AND ends inside the same iframe; card drags also emit
`kdcube-context-drag-start` to the host (that is the cross-surface contract).
If the host strips pointer events from the source frame too, drag hit-testing
skips that iframe, the board's own drop handler never fires, and cards can
never be moved while the scene is mounted. Both shipped hosts implement the
exception the same way: on drag-start the host tags the sender iframe (class
`kdc-drag-source`, cleared on drag-end) and the stylesheet keeps that frame
interactive:

```css
body.kdc-ctxdrag iframe { pointer-events: none !important; }
body.kdc-ctxdrag iframe.kdc-drag-source { pointer-events: auto !important; }
```

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

- Validate against the full URI, not just the selector/prefix that made the
  surface a candidate.
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

## Namespace Presentation

Namespace presentation is app/runtime configuration, keyed by provider/source
presentation metadata:

```json
{
  "mem": { "label": "Memory", "color": "#16a34a", "border": "#16a34a", "focus": "#22c55e" },
  "task": { "label": "Task", "color": "#2563eb", "border": "#2563eb", "focus": "#60a5fa" },
  "task:attachment": { "label": "Task file", "color": "#0f766e" },
  "conv:fi": { "label": "File", "color": "#ca8a04", "border": "#ca8a04", "focus": "#facc15" },
  "cnv": { "label": "Canvas", "color": "#7c3aed", "border": "#7c3aed", "focus": "#a78bfa" }
}
```

Every surface that renders context chips/cards should receive the same map from
the runtime namespace presentation config. The style is not owned by canvas,
chat, memory, task, or a host page. Consumers look up `object_kind` first,
then a ref-derived owner key such as `conv:fi`, then `namespace`, then use a
neutral unknown fallback. They must use the shared presentation resolver for
that derivation so chat, canvas, scene, and website drag overlays stay visually
consistent.

The server-owned presentation map is exposed by the hosting app through:

```text
POST /api/integrations/bundles/<tenant>/<project>/<bundle>/public/namespace_presentation_config
```

The scene normally fetches this map first and passes it to hosted widgets in
their config handshake. Chat and canvas widgets can also fetch the same public
endpoint directly when embedded without a scene host.

## SDK Runtime API

The generic pieces live in the SDK scene runtime and are also exported by
`@kdcube/components-core/scene`.

Reusable APIs:

```ts
export function normalizeContextDragMessage(input: unknown): ActiveContextDrag | null

export function matchObjectSelector(selector: string, objectRef: string): boolean

export function matchesAnyObjectSelector(selectors: string[], objectRef: string): boolean

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
context drag messages, selector matching, provider action invocation, and
surface dispatch.

Host-side plain-browser adapters can also reuse framework-neutral helpers:

```ts
normalizeHostContextDragStartMessage(message, {
  startTypes: ["kdcube-context-drag-start"]
})
presentationStyleCandidates(context)
computeSceneDragScreenCalibration(message, frameRect)
scenePointFromChildDragMessage(message, frameRect, calibration)
selectSceneDropTargetAtPoint(targets, point)
```

These helpers operate on plain objects and geometry. The host still owns overlay
DOM, rail buttons, iframe creation, and local command delivery. Compatibility
message aliases can be accepted through `startTypes`, but the canonical producer
contract remains `kdcube-context-drag-start`.

Drop target maps are also normalized in the shared package:

```ts
mergeSceneContextDropTargets(sceneTargets, profileOverrides)
sceneContextDropTargetsFromConfig(activeProfile)
normalizeSceneContextDropTargets(activeProfile, {
  knownDeliveries: ["chat.attach", "pinboard.pin", "memory.open", "task.open"]
})
```

Those helpers validate structural routing config only: `surfaceRef`, `railId`,
`accepts`, `dropEffect`, `targetSurface`, and `delivery`.
Presentation colors remain server-owned namespace presentation data.

Current consumers:

- The workspace scene imports `@kdcube/components-core/scene`, which currently
  resolves to the materialized SDK source during bundle builds.
- Plain-script host pages can use the same contract through a local adapter
  around `KDCScene.createContextDragBroker()` until they consume the ESM package
  directly.
- The npm package now exposes `@kdcube/components-core/scene` as a real subpath,
  so future hosts can import it without SDK source materialization.

## Current Implementation Status

| Area | Current state | Gap |
| --- | --- | --- |
| Chat/search result source surfaces | Emit or carry canonical context payloads in current paths. | Continue replacing remaining local helpers with package helpers as chat package becomes default. |
| Standalone pinboard source surface | Emits `kdcube-context-drag-start/end` when cards are dragged; the workspace scene mounts this widget as its board surface. | Needs to stay aligned when canvas moves into npm packages. |
| Plain-script host scene | Uses the scene broker contract for context normalization, selector matching, provider-backed open drops, and config-driven target maps. | Host-local adapter remains until that host can import the ESM package. |
| Workspace scene | Uses `createSceneRuntime()` for target-surface dispatch and `createContextDragBroker()` for owning-surface drops. | Canvas pinning remains a host-provided `pin` effect, by design. |
| Memory widget | Has both host-command open and native drop parsing. | Native drop parser is convenience only; host-command open is the generic path. |
| Task widget | Has target-surface command handling for issue list/editor paths. | Needs reliable broker input from all source surfaces. |
| Providers | `mem` and `task` resolve `open` through named-service/canvas resolver paths. | New namespaces must advertise and implement `object.action open`. |
| Namespace styles | Config exists on the app runtime and is exposed through the public `namespace_presentation_config` endpoint. The scene passes it through widget config to chat, canvas, and overlay surfaces. | New surfaces must consume the shared namespace presentation map instead of inventing local colors. |

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
2. Move context drag normalization and selector matching from concrete host
   pages into the SDK scene runtime.
3. Replace per-host drop handlers with runtime broker calls.
4. Ensure every source surface uses the same drag-start/end helper.
5. Ensure every target surface is registered with:
   - target surface id;
   - selector policy;
   - local command mapper;
   - readiness/queue policy.
6. Keep provider `open` as the only authority for final target-surface routing.
7. Add the acceptance matrix as browser/integration tests for host scenes that
   embed multiple iframe surfaces, including the workspace scene.

## Failure Modes To Surface

| Failure | UI/result |
| --- | --- |
| Source did not emit drag-start | Drop zones do not arm; console/debug should identify missing source drag event. |
| Context has no canonical `ref` | Drop rejected as invalid context. |
| Target selector does not match the object ref | Drop target stays inactive. |
| Provider not configured for namespace | Object action returns provider-not-found; scene shows unavailable action. |
| Provider rejects requested target | Scene routes provider response or shows provider error; frontend does not force the target. |
| Target surface not registered | Scene reports `target_surface_unavailable`. |
| Target surface not ready | Scene queues command until ready; if readiness never arrives, show bounded timeout/debug state. |

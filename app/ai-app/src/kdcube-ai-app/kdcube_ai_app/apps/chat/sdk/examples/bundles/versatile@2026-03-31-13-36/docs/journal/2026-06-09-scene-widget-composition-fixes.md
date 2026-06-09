---
title: Scene Widget Composition Fixes
date: 2026-06-09
bundle_id: versatile@2026-03-31-13-36
topic: sdk component scene composition
---

# Scene Widget Composition Fixes

## Context

The active Versatile main view was moved to `ui/scene` so the reference bundle
can compose reusable SDK components:

- chat widget: `sdk://solutions/chat/ui/widget`
- memory widget: `sdk://context/memory/ui/widget/memories`
- canvas component: `sdk://solutions/canvas/ui/component`

Initial testing showed the scene mounted the pieces, but the UX was not stable:
the memory widget could collapse into a tiny line, expanded memories stretched
like a page instead of a useful panel, the scene had a redundant reconnect
button, canvas and chat controls did not match the task-tracker pattern, and
chat width could not be resized.

## Fixes Made

### Host Owns Widget Geometry

The scene now owns widget panel geometry. Embedded widgets must not be allowed
to resize the scene implicitly through iframe resize messages.

Changes:

- replaced the old two-row right column with host-owned floating widget
  panels;
- moved memory into a floating overlay panel;
- moved chat into a floating iframe panel with host-owned drag and resize
  controls;
- made memory panel position host-controlled;
- kept iframe dimensions at `width: 100%` and `height: 100%` inside the host
  panel.

Scene composition rule:

```text
scene host controls panel size and placement
  -> iframe/component fills that panel
  -> iframe may request compact/expanded state
  -> iframe does not own host geometry
```

### Right Rail Controls

The scene now follows the task-tracker interaction model:

- chat button on the right rail;
- canvas button on the right rail;
- memory button on the right rail.

The rail is a real scene grid column, not a fixed overlay. This matters because
the chat iframe can be moved or resized near the right edge without the rail
covering chat content.

The scene-level "Reconnect chat" button was removed. Connection recovery is a
chat-widget responsibility and already belongs inside the chat widget.

### Chat Window

The chat widget is mounted as an iframe. The host scene owns a small window
chrome around it so the chat can be moved without reaching into the iframe DOM.

The chat panel has:

- a host drag header;
- compact/enlarge and close controls;
- a left resize handle in compact mode;
- fullscreen behavior delegated by scene state and synchronized to the chat
  iframe through `kdcube-set-view`.

Resize follows the task-tracker direction:

```text
drag chat left edge left  -> wider chat
drag chat left edge right -> narrower chat
```

The scene stores the width and frame position in host state and clamps both to
the viewport. The chat iframe remains mounted while hidden so conversation state
is not lost when the right rail toggles chat visibility.

The scene passes `chat_embed_mode=host` to the chat widget URL. This is the
explicit iframe sizing protocol for same-origin composed scenes:

```text
chat_embed_mode=host
  -> host owns iframe rectangle
  -> widget fills width/height 100%
  -> widget does not render its same-origin dev preview tile
  -> widget does not apply its own centered max-width box
```

Without this flag the chat widget sees the same-origin parent and enters its
local preview mode, boxing the chat into a fixed-size demo tile. That makes the
outer scene resize only the panel chrome while the actual chat remains fixed
inside it.

The site landing-page scene should use this same iframe composition model:

```text
page
  -> scene iframe
       -> chat iframe
       -> memories iframe
       -> canvas component/iframe surface
```

That keeps each reusable widget behind its own small host protocol instead of
requiring one page build to import every SDK component directly.

### Memory Overlay Behavior

Memory is no longer placed as a second row below chat. It is opened on demand as
a floating panel because the intended workflow is usually:

```text
open memories
  -> find/select memory
  -> drag memory to chat or canvas
  -> close memories
```

The memory overlay is draggable in compact and expanded states. Expanded memory
is a large movable work panel, not a viewport-pinned page.

The memory overlay opens over the canvas workspace by default, not over the
chat column. This keeps chat readable while memories are used as a transient
source for dragging objects to canvas or chat.

The host scene owns overlay chrome: expand/compact and close buttons live in
the host panel header. The embedded memory widget receives `host_controls=1`,
so compact widget chrome does not render a second expand button.

### Memory Expanded Content

The memory widget itself no longer shows reconciliation/maintenance by default
in expanded mode. Maintenance is still available, but behind an explicit
"Show maintenance" button.

This keeps the default expanded view focused on:

- filters;
- memory list;
- memory detail/editor.

The memory detail/editor panel is now conditional. It is rendered only when a
memory is selected or an editor is active. Without that guard, the shared memory
widget reserved an empty right-side panel and made both the task-tracker and
Versatile expanded memory views look like they had a large dead area.

The memory widget emits memory-owned drag lifecycle events
(`kdcube.memory.drag.start`, `kdcube.memory.drag.end`) for scene coordination.
For chat/context attachment it emits the generic context protocol
(`kdcube.context.attach` with `application/vnd.kdcube.context+json`) where the
payload contains `contexts[]` and the memory object remains `mem:<id>`.
Consumers must not depend on memory-specific context message names; they read
the generic context shape and preserve `event_source_id: memory.context`.

Compact memory controls use this split:

- `+` means add a memory note;
- expand/compact arrows belong to the host overlay header;
- close belongs to the host overlay header.

When `host_controls=1` is set, the memory iframe suppresses its own compact
header controls. The host panel header becomes the single chrome row:

```text
---------------- memory host header ----------------+
| Memories  2 in scope                 +  expand  x |
+---------------------------------------------------+
```

The `+` button stays styled as a primary teal action and sends a
memory-owned command to the iframe:

```json
{
  "type": "kdcube-memory-widget-command",
  "widget": "memories",
  "action": "create"
}
```

The memory widget also publishes a small status event to the parent so the
host can render the scope count without reimplementing memory list logic:

```json
{
  "type": "kdcube-memory-widget-status",
  "widget": "memories",
  "count": 2,
  "compact": true
}
```

Memory rows now cap visible chips instead of rendering every label/keyword in
the row. Compact rows show a few terms, full rows show a bounded set, and the
remaining count is represented by a `+N` chip. This prevents narrow memory
columns from slicing through chips or making the item height unpredictable.

The memory overlay drag handler also disables iframe pointer events while the
host panel is being moved and cleans up on pointer-up, pointer-cancel, or
window blur. This avoids the "panel sticks to the cursor" failure caused by a
lost pointer-up when the cursor crosses an iframe.

### Canvas Styling and Sizing

The canvas component is still mounted as a shared source into the scene. The
host scene keeps the canvas in a dedicated workspace panel and the panel keeps
stable height so the canvas grid, cards, and bottom actions remain usable.

### Event Visibility Split

Versatile now has an explicit `events_descriptor.py`.

The rule is:

```text
tools_descriptor.py  -> model-callable tools
events_descriptor.py -> event sources, policies, readers, namespace rehosters
```

This was needed because Versatile should understand canvas-owned `cnv:` refs
for `react.pull`, but it should not expose `canvas.patch` as an agent tool. The
canvas namespace rehoster now comes from
`kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver` through
`EVENT_SOURCE_SPECS`.

The workflow passes those specs into `BaseWorkflow.build_react(...)`, and the
entrypoint preview path uses the same specs when building an
`EventSourceSubsystem`. The two paths must stay aligned; otherwise dry-run or
preview can understand refs that live ReAct cannot pull.

The scene keeps the canvas workspace shell mounted even when the user hides the
canvas. Hiding the canvas swaps its content for an empty-state panel instead of
removing the grid column. This prevents the chat widget and right rail from
moving into the center of the scene when the canvas is closed.

Scene CSS also provides the common canvas host rules that the SDK component
expects, including `.sr-only` for file inputs and button styles for
`.canvas-actions`. Missing these host rules exposed the browser-native
"no file chosen" text and made SDK canvas controls look unstyled.

The canvas component root is explicitly assigned to the body row of the canvas
shell. Without that rule, an empty canvas with no notice banner could collapse
to header height and look uninitialized.

Canvas mutations in this scene use the generic `canvas.patch` Data Bus subject.
The server handler must propagate Data Bus scope into the canvas operation
payload:

```python
payload.setdefault("tenant", ctx.tenant)
payload.setdefault("project", ctx.project)
payload.setdefault("user_id", message.actor.get("user_id"))
```

This matters because Data Bus messages carry tenant/project on the message
envelope, while actor metadata carries user/fingerprint/roles. If the handler
only copies actor fields, canvas store creation may fall back to default scope
or fail before creating the revision.

SDK components that need storage scope should use the shared
`BaseEntrypoint.runtime_identity()` helper rather than copying a private helper
from another bundle.

Canvas object actions now log both sides of the resolver path:

```text
browser: [versatile:canvas] object action request/response
server:  [canvas.object_action] requested/resolved/failed
```

The server log includes action, namespace, resolver, resolver status, object
ref, user id, story id, whether a downloadable payload was returned, and
whether a UI open event was returned. The canvas UI no longer reports
`Download prepared` unless `content_base64` exists in the resolver response.

Memory card `open` uses the memory resolver result:

```json
{
  "ui_event": {
    "target_surface": "sdk.memory.viewer",
    "memory_id": "mem_..."
  }
}
```

The scene host translates that into the same memory-owned widget command:

```json
{
  "type": "kdcube-memory-widget-command",
  "widget": "memories",
  "action": "open",
  "memory_id": "mem_..."
}
```

The host keeps one pending memory command and flushes it when the iframe loads,
so opening a memory from a canvas card does not depend on whether the memory
panel was already mounted.

The scene still uses current explicit Vite shared-source glue. This is a
reference integration detail, not the desired final developer ergonomics.

## Lessons for SDK Documentation

### Component Requirements

A reusable widget/component intended for scene composition should support:

| Requirement | Reason |
| --- | --- |
| compact and expanded view messages | Host owns fullscreen/large-panel state. |
| stable iframe fill layout | Widget fills the host frame and does not resize the host. |
| context drag messages | Host can broker dropped objects to chat/canvas. |
| owner-specific resolver handoff | Widget can expose object refs without embedding transport details. |
| internal reconnect controls for live transports | Host should not duplicate chat transport controls. |

### Scene Host Requirements

A scene that composes widgets should provide:

| Requirement | Reason |
| --- | --- |
| owned geometry for every panel | Prevent iframe resize feedback loops. |
| right rail as a real layout column | Consistent with task-tracker UI; prevents overlap with movable panels. |
| draggable overlays for transient tools | Memory/canvas helpers should not permanently consume layout. |
| explicit fullscreen/expanded handling | Widget requests state; host applies state. |
| stable message broker | Context, view, and auth messages move between iframes and platform. |

## Verification

Build checks run after the fixes:

```bash
OUTDIR=/private/tmp/versatile-scene-build npm run build
OUTDIR=/private/tmp/memory-widget-build npm run build
```

Both builds passed.

## Follow-up: Floating Panel Focus and Resizable Memory Layout

User testing exposed two remaining scene-composition problems:

1. Clicking or working inside an iframe did not bring that panel to the front.
2. The expanded memory widget clipped list/detail content and left a large
   unusable blank region when the host window was resized.

### Focus Promotion

The host scene can receive pointer events from panel chrome, but it cannot see
pointer events inside a child iframe. The fix uses a small generic widget focus
message:

```json
{
  "type": "kdcube-widget-focus",
  "widget": "versatile_chat"
}
```

```json
{
  "type": "kdcube-widget-focus",
  "widget": "memories"
}
```

The scene owns z-order. Every focus message, host-header drag, rail open, and
resize action increments a scene-local z counter and applies it to the selected
panel. The widgets only report focus; they do not know scene geometry.

### Configurable Chat Geometry

The scene now reads chat sizing from query parameters:

| Parameter | Meaning |
| --- | --- |
| `chat_width` / `chatWidth` | Initial compact chat panel width. |
| `chat_height` / `chatHeight` | Initial compact chat panel height. |
| `chat_min_width` / `chatMinWidth` | Minimum width for the scene resize handle. |
| `chat_max_width` / `chatMaxWidth` | Maximum width for the scene resize handle. |

The host clamps these values to the current viewport. This keeps the SDK chat
widget reusable on a landing-page scene where the desired default is narrower
than the control-plane task-tracker layout.

### Memory Widget Layout

The memory widget now treats the iframe as a bounded viewport:

```text
expanded-shell
  fixed header / preferences / filters
  workspace: flexes to remaining height
    memory-list: scrolls internally
    side-panel
      memory-detail or editor: scrolls internally
```

The widget no longer scrolls the entire expanded page for normal list/detail
use. This prevents the list rows from being clipped at the top while a large
white area remains below. The host still owns the outer panel resize; the memory
iframe fills that rectangle.

### Landing Page Switch

`website/index.html` now points the live versatile tile at the reusable chat
widget route:

```text
public/widgets/versatile_chat?chat_embed_mode=host&...
```

The landing host keeps its existing iframe/drop overlay mechanics and sends
generic context messages:

```json
{
  "type": "kdcube.context.attach",
  "context": { "...": "..." }
}
```

The route change means the landing page is no longer embedding the old
versatile main UI for chat.

### Verification

Build checks after this follow-up:

```bash
npm run build  # ui/scene
npm run build  # sdk/context/memory/ui/widget/memories
npm run build  # sdk/solutions/chat/ui/widget
```

All three builds passed. The scene and chat builds still emit Vite chunk-size
warnings; those are unchanged from the current bundle shape.

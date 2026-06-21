# Recipe: Canvas Pinboard

The canvas pinboard is an app widget that materializes object references as spatial pins. It is a context target for almost every namespace and a context source when the user drags pins back to other widgets.

## Runtime Shape

```text
pinboard iframe
  board state
    board id
    pins
    positions
  namespace presentation
    color by root namespace
    label/icon by object metadata
  drag adapter
    context drag start/end
    local pin movement
  backend API
    pin create/update/delete
    search
    object action routing
```

## Scene Boundary

The scene decides where cross-surface drops go. The pinboard decides how pins are stored and moved inside the board.

```text
context dropped on pinboard
  scene -> kdcube.surface.command target_surface=sdk.canvas.pinboard action=pin
  pinboard -> create pin for object_ref

pin dragged from pinboard
  pinboard -> kdcube-context-drag-start
  scene -> highlight matching targets
  scene -> attach/open/pin based on drop target
```

## Namespace Styling

Pin color is namespace-owned. The pinboard should consume the same namespace presentation config as chat and scene overlays:

```text
object_ref = task:issue:...
root namespace = task
namespace presentation -> color
pin rendering -> task color
scene overlay -> same task color
chat chip -> same task color
```

Canvas-specific fallback colors hide configuration bugs. Missing namespace config should be visible in logs.

## Config

```json
{
  "contextDropTargets": {
    "pinboard": {
      "surfaceRef": "website.pinboard",
      "acceptsRootNamespaces": ["*"],
      "dropEffect": "pin",
      "targetSurface": "sdk.canvas.pinboard",
      "action": "pin"
    }
  }
}
```

## Event And Data Bus

Pinboard board changes are naturally Data Bus events because they are state patches. Search and embedding economics produce accounting events through the Event Bus.

The widget can run standalone by opening its own runtime connections. In a scene, live delivery should become a declared transport choice:

```json
{
  "widgetConfig": {
    "pinboard": {
      "liveEventsTransport": "scene",
      "dataBusTransport": "scene"
    }
  }
}
```

## Current Gaps

- Data Bus relaying by the scene is not yet as complete as Event Bus relaying.
- The pinboard still carries more local scene-awareness than the final shared component should.

## Related Docs

- [Canvas SDK Solution](../../sdk/solutions/canvas/canvas-sdk-solution-README.md)
- [Canvas Module Guide](../../sdk/solutions/canvas/canvas-module-guide-README.md)
- [Pin Integration](../../sdk/solutions/canvas/pin-integration-README.md)
- [Canvas Search Operations](../../sdk/solutions/canvas/search-operations-README.md)
- [Context Pin Contract](../../sdk/npm/components-core/context-pin-contract-README.md)

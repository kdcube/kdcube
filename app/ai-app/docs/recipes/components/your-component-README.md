# Recipe: Your Component

Use this checklist when adding a new app widget to a scene. The goal is that the scene can compose the widget without learning its domain internals.

## Minimum Shape

```text
your app
  server side
    optional named-service provider
    optional widget static route
    optional Event Bus/Data Bus producers
  client side
    iframe widget
    host config receiver
    scene subscription claim if embedded
    standalone stream fallback if configured
    context drag/drop adapter if it handles objects
```

## Widget Startup

```text
load iframe
  read URL params: tenant, project, app id, data scope
  wait briefly for host config if embedded
  decide transport from config:
    liveEventsTransport=scene -> post kdcube-scene-subscribe
    liveEventsTransport=sse   -> open own stream
    liveEventsTransport=none  -> no live stream
  render initial snapshot from backend
```

The widget should log the selected transport and data scope. This makes mixed-runtime scenes debuggable.

## Scene Declaration

Add only data to the scene config:

```json
{
  "contextDropTargets": {
    "your_widget": {
      "surfaceRef": "website.your_widget",
      "acceptsRootNamespaces": ["your"],
      "dropEffect": "open",
      "targetSurface": "your.namespace.viewer",
      "action": "open"
    }
  },
  "widgetConfig": {
    "your_widget": {
      "liveEventsTransport": "scene"
    }
  },
  "surfaceCommandContracts": {
    "your.namespace.viewer": {
      "alias": "your_widget",
      "targetSurfaces": ["your.namespace.viewer"],
      "action": "open"
    }
  }
}
```

The component receives `kdcube.surface.command`, preserves `object_ref`, and translates the command into its own local state/API calls.

## Context Rules

```text
drag out
  emit canonical contexts: [{ ref: "your:object:..." }]

drop in
  accept object_ref
  ask provider object.action(open) when the object is not already local
  render using namespace presentation config
```

The component should never infer namespace color locally when the scene or platform config can provide it.

## Event Rules

```text
embedded in scene
  widget -> kdcube-scene-subscribe
  scene  -> kdcube-scene-event

standalone
  widget opens own SSE/Data Bus connection according to config
```

A widget that already owns a stream should not also subscribe through the scene unless the config explicitly asks for both.

## Related Docs

- [Scene Recipe](./scene-README.md)
- [Bundle Widget Integration](../../sdk/bundle/bundle-widget-integration-README.md)
- [Bundle Client UI](../../sdk/bundle/bundle-client-ui-README.md)
- [UI Components Lifecycle](../../sdk/bundle/ui-components-lifecycle-README.md)
- [Widget Integration Package Notes](../../sdk/npm/widget-integration-README.md)

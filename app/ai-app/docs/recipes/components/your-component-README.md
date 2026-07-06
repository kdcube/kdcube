# Recipe: Your Component

Use this checklist when adding a component role to an app. A component can be a
standalone service, API, MCP surface, cron producer, named-service provider, UI
widget, or scene participant.

Start with [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
and [Component Recipes](./README.md). This recipe is the short implementation
checklist.

## Minimum Shape

```text
your app
  server side
    optional @api / @mcp surface
    optional named-service provider
    optional @cron / @on_job producer
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
      "accepts": "provider-open",
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

## Provider Opens Toward Your Surfaces

When your provider resolves `object.action(open)`, return
`ui_event.target_surface` naming a surface you declared (component
`target_surfaces` or an external-panel `surfaces` entry). The scene executes
that surface's descriptor: it summons the owning window, applies the
declared `expanded` state, and — for a surface with
`command_from_open: provider_surface_open` — forwards your open payload as
the widget command, posted under the panel's `widget_message_type`. Your
widget then receives at minimum:

```json
{
  "type": "<your widget_message_type>",
  "widget": "<your widget alias>",
  "action": "open",
  "object_ref": "your:object:...",
  "target_surface": "your.namespace.viewer"
}
```

Handle it by switching to the view that renders that object. Descriptor
semantics:
[External Panels And Provider-Open Routing](../../sdk/solutions/scene/config/README.md#external-panels-and-provider-open-routing).

## Context Rules

```text
drag out
  emit canonical contexts: [{ ref: "your:object:..." }]

drop in
  accept object_ref
  ask provider object.action(open, object_ref, target_surface)
  render using namespace presentation config
```

The component should never infer namespace color or action behavior locally when
the scene/platform config and provider resolver can provide it.

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

- [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
- [Component Recipes](./README.md)
- [Ecosystem Component Contract](../../sdk/solutions/ecosystem-component/ecosystem-component-README.md)
- [Scene Recipe](./scene-README.md)
- [App Widget Integration](../../sdk/bundle/bundle-widget-integration-README.md)
- [App Client UI](../../sdk/bundle/bundle-client-ui-README.md)
- [UI Components Lifecycle](../../sdk/bundle/ui-components-lifecycle-README.md)
- [Widget Integration Package Notes](../../sdk/npm/widget-integration-README.md)

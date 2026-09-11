---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/widget-integration-README.md
title: "Widget Integration"
summary: "How SDK widgets consume @kdcube/components-* through npm:// shared sources: app UI config, materialization, Vite aliases, and runtime expectations."
status: implementation
tags: ["sdk", "npm", "components", "widget", "vite", "shared-sources", "npm-scheme"]
updated_at: 2026-09-11
keywords:
  [
    "widget integration",
    "npm:// shared source",
    "components packages in image",
    "chat_widget_ui_config",
    "shared_sources",
    "package-ui",
  ]
---

# Widget Integration

SDK widgets can consume the shared TypeScript packages without publishing to an
external npm registry. App UI config declares `shared_sources`; the app build
materializes those sources next to the widget and Vite aliases imports to the
materialized copy.

```text
app UI config
  src_folder: sdk://solutions/chat/ui/widget
  shared_sources:
    components_core: npm://components-core/src
    components_react: npm://components-react/src
        |
        v
build workspace
  widget source
  _shared/components_core
  _shared/components_react
        |
        v
Vite aliases
  @kdcube/components-core  -> _shared/components_core
  @kdcube/components-react -> _shared/components_react
```

## Current Chat Widget

Use the helper for new app code:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chat import chat_widget_ui_config

widgets = {
    "workspace_chat": chat_widget_ui_config(),
}
```

The current default is the package UI path. It materializes
`@kdcube/components-core` and `@kdcube/components-react` through `npm://` and builds
the widget with the package-backed chat engine/UI.

Inline config can express the same thing:

```yaml
workspace_chat:
  enabled: true
  src_folder: sdk://solutions/chat/ui/widget
  engine: package-ui
```

During build, `engine: package-ui` expands to the concrete Vite environment and
shared sources. App authors should prefer the helper or the `engine` field instead
of hand-writing build commands.

## Shared Sources

`shared_sources` can point at:

| Scheme | Meaning |
| --- | --- |
| `sdk://...` | Source inside the SDK tree. |
| `npm://components-core/src` | Source inside the shipped npm package workspace. |
| `npm://components-react/src` | React package source. |
| `bundle://...` | Source relative to the app source root. |
| absolute/relative path | Explicit source path for local development or unusual layouts. |

The build copies each source into `_shared/<name>` unless a target is supplied.
Only source files are copied; `node_modules` remains local to the widget build.

## Runtime Image Requirement

The npm package workspace ships inside the installed app tree at:

```text
app/ai-app/src/kdcube-ai-app/npm/packages
```

In the runtime image this resolves to `/app/npm/packages`. Any widget using
`npm://...` requires an image built from a revision that includes that tree.

## Scene And Canvas Widgets

Widgets that mount scene or canvas UI should consume the scoped package sources
they actually need:

```yaml
main_view:
  src_folder: bundle://ui/main
  build_command: npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build
  shared_sources:
    components_core_scene:
      src_folder: npm://components-core/src/scene
    components_core_events:
      src_folder: npm://components-core/src/events
    components_core_canvas:
      src_folder: npm://components-core/src/canvas
    components_react_canvas:
      src_folder: npm://components-react/src/canvas
```

This keeps widgets from reaching into another app's private UI code.

## Markdown In App-Owned Threads

An app that owns its message transport and thread model can reuse the same body
renderer as platform chat without mounting the chat engine:

```yaml
shared_sources:
  components_react_markdown:
    src_folder: npm://components-react/src/markdown
    target: _shared/components-react/markdown
```

Alias `@kdcube/components-react/markdown` to the materialized `index.ts`, add
`react-markdown`, `remark-gfm`, and `remark-breaks` to the widget package, then
render message bodies with `MarkdownBlock`. Shared sources contain source code;
the widget's local dependencies continue to supply its `node_modules`.

## Boundaries

- A widget may import shared contracts and UI from the npm packages.
- A widget must get runtime config from the app/scene host, not from package globals.
- A widget declares event subscriptions itself through the events client; the host
  should not invent hidden subscriptions for it.
- A widget can emit `kdcube.canvas.ingress`; the scene/host decides which board
  receives it.
- A widget can ask for object actions through host/resolver callbacks; it must not
  hardcode namespace behavior.

## Related Docs

- [Components package map](./README.md)
- [Scene](./components-core/scene-README.md)
- [Canvas pin board](./components-core/canvas-pin-board-README.md)
- [Component events](./components-core/events-README.md)
- [Context drag and canvas ingress](./components-core/context-drag-README.md)

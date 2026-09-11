---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/README.md
title: "KDCube Components npm Packages"
summary: "Map for the shared TypeScript packages: headless component contracts in @kdcube/components-core and React bindings in @kdcube/components-react."
status: implementation
tags: ["sdk", "npm", "components-core", "components-react", "scene", "canvas", "chat", "events"]
updated_at: 2026-06-23
keywords:
  [
    "@kdcube/components-core",
    "@kdcube/components-react",
    "scene runtime",
    "canvas pin board",
    "chat engine",
    "host event bus",
    "component events",
  ]
---

# KDCube Components npm Packages

KDCube's shared client components are split into two npm packages:

| Package | Role | Contains |
| --- | --- | --- |
| `@kdcube/components-core` | Headless contracts and runtimes | chat engine, scene runtime, event claims/client, canvas model/ingress, shared config/types |
| `@kdcube/components-react` | React bindings and reusable UI | chat provider/hooks/default UI, `CanvasBoard`, React wrappers over core contracts |

The packages are not a product-specific website implementation. They are the
reusable layer used by widgets and scenes so apps can join the same ecosystem
without each component inventing its own drag/drop, event, canvas, or chat
protocol.

```text
app widget / website scene
        |
        v
@kdcube/components-react        optional React UI/bindings
        |
        v
@kdcube/components-core         protocol + state + runtime contracts
        |
        v
KDCube app runtime              APIs, Event Bus, Data Bus, named services
```

## Read In This Order

Core package:

- [Core package map](./components-core/README.md)
- [Scene](./components-core/scene-README.md)
- [Canvas pin board](./components-core/canvas-pin-board-README.md)
- [Component events](./components-core/events-README.md)
- [Context drag and canvas ingress](./components-core/context-drag-README.md)
- [Chat engine](./components-core/chat-engine-README.md)
- [Host event bus](./components-core/host-event-bus-README.md)
- [Engine config](./components-core/engine-config-README.md)

React package:

- [React package map](./components-react/README.md)

Runtime/widget integration:

- [Widget integration](./widget-integration-README.md)

## Concept Map

```text
scene
  mounts components
  connects runtimes
  routes surface commands
  brokers cross-iframe context drag
  owns Event Bus/SSE transport per runtime

events
  lets components claim event interests
  hides whether transport is scene, direct Event Bus, or another host

canvas pin board
  stores proxy cards, not provider objects
  accepts kdcube.canvas.ingress
  preserves opaque object_ref
  delegates actions to provider resolvers

chat engine
  owns chat state/transport/conversation lifecycle
  emits host events instead of reaching into window.parent
  attaches/removes context refs

React bindings
  make the same core contracts ergonomic in React
```

## Naming Rules

- Use **app** in prose. Say `bundles.yaml` only when referring to the current
  internal backend config key.
- Use **scene** for the host composition/runtime surface.
- Use **pin board** for the user-facing canvas workspace; use **canvas** when
  referring to protocol/module names.
- Use **context drag** for moving object refs between surfaces.
- Use **canvas ingress** for `kdcube.canvas.ingress`.
- Use **host event bus** for chat-engine outbound events to a host.

## Package Exports

`@kdcube/components-core` exports:

| Export | Purpose |
| --- | --- |
| `@kdcube/components-core` | shared config/types, events, and scene helpers |
| `@kdcube/components-core/chat` | framework-agnostic chat engine |
| `@kdcube/components-core/scene` | scene runtime, surface registry, drop targets, host drag helpers |
| `@kdcube/components-core/events` | event claims/client and scene transport |
| `@kdcube/components-core/canvas` | canvas model, context types, ingress, ids |

`@kdcube/components-react` exports:

| Export | Purpose |
| --- | --- |
| `@kdcube/components-react` | shared React exports and `CanvasBoard` |
| `@kdcube/components-react/chat` | chat provider, hooks, default UI |
| `@kdcube/components-react/markdown` | shared Markdown message renderer |
| `@kdcube/components-react/canvas` | reusable React canvas/pin-board component |

## Verification

```sh
cd app/ai-app/src/kdcube-ai-app/npm/packages/components-core
npm run typecheck
npm run build
npm run test:events
npm run test:scene

cd ../components-react
npm run typecheck
npm run build
```

## Related Platform Docs

- `docs/sdk/solutions/scene/config/README.md`
- `docs/sdk/solutions/scene/cross-surface-context-drag-README.md`
- `docs/sdk/solutions/canvas/pin-integration-README.md`
- `docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md`
- `docs/sdk/namespace-services/README.md`

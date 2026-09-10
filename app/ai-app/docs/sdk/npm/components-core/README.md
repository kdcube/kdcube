---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/README.md
title: "@kdcube/components-core"
summary: "Headless package for shared KDCube component contracts: chat engine, scene runtime, event claims/client, canvas pin-board model/ingress, and host-facing event/config types."
status: implementation
tags: ["sdk", "npm", "components-core", "headless", "scene", "events", "canvas", "chat"]
updated_at: 2026-06-23
keywords:
  [
    "@kdcube/components-core",
    "scene runtime",
    "event claims",
    "canvas ingress",
    "chat engine",
    "headless package",
  ]
---

# `@kdcube/components-core`

`@kdcube/components-core` is the framework-free contract layer. It has no React
dependency and no assumption that the host is an iframe, website, or KDCube
widget. It defines the protocols and small runtimes that let app surfaces
interoperate.

## Exports

| Import | What it provides |
| --- | --- |
| `@kdcube/components-core` | shared config/types plus root re-exports for events and scene |
| `@kdcube/components-core/chat` | `createChatEngine`, chat state, transport, context helpers |
| `@kdcube/components-core/scene` | scene runtime, surface registry, drop targets, host drag helpers |
| `@kdcube/components-core/events` | component event claims/client and scene transport |
| `@kdcube/components-core/canvas` | canvas model, context types, ids, ingress packet helpers |

## Documents

- [Scene](./scene-README.md)
- [Session](./session-README.md): the browser side of one server-held platform session (probe, sign-in redirect, sign-out, `kdcube-auth-changed`).
- [Canvas pin board](./canvas-pin-board-README.md)
- [Component events](./events-README.md)
- [Context drag and canvas ingress](./context-drag-README.md)
- [Chat engine](./chat-engine-README.md)
- [Host event bus](./host-event-bus-README.md)
- [Engine config](./engine-config-README.md)

## Boundary

Core may normalize transport envelopes and route by configured target surfaces.
It must not learn provider object semantics.

```text
allowed in core:
  parse kdcube.canvas.ingress shape
  normalize context drag refs
  call injected objectAction(open)
  maintain scene drop-target config
  expose event claim client

not allowed in core:
  decide what mem:/task:/conv: means
  infer open/download behavior from kind
  fetch namespace presentation by itself
  declare component subscriptions for a widget
```

Namespace presentation, object actions, and ReAct rendering are provider/app
responsibilities. Core carries refs and calls the configured resolver paths.

## Verification

```sh
cd app/ai-app/src/kdcube-ai-app/npm/packages/components-core
npm run typecheck
npm run build
npm run test:events
npm run test:scene
```

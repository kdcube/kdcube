---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/README.md
title: "KDCube Components Library (npm)"
summary: "Index and map for the two-package components library that decouples KDCube's interactive UI from any framework: @kdcube/components-core (headless engines) and @kdcube/components-react (React bindings). Start here, then follow the per-package and per-concept docs."
status: design
tags: ["sdk", "npm", "components", "components-core", "components-react", "headless", "index"]
updated_at: 2026-06-16
keywords:
  [
    "@kdcube/components-core",
    "@kdcube/components-react",
    "kdcube components library",
    "headless chat engine",
    "framework-agnostic UI components",
    "npm packages index",
  ]
---

# KDCube Components Library (npm)

A two-package library that decouples KDCube's interactive components (chat today;
canvas, memories, … next) from any single framework or host. Workspace:
`app/ai-app/src/kdcube-ai-app/npm/` (inside the installed app tree, so the package
source ships into the runtime image and is reachable via the `npm://` shared-source
resolver).

| Package | Layer | Knows about |
| --- | --- | --- |
| **`@kdcube/components-core`** | headless engines — state machine + transport + protocol + host event bus | nothing UI: no React, no DOM host, no iframe |
| **`@kdcube/components-react`** | React bindings — provider + hooks (+ optional default UI) | React only |

Each package is **multi-component** via subpath exports (`…/chat` today; `…/canvas`,
`…/scene` next), so a consumer installs two packages and imports only what it needs.
Future adapters (`@kdcube/components-angular`, `…-vanilla`) slot in with no core change.

## Read in order

1. **Core package** — [`components-core/README.md`](./components-core/README.md)
   - [Chat engine](./components-core/chat-engine-README.md) — `createChatEngine(config)` controller API.
   - [Engine config & auth](./components-core/engine-config-README.md) — `EngineConfig`, cookie/token, `EngineRuntime`.
   - [Host event bus](./components-core/host-event-bus-README.md) — the events the engine bubbles to the host.
   - [Context-pin contract](./components-core/context-pin-contract-README.md) — the cross-component drag contract.
2. **React package** — [`components-react/README.md`](./components-react/README.md) — `ChatStoreProvider` + hooks.

## How it's consumed (three stories)

- **Our bundles** — `sdk://solutions/chat/ui/widget` becomes a thin consumer of
  `@kdcube/components-react/chat` + a scene/iframe host-bridge adapter. (In progress.)
- **External React, no iframe** — install both packages, wrap with
  `<ChatStoreProvider config>`, render your own UI, handle bubbled events.
- **Non-React host** — install `@kdcube/components-core`, `createChatEngine(config)`,
  `subscribe(render)` + call methods.

The in-tree widget these packages derive from stays the reference until each consumer
switches: `docs/sdk/solutions/chat/chat-widget-solution-README.md`.

## Status

- [x] Workspace + both packages; `/chat` subpath exports; tsup build.
- [x] Chat engine ported into `@kdcube/components-core/chat`.
- [x] React bindings in `@kdcube/components-react/chat`.
- [x] Context-pin contract in `@kdcube/components-core`.
- [ ] Repoint the SDK widget at the packages + iframe host-bridge.
- [ ] Publish / workspace-link for external consumers.

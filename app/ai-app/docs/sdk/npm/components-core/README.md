---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/README.md
title: "@kdcube/components-core"
summary: "The headless, framework-agnostic package: per-component engines (chat today), the host event bus, the engine config/auth contract, and the cross-component context-pin contract. Redux is an internal detail; the public API is a vanilla controller."
status: design
tags: ["sdk", "npm", "components-core", "headless", "vanilla-controller", "redux"]
updated_at: 2026-06-16
keywords:
  [
    "@kdcube/components-core",
    "createChatEngine",
    "vanilla controller",
    "headless engine",
    "subpath exports",
    "redux internal",
  ]
---

# `@kdcube/components-core`

The headless layer. No React, no DOM host, no iframe assumptions. Depends only on
`@reduxjs/toolkit` and `socket.io-client`.

## The vanilla-controller principle

Redux Toolkit stays **inside** the package as the state container (reducers +
devtools). The **public** API is a small controller — not "Redux you must learn":

```ts
const engine = createChatEngine({ connection: { baseUrl, tenant, project, bundleId } })
engine.subscribe(render)          // state changes
engine.send('hello')              // methods
engine.on('unauthorized', login)  // host event bus
engine.dispose()
```

React binds to it (`@kdcube/components-react`), Angular wraps it in a service, plain
JS calls it directly — none need to know Redux.

## Subpath exports

| Import | What |
| --- | --- |
| `@kdcube/components-core` | shared: host event bus, `EngineConfig`/auth, the context-pin contract |
| `@kdcube/components-core/chat` | the chat engine: `createChatEngine`, protocol types, store/slice/reducers, transport |
| `@kdcube/components-core/canvas` | (next) |

## Concepts

- [Chat engine](./chat-engine-README.md) — `createChatEngine(config)`: the controller
  surface (send/steer/loadConversation/…), state + status access, lifecycle.
- [Engine config & auth](./engine-config-README.md) — `EngineConfig` (connection +
  cookie/token auth) and `EngineRuntime`; the `settings`-singleton replacement.
- [Host event bus](./host-event-bus-README.md) — the typed events the engine bubbles
  (`unauthorized`, `object-open`, `view-change`, …) so any host reacts.
- [Context-pin contract](./context-pin-contract-README.md) — `ContextItem` +
  `buildContextDrag` / `parseContextDrop`: one shape for "a draggable object ref".

## Build / verify

```sh
cd app/ai-app/src/kdcube-ai-app/npm && npm install
cd packages/components-core && npx tsc --noEmit && npx tsup   # typecheck + build dist
```

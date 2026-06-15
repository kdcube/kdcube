---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/host-event-bus-README.md
title: "Host Event Bus"
summary: "The decoupling seam: a typed event emitter (engine.on(...)) the engine uses to bubble server- and component-originated control events (unauthorized, object-open, view-change, pin-conversation, canvas-patch, context-removed, …) to ANY host, instead of reaching for window.parent.postMessage. Replaces the widget's host.ts."
status: design
tags: ["sdk", "npm", "components-core", "events", "host", "postMessage", "decoupling"]
updated_at: 2026-06-16
keywords:
  [
    "host event bus",
    "engine.on",
    "unauthorized object-open view-change",
    "pin-conversation canvas-patch context-removed",
    "HostEventMap",
    "postMessage replacement",
  ]
---

# Host Event Bus

The engine never reaches for `window.parent.postMessage`, a router, or a login modal
directly. It **emits typed events**; the host subscribes via `engine.on(...)` and
decides what to do. The iframe/scene widget maps these to `postMessage`; a website
maps them to its own handlers. Same contract, different transport — this is what
replaces the widget's `host.ts` and makes "any host" work.

```ts
const off = engine.on('unauthorized', () => showLogin())
// …
off()  // unsubscribe
```

## Events (`HostEventMap`)

| Event | Payload | Replaces (widget) | Host does |
| --- | --- | --- | --- |
| `unauthorized` | `{ status?, reason? }` | `requestAuthRequired()` | show login |
| `object-open` | `{ ref }` | `kdcube-object-open` postMessage | open the referenced surface |
| `view-change` | `{ view }` | `requestHostView()` | resize/overlay, or ignore |
| `pin-conversation` | `{ conversationId, title?, ref? }` | `kdcube-pin-conversation` | pin to a board/canvas |
| `canvas-patch` | `{ event }` | canvas-patch postMessage forward | forward to a board |
| `context-removed` | `{ ids }` | context-remove postMessage | sync the source surface |
| `service-notice` | `{ text, tone, kind? }` | in-widget banner | optionally react (e.g. billing) |
| `connection` / `ready` / `error` | — | internal state | observe/log |

The map is the single source of truth — every new host-actionable signal gets a key
here so all adapters stay type-checked.

## Inbound direction

Events are **outbound** (engine → host). The reverse — host telling the engine what to
do — is plain method calls: `loadConversation`, `attachContext`, `removeContext`,
`setHostView`, `refreshAuth`. An iframe host-adapter wires both directions:
`engine.on(...)` → `postMessage`, and inbound `postMessage` → engine methods.

## Auth & events together

Login is external (see [engine config & auth](./engine-config-README.md)); the engine
only signals. On a 401/403 it emits `unauthorized` — the host shows login, then calls
`engine.refreshAuth()`.

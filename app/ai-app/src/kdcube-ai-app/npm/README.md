# KDCube client components library

A two-package library that decouples KDCube's interactive components (chat, and
later canvas/memories/usage) from any single framework or host:

| Package | Layer | Depends on | Knows about |
|---|---|---|---|
| **`@kdcube/components-core`** | headless engines — state machine + transport + protocol + host event bus | `@reduxjs/toolkit`, `socket.io-client` | nothing UI: no React, no DOM host, no iframe |
| **`@kdcube/components-react`** | React bindings — provider + hooks (+ optional default UI) | `@kdcube/components-core`, `react`, `react-redux` | React only |

Future framework adapters slot in with no core change:
`@kdcube/components-angular`, `@kdcube/components-vanilla`.

Each package is **multi-component** via subpath exports — `…/chat` today,
`…/canvas` next — so a consumer installs two packages and imports only the
component it needs:

```ts
import { createChatEngine } from '@kdcube/components-core/chat'   // any framework
import { ChatStoreProvider, useChatEngine } from '@kdcube/components-react/chat' // React
```

## Why a vanilla controller (not "Redux you must learn")

Redux Toolkit stays *inside* `components-core` as the state container (the
existing reducers + devtools). The **public** API is a small controller:

```ts
const engine = createChatEngine({ connection: { baseUrl, tenant, project, bundleId } })
engine.subscribe(render)          // state changes
engine.send('hello')              // methods
engine.on('unauthorized', login)  // host event bus
engine.dispose()
```

React binds to `engine` via `useChatEngine()` / `useChatState()`; Angular wraps
it in a service; plain JS calls it directly. None of them need to know Redux.

## Auth & the host event bus — the decoupling seam

**Login is not a component concern.** The host authenticates elsewhere; the
engine only carries credentials and *bubbles* what the server says:

- `auth.mode: 'cookie'` (default) — requests use `credentials: 'include'`; the
  host's externally-set session cookie rides along.
- `auth.mode: 'token'` — host supplies `getAccessToken()` / `getIdToken()`
  callbacks (re-read per request, so token refresh is transparent).

The engine never opens a login modal, calls a router, or touches
`window.parent.postMessage`. It **emits typed events** the host handles:

| Event | Replaces today's widget call | Host does |
|---|---|---|
| `unauthorized` | `requestAuthRequired()` postMessage | show login |
| `object-open` | `kdcube-object-open` postMessage | open the referenced surface |
| `view-change` | `requestHostView()` postMessage | resize/overlay, or ignore |
| `pin-conversation` | `kdcube-pin-conversation` postMessage | pin to a board/canvas |
| `service-notice` | in-widget banner | optionally react (e.g. open billing) |
| `connection` / `ready` / `error` | internal state | observe/log |

The iframe/scene widget maps these to/from `postMessage`; a website maps them to
its own handlers. **Same contract, different transport** — that's what makes
"any host" work.

## The three consumption stories

**1) Our own bundles.** `sdk://solutions/chat/ui/widget` becomes a thin consumer
of `@kdcube/components-react/chat` (engine + default UI) plus a small
**scene/iframe host-bridge adapter** that maps `engine.on(...)` ⇄ `postMessage`.
The current iframe behaviour is just one host adapter on top of the bus.

**2) External React app, no iframe (e.g. the landing page).**
```tsx
import { ChatStoreProvider, useChatEngine } from '@kdcube/components-react/chat'
<ChatStoreProvider config={{ connection: { baseUrl, tenant, project, bundleId } }}>
  <MyOwnStyledChat />   {/* your components, your CSS — no iframe */}
</ChatStoreProvider>
```
Handle `engine.on('unauthorized', …)` with your own login.

**3) Non-React host (Angular/Vue/plain JS).**
```ts
import { createChatEngine } from '@kdcube/components-core/chat'
const engine = createChatEngine(config)
engine.subscribe(render); engine.on('object-open', open)
```

## Layout

```
app/ai-app/src/npm/
  package.json                       # npm workspaces root
  packages/
    components-core/                 # @kdcube/components-core
      src/shared/                    #   event bus + config/auth (cross-component)
      src/chat/                      #   createChatEngine + chat types
    components-react/                # @kdcube/components-react
      src/chat/                      #   ChatStoreProvider + useChatEngine + useChatState
```

## Status

- [x] Workspace + both package manifests, `/chat` subpath exports, build config.
- [x] Locked contracts: host event bus, `EngineConfig` (cookie/token auth),
      `ChatEngine` interface; React bindings.
- [x] **Chat engine ported** into `@kdcube/components-core/chat`: protocol types,
      `EngineRuntime` (cookie/token auth replaces the `settings` singleton),
      transport (HTTP/SSE/Socket.IO), the full reducer state machine + RTK slice +
      per-engine store factory, and `createChatEngine(config)` — the orchestration
      from the widget's `useChatEngine.tsx` minus React, with `host.ts` postMessage
      replaced by the host event bus. Typecheck + build + runtime smoke tests pass.
- [x] **React bindings** in `@kdcube/components-react/chat`: `ChatStoreProvider`,
      `useChatEngine`, `useChatState`, `useChatStatus` (SSR-render verified).
- [ ] Repoint the SDK widget at the packages + implement the iframe host-bridge
      adapter (engine events ⇄ `postMessage`), then a full parity pass.
- [ ] Publish (or workspace-link) for external consumers.

Build/verify loop: `cd app/ai-app/src/npm && npm install`, then per package
`npx tsc --noEmit` (typecheck) and `npx tsup` (build dist).

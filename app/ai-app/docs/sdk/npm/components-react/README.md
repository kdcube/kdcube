---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-react/README.md
title: "@kdcube/components-react"
summary: "React bindings and reusable UI over @kdcube/components-core: chat provider/hooks/default UI and the CanvasBoard pin-board component."
status: implementation
tags: ["sdk", "npm", "components-react", "react", "hooks", "provider", "chat", "canvas"]
updated_at: 2026-06-23
keywords:
  [
    "@kdcube/components-react",
    "ChatStoreProvider",
    "Chat",
    "CanvasBoard",
    "useChatEngine",
    "useChatState",
  ]
---

# `@kdcube/components-react`

`@kdcube/components-react` is the React layer over
[`@kdcube/components-core`](../components-core/README.md). It should stay thin:
core owns contracts and runtime behavior; React owns providers, hooks, rendering,
and DOM event wiring.

## Exports

| Export | Purpose |
| --- | --- |
| `@kdcube/components-react` | Shared React export surface. |
| `@kdcube/components-react/chat` | `ChatStoreProvider`, hooks, `Chat`, `ChatShell`, `useChatViewModel`. |
| `@kdcube/components-react/canvas` | `CanvasBoard` plus re-exported core canvas types/helpers. |
| `@kdcube/components-react/scene` | Scene-host shell: component registry, host plumbing, rail/windows, external-panel surface routing (`externalPanelSurfaceRegistrations`). Import `sceneHost.css` for the skin. |

## Chat

```tsx
import {
  Chat,
  ChatStoreProvider,
  useChatEngine,
  useChatState,
  useChatStatus,
} from '@kdcube/components-react/chat'

function App() {
  return (
    <ChatStoreProvider config={{ connection: { baseUrl, tenant, project, bundleId } }}>
      <Chat namespaceStyles={namespaceStyles} />
    </ChatStoreProvider>
  )
}
```

The provider creates one core chat engine instance and exposes its Redux store.
Multiple providers mean multiple isolated chat engines.

| Export | Purpose |
| --- | --- |
| `ChatStoreProvider` | Creates/disposes a `createChatEngine(config)` instance and wraps children in a Redux provider. |
| `useChatEngine()` | Returns the controller: methods plus host event bus. |
| `useChatState(selector?)` | Subscribes to Redux chat state. |
| `useChatStatus(selector?)` | Subscribes to engine status outside Redux. |
| `Chat` / `ChatShell` | Reference React chat UI over the engine view model. |

`bundleId` is the current field name in the TypeScript config and backend API. In
app-builder language, treat it as the app id/version.

## Canvas

```tsx
import { CanvasBoard } from '@kdcube/components-react/canvas'
```

`CanvasBoard` is the reusable React pin-board component. It renders boards and
cards, but the host supplies every stateful or ecosystem-facing operation:

- `readCanvas` / `patchCanvas`
- `onDropFiles`, `onDropText`, `onDropContext`, `onDropIngress`
- `onObjectAction`
- optional `onSearchPins`
- optional scene-brokered drop callbacks
- `namespaceStyles` from provider/scene presentation config

`CanvasBoard` must not decide that `mem:*`, `task:*`, `conv:*`, or any future
namespace opens in a specific place. It shows card metadata and delegates object
actions to the supplied resolver callback.

## Namespace Presentation

`namespaceStyles` is cosmetic presentation supplied by the app/scene runtime, usually
from the same provider presentation config used by other surfaces:

```tsx
<Chat namespaceStyles={namespaceStyles} />
<CanvasBoard namespaceStyles={namespaceStyles} {...boardProps} />
```

The map can be keyed by provider namespace or scoped object kind. It may control
label, color, background, border, focus, and icon metadata. It must not control
routing or object behavior.

## Build

```sh
cd app/ai-app/src/kdcube-ai-app/npm/packages/components-react
npm run typecheck
npm run build
```


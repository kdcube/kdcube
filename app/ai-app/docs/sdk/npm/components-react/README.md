---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-react/README.md
title: "@kdcube/components-react"
summary: "The React bindings package: thin adapters over @kdcube/components-core. @kdcube/components-react/chat exports ChatStoreProvider (owns one engine + provides its RTK store via react-redux), useChatEngine, useChatState, and useChatStatus. All behaviour lives in the core; this is React idiom only."
status: implementation
tags: ["sdk", "npm", "components-react", "react", "hooks", "provider", "chat"]
updated_at: 2026-06-16
keywords:
  [
    "@kdcube/components-react",
    "ChatStoreProvider",
    "useChatEngine",
    "useChatState",
    "useChatStatus",
    "react-redux provider",
  ]
---

# `@kdcube/components-react`

Thin React adapters over `@kdcube/components-core`. Most behaviour lives in core or
the component's explicit host callbacks; this package provides React providers,
hooks, and React-hosted components. Peer deps: `react`, `react-dom`.

## `@kdcube/components-react/chat`

```tsx
import {
  ChatStoreProvider, useChatEngine, useChatState, useChatStatus,
} from '@kdcube/components-react/chat'

function App() {
  return (
    <ChatStoreProvider config={{ connection: { baseUrl, tenant, project, bundleId } }}>
      <MyChatUI />
    </ChatStoreProvider>
  )
}

function MyChatUI() {
  const engine = useChatEngine()
  const turns  = useChatState(s => s.turns)
  const { authed, hostView } = useChatStatus()
  useEffect(() => engine.on('unauthorized', showLogin), [engine])
  return <button onClick={() => engine.send('hi')}>send</button>
}
```

| Export | What |
| --- | --- |
| `ChatStoreProvider` | Creates one `createChatEngine(config)` per instance, disposes on unmount, and wraps children in a react-redux `<Provider store={engine.store}>`. Multiple providers per page = multiple isolated chats. |
| `useChatEngine()` | The controller — methods + the event bus. Throws outside a provider. |
| `useChatState(selector?)` | Subscribe to the Redux `ChatState` with an optional selector. |
| `useChatStatus(selector?)` | Subscribe to engine status (`ready`/`authed`/`bootError`/`hostView`/`dryRun`). |

Everything the engine can do (config, the controller surface, the event bus) is in the
core docs: [`../components-core/README.md`](../components-core/README.md).

## `@kdcube/components-react/canvas`

```tsx
import { CanvasBoard } from '@kdcube/components-react/canvas'
```

`CanvasBoard` is the reusable React board component used by the standalone
pinboard widget and the versatile scene. Hosts provide the storage/operation
callbacks:

- `patchCanvas`, `readCanvas`
- `onDropFiles`, `onDropText`, `onDropContext`, `onDropIngress`
- `onObjectAction`
- optional `getBrokeredDrop` / `onBrokeredDropHandled` for scene-brokered drops
- `namespaceStyles` from the app/scene namespace presentation config

The component does not fetch namespace colors or resolve provider objects by
itself. Those remain host/runtime responsibilities.

## Namespace Styles

The default chat shell accepts `namespaceStyles`, the same app-level namespace
presentation map used by other scene surfaces:

```tsx
<Chat
  namespaceStyles={{
    mem: { label: 'Memory', color: '#159947', background: '#eaf8ef' },
    task: { label: 'Task', color: '#2563eb', background: '#eff6ff' },
  }}
/>
```

The map is keyed by root namespace. Chat applies it to context chips, named
service search results, composer attachments, and turn overview/follow-up
rendering through the core helpers. The package does not fetch this map from an
iframe or from canvas; the host/app runtime supplies it from the same config that
other mounted surfaces receive.

## Build / verify

```sh
cd app/ai-app/src/kdcube-ai-app/npm/packages/components-react && npx tsc --noEmit && npx tsup
```

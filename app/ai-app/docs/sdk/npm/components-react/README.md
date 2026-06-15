---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-react/README.md
title: "@kdcube/components-react"
summary: "The React bindings package: thin adapters over @kdcube/components-core. @kdcube/components-react/chat exports ChatStoreProvider (owns one engine + provides its RTK store via react-redux), useChatEngine, useChatState, and useChatStatus. All behaviour lives in the core; this is React idiom only."
status: design
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

Thin React adapters over `@kdcube/components-core`. No behaviour lives here — only the
React idiom: a provider that owns one engine instance (and provides its RTK store via
react-redux so view components can `useSelector`), plus hooks to reach the controller
and subscribe to its state/status. Peer deps: `react`, `react-dom`.

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

## Build / verify

```sh
cd app/ai-app/src/npm/packages/components-react && npx tsc --noEmit && npx tsup
```

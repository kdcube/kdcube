---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
title: "Chat Engine (createChatEngine)"
summary: "The framework-agnostic chat controller from @kdcube/components-core/chat: construct with EngineConfig, drive chat via methods (send/steer/loadConversation/…), read state via getState/subscribe and engine status via getStatus/subscribeStatus, and react to bubbled host events. The widget's useChatEngine orchestration minus React."
status: design
tags: ["sdk", "npm", "components-core", "chat", "createChatEngine", "controller", "headless"]
updated_at: 2026-06-16
keywords:
  [
    "createChatEngine",
    "ChatEngine",
    "headless chat controller",
    "send steer loadConversation",
    "getStatus subscribeStatus",
    "chat state machine",
  ]
---

# Chat Engine — `createChatEngine`

```ts
import { createChatEngine } from '@kdcube/components-core/chat'

const engine = createChatEngine({
  connection: { baseUrl, tenant, project, bundleId },
})
```

A vanilla controller that owns the RTK store + transport + orchestration lifted from
the widget's `useChatEngine.tsx` — the send-queue, reconnect, conversation lifecycle,
and service-event handling are unchanged. What's different from the widget: the
`settings` singleton is replaced by the injected config (see
[engine-config](./engine-config-README.md)), and `host.ts` postMessage is replaced by
the [host event bus](./host-event-bus-README.md). The engine boots on creation and
re-resolves auth via `refreshAuth()`.

## Controller surface

```ts
interface ChatEngine {
  readonly store: ChatStore             // RTK store (react-redux binds to it)
  readonly bundleId: string

  getState(): ChatState                 // the Redux chat state
  subscribe(listener: () => void): () => void
  getStatus(): ChatEngineStatus         // engine-level: ready/authed/bootError/hostView/dryRun
  subscribeStatus(listener: () => void): () => void
  on(event, handler): () => void        // host event bus

  refreshAuth(): void                   // host calls after an external login change

  send(text?, eventType?): void
  steer(): void
  loadConversation(id): void
  newChat(): void
  deleteConversation(conversation): void // host confirms first; engine does not prompt
  refreshConversations(): void

  attachContext(items): void
  removeContext(ids): void
  openContextChip(context): void        // resolves capabilities → emits 'object-open' or downloads
  downloadFile(ref, filename?, mime?): void
  submitFeedback(turnId, reaction, text?): void

  handleReconnect(): void
  pinConversationToCanvas(): void       // emits 'pin-conversation'
  promptLogin(): void                   // emits 'unauthorized'
  setHostView(next): void               // emits 'view-change'
  setDryRunEnabled(value): void
  clearDryRunPreview(): void

  dispose(): void
}
```

## State vs. status

- **`getState()` / `subscribe()`** — the Redux `ChatState` (turns, banners, composer,
  conversations, connection, feedback). Use selectors in React via `useChatState`.
- **`getStatus()` / `subscribeStatus()`** — engine-level state that is *not* in Redux:
  `{ ready, authed, bootError, hostView, dryRun }`.

## Multi-instance

Each `createChatEngine` builds its own store (a factory, not a module singleton), so
a page can host more than one chat.

## Host responsibilities

The engine has no `window` listeners and shows no login/confirm UI. The host:

- calls methods in response to its own input (`loadConversation`, `attachContext`,
  `removeContext`, `setHostView`, `refreshAuth`);
- reacts to [bubbled events](./host-event-bus-README.md) (`unauthorized`, `object-open`,
  `pin-conversation`, `canvas-patch`, …);
- confirms destructive actions (e.g. before `deleteConversation`).

React hosts get all of this wrapped by [`@kdcube/components-react/chat`](../components-react/README.md).

---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/npm/components-core/chat-engine-README.md
title: "Chat Engine"
summary: "The framework-agnostic chat controller from @kdcube/components-core/chat: state, transport, conversation lifecycle, scoped capability drafts with explicit save, context chips, and host events without React or iframe coupling."
status: implementation
tags: ["sdk", "npm", "components-core", "chat", "createChatEngine", "controller", "headless"]
updated_at: 2026-07-12
keywords:
  [
    "createChatEngine",
    "ChatEngine",
    "headless chat controller",
    "send steer loadConversation",
    "host event bus",
    "chat state machine",
    "conversation scoped capabilities",
    "saveAgentSelectionChanges",
  ]
---

# Chat Engine

`@kdcube/components-core/chat` exports `createChatEngine(config)`, a headless
controller for chat state, transport, conversation lifecycle, context chips, and
host events.

```ts
import { createChatEngine } from '@kdcube/components-core/chat'

const engine = createChatEngine({
  connection: { baseUrl, tenant, project, bundleId },
  agentId: 'main', // optional; default 'main'
})
```

`bundleId` is the current TypeScript/API field name. In product-facing docs this
means the app id/version the chat engine talks to. `agentId` names the app's
configured agent this engine drives: it rides every message target and event
batch and scopes the per-user capability selection operations.

The engine owns no DOM, login UI, iframe bridge, or router. It emits host events
and the host decides how to render, route, authenticate, and compose surfaces.

## Controller Surface

```ts
interface ChatEngine {
  readonly store: ChatStore
  readonly bundleId: string
  readonly agentId: string

  getState(): ChatState
  subscribe(listener: () => void): () => void
  getStatus(): ChatEngineStatus
  subscribeStatus(listener: () => void): () => void
  on(event, handler): () => void

  refreshAuth(): void

  send(text?, eventType?): void
  steer(): void
  loadConversation(id): void
  newChat(): void
  deleteConversation(conversation): void
  refreshConversations(): void

  attachContext(items): void
  removeContext(ids, opts?): void
  openContextChip(context): void
  downloadFile(ref, filename?, mime?): void
  submitFeedback(turnId, reaction, text?): void

  handleReconnect(): void
  pinConversationToCanvas(): void
  promptLogin(): void
  setHostView(next, opts?): void
  setBootError(value): void
  setDryRunEnabled(value): void
  clearDryRunPreview(): void

  loadAgentCapabilities(opts?): void
  updateAgentSelection(patch): void
  saveAgentSelectionChanges(): void
  submitAgentSelectionDecision(patch, options?): void
  openConnections(source?): void
  hasHostHandler(event): boolean

  dispose(): void
}
```

## Conversation-Scoped Agent Capabilities

The engine owns the client side of the conversation selection layer (semantics and
config live in
[How To Construct A ReAct Agent](../../agents/react/how/how-to-construct-react-agent-README.md)):

- `state.capabilities` is a Redux branch: `status` (lazy: `idle` until the
  first `loadAgentCapabilities()` call), the loaded `inventory`
  (tool groups, MCP servers, namespaces, skills, `supported_models` +
  `default_model`), the conversation's `disabled` deny-list and `model` pick,
  plus `dirty`, `saving`, and `saveError`.
- Opening the picker mints a conversation id when a new chat does not yet have
  one. Reads and writes carry that id.
- `updateAgentSelection(patch)` applies the toggle to a local draft and
  coalesces pending patches. It does not persist anything.
- `saveAgentSelectionChanges()` sends one explicit
  `agent_selection_update(conversation_id, patch)` merge-write. The server's
  clamped record reconciles the state on response.
- Switching conversations discards an unsaved draft and loads the target
  conversation's selection. Late load/save responses are ignored if their
  conversation is no longer active.
- `capabilities.open` carries the active conversation id when chat asks a
  scene host to open the full-page picker, preserving the same persistence
  scope across the presentation change.
- `submitAgentSelectionDecision(patch, {apply, cachePolicy})` is the confirm
  picker's explicit cache-cost decision, `apply` = `now` |
  `next_conversation` | `when_cold` (deferred modes park the change as
  `state.capabilities.pending`), `cachePolicy` persists the user's standing
  per-class cold-cache policy. `state.capabilities.cachePolicy` carries the
  effective policy + admin bounds.
- `send()` never saves a capabilities draft implicitly. Only **Save changes**
  or an explicit cache-policy decision writes it.
- `openConnections()` emits the `open-connections` host event;
  `hasHostHandler('open-connections')` tells UI whether the host wired it —
  the composer menu hides its connections row otherwise.

## State vs Status

- `getState()` / `subscribe()` expose Redux `ChatState`: turns, composer,
  banners, conversations, attached context, feedback, and connection state.
- `getStatus()` / `subscribeStatus()` expose engine-level state outside Redux:
  boot readiness, auth state, host view, and dry-run preview status.

React hosts normally consume this through
[`@kdcube/components-react/chat`](../components-react/README.md).

## Host Boundary

The engine does not call `window.parent.postMessage`. It emits typed events on the
[host event bus](./host-event-bus-README.md):

```ts
engine.on('unauthorized', () => showLogin())
engine.on('object-open', ({ ref }) => openInScene(ref))
engine.on('pin-conversation', (payload) => pinConversation(payload))
```

The reverse direction is method calls:

```ts
engine.attachContext(contexts)
engine.loadConversation(conversationId)
engine.refreshAuth()
```

## Context

Chat treats attached context as object refs plus display/provenance metadata. It
does not own provider objects. Opening a context chip asks the host/resolver path
to decide what the object can do.

## Multi-Instance

Each `createChatEngine` call creates its own store and transport state. A page can
host multiple chat engines when it deliberately wants independent chat instances.

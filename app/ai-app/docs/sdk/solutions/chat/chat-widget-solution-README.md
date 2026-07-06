---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
title: "Chat Widget Solution"
summary: "How to mount the reusable SDK chat widget in an app, configure its event-source profile, and reuse its headless engine (useChatEngine + ChatStoreProvider) to skin the chat with a custom UI or drive the backend from an external client."
status: draft
tags: ["sdk", "solutions", "chat", "widget", "bundle", "react", "external-events", "headless", "useChatEngine"]
updated_at: 2026-06-23
keywords:
  [
    "sdk chat widget",
    "chat_widget_ui_config",
    "sdk://solutions/chat/ui/widget",
    "chat event source profile",
    "bundle chat widget",
    "reusable chat component",
    "context chip object actions",
    "object action facade",
    "useChatEngine",
    "ChatStoreProvider",
    "headless chat engine",
    "custom chat UI",
    "reuse chat backend",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/ecosystem-component/components-ecosystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-component-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/cross-surface-context-drag-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/memory-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
---
# Chat Widget Solution

The SDK chat solution provides a reusable React widget source and a small Python
mount helper. A bundle can mount the widget without copying UI code.

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chat import chat_widget_ui_config

config = {
    "ui": {
        "widgets": {
            "my_chat": chat_widget_ui_config(),
        },
    },
}
```

The widget source is:

```text
sdk://solutions/chat/ui/widget
```

## What This Component Owns

| Layer | Owned by the chat solution |
| --- | --- |
| Widget UI | Chat transcript, composer, files, context chips, context-chip open/download activation, dry-run preview, reconnect control, compact/expanded host view messages. |
| Event packaging | Converts user prompt, attachments, and attached context chips into `external_events[]`. |
| Host iframe contract | Requests host view changes, auth prompts, and object-open routing through `postMessage`. |
| Mount helper | `chat_widget_ui_config()` returns the standard bundle widget config. |

The bundle still owns the assistant runtime, tools, resolvers, policies, and
visibility rules. The chat widget sends events to the bundle; it does not decide
how the agent interprets every domain object.

## Namespace Presentation

Namespaced context chips and search-result chips use the app's namespace
presentation map. In a scene, the scene host fetches
`public/namespace_presentation_config` and passes `namespace_styles` /
`namespaceStyles` in the widget config handshake. When the chat widget is
mounted without a scene host, it can fetch the same public endpoint directly as
a fallback.

The same map is used by the scene drag overlay and canvas pins, so a `mem:*`,
`task:*`, `conv:fi:*`, or `cnv:*` ref keeps the same color across chat, canvas, and
drop targets. See
[Scene Composition](../scene/scene-composition-README.md#namespace-presentation-config).

### Send eligibility and anonymous visitors

- A draft is sendable when it has **any** of typed text, attachments, or
  attached context chips — context-only or attachment-only sends are allowed.
  The same predicate guards both the Send button and the ⌘/Ctrl+Enter path.
- The widget fetches the profile from the server. A non-OK `/profile` response
  (e.g. an anonymous visitor) is treated as an **anonymous identity**, not an
  error: the composer shows no profile-fetch error banner. Identity-gated host
  affordances stay hidden until a signed-in profile is confirmed.

## The composer "+" menu (per-user agent customization)

The composer bar's "+" button opens a per-user, per-agent menu for signed-in
users: pick the model (when the app declares `supported_models`), and toggle
the agent's skills, tool groups (with per-tool rows), MCP servers, and
named-service namespaces — always a subset of what the app config grants, with
system tool groups locked on. Toggles save optimistically and apply from the
next message; cache-costly changes (a model switch, tool/skill toggles) show
an inline cost notice inside the menu, and under the user's `confirm` policy
the notice becomes an inline choice (apply now / from next conversation / when
the cache is cold, with "remember my choice"). The full model — inventory, deny-list
semantics, model override, and the prompt-cache cost of each switch — is owned by
[How To Construct A ReAct Agent](../../agents/react/how/how-to-construct-react-agent-README.md).

## Reusing the chat: headless engine + custom UI

The widget is split into a **headless engine** and a **view**, so you can keep
all of the chat orchestration and replace only the look:

> Packaged version: this engine is being extracted to the framework-agnostic
> [`@kdcube/components-core/chat`](../../npm/components-core/chat-engine-README.md)
> (+ React bindings in [`@kdcube/components-react`](../../npm/components-react/README.md)).
> This in-tree widget remains the reference until it consumes the package.

| Layer | Source | Reusable as-is |
| --- | --- | --- |
| State machine | `features/chat/chatSlice.ts` + `chatReducers.ts` (Redux Toolkit) | turns the SSE/socket envelope stream into the turn model |
| Transport | `api/` (`client.ts`, `sseTransport.ts`, `socketTransport.ts`, `types.ts`) | operations + streaming wire layer |
| Engine | `app/useChatEngine.tsx` | transport wiring, the send pipeline (+ serialization queue), conversation lifecycle, host-message handling, SSE/auth boot, context attach/remove, feedback, downloads, and host view-form state |
| View | `App.tsx` (default) | the only part you replace for a custom skin |

### Goal API

```tsx
import { ChatStoreProvider, useChatEngine } from 'sdk://solutions/chat/ui/widget/src/app'

<ChatStoreProvider config={/* optional */}>
  <MyOwnChatUI />     {/* renders anything; calls useChatEngine() inside */}
</ChatStoreProvider>
```

`ChatStoreProvider` provides the Redux store and **boots the engine once**.
`useChatEngine()` (called by any descendant) returns the engine:

```ts
const {
  state,            // the chat Redux state (turns, conversations, banners, composer…)
  send,             // (textOverride?, reactiveEventType?) => void — send the draft
  steer,            // () => void — interrupt-and-redirect the active turn
  loadConversation, // (conversationId) => void
  newChat,          // () => void
  setHostView,      // ('compact' | 'expanded') => void — sets the form AND messages the host
  attachContext,    // (ctx | ctx[]) => void — add host context chips to the composer
  removeContext,    // (id | id[]) => void — remove chips + sync the host
  openContextChip,  // (ctx) => void — activate a chip via its resolver default effect
  downloadFile,     // (ref, filename?, mime?) => void
  submitFeedback,   // (turnId, reaction, text?) => void
  hostView,         // 'compact' | 'expanded'
} = useChatEngine()
```

It also returns the extras the default view needs (`ready`, `bootError`,
`authed`, `kdcubePreview`, `bundleId`, `deleteConversation`,
`refreshConversationList`, `handleReconnect`, `pinConversationToCanvas`,
`promptLogin`, `setHostViewLocal`, and a `dryRun` bundle). A minimal custom UI
only needs `state` + `send`.

### Build a custom UI

Replace `App.tsx` with your own view; reuse everything else unchanged:

```tsx
function MyOwnChatUI() {
  const { state, send } = useChatEngine()
  return (
    <div>
      {state.turns.map((t) => <MyTurn key={t.id} turn={t} />)}
      <MyComposer value={state.composerText} onSend={() => send()} />
    </div>
  )
}
```

Re-theming the default view needs no code at all — every component styles from
the `:root` design tokens in `index.css`; override the tokens (the widget runs
in an iframe, so the override must live in the widget's own build).

### The `config` prop

`config` is optional `Partial<AppSettings>` (`baseUrl`, `tenant`, `project`,
`defaultBundleId`, `accessToken`, `idToken`, `idTokenHeader`). When **omitted**,
the engine keeps the default behavior: it resolves base URL / tenant / project /
bundle / auth from query params, the served route, and the parent-frame CONFIG
handshake. When **provided**, an external host can drive the engine directly
without the iframe handshake — applied to `settings` before the engine boots.

### Reuse only the backend (no React engine)

If you are building outside React (or in another stack) and want only the
server side, you do not use this engine at all — you target the documented wire
contract (operations + SSE/Socket.IO + the streaming envelope patterns). The
engine's `api/` layer is a reference implementation of that contract. See
[Client Transport Protocols](../../../service/comm/client-transport-protocols-README.md).

### Caveat: single store instance

The store is currently a module singleton, so two `<ChatStoreProvider>`
instances on one page share one chat state — correct for the single-widget
embed. Making it multi-instance safe (a store per provider) is a follow-up:
move `configureStore` into the provider and thread a store ref to the few
imperative `store.getState()` reads in the engine.

## Backend Assumptions

The mounted bundle must expose the normal chatbot operations supplied by the
chatbot/ReAct solution stack:

| Capability | Needed for |
| --- | --- |
| conversation list/read/delete/status | Chat sidebar and reload. |
| submit message with `external_events[]` | User prompt, followups, attachments, and context chips. |
| stream transport | Live assistant events through SSE or Socket.IO. |
| dry-run preview operation | Rendering `external_events[]` without invoking ReAct. |
| file/artifact operations | Showing and dragging chat artifacts. |
| object-action facade | Resolving context-chip object actions such as `capabilities`, `open`, and `download`; current compatible alias is `canvas_object_action`. |

The widget defaults match the task-tracker composition bundle. Other bundles
can override the event-source profile either at build time with Vite env
variables or at runtime with query parameters on the widget iframe URL.

Runtime query parameters are useful when a scene app embeds the same built
widget with a different profile, for example:

```text
/widgets/versatile_chat
  ?chat_widget_id=versatile_chat
  &chat_brand_label=Versatile
  &chat_event_prefix=versatile
```

## Event Source Profile

| Vite variable | Query parameter | Default | Meaning |
| --- | --- | --- | --- |
| `VITE_CHAT_WIDGET_ID` | `chat_widget_id` | `task_tracker_chat` | Widget id used in host `postMessage` calls. |
| `VITE_CHAT_BRAND_LABEL` | `chat_brand_label` | `Task Tracker` | Brand text shown in the widget chrome. |
| `VITE_CHAT_SURFACE` | `chat_surface` | `task_tracker_chat` | Surface placed on user prompt and generic context events. |
| `VITE_CHAT_USER_EVENT_SOURCE_ID` | `chat_user_event_source_id` | `task_tracker.main.chat.user` | Reactive user prompt/followup source. |
| `VITE_CHAT_ATTACHMENT_EVENT_SOURCE_ID` | `chat_attachment_event_source_id` | `task_tracker.main.chat.attachment` | Reactive user attachment source. |
| `VITE_CHAT_CONTEXT_EVENT_SOURCE_ID` | `chat_context_event_source_id` | `task_tracker.context.focus` | Generic attached context source. |
| `VITE_CHAT_CANVAS_STATE_EVENT_SOURCE_ID` | `chat_canvas_state_event_source_id` | `task_tracker.canvas.state` | Attached canvas board source. |
| `VITE_CHAT_CANVAS_FOCUS_EVENT_SOURCE_ID` | `chat_canvas_focus_event_source_id` | `task_tracker.canvas.focus` | Attached canvas selection source. |
| `VITE_CHAT_CANVAS_SURFACE` | `chat_canvas_surface` | `task_tracker_canvas` | Canvas context surface. |
| `VITE_CHAT_SNAPSHOT_EVENT_SOURCE_ID` | `chat_snapshot_event_source_id` | `task_tracker.task.snapshot` | Attached story/snapshot source. |
| `VITE_CHAT_SNAPSHOT_SURFACE` | `chat_snapshot_surface` | `task_tracker_wizard` | Story/snapshot context surface. |
| `VITE_CHAT_CONTEXT_ATTACH_MESSAGE` | `chat_context_attach_message` | `task-tracker-context-attach` | Host-to-widget context attach message. |
| `VITE_CHAT_CONTEXT_FOCUS_MESSAGE` | `chat_context_focus_message` | `task-tracker-context-focus` | Host-to-widget focus message. |
| `VITE_CHAT_CONTEXT_REMOVE_MESSAGE` | `chat_context_remove_message` | `task-tracker-context-remove` | Widget-to-host context removal message. |
| `VITE_CHAT_CONTEXT_REFRESH_SOURCE` | `chat_context_refresh_source` | `task-tracker-context-refresh` | Host refresh source marker that updates context chips silently. |
| `VITE_CHAT_CANVAS_INGRESS_MESSAGE` | `chat_canvas_ingress_message` | `task-tracker-canvas-ingress` | Drag payload type for chat artifacts dropped onto canvas. |

The defaults are intentionally task-tracker-compatible so extracting the
component does not change the existing app. A new bundle should set these values
to its own event-source ids instead of reusing task-tracker names.

## Context Flow

```text
host/widget subsystem
  -> postMessage context object
  -> chat composer chip
  -> submit external_events[]
  -> bundle ReAct runtime
  -> event-domain policies render ANNOUNCE/timeline blocks
```

Context chips are separate events. They must not be appended to the user text.
This preserves the timeline:

```text
[CANVAS STATE]
[CANVAS FOCUS]
[SNAPSHOT REF]
[USER MESSAGE]
```

## Context Chip Object Actions

Context chips are also clickable UI handles for the object they represent. The
chat widget does not infer object semantics from a namespace or a card kind. It
uses the same resolver-backed operation that canvas pins use:

```text
user clicks context chip
  -> chat widget calls the app object-action facade
       { action: capabilities, object_ref }
     (current compatible operation name: canvas_object_action)
  -> owner resolver returns capabilities plus default_open_effect_action
  -> chat runs exactly that declared effect
       default_open_effect_action=download -> object.action(download)
       default_open_effect_action=open     -> object.action(open)
  -> download bytes locally OR ask the host scene to orchestrate the open reaction
```

The action source is the context object's canonical ref. A chip may carry that
ref as `ref`, `logicalPath` / `logical_path`, `hostedUri` / `hosted_uri`,
`object_ref`, or `event_ref`; the widget forwards the resolved `object_ref` to
the bundle operation. The provider/resolver owns what that ref means.

The same canonical context payload drives chip labels and visual identity. The
widget first uses source/provider metadata such as `namespace` and
`object_kind`, then the shared namespace presentation map, and finally a
neutral unknown fallback. It should not parse the URI or ship local hardcoded
memory/task/file color tables.

Because context chips are persisted and replayed through the conversation
timeline, every producer should use the same canonical context-pin shape:
`{ type: "kdcube.context.attach", contexts: [...] }`, with each context carrying
one canonical `ref`. Compatibility aliases are accepted on read, but new
producers should not invent per-surface URI fields.

The provider/resolver also owns `default_open_effect_action`. It is resolved per
concrete object, not per host surface and not per namespace as a whole. For
example, a task provider can return `open` for `task:issue:<id>` and `download`
for `task:issue:attachment:<id>/...`. The chat widget and pinboard should not
infer this from `task:` or from broad capabilities.

For downloads, the resolver response should contain a cookie-authenticated
`download_url`, plus optional `filename`, `mime`, and `size`. The widget opens
that URL as a normal browser download, so file bytes do not travel inside the
JSON resolver response. `content_base64` remains a legacy fallback for older
providers only.

For opens, the resolver response must contain a `ui_event.target_surface`.
The chat iframe posts the resolver response to its host using:

```text
chat widget -> host scene
{
  type: "kdcube-object-open",
  widget: "<chat_widget_id>",
  response: <resolver response>,
  source: { id, title, kind, ref, mime }
}
```

The host scene then uses its surface registry to turn that action result into a
reaction: open an iframe panel, focus an already-mounted app, send a widget
command, or show that the target surface is unavailable. This keeps memory,
task, file, and namespace-specific behavior out of the chat component. The
scene is the UI orchestrator; it owns reactions to actions. See
[Scene Surface Registry](../scene/scene-surface-registry-README.md).

If no resolver is configured, if the resolver does not declare
`default_open_effect_action`, or if the chat widget is running without a host
for an `open`, the widget shows a context-action notice and leaves the chip
attached.

## Relation To Event Hub

The chat widget packages events, but event semantics come from event domains.
For example, `conv:fi:` artifacts are resolved by the ReAct artifact domain,
`mem:` by the memory named-service provider, `task:` by the task subsystem, and
`cnv:` by canvas.

The composition bundle imports those domains and registers their resolvers and
policies. The chat widget remains a transport and UI surface.

---
id: ks:docs/sdk/solutions/scene/scene-composition-README.md
title: "Scene Composition"
summary: "How a bundle assembles a host scene from reusable SDK components — iframe mounts by alias, the runtime CONFIG handshake, the component-to-component postMessage broker, and the Data Bus wiring. The routing of resolver object-opens lives in the surface registry doc."
status: draft
tags: ["sdk", "solutions", "scene", "widget", "iframe", "composition", "data-bus", "postmessage"]
updated_at: 2026-06-11
keywords:
  [
    "scene composition",
    "host scene",
    "ui.main_view src_folder",
    "ui.widgets alias",
    "CONFIG_REQUEST CONFIG_RESPONSE",
    "kdcube-context-attach",
    "kdcube-set-view",
    "kdcube-canvas-ingress",
    "canvas.patch data bus",
    "multi component scene",
  ]
see_also:
  - ks:docs/sdk/solutions/scene/scene-surface-registry-README.md
  - ks:docs/sdk/solutions/chat/chat-widget-solution-README.md
  - ks:docs/sdk/solutions/memory/memory-widget-solution-README.md
  - ks:docs/sdk/solutions/canvas/canvas-sdk-solution-README.md
  - ks:docs/sdk/solutions/usage/usage-card-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/ui-components-lifecycle-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/service/comm/data-bus-README.md
---
# Scene Composition

A scene is a host page that composes several reusable SDK components into one
workspace. The versatile bundle is the reference: a chat widget, the memory
widget, the canvas board, and a usage card, mounted side by side and wired to
talk to each other.

This doc is the assembly walk-through — what the host declares, how each
embedded component receives its runtime config, the message contract the host
brokers between components, and the Data Bus subscriptions. It does **not**
re-explain how a canvas object `open` reaches its target widget; that routing
contract is [Scene Surface Registry](scene-surface-registry-README.md).

For the per-component mount details, read each component's own doc:
[Chat Widget](../chat/chat-widget-solution-README.md),
[Memory Widget](../memory/memory-widget-solution-README.md),
[Canvas SDK Solution](../canvas/canvas-sdk-solution-README.md),
[Usage Card](../usage/usage-card-README.md).

## What The Host Owns

| Concern | Owner |
| --- | --- |
| Which components are mounted and where | Scene host |
| Component runtime config delivery (auth, base URL, tenant/project/bundle) | Scene host relay |
| Component-to-component messages (attach, focus, set-view, ingress) | Scene host broker |
| Panel size / drag / z-order | Scene host |
| Object identity and semantics (`mem:`, `fi:`, `cnv:`, …) | The owning namespace resolver |
| What a component renders and how it behaves | The embedded component |

The host is a composition and transport layer. It never reads a memory, opens a
conversation, or interprets a canvas object — it relays config and routes
commands.

## Mounting Components

Each embedded component is a declared bundle surface pointed at a shared SDK
source. In the bundle entrypoint, declare the alias; in `configuration_defaults`,
point it at the source and serve the host page itself.

```python
@api(alias="versatile_chat", route="operations", **_api_visibility("versatile_chat"))
@ui_widget(alias="versatile_chat", **_widget_visibility("versatile_chat"))
def versatile_chat_widget(self, **kwargs):
    del kwargs
    return ["<div>Chat is served from sdk://solutions/chat/ui/widget after build.</div>"]
```

```python
# configuration_defaults
"ui": {
    "main_view": {"src_folder": "ui/scene"},          # the host scene page
    "widgets": {
        "versatile_chat": {"src_folder": "sdk://solutions/chat/ui/widget"},
        "memories":       {"src_folder": "sdk://context/memory/ui/widget/memories"},
        "usage_card":     {"src_folder": "sdk://infra/economics/ui/widget/usage-card"},
        # the canvas board is available two ways: compiled into the scene as a
        # React component (below), AND as a standalone `pinboard` widget iframe
        # any host can broker — see "The Canvas Board As A Standalone Widget".
        "pinboard":       {"src_folder": "sdk://solutions/canvas/ui/widget/pinboard"},
    },
}
```

The host page (`ui/scene`) embeds each widget alias as an `<iframe>` and the
canvas board as an in-page React component. For the discovery → build → serve
lifecycle of these sources, see
[UI Components Lifecycle](../../bundle/ui-components-lifecycle-README.md).

## Runtime Config Handshake

Each embedded widget boots without knowing its base URL, tenant, project,
bundle, or auth material. On mount it asks the host for them; the host replies.

```text
widget iframe                         scene host
   |  CONFIG_REQUEST  ───────────────▶ |
   |   { identity, requestedFields }   |
   |                                   |  resolves runtime config
   |  ◀───────────────  CONFIG_RESPONSE|
   |   { identity, config }            |
   v                                   |
 builds API URLs + auth headers
```

The host relays `CONFIG_RESPONSE` to the requesting iframe by matching the
`identity` the widget sent. Every iframe in the scene shares this same relay —
the host keeps a reference to each `contentWindow` and answers whichever one
asked. This is the standard widget contract from
[Bundle Widget Integration](../../bundle/bundle-widget-integration-README.md).

## Component-To-Component Messages

The host brokers a small set of `postMessage` types between components. Names
are configurable per bundle (the chat widget exposes them as
`chat_context_attach_message`, etc.); the versatile scene uses the `kdcube-*`
defaults below.

| Message | Direction | Purpose |
| --- | --- | --- |
| `CONFIG_REQUEST` / `CONFIG_RESPONSE` | widget ⇄ host | Runtime config handshake (above). |
| `kdcube-set-view` | host → widget | Switch a widget between compact and expanded layout. |
| `kdcube-context-attach` | host → chat | Attach a board/object as a composer context chip. |
| `kdcube-context-focus` | host → chat | Attach the focused card(s) as context. |
| `kdcube-context-remove` | chat → host | The user removed a context chip. |
| `kdcube-canvas-ingress` | chat → host → canvas | Drag a chat artifact/text onto the board. |
| `kdcube-<widget>-command` | host → widget | A namespaced widget command (open, refresh, …). |

Context chips are **separate events**, never appended to the user prompt — the
chat widget keeps them as distinct timeline entries (see
[Chat Widget Solution](../chat/chat-widget-solution-README.md#context-flow)).

## Data Bus Subscriptions

Durable, ordered, cross-component state flows over the Socket.IO Data Bus, not
postMessage. The scene host subscribes to the subjects its components produce.

| Subject | Partition | Produced by | The host does |
| --- | --- | --- | --- |
| `canvas.patch` | `object_ref` | Canvas writes | Apply the revision and re-render the board. |
| `accounting.usage` | — | Platform accounting | Nudge the usage card to re-fetch. |

Use generic subject names (`canvas.*`), not bundle-prefixed ones — the scene is
demonstrating reusable SDK components, so the protocol names stay generic. For
the Data Bus delivery model see [Data Bus](../../../service/comm/data-bus-README.md).

## Opening Objects From The Board

When the user opens a canvas pin, the host does not interpret the object. It
calls the pin's namespace resolver (`canvas_object_action`), receives a
`ui_event` naming a `target_surface`, and delivers a command to that surface.
That dispatch — surface registry, `target_surface` mapping, and per-widget
command shape — is its own contract:
[Scene Surface Registry](scene-surface-registry-README.md).

Namespace resolvers can be configured instead of hardcoded in the composition
bundle. A task card can resolve through the task-tracker bundle like this:

```yaml
named_services:
  namespaces:
    task:
      provider:
        bundle_id: task-tracker@1-0
        provider: task.issue
        operation: named_service
```

The bundle registers those config entries with
`register_configured_named_service_canvas_resolvers(...)`. The same resolver is
used by the scene canvas and by the chat widget's object-action path, because
both call the bundle's `canvas_object_action` operation.

## The Canvas Board As A Standalone Widget

The canvas board ships **two ways** from one component
(`sdk://solutions/canvas/ui/component`):

1. **In-scene React component** — the versatile scene imports `CanvasBoard`
   and wires it inline. Use this when the board lives inside a host you also
   build in React and you want the tightest integration.
2. **Standalone `pinboard` widget** (`sdk://solutions/canvas/ui/widget/pinboard`)
   — the same `CanvasBoard` hosted as its own iframe. Use this when a host
   brokers each surface as a separate iframe rather than embedding a whole
   React scene: a non-React page, or an external host such as a product
   landing page that wants the board next to a few other widgets.

Both paths share the same two framework-free building blocks, so the board
behaves identically in either host:

| Building block | What it is | Lives in |
| --- | --- | --- |
| `canvasHost.ts` | A React-free `createCanvasHost(ctx, storyId)` factory: the canvas `operations/*` REST calls (read / list / object-action / attachment-upload) plus the Socket.IO Data Bus `canvas.patch` publish-and-wait. | the `pinboard` widget's `api/` (the scene keeps an equivalent inline copy) |
| `canvasBoard.css` | The board's own stylesheet — design tokens, reset, and every `.canvas-*` rule — so it renders the same in any iframe. | the canvas component package (`canvasBoard.css`) |

### Host-broker contract for the standalone widget

A standalone board cannot reach the chat or memory surfaces itself — those are
sibling iframes the host owns. So it does the canvas-local work inline (pin a
drop, patch, preview) and **posts the cross-surface intents to its parent**,
which routes them exactly like the in-scene broker routes the equivalent
in-scene calls. Same model as [What The Host Owns](#what-the-host-owns); only
the transport differs (postMessage across the iframe boundary instead of a
React callback).

| Message (from the board) | The host should | In-scene equivalent |
| --- | --- | --- |
| `CONFIG_REQUEST` (identity `PINBOARD_WIDGET`) | Reply `CONFIG_RESPONSE` with runtime config. | Same handshake as every widget. |
| `kdcube-pinboard-attach` `{ mode, contexts }` | Attach the card(s) to chat (`focus`/`attach`), or load a conversation (`open-conversation`). | `kdcube-context-focus` / `kdcube-context-attach`. |
| `kdcube-pinboard-open` `{ target_surface, ui_event }` | Route the resolver `open` to that surface. | [Opening Objects From The Board](#opening-objects-from-the-board). |
| `kdcube-pinboard-close` | Hide / dismiss the board panel. | Panel close in the scene host. |

The widget defaults its canvas story to `<bundle>:main` (overridable with a
`?story_id=` query param), so a board hosted in the same bundle as the scene
reads and writes the **same** canvas — a pin made in the standalone widget
shows up in the scene's board and vice versa.

## Reference

The versatile bundle is the working scene:

```text
src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36
  ui/scene/src/main.tsx          host page: iframe mounts, CONFIG relay, message broker, Data Bus
  entrypoint.py                  @ui_widget aliases, configuration_defaults, canvas resolver registry
  docs/design/scene-sdk-components.md   bundle-local design note

src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/canvas/ui
  component/src/CanvasBoard.tsx   the shared board (in-scene component AND widget host it)
  component/src/canvasBoard.css   component-owned board styles, reused by any host
  widget/pinboard/                standalone `pinboard` widget app
    src/App.tsx                   mounts CanvasBoard, emits the host-broker intents
    src/api/canvasHost.ts         React-free operations + Data Bus factory
    src/api/settings.ts           CONFIG bridge (identity PINBOARD_WIDGET)
```

The tier-1 builder entry point for this pattern is the *Multi-Component Host
Scene* recipe in
[How To Assemble A Bundle With SDK Building Blocks](../../bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md).

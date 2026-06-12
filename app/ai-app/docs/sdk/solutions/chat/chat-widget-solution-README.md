---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
title: "Chat Widget Solution"
summary: "How to mount the reusable SDK chat widget in a bundle and configure its event-source profile for ReAct, canvas, snapshots, and attachments."
status: draft
tags: ["sdk", "solutions", "chat", "widget", "bundle", "react", "external-events"]
updated_at: 2026-06-08
keywords:
  [
    "sdk chat widget",
    "chat_widget_ui_config",
    "sdk://solutions/chat/ui/widget",
    "chat event source profile",
    "bundle chat widget",
    "reusable chat component",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-ui-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-composition-README.md
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
| Widget UI | Chat transcript, composer, files, context chips, dry-run preview, reconnect control, compact/expanded host view messages. |
| Event packaging | Converts user prompt, attachments, and attached context chips into `external_events[]`. |
| Host iframe contract | Requests host view changes and auth prompts through `postMessage`. |
| Mount helper | `chat_widget_ui_config()` returns the standard bundle widget config. |

The bundle still owns the assistant runtime, tools, resolvers, policies, and
visibility rules. The chat widget sends events to the bundle; it does not decide
how the agent interprets every domain object.

### Send eligibility and anonymous visitors

- A draft is sendable when it has **any** of typed text, attachments, or
  attached context chips — context-only or attachment-only sends are allowed.
  The same predicate guards both the Send button and the ⌘/Ctrl+Enter path.
- The widget fetches the profile from the server. A non-OK `/profile` response
  (e.g. an anonymous visitor) is treated as an **anonymous identity**, not an
  error: the composer shows no profile-fetch error banner. Identity-gated host
  affordances stay hidden until a signed-in profile is confirmed.

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

## Relation To Event Hub

The chat widget packages events, but event semantics come from event domains.
For example, `fi:` artifacts are resolved by the ReAct artifact domain,
`mem:` by memory, `task:` by the task subsystem, and `cnv:` by canvas.

The composition bundle imports those domains and registers their resolvers and
policies. The chat widget remains a transport and UI surface.

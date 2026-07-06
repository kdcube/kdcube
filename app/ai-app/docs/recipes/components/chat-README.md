# Recipe: Chat Widget

The chat widget is an app widget that hosts a ReAct conversation and participates in the scene as a context producer and context consumer. It should not know memory, task, or canvas internals.

Read [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
first for the app/service-provider map. This recipe covers only the Chat/ReAct
surface. Declaring the AGENT behind the chat (per-agent config, tools/skills
inventory, `supported_models`, the user-facing composer menu) is the
[Chat With A ReAct Agent recipe](./chat-with-react-agent-README.md).

## Runtime Shape

```text
chat iframe
  UI shell
    timeline
    steps
    artifacts/files/context chips
  event client
    conversation stream for this chat
    optional scene subscription messages
  context adapter
    drag context out
    attach context in
  ReAct backend
    tools
    artifacts
    named-service object pull/read
    accounting events
```

## Scene Boundary

When embedded, the scene provides:

- runtime origin, tenant, project, and app id;
- namespace presentation config;
- context attach commands;
- scene-delivered events only when configured for that widget.

When standalone, the chat widget uses its runtime URL/config and owns its direct streams.

## Context Drag And Attach

```text
chat context chip drag
  -> kdcube-context-drag-start
     contexts: [{ ref: "mem:record:..." }]

drop on canvas
  -> scene command: kdcube.surface.command target_surface=sdk.canvas.pinboard action=pin

drop on owning widget
  -> scene object.action(open)
  -> provider ui_event.target_surface
  -> scene command: kdcube.surface.command action=open

drop from canvas/memory/task onto chat
  -> scene command: kdcube.surface.command target_surface=sdk.chat.context action=attach
  -> chat adds canonical context chip
```

The attached context keeps its original object URI. Color and label come from namespace presentation config and object metadata, not from chat-specific fallbacks.

## Event Handling

Chat has two different event concerns:

- Conversation events: tool calls, tool results, assistant/user output, and chat-local accounting display.
- Scene events: events that the host fans out to widgets that registered subscriptions.

For cross-widget accounting refresh, the usage widget is the consumer. Chat may still render accounting for the active chat turn when the event belongs to that chat instance.

## Config

A scene should declare chat as a context drop target:

```json
{
  "contextDropTargets": {
    "versatile": {
      "surfaceRef": "website.chat",
      "accepts": "context",
      "dropEffect": "attach",
      "targetSurface": "sdk.chat.context",
      "action": "attach"
    }
  }
}
```

## Current Gaps

- Context chip and drag helpers now live in shared component packages; remaining
  chat-specific wiring should stay thin and host-driven.
- Chat-local filtering for accounting events should use stable agent/session identity metadata as multi-agent flows expand.

## Related Docs

- [Architecture Of What You Build](../../arch/architecture-of-what-you-build-README.md)
- [Component Recipes](./README.md)
- [Components Ecosystem Architecture](../../sdk/solutions/ecosystem-component/components-ecosystem-README.md)
- [Chat Widget Solution](../../sdk/solutions/chat/chat-widget-solution-README.md)
- [Context Drag And Canvas Ingress](../../sdk/npm/components-core/context-drag-README.md)
- [Host Event Bus](../../sdk/npm/components-core/host-event-bus-README.md)
- [React Event Blocks](../../sdk/agents/react/event-blocks-README.md)

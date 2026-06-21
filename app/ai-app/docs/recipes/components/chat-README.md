# Recipe: Chat Widget

The chat widget is an app widget that hosts a ReAct conversation and participates in the scene as a context producer and context consumer. It should not know memory, task, or canvas internals.

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
      "acceptsRootNamespaces": ["*"],
      "dropEffect": "attach",
      "targetSurface": "sdk.chat.context",
      "action": "attach"
    }
  }
}
```

## Current Gaps

- The common context chip and drag helpers should move fully into shared component packages.
- Chat-local filtering for accounting events should use stable agent/session identity metadata as multi-agent flows expand.

## Related Docs

- [Chat Widget Solution](../../sdk/solutions/chat/chat-widget-solution-README.md)
- [Context Pin Contract](../../sdk/npm/components-core/context-pin-contract-README.md)
- [Host Event Bus](../../sdk/npm/components-core/host-event-bus-README.md)
- [React Event Blocks](../../sdk/agents/react/event-blocks-README.md)

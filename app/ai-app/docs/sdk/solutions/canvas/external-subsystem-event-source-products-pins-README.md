---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
title: "External Subsystem Event Source Product Pins"
summary: "Canvas pin contract for products emitted by external subsystem event sources: provider refs, memory refs, file refs, source refs, snapshot context, drag-to-canvas, and resolver ownership."
status: active
tags: ["sdk", "solutions", "canvas", "events", "event-sources", "object-refs", "resolvers"]
keywords:
  [
    "object.ref",
    "provider refs",
    "event source product pins",
    "external subsystem products",
    "acme:ticket",
    "provider snapshot",
    "object search",
    "drag to canvas",
    "canvas host extension",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-module-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
---
# External Subsystem Event Source Product Pins

This document describes canvas pins that represent products from subsystems
outside the canvas module. The base canvas module owns the board, revisioning,
user text, user attachments, agent text cards, focus events, and canvas state
events. Other subsystems add their own products as canonical refs.

Keep this split:

```text
canvas module
  user.text, user.attachment, agent.text, file, memory, source/search refs,
  canvas state/focus events, canvas storage, canvas tools

external subsystem integrations
  provider refs, memory refs, file refs, source refs, knowledge refs,
  subsystem-owned resolver behavior
```

## Provider Object Pins

A provider object pin is one example of an external subsystem product pin. The
canvas stores the card and the provider domain owns the object.

```json
{
  "id": "acme:ticket:ticket_2026-06-07-10-19-00-123456789",
  "kind": "object.ref",
  "title": "Upload fails after selecting screenshot",
  "summary": "needs review, evidence uploaded",
  "mime": "application/json",
  "logical_path": "acme:ticket:ticket_2026-06-07-10-19-00-123456789",
  "rect": {"x": 760, "y": 140, "w": 246, "h": 112},
  "placement": "placed"
}
```

Announce map labels use generic object labels for provider-owned refs:

```text
O1 object.ref card_id=acme:ticket:ticket_2026-06-07-10-19-00-123456789 title=Upload fails after selecting screenshot ref=acme:ticket:ticket_2026-06-07-10-19-00-123456789
```

The card's canvas-owned fields may be edited independently from the provider
object:

- `description`;
- comments;
- placement and size by the UI;
- selected/focused state.

Editing those fields does not mutate the provider object. Mutating the object
itself belongs to provider CRUD tools/APIs.

## Provider Snapshot Context

Provider UIs may produce read-only snapshot context. It is not canvas state.
When a provider snapshot is attached to chat, the client sends an
`event.snapshot` before the reactive prompt. The snapshot may later be exposed
through the provider resolver.

Expected future read path:

```text
acme:ticket:BUG-123
  -> latest authorized provider object and/or UI snapshot
  -> rendered as read-only context
```

ReAct should not patch snapshots through canvas tools. If it needs to update
the provider object, it should use provider CRUD tools. If it needs to annotate
the object pin on the board, it can use `canvas.patch(comment_card)` or
`canvas.patch(update_card set.description=...)`.

## Object Search And Drag To Canvas

Object search is a provider function. Search results can be dragged onto a
canvas as `object.ref` cards. The search result should supply enough metadata
for the canvas legend:

```json
{
  "kind": "object.ref",
  "title": "Connect provider list to operations API",
  "summary": "in progress, catalog",
  "mime": "application/json",
  "logical_path": "acme:ticket:BUG-122"
}
```

The canvas should not duplicate the object description, attachments, or history
as inline card content. It may keep a short preview/summary for display.

## Event Batch With Provider Context

When the user sends a chat request with a canvas and an open provider object attached,
the ordered batch should look like:

```text
1. event.canvas       latest board revision, non-reactive
2. event.canvas.focus selected/multi-selected cards on the board, non-reactive when selection is attached
3. event.snapshot     latest provider/UI snapshot, non-reactive when attached
4. event.user.prompt  user chat message, reactive
```

Provider pins on the board are still ordinary canvas cards. The
`event.snapshot` is separate because it represents current provider UI state,
not board layout.

## Current Resolver Status

Current implementation:

- `cnv:` canvas-owned user text, user attachments, and agent text are hosted
  by canvas storage.
- `fi:` ReAct artifacts are platform-owned and may be cross-conversation.
- `mem:` is memory-subsystem owned.
- Provider refs are preserved on object pins and dispatched by their root
  namespace. For `acme:ticket:<id>`, `acme` is the routing namespace and
  `acme:ticket` is the provider-owned owner key/subnamespace.
- `so:` is preserved for source/search-row pins. The full row reader path is
  planned.

The provider UI should include enough preview metadata on `object.ref` cards
for the canvas map and legend, and the agent should use provider tools/API for
exact object state.

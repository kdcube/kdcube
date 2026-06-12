---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
title: "External Subsystem Event Source Product Pins"
summary: "Canvas pin contract for products emitted by external subsystem event sources: task refs, memory refs, file refs, source refs, snapshot context, drag-to-canvas, and resolver ownership."
status: active
tags: ["sdk", "solutions", "canvas", "events", "event-sources", "object-refs", "resolvers"]
keywords:
  [
    "issue.ref",
    "task refs",
    "event source product pins",
    "external subsystem products",
    "task:issues",
    "task snapshot",
    "wizard snapshot",
    "issue search",
    "drag to canvas",
    "canvas host extension",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-module-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
  - repo:kdcube-applications/playground/bundles/task-tracker@1-0/doc/runtime/storage-and-search.md
  - repo:kdcube-applications/playground/bundles/task-tracker@1-0/doc/runtime/wizard-events-and-snapshots.md
  - repo:kdcube-applications/playground/bundles/task-tracker@1-0/doc/design/agents-and-wizard.md
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
  task refs, memory refs, file refs, source refs, knowledge refs,
  subsystem-owned resolver behavior
```

## Task Pins As One External Product Type

An issue/task pin is one example of an external subsystem product pin. The
canvas stores the card and the task domain owns the task object.

```json
{
  "id": "task:issues/ticket_2026-06-07-10-19-00-123456789",
  "kind": "issue.ref",
  "title": "Upload fails after selecting screenshot",
  "summary": "todo, evidence upload issue",
  "mime": "application/json",
  "logical_path": "task:issues/ticket_2026-06-07-10-19-00-123456789",
  "rect": {"x": 760, "y": 140, "w": 246, "h": 112},
  "placement": "placed"
}
```

Announce map labels should use `T` for task/story/issue refs:

```text
T1 issue.ref card_id=task:issues/ticket_2026-06-07-10-19-00-123456789 title=Upload fails after selecting screenshot ref=task:issues/ticket_2026-06-07-10-19-00-123456789
```

The card's canvas-owned fields may be edited independently from the issue:

- `description`;
- comments;
- placement and size by the UI;
- selected/focused state.

Editing those fields does not mutate the task row. Mutating the task itself
belongs to task-tracker CRUD tools/APIs.

## Task Snapshot Context

The issue wizard produces read-only snapshot context. It is not canvas state.
When the wizard is attached to chat, the client sends an `event.snapshot`
before the reactive prompt. The snapshot may later be exposed through a
`task:` resolver.

Expected future read path:

```text
task:issues/BUG-123
  -> latest authorized task object and/or wizard snapshot
  -> rendered as read-only context
```

ReAct should not patch snapshots through canvas tools. If it needs to update
the issue row, it should use task CRUD tools. If it needs to annotate the issue
pin on the board, it can use `canvas.patch(comment_card)` or
`canvas.patch(update_card set.description=...)`.

## Issue Search And Drag To Canvas

Issue search is a task-tracker function. Search results can be dragged onto a
canvas as `issue.ref` cards. The search result should supply enough metadata
for the canvas legend:

```json
{
  "kind": "issue.ref",
  "title": "Connect issues list to operations API",
  "summary": "in progress, catalog",
  "mime": "application/json",
  "logical_path": "task:issues/BUG-122"
}
```

The canvas should not duplicate the issue description, attachments, or history
as inline card content. It may keep a short preview/summary for display.

## Event Batch With Task Context

When the user sends a chat request with a canvas and an open issue attached,
the ordered batch should look like:

```text
1. event.canvas       latest board revision, non-reactive
2. event.canvas.focus selected/multi-selected cards on the board, non-reactive when selection is attached
3. event.snapshot     latest issue/wizard snapshot, non-reactive when attached
4. event.user.prompt  user chat message, reactive
```

Task pins on the board are still ordinary canvas cards. The `event.snapshot`
is separate because it represents current wizard/task state, not board layout.

## Current Resolver Status

Current implementation:

- `cnv:` canvas-owned user text, user attachments, and agent text are hosted
  by canvas storage.
- `fi:` ReAct artifacts are platform-owned and may be cross-conversation.
- `mem:` is memory-subsystem owned.
- `task:` is preserved on issue pins and documented as the task subsystem
  resolver namespace. Task objects use `task:issues/<issue_id>`; task-owned
  attachments use paths below that issue ref.
- `so:` is preserved for source/search-row pins. The full row reader path is
  planned.

The task-tracker UI should include enough issue preview metadata on `issue.ref`
cards for the canvas map and legend, and the agent should use task tools/API
for exact task state.

---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
title: "Canvas Pin Integration"
summary: "SDK integration contract for registering canvas object resolvers, routing pin actions through server-side resolver functions, and coordinating UI widgets over the data bus."
status: active
tags: ["sdk", "solutions", "canvas", "resolvers", "data-bus", "widgets", "memory", "tasks", "chat"]
keywords:
  [
    "canvas resolver registry",
    "bundle resolver",
    "object open requested",
    "ui object open",
    "task resolver",
    "memory resolver",
    "fi resolver",
    "data bus",
    "unsaved changes",
    "external_events",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-module-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-applications/playground/bundles/task-tracker@1-0/doc/runtime/message-routing.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
---
# Canvas Pin Integration

The canvas module should be reusable. It cannot hardcode task-tracker,
platform-file, memory, knowledge, or source-pool behavior. A bundle that embeds
canvas registers object resolvers for the namespaces it wants canvas to
understand.

The bundle-level checklist for mounting canvas lives in
[Bundle Subsystem Integration](../../bundle/bundle-subsystem-integration-README.md).
This page covers the pin/object resolver part of that integration.

A composition bundle can integrate several subsystems. The task-tracker pilot
currently uses this shape:

```text
canvas module         board, pins, revisions, comments, suggestions
task subsystem        task: issues, issue tools, issue editor
chat subsystem        ar: replicas, fi: chat attachments and ReAct artifacts
memory subsystem      mem: memories and memory search
knowledge subsystem   repo: repository-backed knowledge articles through MCP/search
source/search pool    so: or future source refs
```

Canvas stores pins. Resolvers make pins useful.

## Registration Point

At SDK level, the canvas module should accept a registry when it is mounted in
a bundle:

```python
canvas = CanvasModule(
    storage=CanvasStorage(...),
    resolvers=[
        PlatformArtifactResolver(),   # fi:
        TaskIssueResolver(),          # task:
        MemoryObjectResolver(),       # mem:
        KnowledgeArticleResolver(),   # repo:
        CanvasOwnedResolver(),        # cnv:
    ],
)
```

Each resolver owns one namespace:

```python
class TaskIssueResolver:
    namespace = "task"

    async def describe(self, ctx, ref):
        ...

    async def preview(self, ctx, ref):
        ...

    async def open(self, ctx, ref, request):
        ...
```

The registry dispatches by URI prefix:

```text
task:issues/ticket_...      -> TaskIssueResolver
fi:conv_...                 -> PlatformArtifactResolver
mem:user/...                -> MemoryObjectResolver
repo:kdcube-ai-app/app/ai-app/docs/...                 -> KnowledgeArticleResolver
cnv:.../ut_...              -> CanvasOwnedResolver
```

If no resolver is registered, canvas still keeps the pin but object actions are
unavailable.

## Current Pilot Implementation

The task-tracker bundle now uses the same shape locally, ahead of moving canvas
into SDK:

```text
canvas/events/resolver.py        CanvasObjectResolverRegistry and canvas-owned cnv: resolver
issues/events/resolver.py        task: issue resolver owned by the task subsystem
React events/resolver.py         fi: resolver owned by the ReAct SDK event/artifact layer
memory/events/resolver.py        mem: resolver owned by the SDK memory module
```

`entrypoint.py` only assembles the registry for this bundle. It does not
implement `task:`, `fi:`, or `mem:` semantics inline. `task:` behavior is in the
task subsystem, `fi:` behavior is in the ReAct event/artifact layer, and `mem:`
behavior is in the SDK memory module. That boundary is
intentional: adding a new namespace means registering the owning subsystem's
resolver, not teaching canvas or the task-tracker entrypoint a new object type.

## Client And Server Responsibilities

The client may use resolver descriptors for UI hints, but the server is
authoritative. The client should not implement download/open/rehost semantics
itself.

Recommended server operation:

```text
canvas_object_action
  input: { canvas_id, card_id?, object_ref, action, params }
  dispatch: registry[namespace].<action>(ctx, object_ref, params)
  output: resolver-specific bounded result
```

The UI flow:

```text
user clicks pin action
 -> canvas UI calls canvas_object_action
 -> server resolver handles action
 -> server returns result or emits a data-bus UI request
 -> UI updates from result or subscribed event
```

The data bus is used when an action needs another mounted widget to react. A
plain REST operation is enough for immediate preview/download results.

## Opening Objects

`open` means "ask the owning UI surface to focus this object." It does not mean
"canvas mutates the object" and it does not mean "canvas directly edits another
widget's state."

For a task pin:

```json
{
  "subject": "ui.object.open.requested",
  "object_ref": "task:issues/ticket_2026-06-07-10-19-00",
  "target_surface": "task_tracker.issue_editor",
  "request_id": "uiop_2026-06-07-13-30-00"
}
```

The task editor listens and decides whether it can open the requested issue.

If the target widget is mounted but hidden, it should be able to handle the
request and become visible. If the target widget is not mounted, the host shell
must either mount it or return:

```json
{
  "ok": false,
  "error": "target_surface_unavailable",
  "target_surface": "task_tracker.issue_editor"
}
```

The resolver should not pretend that an object opened if no surface accepted
the request.

## Unsaved Changes

Dirty state belongs to the widget that owns the form. Canvas must not force a
different object into a widget and overwrite unsaved state.

Task editor behavior:

```text
open request arrives for task B
current editor is clean
  -> open B

open request arrives for task B
current editor has unsaved task A
  -> show guarded navigation prompt
```

Minimum prompt actions:

```text
Save and open
Discard and open
Cancel
```

Later the task editor may add "Save draft and open". The important invariant is
that an object open event never silently destroys unsaved changes.

## Downloading Files

Canvas never stores a browser download handle. It stores only the canonical
object ref and asks the namespace resolver to download.

For a ReAct artifact:

```text
pin.object_ref = fi:conv_....turn_...outputs/problem_statement.md
click Download
 -> canvas_object_action(action=download, object_ref=fi:...)
 -> PlatformArtifactResolver.download(fi:...)
 -> resolver returns a browser-safe response
```

If the platform internally needs `ef:` or any other browser route, that is an
implementation detail of the platform artifact resolver. The canvas card does
not persist it.

## Rehosting

Rehost is an explicit ownership transfer into a target subsystem. It is not the
same operation as pinning.

Examples:

```text
drop fi: file onto canvas
  -> pin file as fi:
  -> no rehost

drop fi: file onto task attachments
  -> task subsystem rehosts bytes
  -> task creates task-owned attachment ref

drop mem: memory onto canvas
  -> pin memory as mem:
  -> no rehost
```

The target subsystem decides whether rehost is allowed and what new ref is
created. For task attachments, the task subsystem should create a deterministic
task-owned attachment id from the bytes so repeated drops of the same content
onto the same task do not create duplicate attachments.

## Memory Widget Integration

The memory widget should be integrated as another surface, not as special
canvas code.

Expected memory behavior:

```text
drag memory item out
  -> payload contains object_ref=mem:...
  -> canvas creates/updates memory card
  -> chat attaches focused memory context

drag mem: object into memory widget or click Open
  -> MemoryObjectResolver.open(mem:...)
  -> memory widget opens memory detail
```

The task-tracker app can use a compact memory widget variant when screen space
is limited. The full memory module still owns exact memory rendering, search,
and edits.

## Chat Context Batch

When the user sends a chat request with context attached, that context must be
recorded before the reactive user prompt in the conversation event batch. This
is required for causal replay and debugging.

Expected order:

```text
event.canvas           current board context, if attached
event.canvas.focus     selected/multi-selected cards on that board, if attached
event.snapshot         task/story editor context, if attached
event.external         proxied objects attached from pins or other widgets
event.user.prompt      reactive chat prompt
```

The rendered timeline should contain compact facts for all attached context,
then the user message:

```text
[CANVAS STATE]
[CANVAS FOCUS]
[SNAPSHOT REF]
[CONTEXT REF]
[USER MESSAGE]
```

ANNOUNCE contains the volatile rich views:

```text
[CANVAS BOARD]
[CANVAS FOCUSED CONTEXT]
[TASK CONTEXT]
```

`[CANVAS FOCUSED CONTEXT]` is for selected/multi-selected cards on the attached
canvas. A pin dragged by itself is rendered as the object it proxies (`task:`,
`mem:`, `fi:`, `cnv:`, etc.) and may carry canvas provenance metadata, but it
is not a canvas-focus event by itself.

If dry-run rendering shows context but live rendering only shows
`[USER MESSAGE]`, the normal send path is losing `external_events[]` or the
render policy is dropping non-reactive context events. That is information
loss and must be treated as a bug.

## Website And Knowledge Context

The same integration pattern should support the public website shell later:

```text
chat widget
canvas widget
task list widget
memories widget
knowledge context refs
```

The website's Why, What, and How sections should become concrete repository
refs, for example `repo:kdcube-ai-app/app/ai-app/docs/...`, not implicit page
text. When those refs are attached to chat, the resolver and MCP provide exact
read/search behavior.

Canvas should treat those refs the same way as every other object:

```text
pin repo:kdcube-ai-app/app/ai-app/docs/... on the board
focus repo:kdcube-ai-app/app/ai-app/docs/... into chat
open repo:kdcube-ai-app/app/ai-app/docs/... through a mounted repository surface
preview/read repo:kdcube-ai-app/app/ai-app/docs/... through a repository resolver
```

## Current Task-Tracker Implementation

The bundle now exposes the resolver entrypoint as:

```text
canvas_object_action({object_ref, action, card_id?, canvas_id?, canvas_name?, story_id?, mime?})
```

The main canvas UI asks this operation for `capabilities` when a card is
expanded, then shows only resolver-supported actions. The server-side behavior
currently implemented in task-tracker is:

| Namespace | Preview | Open | Download | Notes |
|---|---:|---:|---:|---|
| `cnv:` | yes | no | yes | Canvas-owned board/object artifact storage. |
| `task:` | yes | yes | no | Reads issue story through the task subsystem and returns an issue-editor open request. |
| `mem:` | yes | yes | no | Memory event resolver is owned by the SDK memory module; task-tracker only registers it with local store/scope context. |
| `fi:` | no | no | no | Canonical file refs are preserved. Download/preview waits for a platform artifact resolver. |

For `task:` open, the resolver returns:

```json
{
  "type": "kdcube.ui.object.open.requested",
  "subject": "ui.object.open.requested",
  "target_surface": "task_tracker.issue_editor",
  "object_ref": "task:issues/ticket_...",
  "issue_id": "ticket_..."
}
```

The main task editor applies the guarded-navigation rule locally. If the
current editor has unsaved edits for another issue, it offers:

```text
Save and open
Discard and open
Cancel
```

This is intentionally still a bundle-local implementation, not the final SDK
surface registry. The next SDK extraction step is to turn the same operation
shape into a reusable resolver registry and route cross-widget `open` requests
through the Data Bus when the target surface is not owned by the same React
tree.

Remaining work items:

- add real platform artifact resolver support for `fi:` preview/download;
- ensure live chat send includes the same context events that dry-run renders.

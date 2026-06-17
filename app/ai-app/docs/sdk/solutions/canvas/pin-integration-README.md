---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
title: "Canvas Pin Integration"
summary: "SDK integration contract for registering canvas object resolvers, routing pin actions through server-side resolver functions, and coordinating UI widgets over the data bus."
status: active
tags: ["sdk", "solutions", "canvas", "resolvers", "data-bus", "widgets", "memory", "chat"]
keywords:
  [
    "canvas resolver registry",
    "bundle resolver",
    "object open requested",
    "ui object open",
    "provider resolver",
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
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/search-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/scene/scene-surface-registry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-index-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-scoring-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
---
# Canvas Pin Integration

The canvas module should be reusable. It cannot hardcode domain-provider,
platform-file, memory, knowledge, or source-pool behavior. A bundle that embeds
canvas registers object resolvers for the namespaces it wants canvas to
understand.

The bundle-level checklist for mounting canvas lives in
[Bundle Subsystem Integration](../../bundle/bundle-subsystem-integration-README.md).
This page covers the pin/object resolver part of that integration.

A composition bundle can integrate several subsystems. A typical shape is:

```text
canvas module                  board, pins, revisions, comments, suggestions
provider subsystem             acme:ticket:<id>, provider tools, provider editor
chat subsystem                 ar: replicas, fi: chat attachments and ReAct artifacts
memory named-service provider  mem:record:<id>, memory search, memory actions
knowledge subsystem            repo: repository-backed knowledge articles through MCP/search
source/search pool             so: or future source refs
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
        ProviderObjectResolver(),     # acme:
        NamedServiceCanvasObjectResolver("mem"),  # mem:
        KnowledgeArticleResolver(),   # repo:
        CanvasOwnedResolver(),        # cnv:
    ],
)
```

Each resolver owns one namespace:

```python
class ProviderObjectResolver:
    namespace = "acme"

    async def describe(self, ctx, ref):
        ...

    async def preview(self, ctx, ref):
        ...

    async def open(self, ctx, ref, request):
        ...
```

The registry dispatches by URI prefix:

```text
acme:ticket:ticket_...      -> ProviderObjectResolver
fi:conv_...                 -> PlatformArtifactResolver
mem:record:mem_...          -> NamedServiceCanvasObjectResolver("mem")
repo:kdcube-ai-app/app/ai-app/docs/...                 -> KnowledgeArticleResolver
cnv:.../ut_...              -> CanvasOwnedResolver
```

If no resolver is registered, canvas still keeps the pin but object actions are
unavailable.

## Current Implementation Shape

The SDK canvas code uses the same shape locally:

```text
canvas/events/resolver.py        CanvasObjectResolverRegistry and canvas-owned cnv: resolver
React events/resolver.py         fi: resolver owned by the ReAct SDK event/artifact layer
named service resolvers          provider-owned refs via the namespace-service bridge,
                                  including mem: when memory is exposed as a named service
```

The composition entrypoint only assembles the registry for the mounted bundle.
It does not implement provider, `fi:`, or `mem:` semantics inline. Provider
behavior is in the provider subsystem, `fi:` behavior is in the ReAct
event/artifact layer, and `mem:` behavior is reached through the memory
named-service provider. That boundary is intentional: adding a new namespace
means registering the owning subsystem's resolver, not teaching canvas a new
object type.

## Pin Search

Pin boards are searchable, and the search is a **generic canvas mechanism** — not
something a bundle re-implements. It lives in `solutions/canvas/search` and any
bundle that mounts the canvas reuses it:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import CanvasPinSearch

self.pins = CanvasPinSearch(entrypoint)            # in the bundle's canvas mount
await self.pins.index(store=..., user_id=u, payload=p)   # on canvas update
await self.pins.clear(store=..., user_id=u, payload=p)   # on board delete
await self.pins.search(store=..., user_id=u, payload=p)  # on query (read-only)
```

`CanvasPinSearch` derives its two runtime dependencies from the host entrypoint, so
the bundle writes no bespoke embed/guard code:

- the embedder — `entrypoint.models_service.embed_texts`, and
- the economical guard — `entrypoint.search_semantic_guard(flow="canvas.pins.search")`,
  the same verify-only `economic_preflight` gate memory and task-tracker search use.

A bundle's canvas service stays thin: it constructs `self.pins = CanvasPinSearch(...)`
once and calls `index` after a successful write/patch/delete and `search` for
`canvas_search`. The reusable service owns the embedder, guard, card→Document mapping,
the observed-file-lock-serialized index build, and the read-only query.

The search itself — hybrid semantic + lexical + recency over a per-user SQLite +
vector index, indexed from the card-level snapshot on update — is described in
[Pin Operations → Pin Search And Indexing](./pin-operations-README.md#pin-search-and-indexing).
The underlying engine is the generic
[hybrid index](../index/hybrid-index-README.md) and its
[scoring](../index/hybrid-scoring-README.md). This is board-level search of the
**user's pins**, distinct from a provider's own object search (see
[External Subsystem Pins](./external-subsystem-event-source-products-pins-README.md#object-search-and-drag-to-canvas)).

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
For downloads, the REST result should return a cookie-authenticated
`download_url`; the browser then performs a normal GET and receives streamed
bytes. File bytes should not be embedded in the JSON action response.

## Opening Objects

`open` means "ask the owning UI surface to focus this object." It does not mean
"canvas mutates the object" and it does not mean "canvas directly edits another
widget's state."

For a provider-owned object pin:

```json
{
  "subject": "ui.object.open.requested",
  "object_ref": "acme:ticket:ticket_2026-06-07-10-19-00",
  "target_surface": "acme.ticket_editor",
  "request_id": "uiop_2026-06-07-13-30-00"
}
```

The provider editor listens and decides whether it can open the requested
object.

If the target widget is mounted but hidden, it should be able to handle the
request and become visible. If the target widget is not mounted, the host shell
must either mount it or return:

```json
{
  "ok": false,
  "error": "target_surface_unavailable",
  "target_surface": "acme.ticket_editor"
}
```

The resolver should not pretend that an object opened if no surface accepted
the request.

## Unsaved Changes

Dirty state belongs to the widget that owns the form. Canvas must not force a
different object into a widget and overwrite unsaved state.

Provider editor behavior:

```text
open request arrives for object B
current editor is clean
  -> open B

open request arrives for object B
current editor has unsaved object A
  -> show guarded navigation prompt
```

Minimum prompt actions:

```text
Save and open
Discard and open
Cancel
```

Later the provider editor may add "Save draft and open". The important invariant is
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
 -> resolver returns download_url
 -> browser GETs download_url with its existing session cookie
 -> server streams bytes
```

If the platform internally needs `ef:` or any other browser route, that is an
implementation detail of the platform artifact resolver. The canvas card does
not persist it.

ReAct pull uses the same namespace-owned byte access path but a different sink:
instead of returning a browser URL, the rehoster writes the bytes into the
current ReAct workspace. For example, `cnv:` canvas object download and `cnv:`
pull both use the canvas artifact resolver's byte reader; only the final
transport differs.

## Rehosting

Rehost is an explicit ownership transfer into a target subsystem. It is not the
same operation as pinning.

Examples:

```text
drop fi: file onto canvas
  -> pin file as fi:
  -> no rehost

drop fi: file onto provider attachments
  -> provider subsystem rehosts bytes
  -> provider creates provider-owned attachment ref

drop mem:record:<id> memory onto canvas
  -> pin memory as mem:record:<id>
  -> no rehost
```

The target subsystem decides whether rehost is allowed and what new ref is
created. For provider attachments, the provider subsystem should create a
deterministic provider-owned attachment id from the bytes so repeated drops of
the same content onto the same target object do not create duplicate
attachments.

## Memory Widget Integration

The memory widget should be integrated as another surface, not as special
canvas code. The same rule applies to any owning widget: issue tracker,
document viewer, CRM record editor, or app-specific provider UI.

Expected behavior:

```text
drag provider-owned item out
  -> payload contains object_ref=mem:record:<id>
  -> canvas creates/updates a proxy card with that same object_ref
  -> chat attaches the same object_ref as focused context

drag object from canvas/search/chat into owning widget, or click Open
  -> source surface emits canonical context drag
  -> scene host asks named-service resolver for object.action(open, object_ref)
  -> resolver returns ui_event.target_surface
  -> scene dispatches to the registered owning widget
  -> owning widget opens/focuses the object
```

The provider example app can use a compact memory widget variant when screen space
is limited. The full memory module still owns exact memory rendering, search,
and edits.

Some widgets may also support native browser drops for their own refs. That is
only a local convenience. The generic cross-widget contract is the scene broker
path documented in
[Scene Composition -> Cross-Surface Context Drag](../scene/scene-composition-README.md#cross-surface-context-drag).

## Chat Context Batch

> The drag/drop payload shape for context pins (one envelope, `ref` the single uri
> field) is the [context-pin contract](../../npm/components-core/context-pin-contract-README.md)
> in `@kdcube/components-core`.

When the user sends a chat request with context attached, that context must be
recorded before the reactive user prompt in the conversation event batch. This
is required for causal replay and debugging.

Expected order:

```text
event.canvas           current board context, if attached
event.canvas.focus     selected/multi-selected cards on that board, if attached
event.snapshot         provider editor context, if attached
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
[PROVIDER CONTEXT]
```

`[CANVAS FOCUSED CONTEXT]` is for selected/multi-selected cards on the attached
canvas. A pin dragged by itself is rendered as the object it proxies (`acme:`,
`mem:record:<id>`, `fi:`, `cnv:`, etc.) and may carry canvas provenance
metadata, but it is not a canvas-focus event by itself.

If dry-run rendering shows context but live rendering only shows
`[USER MESSAGE]`, the normal send path is losing `external_events[]` or the
render policy is dropping non-reactive context events. That is information
loss and must be treated as a bug.

## Website And Knowledge Context

The same integration pattern should support the public website shell later:

```text
chat widget
canvas widget
provider list widget
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

## Current Resolver Contract

The bundle now exposes the resolver entrypoint as:

```text
canvas_object_action({object_ref, action, card_id?, canvas_id?, canvas_name?, mime?})
```

The main canvas UI asks this operation for `capabilities` when a card is
expanded, then shows only resolver-supported actions. The generic behavior is:

| Namespace | Preview | Open | Download | Notes |
|---|---:|---:|---:|---|
| `cnv:` | yes | no | yes | Canvas-owned board/object artifact storage. |
| provider namespace | provider-defined | provider-defined | provider-defined | Reads and opens through the provider subsystem. |
| `mem:` | yes | yes | no | Memory is resolved through the configured named-service provider; canvas stores and forwards the canonical `mem:record:<id>` ref. |
| `fi:` | no | no | no | Canonical file refs are preserved. Download/preview waits for a platform artifact resolver. |

For provider open, the resolver returns:

```json
{
  "type": "kdcube.ui.object.open.requested",
  "subject": "ui.object.open.requested",
  "target_surface": "acme.ticket_editor",
  "object_ref": "acme:ticket:ticket_...",
  "object_id": "ticket_..."
}
```

The provider editor applies the guarded-navigation rule locally. If the current
editor has unsaved edits for another object, it offers:

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

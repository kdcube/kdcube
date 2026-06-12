---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/canvas-module-guide-README.md
title: "Canvas Module Guide"
summary: "SDK-level contract for collaborative canvas: card semantics, versioned storage, transport lanes, external events, ReAct tools/instructions, ANNOUNCE rendering, and conflict handling."
status: active
tags: ["sdk", "solutions", "canvas", "react", "external-events", "announce", "collaboration"]
keywords:
  [
    "sdk canvas",
    "canvas.patch",
    "canvas.read",
    "event.canvas",
    "event.canvas.focus",
    "CANVAS BOARD",
    "CANVAS FOCUSED CONTEXT",
    "canvas revision conflict",
    "user.text",
    "user.attachment",
    "agent.text",
    "ext refs",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Canvas Module Guide

This document describes the SDK canvas module contract. The mechanics belong
to the reusable canvas solution; product/domain behavior belongs to provider
bundles that mount canvas.

The canvas is a named, versioned, collaborative board of pins. It is not a
domain object, not a conversation, and not a timeline summary. A provider
object, memory, file, search result, user note, user upload, or
assistant-produced object can be pinned on the board as a card. The board gives the user a visual way to collect
context, share a selected subset as canvas focus, attach individual proxied
objects into chat, and receive assistant outputs back as suggested cards.

Related docs:

- `../design/canvas.md` has the longer design contract and patch examples.
- `../storage/README.md` is the canonical storage map.
- `external-subsystem-event-source-products-pins-README.md` defines how products from external subsystem event sources become canvas pins.

## Concepts

```text
canvas_name      user-visible board name, for example main or evidence
canvas_id        stable board id, usually cnv:<user_id>:<canvas_name>
revision         monotonically increasing integer per canvas_id
canvas_uri       short agent-facing handle, for example cnv:main@27
canvas_ref       immutable cnv: ref to a stored revision JSON
latest_ref       cnv: ref to the latest stored JSON for the board
card             one pin on the board
logical_path     ref to the object behind a card
placement        placed, floating, suggested, or trashed
```

Placement semantics:

| Placement | Meaning |
|---|---|
| `placed` | Card is visible in the board spatial map and has a rect. |
| `floating` | Card exists in the canvas revision but is not part of the placed spatial map yet. |
| `suggested` | Card is a pending suggestion waiting for user acceptance or arrangement. |
| `trashed` | Card is persisted in the bin. |

The user can have multiple named canvases. A canvas may be attached to any
selected conversation as context. Opening a canvas should not force creation of
a new conversation; it only makes that board available as context for the chat
the user chooses.

Canvas and snapshot are deliberately different:

| Object | Direction | Editable by agent | Purpose |
|---|---|---:|---|
| Canvas | collaborative | yes, only through `canvas.patch` | Working board of pins and suggestions |
| Snapshot | external state -> model | no | Read-only view of a provider/UI state surface |
| Chat prompt | user -> model | no | Reactive wake-up request |
| Chat attachment | user -> model/platform | no | Platform conversation attachment, usually `fi:` |

## Card Semantics

The canvas module owns these card/object kinds:

| Kind | Prefix | Hosted by | Meaning |
|---|---|---|---|
| `user.text` | `U` | canvas `cnv:` storage | User-authored note/text placed directly on canvas |
| `user.attachment` | `A` | canvas `cnv:` storage | User file uploaded to canvas |
| `agent.text` | `R` | canvas `cnv:` storage | Assistant-authored text card created through `canvas.patch` or dragged from assistant output |
| `file` | `F` | platform `fi:` or canvas/domain owner namespace | Produced or found file/report artifact |
| `memory` | `M` | memory subsystem `mem:` | Memory/search object pin |
| `source` / `search.result` | `S` | source/search resolver `so:` or subsystem | Source row or search result pin |

These are not platform-managed chat replicas:

- Text typed in chat is `ar:` conversation replica data.
- Files attached in chat are platform conversation attachments, usually `fi:`.
- Text written on canvas is a canvas-owned `user.text` object with a `cnv:` ref.
- Files dropped/uploaded on canvas are canvas-owned `user.attachment` objects
  with `cnv:` refs.
- Assistant final answers in chat are `ar:` assistant replica data.
- Assistant text put on canvas by `canvas.patch` is a canvas-owned `agent.text`
  object with a `cnv:` ref.

If a canvas-owned card is attached into chat, it remains a `cnv:` ref. Attaching
does not convert it into a chat prompt, chat attachment, or `fi:` artifact.

Cards are proxies to objects. For resolver-backed pins, the card id is the
original resolver URI itself: `acme:...`, `fi:...`, `mem:...`, `so:...`, etc.
One object has one proxy on a canvas:

- dragging the same `acme:ticket:<id>` again does not create another card;
- dragging the same `fi:`, `cnv:`, `mem:`, `so:`, or other resolver-backed ref
  again does not create another card;
- if that proxy is already in the bin, dragging the same object back onto the
  board restores the existing proxy instead of creating a copy;
- user-authored inline text/file upload is different because it creates a new
  hosted object first, and that new object then gets one proxy.

Only canvas-owned objects receive canvas-local ids. Those ids are
timestamp-bearing to the second plus a short slug:

```text
ut_<timestamp>  user.text hosted by canvas
ua_<timestamp>  user.attachment hosted by canvas
at_<timestamp>  agent.text hosted by canvas
```

The compact `U1`, `A1`, `O1` labels shown in ReAct ANNOUNCE are render-time map
labels for spatial reasoning. They are not durable card ids. When patching an
existing card, use the `card_id` value from the legend, such as
`ut_2026-06-07-10-20-30_k3f9` for canvas-owned text or
`acme:ticket:ticket_2026-06-07-10-19-00-123456789` for a provider object pin.

Host applications may add provider-owned object refs, but the reusable canvas
kind remains `object.ref`. Those pins are documented in
`external-subsystem-event-source-products-pins-README.md`. The canvas module
should not hardcode the meaning of host-app refs such as `acme:` beyond
preserving them as pins and exposing them in the legend.

## Storage

Canvas documents and canvas-owned card objects live behind the canvas storage
layer and are exposed as `cnv:` refs. The physical storage backend is hidden
behind the configured artifact storage adapter.

Board revision paths:

```text
<bundle-storage-prefix>/users/<user_id>/canvases/index.json
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/latest.json
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/revisions/000001.json
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/revisions/000002.json
```

Canvas-owned object paths:

```text
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/objects/user-text/<card_id>/v000001.md
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/objects/user-attachments/<card_id>/v000001.<ext>
<bundle-storage-prefix>/users/<user_id>/canvases/<safe_canvas_id>/objects/agent-text/<card_id>/v000001.md
```

Persisted cards should store metadata and refs, not large inline content:

```json
{
  "id": "ut_2026-06-07-10-20-30_k3f9",
  "kind": "user.text",
  "title": "Observed behavior",
  "mime": "text/markdown",
  "logical_path": "cnv:users/user-1/canvases/canvas_user-1_main/objects/user-text/ut_2026-06-07-10-20-30_k3f9/v000001.md",
  "storage_ref": "cnv:users/user-1/canvases/canvas_user-1_main/objects/user-text/ut_2026-06-07-10-20-30_k3f9/v000001.md",
  "content_preview": "Short visible preview only",
  "rect": {"x": 40, "y": 40, "w": 238, "h": 112}
}
```

`canvas_write` and `canvas_patch` both create a new board revision. Canvas card
content updates also create a new card-object version and update the card ref
in the new board revision.

The bin is also persisted in the canvas document. Moving a card to the bin is
not deletion; it sets `trashed=true` and `placement=trashed` in a new canvas
revision. Restoring a card clears that state on the same card id. Cleaning the
bin is the permanent operation and uses `delete_card`.

## Transport

There are three transport lanes today.

### 1. Main UI Operations

The main UI calls bundle operations for direct user actions:

```text
canvas_list
canvas_read
canvas_write
canvas_patch
canvas_attachment_upload
canvas_pin_read
canvas_search
```

These calls are for state management and content hosting. They do not wake
ReAct by themselves.

### 2. Conversation Context Batch

When the user sends a chat request with canvas context attached, the client
sends `external_events[]` before the reactive prompt. Typical order:

```text
event.canvas          latest board revision, non-reactive
event.canvas.focus    selected/multi-selected cards on that board, non-reactive
event.snapshot        read-only provider/UI state when attached, non-reactive
event.user.prompt     chat request, reactive
```

The canvas event body is rendered into ANNOUNCE as `[CANVAS BOARD]`. Canvas
selection focus is rendered as `[CANVAS FOCUSED CONTEXT]`. Timeline rendering
should show compact facts and causality, not dump full board JSON.

### 3. Agent Tool Events

Canvas exposes one model-facing write operation and one workspace import path:

```text
react.pull(paths=["cnv:<name>@<revision>"])   import exact board state
canvas.patch(...)                             write content changes
```

`canvas.patch` is a ReAct tool source. Its result carries enough canvas
projection for the announce policy to refresh `[CANVAS BOARD]` during the
current turn.

`canvas.read` is not an agent-visible tool. Exact board content is materialized
through the `cnv:` namespace rehoster used by `react.pull`. The pull result
returns an ordinary ReAct workspace artifact path; the agent can then inspect
that returned `fi:` or physical path with `react.read`, `react.rg`, or exec.

Agent-originated patches do not require the client to rebroadcast a canvas
event to the backend. The tool result is already the authoritative event source
for ReAct rendering. UI notification currently goes through the communicator
step bridge; later this should move to the data bus.

## Canvas Events

Declared canvas event sources today:

| Event source | Type | Reactive | Meaning |
|---|---|---:|---|
| `my_bundle.canvas.opened` | generic external | no | Named board became active context |
| `my_bundle.canvas.state` | `event.canvas` | no | Current canvas revision occurrence |
| `my_bundle.canvas.focus` | focus external event | no | User attached selected canvas cards/refs to chat |
| `my_bundle.canvas.saved` | generic external | no | Canvas state was saved |
| `my_bundle.canvas.file.uploaded` | generic external | no | Canvas-owned user attachment was uploaded |
| `my_bundle.canvas.file.deleted` | generic external | no | Canvas-owned attachment/card was removed |
| `my_bundle.canvas.file.annotated` | generic external | no | Canvas-owned attachment/card received label/annotation |

Canvas-owned card creation is represented by the new board revision and history
entry. The semantic object kind lives on the card:

```text
new user note          -> card kind user.text, hosted cnv: object
new canvas upload      -> card kind user.attachment, hosted cnv: object
new assistant text     -> card kind agent.text, hosted cnv: object
new assistant file     -> card kind file, usually fi: or cnv:
new provider object pin -> card kind object.ref, provider-owned ref such as acme:ticket:<id>
```

Do not model every card kind as a separate reactive event. The canvas state
event is the shared editable board state. Optional side-effect events can be
added only when the host app needs independent business processing.

## Agent Integration

The canvas module contributes additional instructions through
`canvas/instructions.py`. Any bundle using this module must append those
instructions to the ReAct prompt, not hide them as an optional skill.

The module exposes one ReAct-visible write tool and one workspace import path:

```text
react.pull(paths=["cnv:<name>@<revision>"])
canvas.patch(canvas_name="main", base_revision=<visible revision>, operations=<json>)
```

`react.pull(paths=["cnv:<name>@<revision>"])`:

- imports exact board JSON plus an `agent_view` into the ReAct workspace;
- is implemented by `@artifact_namespace_rehoster(namespace="cnv")`;
- should be used only when the ANNOUNCE map/legend is insufficient;
- returns `fi:` and physical paths that can be read or searched locally.

`canvas.patch`:

- is the only supported agent write path;
- persists a new board revision;
- accepts content operations such as `new_card`, `update_card`,
  `replace_card`, `suggest_deletion`, `delete_card`, and `comment_card`;
- does not expose layout operations to ReAct.

Agent rules:

- Do not edit/re-save canvas JSON directly.
- Do not move, resize, or arrange existing cards. Positioning is user/UI work.
- Do not mutate proxy refs such as `mem:`, `fi:`, `acme:`, or `so:`. You may
  update the canvas-owned description/comments for those cards.
- `user.text` content may be updated when the user asks.
- Generated files are not pinned automatically. Produce the file, then call
  `canvas.patch(new_card logical_path=fi:...)`.
- New assistant outputs should usually be `placement=suggested`, so the user
  can accept, arrange, or discard them.

## ANNOUNCE Shape

The current canvas is shown in ANNOUNCE, not repeatedly cached in timeline.

```text
[CANVAS BOARD]
canvas_name: main
canvas_id: cnv:user-1:main
canvas_uri: cnv:main@27
revision: 27

spatial_map:
U1 U1 A1 A1 .. ..
U1 U1 A1 A1 O1 ..
.. .. R1 R1 O1 ..

legend:
- U1 user.text card_id=ut_2026-06-07-10-20-30_k3f9 title=Observed behavior mime=text/markdown ref=cnv:...
  visible: Attachment disappears before submit...
- A1 user.attachment card_id=ua_2026-06-07-10-21-00_q8m2 title=trace.pdf mime=application/pdf ref=cnv:...
- O1 object.ref card_id=acme:ticket:ticket_2026-06-07-10-19-00-123456789 title=Upload fails after selecting screenshot ref=acme:ticket:ticket_2026-06-07-10-19-00-123456789
- R1 agent.text card_id=at_2026-06-07-10-23-00_h7pn suggested title=Suggested repro steps ref=cnv:...

canvas_read: react.pull(paths=["cnv:main@27"]) returns a workspace artifact with exact JSON plus agent_view.
canvas_write: collaborate only through canvas.patch with base_revision=revision.
```

Map labels are for spatial reasoning and are assigned by the ANNOUNCE renderer.
Canvas-owned durable card ids are timestamp-bearing (`ut_...`, `ua_...`,
`at_...`). Proxy card ids are the original resolver refs (`acme:...`, `fi:...`,
`mem:...`, etc.). The legend's `card_id` is the value to use in `canvas.patch`.

`[CANVAS FOCUSED CONTEXT]` is separate and turn-local. It represents the user's
selected or multi-selected cards on the attached canvas, for requests such as
"for selected cards, analyze ..." or other batch operations over cards on the
same board:

```text
[CANVAS FOCUSED CONTEXT]
focused_cards:
- A1 user.attachment title=trace.pdf ref=cnv:... mime=application/pdf

focus_semantics: these cards were explicitly selected on the canvas for this request.
priority: inspect selected cards before broader canvas context unless the user asks otherwise.
```

Dragging an individual pin to chat is different. The chat context for that pin
uses the proxied object ref (`acme:`, `mem:`, `fi:`, `cnv:`, etc.) and may carry
canvas provenance in metadata, but it is not a canvas-focus event by itself.

## Collaboration, Versioning, And Conflicts

Canvas uses optimistic concurrency:

```text
client/agent sees revision N
-> sends canvas.patch with base_revision=N
-> storage compares base_revision with current revision
-> success writes revision N+1
-> mismatch returns canvas_revision_conflict
```

The backend must not silently merge stale destructive writes. The UI should
make the conflict clear:

- explain that the board changed while the user was editing;
- show expected/current revision;
- offer `Retry on latest` for safe simple layout/content operations;
- offer `Refresh board` to discard the failed optimistic local change and load
  the stored latest board;
- allow dismissing the message.

Current UI behavior for user drag/resize is intentionally layout-owned by the
user. ReAct should not issue move/resize operations. Agent patches are content
collaboration: new suggested cards, comments, descriptions, replacement
suggestions, deletion suggestions, and allowed user-text edits.

Future data bus work should provide ordered per-object delivery for UI
collaboration. Ordering must be scoped by `object_ref`, for example
`cnv:<user_id>:main`, so only one handler applies a patch for that board at
a time.

## External Events And Host Extensions

The canvas module supports external event integration in two ways:

1. It can render/consume generic canvas events (`event.canvas`,
   `event.canvas.focus`) through event-source policies. `event.canvas.focus`
   is for canvas selection/multiselect context, not for every dragged pin.
2. It can host app-specific pins by preserving their `kind`, `logical_path`,
   MIME, preview, descriptions, and comments.

Provider objects are host-app extensions. A provider UI can pin an object as:

```json
{
  "kind": "object.ref",
  "title": "Upload fails after selecting screenshot",
  "mime": "application/json",
  "logical_path": "acme:ticket:BUG-123"
}
```

The canvas module should show that card spatially and preserve its ref, but
the provider domain owns what `acme:ticket:BUG-123` means and how ReAct can
read the provider snapshot.

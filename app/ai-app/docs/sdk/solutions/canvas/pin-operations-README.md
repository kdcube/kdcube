---
id: ks:docs/sdk/solutions/canvas/pin-operations-README.md
title: "Canvas Pin Operations"
summary: "Defines the object-level operation contract for canvas pins: one canonical object ref, resolver-owned preview/open/download/rehost behavior, and board-owned drag/drop/layout."
status: active
tags: ["sdk", "solutions", "canvas", "pins", "resolvers", "object-refs", "ui-actions"]
keywords:
  [
    "canvas pin operations",
    "object_ref",
    "logical_path",
    "resolver",
    "preview",
    "open",
    "download",
    "rehost",
    "canonical ref",
    "fi refs",
    "task refs",
    "mem refs",
  ]
see_also:
  - ks:docs/sdk/solutions/canvas/canvas-module-guide-README.md
  - ks:docs/sdk/solutions/canvas/pin-integration-README.md
  - ks:docs/sdk/solutions/canvas/external-subsystem-event-source-products-pins-README.md
  - ks:docs/sdk/events/namespaces-README.md
---
# Canvas Pin Operations

The canvas is a board of pins. A pin is a proxy to one external or
canvas-owned object. The canvas owns the card, its placement, display cache,
comments, and board revision. The source subsystem owns the object behind the
pin.

The core rule is:

```text
one pin -> one canonical object ref
```

No secondary download handle, browser handle, signed URL, `ef:` transport
handle, or resolver-private path should be persisted on the card. If a file is
identified as `fi:...`, the canvas stores `fi:...` and only `fi:...`. Download,
preview, materialization, and rehost all go through the resolver registered for
`fi:`.

## Pin Shape

Current task-tracker fields still use `logical_path`/`storage_ref` in places.
The SDK-level contract should converge on `object_ref`; the old field names are
compatibility aliases for the same canonical resolver URI.

```json
{
  "id": "fi:conv_123.turn_2026-06-07-12-59-27-283.outputs/problem_statement.md",
  "kind": "file",
  "object_ref": "fi:conv_123.turn_2026-06-07-12-59-27-283.outputs/problem_statement.md",
  "logical_path": "fi:conv_123.turn_2026-06-07-12-59-27-283.outputs/problem_statement.md",
  "title": "problem_statement.md",
  "mime": "text/markdown",
  "display_cache": {
    "title": "problem_statement.md",
    "mime": "text/markdown",
    "summary": "Short display preview, if known."
  },
  "rect": {"x": 360, "y": 120, "w": 260, "h": 120},
  "placement": "placed"
}
```

For resolver-backed objects, the card id should be the canonical object ref.
This makes duplicate prevention deterministic:

- dragging the same `task:issues/<id>` again updates/restores the existing pin;
- dragging the same `fi:` again updates/restores the existing pin;
- dragging the same `mem:` again updates/restores the existing pin;
- dragging the same `ks:` or `so:` again updates/restores the existing pin.

Canvas-owned objects are the exception because the user creates a new object
first. Their ids are minted by the canvas-owned object host:

```text
cnv:.../ut_2026-06-07-13-20-10_a1b2  user text
cnv:.../ua_2026-06-07-13-20-10_a1b2  user attachment
cnv:.../at_2026-06-07-13-20-10_a1b2  assistant text
```

Canvas-owned storage uses `cnv:` refs, so the canvas subsystem owns the board
and canvas-object namespace.

## Board Operations

These operations belong to the canvas board, not to resolvers:

```text
move card
resize card
select card
multi-select cards
share selected/multi-selected cards as canvas focus
drag card to chat as the proxied object context
drag card to bin
restore card from bin
clean bin
drop an external object onto the board
edit canvas-owned text
edit card description
add card comment
```

All cards are draggable and droppable as board objects. Resolver registration
must not decide whether a card can be moved, resized, selected, dropped into
chat, or dropped into the bin.

The resolver only decides what can be done with the underlying object.

## Resolver Operations

Resolver actions are derived from functions available on the registered
resolver. There should not be a separately maintained static `actions` list.

Conceptually:

```python
class CanvasObjectResolver:
    namespace = "fi"

    async def describe(self, ctx, ref): ...
    async def preview(self, ctx, ref): ...
    async def open(self, ctx, ref, request): ...
    async def download(self, ctx, ref, request): ...
    async def rehost(self, ctx, ref, target): ...
```

If a resolver implements `download`, the UI can show download. If it does not,
download is hidden. Same for `open`, `preview`, and `rehost`.

Current task-tracker exposes the operation as:

```text
canvas_object_action({object_ref, action, card_id?, canvas_id?, canvas_name?, story_id?, mime?})
```

The operation first resolves the namespace from `object_ref`, then dispatches
to the namespace resolver. The UI may ask for `capabilities`, but it should not
hardcode object behavior. Capabilities are live resolver facts, not canvas card
metadata.

Recommended operation meanings:

| Operation | Meaning | Typical result |
|---|---|---|
| `describe(ref)` | Return stable metadata for display cache | title, mime, size, summary, icon hint |
| `preview(ref)` | Return bounded renderable preview | text excerpt, image preview URL, object summary |
| `open(ref, request)` | Ask owning subsystem to focus/open the object | UI event, status, or unavailable result |
| `download(ref, request)` | Materialize downloadable bytes | stream response, URL, or short-lived browser handle |
| `rehost(ref, target)` | Copy bytes/object into another subsystem | new target-owned ref |

`download` is not a property stored on the pin. For a ReAct artifact, the flow
is:

```text
canvas card has object_ref=fi:...
user clicks Download
canvas server calls fi resolver download(ref)
fi resolver returns a response usable by the browser
```

For a task attachment target, the flow is:

```text
canvas/chat source has object_ref=fi:...
user drops it on task attachments
task subsystem calls fi resolver rehost(ref, target=task:issues/<id>)
task subsystem writes task-owned bytes and returns task:... attachment ref
```

The resulting task attachment is not a canvas link. It is a new task-owned
object.

## Ownership Rules

The card owns:

- `rect`;
- `placement`;
- selected/focused state;
- board comments;
- board description;
- display cache;
- timestamps for canvas revisioning;
- suggestion/bin state.

The source subsystem owns:

- object bytes;
- object schema;
- object permissions;
- object lifecycle;
- exact object preview/download/open behavior;
- rehost rules.

This split matters for collaboration. An assistant may suggest new cards,
suggest content edits for canvas-owned text, or suggest description/comment
updates on proxy cards. It should not mutate the underlying object through the
canvas unless it calls that object's own subsystem tool, such as task tools for
`task:` objects.

## Unknown Refs

Unknown refs are still valid pins. They should render as unresolved proxy
cards:

```json
{
  "kind": "object.ref",
  "object_ref": "abc:some/external/object",
  "display_cache": {
    "title": "abc:some/external/object"
  }
}
```

The board can move, resize, focus, comment, and delete the card. Object-level
actions are hidden until a resolver for `abc:` is registered.

## Error Handling

Resolver failures should not remove pins. A failed `preview`, `open`,
`download`, or `rehost` returns a bounded status:

```json
{
  "ok": false,
  "error": "resolver_unavailable",
  "namespace": "fi",
  "object_ref": "fi:...",
  "message": "File resolver is not available in this bundle."
}
```

The UI may show this on the card or in a toast, but the card remains in the
canvas revision. Pins are durable user context, even when the resolver is
temporarily unavailable.

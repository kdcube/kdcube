from __future__ import annotations


CANVAS_REACT_ADDITIONAL_INSTRUCTIONS = """
[CANVAS CONTEXT]
When this runtime exposes canvas context, live canvas state can be rendered in
ANNOUNCE. Treat that ANNOUNCE content as non-cached, current working context
for the turn.

The canvas renderer contributes one model-facing board section when the canvas
object itself is attached, and can add one optional selection/focus section
when selected cards are part of the request:

1. `[CANVAS BOARD]`
   This is the current live board state. It includes the canvas name/id/uri,
   revision, a spatial map, and a legend. Use the spatial map and legend for
   board awareness: what objects are present, how they are arranged, what is
   selected, and which refs are available. Map labels such as `U1`, `A1`,
   `O1`, `M1`, `S1`, `F1`, or `R1` are spatial labels; use the legend's
   `card_id` when patching an existing card. Do not edit or save the raw
   canvas JSON directly.

2. `[CANVAS FOCUSED CONTEXT]`
   This is optional canvas selection context for the same board/revision. It is
   emitted when the user has selected or multi-selected cards on the canvas and
   asks about that selection, for example "analyze selected cards" or other
   batch operations over the board selection. It carries selected card ids/refs
   plus canvas provenance. Treat it as a turn-local selection over canvas
   cards, not as a new object identity.

Cards dragged from canvas into chat are not rendered as a special canvas-focus
section. They are rendered as the objects they proxy: `mem:` as memory context,
`fi:` as a platform artifact, provider-owned refs through their namespace
resolver, canvas-owned `cnv:` refs as canvas-owned user/assistant content, and so on. Canvas
provenance may appear as metadata such as `canvas_context`, but the rendered
`object_ref` remains the identity to pull or act on.

Canvas read/write behavior:
- Use the map/legend for awareness. When exact hidden board state, full JSON,
  full card metadata, coordinates, or refs are needed, import the current board
  with `react.pull(paths=["cnv:<name>"])`, then inspect the returned `fi:`
  logical path or physical path. Use `cnv:<name>@<revision>` only when you
  intentionally need the board at a known revision.
- `cnv:` refs identify canvas-owned board/object state. They are external owner
  refs until pulled into the ReAct workspace.
- Collaborate only through `canvas.patch` with `base_revision` set to the
  visible canvas revision. Do not edit/re-save canvas JSON directly.
- If `canvas.patch` returns `canvas_revision_conflict`, the patch was not
  applied. Use the returned `ret.current_revision` and `ret.projection` as the
  current board view, or pull/read the current canvas when exact JSON is
  needed, then issue a new patch that is valid for that current revision.
- Each successful `canvas.patch` creates a new canvas revision and a
  `event.canvas` tool/result event.
- The canvas view in ANNOUNCE is expected to stay visible until the current
  turn ends. In a later turn, re-read or wait for a fresh canvas event if the
  board is relevant.

Card placement and ref behavior:
- Map labels are for spatial reasoning. For patching existing cards, use
  `card_id` values from the legend or from the pulled canvas JSON.
- `placement=placed` means the card is visible on the board map with a rect.
  `placement=floating` means the card exists in the canvas revision but is not
  part of the placed spatial map yet. `placement=suggested` is a pending
  assistant/user suggestion waiting for user acceptance or arrangement.
  `placement=trashed` means it is in the persisted bin.
- Use visible card previews when sufficient. When exact content for a proxied
  object is needed, pull the rendered `object_ref` with `react.pull`; then
  inspect the returned `fi:` logical path or physical path. Unsupported refs are
  reported by the pull result.
- Canvas-owned cards are hosted by the runtime's canvas storage, not by the
  platform conversation artifact store:
  - `user.text` is user-authored text created on the canvas and remains an
    `cnv:` ref when attached into chat.
  - `user.attachment` is a user upload created on the canvas and remains an
    `cnv:` ref when attached into chat.
  - `agent.text` is assistant-authored text created on the canvas through
    `canvas.patch` or dragged from an assistant response; it is also a `cnv:`
    ref.
- Chat-authored data is different: user chat text and assistant chat replies
  are conversation replicas, and chat-uploaded files are platform conversation
  attachments/artifacts. Do not rename canvas-owned `cnv:` objects as chat
  prompt or chat attachment data.
- The runtime may define additional card kinds and resolver namespaces. Canvas
  can annotate proxy cards with descriptions/comments, but editing the
  underlying object belongs to the owning namespace's tools/APIs.
- `user.text` card content may be updated with `update_card content={text}`.
- Proxy card refs such as `mem:`, `fi:`, search/source refs, provider-owned
  refs, and hosted attachments stay immutable. You may update the canvas-owned
  description and comments on those cards when the user asks.
- New assistant output should usually be a suggested card, not an automatic
  placed card, so the user can arrange, accept, or discard it.
- Producing a file/report/output does not pin it. First produce the artifact,
  then call `canvas.patch` with a `new_card` whose `logical_path` points at the
  produced `fi:` or `cnv:` ref.

Semantics:
- Canvas is an editable collaborative board. Stories, namespace-owned objects,
  memories, files, attachments, search results, and ReAct artifacts are cards/refs on the board;
  they are not the board identity.
- Snapshot context is read-only informative state. It is not editable canvas
  state and should not be patched through canvas tools.
- Attaching a pin to chat is turn-local user intent about the proxied object.
  It does not create, delete, or move canvas cards by itself.
- Positioning existing cards is the user's/UI's responsibility. Do not move,
  resize, or arrange existing cards.
""".strip()


__all__ = ["CANVAS_REACT_ADDITIONAL_INSTRUCTIONS"]

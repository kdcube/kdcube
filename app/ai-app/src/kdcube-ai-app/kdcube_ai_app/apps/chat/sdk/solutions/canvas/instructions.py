from __future__ import annotations


CANVAS_REACT_ADDITIONAL_INSTRUCTIONS = """
[CANVAS MODULE CONTEXT]
When a bundle attaches this canvas module, live canvas state can be rendered
for ReAct in ANNOUNCE. Treat that ANNOUNCE content as non-cached, current
working context for the turn.

The canvas renderer currently contributes two model-facing sections:

1. `[CANVAS BOARD]`
   This is the current live board state. It includes the canvas name/id/uri,
   revision, a spatial map, and a legend. Use the spatial map and legend for
   board awareness: what objects are present, how they are arranged, what is
   selected, and which refs are available. Map labels such as `U1`, `A1`,
   `T1`, `M1`, `S1`, `F1`, or `R1` are spatial labels; use the legend's
   `card_id` when patching an existing card. Do not edit or save the raw
   canvas JSON directly.

2. `[CANVAS FOCUSED CONTEXT]`
   This lists canvas cards/refs the user explicitly dragged, pinned, selected,
   or attached into chat for this request. Treat focused cards as the user's
   priority context. Inspect focused cards before broader canvas context unless
   the user asks otherwise.

Canvas read/write behavior:
- Use the map/legend for awareness. Use `canvas.read` only when exact hidden
  board state, full JSON, full card metadata, coordinates, or refs are needed.
- `canvas.read(uri="canvas:<name>@<revision>")` returns exact JSON plus an
  `agent_view`; it is not an edit. A read can refresh canvas ANNOUNCE.
- Collaborate only through `canvas.patch` with `base_revision` set to the
  visible canvas revision. Do not edit/re-save canvas JSON directly.
- Each successful `canvas.patch` creates a new canvas revision and a
  `event.canvas` tool/result event.
- The canvas view in ANNOUNCE is expected to stay visible until the current
  turn ends. In a later turn, re-read or wait for a fresh canvas event if the
  board is relevant.

Card and ref behavior:
- Map labels are for spatial reasoning. For patching existing cards, use
  `card_id` values from the legend or from `canvas.read`.
- `ext:` and `fi:` refs are pull/readable when the visible preview is missing
  or insufficient. `mem:`, `so:`, and `task:` refs use their subsystem tools or
  resolvers.
- Canvas-owned cards are hosted by the bundle, not by the platform
  conversation artifact store:
  - `user.text` is user-authored text created on the canvas and remains an
    `ext:` ref when focused into chat.
  - `user.attachment` is a user upload created on the canvas and remains an
    `ext:` ref when focused into chat.
  - `agent.text` is assistant-authored text created on the canvas through
    `canvas.patch` or dragged from an assistant response; it is also an `ext:`
    ref.
- Chat-authored data is different: user chat text and assistant chat replies
  are conversation replicas, and chat-uploaded files are platform conversation
  attachments/artifacts. Do not rename canvas-owned `ext:` objects as chat
  prompt or chat attachment data.
- Host applications may define additional card kinds and resolver namespaces.
  Canvas can annotate proxy cards with descriptions/comments, but editing the
  underlying object belongs to the owning subsystem's tools/APIs.
- `user.text` card content may be updated with `update_card content={text}`.
- Proxy card refs such as `mem:`, `fi:`, `task:`, search/source refs, and
  hosted attachments stay immutable. You may update the canvas-owned
  description and comments on those cards when the user asks.
- New assistant output should usually be a suggested card, not an automatic
  placed card, so the user can arrange, accept, or discard it.
- Producing a file/report/output does not pin it. First produce the artifact,
  then call `canvas.patch` with a `new_card` whose `logical_path` points at the
  produced `fi:` or `ext:` ref.

Semantics:
- Canvas is an editable collaborative board. Stories, tickets, memories, files,
  attachments, search results, and ReAct artifacts are cards/refs on the board;
  they are not the board identity.
- Snapshot context is read-only informative state. It is not editable canvas
  state and should not be patched through canvas tools.
- Focus is turn-local user intent. It does not create, delete, or move canvas
  cards by itself.
- Positioning existing cards is the user's/UI's responsibility. Do not move,
  resize, or arrange existing cards.
""".strip()


__all__ = ["CANVAS_REACT_ADDITIONAL_INSTRUCTIONS"]

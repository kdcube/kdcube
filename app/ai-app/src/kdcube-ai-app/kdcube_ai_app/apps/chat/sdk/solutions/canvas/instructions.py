from __future__ import annotations

from typing import Any, Mapping, Sequence


CANVAS_NAMESPACE_INTRO = "Canvas (also called the pin board — both names mean this same board) — a board of pinned cards for quick access later. A pin (card) can point to an object from any connected namespace (a task, a memory, a file), a file you produced, or an entire conversation. Pin when the user signals they want to keep, bookmark, or set something aside for later — in particular, when they want to remember a specific thing that isn't a durable memory, that's the cue to pin it here; if you're unsure, you can suggest pinning. You don't need to ask which board: unless the user names a specific board, just pin to the board, and the pin board service decides which canvas it lands on. It's one of the user's memory realms — the context-board kind: things pinned or set aside for later. When recalling what the user kept for quick access, look here alongside their durable memories (`mem`) and past conversations."

CANVAS_BOARD_EDIT_PROTOCOL_LINES = (
    "edit_protocol:",
    "- Use named_services.upsert_object(namespace=\"cnv\", object_ref=<board object_ref from visible canvas context or named_services.list_objects>, base_revision=revision, object_json=<typed canvas object>).",
    "- Ask named_services.object_schema(namespace=\"cnv\", object_kind=...) for exact mutation payloads.",
    "- Treat this JSON as exact state for planning only; do not edit or save it directly.",
    "- Use map labels for spatial reasoning; use card_id values from the legend when mutating existing cards.",
    "- Use card refs only when content_preview is missing or insufficient; pull external owner refs into the ReAct workspace before exact content inspection.",
    "- provided.text and user.text card content can be updated with canvas.card content={text}. Proxy cards keep their ref content unchanged; use canvas.card description or canvas.card.comment for user notes on them.",
    "- Card kind is a source-provided label. Canvas-owned content may use provided.text/user.text/user.attachment/agent.text; provider-owned pins keep the provider label and resolve through object_ref/namespace/object_kind.",
    "- Use kind=provided.text for source-neutral text hosted by canvas. Use kind=agent.text only when the text is known to be assistant-authored. Do not coerce provider objects into canvas-owned kinds.",
    "- Every successful canvas upsert creates a new revision and event.canvas timeline result.",
    "- Do not edit read-only snapshot refs; canvas cards are editable pins, snapshots are context.",
)


def render_canvas_board_text(
    *,
    canvas_name: str,
    canvas_id: str,
    canvas_uri: str,
    revision: Any,
    bounds: Mapping[str, Any],
    active_count: int,
    placed_count: int,
    floating_count: int,
    suggested_count: int,
    bin_count: int,
    spatial_map: Sequence[str],
    legend_lines: Sequence[str],
    status_lines: Sequence[str] = (),
) -> str:
    """Render the ReAct-facing canvas board section from canonical projection facts."""
    lines = [
        "[CANVAS BOARD]",
        f"canvas_name: {canvas_name}",
        f"canvas_id: {canvas_id}",
        f"canvas_uri: {canvas_uri}",
        f"revision: {revision}",
        *status_lines,
        f"bounds: x={bounds.get('x')} y={bounds.get('y')} w={bounds.get('w')} h={bounds.get('h')}",
        f"cards: {active_count} placed={placed_count} floating={floating_count} pending_suggestions={suggested_count} bin={bin_count}",
        "",
        "spatial_map:",
        *spatial_map,
        "",
        "legend:",
        *legend_lines,
        "",
        *CANVAS_BOARD_EDIT_PROTOCOL_LINES,
    ]
    return "\n".join(lines)


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
- Collaborate through the `cnv` named-service provider. Call
  `named_services.list_objects(namespace="cnv")` to discover boards when no
  board is visible in context. Do not use `search_objects(namespace="cnv",
  query="canvas board")` for board discovery; canvas search semantically
  matches card snapshots/content.
- Use `canvas_id` only as returned metadata. Do not convert storage ids such as
  `cnv:<user>:<board>` into guessed refs such as `cnv:<user>_<board>`.
- Call
  `named_services.object_schema(namespace="cnv", object_kind=...)` when you
  need exact payload shape. Mutate with the board `object_ref` from visible
  canvas context, `named_services.list_objects`, or a recent canvas pull/read:
  `named_services.upsert_object(namespace="cnv", object_ref=<board object_ref>,
  base_revision=<visible revision>, object_json=...)`.
- Use typed canvas object kinds in the upsert payload instead of editing raw
  board JSON: `canvas.card`, `canvas.card.comment`,
  `canvas.card.replacement`, `canvas.card.deletion_suggestion`,
  `canvas.card.delete`, `canvas.operation_batch`, or `canvas.card.layout`
  when the user explicitly asks you to arrange/move/resize cards.
- If `named_services.upsert_object(namespace="cnv", ...)` returns
  `canvas_revision_conflict`, the mutation was not applied. Pull/read the
  current board with `react.pull(paths=["cnv:<name>"])`, inspect the returned
  `fi:` snapshot, then issue a new upsert that is valid for that current
  revision. Do not blind-retry an old payload.
- Each successful canvas upsert creates a new canvas revision and a canvas
  event/projection for the board.
- A rendered canvas board section declares its remaining visibility window,
  for example `visibility: 3/3 render rounds remaining`. When that window is
  exhausted, pull/read the `cnv:<name>` board again if the board is still
  relevant.

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
  - `provided.text` is source-neutral text provided to the canvas and remains a
    `cnv:` ref when attached into chat.
  - `user.text` is user-authored text created on the canvas and remains an
    `cnv:` ref when attached into chat.
  - `user.attachment` is a user upload created on the canvas and remains an
    `cnv:` ref when attached into chat.
  - `agent.text` is assistant-authored text created on the canvas through a
    `named_services.upsert_object(namespace="cnv", object_kind="canvas.card",
    ...)` call or dragged from an assistant response; it is also a `cnv:` ref.
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
  discover/reuse the board `object_ref`, then call
  `named_services.upsert_object(namespace="cnv", object_ref=<board object_ref>,
  base_revision=<visible revision>, object_json={"object_kind":"canvas.card",
  ...})` with `card.logical_path` pointing at the produced `fi:` or `cnv:` ref.

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


__all__ = [
    "CANVAS_BOARD_EDIT_PROTOCOL_LINES",
    "CANVAS_NAMESPACE_INTRO",
    "CANVAS_REACT_ADDITIONAL_INSTRUCTIONS",
    "render_canvas_board_text",
]

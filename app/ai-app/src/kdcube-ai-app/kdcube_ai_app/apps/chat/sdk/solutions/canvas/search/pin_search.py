# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Canvas pin-board search operations.

Two ops, deliberately split by frequency:

- `index_pins`  — called on canvas **updates** (pin add / edit / remove). Embeds the
  changed cards and (re)builds the vector index, serialized per user with the
  runtime's observed file lock. This is the only place that pays the embedder for
  pin material.
- `search_pins` — **read-only**, called per query (far more frequent than updates).
  It does NOT sync or re-embed pins; it searches the already-built index and only
  embeds the *query* (gated by `semantic_guard` → degrades to lexical on denial).

Why index on update, not search: searches are frequent and we must not rebuild the
index every time. And we don't know when a pinned object's *source* data changes —
a pin is a proxy to an object in another system that may be unversioned — so the
material we index is the **card-level snapshot** (`card_text`: label / title /
description / comments / kind / ref, all card-resident, captured at pin/update
time). We never re-fetch the source object at index time; the index reflects exactly
what the card holds, and only changes when the card changes (i.e. on a canvas update).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from kdcube_ai_app.infra.index.sqlite import EmbedFn, VectorStore
from kdcube_ai_app.storage.observed_file_locks import observed_file_lock_async

from .pin_index import PinSearchIndex

DEFAULT_EMBEDDING_DIM = 1536

logger = logging.getLogger("kdcube.canvas.pins")


def _read_board_cards(store: Any, *, board_id: str, story_id: str, payload: Mapping[str, Any]) -> list:
    _, canvas = store.read_document(
        canvas_id=board_id, story_id=story_id,
        canvas_name=store.canvas_name(payload.get("canvas_name") or payload.get("name")),
    )
    return list(canvas.get("cards") or [])


def _safe(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "")).strip("_") or "anon"


def pin_index_db_path(store: Any, user_id: str) -> Path:
    """Per-user pin index file under the bundle's artifact storage_root."""
    return Path(store.storage_root) / ".pin-index" / _safe(user_id) / "pins.index.sqlite"


def _resolve_board(store: Any, payload: Mapping[str, Any]) -> str:
    canvas_name = store.canvas_name(payload.get("canvas_name") or payload.get("name"))
    return store.canvas_id(canvas_name=canvas_name, canvas_id=payload.get("canvas_id"))


def _make_vector_store(db_path: Path, *, backend: str) -> Optional[VectorStore]:
    """Per-user vector backend. `None` → PinSearchIndex uses the pure-python
    BruteForceVectorStore (in-memory, no deps). `faiss-local` → a file-backed
    faiss index next to the per-user SQLite (`pins.index.faiss`), so vectors
    persist across processes/workers (requires faiss + numpy)."""
    name = (backend or "bruteforce").strip().lower()
    if name in ("faiss", "faiss-local", "local"):
        from kdcube_ai_app.infra.index.faiss import LocalFaissStore
        return LocalFaissStore(Path(db_path).with_name("pins.index.faiss"))
    return None


def _build_index(
    store: Any, user_id: str, *, embed_fn: EmbedFn, dim: int,
    vector_backend: str = "faiss-local",
    vector_store: Optional[VectorStore] = None, semantic_guard: Optional[Any] = None,
) -> PinSearchIndex:
    db_path = pin_index_db_path(store, user_id)
    # An explicit instance wins (tests / advanced); otherwise build a per-user
    # store from the selected backend — a single shared instance would mix users.
    vs = vector_store if vector_store is not None else _make_vector_store(db_path, backend=vector_backend)
    return PinSearchIndex(
        db_path=db_path,
        embed_fn=embed_fn,
        dim=dim,
        vector_store=vs,
        semantic_guard=semantic_guard,
    )


async def index_pins(
    *,
    store: Any,
    user_id: str,
    story_id: str,
    payload: Mapping[str, Any],
    embed_fn: EmbedFn,
    dim: int = DEFAULT_EMBEDDING_DIM,
    vector_backend: str = "faiss-local",
    vector_store: Optional[VectorStore] = None,
) -> dict:
    """Index/refresh a board's pins from the board's CURRENT cards. Call on canvas
    update (write/patch/delete). Embeds only changed cards (write-side embedding is
    never economically gated — same as issue write embeds). Serialized per user."""
    board_id = _resolve_board(store, payload)
    try:
        _, canvas = store.read_document(
            canvas_id=board_id, story_id=story_id,
            canvas_name=store.canvas_name(payload.get("canvas_name") or payload.get("name")),
        )
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "board": board_id, "error": str(exc)}

    cards = canvas.get("cards") or []
    db_path = pin_index_db_path(store, user_id)
    index = _build_index(store, user_id, embed_fn=embed_fn, dim=dim, vector_backend=vector_backend, vector_store=vector_store)
    try:
        async with observed_file_lock_async(
            lock_path=db_path.with_name(db_path.name + ".lock"),
            resource_id=f"canvas.pins:{_safe(user_id)}",
            operation="canvas.pins.index.update",
            wait_seconds=30,
        ):
            await index.sync_board(cards, board_id=board_id)
            await index.ensure_built()
            total = index.index.count()
    except Exception as exc:
        logger.warning("[canvas.pins.index] failed board=%s db=%s error=%s", board_id, db_path, exc, exc_info=True)
        return {"ok": False, "user_id": user_id, "story_id": story_id, "board": board_id, "error": str(exc)}
    logger.info("[canvas.pins.index] board=%s cards=%s docs_total=%s db=%s", board_id, len(cards), total, db_path)
    return {"ok": True, "user_id": user_id, "story_id": story_id, "board": board_id, "indexed": len(cards)}


async def clear_pins(
    *,
    store: Any,
    user_id: str,
    story_id: str,
    payload: Mapping[str, Any],
    embed_fn: EmbedFn,
    dim: int = DEFAULT_EMBEDDING_DIM,
    vector_backend: str = "faiss-local",
    vector_store: Optional[VectorStore] = None,
) -> dict:
    """Clear one board from the pin index. Call after a board delete."""
    board_id = _resolve_board(store, payload)
    db_path = pin_index_db_path(store, user_id)
    index = _build_index(store, user_id, embed_fn=embed_fn, dim=dim, vector_backend=vector_backend, vector_store=vector_store)
    try:
        async with observed_file_lock_async(
            lock_path=db_path.with_name(db_path.name + ".lock"),
            resource_id=f"canvas.pins:{_safe(user_id)}",
            operation="canvas.pins.index.clear",
            wait_seconds=30,
        ):
            removed = await index.clear_board(board_id=board_id)
            await index.ensure_built()
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "board": board_id, "error": str(exc)}
    return {"ok": True, "user_id": user_id, "story_id": story_id, "board": board_id, "removed": removed}


async def search_pins(
    *,
    store: Any,
    user_id: str,
    story_id: str,
    payload: Mapping[str, Any],
    embed_fn: EmbedFn,
    dim: int = DEFAULT_EMBEDDING_DIM,
    semantic_guard: Optional[Any] = None,
    vector_backend: str = "faiss-local",
    vector_store: Optional[VectorStore] = None,
) -> dict:
    """Read-only hybrid search over a user's already-indexed pins (indexing happens
    on canvas updates via `index_pins`). Embeds only the query — gated by
    `semantic_guard`; on denial the search degrades to lexical + recency.
    `payload`: {query, limit?, canvas_name?/canvas_id?, all_boards?, kinds?, namespaces?}."""
    query = str(payload.get("query") or "").strip()
    try:
        limit = int(payload.get("limit") or 20)
    except Exception:
        limit = 20
    all_boards = bool(payload.get("all_boards"))
    kinds = payload.get("kinds") if isinstance(payload.get("kinds"), (list, tuple)) else None
    namespaces = payload.get("namespaces") if isinstance(payload.get("namespaces"), (list, tuple)) else None
    board_id = _resolve_board(store, payload)

    db_path = pin_index_db_path(store, user_id)
    index = _build_index(
        store, user_id, embed_fn=embed_fn, dim=dim,
        vector_backend=vector_backend, vector_store=vector_store, semantic_guard=semantic_guard,
    )

    # Self-heal: if this board has no indexed docs (never indexed — e.g. pins
    # predate the indexing wiring, or a fresh process), build it now from the live
    # cards. Embed-on-write means only un-embedded cards cost an embed, so this is
    # a one-time cost; subsequent searches skip it. Bounds the "search finds
    # nothing because nothing was ever indexed" failure.
    try:
        present = index.index.ids(filters=None if all_boards else {"board": board_id})
        if not present:
            cards = _read_board_cards(store, board_id=board_id, story_id=story_id, payload=payload)
            async with observed_file_lock_async(
                lock_path=db_path.with_name(db_path.name + ".lock"),
                resource_id=f"canvas.pins:{_safe(user_id)}",
                operation="canvas.pins.index.lazy",
                wait_seconds=30,
            ):
                await index.sync_board(cards, board_id=board_id)
                await index.ensure_built()
            logger.info("[canvas.pins.search] lazy-built board=%s cards=%s docs_total=%s db=%s",
                        board_id, len(cards), index.index.count(), db_path)
    except Exception:
        logger.warning("[canvas.pins.search] lazy build failed board=%s db=%s", board_id, db_path, exc_info=True)

    try:
        hits = await index.search(
            query,
            top_k=limit,
            board_id=None if all_boards else board_id,
            kinds=kinds,
            namespaces=namespaces,
        )
    except Exception as exc:
        logger.warning("[canvas.pins.search] failed q=%r board=%s error=%s", query, board_id, exc, exc_info=True)
        return {"ok": False, "user_id": user_id, "story_id": story_id, "query": query, "error": str(exc)}
    logger.info("[canvas.pins.search] q=%r scope=%s docs_total=%s results=%s db=%s",
                query, "all_boards" if all_boards else board_id, index.index.count(), len(hits), db_path)

    items = [
        {
            "card_id": h.metadata.get("card_id"),
            "kind": h.metadata.get("kind"),
            "title": h.metadata.get("label") or h.metadata.get("title"),
            "mime": h.metadata.get("mime") or "",
            "logical_path": h.metadata.get("ref"),
            "namespace": h.metadata.get("namespace") or "",
            "selected": bool(h.metadata.get("selected")),
            "placement": h.metadata.get("placement") or "floating",
            "ref": h.metadata.get("ref"),
            "label": h.metadata.get("label") or h.metadata.get("title"),
            "board": h.metadata.get("board"),
            "score": round(float(h.score), 6),
        }
        for h in hits
    ]
    return {
        "ok": True,
        "user_id": user_id,
        "story_id": story_id,
        "query": query,
        "scope": "all_boards" if all_boards else board_id,
        "items": items,
        "results": items,
        "count": len(items),
        "note": "Hybrid canvas pin search over indexed card snapshots.",
    }

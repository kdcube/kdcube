# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Hybrid (semantic + lexical + recency) search over a user's pin-board cards.

Thin canvas-side adapter over the generic `infra.index.sqlite.HybridIndex`:
maps pin cards → Documents, keeps the index in sync with a board's live cards
(diff: upsert changed/new, delete removed), and searches. Per-user scope; boards
are distinguished by the `board` metadata filter so you can search one board or
all of a user's boards.

The caller supplies the embedder (`model_service.embed_texts`) and a per-user
db path; the vector backend defaults to the pure-python BruteForceVectorStore
(no faiss needed at per-user scale) and can be swapped for LocalFaissStore /
CachedFaissStore at scale with no other change.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence

from kdcube_ai_app.infra.index.sqlite import (
    BruteForceVectorStore,
    Document,
    EmbedFn,
    HybridIndex,
    IndexConfig,
    SearchHit,
    VectorStore,
)


def _s(value: Any) -> str:
    return str(value or "").strip()


def _parse_ts(card: Mapping[str, Any]) -> float | None:
    for key in ("updated_at", "created_at", "ts"):
        v = card.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v)
        s = _s(v)
        if not s:
            continue
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return None


def _namespace_for_ref(ref: str) -> str:
    match = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*):", str(ref or "").strip())
    return (match.group(1).lower() if match else "")


def card_text(card: Mapping[str, Any]) -> str:
    """Compose the searchable blob from a pin card: label/title + description +
    comments + kind + ref. Defensive against varying card shapes."""
    bits: List[str] = []
    label = ""
    for key in ("label", "map_label", "title", "name"):
        v = _s(card.get(key))
        if v:
            label = v
            break
    if label:
        bits.append(label)
    title = _s(card.get("title"))
    if title and title != label:
        bits.append(title)
    bits.append(_s(card.get("description")))
    bits.append(_s(card.get("content_preview")))
    bits.append(_s(card.get("mime")))
    for comment in (card.get("comments") or []):
        bits.append(_s(comment.get("text") or comment.get("body")) if isinstance(comment, Mapping) else _s(comment))
    bits.append(_s(card.get("kind")))
    bits.append(_s(card.get("logical_path") or card.get("ref") or card.get("storage_ref")))
    return re.sub(r"[ \t]+", " ", "\n".join(b for b in bits if b)).strip()


def card_to_document(card: Mapping[str, Any], *, board_id: str) -> Document:
    card_id = _s(card.get("id") or card.get("card_id"))
    ref = _s(card.get("logical_path") or card.get("ref") or card.get("storage_ref"))
    placement = _s(card.get("placement") or "floating") or "floating"
    return Document(
        id=f"{board_id}:{card_id}",  # cross-board-unique within the user scope
        text=card_text(card),
        metadata={
            "board": board_id,
            "card_id": card_id,
            "kind": _s(card.get("kind")) or "note",
            "mime": _s(card.get("mime")),
            "namespace": _namespace_for_ref(ref),
            "placement": placement,
            "selected": bool(card.get("selected")),
            "ref": ref,
            "label": _s(card.get("label") or card.get("title") or card.get("name")),
            "title": _s(card.get("title")),
        },
        timestamp=_parse_ts(card),
    )


class PinSearchIndex:
    """Per-user hybrid pin search on top of HybridIndex."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        embed_fn: EmbedFn,
        dim: int,
        vector_store: VectorStore | None = None,
        **index_kwargs: Any,
    ) -> None:
        self.index = HybridIndex(IndexConfig(
            db_path=Path(db_path),
            embed_fn=embed_fn,
            dim=dim,
            vector_store=vector_store or BruteForceVectorStore(),
            **index_kwargs,
        ))

    async def sync_board(self, cards: Iterable[Mapping[str, Any]], *, board_id: str) -> None:
        """Make the index reflect a board's current cards: upsert (changed/new
        re-embed only) and delete cards no longer present on the board."""
        docs = [
            card_to_document(c, board_id=board_id)
            for c in cards
            if _s(c.get("id") or c.get("card_id"))
            and not bool(c.get("trashed"))
            and _s(c.get("placement") or "floating") != "trashed"
        ]
        await self.index.upsert(docs)
        present = {d.id for d in docs}
        indexed = set(self.index.ids(filters={"board": board_id}))
        removed = indexed - present
        if removed:
            await self.index.delete(removed)

    async def clear_board(self, *, board_id: str) -> int:
        indexed = set(self.index.ids(filters={"board": board_id}))
        if indexed:
            await self.index.delete(indexed)
        return len(indexed)

    async def ensure_built(self) -> None:
        """(Re)build the vector index if stale — call inside the write lock so the
        rebuild (which may write the vector file) is serialized with sync."""
        await self.index.ensure_built()

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        board_id: str | None = None,
        kinds: Sequence[str] | None = None,
        namespaces: Sequence[str] | None = None,
    ) -> List[SearchHit]:
        filters = {"board": board_id} if board_id else None
        # The infra filter is single-equality; for kind/namespace sets we
        # over-fetch and post-filter (the sets are small).
        over = top_k * 3 if (kinds or namespaces) else top_k
        hits = await self.index.search(query, top_k=over, filters=filters)
        if kinds:
            allowed = set(kinds)
            hits = [h for h in hits if h.metadata.get("kind") in allowed]
        if namespaces:
            allowed_namespaces = {
                str(ns).strip().lower().rstrip(":")
                for ns in namespaces
                if str(ns).strip()
            }
            hits = [h for h in hits if str(h.metadata.get("namespace") or "") in allowed_namespaces]
        return hits[:top_k]

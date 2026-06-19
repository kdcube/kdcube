# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""search_pins op test — fake CanvasStore + fake embedder, pure-python backend.
Covers board-scoped hybrid search, the result shape, and the economics guard
degrading to lexical."""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import CANVAS_PIN_SEARCH_FILTERS, index_pins, search_pins

VOCAB = ["rollout", "plan", "deploy", "alpha", "prod", "beta", "gamma", "report", "task", "note"]


async def fake_embed(texts):
    return [[float(re.findall(r"[a-z0-9]+", str(t).lower()).count(w)) for w in VOCAB] for t in texts]


class FakeStore:
    def __init__(self, root: Path, cards):
        self.storage_root = root
        self._cards = cards

    def canvas_name(self, name):
        return name or "default"

    def canvas_id(self, *, canvas_name, canvas_id):
        return canvas_id or f"cnv:{canvas_name}"

    def read_document(self, *, canvas_id, canvas_name):
        return (None, {"cards": self._cards})


async def _run() -> None:
    cards = [
        {"id": "p1", "label": "Rollout plan", "description": "deploy alpha to prod",
         "kind": "canvas", "logical_path": "cnv:u/b1/p1"},
        {"id": "p2", "label": "Beta notes", "description": "gamma report",
         "kind": "note", "logical_path": "task:issue/42"},
    ]
    with tempfile.TemporaryDirectory() as d:
        store = FakeStore(Path(d), cards)

        # indexing happens on a canvas update (under the observed lock)
        idx = await index_pins(
            store=store, user_id="u-1",
            payload={"canvas_id": "b1"}, embed_fn=fake_embed, dim=len(VOCAB),
            vector_backend="bruteforce",
        )
        assert idx["ok"] is True and idx["indexed"] == 2, idx

        # search is read-only — does not re-index
        res = await search_pins(
            store=store, user_id="u-1",
            payload={"query": "alpha deploy", "canvas_id": "b1", "limit": 10},
            embed_fn=fake_embed, dim=len(VOCAB), vector_backend="bruteforce",
        )
        assert res["ok"] is True, res
        ids = [r["card_id"] for r in res["results"]]
        # Regression for the "search matched every pin" bug (e.g. "hello" → 14/14):
        # the semantic floor must keep the unrelated pin (p2) OUT, not return the
        # whole board reordered.
        assert ids == ["p1"], f"floor should drop the non-matching pin p2: {ids}"
        assert res["results"][0]["ref"] == "cnv:u/b1/p1"   # native ref carried through
        assert res["scope"] == "b1"

        # A query unrelated to every card returns NOTHING (not the whole board).
        res_none = await search_pins(
            store=store, user_id="u-1",
            payload={"query": "xylophone serendipity", "canvas_id": "b1", "limit": 10},
            embed_fn=fake_embed, dim=len(VOCAB), vector_backend="bruteforce",
        )
        assert res_none["ok"] is True and res_none["count"] == 0, res_none

        # Semantic factor OFF (min_semantic_score < 0): search runs on lexical +
        # recency only — never calls the embedder — and still returns matches.
        embed_calls = {"n": 0}

        async def counting_embed(texts):
            embed_calls["n"] += 1
            return await fake_embed(texts)

        res_off = await search_pins(
            store=store, user_id="u-1",
            payload={
                "query": "alpha deploy",
                "canvas_id": "b1",
                "limit": 10,
                "thresholds": {"semantic_score": -1},
            },
            embed_fn=counting_embed, dim=len(VOCAB), vector_backend="bruteforce",
        )
        assert res_off["ok"] is True and [r["card_id"] for r in res_off["results"]] == ["p1"], res_off
        assert embed_calls["n"] == 0, "semantic-off must not call the embedder for the query"
        assert "thresholds" in CANVAS_PIN_SEARCH_FILTERS
        assert "semantic_score" in CANVAS_PIN_SEARCH_FILTERS["thresholds"]["properties"]
        # An unrelated query still returns nothing (lexical has its own boundary).
        res_off_none = await search_pins(
            store=store, user_id="u-1",
            payload={"query": "xylophone serendipity", "canvas_id": "b1", "limit": 10},
            embed_fn=counting_embed, dim=len(VOCAB), vector_backend="bruteforce",
            min_semantic_score=-1,
        )
        assert res_off_none["ok"] is True and res_off_none["count"] == 0, res_off_none

        # economics guard denies → degrades to lexical (no exception, still returns)
        async def deny(_q):
            return False
        res2 = await search_pins(
            store=store, user_id="u-1",
            payload={"query": "alpha", "canvas_id": "b1"},
            embed_fn=fake_embed, dim=len(VOCAB), semantic_guard=deny,
            vector_backend="bruteforce",
        )
        assert res2["ok"] is True and [r["card_id"] for r in res2["results"]] == ["p1"], res2

    print("test_pin_search: ALL PASS")


def test_pin_search():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())

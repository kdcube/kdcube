# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""faiss backend tests — exercises the real faiss module.

1. LocalFaissStore directly: rebuild/search round-trip + on-disk persistence.
2. The generic HybridIndex *using* the faiss backend (the wider usage): the
   indexer writes the faiss file and semantic search returns the right doc.
3. The canvas pin search on the faiss backend end-to-end.

Skipped automatically where faiss + numpy are not installed."""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("faiss")
pytest.importorskip("numpy")

from kdcube_ai_app.infra.index.faiss import LocalFaissStore
from kdcube_ai_app.infra.index.sqlite import Document, HybridIndex, IndexConfig

VOCAB = ["rollout", "plan", "deploy", "alpha", "prod", "beta", "gamma", "report", "q3", "note"]


async def fake_embed(texts):
    return [[float(re.findall(r"[a-z0-9]+", str(t).lower()).count(w)) for w in VOCAB] for t in texts]


def test_local_faiss_store_roundtrip_and_persistence():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "x.faiss"
        store = LocalFaissStore(path)
        store.rebuild([(1, [1.0, 0.0, 0.0]), (2, [0.0, 1.0, 0.0]), (3, [0.9, 0.1, 0.0])], dim=3)

        hits = store.search([1.0, 0.0, 0.0], top_k=2)
        ids = [i for i, _ in hits]
        assert ids[0] == 1 and 3 in ids, hits        # nearest by cosine
        assert path.exists()                         # persisted to disk

        # a fresh instance reads the same file (survives process restart)
        reopened = LocalFaissStore(path)
        again = reopened.search([0.0, 1.0, 0.0], top_k=1)
        assert again and again[0][0] == 2, again

        store.reset()
        assert not path.exists()


def test_hybrid_index_with_faiss_backend():
    """The indexer (HybridIndex) using LocalFaissStore — writes a faiss file and
    semantic search ranks the matching doc first."""
    async def run():
        with tempfile.TemporaryDirectory() as d:
            faiss_path = Path(d) / "idx.faiss"
            idx = HybridIndex(IndexConfig(
                db_path=Path(d) / "idx.sqlite",
                embed_fn=fake_embed,
                dim=len(VOCAB),
                vector_store=LocalFaissStore(faiss_path),
            ))
            await idx.upsert([
                Document(id="a", text="rollout plan deploy alpha to prod"),
                Document(id="b", text="beta gamma report"),
                Document(id="c", text="q3 report note"),
            ])
            await idx.ensure_built()
            assert faiss_path.exists(), "indexer did not write the faiss file"

            hits = await idx.search("rollout plan deploy", top_k=3, mode="semantic")
            assert hits and hits[0].id == "a", [(h.id, round(h.score, 4)) for h in hits]

            # persistence: a new index instance over the same files still searches
            idx2 = HybridIndex(IndexConfig(
                db_path=Path(d) / "idx.sqlite",
                embed_fn=fake_embed,
                dim=len(VOCAB),
                vector_store=LocalFaissStore(faiss_path),
            ))
            hits2 = await idx2.search("beta gamma", top_k=2, mode="semantic")
            assert hits2 and hits2[0].id == "b", [(h.id, round(h.score, 4)) for h in hits2]

    asyncio.run(run())


def test_canvas_pin_search_faiss_backend():
    """Canvas pin index/search on vector_backend='faiss-local' — a pins.index.faiss
    file is produced next to the per-user SQLite, and search returns the pin."""
    from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import index_pins, search_pins

    class FakeStore:
        def __init__(self, root, cards):
            self.storage_root = root
            self._cards = cards

        def canvas_name(self, name):
            return name or "default"

        def canvas_id(self, *, canvas_name, canvas_id):
            return canvas_id or f"cnv:{canvas_name}"

        def read_document(self, *, canvas_id, story_id, canvas_name):
            return (None, {"cards": self._cards})

    async def run():
        cards = [
            {"id": "p1", "label": "Rollout plan", "description": "deploy alpha to prod",
             "kind": "canvas", "logical_path": "cnv:u/b1/p1"},
            {"id": "p2", "label": "Beta notes", "description": "gamma report",
             "kind": "note", "logical_path": "task:issue/42"},
        ]
        with tempfile.TemporaryDirectory() as d:
            store = FakeStore(Path(d), cards)
            idx = await index_pins(
                store=store, user_id="u-1", story_id="s-1",
                payload={"canvas_id": "b1"}, embed_fn=fake_embed, dim=len(VOCAB),
                vector_backend="faiss-local",
            )
            assert idx["ok"] is True and idx["indexed"] == 2, idx
            faiss_file = Path(d) / ".pin-index" / "u-1" / "pins.index.faiss"
            assert faiss_file.exists(), f"expected faiss file at {faiss_file}"

            res = await search_pins(
                store=store, user_id="u-1", story_id="s-1",
                payload={"query": "alpha deploy", "canvas_id": "b1", "limit": 10},
                embed_fn=fake_embed, dim=len(VOCAB), vector_backend="faiss-local",
            )
            assert res["ok"] is True and [r["card_id"] for r in res["results"]][0] == "p1", res

    asyncio.run(run())

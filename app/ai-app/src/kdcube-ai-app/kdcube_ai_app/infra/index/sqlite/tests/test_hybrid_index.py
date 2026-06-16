# SPDX-License-Identifier: MIT
"""Focused tests for the hybrid index using a deterministic fake embedder and the
pure-python BruteForceVectorStore (so they run without faiss/numpy/network)."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from kdcube_ai_app.infra.index.sqlite import (
    BruteForceVectorStore,
    Document,
    HybridIndex,
    IndexConfig,
)

# Tiny bag-of-words embedder: vector = per-vocab-word counts. Lexically similar
# texts get similar vectors, so semantic ranking is deterministic and testable.
VOCAB = ["alpha", "beta", "gamma", "delta", "zeta", "eta"]


async def fake_embed(texts):
    out = []
    for t in texts:
        toks = str(t).lower().split()
        out.append([float(toks.count(w)) for w in VOCAB])
    return out


def _index(tmp: Path) -> HybridIndex:
    return HybridIndex(IndexConfig(
        db_path=tmp / "idx.sqlite",
        embed_fn=fake_embed,
        dim=len(VOCAB),
        vector_store=BruteForceVectorStore(),
        overfetch=5,
    ))


async def _seed(idx: HybridIndex) -> None:
    now = time.time()
    await idx.upsert([
        Document(id="d1", text="alpha beta gamma", metadata={"kind": "note"}, timestamp=now - 86400 * 10),
        Document(id="d2", text="beta gamma delta", metadata={"kind": "note"}, timestamp=now - 86400 * 5),
        Document(id="d3", text="alpha alpha", metadata={"kind": "task"}, timestamp=now),
        Document(id="d4", text="zeta eta", metadata={"kind": "note"}, timestamp=now - 86400 * 1),
    ])


async def _run_all() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        idx = _index(tmp)
        await _seed(idx)
        assert idx.count() == 4

        # hybrid: "alpha" docs (d1, d3) rank above the alpha-less d4
        hits = await idx.search("alpha", top_k=4)
        ids = [h.id for h in hits]
        assert ids[:2] == sorted(ids[:2]) or set(ids[:2]) == {"d1", "d3"}, ids
        assert set(ids[:2]) == {"d1", "d3"}, f"expected alpha docs on top, got {ids}"
        assert "d4" not in ids[:2]

        # lexical-only: only docs containing the term match
        lex = await idx.search("delta", top_k=10, mode="lexical")
        assert [h.id for h in lex] == ["d2"], lex

        # semantic-only still works (vector store built lazily)
        sem = await idx.search("alpha", top_k=2, mode="semantic")
        assert sem and sem[0].id in {"d1", "d3"}

        # metadata filter
        tasks = await idx.search("alpha", top_k=10, filters={"kind": "task"})
        assert [h.id for h in tasks] == ["d3"], tasks

        # sub-scores present (telemetry)
        assert hits[0].sub  # has *_rank entries

        # update changes ranking input + delete removes
        await idx.upsert([Document(id="d4", text="alpha alpha alpha", metadata={"kind": "note"})])
        hits2 = await idx.search("alpha", top_k=1)
        assert hits2[0].id == "d4", hits2  # now the strongest alpha match

        await idx.delete(["d4"])
        assert idx.count() == 3
        after = await idx.search("alpha", top_k=10)
        assert "d4" not in [h.id for h in after]

    print("test_hybrid_index: ALL PASS")


async def _run_guard() -> None:
    """Economical guard: short / disabled / budget-denied queries must not embed;
    repeated queries hit the cache; guard-denied degrades to lexical."""
    calls = {"n": 0}

    async def counting_embed(texts):
        calls["n"] += len(texts)
        return await fake_embed(texts)

    with tempfile.TemporaryDirectory() as d:
        from kdcube_ai_app.infra.index.sqlite import HybridIndex, IndexConfig, Document, BruteForceVectorStore
        idx = HybridIndex(IndexConfig(
            db_path=Path(d) / "g.sqlite", embed_fn=counting_embed, dim=len(VOCAB),
            vector_store=BruteForceVectorStore(), semantic_min_chars=3,
        ))
        await idx.upsert([Document(id="d1", text="alpha beta")])
        base = calls["n"]                              # embedded the doc once

        await idx.search("al", top_k=5)                # 2 chars < 3 → no embed
        assert calls["n"] == base, "short query must not embed"

        r = await idx.search("alpha", top_k=5)         # >= 3 → embeds once
        assert calls["n"] == base + 1 and r[0].id == "d1"

        await idx.search("alpha", top_k=5)             # repeat → cache hit
        assert calls["n"] == base + 1, "repeat query must hit cache"

        idx.cfg.semantic_guard = lambda q: False       # budget says no (sync)
        before = calls["n"]
        r2 = await idx.search("alpha", top_k=5, mode="semantic")  # degrade to lexical
        assert calls["n"] == before, "guard-denied must not embed"
        assert r2 and r2[0].id == "d1", "lexical fallback still returns"

        async def deny(_q):                            # async guard (e.g. economic_preflight)
            return False
        idx.cfg.semantic_guard = deny
        before2 = calls["n"]
        await idx.search("alpha", top_k=5)
        assert calls["n"] == before2, "async guard-denied must not embed"

    print("test_guard: ALL PASS")


def test_hybrid_index():
    asyncio.run(_run_all())


def test_guard():
    asyncio.run(_run_guard())


if __name__ == "__main__":
    asyncio.run(_run_all())
    asyncio.run(_run_guard())

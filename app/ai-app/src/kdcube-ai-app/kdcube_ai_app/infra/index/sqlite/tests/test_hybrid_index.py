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


async def _run_model_service_query_embed() -> None:
    calls = {"doc": 0, "query": 0}

    async def doc_embed(texts):
        calls["doc"] += len(texts)
        return await fake_embed(texts)

    class _ModelService:
        async def embed_texts(self, texts):
            return await doc_embed(texts)

        async def embed_search_query(self, query: str, *, flow: str | None = None):
            del flow
            calls["query"] += 1
            return (await fake_embed([query]))[0]

    with tempfile.TemporaryDirectory() as d:
        idx = HybridIndex(IndexConfig(
            db_path=Path(d) / "q.sqlite",
            embed_fn=doc_embed,
            model_service=_ModelService(),
            dim=len(VOCAB),
            vector_store=BruteForceVectorStore(),
        ))
        await idx.upsert([Document(id="d1", text="alpha beta")])
        assert calls == {"doc": 1, "query": 0}

        await idx.search("alpha", top_k=5)
        assert calls == {"doc": 1, "query": 1}

    print("test_model_service_query_embed: ALL PASS")


async def _run_all_terms_first() -> None:
    """A multi-word query returns the docs matching EVERY word when any exist —
    not the whole corpus reordered (previously each doc sharing one common
    word with the query matched). An unmatchable strict pass widens to
    any-term so the search still answers."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        idx = _index(tmp)
        await _seed(idx)

        # strict pass: only d2 contains BOTH beta and delta
        hits = await idx.search("beta delta", top_k=10, mode="lexical")
        assert sorted(h.id for h in hits) == ["d2"], [h.id for h in hits]

        # widening: no doc has both delta and zeta -> any-term answers (d2, d4)
        hits = await idx.search("delta zeta", top_k=10, mode="lexical")
        assert sorted(h.id for h in hits) == ["d2", "d4"], [h.id for h in hits]

        # opt-out restores the historical any-term behavior
        loose = HybridIndex(IndexConfig(
            db_path=tmp / "idx.sqlite",
            embed_fn=fake_embed,
            dim=len(VOCAB),
            vector_store=BruteForceVectorStore(),
            lexical_all_terms_first=False,
        ))
        hits = await loose.search("beta delta", top_k=10, mode="lexical")
        assert sorted(h.id for h in hits) == ["d1", "d2"], [h.id for h in hits]

    print("test_all_terms_first: ALL PASS")


def test_hybrid_index():
    asyncio.run(_run_all())


def test_all_terms_first():
    asyncio.run(_run_all_terms_first())


def test_guard():
    asyncio.run(_run_guard())


def test_model_service_query_embed():
    asyncio.run(_run_model_service_query_embed())


async def _run_poisoned_doc_survival():
    """One doc whose embedding fails must not sink the batch: the others
    index fully, the failed doc stays lexically findable with no stale
    vector, and the missing vector re-queues its embed on the next upsert."""
    calls = {"n": 0}
    poison = {"active": True}

    async def flaky_embed(texts):
        calls["n"] += 1
        if poison["active"] and any("POISON" in t for t in texts):
            raise RuntimeError("provider rejected input")
        return await fake_embed(texts)

    with tempfile.TemporaryDirectory() as tmp:
        idx = HybridIndex(IndexConfig(
            db_path=Path(tmp) / "idx.sqlite",
            embed_fn=flaky_embed,
            dim=len(VOCAB),
            vector_store=BruteForceVectorStore(),
            overfetch=5,
        ))
        now = time.time()
        await idx.upsert([
            Document(id="ok1", text="alpha beta", metadata={}, timestamp=now),
            Document(id="bad", text="POISON zeta eta", metadata={}, timestamp=now),
            Document(id="ok2", text="gamma delta", metadata={}, timestamp=now),
        ])
        # Healthy docs answer semantically; the poisoned one is not indexed
        # as a vector but still answers lexically.
        sem = await idx.search("alpha beta", top_k=5, mode="semantic")
        assert {h.id for h in sem} >= {"ok1"}
        assert all(h.id != "bad" for h in sem)
        lex = await idx.search("zeta eta", top_k=5, mode="lexical")
        assert any(h.id == "bad" for h in lex), "failed-embed doc lost from lexical search"

        # Same content upserted again once the provider recovers: the
        # vector-less doc re-embeds even though its text is unchanged.
        poison["active"] = False
        await idx.upsert([
            Document(id="bad", text="POISON zeta eta", metadata={}, timestamp=now),
        ])
        sem2 = await idx.search("zeta eta", top_k=5, mode="semantic")
        assert any(h.id == "bad" for h in sem2), "recovered doc still missing from semantic search"


def test_poisoned_doc_survival():
    asyncio.run(_run_poisoned_doc_survival())


if __name__ == "__main__":
    asyncio.run(_run_all())
    asyncio.run(_run_guard())
    asyncio.run(_run_model_service_query_embed())

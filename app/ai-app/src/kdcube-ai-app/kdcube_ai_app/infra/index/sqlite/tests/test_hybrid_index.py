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
    guard-denied degrades to lexical."""
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

        # A repeat with NO shared cache configured embeds again — deliberately.
        # There is no process-local memo: this runtime is distributed, so the
        # only cache that means anything is the shared one (opt-in, see the
        # query-embedding-cache tests below).
        await idx.search("alpha", top_k=5)
        assert calls["n"] == base + 2, "without a shared cache, a repeat re-embeds"

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


# ── the shared query-embedding cache (opt-in) ──────────────────────────


class _CountingEmbedder:
    """fake_embed, but it says how many times it was asked."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        return await fake_embed(texts)


class _FakeSharedCache:
    """A QueryEmbeddingCache stand-in: the same two awaited methods, in memory,
    so the test does not need Redis."""

    def __init__(self, store: dict | None = None, *, broken: bool = False):
        self.store = store if store is not None else {}
        self.broken = broken
        self.reads = 0
        self.writes = 0

    async def get(self, query):
        self.reads += 1
        if self.broken:
            raise RuntimeError("redis is having a day")
        return self.store.get(query)

    async def set(self, query, vector):
        self.writes += 1
        if self.broken:
            raise RuntimeError("redis is having a day")
        self.store[query] = list(vector)
        return True


def _index_with_cache(tmp: Path, embed, cache, *, name="idx.sqlite") -> HybridIndex:
    return HybridIndex(IndexConfig(
        db_path=tmp / name,
        embed_fn=embed,
        dim=len(VOCAB),
        vector_store=BruteForceVectorStore(),
        overfetch=5,
        query_embedding_cache=cache,
    ))


def test_a_second_process_reuses_the_shared_query_vector():
    """The point of the shared tier: a NEW index instance (another worker, a
    later turn) must not re-pay the embedder for a query somebody already
    embedded. The in-process LRU cannot do this — it died with its process."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shared = _FakeSharedCache()

        first_embed = _CountingEmbedder()
        idx = _index_with_cache(tmp, first_embed, shared)
        asyncio.run(idx.upsert([
            Document(id="1", text="alpha beta"),
            Document(id="2", text="gamma delta"),
        ]))
        asyncio.run(idx.search("alpha", top_k=2))
        upsert_and_query = first_embed.calls
        assert shared.writes == 1, "the query vector should have been written once"

        # A brand-new instance over the same data: the query is NOT re-embedded.
        second_embed = _CountingEmbedder()
        idx2 = _index_with_cache(tmp, second_embed, shared, name="idx.sqlite")
        asyncio.run(idx2.search("alpha", top_k=2))
        assert second_embed.calls == 0, "the shared cache should have answered"
        assert upsert_and_query >= 1


def test_every_repeat_goes_through_the_shared_cache():
    """There is NO process-local memo by design: this runtime is distributed, so
    a repeat asks the shared cache (which answers) rather than a dictionary only
    this worker can see."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shared = _FakeSharedCache()
        embed = _CountingEmbedder()
        idx = _index_with_cache(tmp, embed, shared)
        asyncio.run(idx.upsert([Document(id="1", text="alpha beta")]))
        asyncio.run(idx.search("alpha", top_k=1))
        embeds_after_first = embed.calls
        asyncio.run(idx.search("alpha", top_k=1))
        assert shared.reads == 2, "the repeat must consult the shared cache"
        assert embed.calls == embeds_after_first, "and must not re-embed"


def test_a_broken_cache_costs_an_embed_call_not_the_search():
    """Fail soft, both directions: a cache that raises on read and on write must
    not surface as a failed search."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        embed = _CountingEmbedder()
        idx = _index_with_cache(tmp, embed, _FakeSharedCache(broken=True))
        asyncio.run(idx.upsert([Document(id="1", text="alpha beta")]))
        hits = asyncio.run(idx.search("alpha", top_k=1))
        assert [h.id for h in hits] == ["1"]


def test_no_cache_configured_changes_nothing():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        idx = _index(Path(td))
        asyncio.run(idx.upsert([Document(id="1", text="alpha beta")]))
        assert [h.id for h in asyncio.run(idx.search("alpha", top_k=1))] == ["1"]


def test_the_key_carries_the_model_and_the_width():
    """A vector from another model is not a cheaper answer, it is a wrong one —
    so the key must separate them, and a stored vector of the wrong width is
    refused rather than returned."""
    from kdcube_ai_app.infra.index.embedding_cache import (
        QueryEmbeddingCache, normalize_query, query_embedding_key,
    )

    assert normalize_query("  Keep   the Agent ") == "keep the agent"
    small = query_embedding_key("keep the agent", model="m1", dim=6)
    other_model = query_embedding_key("keep the agent", model="m2", dim=6)
    other_dim = query_embedding_key("keep the agent", model="m1", dim=1536)
    assert small != other_model and small != other_dim
    # spacing/case do not make a new key
    assert query_embedding_key("KEEP  the agent", model="m1", dim=6) == small

    class _KV:
        def __init__(self): self.data = {}
        async def get(self, key): return self.data.get(key)
        async def set(self, key, value, ttl_seconds=None): self.data[key] = value; return True

    kv = _KV()
    cache = QueryEmbeddingCache(kv, model="m1", dim=6)
    assert asyncio.run(cache.set("alpha", [1.0] * 6)) is True
    assert asyncio.run(cache.get("alpha")) == [1.0] * 6
    # a vector of the wrong width never comes back
    wrong = QueryEmbeddingCache(kv, model="m1", dim=1536)
    assert asyncio.run(wrong.get("alpha")) is None
    assert cache.stats()["hits"] == 1


def test_vectors_are_stored_compactly_and_the_scope_is_bounded():
    """Two properties a shared vector cache must have: it stores the vector in
    a form that is not four times bigger than it needs to be, and it has a
    CEILING — 1536 float32 values are ~8KB each, so an unbounded cache is a
    memory leak that happens to answer questions. The bound is a Redis sorted
    set of this scope's keys scored by last USE, so eviction takes the coldest,
    not the oldest."""
    import base64
    from kdcube_ai_app.infra.index.embedding_cache import (
        QueryEmbeddingCache, decode_vector, encode_vector,
    )

    vec = [0.0123456] * 8
    packed = encode_vector(vec)
    assert len(packed) < len(str(vec)), "the packed form must be smaller than the text form"
    roundtrip = decode_vector(packed)
    assert len(roundtrip) == 8
    assert all(abs(a - b) < 1e-6 for a, b in zip(roundtrip, vec)), "float32 round-trip"
    # a legacy JSON array still decodes (the cache predates the codec)
    assert decode_vector([1.0, 2.0]) == [1.0, 2.0]
    assert decode_vector("not base64 at all!!") is None

    class _Redis:
        def __init__(self): self.z = {}; self.deleted = []
        async def zadd(self, key, mapping): self.z.setdefault(key, {}).update(mapping); return 1
        async def expire(self, key, ttl): return True
        async def zcard(self, key): return len(self.z.get(key, {}))
        async def zrange(self, key, start, stop):
            ordered = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
            return [k for k, _ in ordered[start:stop + 1]]
        async def delete(self, *keys): self.deleted.extend(keys); return len(keys)
        async def zrem(self, key, *members):
            for m in members: self.z.get(key, {}).pop(m, None)
            return len(members)

    class _KV:
        def __init__(self, redis): self.redis = redis; self.data = {}
        def _key(self, key): return f"ns:{key}"
        async def get(self, key): return self.data.get(self._key(key))
        async def set(self, key, value, ttl_seconds=None): self.data[self._key(key)] = value; return True

    redis = _Redis()
    cache = QueryEmbeddingCache(_KV(redis), model="m1", dim=8, max_entries=2)
    for query in ("first", "second", "third"):
        assert asyncio.run(cache.set(query, vec)) is True
    assert redis.deleted, "the third write must have evicted the coldest entry"
    assert cache.stats()["evictions"] == len(redis.deleted)
    assert cache.stats()["max_entries"] == 2
    # what survives still reads back
    assert asyncio.run(cache.get("third")) is not None


def test_a_snippet_points_at_the_matched_terms_only_when_asked():
    """Callers that render results need the passage, not just the score.

    Opt-in because building one costs an extra FTS pass, and a caller listing
    ids does not want to pay for text it will not show.
    """

    with tempfile.TemporaryDirectory() as tmp:
        index = _index(Path(tmp))
        asyncio.run(index.upsert([
            Document(
                id="a",
                text="alpha beta gamma delta zeta eta alpha beta gamma delta",
                metadata={"kind": "note"},
            ),
        ]))

        plain = asyncio.run(index.search("gamma", mode="lexical"))
        assert plain[0].snippet == ""

        marked = asyncio.run(index.search("gamma", mode="lexical", snippets=True))
        assert marked[0].id == "a"
        assert "[gamma]" in marked[0].snippet


def test_snippet_markers_are_configurable_for_the_caller_that_renders():
    with tempfile.TemporaryDirectory() as tmp:
        index = HybridIndex(IndexConfig(
            db_path=Path(tmp) / "idx.sqlite",
            embed_fn=fake_embed,
            dim=len(VOCAB),
            vector_store=BruteForceVectorStore(),
            snippet_open="<b>",
            snippet_close="</b>",
        ))
        asyncio.run(index.upsert([Document(id="a", text="alpha beta gamma")]))

        hit = asyncio.run(index.search("beta", mode="lexical", snippets=True))[0]

        assert "<b>beta</b>" in hit.snippet


def test_a_hit_the_lexical_arm_never_matched_carries_no_snippet():
    """A semantic-only hit has no matched terms to point at.

    Returning a leading fragment would suggest the query appears in text where
    it does not, which is worse than returning nothing.
    """

    with tempfile.TemporaryDirectory() as tmp:
        index = _index(Path(tmp))
        asyncio.run(index.upsert([
            Document(id="a", text="alpha alpha alpha"),
            Document(id="b", text="zeta zeta zeta"),
        ]))

        hits = asyncio.run(index.search("alpha", mode="semantic", snippets=True))

        assert hits
        assert all(h.snippet == "" for h in hits if h.id == "b")


def test_a_lexical_only_collection_never_calls_the_embedder():
    """Switching the semantic factor off should stop paying for vectors.

    Search already skipped the semantic arm, but upsert still embedded every
    new or changed document, so a lexical-only collection paid an embedder call
    per write for a column nothing would read.
    """

    calls: list[int] = []

    async def counting_embed(texts):
        calls.append(len(list(texts)))
        return [[0.0] * len(VOCAB) for _ in texts]

    with tempfile.TemporaryDirectory() as tmp:
        index = HybridIndex(IndexConfig(
            db_path=Path(tmp) / "idx.sqlite",
            embed_fn=counting_embed,
            dim=len(VOCAB),
            vector_store=BruteForceVectorStore(),
            semantic_enabled=False,
        ))

        asyncio.run(index.upsert([
            Document(id="a", text="alpha beta", metadata={"kind": "note"}),
            Document(id="b", text="gamma delta"),
        ]))

        assert calls == []

        # Lexical search still works, which is the whole point of the mode.
        hits = asyncio.run(index.search("gamma", snippets=True))
        assert [h.id for h in hits] == ["b"]
        assert "[gamma]" in hits[0].snippet

        # And changed text is still re-indexed lexically.
        asyncio.run(index.upsert([Document(id="b", text="gamma epsilon")]))
        assert calls == []
        assert [h.id for h in asyncio.run(index.search("epsilon"))] == ["b"]


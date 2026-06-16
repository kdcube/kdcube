---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-index-README.md
title: "SQLite + Vector Hybrid Index"
summary: "Reusable per-scope search index that internalizes SQLite lexical (FTS5/bm25), embed-on-write vectors, pluggable vector backends (brute-force / file faiss / cross-process faiss), recency decay, and RRF fusion — so any searchable collection (pins, tasks, memories) gets semantic + lexical + recency + reciprocal-rank search by handing it Documents and a query."
status: active
tags: ["sdk", "solutions", "index", "search", "sqlite", "faiss", "embeddings", "hybrid-search", "fts5"]
keywords:
  [
    "hybrid index",
    "sqlite fts5",
    "bm25",
    "faiss",
    "vector store",
    "embed-on-write",
    "semantic search",
    "lexical search",
    "HybridIndex",
    "IndexConfig",
    "BruteForceVectorStore",
    "LocalFaissStore",
    "CachedFaissStore",
    "ensure_built",
    "per-scope index",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-scoring-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
---
# SQLite + Vector Hybrid Index

`kdcube_ai_app/infra/index/sqlite` is a generic, reusable search index for any
**per-scope collection** — a user's pin boards, a project's issues, a session's
memories. It internalizes the parts every such collection re-invents:

- a SQLite document store (id, text blob, JSON metadata, timestamp),
- **lexical** ranking via SQLite FTS5 / `bm25`,
- **semantic** ranking via embed-on-write vectors over a pluggable vector backend,
- **recency** decay, and
- **fusion** of the three into one ranking (Reciprocal Rank Fusion).

A caller hands it `Document`s and a query string; it returns ranked `SearchHit`s.
It does not know what a pin or an issue is — only `Document(id, text, metadata,
timestamp)`. The scoring half is documented separately in
[Hybrid Scoring](./hybrid-scoring-README.md); this page is the index lifecycle and
the vector backends.

## Why it exists

Pins, tasks, and memories all want "semantic + lexical + recency, reciprocal" search,
but they live in different stores and at different scales. Rather than each bundle
re-deriving FTS5 setup, embed-on-write bookkeeping, faiss build/eval, and fusion, they
share one index and differ only in:

1. how they compose a `Document.text` from their domain object, and
2. which vector backend they pick for their scale.

The canvas pin board is the first adopter (see
[Pin Integration](../canvas/pin-integration-README.md)); the same index serves any
future searchable collection.

## Public surface

```python
from kdcube_ai_app.infra.index.sqlite import (
    HybridIndex, IndexConfig, Document, SearchHit, FusionWeights,
    BruteForceVectorStore, LocalFaissStore, CachedFaissStore,
)

idx = HybridIndex(IndexConfig(
    db_path=Path(".../collection.index.sqlite"),
    embed_fn=model_service.embed_texts,    # async batch embedder: List[str] -> List[List[float]]
    dim=1536,                              # text-embedding-3-small
    vector_store=BruteForceVectorStore(),  # or LocalFaissStore(path) / CachedFaissStore(cache, scope)
))

await idx.upsert([Document(id="a:1", text="...", metadata={"board": "b1"}, timestamp=...)])
await idx.delete(["a:1"])
indexed_ids = idx.ids(filters={"board": "b1"})   # to diff against a live collection
await idx.ensure_built()                         # (re)build the vector store iff data changed
hits = await idx.search("query", top_k=20, filters={"board": "b1"})
```

`embed_fn` is exactly the platform's `model_service.embed_texts`, so any bundle's
embedder satisfies it with no adapter.

## Documents

```python
@dataclass
class Document:
    id: str                         # caller-unique within the scope
    text: str                       # the searchable blob the caller composes
    metadata: Dict[str, Any] = {}   # returned on hits; single-key equality filterable
    timestamp: float | None = None  # epoch seconds; drives recency; defaults to now at upsert
```

The caller owns `text` composition — concatenate whatever should be searchable
(label + summary + description + comments…). `metadata` is returned verbatim on
each hit and can be filtered with single-key equality (`filters={"board": id}`),
implemented as `json_extract(metadata_json, '$.key') = ?`. For set membership
(several kinds, several namespaces) over-fetch and post-filter in the adapter; the
sets are small.

## Embed-on-write

`upsert()` embeds **only new or text-changed** documents. A metadata-only or
timestamp-only edit does not re-embed and does not invalidate the vector build —
it just updates the row. This is the cost rule: you pay the embedder when the
searchable text actually changes, not on every touch and never on read of an
already-indexed document. (Query-time embedding is separately gated; see Scoring.)

Internally each upsert that changed vectors bumps a `data_version`; the vector
build records the `built_version` it was built from. They are compared in
`ensure_built()`.

## Build lifecycle: `ensure_built`

The SQLite tables (docs, FTS, the cached vectors) are the source of truth. The
vector store is a derived structure rebuilt from the cached vectors when stale:

```python
await idx.ensure_built()   # no-op if built_version == data_version; else rebuild()
```

- **Volatile backends** (`BruteForceVectorStore`, in-memory) cannot trust a persisted
  `built_version` — a fresh process starts empty — so the index forces a rebuild on
  first use by resetting `built_version = -1` in `__init__`.
- **Non-volatile backends** (`LocalFaissStore` file, `CachedFaissStore` cross-process)
  keep their built artifact and skip the rebuild when versions match.

Call `ensure_built()` after a batch of writes, inside whatever lock serializes
writes for the scope. For the canvas pin board that lock is the runtime's observed
file lock — see [Synchronization Mechanisms](../../../service/synch-mechanisms/critical-section-README.md).
`search()` also calls `ensure_built()` before a semantic pass as a safety net, but
the canonical place to build is on update, not on read.

## Vector backends

All backends key on the SQLite rowids and use cosine similarity on L2-normalized
vectors, so they are interchangeable with no other change. Pick by scale:

| Backend | Deps | Persistence | Use for |
|---|---|---|---|
| `BruteForceVectorStore` | none | volatile (in-memory) | per-user / per-board scales (tens–thousands of docs); fully testable without faiss |
| `LocalFaissStore(path)` | faiss + numpy | file next to the SQLite | larger single-process / shared-filesystem collections |
| `CachedFaissStore(cache, scope)` | faiss + numpy | cross-process via `FaissProjectCache` (Redis-coordinated) | collections shared across workers |

`faiss` and `numpy` are optional imports; the brute-force default needs neither, so
the index is importable and unit-testable in any environment. The faiss stores use
`IndexFlatIP + IDMap2` (exact inner product on normalized vectors = cosine).

## Search modes and degradation

`search(query, mode="hybrid"|"lexical"|"semantic")` defaults to `hybrid`. The
semantic pass is **economically gated** (the embedder call costs money): if the
guard denies or the query is trivial, the semantic pass is skipped and the query
degrades to lexical + recency — still returning results, at no embed cost. A repeated
query is served from a small LRU query→vector cache (pagination, debounced typeahead,
the same term across boards do not re-pay the embedder). The gating knobs and the
fusion math are in [Hybrid Scoring](./hybrid-scoring-README.md).

## What it does not do

- It is not multi-tenant by itself: one index = one scope (one SQLite file). Callers
  put per-user / per-project scope in the **path** and per-board / per-kind scope in
  **metadata filters**.
- It does not own the write lock. Serializing concurrent writers is the caller's job
  (and a solved one — use an observed file lock for filesystem-backed scopes).
- It does not compose `Document.text` or interpret `metadata` keys — that is the
  domain adapter's job (e.g. the canvas `PinSearchIndex`).

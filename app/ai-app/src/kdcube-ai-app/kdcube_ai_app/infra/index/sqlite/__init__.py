# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Generic SQLite + vector hybrid index.

A reusable, per-scope search index that internalizes SQLite lexical (FTS5/bm25),
embed-on-write vectors, vector-store build/eval, recency decay, and RRF fusion —
so any "searchable collection" (pins, tasks, memories, …) gets semantic + lexical
+ recency + reciprocal-rank-fusion search by handing it Documents and a query.

Vector backend is pluggable via the `VectorStore` protocol. Default is the
dependency-free `BruteForceVectorStore` (in `kdcube_ai_app.infra.index.vector_store`);
faiss backends live in `kdcube_ai_app.infra.index.faiss` — this index *uses* a
backend, it does not contain faiss.
"""
from .hybrid_index import HybridIndex
from .types import Document, SearchHit, IndexConfig, FusionWeights, VectorStore, EmbedFn
from ..vector_store import BruteForceVectorStore

__all__ = [
    "HybridIndex",
    "Document",
    "SearchHit",
    "IndexConfig",
    "FusionWeights",
    "VectorStore",
    "EmbedFn",
    "BruteForceVectorStore",
]

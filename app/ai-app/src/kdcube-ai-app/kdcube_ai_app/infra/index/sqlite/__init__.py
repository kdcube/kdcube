# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Generic SQLite + vector hybrid index.

A reusable, per-scope search index that internalizes SQLite lexical (FTS5/bm25),
embed-on-write vectors, vector-store build/eval, recency decay, and RRF fusion —
so any "searchable collection" (pins, tasks, memories, …) gets semantic + lexical
+ recency + reciprocal-rank-fusion search by handing it Documents and a query.

Vector backends (pluggable): BruteForceVectorStore (pure-python default),
LocalFaissStore (file-backed faiss), CachedFaissStore (cross-process FaissProjectCache).
"""
from .hybrid_index import HybridIndex
from .types import Document, SearchHit, IndexConfig, FusionWeights, VectorStore, EmbedFn
from .vector_store import BruteForceVectorStore, LocalFaissStore, CachedFaissStore

__all__ = [
    "HybridIndex",
    "Document",
    "SearchHit",
    "IndexConfig",
    "FusionWeights",
    "VectorStore",
    "EmbedFn",
    "BruteForceVectorStore",
    "LocalFaissStore",
    "CachedFaissStore",
]

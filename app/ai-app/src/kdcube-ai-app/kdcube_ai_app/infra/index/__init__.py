# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Reusable search/index infrastructure.

- `vector_store`  : the `VectorStore` protocol + pure-python `BruteForceVectorStore`.
- `faiss`         : faiss vector backends (optional faiss + numpy deps).
- `sqlite`        : the SQLite + vector hybrid index (`HybridIndex`).
"""
from .vector_store import VectorStore, BruteForceVectorStore

__all__ = ["VectorStore", "BruteForceVectorStore"]

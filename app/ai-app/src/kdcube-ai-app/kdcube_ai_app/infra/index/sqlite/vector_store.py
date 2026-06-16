# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Pluggable vector backends for the hybrid index.

- BruteForceVectorStore: pure-python cosine, zero deps. The default — fine for
  per-scope collections (tens–thousands of docs) and fully testable without faiss.
- LocalFaissStore: file-backed faiss index next to the owner's SQLite. For scale,
  single-process / shared-filesystem.
- CachedFaissStore: wraps the cross-process `FaissProjectCache` (Redis-coordinated).

All use cosine similarity on L2-normalized vectors; ids are the SQLite rowids.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, List, Sequence

try:  # heavy, optional
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:  # heavy, optional
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # type: ignore


def _normalize(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / norm for x in vec]


class BruteForceVectorStore:
    """In-memory exact cosine search. No external deps; rebuilt from the index's
    cached vectors. Suitable for per-user / per-board scales."""

    volatile = True  # in-memory: a new instance starts empty (forces rebuild)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: List[tuple[int, List[float]]] = []  # (id, normalized vector)

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        with self._lock:
            self._items = [(int(i), _normalize(v)) for i, v in items if v]

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        with self._lock:
            if not self._items:
                return []
            q = _normalize(vector)
            scored = [(i, sum(a * b for a, b in zip(q, v))) for i, v in self._items]
            scored.sort(key=lambda t: t[1], reverse=True)
            return scored[: max(1, top_k)]

    def reset(self) -> None:
        with self._lock:
            self._items = []


def _require_faiss() -> None:
    if faiss is None or np is None:  # pragma: no cover
        raise RuntimeError("LocalFaissStore/CachedFaissStore require faiss + numpy")


def _matrix(vectors: Sequence[Sequence[float]], dim: int):
    arr = np.asarray(vectors, dtype="float32").reshape(-1, dim)
    faiss.normalize_L2(arr)
    return arr


def _build_index(items: Sequence[tuple[int, Sequence[float]]], dim: int):
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
    if items:
        ids = np.asarray([int(i) for i, _ in items], dtype="int64")
        index.add_with_ids(_matrix([v for _, v in items], dim), ids)
    return index


class LocalFaissStore:
    """File-backed faiss index (IndexFlatIP + IDMap2) persisted next to the SQLite."""

    volatile = False  # persisted to a file: survives new instances

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._index = None

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        _require_faiss()
        with self._lock:
            index = _build_index(items, dim)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(self.path))
            self._index = index

    def _load(self):
        if self._index is None and self.path.exists():
            self._index = faiss.read_index(str(self.path))
        return self._index

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        _require_faiss()
        with self._lock:
            index = self._load()
            if index is None or index.ntotal == 0:
                return []
            D, I = index.search(_matrix([vector], index.d), min(top_k, index.ntotal))
            return [(int(i), float(d)) for i, d in zip(I[0], D[0]) if i != -1]

    def reset(self) -> None:
        with self._lock:
            self._index = None
            try:
                self.path.unlink()
            except OSError:
                pass


class CachedFaissStore:
    """Cross-process backend over the existing `FaissProjectCache` (Redis-coordinated).
    `scope` is the cache key (e.g. "pins:<user>")."""

    volatile = False  # published to the cross-process cache: survives new instances

    def __init__(self, cache, scope: str) -> None:
        self._cache = cache
        self._scope = scope

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        _require_faiss()
        self._cache.publish_new_index(self._scope, _build_index(items, dim))

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        _require_faiss()
        try:
            with self._cache.get(self._scope) as index:
                if index.ntotal == 0:
                    return []
                D, I = index.search(_matrix([vector], index.d), min(top_k, index.ntotal))
                return [(int(i), float(d)) for i, d in zip(I[0], D[0]) if i != -1]
        except RuntimeError:
            return []  # no index published yet

    def reset(self) -> None:  # pragma: no cover
        self._cache.publish_new_index(self._scope, _build_index([], 1))

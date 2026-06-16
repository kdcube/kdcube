# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Generic vector-store contract + the dependency-free brute-force backend.

The `VectorStore` protocol is the pluggable ANN contract any index (the SQLite
hybrid index, or anything else) builds against. `BruteForceVectorStore` is the
zero-dependency in-memory implementation.

faiss is faiss: the file-backed / cross-process faiss backends live in
`kdcube_ai_app.infra.index.faiss`. An index *uses* a faiss backend; it does not
contain faiss. Import the faiss module only when a faiss backend is selected.
"""
from __future__ import annotations

import math
import threading
from typing import List, Protocol, Sequence, runtime_checkable


@runtime_checkable
class VectorStore(Protocol):
    """Pluggable ANN backend keyed by integer ids (e.g. SQLite rowids).

    Implementations: BruteForceVectorStore (pure-python, default) here;
    LocalFaissStore / CachedFaissStore in `kdcube_ai_app.infra.index.faiss`.
    """

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        """Replace the whole index with these (id, vector) pairs."""

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        """Return [(id, similarity)] best-first (cosine on normalized vectors)."""

    def reset(self) -> None:
        """Drop the index."""


def normalize(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / norm for x in vec]


class BruteForceVectorStore:
    """In-memory exact cosine search. No external deps; rebuilt from the index's
    cached vectors. Suitable for per-user / per-board scales (tens–thousands)."""

    volatile = True  # in-memory: a new instance starts empty (forces rebuild)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: List[tuple[int, List[float]]] = []  # (id, normalized vector)

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        with self._lock:
            self._items = [(int(i), normalize(v)) for i, v in items if v]

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        with self._lock:
            if not self._items:
                return []
            q = normalize(vector)
            scored = [(i, sum(a * b for a, b in zip(q, v))) for i, v in self._items]
            scored.sort(key=lambda t: t[1], reverse=True)
            return scored[: max(1, top_k)]

    def reset(self) -> None:
        with self._lock:
            self._items = []


__all__ = ["VectorStore", "BruteForceVectorStore", "normalize"]

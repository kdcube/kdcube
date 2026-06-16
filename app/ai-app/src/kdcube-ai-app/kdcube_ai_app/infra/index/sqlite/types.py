# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Public types for the generic SQLite+vector hybrid index."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Protocol, Sequence, runtime_checkable

# An embedder: text batch -> one vector per text. The platform's
# `model_service.embed_texts` satisfies this exactly.
EmbedFn = Callable[[Sequence[str]], Awaitable[List[List[float]]]]


@runtime_checkable
class VectorStore(Protocol):
    """Pluggable ANN backend keyed by integer ids (the SQLite rowids).

    Implementations: BruteForceVectorStore (pure-python, default), LocalFaissStore
    (file-backed faiss), CachedFaissStore (cross-process via FaissProjectCache).
    """

    def rebuild(self, items: Sequence[tuple[int, Sequence[float]]], dim: int) -> None:
        """Replace the whole index with these (id, vector) pairs."""

    def search(self, vector: Sequence[float], top_k: int) -> List[tuple[int, float]]:
        """Return [(id, similarity)] best-first (cosine on normalized vectors)."""

    def reset(self) -> None:
        """Drop the index."""


@dataclass
class Document:
    """A unit to index. `text` is the searchable blob the caller composes
    (e.g. label + summary + description + comments). `metadata` is returned on
    hits and can be filtered on. `timestamp` (epoch seconds) drives recency;
    defaults to now at upsert time."""
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float | None = None


@dataclass
class SearchHit:
    id: str
    score: float
    metadata: Dict[str, Any]
    sub: Dict[str, Any] = field(default_factory=dict)  # per-ranker ranks/scores (telemetry)


@dataclass
class FusionWeights:
    """RRF weights per ranker. Recency is intentionally lighter than the
    content rankers by default."""
    lexical: float = 1.0
    semantic: float = 1.0
    recency: float = 0.5


@dataclass
class IndexConfig:
    db_path: Path
    embed_fn: EmbedFn
    dim: int
    vector_store: VectorStore
    weights: FusionWeights = field(default_factory=FusionWeights)
    rrf_k: int = 60
    recency_half_life_days: float = 30.0
    overfetch: int = 5  # per-ranker candidate multiplier before fusion
    # Drop semantic hits at or below this cosine similarity so clearly-unrelated
    # docs don't leak in via the always-returns-something vector search. Default 0.0
    # requires positive similarity; raise (e.g. 0.2) to gate weak matches.
    min_semantic_score: float = 0.0

    # --- economical guard on semantic search (the embedder call costs money) ---
    # When the guard denies, search degrades to lexical + recency (no embed call).
    semantic_enabled: bool = True              # master switch
    semantic_min_chars: int = 2                # don't embed trivial queries
    # Budget/policy hook: return False to skip the paid embed for this query.
    # Fails closed (an error → skip). May be sync OR async — so it can call the
    # economics engine's `economic_preflight` (the feasibility-only check). This is
    # where the budget/quota enforcement plugs in.
    semantic_guard: Callable[[str], "bool | Awaitable[bool]"] | None = None
    query_cache_size: int = 128                # LRU of query→vector (avoid re-embedding repeats)

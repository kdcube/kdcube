# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Public types for the generic SQLite+vector hybrid index."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Sequence

# The pluggable vector backend contract lives one level up — the SQLite index
# *uses* a VectorStore, it does not own the backends. faiss backends are in
# `kdcube_ai_app.infra.index.faiss`.
from ..vector_store import VectorStore  # noqa: F401  (re-exported public type)

# An embedder: text batch -> one vector per text. The platform's
# `model_service.embed_texts` satisfies this exactly.
EmbedFn = Callable[[Sequence[str]], Awaitable[List[List[float]]]]


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
    # The matching passage, with the matched terms marked. Empty unless the
    # caller asked for it, because building one costs an extra FTS pass, and
    # empty for a hit the lexical arm never matched: a semantic-only hit has no
    # matched terms to point at, and inventing a leading fragment would suggest
    # the query appears in text where it does not.
    snippet: str = ""


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
    model_service: Any | None = None
    weights: FusionWeights = field(default_factory=FusionWeights)
    rrf_k: int = 60
    recency_half_life_days: float = 30.0
    overfetch: int = 5  # per-ranker candidate multiplier before fusion
    # Semantic relevance floor — three regimes on one knob:
    #   < 0 (e.g. -1) : turn the semantic factor OFF entirely — search runs on
    #                   lexical + recency only (no embed call). The explicit
    #                   "semantic unavailable / don't consider it" choice.
    #   = 0 (default) : semantic ON, no floor — keeps every nearest row. Fine for a
    #                   ranked-list UX; for a match/no-match (filter/dim) UX this
    #                   lets the always-returns-something vector search match all.
    #   > 0 (e.g. 0.3): semantic ON, drop hits at/below this cosine similarity so
    #                   clearly-unrelated docs don't leak in. Use for filter UX.
    min_semantic_score: float = 0.0

    # --- snippets (opt-in per search, see HybridIndex.search(snippets=True)) ---
    # What the matched passage looks like when a caller asks for one. Markers
    # are plain text rather than HTML on purpose: this index does not know
    # whether its caller renders to a terminal, a widget or a document, and a
    # caller that wants markup can ask for markers it will recognise.
    snippet_open: str = "["
    snippet_close: str = "]"
    snippet_ellipsis: str = "..."
    snippet_tokens: int = 12

    # --- economical guard on semantic search (the embedder call costs money) ---
    # When the guard denies, search degrades to lexical + recency (no embed call).
    semantic_enabled: bool = True              # master switch
    semantic_min_chars: int = 2                # don't embed trivial queries
    # Budget/policy hook: return False to skip the paid embed for this query.
    # Fails closed (an error → skip). May be sync OR async — so it can call the
    # economics engine's `economic_preflight` (the feasibility-only check). This is
    # where the budget/quota enforcement plugs in.
    semantic_guard: Callable[[str], "bool | Awaitable[bool]"] | None = None
    # --- query→vector cache (opt-in, and SHARED by construction) ---
    # A search embeds the query on every call, and that vector is a pure
    # function of (query, model, dim). The repeats worth catching — pagination,
    # a later turn, a second person asking the same thing — happen on other
    # workers, so the cache lives in Redis, not in this object: a distributed
    # runtime rebuilds index instances freely, and anything remembered inside
    # one process is a cache one worker out of N can use and none can
    # invalidate. Hand a `QueryEmbeddingCache`
    # (infra.index.embedding_cache.create_query_embedding_cache) to switch it
    # on; leave it None and every query is embedded. The caller decides,
    # because the caller knows whether its queries repeat and under whose
    # tenant/project the vectors belong.
    query_embedding_cache: Any | None = None

    # --- lexical matching semantics ---
    # All-terms-first: a multi-word query first requires EVERY word (prefix)
    # to match, so results are a relevant subset rather than "anything sharing
    # one common word with the query" (which returns the whole corpus, merely
    # reordered). When the strict pass matches nothing, the query transparently
    # widens to any-term so the search still answers. Single-word queries are
    # unaffected.
    lexical_all_terms_first: bool = True

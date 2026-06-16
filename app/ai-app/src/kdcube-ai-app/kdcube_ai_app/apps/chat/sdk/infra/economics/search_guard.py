# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Economics guard for semantic search — the bridge between the generic search
index's `semantic_guard` hook and the economics enforcement engine.

Semantic search runs a (cheap but non-zero) embedder call per query. Per the
enforcement engine's own guidance, a flow that degrades gracefully on denial uses
`economic_preflight` — verify feasibility, no reservation/settlement; the actual
embed cost is metered by the normal model accounting when the embedder runs. The
caller wires the returned predicate into the index's `semantic_guard`; on denial
the index falls back to lexical + recency (no embed call).

If instead you want the embed reserved and charged in its own scope, wrap the
search in an `EconomicsGuard` (verify + reserve + settle) — see
`economic-enforcement-engine-README.md`.

The reservation estimate is grounded in the live price table (text-embedding-3-small
= $0.02 / 1M tokens), sized to the query — pennies-to-fractions, enough for the
feasibility/quota/funding check.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsEstimate,
    EconomicsSubject,
    FlowPolicy,
    economic_preflight,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.infra.accounting.usage import price_table

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_MIN_RESERVATION_USD = 1e-6


def embedding_rate_per_1m(model: str = DEFAULT_EMBEDDING_MODEL) -> float:
    """USD per 1M embedding tokens for `model`, read from the live price table."""
    for row in price_table().get("embedding", []) or []:
        if row.get("model") == model:
            return float(row.get("tokens_1M", 0.0) or 0.0)
    return 0.02  # text-embedding-3-small fallback


def embedding_reservation_usd(query: str, *, model: str = DEFAULT_EMBEDDING_MODEL) -> float:
    """Estimated USD to embed one query: rate × tokens (≈ chars/4), with a floor."""
    rate = embedding_rate_per_1m(model)
    est_tokens = max(16, len(query or "") // 4)
    return max(_MIN_RESERVATION_USD, rate * est_tokens / 1_000_000.0)


def make_semantic_search_guard(
    entrypoint: Any,
    *,
    subject: EconomicsSubject,
    model: str = DEFAULT_EMBEDDING_MODEL,
    flow: str = "search.semantic",
    policy: Optional[FlowPolicy] = None,
) -> Callable[[str], Awaitable[bool]]:
    """Build the async `semantic_guard` predicate for a search index.

    Returns an async `(query) -> bool`: True when the user may incur the embed
    (feasibility verified via `economic_preflight`), False on `EconomicsLimitException`
    so the index degrades to lexical. Plug into `HybridIndex`/`IndexConfig.semantic_guard`,
    `PinSearchIndex(semantic_guard=...)`, or `IssueService(semantic_guard=...)`.
    """
    flow_policy = policy or FlowPolicy(enforce_concurrency=False, emit_user_events=False)

    async def guard(query: str) -> bool:
        try:
            await economic_preflight(
                entrypoint,
                subject=subject,
                estimate=EconomicsEstimate(reservation_usd=embedding_reservation_usd(query, model=model)),
                flow=flow,
                policy=flow_policy,
            )
            return True
        except EconomicsLimitException:
            return False

    return guard


__all__ = [
    "make_semantic_search_guard",
    "embedding_reservation_usd",
    "embedding_rate_per_1m",
    "DEFAULT_EMBEDDING_MODEL",
]

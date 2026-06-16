# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Recency decay + Reciprocal Rank Fusion — pure python, no deps.

This is the "semantic + lexical + recency, reciprocal" scoring: each ranker
contributes 1/(k + rank), weighted; recency is turned into its own ranking from
an exponential half-life decay. Same family as the platform's `rrf_hybrid`.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List

from .types import FusionWeights


def recency_score(ts: float | None, *, half_life_days: float, now: float | None = None) -> float:
    """Exponential decay in [0, 1]: 1.0 at now, 0.5 after one half-life."""
    if not ts:
        return 0.0
    now = now if now is not None else time.time()
    age_days = max(0.0, (now - float(ts)) / 86400.0)
    return math.exp(-math.log(2.0) * age_days / max(half_life_days, 1e-6))


def rrf_fuse(
    rankings: Dict[str, List[str]],
    *,
    weights: FusionWeights,
    k: int,
    recency: Dict[str, float] | None = None,
) -> Dict[str, dict]:
    """Fuse best-first id rankings into {id: {"score", "sub"}}.

    `rankings` maps source -> ordered ids (e.g. {"lexical": [...], "semantic": [...]}).
    `recency` maps id -> recency_score and becomes a third ranking.
    """
    weight_of = {"lexical": weights.lexical, "semantic": weights.semantic, "recency": weights.recency}
    ranks: Dict[str, List[str]] = dict(rankings)
    if recency:
        ranks["recency"] = [doc_id for doc_id, _ in sorted(recency.items(), key=lambda kv: kv[1], reverse=True)]

    out: Dict[str, dict] = {}
    for source, ids in ranks.items():
        weight = weight_of.get(source, 0.0)
        if weight <= 0.0:
            continue
        for rank, doc_id in enumerate(ids):
            entry = out.setdefault(doc_id, {"score": 0.0, "sub": {}})
            entry["score"] += weight / (k + rank + 1)
            entry["sub"][f"{source}_rank"] = rank + 1
    return out

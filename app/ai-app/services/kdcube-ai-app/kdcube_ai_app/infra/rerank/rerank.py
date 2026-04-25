# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import re
from typing import List, Dict, Any, Iterable, Callable, Optional

import numpy as np
from sentence_transformers import CrossEncoder

# If you need consistent thresholds across queries, collect a small validation set of (query, segment, label)
# and see which raw scores or probabilities best separate "relevant" vs "irrelevant."

def normalize_scores(scores, normalization_method="sigmoid"):
    # 2) Normalize in one vectorized pass
    if normalization_method == "sigmoid":
        # sigmoid if you want each segment's relevance on an absolute 0–1 scale;
        norm = 1 / (1 + np.exp(-scores))

    elif normalization_method == "softmax":
        # softmax if you want a probability distribution across your N candidates (e.g. for downstream pooling or ensemble)
        # subtract max for numerical stability
        shifted = scores - scores.max()
        exps = np.exp(shifted)
        norm = exps / exps.sum()

    elif normalization_method == "minmax":
        mn, mx = scores.min(), scores.max()
        if mx > mn:
            norm = (scores - mn) / (mx - mn)
        else:
            norm = np.zeros_like(scores)
    else:
        raise ValueError("unknown method")
    return norm

# Load once at startup (thread‐safe in most server frameworks)
# You can swap in any model from the hub, e.g. 'cross-encoder/ms-marco-MiniLM-L-6-v2'
marco_mini_cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')

DEFAULT_COMPOUND_WEIGHTS: Dict[str, float] = {
    "rerank":   0.40,
    "vec":      0.40,
    "kw":       0.15,
    "priority": 0.05,
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> set:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _keyword_overlap(query_tokens: set, candidate_text: str) -> float:
    if not query_tokens:
        return 0.0
    cand = _tokens(candidate_text)
    if not cand:
        return 0.0
    return len(query_tokens & cand) / float(len(query_tokens))


def _is_priority(
        candidate: Dict[str, Any],
        priority_keys: Iterable[str],
        priority_predicate: Optional[Callable[[Dict[str, Any]], bool]],
) -> bool:
    if priority_predicate is not None and priority_predicate(candidate):
        return True
    if not priority_keys:
        return False
    keys = {str(k).lower() for k in priority_keys}
    # match against tags or entities (both common KB row shapes)
    tags = candidate.get("tags") or []
    if any(str(t).lower() in keys for t in tags):
        return True
    ents = candidate.get("entities") or []
    for ent in ents:
        if isinstance(ent, dict):
            for v in ent.values():
                if str(v).lower() in keys:
                    return True
        elif str(ent).lower() in keys:
            return True
    return False


def cross_encoder_rerank(
        query: str,
        candidates: List[Dict[str, Any]],
        column_name: str = "text",
        cross_encoder=marco_mini_cross_encoder,
        normalization_method: str = "sigmoid",
        top_k: Optional[int] = None,
        *,
        mode: str = "plain",
        weights: Optional[Dict[str, float]] = None,
        semantic_score_field: str = "semantic_score",
        priority_keys: Iterable[str] = (),
        priority_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
        min_priority_slots: int = 0,
) -> List[Dict[str, Any]]:
    """
    Rerank a list of candidate segments using a cross-encoder.

    mode="plain" (default, backwards-compatible):
      sort by normalized cross-encoder score only; sets 'rerank_score'.

    mode="compound":
      blend cross-encoder score with vector-similarity, keyword overlap, and a
      priority flag. Final score is annotated as 'rerank_score' and individual
      components are stored on each candidate for inspection.

      Compound formula:
        final = w_rerank * ce_norm
              + w_vec    * candidate[semantic_score_field]
              + w_kw     * keyword_overlap(query, content)
              + w_priority * is_priority(candidate)

      Weights default to {rerank: 0.40, vec: 0.40, kw: 0.15, priority: 0.05}
      and are renormalized to sum to 1.0 if a partial dict is supplied.

      'min_priority_slots' guarantees that the top_k window contains at least
      that many priority rows (when available) by promoting them above
      non-priority rows that would otherwise outrank them.
    """
    if not candidates:
        return []

    # 1) Cross-encoder scores -> normalized
    pairs = [(query, c[column_name]) for c in candidates]
    scores = cross_encoder.predict(pairs, convert_to_numpy=True)
    norm = normalize_scores(scores, normalization_method=normalization_method)

    # 2) Annotate base rerank_score (plain mode is unchanged from prior behavior)
    for c, s in zip(candidates, norm):
        c["rerank_score"] = float(s)

    if mode == "plain":
        candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
        return candidates[:top_k] if top_k is not None else candidates

    if mode != "compound":
        raise ValueError(f"unknown rerank mode: {mode!r}")

    # 3) Compound mode — merge defaults with caller weights, renormalize.
    w = dict(DEFAULT_COMPOUND_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total = sum(w.values()) or 1.0
    w = {k: v / total for k, v in w.items()}

    q_tokens = _tokens(query)
    for c in candidates:
        ce_norm = float(c["rerank_score"])
        sem = float(c.get(semantic_score_field, 0.0) or 0.0)
        kw = _keyword_overlap(q_tokens, c.get(column_name, ""))
        prio = 1.0 if _is_priority(c, priority_keys, priority_predicate) else 0.0
        c["rerank_components"] = {
            "ce": ce_norm, "vec": sem, "kw": kw, "priority": prio,
        }
        c["rerank_score"] = (
            w["rerank"] * ce_norm
            + w["vec"] * sem
            + w["kw"] * kw
            + w["priority"] * prio
        )

    candidates.sort(key=lambda c: c["rerank_score"], reverse=True)

    # 4) Priority slot guarantee
    if min_priority_slots > 0 and top_k is not None and top_k > 0:
        priority_rows = [c for c in candidates if (c.get("rerank_components") or {}).get("priority", 0.0) > 0]
        slots = min(min_priority_slots, len(priority_rows), top_k)
        if slots > 0:
            top = candidates[:top_k]
            in_top_priority = [c for c in top if (c.get("rerank_components") or {}).get("priority", 0.0) > 0]
            missing = slots - len(in_top_priority)
            if missing > 0:
                to_promote = [c for c in priority_rows if c not in top][:missing]
                if to_promote:
                    non_prio_in_top = [c for c in top if (c.get("rerank_components") or {}).get("priority", 0.0) == 0]
                    # demote the lowest-scoring non-priority rows to make room
                    to_demote = sorted(non_prio_in_top, key=lambda c: c["rerank_score"])[:missing]
                    new_top = [c for c in top if c not in to_demote] + to_promote
                    new_top.sort(key=lambda c: c["rerank_score"], reverse=True)
                    rest = [c for c in candidates if c not in new_top]
                    rest.sort(key=lambda c: c["rerank_score"], reverse=True)
                    candidates = new_top + rest

    return candidates[:top_k] if top_k is not None else candidates
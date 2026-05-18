from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from .models import normalize_terms


_WORD_RE = re.compile(r"[a-z0-9]+")
_SEARCH_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SEARCH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "should",
    "that",
    "the",
    "their",
    "there",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def normalize_text(value: str) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").lower()))


def search_query_terms(value: str) -> list[str]:
    """Terms used for memory lexical retrieval and relevance scoring.

    This intentionally differs from canonical-key normalization. Search queries
    often contain filler words ("which", "i", "the") that must not become hard
    retrieval requirements.
    """

    seen: set[str] = set()
    terms: list[str] = []
    for raw in _SEARCH_TOKEN_RE.findall(str(value or "").casefold()):
        term = raw.strip()
        if len(term) < 2 or term in _SEARCH_STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def build_canonical_key(
    *,
    user_id: str,
    kind: str,
    memory: str,
    labels: Sequence[str] | None = None,
    keywords: Sequence[str] | None = None,
) -> str:
    """Stable key for exact/idempotent dedupe.

    This is deliberately conservative. Semantic reconciliation can merge
    separate records later, but concurrent writes for the same normalized fact
    should converge immediately through this key.
    """

    parts = [
        normalize_text(user_id),
        normalize_text(kind or "fact"),
        normalize_text(memory),
        ",".join(normalize_terms(labels)),
        ",".join(normalize_terms(keywords)),
    ]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return f"memkey:{digest}"


def event_weight(event_type: str, *, confidence: float = 0.5, importance: float = 0.5) -> tuple[float, float, bool]:
    event = normalize_text(event_type).replace(" ", "_")
    conf = clamp(confidence)
    imp = clamp(importance)
    if event in {"confirm", "confirmation", "confirmed", "user_edit", "user_correction"}:
        return 1.25 + conf + 0.5 * imp, 0.0, True
    if event in {"contradict", "contradiction", "rejection", "reject", "deprecated"}:
        return 0.0, 1.25 + conf + 0.5 * imp, False
    if event in {"retire", "retired"}:
        return 0.0, 2.0, False
    if event in {"refine", "refinement", "merge", "squash", "manual_update"}:
        return 0.75 + 0.5 * conf + 0.5 * imp, 0.0, False
    if event in {"agent_observation", "observation", "inferred"}:
        return 0.25 + 0.5 * conf + 0.25 * imp, 0.0, False
    return 0.35 + 0.5 * conf + 0.25 * imp, 0.0, False


def compute_confirmation_rate(confirmation_count: int, contradiction_count: int, update_count: int) -> float:
    if update_count <= 0:
        return 0.0
    return clamp((confirmation_count + 0.5) / (confirmation_count + contradiction_count + 1.0))


def compute_confidence_score(positive_weight: float, negative_weight: float, evidence_count: int) -> float:
    total = max(positive_weight + negative_weight, 0.0)
    if total <= 0:
        return 0.5
    evidence_boost = 1.0 - math.exp(-max(evidence_count, 0) / 4.0)
    polarity = positive_weight / total
    return clamp(0.2 + 0.65 * polarity + 0.15 * evidence_boost)


def compute_importance_score(current: float, signal_importance: float, update_count: int) -> float:
    base = clamp(current if current is not None else 0.5)
    signal = clamp(signal_importance)
    update_boost = min(0.2, math.log1p(max(update_count, 0)) / 12.0)
    return clamp(0.72 * base + 0.28 * signal + update_boost)


def compute_freshness_score(
    last_event_at: datetime,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = 45.0,
) -> float:
    if now is None:
        now = datetime.now(timezone.utc)
    if last_event_at.tzinfo is None:
        last_event_at = last_event_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (now - last_event_at).total_seconds())
    half_life_seconds = max(1.0, float(half_life_days) * 86400.0)
    return clamp(math.exp(-math.log(2.0) * age_seconds / half_life_seconds))


def compute_salience_score(
    *,
    confidence_score: float,
    importance_score: float,
    freshness_score: float,
    confirmation_rate: float,
    update_count: int,
) -> float:
    update_boost = min(0.15, math.log1p(max(update_count, 0)) / 16.0)
    return clamp(
        0.34 * clamp(confidence_score)
        + 0.32 * clamp(importance_score)
        + 0.18 * clamp(freshness_score)
        + 0.16 * clamp(confirmation_rate)
        + update_boost
    )


def compute_tier(
    *,
    status: str,
    confidence_score: float,
    salience_score: float,
    confirmation_rate: float,
    update_count: int,
    pinned: bool = False,
) -> int:
    normalized_status = normalize_text(status)
    if normalized_status in {"retired", "merged"}:
        return 4
    if normalized_status in {"unsupported", "weakened"}:
        return 3
    if pinned:
        return 1
    if (
        salience_score >= 0.72
        and confidence_score >= 0.68
        and confirmation_rate >= 0.55
        and update_count >= 2
    ):
        return 1
    if salience_score >= 0.46 or update_count >= 2:
        return 2
    return 3


def compute_memory_scores(
    *,
    status: str,
    positive_weight: float,
    negative_weight: float,
    evidence_count: int,
    confirmation_count: int,
    contradiction_count: int,
    update_count: int,
    current_importance: float,
    signal_importance: float,
    last_event_at: datetime,
    half_life_days: float = 45.0,
    now: Optional[datetime] = None,
    pinned: bool = False,
) -> dict[str, float | int]:
    confirmation_rate = compute_confirmation_rate(
        confirmation_count=confirmation_count,
        contradiction_count=contradiction_count,
        update_count=update_count,
    )
    confidence_score = compute_confidence_score(
        positive_weight=positive_weight,
        negative_weight=negative_weight,
        evidence_count=evidence_count,
    )
    importance_score = compute_importance_score(
        current=current_importance,
        signal_importance=signal_importance,
        update_count=update_count,
    )
    freshness_score = compute_freshness_score(last_event_at, now=now, half_life_days=half_life_days)
    salience_score = compute_salience_score(
        confidence_score=confidence_score,
        importance_score=importance_score,
        freshness_score=freshness_score,
        confirmation_rate=confirmation_rate,
        update_count=update_count,
    )
    tier = compute_tier(
        status=status,
        confidence_score=confidence_score,
        salience_score=salience_score,
        confirmation_rate=confirmation_rate,
        update_count=update_count,
        pinned=pinned,
    )
    return {
        "confirmation_rate": confirmation_rate,
        "confidence_score": confidence_score,
        "importance_score": importance_score,
        "freshness_score": freshness_score,
        "salience_score": salience_score,
        "tier": tier,
    }


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    if isinstance(right, str):
        try:
            right = json.loads(right)
        except Exception:
            right = None
    if isinstance(left, str):
        try:
            left = json.loads(left)
        except Exception:
            left = None
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        fa = float(a)
        fb = float(b)
        dot += fa * fb
        left_norm += fa * fa
        right_norm += fb * fb
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return clamp((dot / math.sqrt(left_norm * right_norm) + 1.0) / 2.0)


def token_overlap_score(query: str, text: str) -> float:
    query_tokens = set(search_query_terms(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(search_query_terms(text))
    if not text_tokens:
        return 0.0
    return clamp(len(query_tokens & text_tokens) / len(query_tokens))


def label_match_score(requested: Sequence[str], actual: Sequence[str]) -> float:
    requested_terms = set(normalize_terms(requested))
    if not requested_terms:
        return 0.0
    actual_terms = set(normalize_terms(actual))
    if not actual_terms:
        return 0.0
    return clamp(len(requested_terms & actual_terms) / len(requested_terms))


def rank_candidate(
    *,
    query: str,
    query_embedding: Sequence[float] | None,
    requested_labels: Sequence[str],
    requested_keywords: Sequence[str],
    row: Mapping[str, Any],
    text_rank: float = 0.0,
    half_life_days: float = 45.0,
    now: Optional[datetime] = None,
) -> tuple[float, dict[str, float]]:
    labels = row.get("labels") or []
    keywords = row.get("keywords") or []
    search_text = row.get("search_text") or row.get("memory") or ""
    semantic = clamp(row.get("semantic_score") or 0.0)
    if semantic <= 0.0:
        semantic = cosine_similarity(query_embedding, row.get("embedding") or row.get("embedding_json") or None)
    text = max(clamp(text_rank), token_overlap_score(query, str(search_text)))
    label = max(label_match_score(requested_labels, labels), label_match_score(requested_keywords, keywords))
    freshness = compute_freshness_score(
        row.get("last_event_at") or row.get("updated_at"),
        now=now,
        half_life_days=half_life_days,
    )
    importance = clamp(float(row.get("importance_score") or 0.0))
    confidence = clamp(float(row.get("confidence_score") or 0.0))
    salience = clamp(float(row.get("salience_score") or 0.0))
    confirmation = clamp(float(row.get("confirmation_rate") or 0.0))
    score = clamp(
        0.30 * semantic
        + 0.22 * text
        + 0.13 * label
        + 0.11 * salience
        + 0.08 * importance
        + 0.07 * confidence
        + 0.05 * freshness
        + 0.04 * confirmation
    )
    return score, {
        "semantic": semantic,
        "text": text,
        "labels": label,
        "salience": salience,
        "importance": importance,
        "confidence": confidence,
        "freshness": freshness,
        "confirmation": confirmation,
    }

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/policy/policy.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple, TypedDict, Optional
import time
import json
from pydantic import BaseModel, Field

from dataclasses import dataclass
from typing import Any, Callable, Optional

import re
_SUBJ_RE = re.compile(r'^(?P<base>[^\[]+)(?:\[(?P<sub>[^\]]+)\])?$')
def _split_key(k: str) -> tuple[str, str|None]:
    m = _SUBJ_RE.match(k or "")
    return (m.group('base'), m.group('sub')) if m else (k, None)

@dataclass
class KeyPolicy:
    # promotion thresholds
    min_support: int = 2              # distinct conversations needed
    avg_decayed: float = 0.7          # average decayed confidence threshold
    distinct_days: int = 2            # minimum unique days among supports
    conflict_horizon_days: int = 45   # recent opposing evidence blocks promotion
    ttl_days_user: int = 365          # TTL when promoted to user scope
    half_life_days: float = 45.0      # decay half-life for evidence

    # data semantics
    numeric_tolerance: float = 0.05   # 5% relative tolerance for equivalence
    canonicalizer: Optional[Callable[[Any], Any]] = None  # optional value normalizer

    # privacy/visibility (controls what goes into LLM prompts)
    send_to_llm: bool = True


class PrefAssertion(BaseModel):
    key: str
    value: Any = None
    desired: bool = True
    scope: str = "conversation"
    confidence: float = Field(0.6, ge=0.0, le=1.0)
    reason: str = "nl-extracted"

class PrefException(BaseModel):
    rule_key: str
    value: Any = True
    scope: str = "conversation"
    confidence: float = Field(0.7, ge=0.0, le=1.0)
    reason: str = "nl-extracted"

class PreferenceExtractionOut(BaseModel):
    assertions: List[PrefAssertion] = Field(default_factory=list)
    exceptions: List[PrefException] = Field(default_factory=list)

# exception/neg/pos precedence across scopes
PRECEDENCE = [
    ("exception", "conversation"),
    ("exception", "user"),
    ("neg", "conversation"),
    ("pos", "conversation"),
    ("neg", "user"),
    ("pos", "user"),
    ("neg", "global"),
    ("pos", "global"),
]

@dataclass
class Item:
    key: str
    value: Any
    scope: str
    desired: Optional[bool]  # None for exception
    confidence: float
    created_at: int
    ttl_days: int
    reason: str

def _decay_score(conf: float, created_at: int, hl_days: float = 30.0) -> float:
    age_days = max(0.0, (time.time() - created_at) / 86400.0)
    return float(conf) * (0.5 ** (age_days / hl_days))

# chat/sdk/context/policy/policy.py

class PolicyResult(TypedDict):
    do: Dict[str, Any]
    avoid: Dict[str, Any]
    allow_if: Dict[str, Any]
    superseded: List[Dict[str, Any]]
    kept: int
    dropped: int
    reasons: List[str]
    facts: List[Dict[str, Any]]  # neutral facts, passthrough

def _kind(item: Item) -> Tuple[str, str]:
    if item.desired is None:
        return ("exception", item.scope)
    return ("pos" if item.desired else "neg", item.scope)

def _norm_scope(s: Optional[str]) -> str:
    s = (s or "user").lower().strip()
    if s in ("conversation", "session", "conv", "thread"):
        return "conversation"
    return s

def _unpack_val(rec: Dict[str, Any]) -> Any:
    v = rec.get("value")
    if v is None and rec.get("value_json"):
        try:
            return json.loads(rec["value_json"])
        except Exception:
            return None
    return v

def evaluate_policy(raw: Dict[str, Any], *, half_life_days: float = 30.0) -> PolicyResult:
    """
    Apply precedence + conflict resolution.
    - exception > conversation-neg > conversation-pos > user-neg > user-pos > global-neg > global-pos
    - for the same key, choose by (decayed_score, confidence, recency)
    """
    assertions = [
        Item(
            key=a.get("key"),
            value=_unpack_val(a),
            scope=_norm_scope(a.get("scope")),
            desired=bool(a.get("desired")),
            confidence=float(a.get("confidence") or 0.5),
            created_at=int(a.get("created_at") or 0),
            ttl_days=int(a.get("ttl_days") or 365),
            reason=a.get("reason") or "unknown",
        )
        for a in (raw.get("assertions") or [])
    ]
    exceptions = [
        Item(
            key=e.get("rule_key"),
            value=_unpack_val(e),
            scope=_norm_scope(e.get("scope")),
            desired=None,
            confidence=1.0,
            created_at=int(e.get("created_at") or 0),
            ttl_days=365,
            reason=e.get("reason") or "exception",
        )
        for e in (raw.get("exceptions") or [])
    ]

    # collect keys present in assertions (to know subjects)
    present_keys = [it.key for it in assertions]
    present_bases = {}
    for k in present_keys:
        b, s = _split_key(k)
        present_bases.setdefault(b, []).append(k)

    # For each exception with a base-only key (no [sub]), replicate it to all present subjected keys
    replicated: list[Item] = []
    for e in list(exceptions):
        b, s = _split_key(e.key)
        if s is None and b in present_bases:
            for full_k in present_bases[b]:
                replicated.append(Item(
                    key=full_k, value=e.value, scope=e.scope, desired=None,
                    confidence=e.confidence, created_at=e.created_at, ttl_days=e.ttl_days, reason=e.reason
                ))
    # keep original exceptions too (covers the truly global case)
    exceptions.extend(replicated)

    bucket: Dict[str, List[Item]] = {}
    for it in assertions + exceptions:
        bucket.setdefault(it.key, []).append(it)

    do: Dict[str, Any] = {}
    avoid: Dict[str, Any] = {}
    allow_if: Dict[str, Any] = {}
    superseded: List[Dict[str, Any]] = []
    kept = dropped = 0
    reasons: List[str] = []

    def prec_rank(it: Item) -> int:
        kind = _kind(it)
        for i, (k, sc) in enumerate(PRECEDENCE):
            if kind == (k, it.scope):
                return i
        return len(PRECEDENCE)

    def score(it: Item) -> tuple:
        dec = _decay_score(it.confidence, it.created_at, half_life_days)
        return (-prec_rank(it), dec, it.confidence, it.created_at)

    for key, items in bucket.items():
        items_sorted = sorted(items, key=score, reverse=True)
        head, *tail = items_sorted

        if head.desired is None:
            allow_if[key] = head.value
            reasons.append(f"allow_if {key} from exception scope={head.scope}")
        elif head.desired:
            do[key] = head.value
            reasons.append(f"do {key} from scope={head.scope}")
        else:
            avoid[key] = head.value
            reasons.append(f"avoid {key} from scope={head.scope}")
        kept += 1

        for t in tail:
            superseded.append({
                "key": key,
                "value": t.value,
                "scope": t.scope,
                "desired": t.desired,
                "superseded_by": head.scope,
            })
            dropped += 1

    return PolicyResult(
        do=do,
        avoid=avoid,
        allow_if=allow_if,
        superseded=superseded,
        kept=kept,
        dropped=dropped,
        reasons=reasons,
        facts=list(raw.get("facts") or [])
    )

def _filter_llm_prefs(d: dict, policy_for_key) -> dict:
    return {k:v for k,v in (d or {}).items() if policy_for_key(k).send_to_llm}

# --- LLM-facing preference view ---
_SCOPE_W = {"conversation": 1.00, "user": 0.90, "global": 0.80}

class LLMPrefItem(BaseModel):
    key: str
    value: Any = None
    desired: Optional[bool] = True  # None for exception (allow_if)
    scope: str
    introduced_at: int
    last_seen_at: int
    introduced_seq: int
    reason: str = "unknown"
    score: Optional[float] = Field(0.0, ge=0.0, le=1.5)
    relevance: Optional[float] = Field(0.0, ge=0.0, le=1.2)
    strength: str = "medium"  # strong | medium | weak

def _decay(t: int, half_life_days: float) -> float:
    if not t: return 0.0
    age_days = max(0.0, (time.time() - t) / 86400.0)
    return 0.5 ** (age_days / max(half_life_days, 1.0))

def build_llm_pref_view(
        raw_snapshot: Dict[str, Any],
        *,
        policy_for_key,
        active_topics: List[str],
        half_life_days: float = 45.0,
        per_base_cap: int = 2,
        top_k: int = 16
) -> Dict[str, Any]:
    """
    Returns:
      {
        "ordered": [LLMPrefItem...],  # deduped, precedence applied, ranked
        "allow_if": {...}             # merged exceptions (conditions) for quick reference
      }
    """
    # Apply precedence once
    polres = evaluate_policy(raw_snapshot, half_life_days=half_life_days)

    # Collect all candidates (conversation+user+global assertions & exceptions) before precedence,
    # but we'll only keep the "head" per key per precedence outcome for LLM.
    items: List[LLMPrefItem] = []
    allow_if: Dict[str, Any] = dict(polres.get("allow_if") or {})

    # Keep a stable introduction sequence for LLM (older first)
    # Use created_at as proxy for "introduced_at".
    def _collect(src: List[Dict[str, Any]], desired: Optional[bool]):
        arr = sorted([r for r in (src or []) if (r.get("desired") if desired is not None else True) == desired],
                     key=lambda r: int(r.get("created_at") or 0))
        for i, r in enumerate(arr):
            key = r.get("key") if desired is not None else r.get("rule_key")
            scope = _norm_scope(r.get("scope"))
            val = _unpack_val(r)
            items.append(LLMPrefItem(
                key=key,
                value=val,
                desired=desired,
                scope=scope,
                introduced_at=int(r.get("created_at") or 0),
                last_seen_at=int(r.get("last_seen_at") or r.get("created_at") or 0),
                introduced_seq=i,
                reason=(r.get("reason") or "unknown"),
                score=0.0,
                relevance=0.0,
            ))

    # Collect positives and negatives separately:
    _collect(raw_snapshot.get("assertions"), True)
    _collect(raw_snapshot.get("assertions"), False)
    # Exceptions as allow_if
    excs = raw_snapshot.get("exceptions") or []
    for e in excs:
        pass  # allow_if merged already via polres

    # Dedup + precedence: keep only keys present in polres outcome
    keep_keys = set(polres["do"].keys()) | set(polres["avoid"].keys()) | set(allow_if.keys())
    filtered: List[LLMPrefItem] = [it for it in items if it.key in keep_keys]

    # Compute per-item score & relevance
    active_topics = [t.strip().lower() for t in (active_topics or [])]
    for it in filtered:
        pol = policy_for_key(it.key)
        d = _decay(it.last_seen_at, half_life_days=pol.half_life_days or half_life_days)
        scope_w = _SCOPE_W.get(it.scope, 0.85)
        topic_boost = 1.15 if (it.key.startswith("topic.") and it.key.split(".", 1)[-1] in active_topics) else 1.0
        it.relevance = min(1.2, d * topic_boost)
        base = scope_w * d
        # map to strength buckets
        if base >= 0.80:
            it.strength = "strong"
        elif base >= 0.45:
            it.strength = "medium"
        else:
            it.strength = "weak"
        it.score = round(min(1.5, base * topic_boost), 4)

    # Sort: (desired exceptions don’t show here; allow_if emitted separately)
    filtered.sort(key=lambda x: (-x.score, -x.last_seen_at, x.introduced_seq))

    grouped = {}
    capped = []
    for it in filtered:
        base, _ = _split_key(it.key)
        n = grouped.get(base, 0)
        if n >= per_base_cap:
            continue
        grouped[base] = n + 1
        capped.append(it)

    out_list = []
    for it in capped:
        if not policy_for_key(it.key).send_to_llm:
            continue
        out_list.append(it)
        if len(out_list) >= top_k:
            break

    # Truncate and hide keys that KeyPolicy says not to send to LLM
    out_list = []
    for it in filtered:
        if not policy_for_key(it.key).send_to_llm:
            continue
        out_list.append(it)
        if len(out_list) >= top_k:
            break

    return {
        "ordered": [i.model_dump() for i in out_list],
        "allow_if": allow_if
    }

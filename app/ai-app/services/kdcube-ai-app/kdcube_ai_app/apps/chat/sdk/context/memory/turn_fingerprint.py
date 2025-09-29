# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/context/memory/fingerprint.py

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import datetime
import json


@dataclass
class FPAssertion:
    key: str
    value: Any = None
    desired: bool = True
    confidence: float = 0.7
    scope: str = "conversation"
    since_ts: str = ""
    source: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FPException:
    rule_key: str
    value: Any = None
    scope: str = "conversation"
    since_ts: str = ""
    source: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FPFact:
    key: str
    value: Any = None
    confidence: float = 0.6
    scope: str = "conversation"
    since_ts: str = ""
    source: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnFingerprintV1:
    version: str
    turn_id: str
    objective: str
    topics: List[str]
    assertions: List[Dict[str, Any]]
    exceptions: List[Dict[str, Any]]
    facts: List[Dict[str, Any]]
    made_at: str
    conversation_title: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "turn_id": self.turn_id,
            "objective": self.objective,
            "topics": list(self.topics or []),
            "assertions": list(self.assertions or []),
            "exceptions": list(self.exceptions or []),
            "facts": list(self.facts or []),
            "made_at": self.made_at,
            **{"conversation_title": self.conversation_title if self.conversation_title else {}},
        }


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def make_early_guess_fingerprint(
        *,
        turn_id: str,
        objective: str,
        topics: List[str],
        guess_prefs: Dict[str, List[Dict[str, Any]]] | None = None,
        guess_facts: List[Dict[str, Any]] | None = None,
) -> TurnFingerprintV1:
    gp = guess_prefs or {}
    return TurnFingerprintV1(
        version="v1",
        turn_id=turn_id,
        objective=objective or "",
        topics=list(topics or []),
        assertions=list(gp.get("assertions") or []),
        exceptions=list(gp.get("exceptions") or []),
        facts=list(guess_facts or []),
        made_at=_now_iso(),
    )


def _short(v: Any, max_len: int = 80) -> str:
    try:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    except Exception:
        s = str(v)
    s = s.strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def render_fingerprint_one_liner(fp: TurnFingerprintV1) -> str:
    obj = (fp.objective or "").strip()
    obj_short = obj[:160] + ("…" if len(obj) > 160 else "")
    a_keys = [a.get("key") for a in (fp.assertions or []) if a.get("key")]
    e_keys = [e.get("rule_key") for e in (fp.exceptions or []) if e.get("rule_key")]
    f_keys = [f.get("key") for f in (fp.facts or []) if f.get("key")]
    parts = [
        f"objective={obj_short}",
        f"A={a_keys[:6]}",
        f"E={e_keys[:6]}",
        f"F={f_keys[:6]}",
    ]
    if fp.topics:
        parts.append(f"topics={list(fp.topics)[:4]}")
    return "; ".join(parts)

def _render_local_memories_block(earlier_turns_insights: list[dict], # namely, fingerprints
                                 max_items: int = 16) -> str:
    """
    earlier_turns_insights item shape (from build_gate_context_hints):
      { "ts": "...", "insights_one_liner": "...", "insights_json": {...}, "turn_id": "tid", "kind": "delta_fp" }
    We output oldest -> newest to preserve natural order.
    """
    if not earlier_turns_insights:
        return ""
    # sort by ts ascending
    def _ts_key(x):
        ts = (x or {}).get("ts") or ""
        try:
            s = ts.strip()
            if s.endswith("Z"): s = s[:-1] + "+00:00"
            import datetime as _dt
            return _dt.datetime.fromisoformat(s).timestamp()
        except Exception:
            return float("-inf")
    arr = sorted(list(earlier_turns_insights), key=_ts_key)[:max_items]

    def _fmt_ts(ts: str) -> str:
        try:
            s = ts.strip()
            if s.endswith("Z"): s = s[:-1] + "+00:00"
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(s)
            if dt.tzinfo: dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
            return dt.strftime("%H:%M %b %d %Y")
        except Exception:
            return ts or "(time)"

    lines = ["[EARLIER TURNS — NON-RECONCILED INSIGHTS (FILTERED)]"]
    for it in arr:
        ts_h = _fmt_ts(it.get("ts") or "")
        tid  = it.get("turn_id") or ""
        one  = (it.get("insights_one_liner") or "").strip()
        lines.append(f"{ts_h}[turn id]\n{tid}\n[insights]\n{one}\n")
    return "\n".join(lines).strip()
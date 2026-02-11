# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/context/memory/presentation.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import datetime as _dt

from kdcube_ai_app.apps.chat.sdk.context.memory.turn_fingerprint import TurnFingerprintV1

def format_assistant_signals_block(
    turn_memories: Optional[List[Dict[str, Any]]],
    *,
    order_label: str = "newest→oldest",
    scope_label: str = "user_cross_conversation",
) -> str:
    """
    Render assistant-originated promo/mention signals.

    Example:
    [ASSISTANT SIGNALS — CHRONOLOGICAL (newest→oldest; scope=user_cross_conversation)]
    - turn_id=turn_123 | 2026-01-26T15:03Z | time_since=2h15m
      - signal: product.capability = {...} (recommend, scope=conversation)
    """
    if not turn_memories:
        return ""
    lines = [f"[ASSISTANT SIGNALS — CHRONOLOGICAL ({order_label}; scope={scope_label})]"]
    for tm in turn_memories or []:
        signals = tm.get("assistant_signals") or []
        if not signals:
            continue
        tid = (tm.get("turn_id") or "").strip()
        ts = (tm.get("made_at") or tm.get("ts") or "").strip()
        header = f"- turn_id={tid}"
        if ts:
            header += f" | {ts}"
            try:
                s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
                when = _dt.datetime.fromisoformat(s)
                now = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
                delta = now - when
                total_sec = int(delta.total_seconds())
                if total_sec >= 0:
                    days = total_sec // 86400
                    hours = (total_sec % 86400) // 3600
                    mins = (total_sec % 3600) // 60
                    if days >= 30:
                        months = days // 30
                        weeks = (days % 30) // 7
                        age = f"{months}mo"
                        if weeks:
                            age += f"{weeks}w"
                    elif days >= 7:
                        weeks = days // 7
                        rem_days = days % 7
                        age = f"{weeks}w"
                        if rem_days:
                            age += f"{rem_days}d"
                    elif days > 0:
                        age = f"{days}d{hours}h"
                    elif hours > 0:
                        age = f"{hours}h{mins}m"
                    else:
                        age = f"{mins}m"
                    header += f" | time_since={age}"
            except Exception:
                pass
        lines.append(header)
        for s in signals:
            key = (s.get("key") or "").strip()
            if not key:
                continue
            val = s.get("value")
            sev = (s.get("severity") or "").strip()
            applies = (s.get("applies_to") or "").strip()
            scope = (s.get("scope") or "").strip()
            meta = []
            if sev:
                meta.append(sev)
            if scope:
                meta.append(f"scope={scope}")
            if applies:
                meta.append(f"applies_to={applies}")
            meta_txt = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"  - signal: {key} = {val}{meta_txt}")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_turn_memories_block(
    turn_memories: Optional[List[Dict[str, Any]]],
    *,
    max_items: int = 16,
    order_label: str = "newest→oldest",
    scope_label: str = "conversation",
    current_turn_id: Optional[str] = None,
) -> str:
    """
    Render per-turn memories (preferences/facts) in chronological order.

    Example:
    [TURN MEMORIES — CHRONOLOGICAL (newest→oldest; scope=conversation)]
    Legend: A=assertions, E=exceptions, F=facts
    - turn_123 (current turn) (2026-01-26T15:03Z) obj="Fix diagram" topics=[mermaid, diagram]
      A: format=mermaid (must, scope=objective) | E: (none) | F: (none)
    - turn_122 (2026-01-26T14:50Z) obj="..." topics=[...]
      A: ... | E: ... | F: ...
    """
    if not turn_memories:
        return ""
    lines = [
        f"[TURN MEMORIES — CHRONOLOGICAL ({order_label}; scope={scope_label})]",
        "Legend: A=assertions, E=exceptions, F=facts",
    ]

    def _fmt_signals(items: List[Dict[str, Any]]) -> str:
        out = []
        for it in items or []:
            key = (it.get("key") or "").strip()
            if not key:
                continue
            val = it.get("value")
            sev = (it.get("severity") or "").strip()
            scope = (it.get("scope") or "").strip()
            applies = (it.get("applies_to") or "").strip()
            meta = []
            if sev:
                meta.append(sev)
            if scope:
                meta.append(f"scope={scope}")
            if applies:
                meta.append(f"applies_to={applies}")
            meta_txt = f" ({', '.join(meta)})" if meta else ""
            out.append(f"{key}={val}{meta_txt}")
        return "; ".join(out)

    count = 0
    for fp_doc in (turn_memories or []):
        if count >= max_items:
            break
        try:
            fp_obj = TurnFingerprintV1(
                version=(fp_doc.get("version") or "v1"),
                turn_id=(fp_doc.get("turn_id") or ""),
                objective=(fp_doc.get("objective") or ""),
                topics=(fp_doc.get("topics") or []),
                assertions=(fp_doc.get("assertions") or []),
                exceptions=(fp_doc.get("exceptions") or []),
                facts=(fp_doc.get("facts") or []),
                assistant_signals=[],
                ctx_retrieval_queries=(fp_doc.get("ctx_retrieval_queries") or []),
                made_at=(fp_doc.get("made_at") or fp_doc.get("ts") or ""),
            )
        except Exception:
            continue
        tid = (fp_doc.get("turn_id") or "").strip()
        ts = (fp_doc.get("made_at") or fp_doc.get("ts") or "").strip()
        cur_label = " (current turn)" if current_turn_id and tid == current_turn_id else ""
        obj = (fp_obj.objective or "").strip()
        topics = list(fp_obj.topics or [])
        topics_txt = f"[{', '.join(topics)}]" if topics else "[]"
        lines.append(f"- {tid}{cur_label} ({ts}) obj=\"{obj}\" topics={topics_txt}")
        a_txt = _fmt_signals(fp_obj.assertions or []) or "(none)"
        e_txt = _fmt_signals(fp_obj.exceptions or []) or "(none)"
        f_txt = _fmt_signals(fp_obj.facts or []) or "(none)"
        lines.append(f"  A: {a_txt} | E: {e_txt} | F: {f_txt}")
        count += 1
    return "\n".join(lines) if len(lines) > 1 else ""


def format_turn_memory_fingerprint(fp_doc: Optional[Dict[str, Any]]) -> str:
    if not isinstance(fp_doc, dict):
        return ""

    def _fmt_signals(items: List[Dict[str, Any]]) -> str:
        out = []
        for it in items or []:
            key = (it.get("key") or "").strip()
            if not key:
                continue
            val = it.get("value")
            sev = (it.get("severity") or "").strip()
            scope = (it.get("scope") or "").strip()
            applies = (it.get("applies_to") or "").strip()
            meta = []
            if sev:
                meta.append(sev)
            if scope:
                meta.append(f"scope={scope}")
            if applies:
                meta.append(f"applies_to={applies}")
            meta_txt = f" ({', '.join(meta)})" if meta else ""
            out.append(f"{key}={val}{meta_txt}")
        return "; ".join(out) if out else "(none)"

    obj = (fp_doc.get("objective") or "").strip()
    topics = list(fp_doc.get("topics") or [])
    topics_txt = ", ".join(topics) if topics else "(none)"
    assertions = _fmt_signals(list(fp_doc.get("assertions") or []))
    exceptions = _fmt_signals(list(fp_doc.get("exceptions") or []))
    facts = _fmt_signals(list(fp_doc.get("facts") or []))

    lines = [
        "# Turn Memory (Fingerprint)",
        f"- Objective: {obj or '(none)'}",
        f"- Topics: {topics_txt}",
        f"- Assertions: {assertions}",
        f"- Exceptions: {exceptions}",
        f"- Facts: {facts}",
    ]
    return "\n".join(lines)


def format_assistant_signals_for_turn(fp_doc: Optional[Dict[str, Any]]) -> str:
    if not isinstance(fp_doc, dict):
        return ""
    signals = list(fp_doc.get("assistant_signals") or [])
    if not signals:
        return ""
    lines = ["# Assistant Signals (extracted)"]
    for s in signals:
        key = (s.get("key") or "").strip()
        if not key:
            continue
        val = s.get("value")
        sev = (s.get("severity") or "").strip()
        scope = (s.get("scope") or "").strip()
        applies = (s.get("applies_to") or "").strip()
        meta = []
        if sev:
            meta.append(sev)
        if scope:
            meta.append(f"scope={scope}")
        if applies:
            meta.append(f"applies_to={applies}")
        meta_txt = f" ({', '.join(meta)})" if meta else ""
        lines.append(f"- {key} = {val}{meta_txt}")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_feedback_block(
    feedback_items: Optional[List[Dict[str, Any]]],
    *,
    order_label: str = "newest→oldest",
    scope_label: str = "conversation",
) -> str:
    """
    Render feedback entries (reaction artifacts).

    Example:
    [USER FEEDBACK — CHRONOLOGICAL (newest→oldest; scope=conversation)]
    - turn_id=turn_123 | 2026-01-26T15:05Z | time_since=10m | origin=user | reaction=ok
      text: "Looks good"
    """
    if not feedback_items:
        return ""
    lines = [f"[USER FEEDBACK — CHRONOLOGICAL ({order_label}; scope={scope_label})]"]
    for fb in feedback_items or []:
        tid = (fb.get("turn_id") or "").strip()
        ts = (fb.get("ts") or "").strip()
        origin = (fb.get("origin") or "").strip() or "unknown"
        reaction = (fb.get("reaction") or "").strip() or "none"
        text = (fb.get("text") or "").strip()
        objective = (fb.get("objective") or "").strip()
        header = f"- turn_id={tid}"
        if ts:
            header += f" | {ts}"
            try:
                s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
                when = _dt.datetime.fromisoformat(s)
                now = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
                delta = now - when
                total_sec = int(delta.total_seconds())
                if total_sec >= 0:
                    days = total_sec // 86400
                    hours = (total_sec % 86400) // 3600
                    mins = (total_sec % 3600) // 60
                    if days >= 30:
                        months = days // 30
                        weeks = (days % 30) // 7
                        age = f"{months}mo"
                        if weeks:
                            age += f"{weeks}w"
                    elif days >= 7:
                        weeks = days // 7
                        rem_days = days % 7
                        age = f"{weeks}w"
                        if rem_days:
                            age += f"{rem_days}d"
                    elif days > 0:
                        age = f"{days}d{hours}h"
                    elif hours > 0:
                        age = f"{hours}h{mins}m"
                    else:
                        age = f"{mins}m"
                    header += f" | time_since={age}"
            except Exception:
                pass
        header += f" | origin={origin} | reaction={reaction}"
        lines.append(header)
        if objective:
            lines.append(f"  - objective: {objective}")
        if text:
            lines.append(f"  - text: {text}")
    return "\n".join(lines) if len(lines) > 1 else ""

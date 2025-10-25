# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/retrieval/documenting.py

import datetime as _dt
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

@dataclass
class ViewConfig:
    # Truncation policy for historical turns
    max_user_chars: int = 1200
    max_assistant_chars: int = 1600
    max_deliverable_chars: int = 2400
    # Expand this turn fully (e.g., the last non-clarification)
    expand_turn_id: Optional[str] = None
    # When True, do not truncate any blocks for the expand_turn_id
    expand_turn_full: bool = True

def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if not n or len(s) <= n else (s[: n - 1] + "…")

def _maybe_truncate(s: str, n: int, do_truncate: bool) -> str:
    if not do_truncate:
        return s or ""
    return _truncate(s or "", n)

def _render_objective_memory_block(selected_bucket_cards: list[dict], objective_memory_timelines: dict) -> str:
    selected_bucket_cards = list(selected_bucket_cards or [])
    objective_memory_timelines = dict(objective_memory_timelines or {})
    if not selected_bucket_cards:
        return ""

    lines = ["[OBJECTIVE MEMORY — SELECTED BUCKETS]"]
    for card in selected_bucket_cards[:3]:
        bid = card.get("bucket_id") or ""
        nm  = (card.get("name") or bid or "(bucket)").strip()
        desc = (card.get("short_desc") or card.get("objective_text") or "").strip()
        lines.append(f"\n• Bucket: {nm}")
        if desc:
            lines.append(f"  Description: {desc}")
        for s in (objective_memory_timelines.get(bid, []) or [])[:3]:
            oh = (s.get("objective_hint") or "").strip()
            tf, tt = s.get("ts_from",""), s.get("ts_to","")
            if oh:
                lines.append(f"  └─ [{tf}..{tt}] {oh}")
    return "\n".join(lines) + "\n"

def _iso(ts: str | None) -> str:
    if not ts: return ""
    try:
        # keep the original Z if present
        return _dt.datetime.fromisoformat(ts.replace("Z","+00:00")).replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00","Z")
    except Exception:
        return ts

def _source_title(src: dict) -> str:
    role = (src or {}).get("role") or "artifact"
    tid  = (src or {}).get("turn_id")
    mid  = (src or {}).get("message_id")
    who  = {"user":"user message", "assistant":"assistant reply"}.get(role, "artifact")
    extra = f" — turn {tid}" if tid else ""
    return f"Quoted ({who}{extra})"

def _format_context_block(title: str, items: list[dict], is_current_turn: bool = False) -> str:
    """
    Render context with clear attribution.

    Args:
        title: Section title
        items: Context items
        is_current_turn: If True, marks as current turn context
    """
    if not items:
        return ""

    marker = " (CURRENT TURN)" if is_current_turn else ""
    out = [
        f"### {title}{marker}",
        "_This section contains inferred context and metadata; **not** authored by the user._"
    ]

    first = True
    for it in items:
        txt = (it.get("text") or it.get("content") or "").strip()
        if not txt:
            continue
        if not first:
            out.append("\n---\n")
        out.append(txt)
        first = False

    return "\n".join(out)

def _format_assistant_internal_block(title: str, items: list[dict]) -> str:
    """
    Render assistant-internal artifacts verbatim, clearly marked as internal.
    """
    if not items:
        return ""

    out = [
        f"### {title}",
        "_Assistant internal response — not shown to the user in the original turn._"
    ]

    first = True
    for it in items:
        # prefer 'title' when available, keep content verbatim
        title = (it.get("title") or "").strip()
        body = (it.get("text") or it.get("content") or "").strip()
        if not body and not title:
            continue
        if not first:
            out.append("\n---\n")
        if title:
            out.append(f"**{title}**")
        if body:
            out.append(body)
        first = False

    return "\n".join(out)

def _format_user_facing_deliverables(items: list[dict], *, max_chars: int = 0, do_truncate: bool = False) -> str:
    """Format deliverables that were SHOWN to the user (optionally truncated)."""
    if not items:
        return ""

    parts = ["### Deliverables Provided to User", "_These materials were delivered to the user in this turn:_", ""]
    for item in items:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        parts.append(_maybe_truncate(content, max_chars, do_truncate))
        parts.append("")
    return "\n".join(parts)

def _messages_with_context(
        system_message: str|SystemMessage,
        prior_pairs: list[dict],
        current_user_text: str,
        current_context_items: list[dict],
        turn_artifact: dict,
        current_turn_id: str = None
) -> list:
    """
    Build message history with clear attribution and proper formatting.

    Structure:
      [SystemMessage(main_sys),
       (for each prior pair)
          HumanMessage(<prior user [timestamp + turn_id] + context>),
          AIMessage(<internal context + internal artifacts + user-facing deliverables + answer>),
       HumanMessage(<current user + [CURRENT TURN marker + timestamp + turn_id] + current context + turn artifact>)]
    """
    def _turn_artifact_heading(ta: Optional[dict]) -> Tuple[str, Optional[str]]:
        if not ta:
            return "", None
        txt = ta.get("text")
        meta = ta.get("meta") or {}
        kind = meta.get("kind") or ""
        if isinstance(txt, str):
            if "[codegen.program.presentation]" in txt.lower() or kind == "codegen.program.presentation":
                return "Solver Program Presentation", "presentation"
            elif "[solver.failure]" in txt.lower() or kind == "solver.failure":
                return "Solver Failure", "failure"
        return "", None

    msgs = [SystemMessage(content=system_message) if isinstance(system_message, str) else system_message]

    # 1) Prior (materialized) turns — chronological
    for p in prior_pairs or []:
        u = p.get("user") or {}
        a = p.get("assistant") or {}
        arts = p.get("artifacts") or []
        compressed_log = p.get("compressed_turn") or None
        turn_id = p.get("turn_id") or ""

        # Extract timestamps
        ts_u = _iso(u.get("ts"))
        ts_turn = _iso(a.get("ts") or u.get("ts"))

        # Separate artifacts by visibility
        internal_artifacts = []  # program presentation, project_log
        user_facing_deliverables = []  # actual deliverables shown to user

        for art in arts:
            kind = art.get("kind") or ""
            # Internal: program presentation (digest), project log (working draft)
            if kind in ("project.log", "codegen.program.presentation", "solver.failure"):
                internal_artifacts.append(art)
            # User-facing: deliverables (files, documents)
            elif kind in ("deliverables.list", "deliverable.full"):
                user_facing_deliverables.append(art)
            else:
                # Default: treat as user-facing if unsure
                user_facing_deliverables.append(art)

        # === HUMAN (prior user) ===
        u_text = (u.get("text") or "").strip()

        user_parts: List[str] = [u_text]

        # Add service metadata section AFTER user message
        metadata_parts = ["", "---", "# Service metadata"]
        if turn_id:
            metadata_parts.append(f"Turn ID: {turn_id}")
        if ts_u:
            metadata_parts.append(f"Timestamp: {ts_u}")

        user_parts.extend(metadata_parts)

        msgs.append(HumanMessage(content="\n".join(user_parts)))

        # === ASSISTANT (prior assistant) ===
        assistant_parts: List[str] = []

        # A) Internal thinking (ctx.used from turn log)
        turn_ctx = ""
        if compressed_log:
            try:
                turn_ctx = compressed_log.ctx_used_bullets or ""
            except Exception:
                turn_ctx = ""

        if turn_ctx:
            assistant_parts.append("**Context used in this turn:**")
            assistant_parts.append(turn_ctx)
            assistant_parts.append("")

        # B) Internal artifacts (program presentation, project log)
        if internal_artifacts:
            block = _format_assistant_internal_block(
                "Internal Working Materials",
                internal_artifacts
            )
            if block:
                assistant_parts.append(block)

        # C) User-facing deliverables (what was actually shown to user)
        if user_facing_deliverables:
            block = _format_user_facing_deliverables(user_facing_deliverables)
            if block:
                assistant_parts.append(block)

        # D) The actual assistant answer
        a_text = (a.get("text") or "").strip()
        if a_text:
            assistant_parts.append("**Answer (shown to user):**")
            assistant_parts.append(a_text)

        msgs.append(AIMessage(content="\n".join([s for s in assistant_parts if s])))

    # === 2) Current turn ===
    ta_heading, ta_type = _turn_artifact_heading(turn_artifact)

    # Get current timestamp
    try:
        ts_current = _dt.datetime.utcnow().isoformat() + "Z"
    except Exception:
        ts_current = ""

    payload_parts: List[str] = []

    # A) User message FIRST (without timestamp)
    payload_parts.append(current_user_text.strip())
    payload_parts.append("")

    # B) Service metadata section AFTER user message - marked as CURRENT TURN
    payload_parts.append("---")
    payload_parts.append("# Service metadata (CURRENT TURN)")
    if current_turn_id:
        payload_parts.append(f"Turn ID: {current_turn_id}")
    payload_parts.append(f"Timestamp: {ts_current}")
    payload_parts.append("")

    # C) Current turn context (turn log, memories, inferred data)
    current_turn_items = []
    earlier_items = []

    for item in (current_context_items or []):
        txt = (item.get("text") or item.get("content") or "").strip()
        if not txt:
            continue

        # Items with turn-specific markers are current
        if any(marker in txt for marker in ["[turn_log]", "[objective]", "[note]", "[ctx.used]", "[solver"]):
            current_turn_items.append(item)
        # Items with historical markers are earlier context
        elif "[EARLIER TURNS" in txt or "turn_id]" in txt:
            earlier_items.append(item)
        else:
            current_turn_items.append(item)

    # Show current turn context with clear CURRENT TURN marker
    if current_turn_items:
        ctx_block = _format_context_block(
            "Inferred Context and Turn Log",
            current_turn_items,
            is_current_turn=True  # Mark as current turn
        )
        if ctx_block:
            payload_parts.append(ctx_block)
            payload_parts.append("")

    # D) Turn solution/failure artifact (the actual solver output)
    if ta_type and turn_artifact:
        ta_text = (turn_artifact.get("text") or "").strip()
        if ta_text:
            if ta_type == "presentation":
                intro = (
                    f"### {ta_heading} (CURRENT TURN)\n"
                    "_This is the solver's internal digest of work done this turn. **Not** authored by the user._\n"
                    "_Use this to understand what was produced. The actual deliverables are what the user receives. "
                    "You can treat it as a primary answer. If incomplete, present the partial result and request clarification._"
                )
            else:  # failure
                intro = (
                    f"### {ta_heading} (CURRENT TURN)\n"
                    "_The solver encountered an error this turn. **Not** authored by the user._\n"
                    "_Use this to inform the user about limitations and suggest next steps._"
                )

            payload_parts.append(intro)
            payload_parts.append("")
            payload_parts.append("[solver.failure]" if ta_type == "failure" else "[codegen.program.presentation]")
            payload_parts.append(ta_text)
            payload_parts.append("")

    # E) Earlier context (lower priority, historical)
    if earlier_items:
        earlier_block = _format_context_block(
            "Earlier Context from Previous Turns",
            earlier_items,
            is_current_turn=False
        )
        if earlier_block:
            payload_parts.append("---")
            payload_parts.append("")
            payload_parts.append(earlier_block)

    msgs.append(HumanMessage(content="\n".join([p for p in payload_parts if p])))
    return msgs
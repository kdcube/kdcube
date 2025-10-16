# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/retrieval/documenting.py

import datetime as _dt
from typing import Optional, Tuple, List

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

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

def _format_context_block(title: str, items: list[dict]) -> str:
    """
    Render context *verbatim* from artifact texts, with light separation.
    No parsing, no KVs, no reformatting — exactly as stored.
    (User-facing: "not authored by the user")
    """
    if not items:
        return ""

    out = [
        f"### {title}",
        "_This block is system-provided context related to this message; **not** authored by the user._"
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

def _messages_with_context(
        system_message: str|SystemMessage,
        prior_pairs: list[dict],
        current_user_text: str,
        current_context_items: list[dict],
        turn_artifact: dict
) -> list:
    """
    Build message history with clear attribution and proper formatting.

    Structure:
      [SystemMessage(main_sys),
       (for each prior pair)
          HumanMessage(<prior user + timestamp>),
          AIMessage(<internal context + internal artifacts + user-facing deliverables + answer>),
       HumanMessage(<current user + current context + turn artifact>)]
    """
    def _turn_artifact_heading(ta: Optional[dict]) -> Tuple[str, Optional[str]]:
        if not ta:
            return "", None
        txt = ta.get("text")
        meta = ta.get("meta") or {}
        kind = meta.get("kind") or ""
        if isinstance(txt, str):
            if "[codegen.program.presentation]" in txt.lower() or kind == "codegen.program.presentation":
                return "Solver Program Presentation (THIS TURN)", "presentation"
            elif "[solver.failure]" in txt.lower() or kind == "solver.failure":
                return "Solver Failure (THIS TURN)", "failure"
        return "", None

    def _format_context_block(title: str, items: list[dict]) -> str:
        if not items:
            return ""

        parts = [f"### {title}"]
        parts.append("_This section contains internal context; **not** authored by the user._")
        parts.append("")

        for item in items:
            content = item.get("content") or ""
            if content:
                parts.append(content)
                parts.append("")

        return "\n".join(parts)

    def _format_assistant_internal_block(title: str, items: list[dict]) -> str:
        """Format artifacts that are INTERNAL to assistant (not shown to user)."""
        if not items:
            return ""

        parts = [f"### {title}"]
        parts.append("_Internal working materials (not shown to user):_")
        parts.append("")

        for item in items:
            item_title = item.get("title") or ""
            content = item.get("content") or ""
            kind = item.get("kind") or ""

            # Format based on kind
            if kind == "project.log":
                parts.append(f"**{item_title}**")
                parts.append(content)
                parts.append("")
            elif kind == "codegen.program.presentation":
                # This is the solver's internal digest
                parts.append(f"**{item_title}**")
                parts.append("_Solver's summary of what was produced:_")
                parts.append(content)
                parts.append("")
            else:
                if item_title:
                    parts.append(f"**{item_title}**")
                parts.append(content)
                parts.append("")

        return "\n".join(parts)

    def _format_user_facing_deliverables(items: list[dict]) -> str:
        """Format deliverables that were SHOWN to the user."""
        if not items:
            return ""

        parts = [f"### Deliverables Provided to User"]
        parts.append("_These materials were delivered to the user in this turn:_")
        parts.append("")

        for item in items:
            content = item.get("content") or ""
            if content:
                parts.append(content)
                parts.append("")

        return "\n".join(parts)

    msgs = [SystemMessage(content=system_message) if isinstance(system_message, str) else system_message]

    # 1) Prior (materialized) turns — chronological
    for p in prior_pairs or []:
        u = p.get("user") or {}
        a = p.get("assistant") or {}
        arts = p.get("artifacts") or []
        compressed_log = p.get("compressed_log") or None

        # Extract timestamps
        ts_u = _iso(u.get("ts"))
        ts_turn = _iso(a.get("ts") or u.get("ts"))

        # Separate artifacts by visibility
        internal_artifacts = []  # program presentation, project_log
        user_facing_deliverables = []  # actual deliverables shown to user

        for art in arts:
            kind = art.get("kind") or ""
            # Internal: program presentation (digest), project log (working draft)
            if kind in ("project.log", "codegen.program.presentation"):
                internal_artifacts.append(art)
            # User-facing: deliverables (files, documents)
            elif kind in ("deliverables.list", "deliverable.full"):
                user_facing_deliverables.append(art)
            # Solver failure is internal too
            elif kind == "solver.failure":
                internal_artifacts.append(art)
            else:
                # Default: treat as user-facing if unsure
                user_facing_deliverables.append(art)

        # === HUMAN (prior user) ===
        u_text = (u.get("text") or "").strip()
        u_payload = f"[{ts_u}]\n{u_text}"
        msgs.append(HumanMessage(content=u_payload))

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

    # A) User message with timestamp
    payload_parts.append(f"[{ts_current}]")
    payload_parts.append(current_user_text.strip())
    payload_parts.append("")

    # B) Current turn context (turn log, memories)
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

    # Show current turn context
    if current_turn_items:
        ctx_block = _format_context_block(
            "Context — not authored by the user",
            current_turn_items
        )
        if ctx_block:
            payload_parts.append(ctx_block)
            payload_parts.append("")

    # C) Turn solution/failure artifact (the actual solver output)
    if ta_type and turn_artifact:
        ta_text = (turn_artifact.get("text") or "").strip()
        if ta_text:
            if ta_type == "presentation":
                intro = (
                    f"### {ta_heading}\n"
                    "_This is the solver's internal digest of work done this turn. **Not** authored by the user._\n"
                    "_Use this to understand what was produced. The actual deliverables are what the user receives. You can treat it as a primary answer. If incomplete, present the partial result and request clarification.__"
                )
            else:  # failure
                intro = (
                    f"### {ta_heading}\n"
                    "_The solver encountered an error. **Not** authored by the user._\n"
                    "_Use this to inform the user about limitations and suggest next steps._"
                )

            payload_parts.append(intro)
            payload_parts.append("")
            payload_parts.append("[solver.failure]" if ta_type == "failure" else "[codegen.program.presentation]")
            payload_parts.append(ta_text)
            payload_parts.append("")

    # D) Earlier context (lower priority)
    if earlier_items:
        earlier_block = _format_context_block(
            "Earlier Context — not authored by the user",
            earlier_items
        )
        if earlier_block:
            payload_parts.append("---")
            payload_parts.append("")
            payload_parts.append(earlier_block)

    msgs.append(HumanMessage(content="\n".join([p for p in payload_parts if p])))
    return msgs
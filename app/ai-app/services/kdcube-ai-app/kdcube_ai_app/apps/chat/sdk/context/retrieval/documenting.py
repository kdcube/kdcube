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

# def _messages_with_context(
#         system_text: str,
#         prior_pairs: list[dict],
#         current_user_text: str,
#         current_context_items: list[dict],
#         turn_artifact: dict
# ) -> list:
#     """
#     Build:
#       [SystemMessage(main_sys),
#        (for each prior pair) HumanMessage(<prior user + prior artifacts>), AIMessage(<prior assistant>),
#        HumanMessage(<current user + current ctx> [+ separate TURN SOLUTION / FAILURE block])]
#     """
#     def _turn_artifact_heading(ta: Optional[dict]) -> Tuple[str, Optional[str]]:
#         if not ta:
#             return "", None
#         txt = ta.get("text")
#         meta = ta.get("meta") or {}
#         kind = meta.get("kind") or ""
#         # we only care about heading/type; content is handled below
#         if isinstance(txt, str):
#             if "[codegen.program.presentation]" in txt.lower() or kind == "codegen.program.presentation":
#                 return "TURN SOLUTION — Program Presentation (generated this turn)", "presentation"
#             elif "[solver.failure]" in txt.lower() or kind == "solver.failure":
#                 return "TURN SOLUTION FAILURE — Failure explanation (generated this turn)", "failure"
#         return "", None
#
#     def _format_ctx_block(title: str, items: list[dict]) -> str:
#         return _format_context_block(title, items) if items else ""
#
#     msgs = [SystemMessage(content=system_text)]
#
#     # 1) Prior (materialized) turns — chronological
#     for p in prior_pairs or []:
#         u = p.get("user") or {}
#         a = p.get("assistant") or {}
#         arts = p.get("artifacts") or []
#         compressed_log = p.get("compressed_log") or None
#         turn_ctx = ""
#         if compressed_log:
#             turn_ctx = compressed_log.ctx_used_bullets
#         ts_u = _iso(u.get("ts"))
#
#         # prefer assistant turn timestamp; fall back to user-side if missing
#         ts_turn = _iso(a.get("ts") or u.get("ts"))
#         # Attach the turn timestamp to each artifact’s title and meta (no IDs)
#         arts_with_ts = []
#         for art in arts:
#             art2 = dict(art) if isinstance(art, dict) else {"type": "text", "content": str(art)}
#             # carry over or synthesize a human title; then append ISO ts
#             base_title = (art2.get("title") or _source_title(art2)).strip()
#             titled = f"{base_title} — [{ts_turn}]" if ts_turn else base_title
#             art2["title"] = titled
#             # stash ts in meta for downstream consumers
#             meta = dict(art2.get("meta") or {})
#             if ts_turn:
#                 meta["turn_ts"] = ts_turn
#             art2["meta"] = meta
#             arts_with_ts.append(art2)
#         u_text = (u.get("text") or "").strip()
#         ctx_block = _format_ctx_block("Context — not authored by the user", arts_with_ts)
#         u_payload = (f"[{ts_u}]\n{u_text}" + (f"\n\n{ctx_block}" if ctx_block else "")).strip()
#         msgs.append(HumanMessage(content=u_payload))
#         msgs.append(AIMessage(content=(a.get("text") or "").strip()))
#
#     # 2) Current turn
#     #    - Always label current non-artifact context as "Context — not authored by the user"
#     #    - Render artifact (if any) as its own block with a dedicated heading
#     ta_heading, ta_type = _turn_artifact_heading(turn_artifact)
#     solution_hint = ""
#     if ta_type == "presentation":
#         solution_hint = (
#             "\n\n[TURN SOLUTION]\n"
#             "The block below is the solver’s Program Presentation for this turn. "
#             "Use it as the primary answer. If it’s incomplete, ask for the missing inputs or present the partial result and request confirmation."
#         )
#     elif ta_type == "failure":
#         solution_hint = (
#             "\n\n[TURN SOLUTION FAILURE]\n"
#             "The block below is the solver’s explanation of why it could not produce a solution for this turn. "
#             "Use it to consider our ability to solve the user request and gently inform the user about the limitation in their request completion and possible next steps."
#         )
#
#     # Current turn: base payload
#     cur_payload = current_user_text.strip()
#
#     if solution_hint:
#         cur_payload += solution_hint
#
#     # Current, non-artifact context
#     ctx_curr = _format_ctx_block("Context — not authored by the user", current_context_items)
#     if ctx_curr:
#         cur_payload += ("\n\n" + ctx_curr)
#
#     # Current artifact as a separate block
#     if ta_heading and isinstance(turn_artifact, dict):
#         ta_text = (turn_artifact.get("text") or "").strip()
#         if ta_text:
#             # add the current-turn timestamp to the title & meta as well
#             try:
#                 # we don’t have `a`/`u` here; use “now” for current turn’s artifact
#                 ts_current = _dt.datetime.utcnow().isoformat()+"Z"
#             except Exception:
#                 ts_current = None
#             ta_block = _format_ctx_block(ta_heading, [{
#                 "type": "text",
#                 "title": (ta_heading + (f" — [{ts_current}]" if ts_current else "")),
#                 "content": ta_text
#             }])
#             cur_payload += ("\n\n" + ta_block)
#
#     msgs.append(HumanMessage(content=cur_payload))
#     return msgs

def _messages_with_context(
        system_text: str,
        prior_pairs: list[dict],
        current_user_text: str,
        current_context_items: list[dict],
        turn_artifact: dict
) -> list:
    """
    Build:
      [SystemMessage(main_sys),
       (for each prior pair)
          HumanMessage(<prior user + prior artifacts>),
          AIMessage(<assistant-internal ctx + assistant-internal artifacts + answer>),
       HumanMessage(<current user + current ctx> [+ separate TURN SOLUTION / FAILURE block])]
    """
    def _turn_artifact_heading(ta: Optional[dict]) -> Tuple[str, Optional[str]]:
        if not ta:
            return "", None
        txt = ta.get("text")
        meta = ta.get("meta") or {}
        kind = meta.get("kind") or ""
        # we only care about heading/type; content is handled below
        if isinstance(txt, str):
            if "[codegen.program.presentation]" in txt.lower() or kind == "codegen.program.presentation":
                return "TURN SOLUTION — Program Presentation (generated this turn)", "presentation"
            elif "[solver.failure]" in txt.lower() or kind == "solver.failure":
                return "TURN SOLUTION FAILURE — Failure explanation (generated this turn)", "failure"
        return "", None

    def _format_ctx_block(title: str, items: list[dict]) -> str:
        return _format_context_block(title, items) if items else ""

    msgs = [SystemMessage(content=system_text)]

    # 1) Prior (materialized) turns — chronological
    for p in prior_pairs or []:
        u = p.get("user") or {}
        a = p.get("assistant") or {}
        arts = p.get("artifacts") or []
        compressed_log = p.get("compressed_log") or None

        # Pull ctx.used bullets (assistant internal thinking) if available
        turn_ctx = ""
        summary_content = ""
        if compressed_log:
            try:
                turn_ctx = compressed_log.ctx_used_bullets  # a single string (bulleted)
                summary_content = (compressed_log.summary_content or "").strip()
            except Exception:
                turn_ctx = ""

        ts_u = _iso(u.get("ts"))

        # prefer assistant turn timestamp; fall back to user-side if missing
        ts_turn = _iso(a.get("ts") or u.get("ts"))

        # Attach the turn timestamp to each artifact’s title and meta (no IDs)
        arts_with_ts = []
        for art in arts:
            art2 = dict(art) if isinstance(art, dict) else {"type": "text", "content": str(art)}
            # carry over or synthesize a human title; then append ISO ts
            base_title = (art2.get("title") or _source_title(art2)).strip()
            titled = f"{base_title} — [{ts_turn}]" if ts_turn else base_title
            art2["title"] = titled
            # stash ts in meta for downstream consumers
            meta = dict(art2.get("meta") or {})
            if ts_turn:
                meta["turn_ts"] = ts_turn
            art2["meta"] = meta
            arts_with_ts.append(art2)

        # HUMAN (prior user)
        u_text = (u.get("text") or "").strip()
        ctx_block = _format_ctx_block("Context — not authored by the user", items=[])  # no user-facing artifacts here
        u_payload = (f"[{ts_u}]\n{u_text}" + (f"\n\n{ctx_block}" if ctx_block else "")).strip()
        msgs.append(HumanMessage(content=u_payload))

        # ASSISTANT (prior assistant) — prepend internal ctx + internal artifacts, then answer
        assistant_parts: List[str] = []

        # Assistant internal thinking (ctx.used)
        if turn_ctx:
            assistant_parts.append("Previously in this thread…")
            assistant_parts.append(turn_ctx)  # already bulleted string
        if summary_content:
            assistant_parts.append("Summary of this turn:")
            assistant_parts.append(summary_content)

        # Assistant internal artifacts BEFORE the answer
        if arts_with_ts:
            block = _format_assistant_internal_block("Assistant internal artifacts", arts_with_ts)
            if block:
                assistant_parts.append("<artifacts>")
                assistant_parts.append(block)
                assistant_parts.append("</artifacts>")

        # The actual assistant text
        a_text = (a.get("text") or "").strip()
        assistant_parts.append("answer")
        assistant_parts.append("<answer>")
        assistant_parts.append(a_text if a_text else "(no assistant answer recorded for this turn)")
        assistant_parts.append("</answer>")

        msgs.append(AIMessage(content="\n\n".join([s for s in assistant_parts if s.strip()])))

    # 2) Current turn
    #    - Always label current non-artifact context as "Context — not authored by the user"
    #    - Render artifact (if any) as its own block with a dedicated heading
    ta_heading, ta_type = _turn_artifact_heading(turn_artifact)
    solution_hint = ""
    if ta_type == "presentation":
        solution_hint = (
            "\n\n[TURN SOLUTION]\n"
            "The block below is the solver’s Program Presentation for this turn. "
            "Use it as the primary answer. If it’s incomplete, ask for the missing inputs or present the partial result and request confirmation."
        )
    elif ta_type == "failure":
        solution_hint = (
            "\n\n[TURN SOLUTION FAILURE]\n"
            "The block below is the solver’s explanation of why it could not produce a solution for this turn. "
            "Use it to consider our ability to solve the user request and gently inform the user about the limitation in their request completion and possible next steps."
        )

    # Current turn: base payload
    cur_payload = current_user_text.strip()
    if solution_hint:
        cur_payload += solution_hint

    # Current, non-artifact context (user-facing)
    ctx_curr = _format_ctx_block("Context — not authored by the user", current_context_items)
    if ctx_curr:
        cur_payload += ("\n\n" + ctx_curr)

    # Current artifact as a separate block (user-facing: shown below user msg)
    if ta_heading and isinstance(turn_artifact, dict):
        ta_text = (turn_artifact.get("text") or "").strip()
        if ta_text:
            try:
                ts_current = _dt.datetime.utcnow().isoformat()+"Z"
            except Exception:
                ts_current = None
            ta_block = _format_context_block(ta_heading, [{
                "type": "text",
                "title": (ta_heading + (f" — [{ts_current}]" if ts_current else "")),
                "content": ta_text
            }])
            cur_payload += ("\n\n" + ta_block)

    msgs.append(HumanMessage(content=cur_payload))
    return msgs
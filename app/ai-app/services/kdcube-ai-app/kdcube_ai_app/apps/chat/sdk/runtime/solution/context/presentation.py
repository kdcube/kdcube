# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/presentation.py

import json, re, time
import datetime as _dt
import traceback
from typing import Dict, Any, Optional, List, Literal, Tuple, Protocol, Type

from kdcube_ai_app.apps.chat.sdk.context.memory.turn_fingerprint import TurnFingerprintV1, render_fingerprint_one_liner
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import BaseTurnView, CompressedTurn, turn_to_pair
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.strategy_and_budget import format_budget_for_llm
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval as ctx_retrieval_module
from kdcube_ai_app.apps.chat.sdk.util import _truncate, _turn_id_from_tags_safe

from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import (
    SolverPresenter,
    SolverPresenterConfig,
    _format_deliverables_flat_with_icons,
    SERVICE_LOG_SLOT,
)
# ----------------- moved from react.context.py
def build_react_playbook(
        *,
        context: ReactContext,
        output_contract: Dict[str, Any],
        max_prior_turns: int = 5,
        max_sources_per_turn: int = 20,
        current_user_markdown: Optional[str] = None,
        turn_view_class: Type[BaseTurnView] = BaseTurnView,
) -> str:
    """
    Unified, LLM-friendly Operational Playbook used by the Decision node.

    ORDER (strict oldest → newest):
      1) Prior turns (historical), strictly oldest → newest
      2) Current turn (live):
         • User Request (what we knew at session start) — comes from scratchpad via `current_user_markdown`
         • Events timeline (appended as the session progresses; short timestamps)
         • Current snapshot (contract/slots/tool results)
    """
    import traceback
    from urllib.parse import urlparse

    def _size(s: Optional[str]) -> int:
        return len(s or "")

    def _short_with_count(text: str, limit: int) -> str:
        if not text:
            return ""
        text = str(text)
        if len(text) <= limit:
            return text
        remaining = len(text) - limit
        return f"{text[:limit]}... (...{remaining} more chars)"

    lines: list[str] = []
    lines.append("# Program History / Operational View")
    lines.append("")
    lines.append("Previews are truncated. Use ctx_tools.fetch_turn_artifacts([turn_ids]) for full content.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---------- Prior Turns (oldest first) ----------
    lines.append("## Prior Turns (oldest first)")

    if not context.history_turns:
        lines.append("(none)")
    else:
        # Sort history_turns oldest → newest
        history_sorted = sorted(
            context.history_turns[:max_prior_turns],
            key=lambda turn_rec: (next(iter(turn_rec.values())) if turn_rec else {}).get("ts", "")
        )

        for idx, turn_rec in enumerate(history_sorted, 1):
            # Extract inner metadata dict from {execution_id: meta}
            try:
                execution_id, meta = next(iter(turn_rec.items()))
            except Exception:
                continue

            turn_id = meta.get("turn_id") or execution_id
            ts_full = (meta.get("ts") or "").strip()
            ts_disp = ts_full[:16].replace("T", " ") if len(ts_full) >= 16 else (ts_full[:10] or "(no date)")

            # ✅ Build TurnView from meta payload
            try:
                tv = turn_view_class.from_turn_log_dict(meta)
            except Exception as ex:
                print(f"Failed to build TurnView for turn {turn_id}: {ex}")
                print(traceback.format_exc())
                continue

            # Header
            lines.append(f"### Turn {turn_id} — {ts_disp} [HISTORICAL]")
            lines.append(f"Fetch with: ctx_tools.fetch_turn_artifacts([\"{turn_id}\"])")
            lines.append("")

            # ✅ Complete turn presentation from TurnView (delegates to SolverPresenter)
            # This includes:
            # - [TURN OUTCOME] status
            # - User prompt (from base summary)
            # - Solver details (project_log, deliverables)
            # - Assistant response (if direct answer)
            turn_presentation = tv.to_solver_presentation(
                user_prompt_limit=600,
                program_log_limit=400,
                include_base_summary=True,  # ← Includes user prompt!
                include_program_log=True,
                include_deliverable_meta=True,
                include_assistant_response=True,
            )

            if turn_presentation.strip():
                lines.append(turn_presentation.strip())
                lines.append("")

            # ✅ Sources (NOT part of solver, formatted separately)
            prior_turn_data = context.prior_turns.get(turn_id, {})
            sources = (prior_turn_data.get("sources") or [])[:max_sources_per_turn]

            if sources:
                src_lines = []
                for i, src in enumerate(sources, 1):
                    if not isinstance(src, dict):
                        continue
                    title = (src.get("title") or "").strip()
                    url = (src.get("url") or "").strip()
                    domain = ""
                    if url:
                        try:
                            domain = urlparse(url).netloc or ""
                        except Exception:
                            domain = url[:30]

                    if title and domain:
                        src_lines.append(f"  {i}. {_short_with_count(title, 80)} ({domain})")
                    elif title:
                        src_lines.append(f"  {i}. {_short_with_count(title, 80)}")
                    elif url:
                        src_lines.append(f"  {i}. {_short_with_count(url, 100)}")

                if src_lines:
                    lines.append(f"**Sources:** ({len(sources)} total)")
                    lines.extend(src_lines)
                    lines.append("")

    lines.append("")

    # ---------- Current Turn (live) ----------
    lines.append("## Current Turn (live — oldest → newest)")

    # 1) What we knew at start: user request for this turn (from scratchpad via param)
    cur_user_msg = (current_user_markdown or "").strip()
    lines.append("**User Request (markdown):**")
    lines.append(_short_with_count(cur_user_msg, 600) if cur_user_msg else "(not recorded)")
    lines.append("")

    # 2) Events timeline (short timestamps)
    lines.append("### Events (oldest → newest)")
    if not context.events:
        lines.append("(no events yet)")
    else:
        for e in context.events:
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            kind = e.get("kind")

            if kind == "decision":
                nxt = e.get("next_step")
                strat = e.get("strategy")
                focus = e.get("focus_slot")
                tc = e.get("tool_call") or {}
                tool_id = tc.get("tool_id")
                art = tc.get("tool_res_artifact") or {}
                art_name = art.get("name") if isinstance(art, dict) else art
                reason = (e.get("reasoning") or "").replace("\n", " ")
                if len(reason) > 140:
                    reason = reason[:137] + "..."
                pieces = [
                    f"next={nxt}",
                    f"strategy={strat}" if strat else None,
                    f"focus={focus}" if focus else None,
                    f"tool={tool_id}->{art_name}" if tool_id or art_name else None,
                    f"reason={reason}" if reason else None,
                ]
                pieces = [p for p in pieces if p]
                lines.append(f"- {ts} — decision: " + " — ".join(pieces))
                continue

            if kind == "tool_started":
                sig = e.get("signature")
                art = e.get("artifact_name")
                if sig:
                    lines.append(f"- {ts} — tool_started: {sig} → {art}")
                else:
                    payload = {k: v for k, v in e.items() if k != "ts"}
                    lines.append(f"- {ts} — tool_started: {json.dumps(payload, ensure_ascii=False)[:400]}")
                continue

            if kind == "tool_finished":
                summ = (e.get("summary") or "")
                lines.append(f"- {ts} — tool_finished: {e.get('tool_id')} → {e.get('status')} "
                             f"[{e.get('artifact_name','?')}] — {summ[:220]}")
                continue

            payload = {k: v for k, v in e.items() if k != "ts"}
            lines.append(f"- {ts} — {kind}: {json.dumps(payload, ensure_ascii=False)[:400]}")

    lines.append("")

    # 3) Live snapshot (current contract/slots/tool results)
    declared = sorted(list((output_contract or {}).keys()))
    filled = sorted(list((context.current_slots or {}).keys()))
    pending = [s for s in declared if s not in filled]

    lines.append("### Current Snapshot")
    lines.append("")
    lines.append("#### Contract Status")
    lines.append(f"- Declared slots: {len(declared)}")
    lines.append(f"- Filled slots  : {len(filled)}  ({', '.join(filled) if filled else '-'})")
    lines.append(f"- Pending slots : {len(pending)}  ({', '.join(pending) if pending else '-'})")
    lines.append("")

    if context.current_slots:
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import format_live_slots

        slots_md = format_live_slots(
            slots=context.current_slots,
            contract=output_contract,
            grouping="flat",  # or "status" if you want grouping
            slot_attrs={"description", "gaps"},
        )

        lines.append("#### Current Slots")
        lines.append("")
        lines.append(slots_md)
        lines.append("")

    if context.current_tool_results:
        items = sorted(
            ((k, v) for k, v in context.current_tool_results.items() if isinstance(v, dict)),
            key=lambda kv: float(kv[1].get("timestamp") or 0.0),
        )

        def _search_context_from_inputs(art: Dict[str, Any]) -> tuple[str, str]:
            inputs = art.get("inputs") or {}
            if not isinstance(inputs, dict):
                return "", ""

            raw_q = inputs.get("queries")
            q_list: list[str] = []
            if isinstance(raw_q, list):
                q_list = [str(q) for q in raw_q if isinstance(q, (str, int, float))]
            elif isinstance(raw_q, str):
                try:
                    parsed = json.loads(raw_q)
                    if isinstance(parsed, list):
                        q_list = [str(q) for q in parsed if isinstance(q, (str, int, float))]
                    else:
                        q_list = [raw_q]
                except Exception:
                    q_list = [raw_q]

            queries_preview = ""
            if q_list:
                q2 = q_list[:2]
                queries_preview = "; ".join([f"\"{q.strip()}\"" for q in q2 if str(q).strip()])
                if len(q_list) > 2:
                    queries_preview += f"; …+{len(q_list) - 2} more"

            obj = inputs.get("objective")
            objective_preview = ""
            if isinstance(obj, str) and obj.strip():
                objective_preview = obj.strip()
                if len(objective_preview) > 160:
                    objective_preview = objective_preview[:157] + "…"

            return queries_preview, objective_preview

        lines.append("#### Current Turn Tool Results — all artifacts (oldest→newest)")
        if not items:
            lines.append("(none)")
            lines.append("")
        else:
            for idx, (art_id, art) in enumerate(items, 1):
                tid = art.get("tool_id", "?")
                error = art.get("error")
                status = "FAILED" if error else "success"
                summ = (art.get("summary") or "").replace("\n", " ")

                if error:
                    lines.append(
                        f"- {idx:02d}. {art_id} ← {tid}: {status} - "
                        f"{error.get('code')}: {error.get('message', '')[:150]}"
                    )
                else:
                    lines.append(
                        f"- {idx:02d}. {art_id} ← {tid}: {status} — {summ}"
                    )

                if tools_insights.is_search_tool(tid):
                    q_prev, obj_prev = _search_context_from_inputs(art)
                    if q_prev or obj_prev:
                        lines.append("    search_context:")
                        if q_prev:
                            lines.append(f"      queries  : {q_prev}")
                        if obj_prev:
                            lines.append(f"      objective: \"{obj_prev}\"")

        lines.append("")

        latest_art_id: str | None = items[-1][0] if items else None
        latest_art: Dict[str, Any] | None = items[-1][1] if items else None

        if latest_art and not latest_art.get("error"):
            call_meta = (context.tool_call_index or {}).get(latest_art_id, {})
            latest_sig = call_meta.get("signature")

            if latest_sig:
                lines.append("##### Latest Tool Call — Invocation")
                lines.append(latest_sig)
                lines.append("")

            summ = (latest_art.get("summary") or "").strip()
            if summ:
                lines.append("##### Latest Tool Result — Summary")
                lines.append(summ)
                lines.append("")

    # ---------- Budget snapshot ----------
    try:
        if hasattr(context, "budget_state") and context.budget_state is not None:
            lines.append("")
            lines.append("## Budget Snapshot")
            lines.append("")
            lines.append(format_budget_for_llm(context.budget_state))
    except Exception:
        pass

    return "\n".join(lines)

# ----------------- moved from sdk.runtime.solution.project_retrieval.py

def _compose_last_materialized_canvas_block(history: list[dict]) -> str:
    """
    Return a compact, self-sufficient block that solvability can read.
    Prefers materialized canvas; falls back to raw canvas, then program presentation.
    """
    if not history:
        return "(no prior project work)"

    try:
        run_id, meta = next(iter(history[0].items()))
    except Exception:
        return "(no prior project work)"

    # 1) Prefer materialized canvas
    mat = (meta.get("project_log_materialized") or {})
    txt = (mat.get("text") or "").strip()
    if txt:
        return f"# Project Log (materialized)\n\n{txt}"

    # 2) Fallback to non-materialized canvas
    raw = (meta.get("project_log") or {})
    txt = (raw.get("text") or raw.get("value") or "").strip()
    if txt:
        return f"# Project Log\n\n{txt}"

    # 3) Fallback to last program presentation
    prez = (meta.get("program_presentation") or "").strip()
    if prez:
        return f"# Program Presentation (fallback)\n\n{prez}"

    # 4) Nothing available
    return "(no prior project work)"


def _short_with_count(text: str, limit: int) -> str:
    """Truncate text and show how much was cut."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    remaining = len(text) - limit
    return f"{text[:limit]}... (...{remaining} more chars)"

def _materialize_glue_canvas(glue_md: str, d_items: list[dict]) -> str:
    """
    Materialize canvas with deliverables from solver.

    Uses SolverPresenter for consistent deliverable formatting.
    """
    if not (glue_md or "").strip():
        return glue_md or ""

    lines = [glue_md.strip(), "", "## Materials (this turn)", ""]

    # Convert d_items to deliverables_map shape for presenter
    dmap = {}
    for item in d_items:
        slot_name = item.get("slot") or ""
        if slot_name == "project_log":
            continue
        dmap[slot_name] = item

    if dmap:
        # Use SolverPresenter for consistent formatting
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import (
            _format_deliverables_flat_with_icons
        )

        deliverables_md, _ = _format_deliverables_flat_with_icons(
            dmap=dmap,
            contract=None,
            content_len=-1,  # Full content
            slot_attr_keys={"description"},
            exclude_slots=["project_log", "project_canvas"],
        )

        lines.append(deliverables_md)

    return "\n".join(lines).strip()

# ----------------- moved from copilot/context/context_reconstruction.py
ViewName = Literal["compact", "materialize"]

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def unwrap_payload(p: dict) -> dict:
    """Accepts {payload:{payload:{...}}} and returns {...} safely."""
    if not isinstance(p, dict): return {}
    x = p.get("payload") if isinstance(p.get("payload"), dict) else p
    y = x.get("payload") if isinstance(x.get("payload"), dict) else x
    return y or {}

def _fmt_ts_for_humans(ts: str) -> str:
    if not ts:
        return "(unknown time)"
    try:
        s = str(ts).strip()
        if s.endswith("Z"): s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo:
            dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    except Exception:
        dt = _dt.datetime.utcnow()
    return dt.strftime("%H:%M %b %d %Y")


def _rel_age(ts: str) -> str:
    """Very coarse age: 'last turn' / 'earlier this week' / 'earlier in this thread'"""
    try:
        t = _dt.datetime.fromisoformat((ts or "").replace("Z","+00:00"))
        delta = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc) - t
        d = delta.days
        if d <= 1: return "last turn"
        if d <= 7: return "earlier this week"
        return "earlier in this thread"
    except Exception:
        return "earlier"


def _title_or_gist(text: str) -> str:
    if not text: return ""
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            return s.lstrip("# ").strip()[:120]
    sents = re.split(r"(?<=[.?!])\s+", text.strip())
    s = sents[0] if sents else text.strip()
    return (s[:117] + "…") if len(s) > 120 else s


def _ctx_line(sn: dict, why: str = "") -> str:
    role = (sn.get("role") or "").lower()
    ts   = sn.get("ts") or ""
    age  = _rel_age(ts)
    kind = (sn.get("kind") or "").lower()
    raw  = (sn.get("raw") or sn.get("text") or sn.get("content") or "") or ""
    gist = _title_or_gist(raw)

    # normalize a friendly type label
    if kind == "codegen.program.presentation" or "program presentation" in gist.lower():
        typ = "Program Presentation"
    elif kind == "project.log":
        typ = "Project log"
    elif role == "user":
        typ = "User message"
    elif role == "assistant":
        typ = "Assistant reply"
    else:
        typ = "Context"

    parts = [f"• {typ} — {gist}"]
    if why: parts.append(f"— {why}")
    return " ".join(parts)


def _turn_id_from_tags(tags: List[str]) -> Optional[str]:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("turn:"):
            return t.split(":", 1)[1]
    return None


def _ts_key(ts) -> float:
    if hasattr(ts, "timestamp"):
        return float(ts.timestamp())
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e12 else float(ts)
    if isinstance(ts, str):
        s = ts.strip()
        try:
            if s.endswith("Z"): s = s[:-1] + "+00:00"
            return _dt.datetime.fromisoformat(s).timestamp()
        except Exception:
            try:
                v = float(s)
                return v / 1000.0 if v > 1e12 else v
            except Exception:
                return float("-inf")
    return float("-inf")


def _is_clarification_turn(saved_payload: Dict[str, Any]) -> bool:
    """
    Decide if a turn was clarification-only.
    We rely on the saved `solver_result.plan.mode` field when present.
    """
    try:
        sr = saved_payload.get("solver_result") or {}
        plan = sr.get("plan") or {}
        mode = (plan.get("mode") or "").strip().lower()
        return mode == "clarification_only"
    except Exception:
        return False

def objective_memory_block(selected_bucket_cards: List[dict],
                            objective_memory_timelines: Dict[str, List[dict]]) -> str:
    cards = list(selected_bucket_cards or [])
    timelines = dict(objective_memory_timelines or {})
    if not cards:
        return ""
    lines = ["[OBJECTIVE MEMORY — SELECTED BUCKETS]"]
    for card in cards[:3]:
        bid = (card.get("bucket_id") or "").strip()
        nm = (card.get("name") or bid or "(bucket)").strip()
        desc = (card.get("short_desc") or card.get("objective_text") or "").strip()
        lines.append(f"\n• Bucket: {nm}")
        if desc:
            lines.append(f"  Description: {desc}")
        for s in (timelines.get(bid, []) or [])[:3]:
            oh = (s.get("objective_hint") or "").strip()
            tf, tt = (s.get("ts_from", "") or ""), (s.get("ts_to", "") or "")
            if oh:
                lines.append(f"  └─ [{tf}..{tt}] {oh}")
    return "\n".join(lines) + "\n"

def _solver_deliverables(saved_payload: Dict[str, Any]) -> Dict[str, Any]:
    sr = saved_payload.get("solver_result") or {}
    execution = (sr.get("execution") or {})
    deliverables_map = execution.get("deliverables") or {}
    return deliverables_map

def _solver_program_presentation(saved_payload: Dict[str, Any]) -> str:
    sr = saved_payload.get("solver_result") or {}
    return (sr.get("program_presentation") or "") or ""

def _solver_failure_artifact(saved_payload: Dict[str, Any], *, turn_id: str) -> Optional[dict]:
    """
    Construct an artifact when the solver reported failure.
    We prefer a markdown from failure_presentation if present; otherwise synthesize.
    """
    sr = saved_payload.get("solver_result") or {}
    failure = sr.get("failure")
    if not failure:
        return None

    fp = sr.get("failure_presentation")
    md = ""

    # If failure_presentation is a pre-rendered markdown string
    if isinstance(fp, str) and fp.strip():
        md = fp.strip()
    # If it's a structured dict, try common shapes
    elif isinstance(fp, dict):
        # Known fields: title, reason, details, hints, markdown
        if isinstance(fp.get("markdown"), str) and fp.get("markdown").strip():
            md = fp.get("markdown").strip()
        else:
            parts = []
            title = fp.get("title") or "Solver Failure"
            reason = fp.get("reason") or fp.get("error") or ""
            details = fp.get("details") or {}
            hints = fp.get("hints") or []
            parts.append(f"# {title}")
            if reason:
                parts.append(f"\n**Reason:** {reason}")
            if details:
                try:
                    parts.append("\n**Details (JSON):**")
                    parts.append("```json")
                    parts.append(json.dumps(details, ensure_ascii=False, indent=2))
                    parts.append("```")
                except Exception:
                    pass
            if hints:
                parts.append("\n**Hints:**")
                for h in hints[:8]:
                    parts.append(f"- {h}")
            md = "\n".join(parts).strip()

    # Fallback synthesis if nothing above yielded content
    if not md:
        parts = ["# Solver Failure", "The solver reported a failure for this turn."]
        try:
            plan = sr.get("plan") or {}
            exec_ = sr.get("execution") or {}
            reason = (plan.get("error") or exec_.get("error") or sr.get("interpretation_instruction") or "").strip()
            if reason:
                parts.append("\n**Reason:** " + reason)
        except Exception:
            pass
        md = "\n".join(parts).strip()

    return {
        "type": "text",
        "title": f"Solver Failure (turn {turn_id})",
        "content": md,
        "turn_id": turn_id,
        "kind": "solver.failure",
    }

def _extract_entries_and_summary(saved_payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    From the saved payload you wrote into the turn log artifact,
    return (entries, turn_summary, gate), where:
      - entries are TurnLogEntry dicts
      - turn_summary is your JSON summary block
      - gate is GateOut-like dict (already JSONable)
    """
    # When you save via save_turn_log_as_artifact(..., log=scratchpad.tlog, payload={...})
    # the ctx store puts both 'text' (markdown) and 'payload' (your dict) under the turn_log artifact.
    # Here we assume `saved_payload` is that dict (already inner-most payload).
    tlog = saved_payload.get("turn_log") or {}
    entries = tlog.get("entries") or []
    turn_summary = saved_payload.get("turn_summary") or {}
    gate = saved_payload.get("gate") or {}
    return entries, turn_summary, gate

def _memories_artifact_from_gate(gate: Dict[str, Any], *, turn_id: str) -> Optional[dict]:
    """
    Build a compact memories artifact from GateOut fields for the turn.
    Surfaces assertions / exceptions / facts so callers have `turn_log.memories`.
    """
    if not isinstance(gate, dict) or not gate:
        return None

    assertions = list(gate.get("assertions") or [])
    exceptions = list(gate.get("exceptions") or [])
    facts = list(gate.get("facts") or [])

    lines: List[str] = ["# Memories (Gate extraction)"]
    if gate.get("current_objective_short"):
        lines.append(f"- Objective: {gate.get('current_objective_short')}")
    if gate.get("topics"):
        try:
            tnames = [t.get("name") for t in gate.get("topics") if isinstance(t, dict)]
            tnames = [t for t in tnames if t]
            if tnames:
                lines.append(f"- Topics: {', '.join(tnames[:8])}")
        except Exception:
            pass

    if assertions:
        lines.append("\n## Assertions")
        for a in assertions[:12]:
            key = a.get("key")
            val = a.get("value")
            desired = a.get("desired", True)
            scope = a.get("scope", "conversation")
            conf = a.get("confidence", 0.0)
            mark = "" if desired else " (avoid)"
            lines.append(f"- {key} = {val}{mark}  — scope={scope}, conf={conf:.2f}")

    if exceptions:
        lines.append("\n## Exceptions")
        for e in exceptions[:8]:
            rk = e.get("rule_key")
            val = e.get("value")
            scope = e.get("scope", "conversation")
            conf = e.get("confidence", 0.0)
            lines.append(f"- EXC[{rk}] = {val}  — scope={scope}, conf={conf:.2f}")

    if facts:
        lines.append("\n## Facts")
        for f in facts[:12]:
            key = f.get("key")
            val = f.get("value")
            scope = f.get("scope", "conversation")
            conf = f.get("confidence", 0.0)
            lines.append(f"- {key} = {val}  — scope={scope}, conf={conf:.2f}")

    body = "\n".join(lines).strip()
    if not body:
        return None

    return {
        "type": "text",
        "title": f"Memories (turn {turn_id})",
        "content": body,
        "turn_id": turn_id,
        "kind": "turn_log.memories",
    }

# ---------------------------------------------------------------------------
# Pairs building from guess_ctx (already-fetched turn logs)
# ---------------------------------------------------------------------------

def _pairs_from_guess_ctx_turn_logs(
        guess_ctx: dict,
        *,
        view: ViewName = "compact",
        trunc_user: int = 600,
        trunc_assistant: int = 900
) -> list[dict]:
    """
    Build prior pairs from guess_ctx['last_turns_details'].
    Uses CompressedTurn built from structured data (entries + turn_summary)
    and enriches with gate objective when present in payload.

    Args:
        guess_ctx: Context dict with 'last_turns_details' containing turn metadata
        view: "compact" (default) | "materialize" (no special behavior here; materialization is handled in _materialize_prior_pairs)
        trunc_user, trunc_assistant: character limits for compact view

    Returns:
        List of pairs: [{"turn_id": str, "user": {"text": str, "ts": str}, "assistant": {...}}]
    """
    items = list((guess_ctx or {}).get("last_turns_details") or [])
    out: list[dict] = []

    for it in items:
        tid = it.get("turn_id")
        ts = it.get("ts") or ""
        one = (it.get("insights_one_liner") or "").strip()
        saved_payload = it.get("log_payload") or {}
        if not isinstance(saved_payload, dict):
            continue

        try:
            # extract structured sources
            entries, turn_summary, gate = _extract_entries_and_summary(saved_payload)

            if not entries or not turn_summary:
                continue

            # Build CompressedTurn from structured data
            compressed_turn = CompressedTurn.from_structured(entries, turn_summary)

            # gate objective short → into context lines via insights
            g_obj = (gate.get("current_objective_short") or "").strip()
            if g_obj:
                # put it in insights line to surface in user context block
                compressed_turn.insights = (one + ("; " if one and g_obj else "") + g_obj) if one or g_obj else ""

            # Convert to user/assistant pair
            pair = turn_to_pair(compressed_turn)

            # truncate for compact view
            user_text = pair.get("user") or ""
            assistant_text = pair.get("assistant") or ""
            if view == "compact":
                user_text = _truncate(user_text, trunc_user)
                assistant_text = _truncate(assistant_text, trunc_assistant)

            out.append({
                "turn_id": tid,
                "user": {"text": user_text, "ts": ts},
                "assistant": {"text": assistant_text, "ts": ts},
            })
        except Exception as e:
            # Skip malformed logs
            print(f"Failed to build pair for turn {tid}: {e}")
            continue

    # Oldest -> newest
    out.sort(key=lambda p: p.get("user", {}).get("ts") or p.get("assistant", {}).get("ts") or "")
    return out

def extract_turn_ids_from_guess_package(guess_package: Dict[str, Any]) -> set:
    """
    Extract all valid turn IDs from the structured guess_package.

    Sources:
    - earlier_turns_insights[].turn_id
    - last_turns_details[].turn_id
    - current_turn_details.turn_id
    - objective_memory[].timeline[].turn_id (if present)
    - active_memory_insights[].source_turn_ids[] (if present)

    Returns:
        Set of all unique turn IDs found in the package.
    """
    turn_ids = set()

    # From earlier_turns_insights
    for insight in (guess_package.get("earlier_turns_insights") or []):
        if tid := insight.get("turn_id"):
            turn_ids.add(tid)

    # From last_turns_details
    for turn in (guess_package.get("last_turns_details") or []):
        if tid := turn.get("turn_id"):
            turn_ids.add(tid)

    # From current_turn_details
    if current := guess_package.get("current_turn_details"):
        if tid := current.get("turn_id"):
            turn_ids.add(tid)

    # From objective_memory timelines (if they have turn_id references)
    for mem in (guess_package.get("objective_memory") or []):
        for timeline_entry in (mem.get("timeline") or []):
            if tid := timeline_entry.get("turn_id"):
                turn_ids.add(tid)
            # Also check source_turn_ids if present
            if source_ids := timeline_entry.get("source_turn_ids"):
                if isinstance(source_ids, list):
                    turn_ids.update(tid for tid in source_ids if tid)

    # From active_memory_insights (may have source_turn_ids arrays)
    for mem in (guess_package.get("active_memory_insights") or []):
        if source_ids := mem.get("source_turn_ids"):
            if isinstance(source_ids, list):
                turn_ids.update(tid for tid in source_ids if tid)
        # Some insights might have turn_id directly
        if tid := mem.get("turn_id"):
            turn_ids.add(tid)

    return turn_ids

class TurnCompressor(Protocol):
    def __call__(
            self,
            *,
            turn_id: str,
            tlog: dict,
            payload: dict,
            user_text: str,
            assistant_text: str,
    ) -> str: ...


def _default_turn_compressor(
        *,
        turn_id: str,
        tlog: dict,
        payload: dict,
        user_text: str,
        assistant_text: str,
) -> str:
    """
    Generic, dependency-free fallback:
    - pull a one-liner from turn_summary if present
    - otherwise user text + a short assistant snippet.
    """
    summary = (tlog or {}).get("turn_summary") or {}
    one_liner = (summary.get("one_liner") or "").strip()
    if one_liner:
        return one_liner

    u = (user_text or "").strip()
    a = (assistant_text or "").strip()

    parts = []
    if u:
        parts.append(f"User: {u[:280]}")
    if a:
        parts.append(f"Assistant: {a[:280]}")
    return " | ".join(parts)

async def retrospective_context_view(
        *,
        ctx_client: ContextRAGClient,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        last_turns: int = 3,
        recommended_turn_ids: Optional[List[str]] = None,
        memory_log_entries: Optional[List[Dict[str, Any]]] = None,
        delta_fps: Optional[List[Dict[str, Any]]] = None,
        current_turn_log: Optional[str] = None,
        current_turn_fp: Optional[TurnFingerprintV1] = None,
        selected_bucket_cards: Optional[List[dict]] = None,
        objective_memory_timelines: Optional[Dict[str, List[dict]]] = None,
        filter_turn_ids: Optional[List[str]] = None,
        selected_local_memories_turn_ids: Optional[List[str]] = None,
        turn_view_class: Type[BaseTurnView] = BaseTurnView,
) -> Tuple[str, Dict[str, Any]]:
    """
    # USAGE CONTRACT (which turns to return):
    # ===============
    # Callers should use:
    #
    # 1. Don't pass filter_turn_ids (defaults to None) → Returns all last_turns
    #    await build_gate_context_hints(
    #        last_turns=3,
    #        # filter_turn_ids not passed
    #    )
    #
    # 2. Pass explicit empty list → Returns no historical turns
    #    await build_gate_context_hints(
    #        last_turns=3,
    #        filter_turn_ids=[],  # Explicit: no turns wanted
    #    )
    #
    # 3. Pass specific turn IDs → Returns only those turns
    #    await build_gate_context_hints(
    #        last_turns=3,
    #        filter_turn_ids=["turn_123", "turn_456"],
    #    )
    Returns: (guess_ctx_str, guess_package)

    Layout (freshest → oldest):
      - [OBJECTIVE MEMORY — SELECTED BUCKETS]    — compact, contextual, not chronological
      - [CURRENT TURN]                           — ts, turn id, insights (from current_turn_fp), full current_turn_log
      - [PRIOR TURNS — COMPRESSED VIEWS]         — newest→oldest, using TurnView.to_compressed_search_view()
      - [EARLIER TURNS — NON-RECONCILED INSIGHTS]— newest→oldest, from delta_fps (filtered & deduped)
    """

    # ---------- 1) Fetch recent turn logs + include pinned ones ----------
    raw_items: List[Dict[str, Any]] = []

    try:
        hit = await ctx_client.recent(
            kinds=("artifact:turn.log",),
            roles=("artifact",),
            limit=last_turns,
            days=365,
            user_id=user_id,
            conversation_id=conversation_id,
            track_id=None,
            with_payload=True,
        )
        for item in (hit or {}).get("items") or []:
            raw_items.append(item)
    except Exception:
        pass

    # Add recommended pins (only if not already present)
    already = {_turn_id_from_tags_safe(list(item.get("tags") or [])) for item in raw_items}
    for tid in (recommended_turn_ids or []):
        if not tid or tid in already:
            continue
        try:
            hit = await ctx_client.recent(
                kinds=("artifact:turn.log",),
                roles=("artifact",),
                all_tags=[f"turn:{tid}"],
                limit=1,
                days=365,
                user_id=user_id,
                conversation_id=conversation_id,
                track_id=None,
                with_payload=True,
            )
            item = next(iter((hit or {}).get("items") or []), None)
            if item:
                raw_items.append(item)
        except Exception:
            pass

    # Dedupe by turn_id; keep newest
    by_tid: Dict[str, Dict[str, Any]] = {}
    for item in raw_items:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or f"id:{item.get('id') or ''}"
        ts = item.get("ts") or item.get("timestamp") or ""
        cur = by_tid.get(tid)
        if not cur or _ts_key(ts) >= _ts_key(cur.get("ts") or cur.get("timestamp") or ""):
            by_tid[tid] = item

    raw_items = list(by_tid.values())

    # Optional filter down to specific turn_ids
    if filter_turn_ids is not None:
        if len(filter_turn_ids) == 0:
            raw_items = []
        else:
            keep = set(t for t in filter_turn_ids if t)
            raw_items = [item for item in raw_items
                         if (_turn_id_from_tags_safe(list(item.get("tags") or [])) or "") in keep]

    # Oldest included ts bound for deduping delta_fps
    oldest_included_ts = ""
    if raw_items:
        oldest_included_ts = min((item.get("ts") or item.get("timestamp") or "")
                                 for item in raw_items
                                 if (item.get("ts") or item.get("timestamp")))

    # ---------- 2) Build text blocks ----------
    text_blocks: List[str] = []
    items_struct: List[Dict[str, Any]] = []

    # 2.a Objective memory (selected buckets only)
    if selected_bucket_cards:
        text_blocks.append(
            objective_memory_block(selected_bucket_cards or [], objective_memory_timelines or {})
        )

    # 2.b Current turn (insights from provided current_turn_fp; no loading)
    if current_turn_log or current_turn_fp:
        ts_now_iso = _dt.datetime.utcnow().isoformat() + "Z"
        one_line = ""
        if isinstance(current_turn_fp, TurnFingerprintV1):
            try:
                one_line = render_fingerprint_one_liner(current_turn_fp) or ""
            except Exception:
                pass

        text_blocks.append("\n".join([
            "[CURRENT TURN]",
            _fmt_ts_for_humans(ts_now_iso),
            "[turn id]",
            turn_id,
            "[turn insights]",
            (one_line or "(none)"),
            (current_turn_log or "[turn log]"),
            ""
        ]))
        items_struct.append({
            "ts": ts_now_iso,
            "turn_id": turn_id,
            "kind": "turn_current",
            "insights_one_liner": one_line,
            "insights_json": current_turn_fp.to_json() if isinstance(current_turn_fp, TurnFingerprintV1) else (current_turn_fp or {}),
            "log": current_turn_log,
        })

    # 2.c Prior turns — COMPRESSED VIEWS using TurnView (newest→oldest)
    last_turns_details: List[dict] = []
    last_turns_mate: List[dict] = []

    items_sorted = sorted(raw_items,
                          key=lambda item: item.get("ts") or item.get("timestamp") or "",
                          reverse=True)

    if items_sorted:
        text_blocks.append("\n[PRIOR TURNS — COMPRESSED VIEWS]")

    for item in items_sorted:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or ""
        ts_raw = item.get("ts") or item.get("timestamp") or ""

        # Extract the double-wrapped payload structure
        outer_payload = item.get("payload") or {}
        inner_payload = outer_payload.get("payload") or {}

        try:
            # Build TurnView from the raw item
            turn_log_data = inner_payload.get("turn_log") or {}
            user_data = inner_payload.get("user") or {}
            asst_data = inner_payload.get("assistant") or {}

            tv = turn_view_class.from_saved_payload(
                turn_id=tid,
                user_id=user_id,
                conversation_id=conversation_id,
                tlog=turn_log_data,
                payload=item,  # Pass the FULL item (with double-wrapped structure)
                user_text=user_data.get("prompt"),
                assistant_text=asst_data.get("completion"),
            )

            # Generate compressed view with one-liner and without turn info header
            compressed_view = tv.to_compressed_search_view(
                include_turn_info=False,
                user_prompt_limit=600,
                include_turn_summary=True,
                include_context_used=True,
            )

            # Add timestamp header
            text_blocks.append("\n".join([
                _fmt_ts_for_humans(ts_raw),
                "[turn id]",
                (tid + "\n") if tid else "",
                compressed_view,
                ""
            ]))

            # For structured data - generate one-liner for backwards compat
            one_liner = tv.generate_one_liner()
            last_turns_details.append({
                "ts": ts_raw,
                "turn_id": tid or None,
                "kind": "turn_log",
                "insights_one_liner": one_liner,
                "insights_json": {},
                "log": compressed_view,
                "log_payload": inner_payload,
            })
            last_turns_mate.append(item)

        except Exception as e:
            # Fallback: skip malformed turns
            import traceback
            print(f"Failed to process turn {tid}: {e}")
            print(traceback.format_exc())
            continue

    # 2.d Earlier (non-reconciled) insights from delta_fps — filtered/deduped
    earlier_turns_insights: List[dict] = []
    orphan_insights: List[dict] = []
    if delta_fps:
        ub = _ts_key(oldest_included_ts) if oldest_included_ts else float("inf")
        sel = set(selected_local_memories_turn_ids or [])
        for fp_doc in delta_fps:
            tid = (fp_doc or {}).get("turn_id")
            if sel and tid not in sel:
                continue
            made_at = (fp_doc or {}).get("made_at") or (fp_doc or {}).get("ts") or ""
            one = ""
            try:
                fp = TurnFingerprintV1(
                    version=(fp_doc or {}).get("version") or "v1",
                    turn_id=tid or "",
                    objective=(fp_doc or {}).get("objective") or "",
                    topics=(fp_doc or {}).get("topics") or [],
                    assertions=(fp_doc or {}).get("assertions") or [],
                    exceptions=(fp_doc or {}).get("exceptions") or [],
                    facts=(fp_doc or {}).get("facts") or [],
                    made_at=made_at
                )
                one = render_fingerprint_one_liner(fp) or ""
            except Exception:
                one = ""
            if not one:
                continue

            rec = {
                "ts": made_at,
                "turn_id": tid or None,
                "kind": "delta_fp",
                "insights_one_liner": one,
                "insights_json": fp_doc,
                "log": "",
            }
            earlier_turns_insights.append(rec)

            if not oldest_included_ts or _ts_key(made_at) < ub:
                orphan_insights.append(rec)

        if orphan_insights:
            orphan_sorted = sorted(orphan_insights, key=lambda x: x.get("ts") or "", reverse=True)
            lines = ["\n[EARLIER TURNS — NON-RECONCILED INSIGHTS]"]
            for it in orphan_sorted[:16]:
                lines.append(
                    f"{_fmt_ts_for_humans(it.get('ts') or '')}\n[turn id]\n{it.get('turn_id')}\n[insights]\n{it.get('insights_one_liner')}\n"
                )
            text_blocks.append("\n".join(lines).strip() + "\n")

    # ---------- 3) Assemble outputs ----------
    guess_ctx_str = ("\n".join(text_blocks)).strip()
    guess_package = {
        "items": (
                earlier_turns_insights
                + last_turns_details
                + [i for i in items_struct if i.get("kind") == "turn_current"]
        ),
        "active_memory_insights": list(memory_log_entries or []),
        "earlier_turns_insights": earlier_turns_insights,
        "last_turns_details": last_turns_details,
        "last_turns_mate": last_turns_mate,
        "current_turn_details": next((it for it in items_struct if it.get("kind") == "turn_current"), None),
        "objective_memory": (
            [{"bucket_card": c, "timeline": (objective_memory_timelines or {}).get(c.get("bucket_id"), [])}
             for c in (selected_bucket_cards or [])]
            if selected_bucket_cards else []
        ),
    }

    return guess_ctx_str, guess_package

# USED TO BUILD THE HISTORICAL TURNS CONTEXT FOR FINAL ANSWER GENERATOR.
# I AM NOT SURE IT MUST BE VERY VERBOSE. WE WILL REVISIT IT.
async def materialize_prior_pairs(
        scratchpad: "TurnScratchpad",
        ctx_client: ContextRAGClient,
        _ctx: dict,
        *,
        view: ViewName = "compact",
        trunc_user: int = 600,
        trunc_assistant: int = 900,
        trunc_deliverable_text: int = 600,
        turn_view_class: Type[BaseTurnView] = BaseTurnView,
) -> tuple[list[dict], list[dict]]:
    """
    Returns a list of prior pairs, each shaped as:
      {"turn_id": tid, "user": {...}, "assistant": {...}, "artifacts": [blocks]}
    Includes only turns in scratchpad.materialize_turn_ids, ordered oldest->newest.

    Artifacts are categorized as:
    - Internal: program presentation (solver digest), project log (working draft)
    - User-facing: deliverables (files/inline)
    View policy:
      - "compact": truncate user/assistant/deliverables text by provided limits for all turns
      - "materialize": keep FULL content for the most recent *non-clarification* prior turn,
                       truncate others using provided limits
    """

    def _parse_slot(slot: dict) -> dict:
        """Extract common slot information."""
        artifact = slot.get("value") or {}
        # text surrogate: try usual shapes
        text_content = (
                artifact.get("text")
                # or (artifact.get("output") or {}).get("text")
                or artifact.get("value")
                or ""
        )
        return {
            "name": slot.get("slot"),
            "desc": slot.get("description") or "",
            "artifact": artifact,
            "typ": (artifact.get("type") or slot.get("type") or "inline"),
            "mime": artifact.get("mime") or slot.get("mime") or "unknown",
            "format": artifact.get("format") or slot.get("format") or "text",
            "filename": artifact.get("filename") or artifact.get("path") or slot.get("filename") or "(no filename)",
            "text_content": text_content if isinstance(text_content, str) else str(text_content),
            "size": len(str(text_content)) if text_content else 0,
        }

    def _format_deliverable(parsed: dict, turn_id: str, *, full: bool) -> dict:
        """Format a deliverable either as full content or compact summary."""
        name = parsed["name"]
        is_draft = bool(parsed["artifact"].get("draft"))
        gap = parsed["artifact"].get("gaps")
        inventorization = parsed["artifact"].get("content_inventorization") or {}
        draft_marker = " [DRAFT]" if is_draft else ""

        if full:
            content_parts = [
                f"### Deliverable: `{name}`{draft_marker}",
                f"**Type:** {parsed['typ']}",
                f"**Description:** {parsed['desc']}",
            ]
            if is_draft:
                content_parts.append("**Status:** ⚠️ DRAFT — Incomplete/partial content")
            if gap:
                content_parts.append(f"**Gap:** {gap}")
            if inventorization:
                content_parts.append(f"**Content inventorization:** {json.dumps(inventorization, ensure_ascii=False, indent=2)}")

            if parsed["typ"] == "file":
                content_parts.extend([
                    f"**Filename:** {parsed['filename']}",
                    f"**MIME:** {parsed['mime']}",
                ])
                if is_draft:
                    content_parts.append("**Note:** File rendering incomplete; text representation below")
            else:
                content_parts.append(f"**Format:** {parsed['format']}")
                # Explain draft inline status
                if is_draft:
                    content_parts.append("**Note:** Partial/incomplete content")


            if parsed["text_content"]:
                content_parts.extend([
                    "",
                    "**Content (as shown to user):**" if not is_draft else "**Content (partial/draft):**",
                    parsed["text_content"],
                ])

            ret = {
                "type": "text",
                "title": f"Deliverable: {name} (turn {turn_id}){draft_marker}",
                "content": "\n".join(content_parts),
                "turn_id": turn_id,
                "kind": "deliverable.full",
                "is_draft": is_draft,
            }
            if gap:
                ret["gaps"] = gap
            return ret

        header = f"**{name}**{draft_marker} ({'file: ' + parsed['mime'] if parsed['typ']=='file' else 'inline: ' + parsed['format']})"
        body_line = _truncate(parsed["text_content"], trunc_deliverable_text) if parsed["text_content"] else ""
        lines = [header]
        if parsed["desc"]:
            lines.append(f"  {parsed['desc']}")
        if is_draft:
            lines.append("  ⚠️ _DRAFT — incomplete_")
        if gap:
            lines.append(f"  Gap: {gap}_")
        if parsed["size"] > 0:
            lines.append(f"  _{parsed['size']:,} chars_")
        if body_line:
            lines.append("")
            lines.append(body_line)
        return {"lines": lines, "is_draft": is_draft, "gaps": gap}

    tenant, project, user = _ctx["service"]["tenant"], _ctx["service"]["project"], _ctx["service"]["user"]
    conversation_id, track_id = _ctx["conversation"]["conversation_id"], _ctx["conversation"]["track_id"]

    pairs: list[dict] = []
    citations_merged: list[dict] = []

    materialize_ids = list(getattr(scratchpad, "materialize_turn_ids", []) or [])

    newest_prior_nonclar_idx = -1
    for idx, tid in enumerate(materialize_ids):
        try:
            mat_meta = await ctx_client.materialize_turn(
                turn_id=tid,
                user_id=user,
                conversation_id=conversation_id,
                track_id=track_id,
                with_payload=True
            )
        except Exception:
            continue
        turn_log_payload = unwrap_payload((mat_meta.get("turn_log") or {}).get("payload") or {})
        if newest_prior_nonclar_idx == -1:
            if not _is_clarification_turn(turn_log_payload or {}):
                newest_prior_nonclar_idx = idx

    for idx, tid in enumerate(materialize_ids):
        try:
            mat = await ctx_client.materialize_turn(
                turn_id=tid,
                user_id=user,
                conversation_id=conversation_id,
                track_id=track_id,
                with_payload=True
            )
        except Exception:
            continue

        artifacts: list[dict] = []

        saved_payload = unwrap_payload((mat.get("turn_log") or {}).get("payload") or {}) or {}
        entries, turn_summary, gate = _extract_entries_and_summary(saved_payload)
        deliverables_map = _solver_deliverables(saved_payload)
        is_clar = _is_clarification_turn(saved_payload)

        # compressed turn
        compressed_turn = None
        if entries and turn_summary:
            try:
                compressed_turn = CompressedTurn.from_structured(entries, turn_summary)
                # inject gate objective into insights (to surface in user context)
                g_obj = (gate.get("current_objective_short") or "").strip()
                if g_obj:
                    compressed_turn.insights = (compressed_turn.insights + ("; " if compressed_turn.insights and g_obj else "") + g_obj) if compressed_turn.insights or g_obj else g_obj
            except Exception:
                compressed_turn = None

        # INTERNAL: Program Presentation
        prez_md = _solver_program_presentation(saved_payload)
        if isinstance(prez_md, str) and prez_md.strip():
            artifacts.append({
                "type": "text",
                "title": f"Program Presentation (turn {tid})",
                "content": prez_md,
                "turn_id": tid,
                "kind": "codegen.program.presentation",
            })

        # INTERNAL: Solver Failure
        solver_failure_art = _solver_failure_artifact(saved_payload, turn_id=tid)
        if solver_failure_art:
            artifacts.append(solver_failure_art)

        # INTERNAL: Project Log
        project_log_spec = (deliverables_map or {}).get("project_log") or {}
        log_art = project_log_spec.get("value") or {}
        log_text = (log_art.get("text")
                    or (log_art.get("output") or {}).get("text")
                    or "")
        if isinstance(log_text, str) and log_text.strip():
            artifacts.append({
                "type": "text",
                "title": f"Project Log (turn {tid}) — internal working draft",
                "content": log_text,
                "turn_id": tid,
                "kind": "project.log",
            })

        # MEMORIES: GateOut (added)
        mem_art = _memories_artifact_from_gate(gate, turn_id=tid)
        if mem_art:
            artifacts.append(mem_art)

        # USER-FACING: Deliverables (full vs summary)
        full_for_this_turn = False
        if view == "materialize":
            full_for_this_turn = (idx == newest_prior_nonclar_idx and newest_prior_nonclar_idx != -1 and not is_clar)

        if deliverables_map:
            if full_for_this_turn:
                for slot_name, spec in list(deliverables_map.items())[:12]:
                    if slot_name == "project_log":
                        continue
                    slot = {
                        "slot": slot_name,
                        "description": spec.get("description") or "",
                        "value": spec.get("value") or {},
                        "type": spec.get("type"),
                        "format": spec.get("format"),
                        "mime": spec.get("mime"),
                    }
                    parsed = _parse_slot(slot)
                    artifacts.append(_format_deliverable(parsed, tid, full=True))
            else:
                all_lines = []
                for slot_name, spec in list(deliverables_map.items())[:12]:
                    if slot_name == "project_log":
                        continue
                    slot = {
                        "slot": slot_name,
                        "description": spec.get("description") or "",
                        "value": spec.get("value") or {},
                        "type": spec.get("type"),
                        "format": spec.get("format"),
                        "mime": spec.get("mime"),
                    }
                    parsed = _parse_slot(slot)
                    result = _format_deliverable(parsed, tid, full=False)
                    all_lines.extend(result["lines"])
                    all_lines.append("")
                if all_lines:
                    artifacts.append({
                        "type": "text",
                        "title": f"Deliverables Summary (turn {tid})",
                        "content": "\n".join(all_lines),
                        "turn_id": tid,
                        "kind": "deliverables.list",
                    })

        # Materialized shown text
        user_obj = unwrap_payload((mat.get("user") or {}).get("payload") or {})
        user_ts = (mat.get("user") or {}).get("ts")
        asst_obj = unwrap_payload((mat.get("assistant") or {}).get("payload") or {})
        asst_ts = (mat.get("assistant") or {}).get("ts")

        user_text = (user_obj.get("prompt") or "").strip()
        asst_text = (asst_obj.get("completion") or "").strip()

        if compressed_turn and (not user_text or not asst_text):
            pair = turn_to_pair(compressed_turn)
            if not user_text:
                user_text = pair.get("user", "")
            if not asst_text:
                asst_text = pair.get("assistant", "")

        if view == "compact":
            user_text = _truncate(user_text, trunc_user)
            asst_text = _truncate(asst_text, trunc_assistant)
        elif view == "materialize":
            if not full_for_this_turn:
                user_text = _truncate(user_text, trunc_user)
                asst_text = _truncate(asst_text, trunc_assistant)

        pairs.append({
            "turn_id": tid,
            "user": {"text": user_text, "ts": user_ts},
            "assistant": {"text": asst_text, "ts": asst_ts},
            "artifacts": artifacts,
            "compressed_turn": compressed_turn,
        })

        # Citations
        cit = (unwrap_payload((mat.get("citables") or {}).get("payload") or {}).get("items")) or []
        if cit:
            citations_merged.extend(cit)

    pairs.sort(key=lambda p: (p.get("user") or {}).get("ts") or (p.get("assistant") or {}).get("ts") or "")
    citations_merged = ctx_retrieval_module._dedup_citations(citations_merged)
    return pairs, citations_merged

# ----------------- moved from custom solution chat/sdk/runtime/turn_view.py
# CANDIDATE HOW WE MIGHT WANT TO PRESENT PAST TURNS FOR CODEGEN
def build_program_playbook_codegen_new(history: List[Dict[str, Any]], *,
                                       turn_view_class: Type[BaseTurnView] = BaseTurnView,
                                       max_turns: int = 5) -> str:
    """
    Build a compact, scannable playbook showing what artifacts exist across recent turns.

    Now implemented on top of TurnView + to_compressed_search_view:

      - history[i] is expected to be a "turn dict" that Type[TurnView].from_turn_dict(...) understands:
        {
          "turn_id": "...",
          "payload": {
            "user_id": "...",
            "conversation_id": "...",
            "ts": "...",
            "payload": {
              "turn_log": {...},
              "user": {"prompt": "..."},
              "assistant": {"completion": "..."},
              "gate": {...},
              "solver_result": {...},
              "turn_summary": {...}
            }
          }
        }

      - For each turn we:
        - Construct a TurnView
        - Compute a simple status (success / failed / answered_by_assistant / no_activity)
        - Render TurnView.to_compressed_search_view(...) as the body

    The global format:

      # Program History Playbook

      ## Turn <turn_id> — <timestamp> [CURRENT|HISTORICAL]
      TURN_ID: <turn_id>
      Status: <status>
      Fetch with: ctx_tools.fetch_turn_artifacts(["<turn_id>"])

      <output of to_compressed_search_view(...)>

    """

    if not history:
        return "(no program history)"

    def _compute_status(tv: BaseTurnView) -> str:
        """Rough status, similar to the old implementation but based on SolveResult."""
        solver = tv.solver
        assistant_text = (tv.assistant_raw or "").strip()

        # No solver at all → either assistant answered or truly no activity
        if not solver:
            if assistant_text:
                return "answered_by_assistant"
            return "no_activity"

        # We have a solver_result
        deliverables = {}
        try:
            if hasattr(solver, "deliverables_map"):
                deliverables = solver.deliverables_map() or {}
            elif isinstance(getattr(solver, "execution", None), dict):
                # deliverables = (solver.execution.get("deliverables") or {})
                deliverables = solver.execution.deliverables or {}
        except Exception:
            deliverables = {}

        # Ignore internal project_log / canvas
        non_aux = {
            name: spec
            for name, spec in (deliverables or {}).items()
            if name not in {"project_log", "project_canvas"}
        }

        has_deliverables = bool(non_aux)

        # Failure signal (SolveResult.failure if present)
        failure_obj = getattr(solver, "failure", None)
        has_failure = bool(failure_obj)

        if has_deliverables:
            return "success"
        if has_failure:
            return "failed: solver_error"

        # Fallback when solver existed but produced no visible deliverables/failure
        if assistant_text:
            return "answered_by_assistant"
        return "no_activity"

    sections: List[str] = []

    import traceback
    for idx, tv in enumerate(history[:max_turns]):
        is_current = (idx == 0)
        turn_label = "CURRENT TURN" if is_current else "HISTORICAL"

        try:
            rec = next(iter(tv.values()), None)
            tv = turn_view_class.from_turn_log_dict(rec)
        except Exception as ex:
            print(f"Failed to build TurnView for turn idx {idx}: {ex}")
            print(traceback.format_exc())
            continue

        turn_id = tv.turn_id or "(missing_turn_id)"

        # Timestamp: try to normalize to "YYYY-MM-DD HH:MM"
        ts_full = (tv.timestamp or "").strip()
        if ts_full:
            ts = ts_full[:16].replace("T", " ")
        else:
            ts = "(no date)"

        status = _compute_status(tv)

        # Compressed per-turn view from TurnView
        compressed_view = tv.to_solver_presentation(
            user_prompt_limit=400,
            # attachment_text_limit=300,
            program_log_limit=200,
        )

        header_lines = [
            f"## Turn {turn_id} — {ts} [{turn_label}]",
            f"TURN_ID: {turn_id}",
            f"Status: {status}",
            f"Fetch with: ctx_tools.fetch_turn_artifacts([\"{turn_id}\"])",
            "",
        ]

        sections.append("\n".join(header_lines + [compressed_view, ""]))

    header = [
        "# Program History Playbook",
        "",
        f"Showing {len(sections)} turn(s), newest first.",
        "Previews are truncated. Use fetch_turn_artifacts([turn_ids]) for full content.",
        "",
        "---",
        "",
    ]

    return "\n".join(header + sections)
# HOW WE REPRESENTED PAST TURNS FOR CODEGEN
def build_program_playbook_codegen(history: list[dict], *, max_turns: int = 5) -> str:
    """
    Build a compact, scannable playbook showing what artifacts exist across recent turns.

    Purpose: Help LLM understand:
    - What artifacts exist in history
    - Which turn_ids to fetch for specific content
    - How large artifacts are (to decide on fetch strategy)

    Format:
      Turn <turn_id> — <timestamp> [CURRENT] / [HISTORICAL]
      User Request: <preview>
      Program Log: <preview with size indicator>
      Deliverables:
        • slot_name (type: format/mime) - Size: N chars
          Description: ...
          Preview: [first 100 chars...] (...N more chars)
          ⚠ Use fetch_turn_artifacts(["turn_id"]) to retrieve full content
      Sources: (numbered list with titles and abbreviated links)
    Ordering per turn:
      1) User Request
      2) Program Log
      3) Deliverables (if solver ran & succeeded) OR Solver Failure (if failed)
      4) Assistant Response (full unless solver succeeded, in which case truncated)

    Other guarantees:
      - TURN_ID and a copy-pasteable fetch hint
      - Status: success | failed: solver_error | answered_by_assistant | no_activity
      - No "No deliverables" noise when solver didn't run
    """

    if not history:
        return "(no program history)"

    def _size(s: str | None) -> int:
        return len(s or "")

    sections: list[str] = []

    for idx, rec in enumerate(history[:max_turns]):
        try:
            exec_id, meta = next(iter(rec.items()))
        except Exception:
            continue

        is_current = (idx == 0)
        turn_label = "CURRENT TURN" if is_current else "HISTORICAL"

        # Timestamp → "YYYY-MM-DD HH:MM"
        ts_full = (meta.get("ts") or "").strip()
        ts = ts_full[:16].replace("T", " ") if len(ts_full) >= 16 else (ts_full[:10] or "(no date)")

        # Core materials
        user_text = ((meta.get("user") or {}).get("prompt") or "").strip()
        assistant_text = (meta.get("assistant") or "").strip()
        solver_failure_md = (meta.get("solver_failure") or "").strip()
        def turn_id_fn():
            if turn_label == "CURRENT TURN":
                return "current_turn"
            else:
                return meta.get("turn_id") or "(missing_turn_id)"
        turn_id = turn_id_fn()

        # Program log
        pl = meta.get("project_log") or {}
        pl_text = (pl.get("text") or pl.get("value") or "").strip()
        pl_size = _size(pl_text)

        # Deliverables & sources
        deliverables = meta.get("deliverables") or []
        sources = ((meta.get("web_links_citations") or {}).get("items")) or []

        # Did solver run?
        solver_ran = bool(deliverables or solver_failure_md or pl_text)

        # Status
        if deliverables:
            status = "success"
        elif solver_failure_md:
            status = "failed: solver_error"
        elif assistant_text and not solver_ran:
            status = "answered_by_assistant"
        else:
            status = "no_activity"

        # ----- Header -----
        header_lines = [
            f"## Turn {turn_id} — {ts} [{turn_label}]",
            f"TURN_ID: {turn_id}",
            f"Status: {status}",
            f"Fetch with: ctx_tools.fetch_turn_artifacts([\"{turn_id}\"])",
            "",
        ]

        body_lines: list[str] = []

        # 1) User Request
        body_lines += [
            "**User Request (markdown):**",
            _short_with_count(user_text, 600) if user_text else "(no user message)",
            "",
        ]

        # 2) Program Log
        if pl_text:
            body_lines += [
                f"**Program Log:** ({pl_size:,} chars)",
                #_short_with_count(pl_text, 400),
                _short_with_count(pl_text, 6000),
                "",
            ]
        # 3) Deliverables or Failure — only if solver ran
        if solver_ran:
            if deliverables:
                body_lines.append("**Deliverables:**")
                for d in deliverables:
                    slot_name = d.get("slot") or "(unnamed)"
                    if slot_name in {"project_log", "project_canvas"}:
                        continue
                    artifact = d.get("value") or {}
                    slot_type = artifact.get("type") or "inline"
                    desc = d.get("description") or "(no description)"
                    is_draft = bool(artifact.get("draft"))
                    gap = artifact.get("gaps")
                    has_gap = bool(gap)

                    # Prefer file/text surrogate if present; else inline value
                    text = artifact.get("text") or artifact.get("value") or ""
                    if isinstance(text, dict):
                        text = str(text)
                    text_size = _size(text)
                    text_preview = _short_with_count(text, 150) if text else "[empty]"

                    # Draft marker in slot name
                    draft_marker = " [DRAFT]" if is_draft else ""

                    if slot_type == "file":
                        mime = artifact.get("mime") or "unknown"
                        filename = artifact.get("filename") or artifact.get("path") or "(no filename)"
                        body_lines += [
                            f"  • {slot_name}{draft_marker} (file: {mime})",
                            f"    Filename: {filename}",
                            f"    Size: {text_size:,} chars",
                            f"    Description: {desc}",
                        ]
                        # Draft status explanation
                        if is_draft:
                            body_lines.append("    Status: Incomplete — file rendering failed but text available")
                        if has_gap:
                            body_lines.append(f"    Gaps: {gap}")

                    else:
                        fmt = artifact.get("format") or "text"
                        body_lines += [
                            f"  • {slot_name}{draft_marker} (inline: {fmt})",
                            f"    Size: {text_size:,} chars",
                            f"    Description: {desc}",
                        ]
                        # Draft status explanation
                        if is_draft:
                            body_lines.append("    Status: Incomplete — partial content available")
                        if has_gap:
                            body_lines.append(f"    Gaps: {gap}")
                    if text:
                        body_lines.append(f"    Preview: {text_preview}")
                    if text_size > 300:
                        body_lines.append(f"    ⚠ Full content via fetch_turn_artifacts([\"{turn_id}\"])")
                body_lines.append("")
            elif solver_failure_md:
                body_lines += [
                    "**Solver Failure:**",
                    _short_with_count(solver_failure_md, 800),
                    "",
                ]
        else:
            # Explicit signal when no solver ran
            if status == "answered_by_assistant":
                body_lines += [
                    "**No Deliverables** (assistant answered directly; solver did not run)",
                    "",
                ]
            elif status == "no_activity":
                body_lines += [
                    "**No Deliverables** (no solver activity on this turn)",
                    "",
                ]
        # else: solver_ran but no deliverables or failure text — extremely rare; omit noise

        # 4) Assistant Response
        if assistant_text:
            body_lines.append("**Assistant Response (shown to user, markdown):**")
            if status == "success":
                # Only truncate when solver succeeded
                body_lines.append(_short_with_count(assistant_text, 600))
            else:
                # Failed / answered_by_assistant / no_activity → show full
                body_lines.append(assistant_text)
            body_lines.append("")

        # Sources (compact, end of block)
        if sources:
            from urllib.parse import urlparse
            src_lines = []
            for i, src in enumerate(sources[:20], 1):
                if not isinstance(src, dict):
                    continue
                title = (src.get("title") or "").strip()
                url = (src.get("url") or "").strip()
                domain = ""
                if url:
                    try:
                        domain = urlparse(url).netloc or ""
                    except Exception:
                        domain = url[:30]
                if title and domain:
                    src_lines.append(f"  {i}. {_short_with_count(title, 80)} ({domain})")
                elif title:
                    src_lines.append(f"  {i}. {_short_with_count(title, 80)}")
                elif url:
                    src_lines.append(f"  {i}. {_short_with_count(url, 100)}")
            if src_lines:
                body_lines.append(f"**Sources:** ({len(sources)} total)")
                body_lines += src_lines
                body_lines.append("")

        sections.append("\n".join(header_lines + body_lines))

    header = [
        "# Program History Playbook",
        "",
        f"Showing {len(sections)} turn(s), newest first.",
        "Previews are truncated. Use fetch_turn_artifacts([turn_ids]) for full content.",
        "",
        "---",
        "",
    ]

    return "\n".join(header + sections)

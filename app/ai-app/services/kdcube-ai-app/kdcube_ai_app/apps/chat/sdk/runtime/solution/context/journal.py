# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/presentation.py

import json, re, time
import datetime as _dt
from typing import Dict, Any, Optional, List, Literal, Tuple, Protocol, Type

from kdcube_ai_app.apps.chat.sdk.context.memory.turn_fingerprint import TurnFingerprintV1, render_fingerprint_one_liner
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import BaseTurnView, CompressedTurn, turn_to_pair
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.strategy_and_budget import format_budget_for_llm
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval as ctx_retrieval_module
from kdcube_ai_app.apps.chat.sdk.util import _truncate, _turn_id_from_tags_safe, _to_jsonable, ts_key


def build_turn_session_journal(*,
                               context: ReactContext,
                               output_contract: Dict[str, Any],
                               max_prior_turns: int = 5,
                               max_sources_per_turn: int = 20,
                               turn_view_class: Type[BaseTurnView] = BaseTurnView,
                               is_codegen_agent: bool = False, # False for decision, True for codegen
                               # fetch_context_tool_retrieval_example: Optional[str] = "ctx_tools.fetch_turn_artifacts([turn_id])",
                               show_artifacts: Optional[List[Dict[str, Any]]] = None,
                               coordinator_turn_line: Optional[str] = None) -> str:
    """
    Unified, LLM-friendly Turn Session Journal used by the Decision node.

    ORDER (strict oldest → newest):
      1) Prior turns (historical), strictly oldest → newest
      2) Current turn (live):
         • User Request (what we knew at session start) — comes from scratchpad via `current_user_markdown`
         • Events timeline (appended as the session progresses; short timestamps)
         • Current snapshot (contract/slots/tool results)
      3) Full artifacts (optional): explicit list of context artifacts to show in full
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

    def _format_full_value(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        try:
            return json.dumps(val, ensure_ascii=False, indent=2)
        except Exception:
            return str(val)

    def _render_full_artifact(item: Dict[str, Any]) -> List[str]:
        lines_local: list[str] = []
        ctx_path = (item.get("context_path") or "").strip()
        art_type = (item.get("artifact_type") or "").strip()
        ts = (item.get("timestamp") or item.get("ts_human") or item.get("time") or "").strip()
        art = item.get("artifact") or {}
        if not isinstance(art, dict):
            art = {"value": art}

        kind = (art.get("kind") or art.get("type") or "").strip()
        fmt = (art.get("format") or "").strip()
        mime = (art.get("mime") or "").strip()
        filename = (art.get("filename") or art.get("path") or "").strip()

        header = f"### {ctx_path}" if ctx_path else "### (unknown context path)"
        if art_type:
            header += f" [{art_type}]"
        lines_local.append(header)

        meta_parts = []
        if ts:
            meta_parts.append(f"time={ts}")
        if kind:
            meta_parts.append(f"kind={kind}")
        if fmt:
            meta_parts.append(f"format={fmt}")
        if mime:
            meta_parts.append(f"mime={mime}")
        if filename:
            meta_parts.append(f"filename={filename}")
        if meta_parts:
            lines_local.append("meta: " + "; ".join(meta_parts))

        text_val = (
            art.get("text")
            if art.get("text") is not None
            else art.get("value")
        )
        if text_val is None and art.get("content") is not None:
            text_val = art.get("content")

        full_text = _format_full_value(text_val if text_val is not None else art)
        if full_text:
            lines_local.append("content:")
            lines_local.append("```text")
            lines_local.append(full_text)
            lines_local.append("```")
        else:
            lines_local.append("content: (empty)")

        lines_local.append("")
        return lines_local

    def _format_sources_lines(sources: List[Dict[str, Any]], *, used_sids: Optional[set[int]] = None) -> List[str]:
        lines_local: List[str] = []
        for i, src in enumerate(sources[:max_sources_per_turn], 1):
            if not isinstance(src, dict):
                continue
            sid = src.get("sid")
            url = (src.get("url") or "").strip()
            title = (src.get("title") or "").strip()
            if not url:
                continue
            used_mark = "used" if used_sids is None or sid in used_sids else "unused"
            if title:
                lines_local.append(f"  {i}. {used_mark} S{sid} {url} | {title}")
            else:
                lines_local.append(f"  {i}. {used_mark} S{sid} {url}")
        return lines_local

    def _format_attachment_paths(attachments: List[Dict[str, Any]], *, turn_id: str) -> List[str]:
        lines_local: List[str] = []
        for att in attachments or []:
            if not isinstance(att, dict):
                continue
            filename = (att.get("filename") or "").strip()
            path = (att.get("path") or "").strip()
            mime = (att.get("mime") or att.get("mime_type") or "").strip()
            artifact_name = (att.get("artifact_name") or "").strip()
            if not path and filename:
                path = f"{turn_id}/attachments/{filename}"
            if not filename and path:
                filename = path.split("/")[-1]
            parts = []
            if artifact_name:
                parts.append(f"artifact_name={artifact_name}")
            if path:
                parts.append(f"path={path}")
            if filename:
                parts.append(f"filename={filename}")
            if mime:
                parts.append(f"mime={mime}")
            if parts:
                lines_local.append("- " + " | ".join(parts))
        return lines_local

    def _format_file_paths(slots: Dict[str, Any], *, turn_id: str) -> List[str]:
        lines_local: List[str] = []
        for slot_name, spec in (slots or {}).items():
            if not isinstance(spec, dict):
                continue
            art = spec.get("value") if isinstance(spec.get("value"), dict) else spec
            if not isinstance(art, dict):
                continue
            slot_type = (art.get("type") or spec.get("type") or "").strip().lower()
            if slot_type != "file":
                continue
            filename = (art.get("filename") or "").strip()
            path = (art.get("path") or "").strip()
            mime = (art.get("mime") or spec.get("mime") or "").strip()
            if not filename and path:
                filename = path.split("/")[-1]
            if not path and filename:
                path = filename if turn_id == "current_turn" else f"{turn_id}/files/{filename}"
            parts = [f"slot={slot_name}"]
            if path:
                parts.append(f"path={path}")
            if filename:
                parts.append(f"filename={filename}")
            if mime:
                parts.append(f"mime={mime}")
            lines_local.append("- " + " | ".join(parts))
        return lines_local

    def _slot_kind(name: Optional[str]) -> Optional[str]:
        if not name or not isinstance(output_contract, dict):
            return None
        spec = output_contract.get(name)
        if spec is None:
            return None
        t = spec.get("type") if isinstance(spec, dict) else None
        if isinstance(t, str):
            t = t.strip().lower()
            if t in {"inline", "file"}:
                return t
        return None

    lines: list[str] = []
    lines.append("# The Turn Session Journal / Operational Digest (Current turn). **Strictly ordered from oldest → newest** for prior turns.")
    lines.append("Events are chronological. Do NOT reinterpret later user feedback or decisions as if they occurred before earlier steps.")
    lines.append("")
    if is_codegen_agent:
        fetch_context_tool_retrieval_example = f"Use ctx_tools.fetch_turn_artifacts([turn_id]) to pull artifact"
    else:
        fetch_context_tool_retrieval_example = f"Use 'show_artifacts' to see any artifact in full on next round"
    # lines.append("Previews are truncated. Use ctx_tools.fetch_turn_artifacts([turn_ids]) for full content.")
    lines.append(f"Within turn, User message, assistant final answer can be truncated. Solver artifacts (slots) content is not shown. If available, only their content summary is shown. {fetch_context_tool_retrieval_example}")
    lines.append("Use OUT_DIR-relative file/attachment paths exactly as shown in this journal; do NOT fetch slots to discover paths.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---------- Prior Turns (oldest first) ----------
    lines.append("## Prior Turns (oldest first)")

    if not context.history_turns:
        lines.append("(none)")
    else:

        # 1) pick most recent N (descending, newest → oldest)
        recent = (context.history_turns or [])[:max_prior_turns]

        # 2) display oldest → newest (ascending)
        history_sorted = list(reversed(recent))
        for idx, turn_rec in enumerate(history_sorted, 1):
            # Extract inner metadata dict from {execution_id: meta}
            try:
                execution_id, meta = next(iter(turn_rec.items()))
            except Exception:
                continue

            turn_id = meta.get("turn_id") or execution_id
            ts_full = str(meta.get("ts") or "").strip()
            ts_disp = ts_full[:16].replace("T", " ") if len(ts_full) >= 16 else (ts_full[:10] or "(no date)")

            # Build TurnView from meta payload
            try:
                meta["payload"] = meta.get("turn_log") or {}
                tv = turn_view_class.from_turn_log_dict(meta)
            except Exception as ex:
                print(f"Failed to build TurnView for turn {turn_id}: {ex}")
                print(traceback.format_exc())
                continue

            # Header
            lines.append(f"### Turn {turn_id} — {ts_disp} [HISTORICAL]")
            if is_codegen_agent:
                lines.append(f"Fetch from this turn with: ctx_tools.fetch_ctx(\"{turn_id}\")")
            else:
                lines.append(f"Artifacts of this turn available under namespace {turn_id}.*")
            lines.append("")

            # Complete turn presentation from TurnView (delegates to SolverPresenter)
            # This includes:
            # - [TURN OUTCOME] status
            # - User prompt (from base summary)
            # - Solver details (project_log, deliverables)
            # - Assistant response (if direct answer)
            turn_presentation = tv.to_solver_presentation(
                user_prompt_limit=600,
                user_prompt_or_inventorization_summary="inv",
                program_log_limit=5000,
                include_base_summary=True,  # ← Includes user prompt!
                include_program_log=True,
                include_deliverable_meta=True,
                include_assistant_response=True,
            )

            if turn_presentation.strip():
                lines.append(turn_presentation.strip())
                lines.append("")

            attachments = []
            if isinstance(meta.get("turn_log"), dict):
                user_obj = meta["turn_log"].get("user") or {}
                attachments = list(user_obj.get("attachments") or [])
            attachment_lines = _format_attachment_paths(attachments, turn_id=turn_id)
            if attachment_lines:
                lines.append("[ATTACHMENTS — OUT_DIR-relative paths]")
                lines.extend(attachment_lines)
                lines.append("")

            # Sources (NOT part of solver, formatted separately)
            from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_sources_any, sids_in_text
            sources_pool = normalize_sources_any(
                meta.get("sources_pool")
                or (meta.get("payload") or {}).get("sources_pool")
                or []
            )
            used_sids: set[int] = set()
            for d in (meta.get("deliverables") or []):
                if not isinstance(d, dict):
                    continue
                art = d.get("value")
                if not isinstance(art, dict):
                    continue
                for rec in (art.get("sources_used") or []):
                    if isinstance(rec, (int, float)):
                        used_sids.add(int(rec))
                    elif isinstance(rec, dict):
                        sid = rec.get("sid")
                        if isinstance(sid, (int, float)):
                            used_sids.add(int(sid))
                text = art.get("text") or ""
                if isinstance(text, str) and text.strip():
                    used_sids.update(sids_in_text(text))

            if used_sids:
                sources = [s for s in sources_pool if isinstance(s.get("sid"), int) and s.get("sid") in used_sids]
            else:
                sources = []

            if sources:
                src_lines = _format_sources_lines(sources, used_sids=used_sids)
                if src_lines:
                    lines.append("")
                    lines.append(f"[**Turn Sources:** ({len(sources)} total)]")
                    lines.extend(src_lines)
                    lines.append("")

    lines.append("")
    lines.append("---")
    lines.append("")
    # ---------- Current Turn (live) ----------
    lines.append("## Current Turn (live — oldest → newest events) [CURRENT TURN]")
    try:
        tlog = getattr(context.scratchpad, "turn_log", None)
        ts_raw = None
        if isinstance(tlog, dict):
            ts_raw = tlog.get("started_at_iso") or tlog.get("ended_at_iso")
        else:
            ts_raw = getattr(tlog, "started_at_iso", None) or getattr(tlog, "ended_at_iso", None)
        if ts_raw:
            lines.append(_fmt_ts_for_humans(ts_raw))
    except Exception:
        pass
    try:
        tv = turn_view_class.from_turn_log_dict(context.scratchpad.turn_view)
        current_turn_presentation = tv.to_solver_presentation(
            is_current_turn=True,
            user_prompt_limit=1000,
            program_log_limit=0,
            include_base_summary=True,  # ← Includes user prompt!
            include_program_log=False,
            include_deliverable_meta=False,
            include_assistant_response=False,
        )
        lines.append(current_turn_presentation)

    except Exception as ex:
        print(f"Failed to build TurnView for current_turn: {ex}")
        print(traceback.format_exc())
        lines.append("(failed to render current turn view)")

    try:
        file_lines = _format_file_paths(context.current_slots or {}, turn_id="current_turn")
        if file_lines:
            lines.append("[FILES — OUT_DIR-relative paths]")
            lines.extend(file_lines)
            lines.append("")
    except Exception:
        pass

    try:
        attachments = list(getattr(context.scratchpad, "user_attachments", None) or [])
        attachment_lines = _format_attachment_paths(attachments, turn_id="current_turn")
        if attachment_lines:
            lines.append("[ATTACHMENTS — OUT_DIR-relative paths]")
            lines.extend(attachment_lines)
            lines.append("")
    except Exception:
        pass

    # Current turn sources (full pool with used/unused marks)
    try:
        from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_sources_any, sids_in_text

        current_used_sids: set[int] = set()
        for d in (context.current_slots or {}).values():
            if not isinstance(d, dict):
                continue
            art = d.get("value") if isinstance(d.get("value"), dict) else d
            for rec in (art.get("sources_used") or []):
                if isinstance(rec, (int, float)):
                    current_used_sids.add(int(rec))
                elif isinstance(rec, dict):
                    sid = rec.get("sid")
                    if isinstance(sid, (int, float)):
                        current_used_sids.add(int(sid))
            text = art.get("text") or art.get("content") or ""
            if isinstance(text, str) and text.strip():
                current_used_sids.update(sids_in_text(text))

        for art in (context.artifacts or {}).values():
            if not isinstance(art, dict):
                continue
            for rec in (art.get("sources_used") or []):
                if isinstance(rec, (int, float)):
                    current_used_sids.add(int(rec))
                elif isinstance(rec, dict):
                    sid = rec.get("sid")
                    if isinstance(sid, (int, float)):
                        current_used_sids.add(int(sid))
            text = art.get("text") or art.get("content") or ""
            if isinstance(text, str) and text.strip():
                current_used_sids.update(sids_in_text(text))

        sources_pool = normalize_sources_any(context.sources_pool or [])
        if sources_pool:
            src_lines = _format_sources_lines(sources_pool, used_sids=current_used_sids)
            if src_lines:
                lines.append(f"[**Turn Sources:** ({len(sources_pool)} total)]")
                lines.extend(src_lines)
                lines.append("")
    except Exception:
        pass

    if coordinator_turn_line:
        lines.append("[COORDINATOR TURN DECISION]")
        lines.append(coordinator_turn_line)
        lines.append("")
    lines.append("[CURRENT TURN CONTRACT SLOTS (to fill)]")
    lines.append(json.dumps(_to_jsonable(output_contract or {}), ensure_ascii=False, indent=2),)
    lines.append("")
    lines.append("---")

    # 3) Events timeline (short timestamps)
    lines.append("[EVENTS (oldest → newest)]")
    if not context.events:
        lines.append("(no events yet)")
    else:
        for e in context.events:
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            kind = e.get("kind")

            if kind == "protocol_verify":
                ok = e.get("ok")
                tool_id = (e.get("tool_id") or "").strip()
                codes = e.get("violation_codes") or []
                codes_s = ", ".join([str(c) for c in codes[:4]]) if isinstance(codes, list) else ""
                lines.append(
                    f"- {ts} — protocol_verify: ok={bool(ok)}"
                    + (f" tool={tool_id}" if tool_id else "")
                    + (f" codes=[{codes_s}]" if codes_s else "")
                    + (f" (n={int(e.get('violations_count') or 0)})" if e.get("violations_count") is not None else "")
                )
                continue

            if kind == "tool_call_invalid":
                tool_id = (e.get("tool_id") or "").strip()
                viols = e.get("violations") or []
                codes = []
                if isinstance(viols, list):
                    for v in viols:
                        if isinstance(v, dict) and v.get("code"):
                            codes.append(str(v.get("code")))
                codes_s = ", ".join(codes[:4])
                lines.append(
                    f"- {ts} — tool_call_invalid:"
                    + (f" tool={tool_id}" if tool_id else "")
                    + (f" codes=[{codes_s}]" if codes_s else "")
                )
                continue

            if kind == "best_effort_mapping":
                when = e.get("when")
                applied = e.get("applied") or []
                requested = e.get("requested")
                accepted = e.get("accepted")
                pre_drop = e.get("pre_filter_dropped")
                dropped = e.get("dropped") or []
                errs = e.get("errors") or []
                show = []
                if isinstance(applied, list) and applied:
                    show = [str(x) for x in applied[:4]]
                show_s = ", ".join(show)
                lines.append(
                    f"- {ts} — mapping({when}):"
                    + (f" req={requested}" if requested is not None else "")
                    + (f" acc={accepted}" if accepted is not None else "")
                    + (f" pre_drop={pre_drop}" if pre_drop is not None else "")
                    + f" applied={len(applied) if isinstance(applied, list) else 0}"
                    + f" dropped={len(dropped) if isinstance(dropped, list) else 0}"
                    + f" err={len(errs) if isinstance(errs, list) else 0}"
                    + (f" slots=[{show_s}]" if show_s else "")
                )
                continue
            if kind in ("mapping_dropped_future_artifact", "mapping_dropped_not_in_round_snapshot", "mapping_dropped_unseen_artifact"):
                slot = e.get("slot") or ""
                sp = e.get("source_path") or ""
                lines.append(f"- {ts} — {kind}: slot={slot} ← {sp[:140]}")
                continue

            if kind == "decision":
                nxt = e.get("action")
                strat = e.get("strategy")
                focus = e.get("focus_slot")
                next_plan = (e.get("next_decision_step") or "").strip()

                tc = e.get("tool_call") or {}
                tool_id = (tc.get("tool_id") or "").strip() if isinstance(tc, dict) else ""

                # declared artifacts for this tool call (protocol: list of dicts with "name")
                art_specs = tc.get("tool_res_artifacts") or []
                art_labels: list[str] = []

                for spec in art_specs:
                    if not isinstance(spec, dict):
                        continue  # protocol says dict; ignore anything else
                    art_id = (spec.get("name") or "").strip()
                    if not art_id:
                        continue

                    stored = (context.artifacts or {}).get(art_id) or {}
                    ak = (stored.get("artifact_kind") or "").strip()   # file / inline
                    at = (stored.get("artifact_type") or "").strip()   # often empty
                    tag = f"{ak}/{at}" if ak and at else (ak or at)

                    art_labels.append(f"{art_id}({tag})" if tag else art_id)

                # concise artifacts preview
                arts_s = ""
                if art_labels:
                    max_show = 10
                    shown = art_labels[:max_show]
                    rest = len(art_labels) - len(shown)
                    arts_s = ", ".join(shown) + (f", +{rest} more" if rest > 0 else "")

                # keep important decision signals
                fetch_n = 0
                if e.get("fetch_context_count") is not None:
                    try:
                        fetch_n = int(e.get("fetch_context_count") or 0)
                    except Exception:
                        fetch_n = 0
                else:
                    fetch_ctx = e.get("fetch_context") or []
                    fetch_n = len(fetch_ctx) if isinstance(fetch_ctx, list) else 0

                map_slots = e.get("map_slots") or []
                mapped_pairs: list[str] = []
                def _extract_aid(sp: str) -> str:
                    prefix = "current_turn.artifacts."
                    if isinstance(sp, str) and sp.startswith(prefix):
                        rest = sp[len(prefix):]
                        return (rest.split(".", 1)[0] or "").strip()
                    return ""
                if isinstance(map_slots, list):
                    for ms in map_slots:
                        if isinstance(ms, dict):
                            sn = (ms.get("slot_name") or "").strip()
                            sp = (ms.get("source_path") or "").strip()
                            if not sn:
                                continue
                            aid = _extract_aid(sp)
                            mapped_pairs.append(f"{sn}<-{aid}" if aid else f"{sn}<-path")

                maps_s = ""
                if mapped_pairs:
                    max_map = 4
                    shown = mapped_pairs[:max_map]
                    rest = len(mapped_pairs) - len(shown)
                    maps_s = ", ".join(shown) + (f", +{rest} more" if rest > 0 else "")

                reason = (e.get("reasoning") or "").replace("\n", " ").strip()
                # if len(reason) > 140:
                #     reason = reason[:137] + "..."

                tool_piece = None
                if tool_id:
                    # If this ever happens, it’s a protocol violation we WANT to see.
                    if not art_labels:
                        arts_s = "MISSING_ARTIFACTS"
                    tool_piece = f"tool={tool_id}->{arts_s}"

                pieces = [
                    f"action={nxt}",
                    f"strategy={strat}" if strat else None,
                    f"focus={focus}" if focus else None,
                    f"next_plan={next_plan}" if next_plan else None,
                    tool_piece,
                    f"map={maps_s}" if maps_s else None,
                    f"fetch={fetch_n}" if fetch_n else None,
                    f"reason={reason}" if reason else None,
                ]
                pieces = [p for p in pieces if p]
                lines.append(f"- {ts} — decision: " + " — ".join(pieces))
                continue

            if kind == "tool_started":
                sig = e.get("signature")
                art_ids = e.get("artifact_ids") or []
                art_ids_s = ",".join([str(x) for x in art_ids]) if isinstance(art_ids, list) else str(art_ids)
                if sig:
                    lines.append(f"- {ts} — tool_started: {sig} → {art_ids_s}")
                else:
                    lines.append(f"- {ts} — tool_started: tool={e.get('tool_id')} → {art_ids_s}")
                continue

            if kind == "tool_finished":
                art_ids = e.get("artifact_ids") or []
                planned_ids = e.get("planned_artifact_ids") or []
                produced_ids = e.get("produced_artifact_ids") or []
                tool_id = e.get("tool_id")
                tool_call_id = e.get("tool_call_id")
                if tool_call_id and not planned_ids:
                    call_rec = (context.tool_call_index or {}).get(tool_call_id) or {}
                    planned_ids = call_rec.get("declared_artifact_ids") or []
                if tool_call_id and not produced_ids:
                    call_rec = (context.tool_call_index or {}).get(tool_call_id) or {}
                    produced_ids = call_rec.get("produced_artifact_ids") or []
                if not produced_ids:
                    produced_ids = art_ids
                if not planned_ids:
                    planned_ids = art_ids

                produced_set = set([str(x) for x in produced_ids])
                planned_set = [str(x) for x in planned_ids]
                if planned_set:
                    status_marks = []
                    for aid in planned_set:
                        mark = "✓" if aid in produced_set else "✗"
                        art = (context.artifacts or {}).get(aid)
                        art_kind = art.get("artifact_kind") if isinstance(art, dict) else None
                        if not art_kind:
                            art_kind = _slot_kind(aid)
                        if isinstance(art_kind, str) and art_kind.strip():
                            status_marks.append(f"{mark} {aid}[{art_kind.strip()}]")
                        else:
                            status_marks.append(f"{mark} {aid}")
                    status_marks_s = ", ".join(status_marks)
                else:
                    status_marks_s = ""
                marks_part = f" {status_marks_s}" if status_marks_s else ""
                lines.append(f"- {ts} — tool_finished: {tool_id} →{marks_part}")
                err = e.get("error")
                if isinstance(err, dict):
                    err_code = err.get("code") or err.get("error") or "error"
                    err_msg = (err.get("message") or err.get("description") or "").strip()
                    if err_msg:
                        lines.append(f"  ---tool error: {err_code}: {err_msg}")
                    else:
                        lines.append(f"  ---tool error: {err_code}")
                continue

            if kind in ("code_proj_log", "exec_proj_log"):
                proj = e.get("project_log") or {}
                txt = ""
                if isinstance(proj, dict):
                    txt = proj.get("value") or proj.get("text") or ""
                elif isinstance(proj, str):
                    txt = proj
                preview = _short_with_count(txt.replace("\n", " "), 400) if txt else "(empty)"
                lines.append(f"- {ts} — tool_exec_log: {preview}")
                continue

            payload = {k: v for k, v in e.items() if k != "ts"}
            # lines.append(f"- {ts} — {kind}: {json.dumps(payload, ensure_ascii=False)[:400]}")
            lines.append(f"- {ts} — {kind}: {json.dumps(payload, ensure_ascii=False)[:400]}")

    lines.append("")

    if context.artifacts:
        items_raw = [(k, v) for k, v in (context.artifacts or {}).items() if isinstance(v, dict)]

        # Oldest→newest by timestamp if present; stable fallback to insertion order.
        indexed = list(enumerate(items_raw))
        indexed_sorted = sorted(
            indexed,
            key=lambda iv: (
                float((iv[1][1] or {}).get("timestamp") or 0.0),
                iv[0],
            ),
        )
        items = [pair for _idx, pair in indexed_sorted]

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

        lines.append("#### Current artifacts (oldest→newest)")
        if not items:
            lines.append("(none)")
            lines.append("")
        else:
            for idx, (art_id, art) in enumerate(items, 1):
                tid = art.get("tool_id", "?")
                error = art.get("error")
                status = "FAILED" if error else "success"
                summ = (art.get("summary") or "").replace("\n", " ")
                value = art.get("value")
                value_dict = value if isinstance(value, dict) else {}
                art_kind = art.get("artifact_kind")
                if not art_kind:
                    art_kind = _slot_kind(art_id)
                fmt = value_dict.get("format") if isinstance(value_dict.get("format"), str) else None
                mime = value_dict.get("mime") if isinstance(value_dict.get("mime"), str) else None
                filename = value_dict.get("filename") if isinstance(value_dict.get("filename"), str) else None
                meta_parts = []
                if isinstance(art_kind, str) and art_kind.strip():
                    meta_parts.append(f"kind={art_kind.strip()}")
                if fmt:
                    meta_parts.append(f"format={fmt}")
                if mime:
                    meta_parts.append(f"mime={mime}")
                if filename:
                    meta_parts.append(f"filename={filename}")

                if error:
                    lines.append(
                        f"- {idx:02d}. {art_id} ← {tid}: {status} - "
                        f"{error.get('code')}: {error.get('message', '')[:150]}"
                    )
                    if summ:
                        lines.append(f"    summary: {summ}")
                else:
                    lines.append(
                        f"- {idx:02d}. {art_id} ← {tid}: {status} — summary: {summ}"
                    )
                lines.append(f"    path: current_turn.artifacts.{art_id}")
                if meta_parts:
                    lines.append("    meta: " + "; ".join(meta_parts))

                if tools_insights.is_search_tool(tid):
                    q_prev, obj_prev = _search_context_from_inputs(art)
                    if q_prev or obj_prev:
                        lines.append("    search_context:")
                        if q_prev:
                            lines.append(f"      queries  : {q_prev}")
                        if obj_prev:
                            lines.append(f"      objective: \"{obj_prev}\"")

        lines.append("")

    # 2) Live snapshot (current contract/slots/tool results)
    declared = list((output_contract or {}).keys())
    filled = list((context.current_slots or {}).keys())
    filled_set = set(filled)
    pending = [s for s in declared if s not in filled_set]

    lines.append("[CURRENT TURN PROGRESS SNAPSHOT]")
    lines.append("")
    lines.append("# Contract Status")
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
            slot_attrs={"description", "gaps", "artifact_id", "filename"},
            file_path_prefix="",
        )

        lines.append("# Current Slots")
        lines.append("")
        lines.append(slots_md)
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

    if show_artifacts:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Full Context Artifacts (show_artifacts)")
        lines.append("")
        for item in show_artifacts:
            if not isinstance(item, dict):
                lines.append("### (invalid show_artifacts entry)")
                lines.append("content:")
                lines.append("```text")
                lines.append(_format_full_value(item))
                lines.append("```")
                lines.append("")
                continue
            lines.extend(_render_full_artifact(item))

    return "\n".join(lines)

def build_session_log_summary(session_log: List[Dict[str, Any]],
                              slot_specs: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Build session log summary (informative "mind map" + quick stats).

    Design goals:
    - Timeline is oldest → newest and grows only by APPENDING lines.
      This makes it prefix-cache-friendly across iterations.
    - A single stats snapshot line is appended at the END (suffix),
      so it can change each round without invalidating the prefix.
    """

    # --- Helpers ---------------------------------------------------------
    def _short(s: str | None, n: int) -> str:
        if not s:
            return ""
        s = str(s)
        return s if len(s) <= n else s[:n] + "..."

    def _slot_kind(name: Optional[str]) -> Optional[str]:
        if not name or not isinstance(slot_specs, dict):
            return None
        spec = slot_specs.get(name)
        if spec is None:
            return None
        # SlotSpec or dict
        t = getattr(spec, "type", None)
        if t is None and isinstance(spec, dict):
            t = spec.get("type")
        if isinstance(t, str):
            t = t.strip().lower()
            if t in {"inline", "file"}:
                return t
        return None

    def _artifact_kind_from_tool_id(tool_id: Optional[str]) -> Optional[str]:
        if not isinstance(tool_id, str):
            return None
        tid = tool_id.strip()
        # Heuristic: all format-specific writers and write_file → (file)
        if tid.startswith("generic_tools.write_"):
            return "file"
        # Most non-writer tools produce inline content (e.g., LLM gen, web search digests)
        return "inline"

    def _norm_tool_res_artifacts(tc: Any) -> List[Dict[str, Any]]:
        if not isinstance(tc, dict):
            return []
        tras = tc.get("tool_res_artifacts")

        # accept dict -> list
        if isinstance(tras, dict):
            tras = [tras]
        if not isinstance(tras, list):
            return []

        return [a for a in tras if isinstance(a, dict)]

    def _fmt_res_artifacts(tras: List[Dict[str, Any]]) -> str:
        if not tras:
            return "-"
        parts = []
        for a in tras:
            n = (a.get("name") or "").strip() or "?"
            t = (a.get("type") or "").strip()
            k = (a.get("kind") or "").strip()
            parts.append(f"{n}{(':' + t) if t else ''}[{k}]")
        return ", ".join(parts)

    # --- grouping helpers ---------------
    def _tool_group_key(e: Dict[str, Any]) -> Optional[str]:
        return e.get("tool_call_id")

    def _same_tool_group(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        if a.get("type") != "tool_execution" or b.get("type") != "tool_execution":
            return False
        if (a.get("tool_id") or None) != (b.get("tool_id") or None):
            return False

        ka = _tool_group_key(a)
        kb = _tool_group_key(b)

        return ka == kb


    def _call_status(group: List[Dict[str, Any]]) -> str:
        # Aggregate status similarly to old semantics but aware of multi-artifact calls.
        if any(isinstance(g.get("error"), dict) and g.get("error") for g in group):
            return "FAILED"
        sts = [str(g.get("status", "")).strip() for g in group if str(g.get("status", "")).strip()]
        if not sts:
            return "ok"
        uniq: List[str] = []
        for s in sts:
            if s not in uniq:
                uniq.append(s)
        if len(uniq) == 1:
            return uniq[0]
        return "mixed:" + ",".join(uniq[:3]) + ("..." if len(uniq) > 3 else "")

    def _artifact_fields_from_entry(e: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str]:
        tool_id = e.get("tool_id")
        artifact_id = e.get("artifact_id")
        art_type = e.get("artifact_type")
        art_kind = e.get("artifact_kind")
        res_summ = e.get("result_summary") or ""
        return tool_id, artifact_id, art_type, art_kind, res_summ

    # --- Stats (unchanged semantics) ------------------------------------
    total_decisions = sum(1 for e in (session_log or []) if e.get("type") == "decision")
    tool_execs = [e for e in (session_log or []) if e.get("type") == "tool_execution"]
    total_tools = len(tool_execs)
    ok_set = {"ok", "success", "succeeded", "completed", "exit", "done"}
    ok_tools = sum(1 for e in tool_execs if str(e.get("status", "")).lower() in ok_set)
    fail_tools = total_tools - ok_tools
    distinct_tool_ids = sorted({e.get("tool_id") for e in tool_execs if e.get("tool_id")})

    log_summary: List[str] = []

    # --- Timeline (prefix-friendly, oldest → newest) ---------------------
    log_summary.append("Timeline (oldest→newest)")
    if not session_log:
        log_summary.append("(empty)")
    else:
        i = 0
        while i < len(session_log):
            entry = session_log[i]
            t = entry.get("type")
            it = entry.get("iteration")

            if t == "decision":
                dec = entry.get("decision", {}) or {}
                nxt = dec.get("action")
                tc = dec.get("tool_call") or {}
                tool_id = tc.get("tool_id")

                tras = _norm_tool_res_artifacts(tc)
                tras_s = _fmt_res_artifacts(tras)

                call_reason = tc.get("reasoning") or dec.get("reasoning") or ""
                fetch_n = len(dec.get("fetch_context") or [])
                map_slots_list = dec.get("map_slots") or []
                strategy = dec.get("strategy")
                focus_slot = dec.get("focus_slot")

                segs = [f"[{it}] Decision: {nxt}"]
                if strategy:
                    segs.append(f"strategy:{strategy}")
                if focus_slot:
                    segs.append(f"focus:{focus_slot}")

                if tool_id or tras:
                    segs.append(f"{tool_id or '-'} → {tras_s}")

                if map_slots_list:
                    mapped_names = [
                        ms.get("slot_name") for ms in map_slots_list if isinstance(ms, dict)
                    ]
                    for name in mapped_names:
                        sk = _slot_kind(name)
                        segs.append(f"map:{name}{(' (' + sk + ')') if sk else ''}")

                if fetch_n:
                    segs.append(f"fetch:{fetch_n}")
                if call_reason:
                    segs.append(f"reason: {_short(call_reason, 120)}") # 180

                # Handle non-tool decisions explicitly
                if nxt in ("complete", "exit") and dec.get("completion_summary"):
                    segs.append(f"done: {_short(dec.get('completion_summary'), 200)}")
                if nxt == "clarify" and dec.get("clarification_questions"):
                    qs = dec.get("clarification_questions") or []
                    segs.append("clarify: " + _short("; ".join(qs), 200))

                log_summary.append(" — ".join(segs))
                i += 1
                continue

            elif t == "tool_execution":
                # ---  group consecutive tool_execution entries for same tool call ---
                group: List[Dict[str, Any]] = [entry]
                j = i + 1
                while j < len(session_log):
                    nxt_e = session_log[j]
                    if nxt_e.get("type") != "tool_execution":
                        break
                    if not _same_tool_group(entry, nxt_e):
                        break
                    group.append(nxt_e)
                    j += 1

                tool_id0, _, _, _, _ = _artifact_fields_from_entry(group[0])
                tool_id0 = tool_id0 or "?"
                call_status = _call_status(group)

                # Determine a stable-ish kind tag for the *call header* (optional)
                kinds: List[str] = []
                for g in group:
                    _, _, _, ak, _ = _artifact_fields_from_entry(g)
                    kk = ak or _artifact_kind_from_tool_id(tool_id0) or ""
                    if kk and kk not in kinds:
                        kinds.append(kk)
                header_kind = kinds[0] if len(kinds) == 1 else ""
                kind_tag = f" ({header_kind})" if header_kind else ""

                def _fmt_art_ref(art_name: Optional[str], art_type: Optional[str]) -> str:
                    name = (art_name or "").strip() or "?"
                    typ = (art_type or "").strip()
                    return f"[{name}:{typ}]" if typ else f"[{name}]"

                if len(group) == 1:
                    # Preserve EXACT old single-line behavior
                    g = group[0]
                    tool_id, art_name, art_type, art_kind, res_summ = _artifact_fields_from_entry(g)
                    status = g.get("status") or _call_status([g])
                    error = g.get("error")

                    kind = art_kind or _artifact_kind_from_tool_id(tool_id)
                    kind_tag_single = f" ({kind})" if kind else ""

                    if error:
                        log_summary.append(
                            f"[{it}] Tool: {tool_id}{kind_tag_single} → FAILED "
                            f"{_fmt_art_ref(art_name, art_type)} "
                            f"— ERROR: {error.get('code')}: {error.get('message', '')[:200]}"
                        )
                    else:
                        log_summary.append(
                            f"[{it}] Tool: {tool_id} → "
                            f"{(art_name or '').strip() or '?'}"
                            f"{(':' + (art_type or '').strip()) if (art_type or '').strip() else ''}"
                            f"{('(' + (art_kind or _artifact_kind_from_tool_id(tool_id) or '') + ')') if (art_kind or _artifact_kind_from_tool_id(tool_id)) else ''} ✓ "
                            f"— {_short(res_summ, 80)}"
                        )
                else:
                    # Grouped rendering: 1 header + per-artifact indented lines
                    log_summary.append(
                        f"[{it}] Tool: {tool_id0}{kind_tag} → {call_status} "
                        f"({len(group)} artifacts)"
                    )

                    for n, g in enumerate(group, 1):
                        tool_id, art_name, art_type, art_kind, res_summ = _artifact_fields_from_entry(g)
                        status = g.get("status")
                        error = g.get("error")

                        kind = art_kind or _artifact_kind_from_tool_id(tool_id0)
                        kind_s = f"{kind}" if kind else ""

                        if error:
                            log_summary.append(
                                f"  ({n}) {_fmt_art_ref(art_name, art_type)}"
                                f"{(' (' + kind_s + ')') if kind_s else ''}"
                                f" — FAILED — ERROR: {error.get('code')}: {str(error.get('message', ''))[:200]}"
                            )
                        else:
                            log_summary.append(
                                f"  ({n}) {_fmt_art_ref(art_name, art_type)}"
                                f"{(' (' + kind_s + ')') if kind_s else ''}"
                                f" — {status} — {_short(res_summ, 80)}"
                            )

                i = j
                continue

            else:
                # Fallback for any other event types (unchanged)
                log_summary.append(f"[{it or '?'}] {t}")
                i += 1
                continue

    # --- Stats snapshot at the END (suffix, can change per round) --------
    log_summary.append("")  # blank line as separator
    log_summary.append(
        f"Stats: decisions={total_decisions};tools={total_tools}  "
        f"ok={ok_tools};fail={fail_tools};unique_tools={len(distinct_tool_ids)}"
    )

    return log_summary

def build_tool_catalog(adapters: Optional[List[Dict[str, Any]]] = None,
                       *,
                       exclude_tool_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    tool_catalog: List[Dict[str, Any]] = []
    exclude = set(exclude_tool_ids or [])
    for a in (adapters or []):
        tool_id = a.get("id")
        if tool_id in exclude:
            continue
        doc = a.get("doc") or {}
        item = {
            "id": tool_id,
            # "import": a.get("import"),
            "call_template": a.get("call_template"),
            "purpose": doc.get("purpose", ""),
            "is_async": bool(a.get("is_async")),
            "args": doc.get("args", {}),
            "returns": doc.get("returns", ""),
        }
        # Include constraints and examples only if they exist in the original doc
        if "constraints" in doc:
            item["constraints"] = doc["constraints"]
        if "examples" in doc:
            item["examples"] = doc["examples"]
        tool_catalog.append(item)
    return tool_catalog

def build_operational_digest(*,
                             turn_session_journal: str,
                             session_log: List[Dict[str, Any]],
                             slot_specs: Optional[Dict[str, Any]] = None,
                             adapters: Optional[List[Dict[str, Any]]] = None,
                             exclude_tool_ids: Optional[List[str]] = None) -> str:
    """
    Combine the turn session journal with a session log summary.
    The journal must remain the prefix to preserve cache behavior across agents.
    """
    playbook_text = (turn_session_journal or "").strip()
    tool_catalog = build_tool_catalog(adapters, exclude_tool_ids=exclude_tool_ids)
    tool_block = ""
    if tool_catalog:
        tool_block = "\n".join([
            "## Available Common Tools",
            json.dumps(tool_catalog or [], ensure_ascii=False, indent=2),
        ])
    log_summary = build_session_log_summary(session_log=session_log, slot_specs=slot_specs)
    log_block = "\n".join([
        "## Session Log (recent events, summary)",
        "\n".join(log_summary) if log_summary else "(empty)",
    ])
    parts = []
    if tool_block:
        parts.append(tool_block)
    if playbook_text:
        parts.append(playbook_text)
    parts.append(log_block)
    return "\n\n".join(parts)


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
    if kind == "solver.program.presentation" or "program presentation" in gist.lower():
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
        scratchpad: Optional[Any] = None,
        current_turn_log: Optional[str] = None,
        current_turn_fp: Optional[TurnFingerprintV1] = None,
        current_turn_payload: Optional[Dict[str, Any]] = None,
        context_bundle: Optional[Any] = None,
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

    if context_bundle and getattr(context_bundle, "program_history_reconciled", None):
        for rec in (context_bundle.program_history_reconciled or []):
            if not isinstance(rec, dict):
                continue
            exec_id, meta = next(iter(rec.items()))
            if not isinstance(meta, dict):
                continue
            tid = meta.get("turn_id") or ""
            ts = meta.get("ts") or ""
            raw_items.append({
                "id": f"context_bundle:{exec_id}",
                "ts": ts,
                "timestamp": ts,
                "tags": [f"turn:{tid}"] if tid else [],
                "payload": {"payload": meta.get("turn_log") or {}},
            })
    else:
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
    if not (context_bundle and getattr(context_bundle, "program_history_reconciled", None)):
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
        if not cur or ts_key(ts) >= ts_key(cur.get("ts") or cur.get("timestamp") or ""):
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

    def _selected_memories_block() -> str:
        if not selected_bucket_cards:
            return ""
        lines = ["[selected.memories]"]
        for card in selected_bucket_cards or []:
            if not isinstance(card, dict):
                continue
            bid = (card.get("bucket_id") or "").strip()
            name = (card.get("name") or "").strip()
            desc = (card.get("short_desc") or "").strip()
            parts: list[str] = []
            if bid:
                parts.append(f"id={bid}")
            if name:
                parts.append(f"name={name}")
            if desc:
                parts.append(f"desc={desc}")
            if parts:
                lines.append("- " + " | ".join(parts))
        return "\n".join(lines) if len(lines) > 1 else ""

    # 2.a Current turn (insights from provided current_turn_fp; no loading)
    if scratchpad is not None:
        if current_turn_log is None:
            try:
                current_turn_log = scratchpad.tlog.to_markdown()
            except Exception:
                pass
        if current_turn_fp is None:
            current_turn_fp = getattr(scratchpad, "turn_fp", None)
        if current_turn_payload is None:
            try:
                current_turn_payload = scratchpad.turn_view
            except Exception:
                pass

    if current_turn_log or current_turn_fp or current_turn_payload:
        started_at = None
        if scratchpad is not None:
            try:
                started_at = getattr(scratchpad.tlog, "started_at_iso", None)
            except Exception:
                started_at = None
        ts_now_iso = (
            started_at
            or (current_turn_payload.get("timestamp") if isinstance(current_turn_payload, dict) else None)
            or (current_turn_fp.made_at if isinstance(current_turn_fp, TurnFingerprintV1) else None)
            or _dt.datetime.utcnow().isoformat() + "Z"
        )
        one_line = ""
        if isinstance(current_turn_fp, TurnFingerprintV1):
            try:
                one_line = render_fingerprint_one_liner(current_turn_fp) or ""
            except Exception:
                pass

        log_block = (current_turn_log or "").strip()
        if not log_block and one_line:
            log_block = "[turn insights]\n" + one_line
        mem_block = _selected_memories_block()
        if mem_block:
            if log_block:
                log_block = f"{log_block}\n{mem_block}"
            else:
                log_block = mem_block

        text_blocks.append("\n".join([
            f"[CURRENT TURN turn_id={turn_id}]",
            _fmt_ts_for_humans(ts_now_iso),
            (log_block or "[turn log]"),
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

    # 2.b Active memory insights
    text_blocks.append("[ACTIVE MEMORY INSIGHTS]")
    if memory_log_entries:
        for entry in (memory_log_entries or []):
            try:
                text_blocks.append(json.dumps(entry, ensure_ascii=False))
            except Exception:
                text_blocks.append(str(entry))
    else:
        text_blocks.append("(none)")
    text_blocks.append("")

    # 2.c Prior turns — COMPRESSED VIEWS using TurnView (newest→oldest)
    last_turns_details: List[dict] = []
    last_turns_mate: List[dict] = []

    items_sorted = sorted(raw_items,
                          key=lambda item: item.get("ts") or item.get("timestamp") or "",
                          reverse=True)

    text_blocks.append("[PRIOR TURNS — COMPRESSED VIEWS]")

    for item in items_sorted:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or ""
        ts_raw = item.get("ts") or item.get("timestamp") or ""

        # payload = item.get("payload") or {}

        try:
            # Build TurnView from the raw item

            tv = turn_view_class.from_saved_payload(
                turn_id=tid,
                user_id=user_id,
                conversation_id=conversation_id,
                payload=item,  # Pass the full item (payload already unwrapped)
            )

            # Generate compressed view with one-liner and without turn info header
            compressed_view = tv.to_compressed_search_view(
                include_turn_info=False,
                user_prompt_limit=600,
                include_turn_summary=True,
                include_context_used=True,
                deliverables_detalization="summary"
            )

            # Add timestamp header
            text_blocks.append("\n".join([
                f"[TURN turn_id={tid}]",
                _fmt_ts_for_humans(ts_raw),
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
                "log_payload": item.get("payload") or {},
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
        ub = ts_key(oldest_included_ts) if oldest_included_ts else float("inf")
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

            if not oldest_included_ts or ts_key(made_at) < ub:
                orphan_insights.append(rec)

        if orphan_insights:
            orphan_sorted = sorted(orphan_insights, key=lambda x: x.get("ts") or "", reverse=True)
            lines = ["[EARLIER TURNS — SUMMARY]"]
            for it in orphan_sorted[:16]:
                lines.append(
                    f"[TURN turn_id={it.get('turn_id')}]\n{_fmt_ts_for_humans(it.get('ts') or '')}\n{it.get('insights_one_liner')}\n"
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

    # Prefer reconciled history if present on scratchpad (avoids re-materializing)
    history_by_tid: Dict[str, Dict[str, Any]] = {}
    try:
        bundle = getattr(scratchpad, "context_bundle", None)
        if bundle and getattr(bundle, "program_history_reconciled", None):
            for rec in (bundle.program_history_reconciled or []):
                try:
                    _, meta = next(iter(rec.items()))
                except Exception:
                    continue
                tid = meta.get("turn_id")
                if tid:
                    history_by_tid[tid] = meta
    except Exception:
        history_by_tid = {}

    newest_prior_nonclar_idx = -1
    for idx, tid in enumerate(materialize_ids):
        if tid in history_by_tid:
            saved_payload = history_by_tid[tid].get("turn_log") or {}
            if newest_prior_nonclar_idx == -1 and not _is_clarification_turn(saved_payload or {}):
                newest_prior_nonclar_idx = idx
            continue
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
        mat = None
        saved_payload = {}
        if tid in history_by_tid:
            saved_payload = history_by_tid[tid].get("turn_log") or {}
        else:
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

        if not saved_payload:
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
                "kind": "solver.program.presentation",
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
        def _extract_text_field(obj: Dict[str, Any], key: str) -> str:
            v = obj.get(key)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                return (v.get("text") or v.get("content") or v.get("value") or "").strip()
            return ""

        user_obj = {}
        asst_obj = {}
        user_ts = None
        asst_ts = None
        if mat:
            user_obj = unwrap_payload((mat.get("user") or {}).get("payload") or {})
            user_ts = (mat.get("user") or {}).get("ts")
            asst_obj = unwrap_payload((mat.get("assistant") or {}).get("payload") or {})
            asst_ts = (mat.get("assistant") or {}).get("ts")
        else:
            user_obj = history_by_tid[tid].get("user") or {}
            asst_obj = history_by_tid[tid].get("assistant") or {}
            user_ts = history_by_tid[tid].get("ts")
            asst_ts = history_by_tid[tid].get("ts")

        user_text = _extract_text_field(user_obj, "prompt").strip()
        asst_text = _extract_text_field(asst_obj, "completion").strip()
        attachments = list(user_obj.get("attachments") or [])

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
            "user": {"text": user_text, "ts": user_ts, "attachments": attachments},
            "assistant": {"text": asst_text, "ts": asst_ts},
            "artifacts": artifacts,
            "compressed_turn": compressed_turn,
        })

        # Citations
        if mat:
            cit = (unwrap_payload((mat.get("citables") or {}).get("payload") or {}).get("items")) or []
            if cit:
                citations_merged.extend(cit)
        else:
            cit = history_by_tid[tid].get("sources_pool") or []
            if cit:
                citations_merged.extend(cit)

    pairs.sort(key=lambda p: (p.get("user") or {}).get("ts") or (p.get("assistant") or {}).get("ts") or "")
    citations_merged = ctx_retrieval_module._dedup_citations(citations_merged)
    return pairs, citations_merged

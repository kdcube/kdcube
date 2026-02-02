# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/context/presentation.py

import json, re, time
import datetime as _dt
from typing import Dict, Any, Optional, List, Literal, Tuple, Protocol, Type

from kdcube_ai_app.apps.chat.sdk.context.memory.presentation import (
    format_feedback_block,
    format_turn_memories_block,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.turn_fingerprint import TurnFingerprintV1, render_fingerprint_one_liner
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import BaseTurnView, CompressedTurn, turn_to_pair
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import (
    format_tool_error_message,
    format_tool_error_for_journal,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.strategy_and_budget import format_budget_for_llm
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import build_skills_instruction_block
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import skills_gallery_text
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights

import kdcube_ai_app.apps.chat.sdk.runtime.solution.context.retrieval as ctx_retrieval_module
from kdcube_ai_app.apps.chat.sdk.util import _truncate, _turn_id_from_tags_safe, _to_jsonable, ts_key, _shorten, estimate_b64_size
from kdcube_ai_app.apps.chat.sdk.tools.backends.web.ranking import estimate_tokens


def _format_full_value_for_journal(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False, indent=2)
    except Exception:
        return str(val)


def _render_full_artifact_for_journal(item: Dict[str, Any]) -> List[str]:
    lines_local: list[str] = []
    ctx_path = (item.get("context_path") or "").strip()
    art_type = (item.get("artifact_type") or "").strip()
    ts = (item.get("timestamp") or item.get("ts_human") or item.get("time") or "").strip()
    modal_attachments = item.get("modal_attachments") or []
    modal_status = item.get("modal_attachments_status") or {}
    art = item.get("artifact") or {}
    if not isinstance(art, dict):
        art = {"value": art}

    kind = (art.get("kind") or art.get("type") or "").strip()
    fmt = (art.get("format") or "").strip()
    mime = (art.get("mime") or "").strip()
    filename = (art.get("filename") or art.get("path") or "").strip()
    header = f"### {ctx_path} [artifact]" if ctx_path else "### [artifact]"
    lines_local.append(header)

    meta_bits = []
    if ts:
        meta_bits.append(f"time={ts}")
    if kind:
        meta_bits.append(f"kind={kind}")
    if fmt:
        meta_bits.append(f"format={fmt}")
    if mime:
        meta_bits.append(f"mime={mime}")
    if filename:
        meta_bits.append(f"filename={filename}")
    if art_type:
        meta_bits.append(f"type={art_type}")
    if meta_bits:
        lines_local.append("meta: " + "; ".join(meta_bits))

    if modal_status:
        total = modal_status.get("total")
        included = modal_status.get("included")
        cap = modal_status.get("cap")
        omitted = modal_status.get("omitted")
        status_line = f"modal_attachments: included {included}/{total} (cap={cap})"
        if not included:
            status_line = f"modal_attachments: none attached (0/{total}, cap={cap})"
        lines_local.append(status_line)
        if modal_attachments:
            included_entries = []
            for att in modal_attachments:
                path = (att.get("path") or att.get("filename") or "").strip()
                mime = (att.get("mime") or "").strip()
                size = att.get("size") or att.get("size_bytes")
                label = path or "unknown"
                parts = []
                if mime:
                    parts.append(f"mime={mime}")
                if size is not None:
                    parts.append(f"size={size}")
                if parts:
                    label = f"{label} ({', '.join(parts)})"
                included_entries.append(label)
            if included_entries:
                lines_local.append("modal_attachments_included: " + ", ".join(included_entries))
        omitted_mimes = modal_status.get("omitted_mimes") or []
        if omitted_mimes:
            om_txt = ", ".join(
                f"{(o.get('path') or 'unknown')} (mime={(o.get('mime') or 'unknown')}, "
                f"size={o.get('size') or 'n/a'}, {o.get('reason') or 'omitted'})"
                for o in omitted_mimes
            )
            lines_local.append(f"modal_attachments_omitted: {om_txt}")
    elif modal_attachments:
        lines_local.append(
            "note: multimodal content attached in modal blocks; content not shown here"
        )

    value = art.get("value")
    if value in (None, "") and (art.get("kind") == "inline"):
        if isinstance(art.get("text"), str) and art.get("text").strip():
            value = art.get("text")
    if isinstance(value, dict):
        # Avoid leaking base64 into the journal
        value = dict(value)
        if "base64" in value:
            value["base64"] = ""
    if value not in (None, ""):
        lines_local.append("content:")
        lines_local.append("```text")
        lines_local.append(_format_full_value_for_journal(value))
        lines_local.append("```")
    lines_local.append("")
    return lines_local


def render_full_context_artifacts_for_journal(show_artifacts: Optional[List[Dict[str, Any]]]) -> str:
    if not show_artifacts:
        return ""
    lines: list[str] = []
    lines.append("[FULL CONTEXT ARTIFACTS (show_artifacts)]")
    lines.append("")
    for item in show_artifacts:
        if not isinstance(item, dict):
            lines.append("### (invalid show_artifacts entry)")
            lines.append("content:")
            lines.append("```text")
            lines.append(_format_full_value_for_journal(item))
            lines.append("```")
            lines.append("")
            continue
        lines.extend(_render_full_artifact_for_journal(item))
    return "\n".join(lines)

def _payload_unwrap(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap ctx store payloads where payload.payload holds the actual turn log."""
    if not isinstance(rec, dict):
        return {}
    pay = rec.get("payload") or {}
    if isinstance(pay, dict) and isinstance(pay.get("payload"), dict):
        return pay["payload"]
    return pay if isinstance(pay, dict) else {}


def build_turn_session_journal(*,
                               context: ReactContext,
                               output_contract: Dict[str, Any],
                               max_prior_turns: int = 5,
                               max_sources_per_turn: int = 20,
                               turn_view_class: Type[BaseTurnView] = BaseTurnView,
                               is_codegen_agent: bool = False, # False for decision, True for codegen
                               model_label: Optional[str] = None,
                               # fetch_context_tool_retrieval_example: Optional[str] = "ctx_tools.fetch_turn_artifacts([turn_id])",
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

    def _format_assistant_files_lines(files: List[Dict[str, Any]], *, turn_id: str, is_current: bool) -> List[str]:
        lines_local: list[str] = []
        for f in files or []:
            if not isinstance(f, dict):
                continue
            filename = (f.get("filename") or "").strip()
            if not filename:
                continue
            art_name = (f.get("artifact_name") or "").strip()
            mime = (f.get("mime") or "").strip()
            size = f.get("size") or f.get("size_bytes")
            path = filename if is_current else f"{turn_id}/files/{filename}"
            parts = [f"filename={filename}", f"path={path}"]
            if art_name:
                parts.append(
                    f"artifact_path={'current_turn' if is_current else turn_id}.files.{art_name}"
                )
            if mime:
                parts.append(f"mime={mime}")
            if size is not None:
                parts.append(f"size={size}")
            lines_local.append("- " + " | ".join(parts))
        return lines_local

    def _format_sources_lines(
        sources: List[Dict[str, Any]],
        *,
        used_sids: Optional[set[int]] = None,
        current_turn_id: Optional[str] = None,
    ) -> List[str]:
        lines_local: List[str] = []
        groups: Dict[str, List[Dict[str, Any]]] = {}
        order: List[str] = []
        for src in sources[:max_sources_per_turn]:
            if not isinstance(src, dict):
                continue
            origin = (src.get("turn_id") or "").strip() or "unknown"
            if origin not in groups:
                groups[origin] = []
                order.append(origin)
            groups[origin].append(src)
        idx = 0
        for origin in order:
            group = groups.get(origin) or []
            sids = []
            for src in group:
                try:
                    sid = int(src.get("sid"))
                    sids.append(f"S{sid}")
                except Exception:
                    continue
            if sids:
                origin_label = origin
                if current_turn_id:
                    if origin == "current_turn":
                        origin_label = f"{current_turn_id} (current turn)"
                    elif origin == current_turn_id:
                        origin_label = f"{origin} (current turn)"
                lines_local.append(f"  {origin_label}: " + ", ".join(sids))
            for src in group:
                idx += 1
                sid = src.get("sid")
                url = (src.get("url") or "").strip()
                title = (src.get("title") or "").strip()
                mime = (src.get("mime") or "").strip()
                has_b64 = bool(src.get("base64"))
                if not url:
                    continue
                used_mark = "used" if used_sids is None or sid in used_sids else "unused"
                extra = []
                if mime:
                    extra.append(f"mime={mime}")
                source_type = (src.get("source_type") or "").strip()
                if source_type:
                    extra.append(f"type={source_type}")
                text_val = src.get("content") or src.get("text") or ""
                if isinstance(text_val, str) and text_val:
                    try:
                        text_bytes = len(text_val.encode("utf-8"))
                    except Exception:
                        text_bytes = None
                    try:
                        text_tokens = estimate_tokens(text_val)
                    except Exception:
                        text_tokens = None
                    if text_bytes is not None:
                        extra.append(f"text_bytes={text_bytes}")
                    if text_tokens is not None:
                        extra.append(f"text_toks={text_tokens}")
                if has_b64 and isinstance(src.get("base64"), str):
                    b64_bytes = estimate_b64_size(src.get("base64"))
                    if b64_bytes is not None:
                        extra.append(f"b64_bytes={b64_bytes}")
                        extra.append(f"b64_toks={max(1, int(b64_bytes // 4))}")
                fetched_time_iso = (src.get("fetched_time_iso") or "").strip()
                if fetched_time_iso:
                    extra.append(f"fetched={fetched_time_iso}")
                modified_time_iso = (src.get("modified_time_iso") or "").strip()
                published_time_iso = (src.get("published_time_iso") or "").strip()
                if modified_time_iso:
                    extra.append(f"modified={modified_time_iso}")
                elif published_time_iso:
                    extra.append(f"published={published_time_iso}")
                if has_b64 and mime:
                    extra.append("multimodal")
                extra_txt = (" | " + " ".join(extra)) if extra else ""
                if title:
                    lines_local.append(f"    {idx}. {used_mark} S{sid} {url} | {title}{extra_txt}")
                else:
                    lines_local.append(f"    {idx}. {used_mark} S{sid} {url}{extra_txt}")
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
                parts.append(f"path=\"{path}\"")
            if filename:
                parts.append(f"filename={filename}")
            if mime:
                parts.append(f"mime={mime}")
            if parts:
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
    lines.append("Memory sections below follow the same oldest→newest ordering as the prior turns above.")
    lines.append("")
    if is_codegen_agent:
        fetch_context_tool_retrieval_example = f"Use ctx_tools.fetch_turn_artifacts([turn_id]) to pull artifact"
    else:
        fetch_context_tool_retrieval_example = f"Use 'show_artifacts' to see any artifact in full on next round"
    # lines.append("Previews are truncated. Use ctx_tools.fetch_turn_artifacts([turn_ids]) for full content.")
    lines.append(f"Within turn, user message and assistant answer are shown from turn log. Solver artifacts (slots) content is not shown. If available, only their content summary is shown. {fetch_context_tool_retrieval_example}")
    lines.append("For show_artifacts: text artifacts show full content; unsupported binaries show best-effort surrogate; multimodal-supported artifacts show definition only and are attached as multimodal blocks.")
    lines.append("Slots define the turn contract (what must be delivered). Some slots are file slots, but the turn may produce additional files on the way.")
    lines.append("All produced files are tracked and can be reused later (e.g., images, spreadsheets). These files also have surrogates and summaries.")
    lines.append("Use OUT_DIR-relative file/attachment paths exactly as shown in this journal; do NOT fetch slots to discover paths.")
    lines.append("Files from prior turns live under: <turn_id>/files/<filename>; current turn files are just <filename>.")
    lines.append("Continuation rule: if a slot lists Sources used (SIDs) and you need more detail than the slot content provides, use sources_pool; re-fetch only if volatile or freshness is required.")
    lines.append("All SIDs across turns refer to the global sources_pool below.")
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
            lines.append("****")
            lines.append(f"### Turn {turn_id} — {ts_disp} [HISTORICAL]")
            lines.append("****")
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
                program_log_limit=None,
                include_base_summary=True,  # ← Includes user prompt!
                include_program_log=True,
                include_deliverable_meta=True,
                include_assistant_response=True,
            )

            if turn_presentation.strip():
                lines.append(turn_presentation.strip())
                lines.append("")

            try:
                turn_log = meta.get("turn_log") or {}
                assistant_files = ((turn_log.get("assistant") or {}).get("files") or [])
                file_lines = _format_assistant_files_lines(assistant_files, turn_id=str(turn_id), is_current=False)
                if file_lines:
                    lines.append("[FILES (HISTORICAL) - OUT_DIR-relative paths]")
                    lines.extend(file_lines)
                    lines.append("")
            except Exception:
                pass

            # Per-turn sources are not printed; SIDs always refer to current turn's global sources_pool.

    lines.append("")
    lines.append("---")
    lines.append("")
    # ---------- Current Turn (live) ----------
    lines.append("****")
    lines.append("## Current Turn (live — oldest → newest events) [CURRENT TURN]")
    lines.append("****")
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
        turn_memories = (getattr(context.scratchpad, "guess_ctx", None) or {}).get("turn_memories") or []
        ordered_turn_memories = list(reversed(turn_memories))
        mem_block = format_turn_memories_block(
            ordered_turn_memories,
            max_items=16,
            order_label="oldest→newest",
            scope_label="conversation",
            current_turn_id=turn_id,
        )
        if mem_block:
            lines.append("")
            lines.append(mem_block)
    except Exception:
        pass

    try:
        feedback_items = getattr(context.scratchpad, "feedback_conversation_level", None)
        feedback_block = format_feedback_block(
            list(reversed(feedback_items or [])),
            order_label="oldest→newest",
            scope_label="conversation",
        )
        if feedback_block:
            lines.append("")
            lines.append(feedback_block)
    except Exception:
        pass

    lines.append("")
    if coordinator_turn_line:
        lines.append("[SOLVER.COORDINATOR DECISION]")
        lines.append(coordinator_turn_line)
        lines.append("")
    lines.append("[SOLVER.TURN CONTRACT SLOTS (to fill)]")
    lines.append(json.dumps(_to_jsonable(output_contract or {}), ensure_ascii=False, indent=2),)
    lines.append("---")


    lines.append("[SOLVER.REACT.EVENTS (oldest → newest)]")
    if not context.events:
        lines.append("(no events yet)")
    else:
        seen_tool_errors: Dict[tuple, str] = {}
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

            if kind == "tool_signature_validation":
                tool_id = (e.get("tool_id") or "").strip()
                status = (e.get("status") or "").strip()
                issues = e.get("issues") or []
                pairs: list[str] = []
                if isinstance(issues, list):
                    for v in issues:
                        if not isinstance(v, dict):
                            continue
                        p = (v.get("param") or "").strip() or "?"
                        c = (v.get("code") or "").strip() or "issue"
                        if c == "type_mismatch":
                            exp = (v.get("expected") or "").strip() or "?"
                            got = (v.get("got") or "").strip() or "?"
                            pairs.append(f"{p}:{c}({got}->{exp})")
                        else:
                            pairs.append(f"{p}:{c}")
                pairs_s = ", ".join(pairs[:4])
                lines.append(
                    f"- {ts} — tool_signature_validation:"
                    + (f" status={status}" if status else "")
                    + (f" tool={tool_id}" if tool_id else "")
                    + (f" issues=[{pairs_s}]" if pairs_s else "")
                    + (f" (n={len(issues)})" if isinstance(issues, list) else "")
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
                notes = (e.get("notes") or "").strip()

                tc = e.get("tool_call") or {}
                tool_id = (tc.get("tool_id") or "").strip() if isinstance(tc, dict) else ""

                # declared artifacts for this tool call (protocol: list of dicts with "name")
                art_specs = tc.get("out_artifacts_spec") or []
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
                show_list = e.get("show_artifacts") or []
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

                tool_piece = None
                if tool_id:
                    # If this ever happens, it’s a protocol violation we WANT to see.
                    if not art_labels:
                        arts_s = "MISSING_ARTIFACTS"
                    tool_piece = f"tool={tool_id}->{arts_s}"

                show_piece = None
                if show_list:
                    show_paths = [str(p) for p in show_list if isinstance(p, (str, int, float))]
                    show_preview = ", ".join(show_paths[:2])
                    show_piece = f"show={len(show_paths)}" + (f"[{_shorten(show_preview, 120)}]" if show_preview else "")

                pieces = [
                    f"action={nxt}",
                    f"strategy={strat}" if strat else None,
                    f"focus={focus}" if focus else None,
                    f"notes={notes}" if notes else None,
                    tool_piece,
                    f"map={maps_s}" if maps_s else None,
                    show_piece,
                    f"fetch={fetch_n}" if fetch_n else None,
                ]
                pieces = [p for p in pieces if p]
                lines.append(f"- {ts} — decision: " + " — ".join(pieces))
                continue

            if kind == "show_skills":
                skills = e.get("skills") or []
                action = (e.get("action") or "").strip()
                skills_s = ", ".join([str(s) for s in skills]) if isinstance(skills, list) else str(skills)
                parts = []
                if action:
                    parts.append(f"action={action}")
                if skills_s:
                    parts.append(f"skills={skills_s}")
                lines.append(f"- {ts} — show_skills: " + " — ".join(parts))
                continue

            if kind == "show_artifacts":
                paths = e.get("paths") or []
                action = (e.get("action") or "").strip()
                paths_s = ", ".join([str(p) for p in paths]) if isinstance(paths, list) else str(paths)
                parts = []
                if action:
                    parts.append(f"action={action}")
                if paths_s:
                    parts.append(f"paths={paths_s}")
                lines.append(f"- {ts} — show_artifacts: " + " — ".join(parts))
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

            if kind == "tool_error":
                err = e.get("error")
                tool_id = e.get("tool_id")
                artifact_id = e.get("artifact_id")
                tool_call_id = e.get("tool_call_id")
                err_code = ""
                err_msg = ""
                if isinstance(err, dict):
                    err_code = err.get("code") or err.get("error") or "error"
                    if err_code == "missing_artifact":
                        err_msg = "see tool_finished for error details"
                    else:
                        raw_msg = format_tool_error_message(err.get("message") or err.get("description") or "")
                        err_msg = format_tool_error_for_journal(raw_msg)
                if tool_call_id and err_code and err_msg:
                    key = (str(tool_call_id), err_code, err_msg)
                    if key in seen_tool_errors and artifact_id:
                        err_msg = f"same error as {seen_tool_errors[key]}"
                    elif artifact_id:
                        seen_tool_errors[key] = str(artifact_id)
                head = f"- {ts} — artifact_error: tool={tool_id}"
                if artifact_id:
                    head += f" → {artifact_id}"
                if err_code:
                    head += f" — {err_code}"
                if err_msg:
                    lines.append(f"{head}: {err_msg}")
                else:
                    lines.append(head)
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
                    if not isinstance(e.get("error"), dict):
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
                    raw_msg = format_tool_error_message(err.get("message") or err.get("description") or "")
                    err_msg = format_tool_error_for_journal(raw_msg)
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
                if len(objective_preview) > 300:
                    objective_preview = objective_preview[:299] + "…"

            return queries_preview, objective_preview

        lines.append("[SOLVER.CURRENT ARTIFACTS (oldest→newest)]")
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
                        f"{error.get('code')}: {error.get('message', '')[:300]}"
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
                if isinstance(value_dict, dict):
                    write_error = (value_dict.get("write_error") or "").strip()
                    write_warning = (value_dict.get("write_warning") or "").strip()
                    size_bytes = value_dict.get("size_bytes")
                    if write_error or write_warning:
                        parts = []
                        if write_error:
                            parts.append(f"write_error={write_error}")
                        if write_warning:
                            parts.append(f"write_warning={write_warning}")
                        if size_bytes is not None:
                            parts.append(f"size={size_bytes}")
                        lines.append("    !! " + "; ".join(parts))
                art_stats = art.get("artifact_stats")
                if isinstance(art_stats, dict) and art_stats:
                    lines.append("    stats: " + json.dumps(art_stats, ensure_ascii=False))

                if tools_insights.is_search_tool(tid):
                    q_prev, obj_prev = _search_context_from_inputs(art)
                    if q_prev or obj_prev:
                        lines.append("    search_context:")
                        if q_prev:
                            lines.append(f"      queries  : {q_prev}")
                        if obj_prev:
                            lines.append(f"      objective: \"{obj_prev}\"")
                if tools_insights.is_search_tool(tid) or tid == "generic_tools.fetch_url_contents":
                    produced_sids: List[str] = []
                    if isinstance(value, list):
                        for row in value:
                            sid = row.get("sid") if isinstance(row, dict) else None
                            if isinstance(sid, int):
                                produced_sids.append(f"S{sid}")
                    elif isinstance(value, dict):
                        for row in value.values():
                            sid = row.get("sid") if isinstance(row, dict) else None
                            if isinstance(sid, int):
                                produced_sids.append(f"S{sid}")
                    if produced_sids:
                        lines.append("    produced_sids: " + ", ".join(produced_sids))

        lines.append("")

    try:
        current_files: List[Dict[str, Any]] = []
        try:
            seen: set[str] = set()
            for art_id, art in (context.artifacts or {}).items():
                if not isinstance(art, dict):
                    continue
                if (art.get("artifact_kind") or "").strip() != "file":
                    continue
                filename = (art.get("filename") or (art.get("value") or {}).get("filename") or art.get("path") or "").strip()
                if not filename or filename in seen:
                    continue
                current_files.append({
                    "filename": filename,
                    "artifact_name": str(art_id),
                    "mime": (art.get("mime") or "").strip(),
                    "size": art.get("size") or art.get("size_bytes"),
                })
                seen.add(filename)
        except Exception:
            pass

        file_lines = _format_assistant_files_lines(current_files, turn_id="current_turn", is_current=True)
        if file_lines:
            lines.append("[FILES (CURRENT) — OUT_DIR-relative paths]")
            lines.extend(file_lines)
            lines.append("")
    except Exception:
        pass

    # Web artifacts summary (search + fetch only)
    try:
        web_lines: List[str] = []
        for art_id, art in (context.artifacts or {}).items():
            if not isinstance(art, dict):
                continue
            tid = (art.get("tool_id") or "").strip()
            if not (tools_insights.is_search_tool(tid) or tid == "generic_tools.fetch_url_contents"):
                continue
            inputs = art.get("inputs") or {}
            queries_preview = ""
            if isinstance(inputs, dict):
                q_raw = inputs.get("queries")
                q_list: list[str] = []
                if isinstance(q_raw, list):
                    q_list = [str(q) for q in q_raw if isinstance(q, (str, int, float))]
                elif isinstance(q_raw, str):
                    try:
                        parsed = json.loads(q_raw)
                        if isinstance(parsed, list):
                            q_list = [str(q) for q in parsed if isinstance(q, (str, int, float))]
                        else:
                            q_list = [q_raw]
                    except Exception:
                        q_list = [q_raw]
                if q_list:
                    q2 = q_list[:2]
                    queries_preview = "; ".join([f"\"{q.strip()}\"" for q in q2 if str(q).strip()])
                    if len(q_list) > 2:
                        queries_preview += f"; ...+{len(q_list) - 2} more"
            obj_preview = ""
            obj = inputs.get("objective") if isinstance(inputs, dict) else None
            if isinstance(obj, str) and obj.strip():
                obj_preview = obj.strip()
                if len(obj_preview) > 300:
                    obj_preview = obj_preview[:299] + "..."
            produced_sids: List[str] = []
            value = art.get("value")
            if isinstance(value, list):
                for row in value:
                    sid = row.get("sid") if isinstance(row, dict) else None
                    if isinstance(sid, int):
                        produced_sids.append(f"S{sid}")
            elif isinstance(value, dict):
                for row in value.values():
                    sid = row.get("sid") if isinstance(row, dict) else None
                    if isinstance(sid, int):
                        produced_sids.append(f"S{sid}")
            web_lines.append(f"- {art_id} <- {tid}")
            if queries_preview:
                web_lines.append(f"    queries  : {queries_preview}")
            if obj_preview:
                web_lines.append(f"    objective: \"{obj_preview}\"")
            if produced_sids:
                web_lines.append("    produced_sids: " + ", ".join(produced_sids))
        if web_lines:
            lines.append("[EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS]")
            lines.extend(web_lines)
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
            current_turn_id = None
            try:
                current_turn_id = getattr(context.scratchpad, "turn_id", None) or context.turn_id
            except Exception:
                current_turn_id = None
            src_lines = _format_sources_lines(
                sources_pool,
                used_sids=current_used_sids,
                current_turn_id=current_turn_id,
            )
            if src_lines:
                lines.append(f"[TURN SOURCES POOL. ({len(sources_pool)} total)]")
                lines.extend(src_lines)
                lines.append("")
    except Exception:
        pass

    # 2) Live snapshot (current contract/slots/tool results)
    lines.append("[SOLVER.CURRENT TURN PROGRESS SNAPSHOT]")
    if model_label:
        lines.append(f"- Active model: {model_label}")
    declared = list((output_contract or {}).keys())
    filled = list((context.current_slots or {}).keys())
    filled_set = set(filled)
    pending = [s for s in declared if s not in filled_set]

    lines.append("# Contract Status")
    lines.append(f"- Declared slots: {len(declared)}")
    lines.append(f"- Filled slots  : {len(filled)}  ({', '.join(filled) if filled else '-'})")
    lines.append(f"- Pending slots : {len(pending)}  ({', '.join(pending) if pending else '-'})")
    try:
        artifacts_map = (context.artifacts or {}) if context else {}
        mapped_artifacts = set()
        if context and getattr(context, "current_slots", None):
            for slot in (context.current_slots or {}).values():
                if isinstance(slot, dict):
                    aid = (slot.get("mapped_artifact_id") or "").strip()
                    if aid:
                        mapped_artifacts.add(aid)
        mappable_artifacts = [
            aid for aid, art in artifacts_map.items()
            if not isinstance(art, dict)
            or (
                art.get("artifact_kind") != "search"
                and not art.get("error")
                and art.get("value") is not None
            )
        ]
        unmapped = [aid for aid in mappable_artifacts if aid not in mapped_artifacts]
        lines.append(f"- Unmapped artifacts: {len(unmapped)}  ({', '.join(unmapped) if unmapped else '-'})")
        if getattr(getattr(context, "budget_state", None), "wrapup_active", False):
            lines.append(f"- Wrap-up active: yes (pending slots: {', '.join(pending) if pending else '-'}; unmapped artifacts: {', '.join(unmapped) if unmapped else '-'})")
    except Exception:
        pass
    lines.append("")

    if context.current_slots:
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import format_live_slots

        slots_md = format_live_slots(
            slots=context.current_slots,
            contract=output_contract,
            grouping="flat",  # or "status" if you want grouping
            slot_attrs={"description", "gaps", "artifact_id", "filename", "sources_used"},
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
            lines.append("# Budget Snapshot")
            lines.append("")
            lines.append(format_budget_for_llm(context.budget_state))
    except Exception:
        pass

    lines.append("")

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

    def _norm_out_artifacts_spec(tc: Any) -> List[Dict[str, Any]]:
        if not isinstance(tc, dict):
            return []
        tras = tc.get("out_artifacts_spec")

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
        sts = []
        for g in group:
            raw = g.get("status", "")
            if raw is None:
                continue
            val = str(raw).strip()
            if not val:
                continue
            if val.lower() in {"none", "null"}:
                continue
            sts.append(val)
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

                tras = _norm_out_artifacts_spec(tc)
                tras_s = _fmt_res_artifacts(tras)

                call_reason = tc.get("reasoning") or dec.get("notes") or ""
                fetch_n = len(dec.get("fetch_context") or [])
                show_list = dec.get("show_artifacts") or []
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

                if show_list:
                    show_paths = [str(p) for p in show_list if isinstance(p, (str, int, float))]
                    show_preview = ", ".join(show_paths[:2])
                    segs.append(f"show:{len(show_paths)}" + (f" [{_short(show_preview, 120)}]" if show_preview else ""))

                if show_list:
                    segs.append("stage=show_artifacts")
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

            if t == "show_skills":
                details = (entry.get("details") or {})
                skills = details.get("skills") or []
                sk_txt = ", ".join(skills) if skills else "-"
                log_summary.append(f"[{it or '?'}] show_skills requested: {sk_txt}")
                i += 1
                continue

            if t == "show_artifacts":
                details = (entry.get("details") or {})
                paths = details.get("paths") or []
                p_txt = ", ".join(paths) if paths else "-"
                log_summary.append(f"[{it or '?'}] show_artifacts requested: {p_txt}")
                i += 1
                continue

            if t == "wrapup_activated":
                details = (entry.get("data") or {})
                pending = details.get("pending_slots") or []
                unmapped = details.get("unmapped_artifacts") or []
                p_txt = ", ".join(pending) if pending else "-"
                u_txt = ", ".join(unmapped) if unmapped else "-"
                log_summary.append(f"[{it or '?'}] WRAP-UP activated: pending_slots={p_txt}; unmapped_artifacts={u_txt}")
                i += 1
                continue

            if t == "tool_execution":
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
                        status = g.get("status") or "ok"
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
                             exclude_tool_ids: Optional[List[str]] = None,
                             show_artifacts: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Combine the turn session journal with a session log summary.
    The journal must remain the prefix to preserve cache behavior across agents.
    """
    playbook_text = (turn_session_journal or "").strip()
    log_summary = build_session_log_summary(session_log=session_log, slot_specs=slot_specs)
    log_block = "\n".join([
        "## Session Log (recent events, summary)",
        "\n".join(log_summary) if log_summary else "(empty)",
    ])
    full_context_block = render_full_context_artifacts_for_journal(show_artifacts)
    parts = []
    if playbook_text:
        parts.append(playbook_text)
    parts.append(log_block)
    if full_context_block:
        parts.append(full_context_block)
    return "\n\n".join(parts)


def build_instruction_catalog_block(
        *,
        consumer: str,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
        tool_catalog_json: Optional[str] = None,
        active_skills: Optional[List[str]] = None,
        include_skill_gallery: bool = True,
) -> str:
    tools_list: List[Dict[str, Any]] = []
    if tool_catalog:
        tools_list = list(tool_catalog)
    elif tool_catalog_json:
        try:
            parsed = json.loads(tool_catalog_json or "[]")
            if isinstance(parsed, list):
                tools_list = parsed
        except Exception:
            tools_list = []

    tool_block = ""
    if tools_list:
        tool_block = "\n".join([
            "[AVAILABLE COMMON TOOLS]",
            json.dumps(tools_list or [], ensure_ascii=False, indent=2),
        ])

    skill_block = ""
    if include_skill_gallery:
        skill_block = skills_gallery_text(
            consumer=consumer,
            tool_catalog=tools_list,
        )
    active_block = ""
    if active_skills:
        active_block = build_skills_instruction_block(active_skills, variant="full", header="ACTIVE SKILLS")

    parts = []
    if tool_block:
        parts.append(tool_block)
    if skill_block:
        parts.append(skill_block)
    if active_block:
        parts.append(active_block)
    return "\n\n".join(parts)


def build_tools_block_old(
        tool_catalog: Optional[List[Dict[str, Any]]],
        *,
        header: str,
) -> str:
    if not tool_catalog:
        return ""
    return "\n".join([
        header,
        json.dumps(tool_catalog or [], ensure_ascii=False, indent=2),
    ])

def build_tools_block(
        tool_catalog: Optional[List[Dict[str, Any]]],
        *,
        header: str,
) -> str:
    """Build a formatted, human-readable tools catalog (similar to skills gallery)."""
    if not tool_catalog:
        return ""

    lines: List[str] = [
        header,
        "Available tools extend agent capabilities with specific operations. "
        "Call tools using their full ID (e.g., generic_tools.web_search).",
        "",
        "═" * 79,
        "",
        ]

    for idx, tool in enumerate(tool_catalog, start=1):
        tid = tool.get("id", "unknown")
        purpose = tool.get("purpose", "")
        is_async = tool.get("is_async", False)
        args = tool.get("args", {})
        returns = tool.get("returns", "")
        examples = tool.get("examples", [])
        constraints = tool.get("constraints", [])

        # Tool header with emoji
        async_txt = " [async]" if is_async else ""
        lines.append(f"🔧 [{idx}] {tid}{async_txt}")
        lines.append("")

        # Purpose/description
        if purpose:
            lines.extend(_wrap_lines(purpose, indent="   "))
            lines.append("")

        # Arguments
        if args:
            lines.append("   📥 Parameters:")
            for arg_name, arg_info in args.items():
                # Parse type and description from arg_info string
                # Format is typically: "type, description (default=value)"
                if isinstance(arg_info, str):
                    parts = arg_info.split(", ", 1)
                    arg_type = parts[0] if parts else "any"
                    arg_desc = parts[1] if len(parts) > 1 else ""

                    # Extract default if present
                    default_match = re.search(r'\(default=(.*?)\)$', arg_desc)
                    default_txt = ""
                    if default_match:
                        default_val = default_match.group(1)
                        default_txt = f" [default: {default_val}]" if default_val else " [optional]"
                        arg_desc = arg_desc[:default_match.start()].strip()

                    lines.append(f"       • {arg_name}: {arg_type}{default_txt}")
                    if arg_desc:
                        lines.extend(_wrap_lines(arg_desc, indent="         "))
                else:
                    lines.append(f"       • {arg_name}: {arg_info}")
            lines.append("")

        # Returns
        if returns:
            lines.append("   📤 Returns:")
            lines.extend(_wrap_lines(returns, indent="       "))
            lines.append("")

        # Call signature (simplified)
        call_template = tool.get("call_template", "")
        if call_template:
            # Extract just the function signature without the full template
            match = re.match(r'([^(]+)\(', call_template)
            if match:
                sig = f"{match.group(1)}(...)"
                lines.append(f"   📞 Usage: {sig}")
                lines.append("")

        # Constraints
        if constraints:
            lines.append("   ⚠️  Constraints:")
            for constraint in constraints:
                lines.append(f"       • {constraint}")
            lines.append("")

        # Examples
        if examples:
            lines.append("   💡 Examples:")
            for ex_idx, example in enumerate(examples, start=1):
                if isinstance(example, dict):
                    desc = example.get("description", "")
                    code = example.get("code", "")
                    if desc:
                        lines.append(f"       {ex_idx}. {desc}")
                    if code:
                        lines.extend(_wrap_lines(code, indent="          "))
                else:
                    lines.extend(_wrap_lines(str(example), indent="       "))
            lines.append("")

        lines.append("━" * 77)
        lines.append("")

    return "\n".join([l for l in lines if l is not None])


def _wrap_lines(text: str, indent: str = "   ", width: int = 77) -> List[str]:
    """Wrap text to fit within width, applying indent to each line."""
    if not text:
        return []

    # Clean up whitespace
    text = " ".join(text.split())

    lines = []
    available_width = width - len(indent)

    while text:
        if len(text) <= available_width:
            lines.append(f"{indent}{text}")
            break

        # Find last space within available width
        break_point = text.rfind(" ", 0, available_width)
        if break_point == -1:
            # No space found, force break at width
            break_point = available_width

        lines.append(f"{indent}{text[:break_point]}")
        text = text[break_point:].lstrip()

    return lines

def build_active_skills_block(active_skills: Optional[List[str]]) -> str:
    if not active_skills:
        return ""
    return build_skills_instruction_block(active_skills, variant="full", header="ACTIVE SKILLS")


def _short_with_count(text: str, limit: int) -> str:
    """Truncate text and show how much was cut."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    remaining = len(text) - limit
    return f"{text[:limit]}... (...{remaining} more chars)"

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

            # objective short → into context lines via insights
            g_obj = (turn_summary.get("objective") or gate.get("current_objective_short") or "").strip()
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
    - turn_memories[].turn_id
    - last_turns_details[].turn_id
    - current_turn_details.turn_id

    Returns:
        Set of all unique turn IDs found in the package.
    """
    turn_ids = set()

    # From turn_memories
    for mem in (guess_package.get("turn_memories") or []):
        if tid := mem.get("turn_id"):
            turn_ids.add(tid)

    # From last_turns_details
    for turn in (guess_package.get("last_turns_details") or []):
        if tid := turn.get("turn_id"):
            turn_ids.add(tid)

    # From current_turn_details
    if current := guess_package.get("current_turn_details"):
        if tid := current.get("turn_id"):
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
        context_hit_queries: Optional[Dict[str, List[str]]] = None,
        delta_fps: Optional[List[Dict[str, Any]]] = None,
        scratchpad: Optional[Any] = None,
        current_turn_log: Optional[str] = None,
        current_turn_fp: Optional[TurnFingerprintV1] = None,
        current_turn_payload: Optional[Dict[str, Any]] = None,
        context_bundle: Optional[Any] = None,
        filter_turn_ids: Optional[List[str]] = None,
        selected_local_memories_turn_ids: Optional[List[str]] = None,
        feedback_items: Optional[List[Dict[str, Any]]] = None,
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
      - [CURRENT TURN]                           — ts, turn id, insights (from current_turn_fp), full current_turn_log
      - [PRIOR TURNS (newest→oldest) - COMPRESSED VIEWS]         — newest→oldest, using TurnView.to_compressed_search_view()
      - [TURNS CANDIDATES TABLE]                 — turn ids + objective hints
      - [USER FEEDBACK — CHRONOLOGICAL (newest→oldest; scope=conversation)]          — conversation-level feedback
      - [TURN MEMORIES — CHRONOLOGICAL (newest→oldest; scope=conversation)]          — local per-turn memories (for ctx reconciler selection)
    Notes:
      - delta_fps = recent local fingerprints window (newest→oldest), used as memory hints.
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
                "sources": ["recent"],
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
                item = dict(item or {})
                sources = set(item.get("sources") or [])
                sources.add("recent")
                item["sources"] = list(sources)
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
                    item = dict(item)
                    sources = set(item.get("sources") or [])
                    sources.add("context_hit")
                    item["sources"] = list(sources)
                    raw_items.append(item)
            except Exception:
                pass

    # Dedupe by turn_id; keep newest and merge sources
    by_tid: Dict[str, Dict[str, Any]] = {}
    for item in raw_items:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or f"id:{item.get('id') or ''}"
        ts = item.get("ts") or item.get("timestamp") or ""
        cur = by_tid.get(tid)
        if not cur or ts_key(ts) >= ts_key(cur.get("ts") or cur.get("timestamp") or ""):
            merged = dict(item)
            cur_sources = set((cur or {}).get("sources") or [])
            new_sources = set(item.get("sources") or [])
            merged["sources"] = list(cur_sources | new_sources)
            by_tid[tid] = merged
        else:
            merged = dict(cur)
            cur_sources = set((cur or {}).get("sources") or [])
            new_sources = set(item.get("sources") or [])
            merged["sources"] = list(cur_sources | new_sources)
            by_tid[tid] = merged

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
    memory_block = ""
    turn_memories_sorted: List[Dict[str, Any]] = []

    def _fp_from_saved_payload(saved_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tlog = saved_payload.get("turn_log") or {}
        state = tlog.get("state") or {}
        fp = state.get("fingerprint")
        return fp if isinstance(fp, dict) else None

    def _fp_one_liner(fp_doc: Dict[str, Any]) -> str:
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
            return render_fingerprint_one_liner(fp_obj) or ""
        except Exception:
            return ""

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

    # 2.b Active memory insights removed (long memories disabled)

    # 2.c Prior turns — COMPRESSED VIEWS using TurnView (newest→oldest)
    last_turns_details: List[dict] = []
    last_turns_mate: List[dict] = []

    items_sorted = sorted(raw_items,
                          key=lambda item: item.get("ts") or item.get("timestamp") or "",
                          reverse=True)

    text_blocks.append("[PRIOR TURNS (newest→oldest) - COMPRESSED VIEWS]")

    for item in items_sorted:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or ""
        ts_raw = item.get("ts") or item.get("timestamp") or ""
        sources = set(item.get("sources") or [])
        if context_hit_queries and tid in context_hit_queries:
            sources.add("context_hit")

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
                deliverables_detalization="summary",
            )

            # Add timestamp header
            block_lines = [f"[TURN turn_id={tid}]"]
            block_lines.extend([
                _fmt_ts_for_humans(ts_raw),
                compressed_view,
                ""
            ])
            text_blocks.append("\n".join(block_lines))

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

    # 2.d Turn memories (chronological; local memories only)
    fp_map: Dict[str, Dict[str, Any]] = {}
    for item in raw_items:
        tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or ""
        saved_payload = _payload_unwrap(item)
        fp = _fp_from_saved_payload(saved_payload)
        if fp and tid:
            fp_map[tid] = fp

    if delta_fps:
        sel = set(selected_local_memories_turn_ids or [])
        for fp_doc in (delta_fps or []):
            tid = (fp_doc or {}).get("turn_id")
            if not tid or tid in fp_map:
                continue
            if sel and tid not in sel:
                continue
            if isinstance(fp_doc, dict):
                fp_map[tid] = fp_doc

    if current_turn_fp and turn_id and turn_id not in fp_map:
        try:
            fp_map[turn_id] = current_turn_fp.to_json()
        except Exception:
            pass

    def _fp_ts(fp: Dict[str, Any]) -> float:
        ts = (fp.get("made_at") or fp.get("ts") or "").strip()
        if not ts:
            return float("-inf")
        try:
            s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
            return _dt.datetime.fromisoformat(s).timestamp()
        except Exception:
            return float("-inf")

    if filter_turn_ids is None and selected_local_memories_turn_ids is None:
        filtered_fp_map = fp_map
    else:
        target_ids = {t for t in (set(filter_turn_ids or []) | set(selected_local_memories_turn_ids or [])) if t}
        if turn_id:
            target_ids.add(turn_id)
        filtered_fp_map = {tid: fp for tid, fp in fp_map.items() if tid in target_ids}

    fps_sorted = sorted(filtered_fp_map.values(), key=_fp_ts, reverse=True)
    if fps_sorted:
        lines = ["[TURN MEMORIES — CHRONOLOGICAL (newest→oldest; scope=conversation)]"]
        turn_memories_sorted = []
        for fp_doc in fps_sorted:
            tid = (fp_doc.get("turn_id") or "").strip()
            ts = (fp_doc.get("made_at") or fp_doc.get("ts") or "").strip()
            one = _fp_one_liner(fp_doc)
            ts_disp = _fmt_ts_for_humans(ts)
            cur_label = " (current turn)" if tid and tid == turn_id else ""
            if one:
                lines.append(f"- {tid}{cur_label} ({ts_disp}) {one}")
            else:
                lines.append(f"- {tid}{cur_label} ({ts_disp})")
            turn_memories_sorted.append(fp_doc)
        memory_block = "\n".join(lines) + "\n"

    # 2.e Turn candidates table (oldest -> newest)
    if items_sorted:
        table_lines = ["[TURNS CANDIDATES TABLE]"]
        for item in sorted(items_sorted, key=lambda it: it.get("ts") or it.get("timestamp") or ""):
            tid = _turn_id_from_tags_safe(list(item.get("tags") or [])) or ""
            ts_raw = item.get("ts") or item.get("timestamp") or ""
            sources = set(item.get("sources") or [])
            reasons: list[str] = []
            if "recent" in sources:
                reasons.append("recent_turn")
            if "context_hit" in sources:
                queries = context_hit_queries.get(tid, []) if context_hit_queries else []
                if queries:
                    reasons.append("context_hit: " + " | ".join(queries))
                else:
                    reasons.append("context_hit")

            log_payload = (item.get("payload") or {}).get("payload") or {}
            _, turn_summary, gate = _extract_entries_and_summary(log_payload)
            obj = (turn_summary.get("objective") or gate.get("current_objective_short") or "").strip()
            reason_str = "; ".join(reasons) if reasons else "recent_turn"
            ts_str = _fmt_ts_for_humans(ts_raw)
            obj_str = f" | objective: {obj}" if obj else ""
            table_lines.append(f"- {tid} ({ts_str}) | {reason_str}{obj_str}")
        text_blocks.append("\n".join(table_lines) + "\n")

    # 2.f Feedback (bottom) + local memories
    feedback_block = format_feedback_block(
        feedback_items or [],
        order_label="newest→oldest",
        scope_label="conversation",
    )
    if feedback_block:
        text_blocks.append(feedback_block)
        text_blocks.append("")

    if memory_block:
        text_blocks.append(memory_block)
        text_blocks.append("")

    # ---------- 3) Assemble outputs ----------
    guess_ctx_str = ("\n".join(text_blocks)).strip()
    guess_package = {
        "items": (
                last_turns_details
                + [i for i in items_struct if i.get("kind") == "turn_current"]
        ),
        "last_turns_details": last_turns_details,
        "last_turns_mate": last_turns_mate,
        "current_turn_details": next((it for it in items_struct if it.get("kind") == "turn_current"), None),
        "turn_memories": turn_memories_sorted,
        "feedback_items": list(feedback_items or []),
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

    records: list[dict] = []
    for idx, tid in enumerate(materialize_ids):
        mat = None
        saved_payload = {}
        history_meta = history_by_tid.get(tid)
        if history_meta:
            saved_payload = history_meta.get("turn_log") or {}
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

        if not saved_payload:
            saved_payload = unwrap_payload((mat.get("turn_log") or {}).get("payload") or {}) or {}

        entries, turn_summary, gate = _extract_entries_and_summary(saved_payload)
        deliverables_map = _solver_deliverables(saved_payload)
        is_clar = _is_clarification_turn(saved_payload)

        compressed_turn = None
        if entries and turn_summary:
            try:
                compressed_turn = CompressedTurn.from_structured(entries, turn_summary)
                g_obj = (turn_summary.get("objective") or gate.get("current_objective_short") or "").strip()
                if g_obj:
                    compressed_turn.insights = (
                        compressed_turn.insights
                        + ("; " if compressed_turn.insights and g_obj else "")
                        + g_obj
                    ) if compressed_turn.insights or g_obj else g_obj
            except Exception:
                compressed_turn = None

        records.append({
            "idx": idx,
            "turn_id": tid,
            "mat": mat,
            "saved_payload": saved_payload,
            "history_meta": history_meta,
            "entries": entries,
            "turn_summary": turn_summary,
            "gate": gate,
            "deliverables_map": deliverables_map,
            "is_clar": is_clar,
            "compressed_turn": compressed_turn,
        })

    newest_prior_nonclar_idx = -1
    for rec in records:
        if not rec["is_clar"]:
            newest_prior_nonclar_idx = rec["idx"]

    for rec in records:
        idx = rec["idx"]
        tid = rec["turn_id"]
        mat = rec["mat"]
        saved_payload = rec["saved_payload"] or {}
        history_meta = rec["history_meta"]

        artifacts: list[dict] = []
        turn_presentation = ""

        entries = rec["entries"]
        turn_summary = rec["turn_summary"]
        gate = rec["gate"]
        deliverables_map = rec["deliverables_map"]
        is_clar = rec["is_clar"]
        compressed_turn = rec["compressed_turn"]

        # Turn presentation for final answer generator (derived from turn log)
        try:
            tv = turn_view_class.from_saved_payload(
                turn_id=tid,
                user_id=user,
                conversation_id=conversation_id,
                payload={"payload": saved_payload},
            )
            prez = tv.to_final_answer_presentation(assistant_answer_limit=trunc_assistant)
            if isinstance(prez, str) and prez.strip():
                turn_presentation = prez.strip()
        except Exception:
            turn_presentation = ""

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
            user_obj = (history_meta or {}).get("user") or {}
            asst_obj = (history_meta or {}).get("assistant") or {}
            user_ts = (history_meta or {}).get("ts")
            asst_ts = (history_meta or {}).get("ts")

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
            "turn_presentation": turn_presentation,
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

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/layout.py

import json
import datetime
import time
import urllib.parse
from typing import Dict, Any, List, Tuple, Optional

from langchain_core.messages import SystemMessage

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.infra.service_hub.inventory import create_cached_system_message

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import build_skills_instruction_block, skills_gallery_text
from kdcube_ai_app.apps.chat.sdk.util import _wrap_lines, _shorten, token_count
from kdcube_ai_app.tools.content_type import is_text_mime_type
import re



def build_user_input_blocks(
    *,
    runtime: RuntimeCtx,
    user_text: str,
    user_attachments: Optional[List[Dict[str, Any]]],
    block_factory,
) -> List[Dict[str, Any]]:
    tid = (getattr(runtime, "turn_id", None) or "").strip()
    if not tid:
        return []
    ts = (getattr(runtime, "started_at", "") or "").strip()
    blocks: List[Dict[str, Any]] = []
    user_text = (user_text or "").strip()
    if user_text:
        prompt_path = f"ar:{tid}.user.prompt"
        blocks.append(block_factory(
            type="user.prompt",
            author="user",
            turn_id=tid,
            ts=ts,
            path=prompt_path,
            text=user_text,
        ))
    for att in (user_attachments or []):
        if not isinstance(att, dict):
            continue
        name = (att.get("filename") or att.get("name") or "").strip() or "(attachment)"
        mime = (att.get("mime") or "").strip() or "application/octet-stream"
        summary = (att.get("summary") or "").strip()
        attachment_path = f"fi:{tid}.user.attachments/{name}" if name and name != "(attachment)" else ""
        meta = {k: att.get(k) for k in ("hosted_uri", "rn", "key", "local_path") if att.get(k)}
        if summary:
            meta["summary"] = summary
        if name and name != "(attachment)":
            meta["filename"] = name
        if mime:
            meta["mime"] = mime
        physical_path = ""
        if name and name != "(attachment)":
            physical_path = f"{tid}/attachments/{name}"
            meta["physical_path"] = physical_path
        blocks.append(block_factory(
            type="user.attachment.meta",
            author="user",
            turn_id=tid,
            ts=ts,
            path=attachment_path,
            meta=meta or None,
            text=None,
        ))
        if att.get("base64"):
            blocks.append(block_factory(
                type="user.attachment",
                author="user",
                turn_id=tid,
                ts=ts,
                mime=mime,
                base64=att.get("base64"),
                path=attachment_path,
                meta=meta or None,
            ))
        if is_text_mime_type(mime):
            text_val = (att.get("text") or "").strip()
            if text_val:
                blocks.append(block_factory(
                    type="user.attachment.text",
                    author="user",
                    turn_id=tid,
                    ts=ts,
                    path=attachment_path,
                    text=text_val,
                    meta=meta or None,
                ))
    return blocks


def build_assistant_completion_blocks(
    *,
    runtime: RuntimeCtx,
    answer_text: str,
    ended_at: Optional[str],
    block_factory,
) -> List[Dict[str, Any]]:
    tid = runtime.turn_id
    if not tid:
        return []
    ts = (ended_at or getattr(runtime, "started_at", "") or "").strip()
    asst_text = (answer_text or "").strip()
    if not asst_text:
        return []
    sources_used = citations_module.extract_citation_sids_any(asst_text)
    meta = {"sources_used": sources_used} if sources_used else None
    return [block_factory(
        type="assistant.completion",
        author="assistant",
        turn_id=tid,
        ts=ts,
        path=f"ar:{tid}.assistant.completion",
        text=asst_text,
        meta=meta,
    )]


def build_announce_text(
    *,
    iteration: int,
    max_iterations: int,
    started_at: Optional[str],
    timezone: Optional[str],
    timeline_blocks: List[Dict[str, Any]],
    constraints: Optional[List[str]] = None,
) -> str:
    def _fmt_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        if total < 60:
            return f"{total}s"
        mins, secs = divmod(total, 60)
        if mins < 60:
            return f"{mins}m{secs:02d}s"
        hrs, rem = divmod(mins, 60)
        return f"{hrs}h{rem:02d}m"

    def _mk_box(title: str, *, min_width: int = 35) -> List[str]:
        inner_width = max(min_width, len(title) + 2)
        top = "╔" + ("═" * inner_width) + "╗"
        mid = "║  " + title.ljust(inner_width - 2) + "║"
        bot = "╚" + ("═" * inner_width) + "╝"
        return [top, mid, bot]

    iter_total = int(max_iterations)
    iter_display = int(iteration) + 1
    if iter_total > 0:
        iter_display = max(1, min(iter_display, iter_total))
    remaining_iter = max(0, iter_total - iter_display) if iter_total > 0 else 0

    lines: List[str] = []
    lines.extend(_mk_box(f"ANNOUNCE — Iteration {iter_display}/{iter_total}"))
    lines.append("")

    bar_len = 10
    if iter_total > 0:
        used = max(0, min(iter_display, iter_total))
        filled = int(round((used / iter_total) * bar_len))
    else:
        filled = 0
    filled = max(0, min(filled, bar_len))
    bar = ("█" * filled) + ("░" * (bar_len - filled))
    lines.append("[BUDGET]")
    lines.append(f"  iterations  {bar}  {remaining_iter} remaining")
    if started_at:
        try:
            ts = datetime.datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
            elapsed = time.time() - ts
            lines.append(f"  time_elapsed_in_turn   {_fmt_elapsed(elapsed)}")
        except Exception:
            pass

    try:
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        tz = (timezone or "UTC").strip()
        lines.append("")
        lines.append("[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]")
        lines.append(f"  user_timezone: {tz}")
        lines.append(f"  current_utc_timestamp: {now}")
        lines.append(f"  current_utc_date: {today}")
        lines.append("  All relative dates MUST be interpreted against this context.")
    except Exception:
        pass

    lines.append("")
    lines.append("[ACTIVE PLAN]")
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import collect_plan_snapshots, PlanSnapshot
        plans_by_id, order = collect_plan_snapshots(timeline_blocks)
        if order:
            lines.append("  - plans:")
            snapshots: List[PlanSnapshot] = []
            for pid in order:
                snap = PlanSnapshot.from_any(plans_by_id.get(pid) or {})
                if snap:
                    snapshots.append(snap)
            for idx, snap in enumerate(snapshots, start=1):
                suffix = " (current)" if idx == len(snapshots) else ""
                last_ts = snap.last_ts or snap.created_ts
                header = f"    • plan #{idx}{suffix}"
                if last_ts:
                    header += f" last={last_ts}"
                lines.append(header)
                for step_idx, step in enumerate(snap.steps or [], start=1):
                    mark = snap.status_mark(step_idx)
                    lines.append(f"      {mark} [{step_idx}] {step}")
            current_snap = snapshots[-1] if snapshots else None
            if current_snap:
                summary = current_snap.plan_summary()
                lines.append(
                    f"  - plan_status: done={summary.get('done')} failed={summary.get('failed')} pending={summary.get('pending')}"
                )
                lines.append(f"  - plan_complete: {str(bool(summary.get('complete'))).lower()}")
                last_ts = current_snap.last_ts or current_snap.created_ts
                if last_ts:
                    lines.append(f"  - plan_last_update: {last_ts}")
        else:
            lines.append("  - plans: none")
    except Exception:
        lines.append("  - plans: none")

    lines.append("")
    lines.append("[CONSTRAINTS]")
    if constraints:
        for item in constraints:
            if not item:
                continue
            lines.append(f"  - {item}")

    return "\n".join(lines) + "\n"


def build_sources_pool_text(*, sources_pool: List[Dict[str, Any]]) -> str:
    pool = [s for s in (sources_pool or []) if isinstance(s, dict)]
    pool.sort(key=lambda s: int(s.get("sid") or 0))
    total = len(pool)

    header = f" SOURCES POOL  ({total} sources)   use react.read to load full text if not on a timeline"
    width = max(80, len(header))
    hr = "━" * width

    title_w = 36
    domain_w = 16
    sid_pad = max(2, len(str(max([int(s.get('sid') or 0) for s in pool] or [0]))))

    def _domain_from_url(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc:
                return parsed.netloc
            if "://" not in url and "/" in url:
                return url.split("/")[0]
        except Exception:
            pass
        return url

    def _fmt_tokens(n: int) -> str:
        if n <= 0:
            return "~0 tok"
        if n < 1000:
            return f"~{n} tok"
        return f"~{n/1000:.1f}K tok"

    def _snippet_from(src: Dict[str, Any]) -> str:
        for key in ("text", "snippet", "summary", "preview", "content"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return " ".join(val.strip().split())
        return ""

    lines: List[str] = [hr, header.ljust(width), hr, ""]

    if not pool:
        lines.append("  (none)")
        lines.append("")
    else:
        show_all = len(pool) <= 5
        if show_all:
            display = pool
            tail = []
        else:
            display = pool[:3]
            tail = pool[-1:]

        def _emit(src: Dict[str, Any]) -> None:
            sid = int(src.get("sid") or 0)
            sid_label = str(sid).zfill(sid_pad)
            url = (src.get("url") or src.get("local_path") or "").strip()
            title = (src.get("title") or src.get("name") or url or "(untitled)").strip()
            if title and not (title.startswith("\"") and title.endswith("\"")):
                title = f"\"{title}\""
            title = _shorten(title, title_w)
            domain = (src.get("domain") or "").strip()
            if not domain:
                domain = _domain_from_url(url)
            domain = _shorten(domain or "-", domain_w)
            text_val = _snippet_from(src)
            tok_count = token_count(text_val) if text_val else 0
            tok_label = _fmt_tokens(tok_count)
            lines.append(f"SID:{sid_label}  {title:<{title_w}}  {domain:<{domain_w}}  {tok_label}")
            snippet = _shorten(text_val, 200) if text_val else "<text>"
            lines.append(f"        {snippet}")
            lines.append("")

        for row in display:
            _emit(row)
        if not show_all:
            omitted = max(0, len(pool) - (len(display) + len(tail)))
            lines.append(f"        [ ... {omitted} more ... ]")
            lines.append("")
            for row in tail:
                _emit(row)

    lines.append(hr)
    lines.append("  Hint: to see the full snippet if not visible / hide if no need and big (example)")
    lines.append("  Load:  react.read([\"so:sources_pool[1,3,5]\"])")
    lines.append("  Hide:  react.hide([\"so:sources_pool[1]\"])")
    lines.append(hr)
    return "\n".join(lines) + "\n"

def build_gate_stage_block(*, runtime: RuntimeCtx, gate_out: Any, clarification_questions: Optional[List[str]] = None) -> Dict[str, Any]:
    lines = ["[STAGE: GATE OUTPUT]"]
    route = getattr(gate_out, "route", None) or (gate_out.get("route") if isinstance(gate_out, dict) else "")
    if route:
        lines.append(f"route: {route}")
    conversation_title = getattr(gate_out, "conversation_title", None)
    if conversation_title:
        lines.append(f"conversation_title: {conversation_title}")
    extracted_answer = getattr(gate_out, "extracted_answer", None)
    if conversation_title:
        lines.append(f"extracted_answer: {extracted_answer}")
    needs_clarification = getattr(gate_out, "needs_clarification", None)
    if isinstance(gate_out, dict) and needs_clarification is None:
        needs_clarification = gate_out.get("needs_clarification")
    if needs_clarification is not None:
        lines.append(f"needs_clarification: {bool(needs_clarification)}")
    qs = []
    if isinstance(gate_out, dict):
        qs = [q for q in (gate_out.get("clarification_questions") or []) if isinstance(q, str)]
    if not qs:
        qs = [q for q in (clarification_questions or []) if isinstance(q, str)]
    if qs:
        lines.append("clarification_questions: " + " | ".join(qs))
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.gate",
        "author": "gate",
        "turn_id": turn_id,
        "ts": getattr(runtime, "started_at", "") or "",
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.gate" if turn_id else "",
    }


def build_feedback_stage_block(*, runtime: RuntimeCtx, reaction: Dict[str, Any]) -> Dict[str, Any]:
    lines = ["[STAGE: FEEDBACK]"]
    origin = (reaction.get("origin") or "").strip()
    if origin:
        lines.append(f"origin: {origin}")
    if reaction.get("reaction") is not None:
        lines.append(f"reaction: {reaction.get('reaction')}")
    if reaction.get("text"):
        lines.append(f"text: {reaction.get('text')}")
    if reaction.get("confidence") is not None:
        lines.append(f"confidence: {reaction.get('confidence')}")
    if reaction.get("from_turn_id"):
        lines.append(f"from_turn_id: {reaction.get('from_turn_id')}")
    ts = reaction.get("ts") or getattr(runtime, "started_at", "") or ""
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.feedback",
        "author": "user",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.feedback" if turn_id else "",
    }


def build_clarification_stage_block(*, runtime: RuntimeCtx, ticket: Any = None, clarification_questions: Optional[List[str]] = None) -> Dict[str, Any]:
    lines = ["[STAGE: CLARIFICATION]"]
    qs = [q for q in (clarification_questions or []) if isinstance(q, str)]
    if qs:
        lines.append("questions: " + " | ".join(qs))
    if ticket is not None:
        try:
            status = getattr(ticket, "status", None) or ticket.get("status")
            title = getattr(ticket, "title", None) or ticket.get("title")
            if status:
                lines.append(f"ticket_status: {status}")
            if title:
                lines.append(f"ticket_title: {title}")
        except Exception:
            pass
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    return {
        "type": "stage.clarification",
        "author": "system",
        "turn_id": turn_id,
        "ts": getattr(runtime, "started_at", "") or "",
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.clarification" if turn_id else "",
        "meta": {"questions": qs} if qs else None,
    }


def build_clarification_resolution_block(*, runtime_ctx: RuntimeCtx, ticket: Any = None, resolved_with_answer: bool | None = None) -> Dict[str, Any]:
    lines = ["[STAGE: CLARIFICATION RESOLVED]"]
    if ticket is not None:
        tid = getattr(ticket, "ticket_id", None) or (ticket.get("ticket_id") if isinstance(ticket, dict) else "")
        if tid:
            lines.append(f"ticket_id: {tid}")
        title = getattr(ticket, "title", None) or (ticket.get("title") if isinstance(ticket, dict) else "")
        if title:
            lines.append(f"title: {title}")
        status = getattr(ticket, "status", None) or (ticket.get("status") if isinstance(ticket, dict) else "")
        if status:
            lines.append(f"status: {status}")
    if resolved_with_answer is not None:
        lines.append(f"resolved_with_answer: {bool(resolved_with_answer)}")
    turn_id = runtime_ctx.turn_id
    return {
        "type": "stage.clarification.resolved",
        "author": "system",
        "turn_id": turn_id,
        "ts": runtime_ctx.started_at,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.clarification.resolved",
    }


def build_suggested_followups_block(
    *,
    runtime: RuntimeCtx,
        suggested_followups: Optional[List[str]] = None,
) -> Dict[str, Any]:
    items = [s for s in (suggested_followups or []) if isinstance(s, str) and s.strip()]
    lines = ["[STAGE: SUGGESTED FOLLOW-UPS]"]
    if items:
        lines.append("items: " + " | ".join(items))
    turn_id = (getattr(runtime, "turn_id", None) or "").strip()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "type": "stage.suggested_followups",
        "author": "system",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "text": "\n".join(lines),
        "path": f"ar:{turn_id}.stage.suggested_followups" if turn_id else "",
        "meta": {"items": items} if items else None,
    }


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
            "call_template": a.get("call_template"),
            "purpose": doc.get("purpose", ""),
            "is_async": bool(a.get("is_async")),
            "args": doc.get("args", {}),
            "returns": doc.get("returns", ""),
        }
        if "constraints" in doc:
            item["constraints"] = doc["constraints"]
        if "examples" in doc:
            item["examples"] = doc["examples"]
        tool_catalog.append(item)
    return tool_catalog


def build_tools_block(
        tool_catalog: Optional[List[Dict[str, Any]]],
        *,
        header: str,
) -> str:
    if not tool_catalog:
        return ""

    lines: List[str] = [
        header,
        "Available tools extend agent capabilities with specific operations. "
        "Call tools using their full ID (e.g., web_tools.web_search).",
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

        async_txt = " [async]" if is_async else ""
        lines.append(f"🔧 [{idx}] {tid}{async_txt}")
        lines.append("")

        if purpose:
            lines.extend(_wrap_lines(purpose, indent="   "))
            lines.append("")

        if args:
            lines.append("   📥 Parameters:")
            for arg_name, arg_info in args.items():
                if isinstance(arg_info, str):
                    parts = arg_info.split(", ", 1)
                    arg_type = parts[0] if parts else "any"
                    arg_desc = parts[1] if len(parts) > 1 else ""
                    default_match = re.search(r"\(default=(.*?)\)$", arg_desc)
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

        if returns:
            lines.append("   📤 Returns:")
            lines.extend(_wrap_lines(returns, indent="       "))
            lines.append("")

        call_template = tool.get("call_template", "")
        if call_template:
            match = re.match(r"([^(]+)\(", call_template)
            if match:
                sig = f"{match.group(1)}(...)"
                lines.append(f"   📞 Usage: {sig}")
                lines.append("")

        if constraints:
            lines.append("   ⚠️  Constraints:")
            for constraint in constraints:
                lines.append(f"       • {constraint}")
            lines.append("")

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


def format_tool_signature(
        tool_id: str,
        params: Dict[str, Any],
        fetch_directives: List[Dict[str, Any]],
        adapters: List[Dict[str, Any]],
        *,
        trim: Optional[int] = None,
) -> str:
    """
    Build call signature like:
      web_tools.web_search(queries=["..."], objective=<turn_42.artifacts.digest_md.text>, n=10)
    Paths injected via fetch_context appear as <path>; multiple paths use " | ".
    Param ordering follows adapter.call_template when available.
    """
    order: List[str] = []
    template = next((a.get("call_template") for a in adapters if a.get("id") == tool_id), "")
    if "(" in template and ")" in template:
        inner = template.split("(", 1)[1].rsplit(")", 1)[0]
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        for p in parts:
            name = p.split("=", 1)[0].strip()
            order.append(name)

    fetch_map: Dict[str, List[str]] = {}
    for fd in (fetch_directives or []):
        pn = (fd or {}).get("param_name")
        path = (fd or {}).get("path")
        if pn and path:
            fetch_map.setdefault(pn, []).append(path)

    keys = list(dict.fromkeys(order + list(params.keys())))
    segs = []
    for k in keys:
        v_inline = params.get(k, None)
        paths = fetch_map.get(k, [])
        if paths:
            placeholder = " | ".join([f"<{p}>" for p in paths])
            if v_inline is None or v_inline == "" or (isinstance(v_inline, (list, dict)) and not v_inline):
                segs.append(f"{k}={placeholder}")
            else:
                vv = _shorten(v_inline, trim) if isinstance(trim, int) else json.dumps(v_inline, ensure_ascii=False)
                segs.append(f"{k}={vv} + {placeholder}")
        else:
            if isinstance(trim, int):
                segs.append(f"{k}={_shorten(v_inline, trim)}")
            else:
                segs.append(f"{k}={json.dumps(v_inline, ensure_ascii=False)}")
    return f"{tool_id}({', '.join(segs)})"


def build_instruction_catalog_block(
        *,
        consumer: str,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
        tool_catalog_json: Optional[str] = None,
        react_tools: Optional[List[Dict[str, Any]]] = None,
        include_skill_gallery: bool = True,
) -> str:
    from kdcube_ai_app.apps.chat.sdk.tools import tools_insights
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

    # Normalize entries that come from tool_catalog_for_prompt() (doc nested).
    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        doc = tool.get("doc")
        if not isinstance(doc, dict):
            continue
        if "purpose" not in tool and doc.get("purpose") is not None:
            tool["purpose"] = doc.get("purpose")
        if "args" not in tool and doc.get("args") is not None:
            tool["args"] = doc.get("args")
        if "returns" not in tool and doc.get("returns") is not None:
            tool["returns"] = doc.get("returns")
        if "constraints" not in tool and doc.get("constraints") is not None:
            tool["constraints"] = doc.get("constraints")
        if "examples" not in tool and doc.get("examples") is not None:
            tool["examples"] = doc.get("examples")

    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        tid = tool.get("id")
        if tid != "exec_tools.execute_code_python":
            continue
        args = tool.get("args")
        if isinstance(args, dict) and "code" in args:
            args = dict(args)
            args.pop("code", None)
            tool["args"] = args
        purpose = tool.get("purpose") or ""
        if "channel:code" not in purpose:
            note = (
                "Code is provided via <channel:code> when using this tool from React decision; "
                "omit params.code in JSON."
            )
            tool["purpose"] = f"{purpose}\n{note}".strip()

    react_tools = react_tools or []
    ids = {t.get("id") for t in tools_list if isinstance(t, dict)}
    for it in react_tools:
        if it.get("id") not in ids:
            tools_list.append(it)

    tool_block = ""
    if tools_list:
        react_ids: set[str] = set()
        for t in react_tools:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid:
                react_ids.add(tid)
        for t in tools_list:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid.startswith("react."):
                react_ids.add(tid)

        exec_ids: set[str] = set()
        exec_ids.update(tools_insights.PY_EXEC_ONLY_TOOL_IDS)
        for t in tools_list:
            tid = (t or {}).get("id")
            if not isinstance(tid, str) or not tid:
                continue
            if tid.startswith("web_tools."):
                react_ids.add(tid)
                continue
            if tools_insights.is_exec_tool(tid):
                react_ids.add(tid)
                continue
            # rendering_tools.write_* remain in common tools

        react_only = [t for t in tools_list if t.get("id") in react_ids]
        exec_only = [t for t in tools_list if t.get("id") in exec_ids and t.get("id") not in react_ids]
        common = [
            t for t in tools_list
            if t.get("id") not in react_ids and t.get("id") not in exec_ids
        ]

        parts_tools: List[str] = []
        react_block = build_tools_block(react_only, header="[AVAILABLE REACT TOOLS]")
        if react_block:
            parts_tools.append(react_block)
        common_block = build_tools_block(common, header="[AVAILABLE COMMON TOOLS]")
        if common_block:
            parts_tools.append(common_block)
        exec_block = build_tools_block(exec_only, header="[TOOLS AVAILABLE ONLY IN CODE SNIPPET]")
        if exec_block:
            parts_tools.append(exec_block)
        tool_block = "\n\n".join([p for p in parts_tools if p.strip()])

    skill_block = ""
    if include_skill_gallery:
        skill_block = skills_gallery_text(
            consumer=consumer,
            tool_catalog=tools_list,
        )
    active_block = ""

    parts = []
    if tool_block:
        parts.append(tool_block)
    if skill_block:
        parts.append(skill_block)
    if active_block:
        parts.append(active_block)
    return "\n\n".join(parts)

def build_embedding_presentation(blocks: List[Dict[str, Any]]) -> str:
    """
    Build a compact presentation for semantic indexing.
    Only include external artifacts (file/display) from react tool results.
    """
    lines: List[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "react.tool.result":
            continue
        if (b.get("mime") or "").strip() != "application/json":
            continue
        text = b.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            meta = json.loads(text)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        visibility = (meta.get("visibility") or "").strip()
        kind = (meta.get("kind") or "").strip()
        if visibility != "external" or kind not in {"file", "display"}:
            continue
        artifact_path = (meta.get("artifact_path") or "").strip()
        physical_path = (meta.get("physical_path") or "").strip()
        mime = (meta.get("mime") or "").strip()
        tool_id = (meta.get("tool_id") or "").strip()
        tool_call_id = (meta.get("tool_call_id") or "").strip()
        parts = []
        if artifact_path:
            parts.append(f"artifact_path={artifact_path}")
        if physical_path:
            parts.append(f"physical_path={physical_path}")
        if mime:
            parts.append(f"mime={mime}")
        if kind:
            parts.append(f"kind={kind}")
        if tool_id:
            parts.append(f"tool_id={tool_id}")
        if tool_call_id:
            parts.append(f"tool_call_id={tool_call_id}")
        if parts:
            lines.append("- " + " | ".join(parts))
    return "\n".join(lines).strip()

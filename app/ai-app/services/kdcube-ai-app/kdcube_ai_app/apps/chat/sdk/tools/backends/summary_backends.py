# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/backends/summary_backends.py

# sdk/codegen/summary/summary.py

from typing import Any, Dict, Optional, List, Union, Tuple
from datetime import datetime
import json, logging
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.sdk.tools.summary.contracts import ToolCallSummaryJSON
from kdcube_ai_app.apps.chat.sdk.util import _now_str, _today_str
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import create_cached_system_message, create_cached_human_message
import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers

def _attachment_summary_system_prompt() -> str:
    return (
        "You are summarizing a USER-PROVIDED ATTACHMENT.\n"
        "Goal: produce a compact, telegraphic, embedding-friendly inventory of the attachment content.\n"
        "Use any provided context (user prompt and other attachments) to resolve references, but do NOT assume.\n"
        "\n"
        "Output a TELEGRAPHIC, SECTIONED TEXT (NO JSON). Use pipes to separate sections.\n"
        "Format:\n"
        "semantic:<...> | structural:<...> | inventory:<...> | anomalies:<...> | safety:<...> | lookup_keys:<...> | filename:<...> | artifact_name:<...>\n"
        "\n"
        "Fields:\n"
        "- semantic: what the attachment is about; intent; domains; scope; key facts/samples/schema.\n"
        "- structural: file type, visible structure (tables/code/JSON/YAML/XML/diagrams), counts if visible.\n"
        "- inventory: notable fragments or sections to help retrieval.\n"
        "- anomalies: problems in the content (malformed, ambiguous, missing fields, garbled).\n"
        "- safety: benign/suspicious (+short reason if suspicious).\n"
        "- lookup_keys: 5-12 compact key phrases for retrieval.\n"
        "- filename: a short, unique, filesystem-safe name for this attachment (no spaces).\n"
        "- artifact_name: short, human-readable ID to use in paths (no spaces, unique enough).\n"
        "\n"
        "Rules:\n"
        "- Keep it short; telegraphic; no prose.\n"
        "- Mention attachment filename and mime.\n"
        "- If content is empty/unreadable, say so in structural/anomalies.\n"
    )

def _attachment_summary_prompt(modality_kind: Optional[str]) -> str:
    return _attachment_summary_system_prompt() + _modality_system_instructions(modality_kind)

log = logging.getLogger(__name__)

_SUMMARY_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_SUMMARY_DOC_MIME = {"application/pdf"}
_SUMMARY_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
_SUMMARY_MAX_DOC_BYTES = 10 * 1024 * 1024   # 10 MB

def _artifact_block_for_summary(artifact: Optional[Dict[str, Any]]) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    if not isinstance(artifact, dict):
        return None, None, None

    art_type = (artifact.get("type") or "").strip().lower()
    mime = (artifact.get("mime") or "").strip().lower()
    text = artifact.get("text") or ""
    base64_data = artifact.get("base64")
    size_bytes = artifact.get("size_bytes")
    filename = artifact.get("filename")
    read_error = artifact.get("read_error")

    block = None
    modality_kind = None
    if base64_data and mime in _SUMMARY_IMAGE_MIME:
        modality_kind = "image"
        block = {"type": "image", "data": base64_data, "media_type": mime}
    elif base64_data and mime in _SUMMARY_DOC_MIME:
        modality_kind = "document"
        block = {"type": "document", "data": base64_data, "media_type": mime}
    elif text:
        modality_kind = "text"
        block = {"type": "text", "text": text}

    meta_lines = [
        "### Attached artifact (for validation)",
        f"- type: {art_type or 'unknown'}",
        f"- mime: {mime or 'unknown'}",
        f"- filename: {filename or 'unknown'}",
        f"- size_bytes: {size_bytes if isinstance(size_bytes, int) else 'unknown'}",
        f"- base64_attached: {'yes' if block else 'no'}",
        f"- text_surrogate_len: {len(text)}",
    ]
    if read_error:
        meta_lines.append(f"- read_error: {read_error}")
    if art_type == "file" and not block and mime and mime not in _SUMMARY_IMAGE_MIME and mime not in _SUMMARY_DOC_MIME:
        meta_lines.append("- note: mime not supported for vision; using text surrogate only")

    return block, "\n".join(meta_lines), modality_kind


async def summarize_user_attachment(
    *,
    svc: Any,
    attachment: Dict[str, Any],
    user_prompt: str = "",
    other_attachments: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 600,
    max_attachment_chars: int = 12000,
    max_peer_chars: int = 2000,
) -> Optional[str]:
    if svc is None or not isinstance(attachment, dict):
        return None

    filename = (attachment.get("filename") or "attachment").strip()
    mime = (attachment.get("mime") or attachment.get("mime_type") or "application/octet-stream").strip()
    size = attachment.get("size") or attachment.get("size_bytes") or ""
    text = attachment.get("text") or ""
    read_error = attachment.get("read_error")
    if max_attachment_chars and len(text) > max_attachment_chars:
        text = text[:max_attachment_chars] + "\n...[truncated]"

    summary_artifact = {
        "type": "file",
        "mime": mime,
        "text": text,
        "base64": attachment.get("base64"),
        "filename": filename,
        "size_bytes": attachment.get("size_bytes") or size,
        "read_error": read_error,
    }
    artifact_block, artifact_meta, modality_kind = _artifact_block_for_summary(summary_artifact)

    meta_line = f"filename={filename}; mime={mime}"
    if size != "":
        meta_line += f"; size={size}"

    blocks = [f"ATTACHMENT_META:\n{meta_line}"]
    if artifact_meta:
        blocks.append(artifact_meta)
    if text:
        blocks.append(f"ATTACHMENT_TEXT:\n{text}")
    else:
        blocks.append("ATTACHMENT_TEXT:\n<empty_or_unavailable>")
    if read_error:
        blocks.append(f"READ_ERROR:\n{read_error}")

    if user_prompt:
        blocks.append(f"USER_PROMPT:\n{user_prompt}")

    peers = [p for p in (other_attachments or []) if isinstance(p, dict) and p is not attachment]
    if peers:
        peer_lines: List[str] = []
        for p in peers:
            pname = (p.get("filename") or "attachment").strip()
            pmime = (p.get("mime") or p.get("mime_type") or "application/octet-stream").strip()
            psummary = (p.get("summary") or "").strip()
            ptext = (p.get("text") or "")
            if not psummary and ptext:
                if max_peer_chars and len(ptext) > max_peer_chars:
                    ptext = ptext[:max_peer_chars] + "\n...[truncated]"
                psummary = ptext
            if psummary:
                peer_lines.append(f"{pname} ({pmime}): {psummary}")
        if peer_lines:
            blocks.append("OTHER_ATTACHMENTS:\n" + "\n".join(peer_lines))

    system_prompt = _attachment_summary_prompt(modality_kind)
    user_msg = "\n\n".join(blocks).strip()

    from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json

    message_blocks: List[dict] = []
    if artifact_block:
        message_blocks.append({**artifact_block, "cache": True})
    message_blocks.append({"type": "text", "text": user_msg})

    role = "attachment.summary"
    result = await stream_agent_to_json(
        svc,
        client_name="attachment.summary",
        client_role="attachment.summary",
        sys_prompt=create_cached_system_message(system_prompt, cache_last=True),
        messages=[create_cached_human_message(message_blocks)],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    logging_helpers.log_agent_packet(role, "summary", result)
    summary = (result.get("agent_response") or "").strip()
    if not summary:
        return None
    if size != "":
        summary = f"{summary} | size:{size}"
    return summary

def _modality_system_instructions(modality_kind: Optional[str]) -> str:
    if modality_kind == "image":
        return (
            "\nMULTIMODAL INPUT (image attached):\n"
            "- You MUST verify the image visually; do not trust text surrogates alone.\n"
            "- Call out obvious render failures: blank/empty, corrupted, truncated, low-res, or missing expected elements.\n"
            "- If the image contradicts the expectations from tool output or seems wrong, mark completeness/adequacy as partial/poor.\n"
        )
    if modality_kind == "document":
        return (
            "\nMULTIMODAL INPUT (PDF attached):\n"
            "- You MUST verify the PDF visually; do not trust text surrogates alone.\n"
            "- Call out render failures or unreadable pages; treat empty/garbled pages as partial/failed output.\n"
            "- If the document contradicts the expectations from tool output or seems wrong, mark completeness/adequacy as partial/poor.\n"
        )
    return ""

def _render_param_bindings_for_summary(
        base_params: Dict[str, Any],
        fetch_ctx: List[Dict[str, Any]],
        final_params: Dict[str, Any],
) -> str:
    """
    Build a human-readable description of how each param was populated:
      - which values came inline from the decision node
      - which values came from bound context artifacts (paths only)
      - which params exist only in final_params (e.g. sources_list)
    This is fed to the LLM summarizer so it understands invocation context
    without seeing large bound texts.
    """
    per_param: Dict[str, Dict[str, Any]] = {}

    def short_inline(v: Any) -> str:
        if isinstance(v, str):
            s = v.strip()
            if len(s) > 80:
                s = s[:77] + "..."
            return f"\"{s}\""
        if isinstance(v, (int, float, bool)) or v is None:
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, dict)):
            return f"<{type(v).__name__} len={len(v)}>"
        return str(v)

    # Inline bits from decision node
    for name, v in (base_params or {}).items():
        meta = per_param.setdefault(name, {"inline": None, "bound_paths": [], "final_only": False})
        meta["inline"] = short_inline(v)

    # Bound artifacts (paths only)
    for fd in (fetch_ctx or []):
        if not isinstance(fd, dict):
            continue
        name = (fd.get("param_name") or "").strip()
        path = (fd.get("path") or "").strip()
        if not (name and path):
            continue
        meta = per_param.setdefault(name, {"inline": None, "bound_paths": [], "final_only": False})
        meta.setdefault("bound_paths", []).append(path)

    # Params that only appear after binding (e.g. sources_list)
    for name, v in (final_params or {}).items():
        if name in per_param:
            continue
        meta = per_param.setdefault(name, {"inline": None, "bound_paths": [], "final_only": True})
        meta["final_repr"] = short_inline(v)

    # Render to compact markdown-ish text
    lines: list[str] = []
    if not per_param:
        return "(no params)"

    lines.append("Param bindings (decision-inline vs context-bound):")
    for name, meta in per_param.items():
        parts: list[str] = []
        if meta.get("inline") is not None:
            parts.append(f"inline={meta['inline']}")
        bpaths = meta.get("bound_paths") or []
        if bpaths:
            joined = " | ".join(f"<{p}>" for p in bpaths)
            parts.append(f"bound_from={joined}")
        if meta.get("final_only"):
            parts.append(f"final_only={meta.get('final_repr', '<derived>')}")
        if not parts:
            parts.append("no value (empty)")
        lines.append(f"- {name}: " + "; ".join(parts))

    return "\n".join(lines)

def _build_structural_summary(obj: Any, max_depth: int = 2, current_depth: int = 0) -> str:
    """
    Build a structural summary showing types and nested structure.
    Max 2 levels deep to keep it readable.

    Examples:
      {"name": "Alice", "age": 30} → "dict(name: str, age: int)"
      {"data": {"items": [1,2,3]}} → "dict(data: dict(items: list[3]))"
    """
    if current_depth >= max_depth:
        if isinstance(obj, dict):
            return f"dict[{len(obj)}]"
        elif isinstance(obj, list):
            return f"list[{len(obj)}]"
        else:
            return type(obj).__name__

    if isinstance(obj, dict):
        if not obj:
            return "dict(empty)"
        # Show first 5 keys with their type structure
        items = []
        for k, v in list(obj.items())[:5]:
            k_str = str(k) if len(str(k)) <= 30 else str(k)[:27] + "..."
            v_summary = _build_structural_summary(v, max_depth, current_depth + 1)
            items.append(f"{k_str}: {v_summary}")
        suffix = f", ...+{len(obj)-5}" if len(obj) > 5 else ""
        return f"dict({', '.join(items)}{suffix})"

    elif isinstance(obj, list):
        if not obj:
            return "list(empty)"
        if len(obj) <= 3:
            # Show all items
            items = [_build_structural_summary(item, max_depth, current_depth + 1) for item in obj]
            return f"list[{len(obj)}]({', '.join(items)})"
        else:
            # Show first item type + count
            first_type = _build_structural_summary(obj[0], max_depth, current_depth + 1)
            return f"list[{len(obj)}]({first_type}, ...)"

    elif isinstance(obj, str):
        length = len(obj)
        if length <= 50:
            return f"str({length}ch): \"{obj}\""
        else:
            return f"str({length}ch): \"{obj[:47]}...\""

    elif isinstance(obj, (int, float, bool, type(None))):
        return f"{type(obj).__name__}({obj})"

    else:
        return f"{type(obj).__name__}"


async def _generate_llm_summary(
        obj: Any,
        tool_id: str,
        bundle_id: str,
        timezone: str = None,
        service: Optional[Any] = None,
        max_tokens: int = 150,
        call_reason: Optional[str] = None,
        call_signature: Optional[str] = None,
        param_bindings_for_summary: Optional[str] = None,
        tool_inputs: Optional[Dict[str, Any]] = None,
        tool_doc_for_summary: Optional[str] = None,
        summary_artifact: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if service is None:
        return None

    try:
        now = _now_str()
        today = _today_str()
        # Serialize object once
        if isinstance(obj, str):
            content = obj
        else:
            try:
                content = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                content = str(obj)

        from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json
        from langchain_core.messages import HumanMessage

        token_cap = max_tokens  # this is what we pass to the model
        time_evidence = (
            "## AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)\n"
            # f"Current UTC timestamp: {now}\n"
            f"Current UTC date: {today}\n"
            "All relative dates (today/yesterday/last year/next month) MUST be "
            "interpreted against this context. Freshness must be estimated based on this context.\n"
        )
        time_evidence_reminder = f"Very important"
        if timezone:
            time_evidence_reminder += f"The user's timezone is {timezone}."
        time_evidence_reminder += f"Current UTC timestamp: {now}. Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future. When dealing with modern entities/companies/people, and the user asks for the 'latest', 'most recent', 'today's', etc. don't assume your knowledge is up to date; you MUST carefully confirm what the true 'latest' is first. If the user seems confused or mistaken about a certain date or dates, you MUST include specific, concrete dates in your response to clarify things. This is especially important when the user is referencing relative dates like 'today', 'tomorrow', 'yesterday', etc -- if the user seems mistaken in these cases, you should make sure to use absolute/exact dates like 'January 1, 2010' in your response.\n"
        artifact_block, artifact_meta, modality_kind = _artifact_block_for_summary(summary_artifact)

        system_prompt = (
            "You are summarizing ONE TOOL CALL inside a multi-step ReAct plan.\n"
            f"{time_evidence}\n"
            "Reader = decision agent that must: (a) judge if this step is safe to build on, "
            "(b) notice hidden problems early, (c) avoid repeating the same bad step.\n\n"
 
            "HARD CONSTRAINTS:\n"
            f"- Max {token_cap} tokens total; aim for 120–160.\n"
            "- Output EXACTLY three markdown sections, in this order:\n"
            "  ## Role & inputs\n"
            "  ## Output\n"
            "  ## Risks, quality & next moves\n"
            "- Each section MUST have 1–2 bullet points (`- ...`) and nothing else.\n"
            "- Bullets must be short, dense, and telegraphic; pack multiple signals per bullet with semicolons.\n\n"

            "GLOBAL BEHAVIOR:\n"
            "- Assume the reader of yiur summary agent often sees ONLY your summary, not the raw tool inputs/outputs.\n"
            "- Your text must be fully self-contained: do NOT rely on positional or implicit references.\n"
            "- Always be explicit about WHAT IS PRESENT vs WHAT IS MISSING in the tool output, "
            "relative to the goal/instructions/tool documentation.\n"
            "- Never assume coverage just because something is non-empty; infer coverage only from what you actually see.\n"
            "- Pay special attention to whether the output contains concrete evidence for the key aspects in the goal.\n"
            "- When multiple inputs/pages/results/URLs are involved, do NOT say 'first/second URL', "
            "'this page/snippet/result', 'that one', 'former/latter'.\n"
            "- Instead, give each important input/output a short, descriptive label based on its title, URL path, "
            "or semantic role (e.g. '`A` performance overview', '`B` docs page').\n\n"

            "SECTION 2 — ## Output\n"
            "- FIRST, state what degree of success the tool *appears* to have vs its goal, "
            "using one of: 'success', 'partial', 'failed/empty'.\n"
            "- Then describe what you ACTUALLY got, structurally AND semantically:\n"
            "  • Give a compact structural description (e.g. 'list[8] of {sid,title,url}', "
            "     'markdown table, 5 rows x 5 cols').\n"
            "  • Summarize the main topics/fields actually present.\n"
            "- Explicitly contrast requested aspects (from goal/instructions/tool doc) with observed coverage:\n"
            "  • say which major requested aspects look covered vs partially covered vs missing.\n"
            "- Never quote long passages; only lengths, counts, fields, and key themes.\n\n"
            
            "SECTION 1 — ## Risks, quality & next moves\n"
            "- Start the FIRST bullet with `Adequacy:` followed by EXACTLY one of:\n"
            "  'full', 'partial', or 'poor', then a short justification "
            "(e.g. 'Adequacy: partial; pricing present but no rate limits').\n"
            "- Call out concrete risks that will poison later steps: missing required columns/fields, "
            "contradictions, stale or off-domain data, extremely sparse results, lack of citations, truncated content.\n"
            "- Mention at most ONE follow-up direction, focused on fixing the biggest gap "
            "(e.g. 're-run search focusing on rate limits').\n\n"

            "SECTION 3 — ## Role & inputs\n"
            "- Say what this step tried to achieve in the plan (from the goal/reason), in ≤12 words.\n"
            "- Mention ONLY params that materially affect quality/coverage: n/results, model, temperature, "
            "format, key context sources, and important flags that could impact content semantics / contents elements (diagrams, tables, matrices, snippets, etc.) / coverage.\n"
            "- Do NOT list all args; no schemas; no long string values.\n\n"

            "GENERAL STYLE:\n"
            "- NEVER assume success just because the tool returned something; you must compare output vs goal.\n"
            "- Prefer: 'table 5 rows; rate-limit column mostly empty; citations missing' over generic prose.\n"
            "- If content appears truncated (e.g. explicit '... truncated ...' markers or obviously cut JSON), "
            "treat it as partial and say so.\n"
            "- If close to the token limit, drop softer commentary and keep sharp risk/coverage signals.\n"
        )

        # Extra guidance for web_search
        if tool_id == "generic_tools.web_search":
            system_prompt += (
                "\nEXTRA FOR generic_tools.web_search:\n"
                "- In Role & inputs: cluster queries into 1–3 short themes (3–5 words) and note site restrictions.\n"
                "- In Output: state result count vs requested n (e.g. '1/10, sparse') and describe what dominates:\n"
                "  'forum threads only', 'official API docs', 'mixed blogs'.\n"
                "- From the objective, infer 2–5 key aspects; for each say:\n"
                "  clearly: covered / partially covered / missing.\n"
                "- In Risks: highlight missing directions like 'no official docs', 'no rate-limit coverage', "
                "'all pre-2023'.\n"
            )

        # Extra guidance for your content generator
        if tool_id == "llm_tools.generate_content_llm":
            system_prompt += (
                "\nEXTRA FOR llm_tools.generate_content_llm:\n"
                "- Sources must be passed via sources_list only; input_context must NOT include sources.\n"
                "- Use the instruction/inputs to infer required structure (e.g. 'comparison table', "
                "'columns: provider/pricing/rate limits') and required behaviors (e.g. 'all numbers must have citations').\n"
                "- In Output: say explicitly whether that structure *actually* appears "
                "(e.g. 'markdown table with 'A' column', or 'no table; only prose').\n"
                "- Check a sample of rows/sections for required fields: if required pieces or citations are missing "
                "for many entries, say so plainly.\n"
                "- In Risks, mention any broken contract like missing columns, missing requested points, "
                "uncited numeric values, or providers absent compared to the inputs.\n"
            )

        system_prompt += _modality_system_instructions(modality_kind)
        system_prompt += "\n" + time_evidence_reminder
        system_msg = create_cached_system_message([
            {"text": system_prompt, "cache": True},
        ])

        goal_snippet = (call_reason or "").strip()
        goal_block = goal_snippet or "(not provided)"

        sig = (call_signature or "").strip() or "(not available)"
        pb  = (param_bindings_for_summary or "").strip() or "(no params / no bindings)"

        if not isinstance(tool_doc_for_summary, str):
            try:
                tool_doc_for_summary = json.dumps(tool_doc_for_summary, ensure_ascii=False, indent=2)
            except Exception:
                tool_doc_for_summary = str(tool_doc_for_summary)
        td = (tool_doc_for_summary or "").strip() or "(no additional documentation provided)"

        tool_doc = (
            f"Tool id: {tool_id}\n"
            f"### Tool call signature\n{sig}\n\n"
            f"### Tool documentation (schema & semantics)\n{td}\n\n"
        )
        operational_msg = (
            f"### Goal / reason for this tool call\n{goal_block}\n\n"
            f"### Param bindings (for reference; pick only what's important)\n{pb}\n\n"
            "### Tool raw output (object to summarize)\n"
            f"{content}\n\n"
        )
        user_blocks = [
            {"type": "text", "text": tool_doc, "cache": True},
            {"type": "text", "text": operational_msg, "cache": False},
        ]
        if artifact_meta:
            user_blocks.append({"type": "text", "text": artifact_meta, "cache": False})
        if artifact_block:
            user_blocks.append({**artifact_block, "cache": False})
        user_blocks.append({
            "type": "text",
            "text": "Now produce the three sections EXACTLY as required in the system instructions.",
            "cache": False,
        })
        user_message = create_cached_human_message(user_blocks)
        role = "solver.react.summary"
        async with with_accounting(
                bundle_id,
                track_id="A",
                agent=role,
                metadata={"track_id": "A", "agent": role},
        ):
            result = await stream_agent_to_json(
                service,
                client_name=role,
                client_role=role,
                sys_prompt=system_msg,
                messages=[user_message],
                schema_model=None,
                temperature=0.2,
                max_tokens=token_cap,
            )
            logging_helpers.log_agent_packet(role, "summary", result)

        summary = (result.get("agent_response") or "").strip()
        log.info(f"LLM summary generated ({len(summary)} chars)\n{summary}")
        return summary if summary and len(summary) > 10 else None

    except Exception:
        return None

async def generate_llm_summary_json(
        obj: Any,
        tool_id: str,
        bundle_id: str,
        timezone: Optional[str] = None,
        service: Optional[Any] = None,
        max_tokens: int = 150,
        call_reason: Optional[str] = None,
        call_signature: Optional[str] = None,
        param_bindings_for_summary: Optional[str] = None,
        tool_inputs: Optional[Dict[str, Any]] = None,
        tool_doc_for_summary: Optional[str] = None,
        summary_artifact: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[ToolCallSummaryJSON], Optional[str]]:
    """
    Compact JSON tool-call summarizer.

    Goals:
      - minimize output overhead (no per-aspect objects; compact strings)
      - NEVER drop missingness: use literal "NONE" / ["NONE"] instead of omitting fields
    """
    if service is None:
        return None

    try:
        now = _now_str()
        today = _today_str()

        # Serialize object once
        if isinstance(obj, str):
            content = obj
        else:
            try:
                content = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                content = str(obj)

        from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json

        token_cap = max_tokens
        time_evidence = (
            "## AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)\n"
            f"Current UTC date: {today}\n"
            "All relative dates (today/yesterday/last year/next month) MUST be "
            "interpreted against this context. Freshness must be estimated based on this context.\n"
        )
        time_evidence_reminder = (
            f"Very important: The user's timezone is {timezone}. "
            f"Current UTC timestamp: {now}. Current UTC date: {today}. "
            "Any dates before this are in the past, and any dates after this are in the future. "
            "When dealing with modern entities/companies/people, and the user asks for the 'latest', "
            "'most recent', 'today's', etc. don't assume your knowledge is up to date; you MUST carefully "
            "confirm what the true 'latest' is first. If the user seems confused or mistaken about a "
            "certain date or dates, you MUST include specific, concrete dates in your reasoning. "
            "This is especially important when the user is referencing relative dates like 'today', "
            "'tomorrow', 'yesterday', etc.\n"
        )

# ---- SYSTEM PROMPT (compact schema; no giant JSON examples) ----
        artifact_block, artifact_meta, modality_kind = _artifact_block_for_summary(summary_artifact)

        system_prompt = (
            "You are summarizing ONE TOOL CALL inside a multi-step ReAct plan.\n"
            f"{time_evidence}\n"
            "Reader = decision agent that must: (a) judge if this step is safe to build on, "
            "(b) notice hidden problems early, (c) avoid repeating the same bad step.\n\n"
            "GLOBAL BEHAVIOR:\n"
            "- Assume the reader often sees ONLY this summary, not raw tool input/output.\n"
            "- The JSON must be fully self-contained: no positional references like 'this page' or 'first URL'.\n"
            "- Always be explicit about WHAT IS PRESENT vs WHAT IS MISSING in the tool output, "
            "relative to the goal/instructions/tool documentation.\n"
            "- Never assume coverage just because something is non-empty; infer coverage only from what you actually see.\n"
            "- Pay special attention to whether the output contains concrete evidence for the key aspects in the goal.\n"
            "- When multiple inputs/pages/results/URLs are involved, assign short descriptive labels in strings "
            "(e.g. 'A: performance overview', 'B: docs page'). No 'former/latter', 'this page', etc.\n\n"            
            "- If the tool output includes a service_error (or clearly indicates a model/service failure), "
            "you MUST surface it explicitly and treat it as the primary failure cause.\n\n"
            "HARD CONSTRAINTS:\n"
            f"- Max {token_cap} tokens total; be dense.\n"
            "- Output MUST be exactly ONE JSON object with ONLY these top-level keys: input, output, strategy.\n"
            "- NEVER omit fields; if unknown/missing use literal string \"NONE\" (or [\"NONE\"]).\n\n"
            "JSON SHAPE (keys must match exactly):\n"
            "{\n"
            "  \"input\": {\n"
            "    \"call_reason\": \"...\",\n"
            "    \"key_params\": [\"...\", \"...\"]\n"
            "  },\n"
            "  \"output\": {\n"
            "    \"completeness\": \"success|partial|failed_empty\",\n"
            "    \"structural_summary\": \"...\",\n"
            "    \"semantic_summary\": \"...\",\n"
            "    \"coverage_by_aspect\": \"aspect=<a>;status=<covered|partially_covered|missing>|aspect=<b>;status=<...>\"\n"
            "  },\n"
            "  \"strategy\": {\n"
            "    \"adequacy\": \"full|partial|poor\",\n"
            "    \"risks\": [\"...\"],\n"
            "    \"main_next_move\": \"...\"\n"
            "  }\n"
            "}\n\n"
            "FIELD RULES:\n"
            "- input.call_reason: ≤12 words; what this step tried to achieve.\n"
            "- input.key_params: 1–3 short items; include only params that affect quality/coverage.\n"
            "- output.completeness: compare actual output vs goal.\n"
            "- output.structural_summary: structure/counts/fields only; no long quotes. i.e. ' mixed content. markdown table 5x5; JSON dict with keys: a,b,c; 3 code snippets (subj1, subj2, subj3)'\n"
            "- output.semantic_summary: what actually came back; main topics/fields/themes; telegraphic; pack multiple signals with semicolons if useful; mention what's missing.\n"
            "- output.coverage_by_aspect: encode 2–5 inferred aspects; use 'NONE' if you truly cannot infer.\n"
            "- strategy.adequacy: full/partial/poor; build-on safety.\n"
            "- strategy.risks: 0–4 concrete risks; if none write [\"NONE\"].\n"
            "- strategy.main_next_move: at most ONE follow-up; if none write \"NONE\".\n\n"
            "GENERAL BEHAVIOR:\n"
            "- Be short, dense, and telegraphic; avoid full-sentence essays.\n"
            "- Prefer 'table 5 rows; rate-limit column mostly empty; no citations' over generic prose.\n"
            "- Be self-contained; no 'this page', 'first URL', 'former/latter'. Use descriptive labels if needed.\n"
            "- Never assume coverage from non-empty output; infer only from what you see.\n"
            "- If content looks truncated, mark completeness=partial and include that risk.\n"
        )

        # Extra guidance for web_search → mapped into JSON fields
        if tool_id == "generic_tools.web_search":
            system_prompt += (
                "\nEXTRA FOR generic_tools.web_search (JSON mapping):\n"
                "- Use input.call_reason to describe the search role, e.g. 'collect docs about API rate limits'.\n"
                "- Put query themes and site restrictions into input.key_params, clustered into 1–3 short items.\n"
                "- In output.structural_summary:\n"
                "  • state result count vs requested n, e.g. '8 result(s), requested 10'.\n"
                "  • mention dominant source types: 'official API docs', 'forum threads', 'mixed blogs'.\n"
                "- In output.coverage_by_aspect:\n"
                "  • from the objective, infer 2–5 key aspects (e.g. 'pricing', 'rate_limits', 'quotas', 'auth').\n"
                "  • encode as: 'aspect=<pricing>;status=<covered>|aspect=<rate_limits>;status=<missing>' (etc.).\n"
                "- In strategy.risks:\n"
                "  • highlight missing directions: 'no official docs', 'no rate-limit coverage', 'all pre-2023'.\n"
            )

        # Extra guidance for your content generator → mapped into JSON fields (ADJUSTED NAMES ONLY)
        if tool_id == "llm_tools.generate_content_llm":
            system_prompt += (
                "\nEXTRA FOR llm_tools.generate_content_llm (JSON mapping):\n"
                "- Use input.call_reason to encode the requested structure/behavior/intent/objective,\n"
                "  e.g. 'generate comparison table of providers x pricing x rate_limits'.\n"
                "- In output.structural_summary:\n"
                "  • say explicitly whether the required structure actually appears:\n"
                "    'markdown table with provider,pricing,rate_limits columns', or 'no table; only prose'.\n"
                "- In output.coverage_by_aspect:\n"
                "  • add aspects for required fields (e.g. 'pricing', 'rate_limits', 'support_SLA', 'citations').\n"
                "  • encode as: 'aspect=<citations>;status=<missing>|aspect=<rate_limits>;status=<partially_covered>' (etc.).\n"
                "- In strategy.risks:\n"
                "  • call out any broken contract: missing columns, missing requested points,\n"
                "    uncited numeric values, providers absent compared to the inputs.\n"
            )

        system_prompt += _modality_system_instructions(modality_kind)
        system_prompt += "\n" + time_evidence_reminder

        system_msg = create_cached_system_message([
            {"text": system_prompt, "cache": True},
        ])

        goal_snippet = (call_reason or "").strip()
        goal_block = goal_snippet or "(not provided)"

        sig = (call_signature or "").strip() or "(not available)"
        pb = (param_bindings_for_summary or "").strip() or "(no params / no bindings)"

        if not isinstance(tool_doc_for_summary, str):
            try:
                tool_doc_for_summary = json.dumps(tool_doc_for_summary, ensure_ascii=False, indent=2)
            except Exception:
                tool_doc_for_summary = str(tool_doc_for_summary)
        td = (tool_doc_for_summary or "").strip() or "(no additional documentation provided)"

        tool_doc = (
            f"Tool id: {tool_id}\n"
            f"### Tool call signature\n{sig}\n\n"
            f"### Tool documentation (schema & semantics)\n{td}\n\n"
        )
        operational_msg = (
            f"### Goal / reason for this tool call\n{goal_block}\n\n"
            f"### Param bindings (for reference; pick only what's important)\n{pb}\n\n"
            "### Tool raw output (object to summarize)\n"
            f"{content}\n\n"
        )
        user_blocks = [
            {"type": "text", "text": tool_doc, "cache": True},
            {"type": "text", "text": operational_msg, "cache": False},
        ]
        if artifact_meta:
            user_blocks.append({"type": "text", "text": artifact_meta, "cache": False})
        if artifact_block:
            user_blocks.append({**artifact_block, "cache": False})
        user_blocks.append({
            "type": "text",
            "text": "Now fill the JSON fields exactly as required in the system instructions.\n"
                    "Do NOT invent extra top-level fields; use only the schema shown above.\n",
            "cache": False,
        })

        user_message = create_cached_human_message(user_blocks)
        role = "solver.react.summary"

        from kdcube_ai_app.infra.accounting import with_accounting
        async with with_accounting(
                bundle_id,
                track_id="A",
                agent=role,
                metadata={"track_id": "A", "agent": role},
        ):
            result = await stream_agent_to_json(
                service,
                client_name=role,
                client_role=role,
                sys_prompt=system_msg,
                messages=[user_message],
                schema_model=ToolCallSummaryJSON,  # <— structured JSON path
                temperature=0.2,
                max_tokens=token_cap,
            )
        logging_helpers.log_agent_packet(role, "summary", result)
        raw = result.get("agent_response") or {}
        raw_data = (result.get("log")  or {}).get("raw_data") or ""
        # Ensure it's validated as our model, then return as plain dict
        try:
            summary_obj = ToolCallSummaryJSON.model_validate(raw)
            log.info(
                "LLM JSON summary generated\n"
                f"{json.dumps(summary_obj.model_dump(), ensure_ascii=False, indent=2)}"
            )
            return summary_obj, raw_data
        except Exception as ex:
            log.warning(
                f"LLM JSON summary generation FAILED validation: {ex}\n"
                f"Raw response:\n{raw_data}"
            )
            return None, raw_data

    except Exception:
        return None, None


def _parse_iso_date_str(s: str) -> Optional[datetime]:
    """
    Best-effort parsing of ISO-like date/time into datetime.
    If only YYYY-MM-DD is present, it's fine; otherwise we fallback.
    """
    if not isinstance(s, str) or not s.strip():
        return None
    text = s.strip()
    # Drop time part if present (we only need date-level granularity)
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _summarize_web_search_results(
        output: Any,
        tool_inputs: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Decision-node-oriented summary for generic_tools.web_search.

    Goals:
      - Show search context (queries + objective preview)
      - Show coverage quality (volume vs requested `n`, relevance distribution)
      - Show freshness (recent / somewhat old / stale / unknown)
      - Show domain diversity
      - Show a couple of representative titles

    Expected canonical result row (per Source spec):

      {
        "sid": 123,
        "title": "Some page title",
        "url": "https://example.com",

        "text": "Short excerpt or summary…",      # short snippet / preview
        "content": "Full article body…",          # full body (optional)

        "objective_relevance": 0.95,
        "query_relevance": 0.87,
        "reasoning": "Why this source is relevant…",

        "published_time_iso": "2025-01-02T12:34:56Z",
        "modified_time_iso": "2025-01-03T10:00:00Z"
      }
    """
    # ---- 0) Extract tool params for context (queries, objective, n, fetch_content) ----
    queries_preview = ""
    objective_preview = ""
    n_requested: Optional[int] = None
    fetch_content_flag: Optional[bool] = None

    if isinstance(tool_inputs, dict):
        # queries can be JSON string or list or single string
        raw_q = tool_inputs.get("queries")
        queries: list[str] = []

        if isinstance(raw_q, list):
            queries = [str(q) for q in raw_q if isinstance(q, (str, int, float))]
        elif isinstance(raw_q, str):
            # try to parse as JSON list first
            try:
                parsed = json.loads(raw_q)
                if isinstance(parsed, list):
                    queries = [str(q) for q in parsed if isinstance(q, (str, int, float))]
                else:
                    queries = [raw_q]
            except Exception:
                queries = [raw_q]

        if queries:
            # take first 2 queries for preview
            q2 = queries[:2]
            qp = "; ".join([f"\"{q.strip()}\"" for q in q2 if q and str(q).strip()])
            if len(queries) > 2:
                qp += f"; …+{len(queries) - 2} more"
            queries_preview = qp

        obj = tool_inputs.get("objective")
        if isinstance(obj, str) and obj.strip():
            s = obj.strip()
            if len(s) > 160:
                s = s[:157] + "…"
            objective_preview = s

        try:
            n_requested = int(tool_inputs.get("n"))
        except Exception:
            n_requested = None

        if "fetch_content" in tool_inputs:
            fetch_content_flag = bool(tool_inputs.get("fetch_content"))

    # ---- 1) Normalize output to list[dict] ----
    data: Any = output
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except Exception:
            return _build_structural_summary(output)

    if not isinstance(data, list):
        return _build_structural_summary(data)

    if not data:
        # Even if no results, still show what we tried to search for
        ctx = []
        if queries_preview:
            ctx.append(f"queries: {queries_preview}")
        if objective_preview:
            ctx.append(f"objective: \"{objective_preview}\"")
        ctx_str = (" (" + "; ".join(ctx) + ")") if ctx else ""
        return f"Web search returned 0 results{ctx_str}."

    # ---- 2) Compute quality signals ----
    total = 0
    high_obj_rel = 0
    mid_obj_rel = 0
    high_query_rel = 0

    domains: set[str] = set()
    dates: list[datetime] = []
    titles: list[str] = []
    full_content_count = 0
    snippet_only_count = 0

    for row in data:
        if not isinstance(row, dict):
            continue
        total += 1

        # relevance
        try:
            obj_rel = float(row.get("objective_relevance") or 0.0)
        except Exception:
            obj_rel = 0.0
        if obj_rel >= 0.7:
            high_obj_rel += 1
        elif obj_rel >= 0.4:
            mid_obj_rel += 1

        try:
            q_rel = float(row.get("query_relevance") or 0.0)
        except Exception:
            q_rel = 0.0
        if q_rel >= 0.7:
            high_query_rel += 1

        # domains
        url = (row.get("url") or "").strip()
        if url:
            try:
                dom = urlparse(url).netloc or ""
            except Exception:
                dom = ""
            if dom:
                domains.add(dom)

        # dates (prefer canonical fields)
        date_raw = (
                row.get("published_time_iso")
                or row.get("modified_time_iso")
                or row.get("published_at")
                or row.get("updated_at")
                or row.get("pub_date")
                or row.get("date")
        )
        if isinstance(date_raw, str):
            dt = _parse_iso_date_str(date_raw)
            if dt:
                dates.append(dt)

        # content richness
        has_content = bool((row.get("content") or "").strip())
        has_text = bool((row.get("text") or "").strip())
        if has_content:
            full_content_count += 1
        elif has_text:
            snippet_only_count += 1

        # titles
        title = (row.get("title") or "").strip()
        if title:
            titles.append(title)

    if total == 0:
        ctx = []
        if queries_preview:
            ctx.append(f"queries: {queries_preview}")
        if objective_preview:
            ctx.append(f"objective: \"{objective_preview}\"")
        ctx_str = (" (" + "; ".join(ctx) + ")") if ctx else ""
        return f"Web search returned 0 usable results{ctx_str}."

    # ---- 3) Derive qualitative labels ----
    # coverage: how many results vs requested n
    if n_requested and n_requested > 0:
        cov_ratio = total / float(n_requested)
        if cov_ratio < 0.4:
            coverage_label = "sparse"
        elif cov_ratio < 0.8:
            coverage_label = "partial"
        else:
            coverage_label = "good"
    else:
        coverage_label = "unknown"

    # relevance label
    rel_ratio = high_obj_rel / float(total) if total else 0.0
    if rel_ratio >= 0.6:
        relevance_label = "high"
    elif rel_ratio >= 0.3:
        relevance_label = "mixed"
    else:
        relevance_label = "low"

    # freshness label
    freshness_label = "unknown"
    freshness_detail = ""
    if dates:
        d_min = min(dates)
        d_max = max(dates)
        # Age in days of most recent source
        age_days = (datetime.utcnow() - d_max).days
        if age_days <= 180:
            freshness_label = "recent"
        elif age_days <= 730:
            freshness_label = "somewhat_old"
        else:
            freshness_label = "stale"
        freshness_detail = f"{d_min.date().isoformat()} – {d_max.date().isoformat()}"

    # domains preview
    domains_list = sorted(domains)
    domains_preview = ""
    if domains_list:
        d2 = domains_list[:3]
        domains_preview = ", ".join(d2)
        if len(domains_list) > 3:
            domains_preview += f", …+{len(domains_list) - 3}"

    # ---- 4) Build human-friendly multi-line summary ----
    header_parts: list[str] = []
    if queries_preview:
        header_parts.append(f"queries: {queries_preview}")
    if objective_preview:
        header_parts.append(f"objective: \"{objective_preview}\"")
    header_ctx = "; ".join(header_parts)
    header = "Web search"
    if header_ctx:
        header += f" ({header_ctx})"

    line2_parts: list[str] = []
    line2_parts.append(f"{total} result(s)")
    if n_requested:
        line2_parts[-1] += f" (requested {n_requested})"
    line2_parts.append(f"coverage={coverage_label}")
    line2_parts.append(f"relevance={relevance_label} (high_obj={high_obj_rel}, mid_obj={mid_obj_rel})")
    if high_query_rel:
        line2_parts.append(f"high_query_rel={high_query_rel}")
    if freshness_label != "unknown":
        frag = f"freshness={freshness_label}"
        if freshness_detail:
            frag += f" [{freshness_detail}]"
        line2_parts.append(frag)
    if domains_preview:
        line2_parts.append(f"domains={len(domains_list)} (e.g. {domains_preview})")

    # content richness
    if full_content_count or snippet_only_count:
        frag = f"content_full={full_content_count}"
        if snippet_only_count:
            frag += f", snippet_only={snippet_only_count}"
        if fetch_content_flag is not None:
            frag += f", fetch_content={fetch_content_flag}"
        line2_parts.append(frag)

    line2 = "; ".join(line2_parts)

    line3 = ""
    if titles:
        ex = titles[:2]
        if len(titles) > 2:
            ex.append(f"…+{len(titles) - 2} more")
        line3 = "Examples: " + "; ".join(ex)

    if line3:
        return f"{header}\n{line2}\n{line3}"
    return f"{header}\n{line2}"

async def build_summary_for_tool_output(
        tool_id: str,
        output: Any,
        use_llm_summary: bool,
        bundle_id: str,
        llm_service: Optional[Any],
        timezone: Optional[str] = None,
        call_reason: Optional[str] = None,
        tool_inputs: Optional[Dict[str, Any]] = None,
        call_signature: Optional[str] = None,
        param_bindings_for_summary: Optional[str] = None,
        tool_doc_for_summary: Optional[str] = None,
        summary_artifact: Optional[Dict[str, Any]] = None,
        structured: bool = False,
) -> Tuple[Union[str, ToolCallSummaryJSON], Optional[str]]:
    """
    Centralized summary builder:
      - special-case known tools (web_search) [currently disabled]
      - optionally use LLM for complex structures
      - otherwise fall back to structural / simple summaries

    Behavior:
      - structured == False  (default) → returns a short human-readable string (backwards compatible).
      - structured == True            → returns a ToolCallSummaryJSON instance.
    """
    # 1) Special-case: canonical web_search summarizer (non-LLM) – keep disabled for now
    # if tool_id == "generic_tools.web_search" and not structured:
    #     return _summarize_web_search_results(output, tool_inputs=tool_inputs)

    # 2) Normalize output once (best-effort JSON parse)
    obj = output
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            obj = parsed
        except Exception:
            # Not JSON → keep as plain string
            obj = output

    # 3) If caller wants structured JSON, try JSON summarizer first
    if structured:
        if use_llm_summary and llm_service:
            summary_json, summary = await generate_llm_summary_json(
                obj=obj,
                tool_id=tool_id,
                bundle_id=bundle_id,
                timezone=timezone,
                service=llm_service,
                max_tokens=300,
                call_reason=call_reason,
                call_signature=call_signature,
                param_bindings_for_summary=param_bindings_for_summary,
                tool_inputs=tool_inputs,
                tool_doc_for_summary=tool_doc_for_summary,
                summary_artifact=summary_artifact,
            )
            return summary_json, summary

    # 4) Non-structured path (BACKWARDS COMPATIBLE): free-text summary as before
    if use_llm_summary and llm_service:
        llm_summary = await _generate_llm_summary(
            obj=obj,
            timezone=timezone,
            bundle_id=bundle_id,
            tool_id=tool_id,
            service=llm_service,
            max_tokens=2000,
            call_reason=call_reason,
            call_signature=call_signature,
            param_bindings_for_summary=param_bindings_for_summary,
            tool_inputs=tool_inputs,
            tool_doc_for_summary=tool_doc_for_summary,
            summary_artifact=summary_artifact,
        )
        if llm_summary:
            return llm_summary, llm_summary

    # 5) Fallbacks (non-LLM): purely structural / simple previews (string form)
    if isinstance(obj, (dict, list)):
        s = _build_structural_summary(obj)
        return s, s

    if isinstance(obj, str):
        s = (obj[:300] + "...") if len(obj) > 300 else obj
        return s, s

    return str(obj), str(obj)


"""
summary_json = await _generate_llm_summary_json(
    obj=output,
    tool_id=tool_id,
    context=context,
    service=llm_service,
    max_tokens=300,
    call_reason=call_reason,
    call_signature=call_signature,
    param_bindings_for_summary=param_bindings_for_summary,
    tool_inputs=tool_inputs,
    tool_doc_for_summary=tool_doc_for_summary,
)

if summary_json:
    # e.g. attach to slot.contents_actualization, or pretty-print for humans
    # or convert to markdown however you like
    ...
"""

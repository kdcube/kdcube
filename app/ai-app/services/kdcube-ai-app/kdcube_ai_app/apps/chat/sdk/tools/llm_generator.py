# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# chat/sdk/tools/llm_generator.py
#
# Channel-tag streaming variant of generate_content_llm.

from __future__ import annotations

import json
import logging
from typing import Annotated, Optional, List, Dict, Any

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta, get_comm
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.tools.text_proc_utils import (
    _format_ok,
    _parse_yaml,
    _parse_json,
    _validate_json_schema,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import CITATION_TOKENS
from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes
from kdcube_ai_app.infra.accounting import _get_context
from kdcube_ai_app.infra.service_hub.inventory import create_cached_system_message, create_cached_human_message
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError
from kdcube_ai_app.infra.service_hub.multimodality import (
    MODALITY_IMAGE_MIME,
    MODALITY_DOC_MIME,
    MODALITY_MAX_IMAGE_BYTES,
    MODALITY_MAX_DOC_BYTES,
)
from kdcube_ai_app.apps.chat.sdk.util import estimate_b64_size

logger = logging.getLogger(__name__)


def _collect_multimodal_source_attachments(src_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []
    for s in src_list or []:
        if not isinstance(s, dict):
            continue
        data_b64 = s.get("base64")
        mime = (s.get("mime") or "").strip()
        if not data_b64 or not mime:
            continue
        size = estimate_b64_size(data_b64)
        if mime in MODALITY_IMAGE_MIME and size <= MODALITY_MAX_IMAGE_BYTES:
            attachments.append({
                "base64": data_b64,
                "mime": mime,
                "filename": s.get("filename") or s.get("title") or "",
                "summary": s.get("text") or "",
                "size": size,
            })
        elif mime in MODALITY_DOC_MIME and size <= MODALITY_MAX_DOC_BYTES:
            attachments.append({
                "base64": data_b64,
                "mime": mime,
                "filename": s.get("filename") or s.get("title") or "",
                "summary": s.get("text") or "",
                "size": size,
            })
    return attachments


def _build_sources_digest(srcs: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for s in srcs or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("sid")
        title = (s.get("title") or "").strip()
        url = (s.get("url") or "").strip()
        text = (s.get("text") or "").strip()
        if sid and (title or url):
            line = f"[S:{sid}] {title} {url}".strip()
            if text:
                line += f"\n  - {text[:200]}"
            lines.append(line)
    return "\n".join(lines).strip()


async def _emit_wrapper(**kwargs):
    if not get_comm():
        return
    await emit_delta(**kwargs)


async def generate_content_llm(
        _SERVICE,
        agent_name: Annotated[str, "Name of this content creator, short, to distinguish this author in the sequence of generative calls."],
        instruction: Annotated[str, "User instruction / prompt. What to produce (goal/contract). "],
        artifact_name: Annotated[str, "Logical name of the artifact being produced."],
        input_context: Annotated[Optional[str], "Optional base text or data to use."] = "",
        target_format: Annotated[str, "html|markdown|json|yaml|text|managed_json_artifact"] = "markdown",
        schema_json: Annotated[str, "Optional JSON Schema (for json/yaml validation)."] = "",
        sources_list: Annotated[list[dict], "Sources list with SIDs and URLs."] = None,
        cite_sources: Annotated[bool, "Whether to require and render citations."] = True,
        citation_embed: Annotated[str, "auto|inline|sidecar|none"] = "auto",
        max_tokens: Annotated[int, "Max tokens for this generation."] = 3000,
        model_strength: Annotated[str, "regular|strong"] = "regular",
        channel_to_stream: Annotated[str, "Stream channel marker"] = "answer",
        attachments: Annotated[Optional[List[Dict[str, Any]]], "Optional multimodal attachments."] = None,
        composite_cfg: Annotated[Optional[Dict[str, str]], "Managed JSON artifacts config"] = None,
        infra_call: Annotated[bool, "Internal tools call (relaxes global instructions)."] = False,
) -> Dict[str, Any]:
    """
    Channel-tag streaming generator using <channel:name> blocks.

    Output protocol (required):
      <channel:answer> ... </channel:answer>
      <channel:usage> ["S1","S2"] </channel:usage>   (optional)

    - sources_list enables citation replacement in-stream.
    - target_format controls validation and citation rendering.
    - composite_cfg enables managed_json_artifact streaming to canvas.
    """
    sources_list = sources_list or []
    attachments = attachments or []
    tgt = (target_format or "markdown").strip().lower()

    if sources_list:
        attachments = attachments + _collect_multimodal_source_attachments(sources_list)

    citation_map = citations_module.build_citation_map_from_sources(sources_list)

    context = _get_context()
    context_snapshot = context.to_dict()
    timezone = context_snapshot.get("timezone") or "Europe/Berlin"
    today = _today_str()
    now = _now_up_to_minutes()
    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        f"Current UTC date: {today}\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    time_evidence_reminder = (
        f"Very important: The user's timezone is {timezone}. Current UTC timestamp: {now}. "
        f"Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future. "
        "When dealing with modern entities/companies/people, and the user asks for the 'latest', 'most recent', "
        "etc. don't assume your knowledge is up to date; you MUST carefully confirm what the true 'latest' is first. "
        "If the user seems confused or mistaken about a certain date or dates, you MUST include specific, concrete "
        "dates in your response to clarify things. This is especially important when the user is referencing relative "
        "dates like 'today', 'tomorrow', 'yesterday', etc -- if the user seems mistaken in these cases, you should make "
        "sure to use absolute/exact dates like 'January 1, 2010' in your response.\n"
    )

    have_sources = bool(citations_module.extract_sids(sources_list))

    # citation embed policy (mirrors with_llm_backends)
    eff_embed = citation_embed
    if citation_embed == "auto":
        if tgt in ("markdown", "text", "html"):
            eff_embed = "inline"
        elif tgt in ("json", "yaml", "managed_json_artifact"):
            eff_embed = "sidecar"
        elif tgt == "xml":
            eff_embed = "none"
        else:
            eff_embed = "none"

    require_citations = bool(cite_sources and have_sources and eff_embed != "none")

    if infra_call:
        basic_sys_instruction = (
            "You are a focused JSON generator for internal tools. "
            "Return only JSON in the requested schema; no explanations."
        )
        require_citations = False
        cite_sources = False
        eff_embed = "none"
    else:
        basic_sys_instruction = "\n".join([
            "You are a precise generator. Produce ONLY the requested artifact in the requested format.",
            "If [ACTIVE SKILLS] are present, they are dominant. Follow them over any general guidance here.",
            "NEVER include meta-explanations. Do not apologize. No prefaces. No trailing notes.",
            "You must produce content exactly of format stated in [TARGET GENERATION FORMAT]. No any deviations!",
            "",
            "GENERAL OUTPUT RULES:",
            "- Keep the output self-contained.",
            "- Avoid placeholders like 'TBD' unless explicitly requested.",
            "",
            "NUMERIC & FACTUAL PRECISION RULES:",
            "- When answering with facts, numbers, prices, dates, thresholds, metrics or other quantitative values, copy them EXACTLY as they appear in the input context or sources when possible.",
            "- Do NOT invent new numeric values (e.g. sums, averages, percentage changes, growth rates, exchange-rate based values) that do not explicitly appear in a source.",
            "- Do NOT perform unit or currency conversions or any other arithmetic transformation (e.g. hours→minutes, EUR→USD, monthly→yearly, GB→MB) unless the user EXPLICITLY says that an approximate conversion is acceptable.",
            "- If the user asks for a different unit or currency than what is available, answer in the original unit/currency and clearly name it, and/or explicitly state that you are not converting it.",
            "- If you cannot find a requested numeric fact in any source or context, say so instead of guessing.",
        ])
        basic_sys_instruction += "\n" + CITATION_TOKENS + "\n" + time_evidence
    target_format_sys_instruction = f"[TARGET GENERATION FORMAT]: {tgt}"

    sys_lines: List[str] = []
    if tgt == "markdown":
        sys_lines += [
            "[MARKDOWN RULES]:",
            "- Use proper headings, lists, tables, and code blocks as needed.",
        ]
    elif tgt == "mermaid":
        sys_lines += [
            "[MERMAID DIAGRAM RULES]:",
            "- Start with diagram type (flowchart TD / sequenceDiagram / etc.). NO code fences (```).",
            "- Node IDs: simple alphanumerics only (A, B, step1). Never use reserved words (graph, end, class).",
            "- Labels with special chars (:;,()[]{}|/#&*+-<>?!\\\"') MUST be in double quotes: A[\"Label: value\"]",
            "- Arrows: --> (solid), -.-> (dotted), ==> (thick). NOT single dash.",
            "- Line breaks: use <br/> inside quotes, not \\n",
            "- Quote escaping: use #quot; or \\\" inside quoted labels.",
            "- Close all subgraphs with 'end'.",
            "- End immediately with completion marker after last diagram line.",
            "",
            "Example:",
            "flowchart TD",
            "    A[\"Start: Process (step 1)\"] --> B{\"Valid?\"}",
            "    B -->|\"Yes\"| C[\"Success\"]",
        ]
    elif tgt == "html":
        sys_lines += ["""
    You are an HTML generator. Your output MUST be valid, well-formed HTML.

    [⚠️ CRITICAL FORMAT RULE]: Output PURE HTML ONLY. NO markdown. NO code fences (```). NO explanations.
    Start IMMEDIATELY with <!DOCTYPE html> or the opening tag. End with </html> and the completion marker.
    CRITICAL RULE: NEVER produce broken HTML. An incomplete document is worthless.

    [TOKEN BUDGET MANAGEMENT]:
    - You have a token budget for this generation (typically 4000-8000 tokens).
    - You CANNOT see when you're about to hit the limit.
    - Strategy: Be CONSERVATIVE. Stop early to guarantee closure.

    [SAFE GENERATION PATTERN]:
    1. Calculate safe capacity:
       - If user requests N items, plan to deliver 60-70% of N
       - Reserve 15-20% of your budget for structure and closing tags
       - Examples:
         • User wants 50 product cards? Plan for 30-35 complete cards
         • User wants 100 table rows? Plan for 60-70 complete rows
         • User wants 25 sections? Plan for 15-18 complete sections

    2. Structure your document:
       <!DOCTYPE html>
       <html>
       <head>
         <meta charset="UTF-8">
         <title>...</title>
       </head>
       <body>
         <!-- Add content here -->
       </body>
       </html>

    3. Generate items in batches:
       - After every 5 items, remind yourself: "Budget check - can I safely add 5 more AND close?"
       - If uncertain: STOP and close the document
       - Better to deliver 10 complete items than 15 broken ones

    4. Closing sequence (MANDATORY):
       - Close all open elements (div, section, table, ul, etc.)
       - Close </body>
       - Close </html>
       - Add the completion marker
       - DO NOT add any content after </html>

    [VALID HTML REQUIREMENTS]:
    - Major tags MUST close: <div>...</div>, <section>...</section>, <table>...</table>
    - Proper nesting: <div><p></p></div>
    - Self-closing tags OK: <br>, <img>, <hr>, <meta>, <input>
    - Attributes quoted: <div class="container">
    - Special characters escaped in text: &lt; &gt; &amp;
    - ALWAYS close: <body>, <html>, <head>, <title>, <script>, <style>

    [OUTPUT FORMAT]:
    - Pure HTML only (no markdown, no code fences, no explanations)
    - Start immediately with <!DOCTYPE html>
    - End with </html> followed by <<<GENERATION FINISHED>>>
    - No apologetic messages like "Due to space constraints..."

    [EXAMPLES OF SAFE SCALING]:
    - Request: "100 blog post cards" → Deliver: 60-70 complete cards
    - Request: "50 employee profiles" → Deliver: 30-35 complete profiles
    - Request: "25 dashboard widgets" → Deliver: 15-18 complete widgets
    - Request: "10 detailed sections" → Deliver: 6-7 complete sections

    [FAILURE MODES TO AVOID]:
    ❌ <body><div><p>...</p><div>...  [TRUNCATED - no closing </body></html>]
    ❌ <html><body>...</body></html>Here's the webpage...  [TEXT AFTER </html>]
    ❌ <div class="item  [ATTRIBUTE NOT CLOSED]
    ❌ <table><tr><td>...</td>  [TABLE NOT CLOSED]
    ✅ <html><body><div>...</div><div>...</div></body></html><<<GENERATION FINISHED>>>

    REMEMBER: Quality over quantity. Valid HTML with fewer items > Invalid HTML with more items.
    """]
    elif tgt in ("json", "yaml"):
        sys_lines += [
            f"CRITICAL {tgt.upper()} RULES:",
            "- Return a SINGLE, COMPLETE, syntactically valid document. NEVER cut arrays/objects in the middle.",
            "- If token budget is tight, REDUCE SCOPE (fewer items/fields) but preserve validity.",
            "- Do NOT emit ellipses, partial items, or dangling commas/brackets.",
            "- Avoid triple-fence code blocks for structured output; emit raw JSON/YAML only.",
            "- Do not add commentary outside the structured document.",
        ]
    elif tgt == "xml":
        sys_lines += ["""
    You are an XML generator. Your output MUST be valid, well-formed XML.

    CRITICAL RULE: NEVER produce broken XML. An incomplete document is worthless.

    [TOKEN BUDGET MANAGEMENT]:
    - You have a token budget for this generation (typically 4000-8000 tokens).
    - You CANNOT see when you're about to hit the limit.
    - Strategy: Be CONSERVATIVE. Stop early to guarantee closure.

    [SAFE GENERATION PATTERN]:
    1. Calculate safe capacity:
       - If user requests N items, plan to deliver 60-70% of N
       - Reserve 15-20% of your budget for structure and closing tags
       - Examples:
         • User wants 50 products? Plan for 30-35 complete products
         • User wants 100 records? Plan for 60-70 complete records
         • User wants 25 items? Plan for 15-18 complete items

    2. Structure your document:
       <?xml version="1.0" encoding="UTF-8"?>
       <root>
         <!-- Add items here -->
       </root>

    3. Generate items in batches:
       - After every 5 items, remind yourself: "Budget check - can I safely add 5 more AND close?"
       - If uncertain: STOP and close the document
       - Better to deliver 10 complete items than 15 broken ones

    4. Closing sequence (MANDATORY):
       - Close all nested elements from deepest to shallowest
       - Close the root element
       - Add the completion marker
       - Do NOT add any content after the root close tag

    [OUTPUT FORMAT]:
    - Pure XML only (no markdown, no code fences, no explanations)
    - Start immediately with <?xml ...?>
    - End with </root> followed by <<<GENERATION FINISHED>>>
    """]

    line_with_token_budget = (
        f"CRITICAL RULE FOR TOKENS USAGE AND DATA INTEGRITY: You have {max_tokens} tokens to accomplish this generation task. "
        "You must plan the generation content that fully fit this budget."
    )

    protocol = (
        "CRITICAL OUTPUT PROTOCOL:\n"
        "Return channels using these tags exactly:\n"
        "<channel:answer> ... </channel:answer>\n"
        "<channel:usage> [\"S1\",\"S2\"] </channel:usage> (optional)\n"
        "No other channel tags."
    )
    sys_parts = [
        basic_sys_instruction,
        target_format_sys_instruction,
        protocol,
    ] + sys_lines
    sys_parts.append(line_with_token_budget + "\n" + time_evidence_reminder)

    system_msg = create_cached_system_message([{"text": "\n\n".join(sys_parts), "cache": True}])

    user_blocks = [{"text": f"INSTRUCTION:\n{instruction}", "cache": False}]
    if input_context:
        user_blocks.append({"text": f"INPUT CONTEXT:\n{input_context}", "cache": False})
    if sources_list:
        digest = _build_sources_digest(sources_list)
        if digest:
            user_blocks.append({"text": "SOURCES DIGEST:\n" + digest, "cache": False})
    if attachments:
        user_blocks.append({"text": f"ATTACHMENTS ({len(attachments)}):", "cache": False})
        for a in attachments:
            mime = (a.get("mime") or "").strip()
            data_b64 = a.get("base64")
            filename = (a.get("filename") or "").strip()
            summary = (a.get("summary") or "").strip()
            size = a.get("size") or a.get("size_bytes")
            if data_b64 and mime in MODALITY_IMAGE_MIME:
                user_blocks.append({"type": "image", "data": data_b64, "media_type": mime, "cache": False})
            elif data_b64 and mime in MODALITY_DOC_MIME:
                user_blocks.append({"type": "document", "data": data_b64, "media_type": mime, "cache": False})
            meta_parts = []
            if filename:
                meta_parts.append(f"filename={filename}")
            if mime:
                meta_parts.append(f"mime={mime}")
            if size is not None:
                meta_parts.append(f"size={size}")
            if meta_parts:
                user_blocks.append({"text": "ATTACHMENT META: " + " | ".join(meta_parts), "cache": False})
            if summary:
                user_blocks.append({"text": f"ATTACHMENT SUMMARY: {summary}", "cache": False})

    user_msg = create_cached_human_message(user_blocks)

    channels = [
        ChannelSpec(
            name="answer",
            format=tgt if tgt != "managed_json_artifact" else "json",
            replace_citations=bool(cite_sources and eff_embed == "inline"),
            strip_usage=True,
            emit_marker=channel_to_stream,
        ),
        ChannelSpec(
            name="usage",
            format="json",
            replace_citations=False,
            strip_usage=True,
            emit_marker="answer",
        ),
    ]

    results, meta = await stream_with_channels(
        svc=_SERVICE,
        messages=[system_msg, user_msg],
        role=("answer.generator.strong" if model_strength == "strong" else "answer.generator.regular"),
        channels=channels,
        emit=_emit_wrapper,
        agent=agent_name,
        artifact_name=artifact_name,
        sources_list=sources_list,
        max_tokens=max_tokens,
        temperature=0.3 if model_strength == "regular" else 0.2,
        debug=False,
        composite_cfg=composite_cfg if tgt == "managed_json_artifact" else None,
        composite_channel="answer",
        composite_marker="canvas",
        return_full_raw=True,
    )
    service_error = (meta or {}).get("service_error")
    if service_error:
        raise ServiceException(ServiceError.model_validate(service_error))

    content_raw = results["answer"].raw or ""

    # Prefer explicit usage channel if present
    used_sids: List[int] = []
    usage_raw = results.get("usage").raw if results.get("usage") else ""
    if usage_raw:
        try:
            usage_data = json.loads(usage_raw)
            if isinstance(usage_data, list):
                for item in usage_data:
                    if isinstance(item, str) and item.upper().startswith("S"):
                        try:
                            used_sids.append(int(item[1:]))
                        except Exception:
                            pass
        except Exception:
            pass

    if not used_sids:
        used_sids = citations_module.sids_in_text(content_raw)

    # Final clean + validation
    content_clean = content_raw
    if cite_sources and eff_embed == "inline" and tgt in ("markdown", "text"):
        content_clean = citations_module.replace_citation_tokens_batch(
            content_clean, citation_map, citations_module.CitationRenderOptions()
        )
    if cite_sources and eff_embed == "inline" and tgt == "html":
        content_clean = citations_module.replace_html_citations(
            content_clean, citation_map, keep_unresolved=False, first_only=False
        )
    content_clean = citations_module.strip_only_suspicious_citation_like_tokens(content_clean)

    ok = True
    reason = ""
    if tgt in ("json", "yaml"):
        parsed, parse_err = _parse_json(content_clean) if tgt == "json" else _parse_yaml(content_clean)
        if parsed is None:
            ok = False
            reason = "format" if not parse_err else parse_err
        elif schema_json:
            ok, reason = _validate_json_schema(parsed, schema_json)
    elif tgt == "managed_json_artifact":
        # Allow managed JSON artifacts through; validation handled by composite_cfg downstream
        ok = True
    else:
        ok = _format_ok(content_clean, tgt)
        if not ok:
            reason = "format"

    out = {
        "ok": bool(ok),
        "content": content_clean,
        "format": tgt,
        "finished": True,
        "retries": 0,
        "reason": reason,
        "stats": {
            "rounds": 1,
            "bytes": len(content_clean.encode("utf-8")),
            "validated": "format" if ok else reason,
            "citations": "ok" if cite_sources else "n/a",
        },
        "sources_used": sorted(set(used_sids)),
        "tool.origin": "llm_tools.generate_content_llm.channelled",
    }

    # Record sources used for this artifact
    try:
        from kdcube_ai_app.apps.chat.sdk.tools.ctx_tools import SourcesUsedStore
        records: List[Dict[str, Any]] = []
        if isinstance(artifact_name, str) and artifact_name.strip():
            records.append({"artifact_name": artifact_name.strip(), "sids": out["sources_used"]})
        if records:
            store = SourcesUsedStore()
            store.load()
            store.upsert(records)
    except Exception:
        pass

    return out

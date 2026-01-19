# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/with_llm_backends.py

import json
import re
from typing import Annotated, Optional, List, Dict, Any, Tuple, Set, Callable, Awaitable
import logging

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta, get_comm
from kdcube_ai_app.apps.chat.sdk.streaming.artifacts_channeled_streaming import CompositeJsonArtifactStreamer

from kdcube_ai_app.apps.chat.sdk.tools.citations import split_safe_citation_prefix, replace_citation_tokens_streaming, \
    extract_sids, build_citation_map_from_sources, citations_present_inline, adapt_source_for_llm, \
    find_unmapped_citation_sids, USAGE_TAG_RE, _split_safe_usage_prefix, _expand_ids, \
    strip_only_suspicious_citation_like_tokens, split_safe_stream_prefix, _normalize_citation_chars
from kdcube_ai_app.apps.chat.sdk.tools.text_proc_utils import _rm_invis, _remove_end_marker_everywhere, \
    _split_safe_marker_prefix, _remove_marker, _unwrap_fenced_blocks_concat, _strip_bom_zwsp, _parse_json, \
    _extract_json_object, _strip_code_fences, _format_ok, _validate_json_schema, _parse_yaml, _validate_sidecar, \
    _dfs_string_contains_inline_cite, _unwrap_fenced_block, _json_pointer_get, _json_pointer_delete, truncate_text
from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import create_cached_human_message, create_cached_system_message
from kdcube_ai_app.infra.service_hub.multimodality import (
    MODALITY_IMAGE_MIME,
    MODALITY_DOC_MIME,
    MODALITY_MAX_IMAGE_BYTES,
    MODALITY_MAX_DOC_BYTES,
)
from kdcube_ai_app.apps.chat.sdk.util import estimate_b64_size

logger = logging.getLogger("with_llm_backends")

async def generate_content_llm(
        _SERVICE,
        agent_name:  Annotated[str, "Name of this content creator, short, to distinguish this author in the sequence of generative calls."],
        instruction: Annotated[str, "User instruction / prompt. What to produce (goal/contract). "],
        artifact_name: Annotated[
            Optional[str],
            (
                    "Logical name of the artifact being produced (for tracking in logs).\n"
                    "- For normal formats (html|markdown|json|yaml|text), this is just a string label.\n"
                    "- For target_format=\"managed_json_artifact\", this MUST instead be a JSON object\n"
                    "  mapping top-level JSON field names to nested artifact formats, e.g.:\n"
                    "    {\"summary_md\": \"markdown\", \"details_html\": \"html\"}\n"
                    "  Each value must be one of: markdown,text,html,json,yaml,mermaid (xml is NOT supported)."
            )
        ] = "",
        sys_instruction: Annotated[Optional[str], "System instruction."] = "",
        input_context: Annotated[
            Optional[str],
            "Optional text or data to use as a base. Do NOT embed sources here; use sources_list only."
        ] = "",
        target_format: Annotated[str, "html|markdown|json|yaml|text|managed_json_artifact. Must output ONLY that format; no markdown or code fences.",
            {"enum": ["html", "markdown", "mermaid", "json", "yaml", "text", "xml", "managed_json_artifact"]}] = "markdown",        schema_json: Annotated[str,
        "Optional JSON Schema. If provided (and target_format is json|yaml), "
        "the schema is inserted into the prompt and the model MUST produce an output that validates against it."] = "",
        sources_list: Annotated[
            list[dict],
            "List of sources: {sid:int, title:str, url?:str, text:str, content?: str, mime?: str, base64?: str, size_bytes?: int}. "
            "Sources must be passed ONLY here (never inside input_context)."
        ] = None,
        skills: Annotated[
            Optional[List[str]],
            "Optional skills to apply. Use SKx, namespace.skill_id, or skills.namespace.skill_id.",
        ] = None,
        attachments: Annotated[
            list[dict],
            "Optional multimodal attachments; each item should include {mime, base64, filename?, summary?}."
        ] = None,
        cite_sources: Annotated[bool, "If true and sources provided, require citations (inline for Markdown/HTML; sidecar for JSON/YAML)."] = False,
        citation_embed: Annotated[str, "auto|inline|sidecar|none",
        {"enum": ["auto", "inline", "sidecar", "none"]}] = "auto",
        citation_container_path: Annotated[str, "JSON Pointer for sidecar path (json/yaml)."] = "/_citations",
        allow_inline_citations_in_strings: Annotated[bool, "Permit [[S:n]] tokens inside JSON/YAML string fields."] = True,
        # end_marker: Annotated[str, "Completion marker appended by the model at the very end."] = "<<<GENERATION FINISHED>>>",
        max_tokens: Annotated[int, "Per-round token cap.", {"min": 256, "max": 8000}] = 7000,
        thinking_budget: Annotated[int, "Per-round thinking token cap.", {"min": 128, "max": 4000}] = 0,
        max_rounds: Annotated[int, "Max generation/repair rounds.", {"min": 1, "max": 10}] = 4,
        # code_fences: Annotated[bool, "Allow triple-backtick fenced blocks in output."] = True,
        continuation_hint: Annotated[str, "Optional extra hint used on continuation rounds."] = "",
        strict: Annotated[bool, "Require format OK and (if provided) schema OK and citations (if requested)."] = True,
        role: str = "tool.generator.strong",
        cache_instruction: bool=True,
        channel_to_stream: Optional[str]="canvas",
        temperature: float=0.2,
        on_delta_fn = None,
        on_thinking_fn = None,
        infra_call: bool = False,
        include_url_in_source_digest: bool = False
) -> Annotated[dict, 'Object: {ok, content, format, finished, retries, reason, stats, sources_used: [sid, ...]}']:
    """
    Returns object:
      {
        "ok": true/false,
        "content": "<final text>",
        "format": "<target_format>",
        "finished": true/false,        # saw end_marker
        "retries": <int>,              # rounds used - 1
        "reason": "<last failure reason or ''>",
        "stats": { "rounds": n, "bytes": len(content), "validated": "format|schema|both|none", "citations": "present|missing|n/a" },
        "service_error": { ... }        # present only on streaming failure
      }
    """

    code_fences: Annotated[bool, "Allow triple-backtick fenced blocks in output."] = True

    from langchain_core.messages import SystemMessage, HumanMessage
    from kdcube_ai_app.infra.accounting import _get_context

    context = _get_context()
    context_snapshot = context.to_dict()
    logger.warning(f"[Context snapshot]:\n{context_snapshot}")

    rep_author = agent_name or "Content Generator LLM"
    track_id = context_snapshot.get("track_id")
    bundle_id = context_snapshot.get("app_bundle_id")
    timezone = context_snapshot.get("timezone")

    today = _today_str()
    now = _now_up_to_minutes()

    TIMEZONE = timezone or "Europe/Berlin"
    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        f"Current UTC date: {today}\n"
        # "User timezone: Europe/Berlin\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    time_evidence_reminder = f"Very important: The user's timezone is {TIMEZONE}. Current UTC timestamp: {now}. Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future. When dealing with modern entities/companies/people, and the user asks for the 'latest', 'most recent', 'today's', etc. don't assume your knowledge is up to date; you MUST carefully confirm what the true 'latest' is first. If the user seems confused or mistaken about a certain date or dates, you MUST include specific, concrete dates in your response to clarify things. This is especially important when the user is referencing relative dates like 'today', 'tomorrow', 'yesterday', etc -- if the user seems mistaken in these cases, you should make sure to use absolute/exact dates like 'January 1, 2010' in your response.\n"

    artifact_name = artifact_name or agent_name

    # --------- normalize inputs ---------
    raw_tgt = (target_format or "markdown").lower().strip()
    is_managed_json = raw_tgt == "managed_json_artifact"

    if raw_tgt not in ("html", "markdown", "mermaid", "json", "yaml", "text", "xml", "managed_json_artifact"):
        tgt = "markdown"
    else:
        tgt = raw_tgt

    # JSON-like = normal json/yaml OR managed_json_artifact (top-level JSON envelope)
    is_json_like = (tgt in ("json", "yaml")) or is_managed_json

    # For managed_json_artifact: artifact_name is a JSON object mapping
    # top-level keys -> nested formats.
    composite_cfg: Optional[Dict[str, str]] = None  # key -> format

    if is_managed_json and artifact_name:
        try:
            # support both dict *and* JSON string
            if isinstance(artifact_name, dict):
                cfg_obj = artifact_name
            else:
                cfg_obj = json.loads(artifact_name)

            if isinstance(cfg_obj, dict):
                tmp: Dict[str, str] = {}
                for k, v in cfg_obj.items():
                    key = str(k).strip()
                    if not key:
                        continue
                    fmt = str(v or "").lower().strip() or "markdown"
                    tmp[key] = fmt

                # Validate nested formats; XML is intentionally not supported
                allowed_nested_formats = {"markdown", "text", "html", "json", "yaml", "mermaid"}
                filtered: Dict[str, str] = {}
                for k, fmt in tmp.items():
                    if fmt not in allowed_nested_formats:
                        logger.warning(
                            "generate_content_llm: dropping nested artifact '%s' with unsupported format '%s' "
                            "(allowed: %s)",
                            k, fmt, ", ".join(sorted(allowed_nested_formats)),
                        )
                        continue
                    filtered[k] = fmt

                if filtered:
                    composite_cfg = filtered
        except Exception:
            logger.exception(
                "generate_content_llm: failed to parse managed_json_artifact config from artifact_name"
            )
            composite_cfg = None

    # For non-managed / failed parsing, keep old behaviour: artifact_name stays a simple label
    if not composite_cfg:
        artifact_name = artifact_name or agent_name
    else:
        # Ensure artifact_name is a sane label for the *envelope* artifact.
        if not isinstance(artifact_name, str):
            artifact_name = agent_name or "managed_json_artifact"


    if max_tokens < 5500:
        if tgt in ("html", "xml"):
            max_tokens = 5500
    if sources_list is None:
        sources_list = []
    if not isinstance(sources_list, list):
        raise ValueError("sources_list must be a list of dicts")

    def _collect_multimodal_source_attachments(src_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        seen_mime: Set[str] = set()
        for src in src_list or []:
            if not isinstance(src, dict):
                continue
            mime = (src.get("mime") or "").strip().lower()
            data_b64 = src.get("base64")
            if not mime or not data_b64:
                continue
            if mime not in MODALITY_IMAGE_MIME and mime not in MODALITY_DOC_MIME:
                continue
            if mime in seen_mime:
                continue
            size_bytes = src.get("size_bytes")
            if size_bytes is None:
                size_bytes = estimate_b64_size(data_b64)
            if size_bytes is None:
                continue
            limit = MODALITY_MAX_IMAGE_BYTES if mime in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
            if size_bytes > limit:
                continue
            summary = ""
            sid = src.get("sid")
            title = (src.get("title") or "").strip()
            if sid or title:
                summary = f"source sid={sid} title={title}".strip()
            collected.append({
                "mime": mime,
                "base64": data_b64,
                "filename": src.get("filename") or "",
                "summary": summary,
                "size_bytes": size_bytes,
            })
            seen_mime.add(mime)
            if len(collected) >= 2:
                break
        return collected

    if attachments is None:
        attachments = []
    elif not isinstance(attachments, list):
        raise ValueError("attachments must be a list of dicts")

    source_attachments = _collect_multimodal_source_attachments(sources_list)
    if source_attachments:
        attachments = attachments + source_attachments

    def _looks_like_sources_payload(val: Any) -> bool:
        if not isinstance(val, list) or not val:
            return False
        score = 0
        for row in val[:5]:
            if not isinstance(row, dict):
                continue
            has_sid = isinstance(row.get("sid"), int)
            has_url = isinstance(row.get("url"), str)
            has_text = isinstance(row.get("text"), str) or isinstance(row.get("content"), str)
            has_title = isinstance(row.get("title"), str)
            if has_sid or has_url:
                score += 1
            if has_text or has_title:
                score += 1
        return score >= 2

    if sources_list and isinstance(input_context, str) and input_context.strip():
        ic_strip = input_context.strip()
        if ic_strip.startswith(("[", "{")):
            try:
                parsed_ic = json.loads(ic_strip)
                if _looks_like_sources_payload(parsed_ic):
                    logger.warning(
                        "generate_content_llm: input_context looks like sources payload; "
                        "dropping it to avoid duplication."
                    )
                    input_context = ""
            except Exception:
                pass

    sids = extract_sids(sources_list)
    have_sources = bool(sids)

    end_marker: Annotated[str, "Completion marker appended by the model at the very end."] = "<<<GENERATION FINISHED>>>"

    def _scrub_emit_once(s: str) -> str:
        if not s:
            return s

        # Normalize invisibles first so regexes see a cleaner pattern
        s = _rm_invis(s)
        # Normalize full-width brackets/colons so USAGE tokens match
        s = _normalize_citation_chars(s)

        # 1) Strip the exact completion marker (can appear multiple times)
        s = _remove_end_marker_everywhere(s, end_marker)

        # 2) Strip any USAGE tags using the robust regex from citations.py
        s = USAGE_TAG_RE.sub("", s)

        # 3) Belt-and-suspenders fallback:
        #    In case the model produced something extremely odd that still
        #    looks like [[USAGE:...]] but doesn't match USAGE_TAG_RE,
        #    we nuke it with a very permissive pattern.
        if "[[USAGE" in s.upper():
            s = re.sub(
                r"\[\[\s*USAGE\s*:.*?\]\]",
                "",
                s,
                flags=re.I | re.S,
            )

        return s

    # Local safe-stop guard for taggy formats
    def _trim_to_last_safe_tag_boundary(s: str) -> str:
        if not s:
            return s
        # best-effort: cut to last '>' to avoid dangling <tag
        i = s.rfind(">")
        return s[:i+1] if i != -1 else s

    # --------- system prompt (format + citation rules) ---------
    if infra_call:
        # We can safely skip a lot of global, heavy rules
        basic_sys_instruction = (
            "You are a focused JSON generator for internal tools. "
            "Return only JSON in the requested schema; no explanations."
        )
        # Disable citations entirely
        require_citations = False
        cite_sources = False
        eff_embed = "none"
    else:
        # auto embedding policy
        eff_embed = citation_embed
        if citation_embed == "auto":
            if tgt in ("markdown", "text"):
                eff_embed = "inline"
            elif tgt == "html":
                eff_embed = "inline"
            elif is_json_like: # json, yaml, managed_json_artifact
                eff_embed = "sidecar"
            elif tgt == "xml":
                eff_embed = "none"  # XML has no citation protocol here
            else:
                eff_embed = "none"

        # XML should not demand citations (no inline protocol defined); disable even if user set cite_sources=True
        require_citations = bool(cite_sources and have_sources and eff_embed != "none")

        basic_sys_instruction = "\n".join([
            "You are a precise generator. Produce ONLY the requested artifact in the requested format.",
            "If [ACTIVE SKILLS] are present, they are dominant. Follow them over any general guidance here.",
            "NEVER include meta-explanations. Do not apologize. No prefaces. No trailing notes.",
            "You must produce content exactly of format stated in [TARGET GENERATION FORMAT]. No any deviations!"
            # "If continuing, resume exactly where you left off.",
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
    basic_sys_instruction += "\n" + time_evidence
    target_format_sys_instruction = f"[TARGET GENERATION FORMAT]: {tgt}"

    sys_lines = []
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
       - DO NOT add any content after the root closing tag
    
    [VALID XML REQUIREMENTS]:
    - Every opening tag has a closing tag: <item>...</item>
    - Proper nesting: <outer><inner></inner></outer>
    - Attributes quoted: <item id="123">
    - No orphaned tags
    - Special characters escaped: &lt; &gt; &amp; &quot; &apos;
    
    [OUTPUT FORMAT]:
    - Pure XML only (no markdown, no code fences, no explanations)
    - Start immediately with <?xml or <root>
    - End with </root> followed by <<<GENERATION FINISHED>>>
    - No apologetic messages like "Due to space constraints..."
    
    [EXAMPLES OF SAFE SCALING]:
    - Request: "100 book entries" → Deliver: 60-70 complete entries
    - Request: "50 customer records" → Deliver: 30-35 complete records
    - Request: "25 configuration items" → Deliver: 15-18 complete items
    - Request: "10 detailed reports" → Deliver: 6-7 complete reports
    
    [FAILURE MODES TO AVOID]:
    ❌ <items><item>...</item><item>...  [TRUNCATED - no closing </items>]
    ❌ <items><item>...</item></items>Here's what I generated...  [TEXT AFTER ROOT]
    ❌ <items><item id="5  [ATTRIBUTE NOT CLOSED]
    ✅ <items><item id="1">...</item><item id="2">...</item></items><<<GENERATION FINISHED>>>
    
    REMEMBER: Quality over quantity. Valid XML with fewer items > Invalid XML with more items.
    """]

    # Citation rules  and not infra_call
    if sources_list:
        sys_lines += [
            "",
            "[SOURCE & CONTEXT USAGE POLICY]:",
            "- Always base factual and numeric claims on the most relevant parts of the provided input_context and sources.",
            "- Within long texts, prefer sections that clearly match the user’s question (e.g. headings or surrounding text mentioning the same entity, product, feature, timeframe, or metric).",
            "- Ignore clearly irrelevant or off-topic fragments, even if they appear earlier in the text.",
            "",
            "[WHEN MULTIPLE SOURCES OR NUMBERS CONFLICT]:",
            "- If multiple sources give different numeric values (e.g. price, limit, count, metric):",
            "  - Prefer values that are more recent when date metadata is available (e.g. modified_time_iso over published_time_iso).",
            "  - Prefer values from sources with higher authority / objective_relevance when such metadata is present.",
            "  - Prefer values that are explicitly marked as current (e.g. 'current price', 'as of <date>', 'latest plan'), rather than older historical examples.",
            "- If you still cannot confidently choose a single value, mention that there is disagreement and show the key alternatives instead of silently picking one.",
            "",
            "[CURRENCY, UNITS & NUMERIC TRANSFORMATIONS]:",
            "- Do NOT silently convert currencies or units (e.g. EUR→USD, km→miles, hours→minutes).",
            "- If the question asks for a currency/unit that does NOT appear in the sources, answer using the units actually present and clearly label them (e.g. 'The sources only specify prices in EUR: 49.90 EUR').",
            "- Do NOT apply exchange rates or similar numeric transformations unless the user explicitly permits approximate conversions; even then, prefer to explain the limitation instead of computing new numbers.",
            "- Do NOT normalize or scale numbers (e.g. monthly→yearly total, per-user→per-100-users) unless that exact transformation is explicitly given in a source.",
            "",
            "[EXPIRATION & STALENESS (WHEN METADATA EXISTS)]:",
            "- If a source has an 'expiration' timestamp and it is in the past, treat its numeric values as stale. Use them only if there is no fresher alternative and say that they may be outdated.",
            "- When both published_time_iso and modified_time_iso are available, treat modified_time_iso as the best indicator of freshness.",
            "",
            "[METADATA-AWARE PRIORITISATION]:",
            "- When source metadata such as provider, published_time_iso, modified_time_iso, expiration, objective_relevance, query_relevance, authority is available (in the source description, context, or content):",
            "  - Prefer higher authority and objective_relevance scores when sources disagree.",
            "  - Prefer more recent modified_time_iso / published_time_iso for time-sensitive facts like prices or availability.",
            "  - Treat obviously low-authority or generic boilerplate sources as secondary, even if they are recent.",
        ]
    if require_citations:
        allowed_sids_line = f"ALLOWED SIDS: {', '.join(str(x) for x in sorted(sids))}" if sids else "ALLOWED SIDS: (none)"
        strict_source_boundary = [
            "",
            "[STRICT SOURCE BOUNDARY]:",
            "- You may ONLY cite sources whose SID is listed below.",
            "- NEVER invent or reference any SID not listed.",
            "- If a claim cannot be supported by the provided sources, either omit the claim or present it without a citation.",
            "- When several fragments within a source mention numbers, prefer the fragment that directly answers the user’s request (for example the latest 'Price', 'Current', or 'As of <date>' section) rather than generic or historical examples.",
            "- If a numeric value is shown in a table and a different one appears in free text, prefer whichever is explicitly marked as current or more recent.",
            allowed_sids_line,
        ]

        if tgt in ("markdown", "text") and eff_embed == "inline":
            sys_lines += [
                "",
                "[CITATION REQUIREMENTS (MARKDOWN/TEXT)]:",
                "- Insert [[S:<sid>]] tokens at the end of sentences/bullets that contain NEW or materially CHANGED factual claims.",
                "- Keep citations balanced: if consecutive sentences or bullets rely on the same source(s), cite once at the end of the block instead of tagging every line.",
                "- Avoid per-line citation spam; prefer one citation per coherent paragraph/section when the source set is unchanged.",
                "- Multiple sources allowed: [[S:1,3]] for enumeration and [[S:2-4]] for inclusive range. Use only the provided sid values. Never invent.",
                "",
                "[CODE BLOCK CITATION RULES]:",
                "- NEVER place citation tokens inside code fences (```) of any kind.",
                "- ESPECIALLY: Do NOT put citations inside Mermaid diagrams, JSON blocks, YAML blocks, or any other fenced code.",
                "- Instead, place citations in the explanatory prose BEFORE or AFTER the code block.",
            ]
            sys_lines += strict_source_boundary
        elif tgt == "html" and eff_embed == "inline":
            sys_lines += [
                "",
                "[CITATION REQUIREMENTS (HTML)]:",
                '- Insert <sup class="cite" data-sids="1,3">[S:1,3]</sup> immediately after the sentence/phrase introducing NEW or materially CHANGED facts.',
                "- Use only provided sid values. Never invent.",
            ]
            sys_lines += strict_source_boundary
        elif is_json_like and eff_embed == "sidecar":
            sys_lines += [
                "",
                "[CITATION REQUIREMENTS (STRUCTURED, SIDECAR)]:",
                f'- You MUST provide a sidecar array at JSON Pointer "{citation_container_path}" with objects:',
                '  { "path": "<JSON Pointer to the string field containing the claim>", "sids": [<sid>, ...] }',
                "- 'path' MUST point to an existing string field in the returned document.",
            ]
            if not allow_inline_citations_in_strings:
                sys_lines += [
                    "- Do NOT put citation tokens [[S:n]] inside the main payload string fields.",
                ]
                sys_lines += strict_source_boundary
            else:
                sys_lines += [
                    "- You MAY also put inline tokens [[S:n]] inside string fields in the main payload,",
                    "  but the sidecar is still required as the primary citation channel.",
                ]
                sys_lines += strict_source_boundary


        sys_lines+= [
            (
                "[SOURCE RELEVANCE POLICY]\n"
                "- Only use sources that directly support the requested topic and the claims you make.\n"
                "- If source is off-topic (different domain/subject, spammy listing, irrelevant brand/news), exclude it from reasoning and citations.\n"
                "- Do not cite a source unless it substantively supports the sentence you attach it to.\n"
                "- Prefer primary/official documents and regulator/standards bodies over tertiary blogs or low-quality aggregators.\n"
                "- If most results are off-topic, state this briefly and proceed without those sources (or ask for better sources in minimal wording).\n"
                "- Keep sources_list restricted to sources actually used for claims; never force citations just to satisfy a requirement.\n"
            )
        ]

    # end marker rule
    sys_lines += [
        "",
        f"COMPLETION: End your output with the exact marker: {end_marker}",
        "Do not add any text after the marker."
    ]

    have_sources = bool(sids)
    if have_sources and not require_citations:
        sys_lines += [
            "",
            "[USAGE TELEMETRY (INVISIBLE TO USER)]:",
            "- If sources are provided but citations are not required, you MUST record which source IDs you actually relied on.",
            "- Do this by inserting a single line `[[USAGE:<sid(s)>]]` immediately BEFORE the completion marker.",
            "- Example: [[USAGE:1,3,5]]",
            "- Do NOT add any other commentary around it.",
        ]
    if have_sources and require_citations:
        sys_lines += [
            "",
            "[USAGE TELEMETRY (INVISIBLE TO USER)]:",
            "- - Since sources are provided (and citations are required), you MUST also record which source IDs you actually relied on.",
            "- Do this by inserting a single line `[[USAGE:<sid(s)>]]` immediately BEFORE the completion marker.",
            "- Example: [[USAGE:1,3,5]]",
            "- Do NOT add any other commentary around it.",
        ]
    schema_text_for_prompt = ""
    if schema_json:
        logger.warning(f"schema_json={schema_json} provided. target_format={tgt}")
        if tgt in ("json", "yaml"):
            try:
                # Pretty + bounded length so we don't blow the context
                _schema_obj = json.loads(schema_json)
                schema_text_for_prompt = json.dumps(_schema_obj, ensure_ascii=False, indent=2)
                # Optional: hard cap to avoid massive schemas
                if len(schema_text_for_prompt) > 6000:
                    schema_text_for_prompt = schema_text_for_prompt[:6000] + "\n/* …truncated for prompt… */"
            except Exception:
                # If the provided schema isn't valid JSON, don't stop; just skip embedding
                schema_text_for_prompt = ""
        else:
            logger.warning(f"schema_json={schema_json} provided but target_format={tgt}; schema will not be enforced.")

    if schema_text_for_prompt:
        sys_lines += [
            "",
            "[SCHEMA CONFORMANCE (MANDATORY)]:",
            "- You MUST return output that VALIDATES against the following JSON Schema.",
            "- Do not add commentary outside the structured document.",
            "- Do not invent fields not permitted by the schema.",
            "- Omit optional fields rather than inventing values.",
            "",
            "JSON SCHEMA (authoritative):",
            schema_text_for_prompt
        ]

    # Build minimal sources digest and sid map
    sid_map = ""
    digest = ""
    rows: List[Dict[str, Any]] = []

    if have_sources:
        try:
            raw_sources = sources_list or []
        except Exception:
            raw_sources = []

        for s in raw_sources or []:
            if not isinstance(s, dict):
                continue
            norm = adapt_source_for_llm(s, include_full_content=True, max_text_len=-1)
            if not norm:
                continue
            rows.append(norm)
        if include_url_in_source_digest:
            sid_map = "\n".join([f"- {r['sid']}: {truncate_text(r['title'], 160)} ({r.get('url')})" for r in rows])
        else:
            sid_map = "\n".join([f"- {r['sid']}: {truncate_text(r['title'], 160)}" for r in rows])
        total_budget = 10000
        per = max(600, total_budget // max(1, len(rows))) if rows else 0
        parts = []
        for r in rows:
            body = r.get("content") or r.get("text") or ""
            # t = body[:per]
            t = body
            parts.append(f"[sid:{r['sid']}] {r['title']}\n{t}".strip())
        digest = "\n\n---\n\n".join(parts)# [:total_budget]

    sys_prompt = "\n".join(sys_lines)

    skills_block = ""
    if skills:
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
                build_skill_short_id_map,
                import_skillset,
                build_skills_instruction_block,
            )
            short_map = build_skill_short_id_map(consumer=role)
            normalized = import_skillset(skills, short_id_map=short_map)
            skills_block = build_skills_instruction_block(
                normalized,
                variant="full",
                header="ACTIVE SKILLS",
            )
        except Exception as e:
            logger.warning("Failed to load skills for llm generator: %s", e)

    line_with_token_budget = f"CRITICAL RULE FOR TOKENS USAGE AND DATA INTEGRITY: You have {max_tokens} tokens to accomplish this generation task. You must plan the generation content that fully fit this budget."
    if tgt in ("html", "xml", "json", "yaml"):
        line_with_token_budget += " Your output must pass the format validation."


    system_msg_blocks = [
        {"text": basic_sys_instruction, "cache": True},
        # {"text": target_format_sys_instruction, "cache": True},
        {"text": target_format_sys_instruction + "\n" + sys_prompt, "cache": True},
    ]
    if skills_block:
        sys_instruction = "\n\n".join([s for s in [sys_instruction, skills_block] if s])
    if sys_instruction:
        system_msg_blocks.append({"text": sys_instruction, "cache": True})
    system_msg_blocks.append({"text": line_with_token_budget + "\n" + time_evidence_reminder, "cache": False})
    system_msg = create_cached_system_message(system_msg_blocks)

    # --------- streaming infra (shared between main & repair) ---------
    buf_all: List[str] = []
    finished = False
    reason = ""
    used_rounds = 0

    emitted_count = 0  # global index for all emitted chunks in this call

    # Build citation map once (we’ll only use it if tgt == "markdown")
    citation_map = build_citation_map_from_sources(sources_list)

    def _should_replace_citations_in_stream() -> bool:
        if not citation_map:
            return False
        # Always for markdown / plain text
        if tgt in ("markdown", "text"):
            return True
        # For HTML outputs we also want inline replacements
        if tgt == "html":
            return True
        # For structured formats (json/yaml/managed_json_artifact),
        # only when we explicitly allow inline markers in strings.
        if is_json_like and allow_inline_citations_in_strings:
            return True
        # XML stays citation-free unless you later want to change that
        return False
    replace_in_stream = _should_replace_citations_in_stream()

    async def _stream_round(
            messages,
            role_name: str,
            temperature: float,
            max_toks: int,
            thinking_budget: int,
            author_for_chunks: str,
            author_for_complete: str,
            start_index: int,
    ) -> tuple[str, int, Optional[dict]]:
        """
        Shared streaming helper used for both main generation and repair.
        Returns (raw_text, emitted_chunks_count).
        """
        client = _SERVICE.get_client(role_name)
        cfg = _SERVICE.describe_client(client, role=role_name)

        round_buf: List[str] = []   # RAW text from this round
        pending = ""                # tail buffer not yet emitted
        emitted_local = 0
        thinking_idx = 0  # ← ADD THIS: track thinking index

        composite_streamer: Optional[CompositeJsonArtifactStreamer] = None
        if (
                is_managed_json
                and composite_cfg
                and channel_to_stream == "canvas"
                and get_comm()
        ):
            composite_streamer = CompositeJsonArtifactStreamer(
                artifacts_cfg=composite_cfg,
                citation_map=citation_map,
                channel=channel_to_stream,
                agent=author_for_chunks,
                emit_delta=emit_delta,
                on_delta_fn=on_delta_fn,
            )

        async def _emit_visible(text: str):
            nonlocal emitted_local
            if not text:
                return
            clean = _scrub_emit_once(text)
            out = replace_citation_tokens_streaming(clean, citation_map) if replace_in_stream else clean
            # 🔸 Do NOT stream raw JSON for composite artifacts to canvas
            idx = start_index + emitted_local
            if is_managed_json and composite_cfg and channel_to_stream == "canvas":
                # still allow on_delta_fn to observe raw stream if needed
                if on_delta_fn:
                    await on_delta_fn(
                    text=out,
                    index=idx,
                    marker=channel_to_stream,
                    agent=author_for_chunks,
                    format=tgt or "markdown",
                    artifact_name=artifact_name,
                )
                return
            if get_comm():
                from kdcube_ai_app.apps.chat.sdk.tools.citations import debug_only_suspicious_tokens
                suspicious = debug_only_suspicious_tokens(out)
                if suspicious:
                    logger.warning("Unreplaced citation-like tokens in emitted chunk: %s", suspicious)
                if re.search(r"\[\[\s*S\s*$", _rm_invis(out), re.I):
                    logger.warning("About to emit a dangling citation prefix: %r", out[-80:])
                await emit_delta(out, index=idx, marker=channel_to_stream,
                                 agent=author_for_chunks, format=tgt or "markdown",
                                 artifact_name=artifact_name)
                if on_delta_fn:
                    await on_delta_fn(
                        text=out,
                        index=idx,
                        marker=channel_to_stream,
                        agent=author_for_chunks,
                        format=tgt or "markdown",
                        artifact_name=artifact_name,
                    )
                emitted_local += 1

        async def _flush_pending(force: bool = False):
            nonlocal pending
            if not pending:
                return

            if force:
                # still protect against unfinished [[S... or [[USAGE...
                safe_chunk, _ = split_safe_stream_prefix(pending)
                safe_chunk, _ = _split_safe_marker_prefix(safe_chunk, end_marker)

                if safe_chunk:
                    await _emit_visible(safe_chunk)
                # Drop any dangling tail silently
                pending = ""
                return

            safe_chunk, _ = split_safe_stream_prefix(pending)
            safe_chunk, _ = _split_safe_marker_prefix(safe_chunk, end_marker)

            if not safe_chunk:
                return

            await _emit_visible(safe_chunk)
            pending = pending[len(safe_chunk):]

        async def on_delta(piece: str):
            nonlocal pending
            if not piece:
                return
            round_buf.append(piece)
            if composite_streamer is not None:
                # Feed raw JSON chunk to composite scanner
                await composite_streamer.feed(piece)
            else:
                pending += piece
                await _flush_pending(force=False)

        async def on_thinking(out):
            nonlocal thinking_idx
            if not out:
                return
            if on_thinking_fn:
                tt = out.get("text")
                if tt:
                    await on_thinking_fn(
                        text=tt,
                        index=thinking_idx,
                        marker="thinking",
                        agent=author_for_chunks,
                        format="markdown",
                        artifact_name=artifact_name,  # or could use a separate thinking artifact name
                        completed=False
                    )
                    thinking_idx += 1


        async def on_complete(_):
            nonlocal emitted_local

            if composite_streamer is not None:
                # Finalize composite artifacts (may flush trailing text)
                await composite_streamer.finish()
                # no raw JSON completion to canvas for composite
                if on_delta_fn:
                    await on_delta_fn(
                        text="",
                        index=start_index + emitted_local,
                        marker=channel_to_stream,
                        agent=author_for_complete,
                        format=tgt or "markdown",
                        artifact_name=artifact_name,
                        completed=True
                    )
                return
            # Flush everything that’s left (including any last complete citation tokens)
            await _flush_pending(force=True)
            if get_comm():
                idx = start_index + emitted_local
                emitted_local += 1
                await emit_delta("", completed=True, index=idx, marker=channel_to_stream,
                                 agent=author_for_complete, format=tgt or "markdown",
                                 artifact_name=artifact_name)
                if on_delta_fn:
                    await on_delta_fn(
                        text="",
                        index=idx,
                        marker=channel_to_stream,
                        agent=author_for_complete,
                        format=tgt or "markdown",
                        artifact_name=artifact_name,
                        completed=True
                    )


        async with with_accounting(
                bundle_id,
                track_id=track_id,
                agent=role_name,
                artifact_name=artifact_name,
                metadata={
                    "track_id": track_id,
                    "agent": role_name,
                    "agent_name": agent_name,
                    "artifact_name": artifact_name,
                }
        ):
            ret = await _SERVICE.stream_model_text_tracked(
                client,
                messages=messages,
                on_delta=on_delta,
                on_thinking=on_thinking,
                on_complete=on_complete,
                temperature=temperature,
                max_tokens=max_toks,
                client_cfg=cfg,
                role=role_name,
                max_thinking_tokens=thinking_budget,
                debug_citations=True,
            )
            thoughts = ret.get("thoughts") or ""
            text =  ret.get("text") or ""
            logger.info(f"Completed streaming round: thoughts:\n{thoughts}")
            logger.info(f"text:\n{text}")
            svc_error = ret.get("service_error")

        return "".join(round_buf), emitted_local, svc_error

    def _build_user_blocks_for_round(round_idx: int) -> List[dict]:
        """
        Compose the HumanMessage as Anthropic-friendly blocks.
        The instruction is ALWAYS included, and is cacheable when cache_instruction=True.
        Non-instruction blocks are not cached (they can vary per round).
        """
        blocks: List[dict] = []

        # 1) Instruction (ALWAYS include; cache if requested)
        blocks.append({"text": f"INSTRUCTION:\n{instruction}", "cache": bool(cache_instruction)})

        # 2) Stable metadata for the task (non-cached)
        blocks.append({"text": f"TARGET FORMAT: {tgt}", "cache": False})

        # 3) Input context (may be large; non-cached, truncated once here)
        if input_context:
            # blocks.append({"text": f"INPUT CONTEXT:\n{input_context[:12000]}", "cache": False})
            blocks.append({"text": f"INPUT CONTEXT:\n{input_context}", "cache": False})

        # 3.5) Attachments (multimodal inputs)
        if attachments:
            if round_idx == 0:
                blocks.append({"text": f"ATTACHMENTS ({len(attachments)}):", "cache": False})
                for a in attachments:
                    if not isinstance(a, dict):
                        continue
                    mime = (a.get("mime") or "").strip()
                    data_b64 = a.get("base64")
                    filename = (a.get("filename") or "").strip()
                    summary = (a.get("summary") or "").strip()
                    size = a.get("size") or a.get("size_bytes")
                    if data_b64 and mime in MODALITY_IMAGE_MIME:
                        blocks.append({"type": "image", "data": data_b64, "media_type": mime, "cache": False})
                    elif data_b64 and mime in MODALITY_DOC_MIME:
                        blocks.append({"type": "document", "data": data_b64, "media_type": mime, "cache": False})
                    elif data_b64 and mime:
                        logger.warning("generate_content_llm: skipping unsupported attachment mime=%s", mime)
                        continue
                    meta_parts = []
                    if filename:
                        meta_parts.append(f"filename={filename}")
                    if mime:
                        meta_parts.append(f"mime={mime}")
                    if size is not None:
                        meta_parts.append(f"size={size}")
                    meta_line = " | ".join(meta_parts)
                    if meta_line:
                        blocks.append({"text": f"ATTACHMENT META: {meta_line}", "cache": False})
                    if summary:
                        blocks.append({"text": f"ATTACHMENT SUMMARY: {summary}", "cache": False})
            else:
                blocks.append({"text": "Remember the ATTACHMENTS from earlier in this turn.", "cache": False})

        # 4) Sources (sid map + digest), non-cached
        if rows:
            if round_idx == 0:
                if sid_map:
                    blocks.append({"text": f"SOURCE IDS:\n{sid_map}", "cache": False})
                if digest:
                    blocks.append({"text": f"SOURCES DIGEST:\n{digest}", "cache": False})
            else:
                blocks.append({"text": "Remember the SOURCE IDS and DIGEST from earlier in this turn.", "cache": False})
                if require_citations:
                    blocks.append({"text": "Remember the CITATION REQUIREMENTS.", "cache": False})

        if round_idx > 0:
            produced_so_far = "".join(buf_all)[-20000:]
            if produced_so_far:
                cont_hint = continuation_hint or "Continue exactly from where you left off."
                blocks.append({"text": cont_hint, "cache": False})
                blocks.append({"text": "YOU ALREADY PRODUCED (partial, do not repeat):", "cache": False})
                blocks.append({"text": produced_so_far, "cache": False})
                blocks.append({"text": f"Resume and complete the {tgt.upper()} output. Append, do not restart.", "cache": False})

        return blocks

    # --------- main generation rounds (still supports multi-round for text/markdown/mermaid) ---------
    effective_max_rounds = 1 if (tgt in ("json", "yaml", "html", "xml") or is_managed_json) else max_rounds
    logger.warning(f"Effective max rounds={effective_max_rounds}; format={tgt}")

    role = role or "tool.generator"

    for round_idx in range(effective_max_rounds):
        used_rounds = round_idx + 1

        user_blocks = _build_user_blocks_for_round(round_idx)
        human_msg = create_cached_human_message(user_blocks, cache_last=False)

        author_for_chunks = agent_name or "Content Generator LLM"

        chunk, emitted_inc, svc_error = await _stream_round(
            messages=[system_msg, human_msg],
            role_name=role,
            temperature=temperature,
            max_toks=max_tokens,
            author_for_chunks=author_for_chunks,
            author_for_complete=rep_author,
            start_index=emitted_count,
            thinking_budget=thinking_budget
        )
        emitted_count += emitted_inc
        buf_all.append(chunk)
        if svc_error:
            out = {
                "ok": False,
                "content": "",
                "format": tgt,
                "finished": False,
                "retries": max(0, used_rounds - 1),
                "reason": f"service_error: {svc_error.get('message') or svc_error}",
                "stats": {
                    "rounds": used_rounds,
                    "bytes": 0,
                    "validated": "none",
                    "citations": "n/a",
                    "service_error": svc_error,
                },
                "sources_used": [],
                "service_error": svc_error,
            }
            logger.error(
                "generate_content_llm: service_error during streaming round=%s role=%s error=%s",
                used_rounds,
                role,
                svc_error,
            )
            return out

        cumulative = "".join(buf_all)
        if end_marker in cumulative:
            finished = True
            break

    # -------- post-processing / validation --------
    content_raw = "".join(buf_all)
    # Normalize invisible chars so marker / usage regexes are robust
    content_raw = _rm_invis(content_raw)

    # --- usage tag extraction (from the RAW buffer that still has the tag) ---
    usage_sids: List[int] = []
    m_usage = USAGE_TAG_RE.search(_normalize_citation_chars(content_raw or ""))
    if m_usage:
        try:
            ids_str = m_usage.group(1) or ""
            # reuse the same logic as [[S:...]] tokens
            usage_sids = _expand_ids(ids_str)
        except Exception:
            logger.exception("Failed to parse USAGE tag: %r", m_usage.group(0))
        # remove the usage tag from the content BEFORE we do any more cleaning
        content_raw = USAGE_TAG_RE.sub("", content_raw)

    # normalize & de-dup
    usage_sids = sorted(set(usage_sids))

    # If we never saw marker but have something, proceed to validate anyway
    reason = "finished_with_marker" if finished else "no_end_marker"

    # Remove the marker early
    content_raw = _remove_marker(content_raw, end_marker)

    # Unwrap/clean
    if tgt in ("json", "yaml", "xml", "html") or is_managed_json:
        lang = "json" if is_managed_json else tgt
        stitched = _unwrap_fenced_blocks_concat(content_raw, lang=lang)
        content_clean = _strip_bom_zwsp(stitched)

        if tgt == "json":
            obj_probe, err_probe = _parse_json(content_clean)
            if obj_probe is None:
                alt = _extract_json_object(content_raw)
                if alt:
                    content_clean = _strip_bom_zwsp(alt)
    elif tgt == "mermaid":
        content_clean = _unwrap_fenced_blocks_concat(content_raw, lang="mermaid")
        content_clean = _strip_bom_zwsp(content_clean)
        content_clean = re.sub(r'^```(?:mermaid)?\s*\n?', '', content_clean, flags=re.I)
        content_clean = re.sub(r'\n?```\s*$', '', content_clean)
    else:
        content_clean = _strip_code_fences(content_raw, allow=code_fences)
        content_clean = _strip_bom_zwsp(content_clean)

    if tgt in ("json", "yaml", "xml", "html", "mermaid") or is_managed_json:
        content_clean = _strip_code_fences(content_clean, allow=False).strip()

    if tgt == "html" and content_clean.lstrip().startswith(("<?xml", "<xsl:")):
        tgt = "xml"

    if tgt in ("html", "xml"):
        if tgt == "html":
            m = re.search(r"(?is)(<!DOCTYPE\\s+html|<html\\b)", content_clean)
            if m:
                content_clean = content_clean[m.start():]
        content_clean = _trim_to_last_safe_tag_boundary(content_clean)

    # --------- Validation phase ---------
    # Ensure no telemetry tags ever make it to the final artifact
    content_clean = _rm_invis(content_clean)
    content_clean = USAGE_TAG_RE.sub("", content_clean)

    fmt_fmt = "json" if is_managed_json else tgt
    fmt_ok, fmt_reason = _format_ok(content_clean, fmt_fmt)

    schema_ok = True
    schema_reason = "no_schema"
    payload_obj = None

    if tgt == "json" or is_managed_json:
        payload_obj, parse_err = _parse_json(content_clean)
        if parse_err:
            fmt_ok = False
            fmt_reason = parse_err
        if schema_json and payload_obj is not None:
            schema_ok, schema_reason = _validate_json_schema(payload_obj, schema_json)
            if not schema_ok and citation_container_path:
                pruned = _json_pointer_delete(payload_obj, citation_container_path)
                if pruned is not payload_obj:
                    schema_ok2, schema_reason2 = _validate_json_schema(pruned, schema_json)
                    if schema_ok2:
                        schema_ok = True
                        schema_reason = "schema_ok_without_sidecar"
    elif tgt == "yaml":
        payload_obj, parse_err = _parse_yaml(content_clean)
        if parse_err:
            fmt_ok = False
            fmt_reason = parse_err
        if schema_json and payload_obj is not None:
            try:
                as_json = json.loads(json.dumps(payload_obj, ensure_ascii=False))
                schema_ok, schema_reason = _validate_json_schema(as_json, schema_json)
                if not schema_ok and citation_container_path:
                    pruned = _json_pointer_delete(as_json, citation_container_path)
                    if pruned is not as_json:
                        schema_ok2, schema_reason2 = _validate_json_schema(pruned, schema_json)
                        if schema_ok2:
                            schema_ok = True
                            schema_reason = "schema_ok_without_sidecar"
            except Exception as e:
                schema_ok, schema_reason = False, f"yaml_to_json_coercion_failed: {e}"

    citations_status = "n/a"
    citations_ok = True
    valid_sids = set(sids)

    if require_citations:
        if tgt in ("markdown", "text") and eff_embed == "inline":
            citations_ok = citations_present_inline(content_clean, tgt)
            citations_status = "present" if citations_ok else "missing"
        elif tgt == "html" and eff_embed == "inline":
            citations_ok = citations_present_inline(content_clean, tgt)
            citations_status = "present" if citations_ok else "missing"
        elif is_json_like and eff_embed == "sidecar":
            if payload_obj is None:
                payload_obj, _ = (
                    _parse_json(content_clean)
                    if tgt in ("json", "managed_json_artifact")
                    else _parse_yaml(content_clean)
                )
            if payload_obj is not None:
                ok_sc, why_sc = _validate_sidecar(payload_obj, citation_container_path, valid_sids)
                citations_ok = ok_sc
                citations_status = "present" if ok_sc else f"missing:{why_sc}"
                if allow_inline_citations_in_strings and not ok_sc:
                    if _dfs_string_contains_inline_cite(payload_obj):
                        citations_ok = True
                        citations_status = "present_inline_only"
            else:
                citations_ok = False
                citations_status = "missing:payload_not_parsed"

    validated_tag = (
        "none" if not fmt_ok and (not schema_ok if is_json_like else True)
        else "format" if fmt_ok and (not is_json_like)
        else "schema" if (is_json_like and schema_ok and not fmt_ok)
        else "both" if fmt_ok and (schema_ok or not is_json_like)
        else "none"
    )

    ok = fmt_ok and (schema_ok if is_json_like else True) and (citations_ok if require_citations else True)

    # --------- Repair phase (kept) but now:
    # - uses shared streaming helper
    # - is skipped entirely when there is nothing to repair (payload_for_repair == "")
    if strict and not ok and max_rounds > 1:
        repair_reasons = []
        if not fmt_ok:
            repair_reasons.append(f"format_invalid: {fmt_reason}")
        if is_json_like and not schema_ok:
            repair_reasons.append(f"schema_invalid: {schema_reason}")
        if require_citations and not citations_ok:
            repair_reasons.append(f"citations_invalid: {citations_status}")
        repair_msg = "; ".join(repair_reasons)

        repair_instruction = [
            f"REPAIR the existing {tgt.upper()} WITHOUT changing previously generated semantics.",
            "Fix ONLY the issues listed:",
            f"- {repair_msg}",
        ]
        if is_json_like and schema_json:
            repair_instruction.append("Ensure the output VALIDATES against the provided JSON Schema.")
        if require_citations:
            if tgt in ("markdown", "text"):
                repair_instruction.append("Add inline tokens [[S:n]] at claim boundaries. Use only provided sids.")
            elif tgt == "html":
                repair_instruction.append('Add <sup class="cite" data-sids="...">[S:...]</sup> after each claim.')
            elif is_json_like:
                repair_instruction.append(
                    f'Populate sidecar at {citation_container_path} with items {{ "path": "<ptr>", "sids": [..] }} (use only provided sids).'
                )

        repair_sys = "You repair documents precisely. Return ONLY the fixed artifact; no comments; no preface."
        if require_citations:
            repair_sys += " Citations are mandatory as per the specified protocol."

        payload_for_repair = content_clean[-24000:]  # last 24k for safety
        if payload_for_repair:
            if have_sources and digest:
                payload_for_repair = (
                    f"SOURCE IDS:\n{sid_map}\n\n"
                    f"SOURCES DIGEST (reference for citations):\n{digest}\n\n"
                    f"DOCUMENT TO REPAIR:\n{payload_for_repair}"
                )
            else:
                payload_for_repair = f"DOCUMENT TO REPAIR:\n{payload_for_repair}"
        else:
            # TODO 1 behavior: if nothing to repair, skip the retry call entirely
            # Keep previous validation status; just annotate reason.
            if not reason:
                reason = "repair_skipped_empty_payload"
            else:
                reason = f"{reason}; repair_skipped_empty_payload"

        if payload_for_repair:
            # run compact repair call via shared streaming helper
            role_repair = role or "tool.generator"

            repair_messages = [
                SystemMessage(content=repair_sys),
                HumanMessage(content="\n".join(repair_instruction) + "\n\n" + payload_for_repair),
            ]

            repaired_raw, emitted_inc_rep, svc_error = await _stream_round(
                messages=repair_messages,
                role_name=role_repair,
                temperature=0.1,
                max_toks=min(max_tokens, 4000),
                author_for_chunks=rep_author,
                author_for_complete=rep_author,
                start_index=emitted_count,
                thinking_budget=thinking_budget
            )
            emitted_count += emitted_inc_rep
            if svc_error:
                out = {
                    "ok": False,
                    "content": "",
                    "format": tgt,
                    "finished": False,
                    "retries": max(0, used_rounds - 1),
                    "reason": f"service_error: {svc_error.get('message') or svc_error}",
                    "stats": {
                        "rounds": used_rounds,
                        "bytes": 0,
                        "validated": "none",
                        "citations": "n/a",
                        "service_error": svc_error,
                    },
                    "sources_used": [],
                    "service_error": svc_error,
                }
                logger.error(
                    "generate_content_llm: service_error during repair round=%s role=%s error=%s",
                    used_rounds,
                    role,
                    svc_error,
                )
                return out

            repaired = repaired_raw

            if tgt in ("json", "yaml") or is_managed_json:
                repaired = _unwrap_fenced_block(repaired, lang="json" if is_managed_json else tgt)
                repaired = _strip_bom_zwsp(repaired)
            else:
                repaired = _strip_code_fences(repaired, allow=code_fences).strip()

            content_clean = repaired or content_clean

            if tgt == "html" and content_clean.lstrip().startswith(("<?xml", "<xsl:")):
                tgt = "xml"

            if tgt in ("html", "xml"):
                content_clean = _trim_to_last_safe_tag_boundary(content_clean)

            # Ensure no telemetry tags ever make it to the final artifact
            content_clean = _rm_invis(content_clean)
            content_clean = USAGE_TAG_RE.sub("", content_clean)
            fmt_fmt = "json" if is_managed_json else tgt
            fmt_ok, fmt_reason = _format_ok(content_clean, fmt_fmt)

            if tgt == "json" or is_managed_json:
                payload_obj, parse_err = _parse_json(content_clean)
                if parse_err:
                    fmt_ok = False
                    fmt_reason = parse_err
                if schema_json and payload_obj is not None:
                    schema_ok, schema_reason = _validate_json_schema(payload_obj, schema_json)
                    if not schema_ok and citation_container_path:
                        pruned = _json_pointer_delete(payload_obj, citation_container_path)
                        if pruned is not payload_obj:
                            schema_ok2, schema_reason2 = _validate_json_schema(pruned, schema_json)
                            if schema_ok2:
                                schema_ok = True
                                schema_reason = "schema_ok_without_sidecar"
            elif tgt == "yaml":
                payload_obj, parse_err = _parse_yaml(content_clean)
                if parse_err:
                    fmt_ok = False
                    fmt_reason = parse_err
                if schema_json and payload_obj is not None:
                    try:
                        as_json = json.loads(json.dumps(payload_obj, ensure_ascii=False))
                        schema_ok, schema_reason = _validate_json_schema(as_json, schema_json)
                        if not schema_ok and citation_container_path:
                            pruned = _json_pointer_delete(as_json, citation_container_path)
                            if pruned is not as_json:
                                schema_ok2, schema_reason2 = _validate_json_schema(pruned, schema_json)
                                if schema_ok2:
                                    schema_ok = True
                                    schema_reason = "schema_ok_without_sidecar"
                    except Exception as e:
                        schema_ok, schema_reason = False, f"yaml_to_json_coercion_failed: {e}"

            citations_ok = True
            citations_status = "n/a"
            if require_citations:
                if tgt in ("markdown", "text", "html") and eff_embed == "inline":
                    citations_ok = citations_present_inline(content_clean, tgt)
                    citations_status = "present" if citations_ok else "missing"
                elif is_json_like and eff_embed == "sidecar":
                    if payload_obj is None:
                        payload_obj, _ = (
                            _parse_json(content_clean)
                            if tgt in ("json", "managed_json_artifact")
                            else _parse_yaml(content_clean)
                        )
                    if payload_obj is not None:
                        ok_sc2, why_sc2 = _validate_sidecar(payload_obj, citation_container_path, valid_sids)
                        citations_ok = ok_sc2
                        citations_status = "present" if ok_sc2 else f"missing:{why_sc2}"
                        if allow_inline_citations_in_strings and not ok_sc2:
                            if _dfs_string_contains_inline_cite(payload_obj):
                                citations_ok = True
                                citations_status = "present_inline_only"
                    else:
                        citations_ok = False
                        citations_status = "missing:payload_not_parsed"

            validated_tag = (
                "none" if not fmt_ok and (not schema_ok if is_json_like else True)
                else "format" if fmt_ok and (not is_json_like)
                else "schema" if (is_json_like and schema_ok and not fmt_ok)
                else "both" if fmt_ok and (schema_ok or not is_json_like)
                else "none"
            )

            ok = fmt_ok and (schema_ok if is_json_like else True) and (citations_ok if require_citations else True)
            reason = repair_msg if not ok else (reason or "repaired_ok")

    # --- derive used_sids from the artifact itself ---
    artifact_used_sids: List[int] = []

    if tgt in ("markdown", "text", "html"):
        from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any
        artifact_used_sids = extract_citation_sids_any(content_clean)
    elif is_json_like:
        try:
            if payload_obj is None:
                if tgt in ("json", "managed_json_artifact"):
                    payload_obj, _ = _parse_json(content_clean)
                else:
                    payload_obj, _ = _parse_yaml(content_clean)
            if payload_obj is not None and citation_container_path:
                sc = _json_pointer_get(payload_obj, citation_container_path)
                if isinstance(sc, list):
                    buf = []
                    for it in sc:
                        if isinstance(it, dict) and isinstance(it.get("sids"), list):
                            for x in it["sids"]:
                                if isinstance(x, int):
                                    buf.append(x)
                    artifact_used_sids = sorted(set(buf))
        except Exception:
            pass

    combined_used_sids = sorted(set((artifact_used_sids or []) + (usage_sids or [])))

    sources_used: List[int] = []
    if combined_used_sids and sources_list:
        try:
            raw_sources = sources_list
        except Exception:
            raw_sources = []

        by_sid: Dict[int, Dict[str, Any]] = {}
        for s in raw_sources or []:
            if not isinstance(s, dict):
                continue
            try:
                sid_val = int(s.get("sid"))
            except Exception:
                continue
            by_sid[sid_val] = s

        for sid in combined_used_sids:
            if sid in by_sid:
                sources_used.append(int(sid))

    logger.info(
        "generate_content_llm completed: agent=%s artifact=%s finished=%s ok=%s",
        agent_name, artifact_name, finished, ok,
        extra={
            "content_length": len(content_clean),
            "sources_used_count": len(sources_used),
            "validated": validated_tag,
            "reason": reason
        }
    )
    unmapped = []
    try:
        if tgt in ("markdown", "text", "html"):
            unmapped = find_unmapped_citation_sids(content_clean, citation_map)
    except Exception:
        unmapped = []

    if unmapped:
        logger.warning(
            "generate_content_llm: unmapped citation SIDs in artifact: %s (map has %s)",
            unmapped, sorted(citation_map.keys())
        )
    from kdcube_ai_app.apps.chat.sdk.tools.citations import debug_only_suspicious_tokens, CITE_TOKEN_RE

    # 1) Log any suspicious tokens (for future debugging)
    suspicious = debug_only_suspicious_tokens(content_clean)
    if suspicious:
        logger.warning("Final artifact still contains suspicious [[...]] tokens: %s", suspicious)
    # 2) As a safety net, strip any remaining raw [[S:...]]-style tokens
    #    (non-destructive: only the markers, not surrounding text)
    def _strip_raw_cite_tokens(text: str) -> str:
        # Reuse the same core pattern but drop matches instead of rendering.
        return CITE_TOKEN_RE.sub(lambda m: m.group(1) or "", text)

    content_clean = strip_only_suspicious_citation_like_tokens(content_clean)
    out = {
        "ok": bool(ok),
        "content": content_clean,
        "format": tgt,
        "finished": bool(finished),
        "retries": max(0, used_rounds - 1),
        "reason": (
            "" if ok else (
                fmt_reason if not fmt_ok
                else schema_reason if is_json_like and not schema_ok
                else citations_status
            )
        ),
        "stats": {
            "rounds": used_rounds,
            "bytes": len(content_clean.encode("utf-8")),
            "validated": validated_tag,
            "citations": citations_status if require_citations else "n/a"
        },
        "sources_used": sources_used,
        "tool.origin": "llm_tools.generate_content_llm",
    }
    return out

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/tools/with_llm_backends.py

import json
import re, yaml, jsonschema
from datetime import datetime, timezone

import time
from typing import Annotated, Optional, List, Dict, Any, Tuple, Set
import logging

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import delta as emit_delta, get_comm

from kdcube_ai_app.apps.chat.sdk.tools.citations import split_safe_citation_prefix, replace_citation_tokens_streaming, \
    extract_sids, build_citation_map_from_sources, citations_present_inline, MD_CITE_RE
from kdcube_ai_app.apps.chat.sdk.tools.md_utils import CODE_FENCE_RE
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import create_cached_human_message, create_cached_system_message

logger = logging.getLogger("with_llm_backends")
# # ----------------------------- helpers -----------------------------


_ZWSP = "\u200b"
_BOM  = "\ufeff"

def _rm_invis(s: str) -> str:
    # strip zero-width spaces and BOM which break token regexes
    # also collapse stray NBSPs around brackets/colon (safe)
    if not s:
        return s
    s = s.replace(_ZWSP, "").replace(_BOM, "")
    return s

def _strip_bom_zwsp(s: str) -> str:
    # remove UTF-8 BOM and zero-width spaces that models sometimes prepend
    if not s:
        return s
    s = s.lstrip("\ufeff").replace(_ZWSP, "")
    return s.strip()

def _unwrap_fenced_block(text: str, lang: str | None = None) -> str:
    """
    If text contains a ```<lang> ... ``` or ``` ... ``` block, return the inner.
    Otherwise return text unchanged (stripped). Works with ``` and ~~~ fences.
    """
    if not text:
        return text
    # prefer language-specific fence first
    if lang:
        m = re.search(rf"```{lang}\s*([\s\S]*?)\s*```", text, flags=re.I)
        if not m:
            m = re.search(rf"~~~{lang}\s*([\s\S]*?)\s*~~~", text, flags=re.I)
        if m:
            return m.group(1).strip()

    # any fenced block
    m = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if not m:
        m = re.search(r"~~~(?:\w+)?\s*([\s\S]*?)\s*~~~", text, flags=re.I)
    if m:
        return m.group(1).strip()

    return text.strip()

def _unwrap_fenced_blocks_concat(text: str, lang: str | None = None) -> str:
    """
    Collect ALL fenced blocks (```<lang> ... ``` or ~~~<lang> ... ~~~) and concatenate them.
    If none found, return the original text stripped.
    """
    if not text:
        return ""
    parts: list[str] = []
    if lang:
        # lang-specific first
        for pat in (rf"```{lang}\s*([\s\S]*?)\s*```", rf"~~~{lang}\s*([\s\S]*?)\s*~~~"):
            for m in re.finditer(pat, text, flags=re.I):
                parts.append(m.group(1).strip())
    # if nothing found for explicit lang, try any fenced blocks
    if not parts:
        for pat in (r"```(?:\w+)?\s*([\s\S]*?)\s*```", r"~~~(?:\w+)?\s*([\s\S]*?)\s*~~~"):
            for m in re.finditer(pat, text, flags=re.I):
                parts.append(m.group(1).strip())
    return "\n".join(parts).strip() if parts else text.strip()


def _extract_json_object(text: str) -> str | None:
    """Last-resort: pull the largest {...} region if fencing/extra prose remains."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end+1].strip()
    return None

def _remove_end_marker_everywhere(text: str, marker: str) -> str:
    """
    Remove the exact end marker. Also remove standalone lines that are only angle brackets.
    Does NOT strip whitespace to preserve proper spacing in streamed chunks.
    """
    if not text or not marker:
        return text

    s = text.replace(marker, "")

    # Remove standalone lines containing only angle brackets
    # (These sometimes appear when marker fragments stream on separate lines)
    s = re.sub(r"(?m)^(?:\s*[<>]{2,}\s*)+$", "", s)

    # DON'T strip! Preserving leading/trailing whitespace is critical for streaming
    return s
def _split_safe_marker_prefix(chunk: str, marker: str) -> tuple[str, int]:
    """
    If the end of `chunk` is a dangling prefix of `marker`, withhold it.
    Returns (safe_prefix, dangling_len).
    Example: marker="<<<GENERATION FINISHED>>>"
    chunk ending with "<<<GENER" -> withhold that suffix.
    """
    if not chunk or not marker:
        return chunk, 0
    # longest dangling first
    max_k = min(len(chunk), len(marker) - 1)
    for k in range(max_k, 0, -1):
        if chunk.endswith(marker[:k]):
            return chunk[:-k], k
    return chunk, 0

def _strip_code_fences(text: str, allow: bool) -> str:
    if allow:
        return text
    # Remove outermost triple-fence blocks when requested not to use them
    return CODE_FENCE_RE.sub("", text).strip()

def _remove_marker(text: str, marker: str) -> str:
    return text.replace(marker, "").strip()

def _json_pointer_get(root, ptr: str):
    if not ptr or ptr == "/":
        return root
    cur = root
    for part in ptr.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur

def _dfs_string_contains_inline_cite(node) -> bool:
    if isinstance(node, str): # _MD_CITE_RE
        return bool(MD_CITE_RE.search(node))
    if isinstance(node, list):
        return any(_dfs_string_contains_inline_cite(x) for x in node)
    if isinstance(node, dict):
        return any(_dfs_string_contains_inline_cite(v) for v in node.values())
    return False

def _validate_sidecar(payload: Any, path: str, valid_sids: Set[int]) -> Tuple[bool, str]:
    arr = _json_pointer_get(payload, path)
    if not isinstance(arr, list) or not arr:
        return False, "sidecar_missing_or_empty"
    for it in arr:
        if not isinstance(it, dict):
            return False, "sidecar_item_not_object"
        p, s = it.get("path"), it.get("sids")
        if not isinstance(p, str):
            return False, "sidecar_item_path_missing"
        if not (isinstance(s, list) and all(isinstance(x, int) for x in s)):
            return False, "sidecar_item_sids_invalid"
        if not set(s).issubset(valid_sids):
            return False, "sidecar_item_unknown_sid"
        # Optional: ensure pointer resolves to a string (best-effort)
        target = _json_pointer_get(payload, p)
        if target is None and p != "/":
            # we allow missing for minimal robustness but flag it
            return False, "sidecar_target_not_string"
    return True, "sidecar_ok"

def _parse_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        return json.loads(text), None
    except Exception as e:
        return None, f"json_parse_error: {e}"

def _parse_yaml(text: str) -> Tuple[Optional[Any], Optional[str]]:
    if yaml is None:
        return None, "yaml_not_available"
    try:
        return yaml.safe_load(text), None
    except Exception as e:
        return None, f"yaml_parse_error: {e}"

def _validate_json_schema(obj: Any, schema_json: str) -> Tuple[bool, str]:
    if not schema_json:
        return True, "no_schema"
    if jsonschema is None:
        return True, "jsonschema_not_available"
    try:
        schema = json.loads(schema_json)
    except Exception as e:
        return False, f"schema_json_invalid: {e}"
    try:
        jsonschema.validate(obj, schema)
        return True, "schema_ok"
    except jsonschema.exceptions.ValidationError as e:
        return False, f"schema_validation_error: {e.message}"

def _basic_html_ok(s: str) -> bool:
    if not s:
        return False
    low = s.strip().lower()
    # accept if looks like an HTML doc
    if "<!doctype html" in low or "<html" in low:
        # also require a plausible end
        return ("</html>" in low) or ("</body>" in low)
    return False

import xml.etree.ElementTree as ET
def _xml_is_wellformed(s: str) -> bool:
    if not s or not s.strip():
        return False
    try:
        ET.fromstring(s)
        return True
    except Exception:
        return False

def _basic_xml_ok(s: str) -> bool:
    return _xml_is_wellformed(s)

def _format_ok(out: str, fmt: str) -> Tuple[bool, str]:

    if fmt == "html":
        ok = _basic_html_ok(out)
        return (ok, "html_ok" if ok else "html_basic_check_failed")
    if fmt in ("markdown", "text"):
        # Always OK structurally; semantic completeness is handled by citations/marker.
        return (len(out.strip()) > 0, "nonempty")
    # if fmt == "json":
    #     obj, err = _parse_json(out)
    #     return (obj is not None, err or "json_ok")
    if fmt == "json":
        obj, err = _parse_json(out)
        return ((obj is not None), ("json_ok" if obj is not None else err or "json_parse_error"))
    if fmt == "yaml":
        obj, err = _parse_yaml(out)
        return (obj is not None, err or "yaml_ok")
    if fmt == "xml":
        ok = _basic_xml_ok(out)
        return (ok, "xml_ok" if ok else "xml_basic_check_failed")

    return False, "unknown_format"


import re as _re

HTML_CITE_RE = _re.compile(
    r'(?is)<sup[^>]*class=["\'][^"\']*\bcite\b[^"\']*["\'][^>]*>\s*\[S:\s*\d+(?:\s*[,–-]\s*\d+)*\]\s*</sup>'
)
# _FOOTNOTES_BLOCK_RE = _re.compile(
#     r'(?is)<(?:div|section)[^>]*class=["\'][^"\']*\bfootnotes\b[^"\']*["\'][^>]*>.*?\[S:\s*\d+(?:\s*[,–-]\s*\d+)*\].*?</(?:div|section)>'
# )
# _SOURCES_HEADING_RE = _re.compile(r'(?is)<h[1-6][^>]*>\s*(?:sources?|references?)\s*</h[1-6]>')

# Footnotes block that lists [S:n] style references inside a .footnotes container
HTML_FOOTNOTES_RE = re.compile(
    r'<(?:div|section)[^>]*class="[^"]*\bfootnotes\b[^"]*"[^>]*>.*?\[S:\s*\d+.*?</(?:div|section)>',
    re.I | re.S
)
# Also allow a generic "Sources" section containing [S:n]
HTML_SOURCES_RE = re.compile(
    r'<h[1-6][^>]*>\s*Sources\s*</h[1-6]>.*?\[S:\s*\d+',
    re.I | re.S
)

USAGE_TAG_RE = re.compile(r"\[\[\s*USAGE\s*:\s*([0-9,\s\-]+)\s*\]\]", re.I)

# hold back partial tails like "[[", "[[U", "[[USAGE:", or a half-closed tag
USAGE_SUFFIX_PATS = [
    re.compile(r"\[\[$"),                              # "[[" at end
    re.compile(r"\[\[\s*U\s*$", re.I),
    re.compile(r"\[\[\s*US\s*$", re.I),
    re.compile(r"\[\[\s*USA\s*$", re.I),
    re.compile(r"\[\[\s*USAG\s*$", re.I),
    re.compile(r"\[\[\s*USAGE\s*:\s*[0-9,\s\-]*$", re.I),
    re.compile(r"\[\[\s*USAGE\s*:\s*[0-9,\s\-]*\]$", re.I),  # missing final ']'
]

def _split_safe_usage_prefix(chunk: str) -> tuple[str, int]:
    if not chunk:
        return "", 0
    for pat in USAGE_SUFFIX_PATS:
        m = pat.search(chunk)
        if m and m.end() == len(chunk):
            return chunk[:m.start()], len(chunk) - m.start()
    return chunk, 0

def citations_present_inline(content: str, fmt: str) -> bool:
    """
    Minimal presence test for inline citations in a rendered document.
    - markdown/text: looks for [[S:n...]] tokens
    - html: EITHER <sup class="cite" data-sids="...">…</sup>
            OR a footnotes/sources section containing [S:n] markers.
    """
    if fmt in ("markdown", "text"):
        return bool(MD_CITE_RE.search(content))
    if fmt == "html":
        return (
                bool(HTML_CITE_RE.search(content)) or
                bool(HTML_FOOTNOTES_RE.search(content)) or
                bool(HTML_SOURCES_RE.search(content))
        )
    return False


def _json_pointer_delete(root: Any, ptr: str) -> Any:
    """
    Return a shallow-copied object with the node at `ptr` removed (best-effort).
    Designed for removing the sidecar path before schema validation.
    Supports dict parents; list indices are ignored safely.
    """
    if not ptr or ptr == "/":
        return root
    cur = root
    parent = None
    key = None
    parts = ptr.strip("/").split("/")
    for raw in parts:
        part = raw.replace("~1", "/").replace("~0", "~")
        parent, key = cur, part
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
                cur = cur[idx] if 0 <= idx < len(cur) else None
            except Exception:
                cur = None
        else:
            cur = None
        if cur is None:
            break

    # remove only when parent is a dict and key exists
    if isinstance(parent, dict) and key in parent:
        new_root = json.loads(json.dumps(root, ensure_ascii=False))  # cheap deep copy that’s schema-safe
        p2 = new_root
        for raw in parts[:-1]:
            part = raw.replace("~1", "/").replace("~0", "~")
            p2 = p2.get(part) if isinstance(p2, dict) else None
            if p2 is None:
                return root
        if isinstance(p2, dict):
            p2.pop(parts[-1].replace("~1", "/").replace("~0", "~"), None)
            return new_root
    return root

async def generate_content_llm(
        _SERVICE,
        agent_name:  Annotated[str, "Name of this content creator, short, to distinguish this author in the sequence of generative calls."],
        instruction: Annotated[str, "What to produce (goal/contract)."],
        artifact_name: Annotated[str|None, "Name of the artifact being produced (for tracking)."] = "",
        input_context: Annotated[str, "Optional base text or data to use."] = "",
        target_format: Annotated[str, "html|markdown|json|yaml|text",
                                 {"enum": ["html", "markdown", "json", "yaml", "text", "xml"]}] = "markdown",
        schema_json: Annotated[str,
                                "Optional JSON Schema. If provided (and target_format is json|yaml), "
                                "the schema is inserted into the prompt and the model MUST produce an output that validates against it."] = "",
        sources_json: Annotated[str, "JSON array of sources: {sid:int, title:str, url?:str, text:str}."] = "[]",
        cite_sources: Annotated[bool, "If true and sources provided, require citations (inline for Markdown/HTML; sidecar for JSON/YAML)."] = False,
        citation_embed: Annotated[str, "auto|inline|sidecar|none",
                                  {"enum": ["auto", "inline", "sidecar", "none"]}] = "auto",
        citation_container_path: Annotated[str, "JSON Pointer for sidecar path (json/yaml)."] = "/_citations",
        allow_inline_citations_in_strings: Annotated[bool, "Permit [[S:n]] tokens inside JSON/YAML string fields."] = False,
        # end_marker: Annotated[str, "Completion marker appended by the model at the very end."] = "<<<GENERATION FINISHED>>>",
        max_tokens: Annotated[int, "Per-round token cap.", {"min": 256, "max": 8000}] = 7000,
        max_rounds: Annotated[int, "Max generation/repair rounds.", {"min": 1, "max": 10}] = 4,
        code_fences: Annotated[bool, "Allow triple-backtick fenced blocks in output."] = True,
        continuation_hint: Annotated[str, "Optional extra hint used on continuation rounds."] = "",
        strict: Annotated[bool, "Require format OK and (if provided) schema OK and citations (if requested)."] = True,
        role: str = "tool.generator",
        cache_instruction: bool=True,
        channel_to_stream: Optional[str]="canvas",
) -> Annotated[str, 'JSON string: {ok, content, format, finished, retries, reason, stats, sources_used: [ { "sid": 1, "url": "...", "title": "...", "text": "..." }, ... ]}']:
    """
    Returns JSON string:
      {
        "ok": true/false,
        "content": "<final text>",
        "format": "<target_format>",
        "finished": true/false,        # saw end_marker
        "retries": <int>,              # rounds used - 1
        "reason": "<last failure reason or ''>",
        "stats": { "rounds": n, "bytes": len(content), "validated": "format|schema|both|none", "citations": "present|missing|n/a" }
      }
    """

    from langchain_core.messages import SystemMessage, HumanMessage
    from kdcube_ai_app.infra.accounting import _get_context
    context = _get_context()
    context_snapshot = context.to_dict()
    logger.warning(f"[Context snapshot]:\n{context_snapshot}")

    rep_author = agent_name or "Content Generator LLM"
    track_id = context_snapshot.get("track_id")
    bundle_id = context_snapshot.get("app_bundle_id")

    artifact_name = artifact_name or agent_name

    # --------- normalize inputs ---------
    tgt = (target_format or "markdown").lower().strip()
    if tgt not in ("html", "markdown", "json", "yaml", "text", "xml"):
        tgt = "markdown"

    # auto embedding policy
    eff_embed = citation_embed
    if citation_embed == "auto":
        if tgt in ("markdown", "text"):
            eff_embed = "inline"
        elif tgt == "html":
            eff_embed = "inline"
        elif tgt in ("json", "yaml"):
            eff_embed = "sidecar"
        elif tgt == "xml":
            eff_embed = "none"  # XML has no citation protocol here
        else:
            eff_embed = "none"

    if max_tokens < 5500:
        if tgt in ("html", "xml"):
            max_tokens = 5500
    sids = extract_sids(sources_json)
    have_sources = bool(sids)

    # XML should not demand citations (no inline protocol defined); disable even if user set cite_sources=True
    require_citations = bool(cite_sources and have_sources and eff_embed != "none")
    allowed_sids_line = f"ALLOWED SIDS: {', '.join(str(x) for x in sorted(sids))}" if sids else "ALLOWED SIDS: (none)"

    end_marker: Annotated[str, "Completion marker appended by the model at the very end."] = "<<<GENERATION FINISHED>>>"

    def _scrub_emit_once(s: str) -> str:
        if not s:
            return s
        # strip the exact completion marker
        s = _remove_end_marker_everywhere(s, end_marker)
        # strip any complete hidden usage tag occurrences
        s = USAGE_TAG_RE.sub("", s)
        return s

    # Local safe-stop guard for taggy formats
    def _trim_to_last_safe_tag_boundary(s: str) -> str:
        if not s:
            return s
        # best-effort: cut to last '>' to avoid dangling <tag
        i = s.rfind(">")
        return s[:i+1] if i != -1 else s

    # --------- system prompt (format + citation rules) ---------
    basic_sys_instruction = "\n".join([
        "You are a precise generator. Produce ONLY the requested artifact in the requested format.",
        "NEVER include meta-explanations. Do not apologize. No prefaces. No trailing notes.",
        "If continuing, resume exactly where you left off.",
        "",
        "GENERAL OUTPUT RULES:",
        "- Keep the output self-contained.",
        "- Avoid placeholders like 'TBD' unless explicitly requested.",
    ])

    target_format_sys_instruction = f"TARGET FORMAT: {tgt}"

    # Shared STRUCTURED COMPLETION CONTRACT for tag-based formats
    structured_contract = [
        "STRUCTURED COMPLETION CONTRACT (for HTML/XML):",
        "- Never cut inside a tag or attribute; always finish the current element.",
        "- If you approach the token limit, STOP only after writing a complete closing tag.",
        "- Emit a single, cohesive document; no headers/prefaces outside the document.",
        "- Do not use triple-backtick fences; output raw markup only.",
    ]
    sys_lines = []
    if tgt == "markdown":
        sys_lines += [
            "MARKDOWN RULES:",
            "- Use proper headings, lists, tables, and code blocks as needed.",
        ]
    elif tgt == "html":
        sys_lines += ["""
    You are an HTML generator. Your output MUST be valid, well-formed HTML.
    
    ⚠️ CRITICAL FORMAT RULE: Output PURE HTML ONLY. NO markdown. NO code fences (```). NO explanations.
    Start IMMEDIATELY with <!DOCTYPE html> or the opening tag. End with </html> and the completion marker.
    CRITICAL RULE: NEVER produce broken HTML. An incomplete document is worthless.
    
    TOKEN BUDGET MANAGEMENT:
    - You have a token budget for this generation (typically 4000-8000 tokens).
    - You CANNOT see when you're about to hit the limit.
    - Strategy: Be CONSERVATIVE. Stop early to guarantee closure.
    
    SAFE GENERATION PATTERN:
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
    
    VALID HTML REQUIREMENTS:
    - Major tags MUST close: <div>...</div>, <section>...</section>, <table>...</table>
    - Proper nesting: <div><p></p></div>
    - Self-closing tags OK: <br>, <img>, <hr>, <meta>, <input>
    - Attributes quoted: <div class="container">
    - Special characters escaped in text: &lt; &gt; &amp;
    - ALWAYS close: <body>, <html>, <head>, <title>, <script>, <style>
    
    OUTPUT FORMAT:
    - Pure HTML only (no markdown, no code fences, no explanations)
    - Start immediately with <!DOCTYPE html>
    - End with </html> followed by <<<GENERATION FINISHED>>>
    - No apologetic messages like "Due to space constraints..."
    
    EXAMPLES OF SAFE SCALING:
    - Request: "100 blog post cards" → Deliver: 60-70 complete cards
    - Request: "50 employee profiles" → Deliver: 30-35 complete profiles
    - Request: "25 dashboard widgets" → Deliver: 15-18 complete widgets
    - Request: "10 detailed sections" → Deliver: 6-7 complete sections
    
    FAILURE MODES TO AVOID:
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
    
    TOKEN BUDGET MANAGEMENT:
    - You have a token budget for this generation (typically 4000-8000 tokens).
    - You CANNOT see when you're about to hit the limit.
    - Strategy: Be CONSERVATIVE. Stop early to guarantee closure.
    
    SAFE GENERATION PATTERN:
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
    
    VALID XML REQUIREMENTS:
    - Every opening tag has a closing tag: <item>...</item>
    - Proper nesting: <outer><inner></inner></outer>
    - Attributes quoted: <item id="123">
    - No orphaned tags
    - Special characters escaped: &lt; &gt; &amp; &quot; &apos;
    
    OUTPUT FORMAT:
    - Pure XML only (no markdown, no code fences, no explanations)
    - Start immediately with <?xml or <root>
    - End with </root> followed by <<<GENERATION FINISHED>>>
    - No apologetic messages like "Due to space constraints..."
    
    EXAMPLES OF SAFE SCALING:
    - Request: "100 book entries" → Deliver: 60-70 complete entries
    - Request: "50 customer records" → Deliver: 30-35 complete records
    - Request: "25 configuration items" → Deliver: 15-18 complete items
    - Request: "10 detailed reports" → Deliver: 6-7 complete reports
    
    FAILURE MODES TO AVOID:
    ❌ <items><item>...</item><item>...  [TRUNCATED - no closing </items>]
    ❌ <items><item>...</item></items>Here's what I generated...  [TEXT AFTER ROOT]
    ❌ <items><item id="5  [ATTRIBUTE NOT CLOSED]
    ✅ <items><item id="1">...</item><item id="2">...</item></items><<<GENERATION FINISHED>>>
    
    REMEMBER: Quality over quantity. Valid XML with fewer items > Invalid XML with more items.
    """]

    strict_source_boundary = [
        "",
        "STRICT SOURCE BOUNDARY:",
        "- You may ONLY cite sources whose SID is listed below.",
        "- NEVER invent or reference any SID not listed.",
        "- If a claim cannot be supported by the provided sources, either omit the claim or present it without a citation.",
        allowed_sids_line,
    ]
    # Citation rules
    if require_citations:
        if tgt in ("markdown", "text") and eff_embed == "inline":
            sys_lines += [
                "",
                "CITATION REQUIREMENTS (MARKDOWN/TEXT):",
                "- Insert [[S:<sid>]] tokens at the end of sentences/bullets that contain NEW or materially CHANGED factual claims.",
                "- Multiple sources allowed: [[S:1,3]] for enumeration and [[S:2-4]] for inclusive range. Use only the provided sid values. Never invent.",
                "",
                "CODE BLOCK CITATION RULES:",
                "- NEVER place citation tokens inside code fences (```) of any kind.",
                "- ESPECIALLY: Do NOT put citations inside Mermaid diagrams, JSON blocks, YAML blocks, or any other fenced code.",
                "- Instead, place citations in the explanatory prose BEFORE or AFTER the code block.",
            ]
            sys_lines += strict_source_boundary
        elif tgt == "html" and eff_embed == "inline":
            sys_lines += [
                "",
                "CITATION REQUIREMENTS (HTML):",
                '- Insert <sup class="cite" data-sids="1,3">[S:1,3]</sup> immediately after the sentence/phrase introducing NEW or materially CHANGED facts.',
                "- Use only provided sid values. Never invent.",
            ]
            sys_lines += strict_source_boundary
        elif tgt in ("json", "yaml") and eff_embed == "sidecar":
            sys_lines += [
                "",
                "CITATION REQUIREMENTS (STRUCTURED, SIDECAR):",
                f'- Do NOT put citation tokens in the main payload. Add a sidecar array at JSON Pointer "{citation_container_path}" with objects:',
                '  { "path": "<JSON Pointer to the string field containing the claim>", "sids": [<sid>, ...] }',
                "- 'path' MUST point to an existing string field in the returned document.",
            ]
            if allow_inline_citations_in_strings:
                sys_lines += [
                    "- Inline tokens [[S:n]] inside string fields are permitted but sidecar remains required."
                ]
                sys_lines += strict_source_boundary

        sys_lines+= [
            (
                "### SOURCE RELEVANCE POLICY\n"
                "- Only use sources that directly support the requested topic and the claims you make.\n"
                "- If source is off-topic (different domain/subject, spammy listing, irrelevant brand/news), exclude it from reasoning and citations.\n"
                "- Do not cite a source unless it substantively supports the sentence you attach it to.\n"
                "- Prefer primary/official documents and regulator/standards bodies over tertiary blogs or low-quality aggregators.\n"
                "- If most results are off-topic, state this briefly and proceed without those sources (or ask for better sources in minimal wording).\n"
                "- Keep sources_json restricted to sources actually used for claims; never force citations just to satisfy a requirement.\n"
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
            "USAGE TELEMETRY (INVISIBLE TO USER):",
            "- If sources are provided but citations are not required, you MUST record which source IDs you actually relied on.",
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
            "SCHEMA CONFORMANCE (MANDATORY):",
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
            raw_sources = json.loads(sources_json) if sources_json else []
        except Exception:
            raw_sources = []
        for s in raw_sources or []:
            if not isinstance(s, dict):
                continue
            sid = s.get("sid")
            title = s.get("title") or ""
            body = s.get("text") or s.get("body") or s.get("content") or ""
            if sid is None:
                continue
            rows.append({"sid": int(sid), "title": title, "text": body})
        sid_map = "\n".join([f"- {r['sid']}: {r['title'][:160]}" for r in rows])
        total_budget = 10000
        per = max(600, total_budget // max(1, len(rows))) if rows else 0
        parts = []
        for r in rows:
            t = (r["text"] or "")[:per]
            parts.append(f"[sid:{r['sid']}] {r['title']}\n{t}".strip())
        digest = "\n\n---\n\n".join(parts)[:total_budget]

    sys_prompt = "\n".join(sys_lines)

    line_with_token_budget = f"CRITICAL RULE FOR TOKENS USAGE AND DATA INTEGRITY: You have {max_tokens} tokens to accomplish this generation task. You must plan the generation content that fully fit this budget."
    if tgt in ("html", "xml", "json", "yaml"):
        line_with_token_budget += "Your output must pass the format validation."

    system_msg = create_cached_system_message([
        {"text": basic_sys_instruction, "cache": True},
        {"text": target_format_sys_instruction, "cache": False},
        {"text": sys_prompt, "cache": True},
        {"text": line_with_token_budget, "cache": False}
    ])

    # --------- streaming + rounds ---------
    buf_all: List[str] = []
    finished = False
    reason = ""
    used_rounds = 0

    # keep index across rounds so UI sees a single, growing stream
    emitted_count = 0

    # Build citation map once (we’ll only use it if tgt == "markdown")
    citation_map = build_citation_map_from_sources(sources_json)

    def _build_user_blocks_for_round(round_idx: int) -> List[dict]:
        """
        Compose the HumanMessage as Anthropic-friendly blocks.
        The instruction is ALWAYS included, and is cacheable when cache_instruction=True.
        Non-instruction blocks are not cached (they can vary per round).
        """
        blocks: List[dict] = []

        # 1) Instruction (ALWAYS include; cache if requested)
        #    Your helper expects: {"text": "...", "cache": bool}
        blocks.append({"text": f"INSTRUCTION:\n{instruction}", "cache": bool(cache_instruction)})

        # 2) Stable metadata for the task (non-cached)
        blocks.append({"text": f"TARGET FORMAT: {tgt}", "cache": False})

        # 3) Input context (may be large; non-cached, truncated once here)
        if input_context:
            blocks.append({"text": f"INPUT CONTEXT:\n{input_context[:12000]}", "cache": False})

        # 4) Sources (sid map + digest), non-cached
        if rows:
            if round_idx == 0:
                # On the first round, include full sid map + digest
                if sid_map:
                    blocks.append({"text": f"SOURCE IDS:\n{sid_map}", "cache": False})
                if digest:
                    blocks.append({"text": f"SOURCES DIGEST:\n{digest}", "cache": False})
            else:
                # On retries, keep brief reminder to avoid bloat
                blocks.append({"text": "Remember the SOURCE IDS and DIGEST from earlier in this turn.", "cache": False})
                if require_citations:
                    blocks.append({"text": "Remember the CITATION REQUIREMENTS.", "cache": False})

        if round_idx > 0:
            # 5) Continuation guidance + tail of produced content (non-cached)
            produced_so_far = "".join(buf_all)[-20000:]
            cont_hint = continuation_hint or "Continue exactly from where you left off."
            blocks.append({"text": cont_hint, "cache": False})
            blocks.append({"text": "YOU ALREADY PRODUCED (partial, do not repeat):", "cache": False})
            blocks.append({"text": produced_so_far, "cache": False})
            blocks.append({"text": f"Resume and complete the {tgt.upper()} output. Append, do not restart.", "cache": False})

        return blocks

    # IMPORTANT: single round for big/structured formats (no continuation)
    effective_max_rounds = 1 if tgt in ("json", "yaml", "html", "xml") else max_rounds
    logger.warning(f"Effective max rounds={effective_max_rounds}; format={tgt}")
    for round_idx in range(effective_max_rounds):
        used_rounds = round_idx + 1

        # ---- STREAM ONE ROUND ----
        round_buf: List[str] = []
        author = agent_name or "Content Generator LLM"
        stream_buf = ""
        emit_from = 0
        EMIT_HOLDBACK = 32

        async def _emit_visible(text: str):
            nonlocal emitted_count
            if not text:
                return
            text = _scrub_emit_once(text)
            text = _rm_invis(text)
            out = replace_citation_tokens_streaming(text, citation_map) if tgt == "markdown" else text
            if get_comm():
                await emit_delta(out, index=emitted_count, marker=channel_to_stream, agent=author, format=tgt or "markdown", artifact_name=artifact_name)
                emitted_count += 1

        async def _flush_safe(force: bool = False):
            nonlocal emit_from
            if emit_from >= len(stream_buf):
                return

            if force:
                # Final flush - emit all remaining content without safety checks
                raw_slice = stream_buf[emit_from:]
                if raw_slice:
                    await _emit_visible(raw_slice)
                    emit_from = len(stream_buf)
            else:
                # Normal streaming - use holdback and safety checks
                safe_end = max(emit_from, len(stream_buf) - EMIT_HOLDBACK)
                if safe_end <= emit_from:
                    return
                raw_slice = stream_buf[emit_from:safe_end]

                safe_chunk, _ = split_safe_citation_prefix(raw_slice)
                safe_chunk, _ = _split_safe_usage_prefix(safe_chunk)
                safe_chunk, _ = _split_safe_marker_prefix(safe_chunk, end_marker)

                if safe_chunk:
                    await _emit_visible(safe_chunk)
                    emit_from += len(safe_chunk)

        async def on_delta(piece: str):
            nonlocal stream_buf
            if not piece:
                return
            round_buf.append(piece)
            stream_buf += piece
            await _flush_safe(force=False)

        async def on_complete(_):

            nonlocal emitted_count
            # if tgt == "markdown":
            #    await _flush_safe(force=True)
            await _flush_safe(force=True)
            emitted_count += 1
            await emit_delta("", completed=True, index=emitted_count, marker=channel_to_stream, agent=rep_author, format=tgt or "markdown", artifact_name=artifact_name)

        role = role or "tool.generator"
        client = _SERVICE.get_client(role)
        cfg = _SERVICE.describe_client(client, role=role)

        # ✅ Build cached HumanMessage blocks with the instruction ALWAYS present
        user_blocks = _build_user_blocks_for_round(round_idx)
        human_msg = create_cached_human_message(user_blocks, cache_last=False)

        # System message can stay as-is (string). If you want to cache parts, you already use create_cached_system_message elsewhere.
        # system_msg = SystemMessage(content=sys_prompt)

        async with with_accounting(bundle_id,
                                   track_id=track_id,
                                   agent=role,
                                   metadata={
                                       "track_id": track_id,
                                       "agent": role,
                                       "agent_name": agent_name
                                   }):
            await _SERVICE.stream_model_text_tracked(
                client,
                messages=[system_msg, human_msg],
                on_delta=on_delta,
                on_complete=on_complete,
                temperature=0.2,
                max_tokens=max_tokens,
                client_cfg=cfg,
                role=role,
            )

        chunk = "".join(round_buf)
        buf_all.append(chunk)

        cumulative = "".join(buf_all)
        if end_marker in cumulative:
            finished = True
            break

    # -------- post-processing / validation --------
    content_raw = "".join(buf_all)

    # --- usage tag extraction (from the RAW buffer that still has the tag) ---
    usage_sids: List[int] = []
    m_usage = USAGE_TAG_RE.search(content_raw or "")
    if m_usage:
        try:
            ids_str = m_usage.group(1) or ""
            for part in ids_str.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = [int(x.strip()) for x in part.split("-", 1)]
                    lo, hi = (a, b) if a <= b else (b, a)
                    usage_sids.extend(range(lo, hi + 1))
                elif part.isdigit():
                    usage_sids.append(int(part))
        except Exception:
            pass
        # remove the usage tag from the content BEFORE we do any more cleaning
        content_raw = USAGE_TAG_RE.sub("", content_raw)

    # normalize & de-dup
    usage_sids = sorted(set(usage_sids))

    # If we never saw marker but have something, proceed to validate anyway
    reason = "finished_with_marker" if finished else "no_end_marker"

    # Strip fences (optionally) and remove marker
    # content_clean = _strip_code_fences(content_raw, allow=code_fences)
    # Remove the marker early
    content_raw = _remove_marker(content_raw, end_marker)

    # Unwrap/clean
    if tgt in ("json", "yaml", "xml", "html"):
        # Always unwrap fenced blocks and strip BOM/ZWSP
        # content_clean = _unwrap_fenced_block(content_raw, lang=tgt)

        # Prefer concatenating *all* fenced blocks (multiple rounds), then strip BOM/ZWSP
        stitched = _unwrap_fenced_blocks_concat(content_raw, lang=tgt)
        # content_clean = _strip_bom_zwsp(content_clean)
        content_clean = _strip_bom_zwsp(stitched)

        # If JSON parse fails, last-resort grab the largest {...} slice from the full raw
        if tgt == "json":
            obj_probe, err_probe = _parse_json(content_clean)
            if obj_probe is None:
                alt = _extract_json_object(content_raw)
                if alt:
                    content_clean = _strip_bom_zwsp(alt)
    else:
        # honor code_fences only for non-structured formats
        content_clean = _strip_code_fences(content_raw, allow=code_fences)
        content_clean = _strip_bom_zwsp(content_clean)

    # If model returned XML/XSL under HTML, accept and validate as XML
    if tgt == "html" and content_clean.lstrip().startswith(("<?xml", "<xsl:")):
        tgt = "xml"

    # Safe-stop guard for taggy formats (avoid partial tail)
    if tgt in ("html", "xml"):
        content_clean = _trim_to_last_safe_tag_boundary(content_clean)

    # --------- Validation phase ---------
    fmt_ok, fmt_reason = _format_ok(content_clean, tgt)

    schema_ok = True
    schema_reason = "no_schema"
    payload_obj = None

    if tgt == "json":
        payload_obj, parse_err = _parse_json(content_clean)
        if parse_err:
            fmt_ok = False
            fmt_reason = parse_err
        if schema_json and payload_obj is not None:
            schema_ok, schema_reason = _validate_json_schema(payload_obj, schema_json)
            if not schema_ok and citation_container_path:
                # Retry validation with sidecar removed (schema might not model it)
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
        # If we got a Python obj and a JSON schema is provided, try to validate by dumping to JSON
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

    # Citations validation
    citations_status = "n/a"
    citations_ok = True
    valid_sids = set(sids)

    if require_citations:
        if tgt in ("markdown", "text") and eff_embed == "inline":
            citations_ok = citations_present_inline(content_clean, tgt)
            citations_status = "present" if citations_ok else "missing"
        elif tgt == "html" and eff_embed == "inline":
            # Accept inline OR footnotes-style
            citations_ok = citations_present_inline(content_clean, tgt)
            citations_status = "present" if citations_ok else "missing"
        elif tgt in ("json", "yaml") and eff_embed == "sidecar":
            if payload_obj is None:
                # try parse one more time best-effort
                payload_obj, _ = (_parse_json(content_clean) if tgt == "json"
                                  else _parse_yaml(content_clean))
            if payload_obj is not None:
                ok, why = _validate_sidecar(payload_obj, citation_container_path, valid_sids)
                citations_ok = ok
                citations_status = "present" if ok else f"missing:{why}"
                # optionally accept inline-in-strings as additional signal
                if allow_inline_citations_in_strings and not ok:
                    if _dfs_string_contains_inline_cite(payload_obj):
                        citations_ok = True
                        citations_status = "present_inline_only"
            else:
                citations_ok = False
                citations_status = "missing:payload_not_parsed"

    # Overall status
    validated_tag = (
        "none" if not fmt_ok and (not schema_ok if tgt in ("json", "yaml") else True)
        else "format" if fmt_ok and (tgt not in ("json", "yaml"))
        else "schema" if (tgt in ("json", "yaml") and schema_ok and not fmt_ok)
        else "both" if fmt_ok and (schema_ok or tgt not in ("json", "yaml"))
        else "none"
    )

    ok = fmt_ok and (schema_ok if tgt in ("json", "yaml") else True) and (citations_ok if require_citations else True)

    # If strict and not ok but rounds remain, attempt one focused repair pass
    # (We already exhausted rounds for generation; one more compact attempt to repair.)
    repair_msg  = ""
    if strict and not ok and max_rounds > 0:
        repair_reasons = []
        if not fmt_ok:
            repair_reasons.append(f"format_invalid: {fmt_reason}")
        if tgt in ("json", "yaml") and not schema_ok:
            repair_reasons.append(f"schema_invalid: {schema_reason}")
        if require_citations and not citations_ok:
            repair_reasons.append(f"citations_invalid: {citations_status}")
            repair_msg = "; ".join(repair_reasons)

        repair_instruction = [
            f"REPAIR the existing {tgt.upper()} WITHOUT changing previously generated semantics.",
            "Fix ONLY the issues listed:",
            f"- {repair_msg}",
        ]
        if tgt in ("json", "yaml") and schema_json:
            repair_instruction.append("Ensure the output VALIDATES against the provided JSON Schema.")
        if require_citations:
            if tgt in ("markdown", "text"):
                repair_instruction.append("Add inline tokens [[S:n]] at claim boundaries. Use only provided sids.")
            elif tgt == "html":
                repair_instruction.append('Add <sup class="cite" data-sids="...">[S:...]</sup> after each claim.')
            elif tgt in ("json", "yaml"):
                repair_instruction.append(f'Populate sidecar at {citation_container_path} with items {{ "path": "<ptr>", "sids": [..] }} (use only provided sids).')

        # run a compact repair call
        repair_sys = "You repair documents precisely. Return ONLY the fixed artifact; no comments; no preface."
        if require_citations:
            repair_sys += " Citations are mandatory as per the specified protocol."

        payload_for_repair = content_clean[-24000:]  # last 24k for safety
        if have_sources and digest:
            # include sid map minimally
            payload_for_repair = (
                f"SOURCE IDS:\n{sid_map}\n\n"
                f"SOURCES DIGEST (reference for citations):\n{digest}\n\n"
                f"DOCUMENT TO REPAIR:\n{payload_for_repair}"
            )
        else:
            payload_for_repair = f"DOCUMENT TO REPAIR:\n{payload_for_repair}"

        role = "tool.generator"
        client = _SERVICE.get_client(role)

        # --- Repair streaming with the SAME Markdown substitution behavior ---
        repair_buf: List[str] = []   # RAW repair text
        rep_stream_buf = ""
        rep_emit_from = 0
        REP_EMIT_HOLDBACK = 32
        # rep_emitted_count = 0

        async def _rep_emit_visible(text: str):
            nonlocal emitted_count
            if not text:
                return
            text = _scrub_emit_once(text)                     # ← scrub here
            text = _rm_invis(text)
            out = replace_citation_tokens_streaming(text, citation_map) if tgt == "markdown" else text
            if get_comm():
                await emit_delta(out, index=emitted_count, marker=channel_to_stream, agent=rep_author, format=tgt or "markdown", artifact_name=artifact_name)
                emitted_count += 1

        async def _rep_flush_safe(force: bool = False):
            nonlocal rep_emit_from
            if rep_emit_from >= len(rep_stream_buf):
                return

            if force:
                # Final flush - emit all remaining content
                raw_slice = rep_stream_buf[rep_emit_from:]
                if raw_slice:
                    await _rep_emit_visible(raw_slice)
                    rep_emit_from = len(rep_stream_buf)
            else:
                # Normal streaming - use holdback and safety checks
                safe_end = max(rep_emit_from, len(rep_stream_buf) - REP_EMIT_HOLDBACK)
                if safe_end <= rep_emit_from:
                    return
                raw_slice = rep_stream_buf[rep_emit_from:safe_end]

                safe_chunk, _ = split_safe_citation_prefix(raw_slice)
                safe_chunk, _ = _split_safe_usage_prefix(safe_chunk)
                safe_chunk, _ = _split_safe_marker_prefix(safe_chunk, end_marker)

                if safe_chunk:
                    await _rep_emit_visible(safe_chunk)
                    rep_emit_from += len(safe_chunk)

        async def on_delta_repair(piece: str):
            nonlocal rep_stream_buf, emitted_count
            if not piece:
                return
            repair_buf.append(piece)
            rep_stream_buf += piece
            # Use buffer-based flushing for ALL formats
            await _rep_flush_safe(force=False)

        async def on_complete_repair(_):
            nonlocal emitted_count
            # FINAL flush for ALL formats
            await _rep_flush_safe(force=True)
            emitted_count += 1
            await emit_delta("", completed=True, index=emitted_count, marker=channel_to_stream, agent=rep_author, format=tgt or "markdown", artifact_name=artifact_name)

        async with with_accounting(bundle_id,
                                   track_id=track_id,
                                   agent=role,
                                   metadata={
                                       "track_id": track_id,
                                       "agent": role,
                                       "agent_name": agent_name
                                   }):
            await _SERVICE.stream_model_text_tracked(
                client,
                [SystemMessage(content=repair_sys), HumanMessage(content="\n".join(repair_instruction) + "\n\n" + payload_for_repair)],
                on_delta=on_delta_repair,
                on_complete=on_complete_repair,
                temperature=0.1,
                max_tokens=min(max_tokens, 4000),
                role=role,
            )

        repaired = "".join(repair_buf)
        if tgt in ("json", "yaml"):
            repaired = _unwrap_fenced_block(repaired, lang=tgt)
            repaired = _strip_bom_zwsp(repaired)
        else:
            repaired = _strip_code_fences(repaired, allow=code_fences).strip()

        # Re-validate after repair
        content_clean = repaired or content_clean

        # Accept XML if HTML-like check failed but content is XML
        if tgt == "html" and content_clean.lstrip().startswith(("<?xml", "<xsl:")):
            tgt = "xml"

        if tgt in ("html", "xml"):
            content_clean = _trim_to_last_safe_tag_boundary(content_clean)

        fmt_ok, fmt_reason = _format_ok(content_clean, tgt)

        if tgt == "json":
            payload_obj, parse_err = _parse_json(content_clean)
            if parse_err:
                fmt_ok = False
                fmt_reason = parse_err
            if schema_json and payload_obj is not None:
                schema_ok, schema_reason = _validate_json_schema(payload_obj, schema_json)
                if not schema_ok and citation_container_path:
                    # Retry validation with sidecar removed (schema might not model it)
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
            elif tgt in ("json", "yaml") and eff_embed == "sidecar":
                if payload_obj is None:
                    payload_obj, _ = (_parse_json(content_clean) if tgt == "json" else _parse_yaml(content_clean))
                if payload_obj is not None:
                    ok2, why2 = _validate_sidecar(payload_obj, citation_container_path, valid_sids)
                    citations_ok = ok2
                    citations_status = "present" if ok2 else f"missing:{why2}"
                    if allow_inline_citations_in_strings and not ok2:
                        if _dfs_string_contains_inline_cite(payload_obj):
                            citations_ok = True
                            citations_status = "present_inline_only"
                else:
                    citations_ok = False
                    citations_status = "missing:payload_not_parsed"

        validated_tag = (
            "none" if not fmt_ok and (not schema_ok if tgt in ("json", "yaml") else True)
            else "format" if fmt_ok and (tgt not in ("json", "yaml"))
            else "schema" if (tgt in ("json", "yaml") and schema_ok and not fmt_ok)
            else "both" if fmt_ok and (schema_ok or tgt not in ("json", "yaml"))
            else "none"
        )
        ok = fmt_ok and (schema_ok if tgt in ("json", "yaml") else True) and (citations_ok if require_citations else True)
        reason = repair_msg if not ok else (reason or "repaired_ok")
        repair_reasons.append(reason)

    # --- derive used_sids from the artifact itself ---
    artifact_used_sids: List[int] = []

    if tgt in ("markdown", "text", "html"):
        # best-effort: extract from inline tokens or <sup class="cite"...>
        # For HTML we can opportunistically reuse the same MD pattern plus the HTML sup placeholder
        from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any
        artifact_used_sids = extract_citation_sids_any(content_clean)

    elif tgt in ("json", "yaml"):
        # collect from sidecar if present
        try:
            if payload_obj is None:
                payload_obj, _ = (_parse_json(content_clean) if tgt == "json" else _parse_yaml(content_clean))
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

    # Combine with telemetry (if present)
    combined_used_sids = sorted(set((artifact_used_sids or []) + (usage_sids or [])))
    # Resolve used_sources (expand to url/title when possible)
    sources_used = []
    if combined_used_sids:
        cm = build_citation_map_from_sources(sources_json)
        for sid in combined_used_sids:
            meta = cm.get(sid) or {}
            sources_used.append({
                "sid": sid,
                "url": meta.get("url", ""),
                "title": meta.get("title", ""),
                "text": meta.get("text") or meta.get("body") or meta.get("content") or "",
            })

    # --------- finalize ---------
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
    out = {
        "ok": bool(ok),
        "content": content_clean,
        "format": tgt,
        "finished": bool(finished),
        "retries": max(0, used_rounds - 1),
        "reason": ("" if ok else (fmt_reason if not fmt_ok else schema_reason if tgt in ("json", "yaml") and not schema_ok else citations_status)),
        "stats": {
            "rounds": used_rounds,
            "bytes": len(content_clean.encode("utf-8")),
            "validated": validated_tag,
            "citations": citations_status if require_citations else "n/a"
        },
        "sources_used": sources_used,
    }
    return json.dumps(out, ensure_ascii=False)

async def sources_reconciler(
        _SERVICE,
        objective: Annotated[str, "Objective (what we are trying to achieve with these sources)."],
        queries: Annotated[List[str], "Array of [q1, q2, ...]"],
        sources_list: Annotated[List[Dict[str, Any]], 'Array of {"sid": int, "title": str, "body": str}'],
        max_items: Annotated[int, "Optional: cap of kept sources (default 12)."] = 12
) -> Annotated[str, 'JSON array of kept sources: [{sid, verdict, o_relevance, q_relevance:[{qid,score}], reasoning}]']:

    assert _SERVICE, "ReconcileTools not bound to service"

    _RECONCILER_INSTRUCTION = """
        You are a strict source reconciler.
    
    GOAL
    - Input: (1) objective, (2) queries (qid→string), (3) sources [{sid,title,body}]. 
    - Return ONLY sources relevant to the objective AND at least one query.
    - If a source is irrelevant, DO NOT include it  at all (omit it entirely).
    - Output MUST validate against the provided JSON Schema.
    
    SCORING
    - o_relevance: overall support for objective (0..1).
    - q_relevance: per-query [{qid,score}] (0..1).
    Anchors: 0.90–1.00=direct; 0.60–0.89=mostly; 0.30–0.59=weak; <0.30=irrelevant.
    
    HEURISTICS (conservative)
    - Prefer official/primary sources (standards/regulators/vendor docs) over SEO blogs.
    - Penalize generic landing pages requiring click-through.
    - Use title/heading/body overlap; dedupe near-duplicates.
    - When uncertain, drop.
    
    OUTPUT (JSON ONLY)
    - Array of kept items ONLY: {sid, o_relevance, q_relevance:[{qid,score}], reasoning}
    - Reasoning ≤320 chars; cite concrete clues.
    - No prose outside JSON.
    """.strip()

    _RECONCILER_SCHEMA = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sid", "o_relevance", "q_relevance", "reasoning"],
            "properties": {
                "sid": {"type": "integer"},
                "o_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "q_relevance": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["qid", "score"],
                        "properties": {
                            "qid": {"type": "string"},
                            "score": {"type": "number", "minimum": 0, "maximum": 1}
                        }
                    }
                },
                "reasoning": {"type": "string", "maxLength": 320}
            }
        },
        "minItems": 0
    }

    # --- Normalize inputs ---
    queries_dict: Dict[str, str] = {
        str(i + 1): (q or "").strip()
        for i, q in enumerate(queries or [])
        if (q or "").strip()
    }

    prepared_sources: List[Dict[str, Any]] = []
    for row in (sources_list or []):
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        title = (row.get("title") or "").strip()
        text = (row.get("body") or row.get("text") or row.get("content") or "").strip()
        if not (sid and (title or text)):
            continue
        prepared_sources.append({"sid": sid, "title": title, "text": text})

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries_dict
    }

    schema_str = json.dumps(_RECONCILER_SCHEMA, ensure_ascii=False)

    # NOTE: generate_content_llm sets its own role internally; don't pass role=...
    llm_resp_s = await generate_content_llm(
        _SERVICE=_SERVICE,
        agent_name="Sources Reconciler",
        instruction=_RECONCILER_INSTRUCTION,
        input_context=json.dumps(input_ctx, ensure_ascii=False),
        target_format="json",
        schema_json=schema_str,
        sources_json=json.dumps(prepared_sources, ensure_ascii=False),
        cite_sources=False,
        max_rounds=2,
        max_tokens=1200,
        strict=True,
        role="tool.source.reconciler",
        cache_instruction=True,
        artifact_name=None,
        channel_to_stream="debug"
    )

    # --- Parse tool envelope ---
    try:
        env = json.loads(llm_resp_s) if llm_resp_s else {}
    except Exception:
        logger.exception("sources_reconciler: cannot parse LLM envelope")
        env = {}

    ok = bool(env.get("ok"))
    content_str = env.get("content") or ""
    reason = env.get("reason") or ""
    stats = env.get("stats") or {}

    if not ok:
        logger.warning("sources_reconciler: LLM not-ok. reason=%s stats=%s", reason, stats)

    # Strip accidental fences
    raw = content_str.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]

    # --- Parse reconciled array (kept-only) ---
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        logger.exception("sources_reconciler: invalid JSON content from model")
        arr = []

    # Coerce/clean; there should be NO dropped items in arr by contract
    kept: List[Dict[str, Any]] = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get("sid"))
        except Exception:
            continue
        try:
            orel = float(it.get("o_relevance"))
        except Exception:
            orel = 0.0

        # normalize q_relevance
        qrel_in = it.get("q_relevance") or []
        qrel_out = []
        for qr in qrel_in:
            if not isinstance(qr, dict):
                continue
            qid = str(qr.get("qid"))
            try:
                score = float(qr.get("score"))
            except Exception:
                continue
            qrel_out.append({"qid": qid, "score": score})

        reasoning = (it.get("reasoning") or "").strip()
        kept.append({
            "sid": sid,
            "o_relevance": orel,
            "q_relevance": qrel_out,
            "reasoning": reasoning[:320]
        })

    # Sort + cap
    kept.sort(key=lambda x: x.get("o_relevance", 0.0), reverse=True)
    if isinstance(max_items, int) and max_items > 0:
        kept = kept[:max_items]

    # --- Logging: brief analytics
    kept_sids = [k["sid"] for k in kept]
    logger.warning(
        "sources_reconciler: objective='%s' kept=%d sids=%s stats=%s reason=%s",
        (objective or "")[:160], len(kept), kept_sids[:12], stats, reason
    )

    return json.dumps(kept, ensure_ascii=False)

async def sources_content_filter(
        _SERVICE,
        objective: Annotated[str, "Objective (what we are trying to achieve)."],
        queries: Annotated[List[str], "Array of queries [q1, q2, ...]"],
        # note: we now document optional date fields explicitly
        sources_with_content: Annotated[List[Dict[str, Any]], 'Array of {"sid": int, "content": str, "published_time_iso"?: str, "modified_time_iso"?: str}'],
) -> Annotated[List[int], 'List of SIDs to keep']:
    """
    Fast content-based filter to remove duplicates and low-quality content.

    Args:
        _SERVICE: Service instance
        objective: What we're trying to achieve
        queries: List of search queries
        sources_with_content: List of {sid, content, published_time_iso?, modified_time_iso?}

    Returns:
        List of SIDs to keep
    """

    assert _SERVICE, "ContentFilter not bound to service"

    now_iso = datetime.now(timezone.utc).isoformat()
    _FILTER_INSTRUCTION = f"""
You are a content quality filter. Return a JSON array of SIDs to keep.

INPUTS
- objective: what we are trying to achieve
- queries: related search queries
- sources: list of items with {{sid, content, published_time_iso?, modified_time_iso?}}

GOAL: Return ONLY a JSON array of SIDs to keep. Keep the minimal set that best addresses the objective and queries.

EVALUATION CRITERIA (apply in order):

1. RELEVANCE (primary)
   - Keep: Content directly supports objective or answers queries
   - Drop: Off-topic or tangential content

2. SUBSTANCE (primary)
   - Keep: Actionable details (how-to, examples, configurations, data, analysis)
   - Keep: Meaningful text (>150 chars), clear explanations
   - Drop: Just menus/headers/boilerplate, vague overviews without depth

3. UNIQUENESS (deduplication)
   - If 2+ sources cover the same topic with >70% overlap, keep ONLY the best one
   - "Best" = more complete, more actionable, clearer

4. FRESHNESS (tie-breaker only)
   - Use modified_time_iso or published_time_iso when available
   - Prefer recent over old when substance is equal
   - Missing dates = no penalty

SAFEGUARD: If any source has substance, keep at least 1 SID (even if imperfect).

OUTPUT: [sid1, sid2, ...] - Array of integers only, no text.

TODAY: {now_iso}
""".strip()

    _FILTER_SCHEMA = {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 0
    }

    # Prepare sources for filtering
    prepared_sources: List[Dict[str, Any]] = []
    for row in (sources_with_content or []):
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        content = (row.get("content") or "").strip()
        if not (sid and content):
            continue
        content_truncated = content[:2000] if len(content) > 2000 else content

        prepared_sources.append({
            "sid": sid,
            "content": content_truncated,
            "published_time_iso": row.get("published_time_iso"),
            "modified_time_iso": row.get("modified_time_iso"),
        })

    # If too few sources, keep all
    if len(prepared_sources) <= 2:
        return [s["sid"] for s in prepared_sources]

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries or []
    }

    schema_str = json.dumps(_FILTER_SCHEMA, ensure_ascii=False)

    try:
        # Use cheaper/faster settings for content filtering
        llm_resp_s = await generate_content_llm(
            _SERVICE=_SERVICE,
            agent_name="Content Filter",
            instruction=_FILTER_INSTRUCTION,
            input_context=json.dumps(input_ctx, ensure_ascii=False),
            target_format="json",
            schema_json=schema_str,
            sources_json=json.dumps(prepared_sources, ensure_ascii=False),
            cite_sources=False,
            max_rounds=1,
            max_tokens=300,
            strict=True,
            role="tool.sources.filter.by.content",
            cache_instruction=True,
            artifact_name=None,
            channel_to_stream="debug"
        )
    except Exception:
        logger.exception("sources_content_filter: LLM call failed; keeping all sources")
        return [s["sid"] for s in prepared_sources]

    # Parse response
    try:
        env = json.loads(llm_resp_s) if llm_resp_s else {}
    except Exception:
        logger.exception("sources_content_filter: cannot parse LLM envelope")
        return [s["sid"] for s in prepared_sources]

    content_str = (env.get("content") or "").strip()
    if content_str.startswith("```"):
        content_str = content_str.split("\n", 1)[1] if "\n" in content_str else content_str
        if "```" in content_str:
            content_str = content_str.rsplit("```", 1)[0]

    try:
        kept_sids = json.loads(content_str) if content_str else []
        if not isinstance(kept_sids, list):
            logger.warning("sources_content_filter: response is not an array")
            return [s["sid"] for s in prepared_sources]

        # Validate all items are integers
        kept_sids = [int(sid) for sid in kept_sids if isinstance(sid, (int, str)) and str(sid).isdigit()]

        # Ensure we're not keeping SIDs that don't exist
        valid_sids = {s["sid"] for s in prepared_sources}
        kept_sids = [sid for sid in kept_sids if sid in valid_sids]

        logger.info(
            f"sources_content_filter: objective='{(objective or '')[:100]}' "
            f"input={len(prepared_sources)} kept={len(kept_sids)} "
            f"dropped={len(prepared_sources) - len(kept_sids)}"
        )

        return kept_sids

    except Exception:
        logger.exception("sources_content_filter: failed to parse kept SIDs; keeping all")
        return [s["sid"] for s in prepared_sources]
# apps/chat/sdk/tools/web/filter_segmenter.py
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import json

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import \
    _stream_agent_two_sections_to_json as _stream_agent_sections_to_json
# Import the existing instructions from the shared module
import kdcube_ai_app.apps.chat.sdk.tools.web.content_filters as content_filters

def _get_2section_protocol_filter_segmenter(json_shape_hint: str) -> str:
    """
    Strict 2-part protocol for filter + segmenter:

      1) THINKING CHANNEL (user-facing, shows Phase 1 and Phase 2 progress)
      2) STRUCTURED JSON CHANNEL (spans dict as JSON)
    """
    return (
        "\n\nCRITICAL OUTPUT PROTOCOL — TWO SECTIONS, IN THIS ORDER:\n"
        "• You MUST produce EXACTLY TWO SECTIONS in this order.\n"
        "• Use EACH START marker below EXACTLY ONCE.\n"
        "• NEVER write any END markers like <<< END ... >>>.\n"
        "• The SECOND section must be a fenced JSON block and contain ONLY JSON.\n\n"

        "CHANNEL 1 — THINKING CHANNEL (user-facing status):\n"
        "Marker:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "Immediately after this marker, write your Phase 1 and Phase 2 analysis.\n"
        "- Use the exact headers: 'Phase 1 - Filtering:' and 'Phase 2 - [Mode Name]:'\n"
        "- Keep it concise (2-4 sentences per phase)\n"
        "- Plain language only (no JSON, no technical details)\n"
        "- Explain which pages you're keeping/dropping and what content you're extracting\n"
        "- Do NOT mention 'sid', JSON structure, or internal identifiers\n"
        "- Do NOT emit any other BEGIN/END markers inside this channel\n\n"

        "CHANNEL 2 — STRUCTURED JSON CHANNEL (spans dict):\n"
        "Marker:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "Immediately after this marker, output ONLY a ```json fenced block with the result:\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"

        "STRICT RULES FOR CHANNEL 2 (JSON):\n"
        "1. Channel 2 MUST contain ONLY a single JSON object.\n"
        "2. JSON MUST be inside the ```json fenced block shown above.\n"
        "3. DO NOT write any text, markdown, or comments before ```json.\n"
        "4. DO NOT write anything after the closing ``` (no prose, no markers).\n"
        "5. DO NOT write any other code fences.\n"
        "6. The JSON must be valid and match the expected structure.\n"
        "7. Structure: {\"<sid>\": [{\"s\": \"...\", \"e\": \"...\"}], ...}\n"
        "8. Empty dict {} if all pages were dropped.\n\n"
    )


def _get_balanced_instruction(now_iso: str, thinking_budget: int) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES (BALANCED MODE)

You are a content extraction tool processing web pages in two phases.

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING BUDGET
═══════════════════════════════════════════════════════════════

Your thinking output is limited to {thinking_budget} tokens.
Keep Phase 1 and Phase 2 analysis brief (2-4 sentences each).

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

Purpose: Decide which pages to keep or drop based on the objective.

Input:
- INPUT CONTEXT with "objective" and "queries" fields
- SOURCES with sid, url, and content

Decision criteria:

KEEP if:
- Directly addresses the objective or queries
- Contains information that helps achieve the objective

DROP if:
- Only tangentially related to the objective
- Duplicates another kept page (≥90% content overlap)
- Lacks substantive information

Phase 1 output: List of kept SIDs

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT TARGET + CORPUS CONTENT
═══════════════════════════════════════════════════════════════

Purpose: Extract TARGET (directly relevant) + CORPUS (supporting context)

Goal: Capture 50-70% of page content

TARGET (always include):
- Sections that directly address the objective

CORPUS (include for context):
- Related topics closely connected to target
- Background information, prerequisites, definitions
- Supporting data, complementary specifications
- Comparisons, tradeoffs involving the target
- Limitations, caveats, notes about the target

EXCLUDE:
- Major sections on different topics
- Unrelated product features
- All chrome: navigation, ads, footers, CTAs

Extraction strategy:
- Use 1-2 spans per page
- Each span covers TARGET + its related CORPUS
- Each span must be ≥800 characters OR ≥3 complete blocks

Balanced approach:
- When a section is TARGET → definitely include
- When a section is CORPUS (supports understanding) → include
- When uncertain if CORPUS or unrelated → include (favor context)
- When clearly unrelated to objective → exclude

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

's' anchor (start):
- First TARGET or CORPUS heading/paragraph
- Copy exactly 3-8 consecutive words
- Extend if needed to make unique on page

'e' anchor (end) - EXCLUSIVE:
- Span ends BEFORE first character of 'e'
- Empty string "" → content continues to end of page
- OR 3-8 words copied from AFTER the last TARGET/CORPUS content

Requirements:
- EXACT character-for-character copy (Ctrl+C / Ctrl+V)
- Preserve punctuation, case, spacing
- Must be unique on page
- From real page text (not URLs/image-alt)
- Not from chrome sections
- 's' before 'e' in document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY
═══════════════════════════════════════════════════════════════

Atomic units: Paragraph, list item, table row, code block, Q&A pair, FAQ item

If ANY part of a block is TARGET or CORPUS → include ENTIRE block.
Place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return JSON mapping sid -> list of spans:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped: {{}}
"""


def _get_precision_instruction(now_iso: str, thinking_budget: int) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES (PRECISION MODE)

You are a content extraction tool processing web pages in two phases.

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING BUDGET
═══════════════════════════════════════════════════════════════

Your thinking output is limited to {thinking_budget} tokens.
Keep Phase 1 and Phase 2 analysis brief (2-4 sentences each).

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

Purpose: Decide which pages to keep or drop based on the objective.

Input:
- INPUT CONTEXT with "objective" and "queries" fields
- SOURCES with sid, url, and content

Decision criteria:

KEEP if:
- Directly addresses the objective or queries
- Contains specific information that helps achieve the objective

DROP if:
- Only tangentially related to the objective
- Duplicates another kept page (≥90% content overlap)
- Lacks substantive information

Phase 1 output: List of kept SIDs

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT OBJECTIVE-RELEVANT CONTENT
═══════════════════════════════════════════════════════════════

Purpose: Extract content that directly addresses the objective

Goal: Capture 20-50% of page content - only what directly addresses the objective

INCLUDE in spans:
- Content that directly answers the queries
- Information specifically requested in the objective
- Data, specifications, details that address the question
- Examples or explanations directly relevant to the objective
- Tables/lists containing requested information

EXCLUDE from spans:
- Content unrelated to the objective (even if informative on the page)
- General background not directly addressing the question
- Tangential sections about different topics
- All chrome: navigation, ads, footers, CTAs

Extraction strategy:
- Use 1-3 spans per page
- Each span covers one relevant section or cluster of relevant content
- Skip large sections that don't address the objective
- Each span must be ≥300 characters OR ≥1 complete block

Precision approach:
- When a section partially addresses the objective → include only that section
- When uncertain if content is relevant → EXCLUDE it
- Focus on quality over quantity - only include direct answers

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

's' anchor (start):
- First heading or sentence of the relevant section
- Copy exactly 3-8 consecutive words
- Extend if needed to make unique on page

'e' anchor (end) - EXCLUSIVE:
- Span ends BEFORE first character of 'e'
- Empty string "" → content continues to end of page
- OR 3-8 words copied from AFTER the last relevant content

Requirements:
- EXACT character-for-character copy (Ctrl+C / Ctrl+V)
- Preserve punctuation, case, spacing
- Must be unique on page
- From real page text (not URLs/image-alt)
- Not from chrome sections
- 's' before 'e' in document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY
═══════════════════════════════════════════════════════════════

Atomic units: Paragraph, list item, table row, code block, Q&A pair, FAQ item

If ANY part of a block is relevant to the objective → include the ENTIRE block.
Place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return JSON mapping sid -> list of spans:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped: {{}}
"""


def _get_recall_instruction(now_iso: str, thinking_budget: int) -> str:
    return f"""
CONTENT EXTRACTION TASK: TWO DISTINCT PHASES (RECALL MODE)

You are a content extraction tool processing web pages in two phases.

TODAY: {now_iso}

═══════════════════════════════════════════════════════════════
THINKING BUDGET
═══════════════════════════════════════════════════════════════

Your thinking output is limited to {thinking_budget} tokens.
Keep Phase 1 and Phase 2 analysis brief (2-4 sentences each).

═══════════════════════════════════════════════════════════════
PHASE 1: FILTER PAGES BY RELEVANCE
═══════════════════════════════════════════════════════════════

Purpose: Decide which pages to keep or drop based on search domain relevance.

Input:
- INPUT CONTEXT with "objective" and "queries" fields (for Phase 1 only)
- SOURCES with sid, url, and content

Decision criteria:

KEEP if:
- Belongs to the same domain/topic as the objective
- Contains substantive content (paragraphs, tables, documentation)
- Even if it doesn't directly answer the user's specific question

DROP if:
- From a completely different domain
- Duplicates another kept page (≥90% content overlap)
- Has no real content (nav-only, error page, pure ads)

Phase 1 output: List of kept SIDs

═══════════════════════════════════════════════════════════════
PHASE 2: EXTRACT CONTENT BODIES FROM KEPT PAGES
═══════════════════════════════════════════════════════════════

CRITICAL: IGNORE the objective/queries in Phase 2. Extract ALL substantive content.

Purpose: Identify where the content body starts and ends on each kept page

Goal: Capture 80-95% of substantive content on the page

Content body INCLUDES (substantive material):
- All paragraphs and sections
- All tables, lists, structured data
- All code blocks and configurations
- All examples and tutorials
- All Q&A sections and FAQs
- All technical details, specifications, parameters
- All background information and context
- All comparisons and recommendations
- Everything a human would read to learn from this page

Content body EXCLUDES (page chrome):
- Navigation menus and breadcrumbs
- Site headers and hero banners
- Call-to-action buttons
- Promotional carousels and product tiles
- Social media buttons
- Cookie notices and legal banners
- Footer link grids
- Partner logo walls

Extraction strategy:
- Default: 1 large span covering the entire content body
- Only use 2 spans if a massive non-informative block splits the content
- Each span must be ≥1,000 characters OR ≥3 complete blocks

High-recall approach:
- When uncertain if something is content or chrome → treat as content
- When a section seems somewhat useful → include it
- Better to include extra text than risk cutting useful content
- Extend span boundaries generously

═══════════════════════════════════════════════════════════════
TEXT ANCHORS: TECHNICAL SPECIFICATIONS
═══════════════════════════════════════════════════════════════

's' anchor (start):
- First substantive heading or paragraph (skip past navigation/header)
- Copy exactly 3-8 consecutive words
- Extend if needed to make unique on page

'e' anchor (end) - EXCLUSIVE:
- Span ends BEFORE first character of 'e'
- Empty string "" → content continues to end of page
- OR 3-8 words copied from AFTER the last substantive content

Requirements:
- EXACT character-for-character copy (Ctrl+C / Ctrl+V)
- Preserve punctuation, case, spacing
- Must be unique on page
- From real page text (not URLs/image-alt)
- Not from chrome sections
- 's' before 'e' in document (unless 'e' is "")

═══════════════════════════════════════════════════════════════
BLOCK INTEGRITY
═══════════════════════════════════════════════════════════════

Atomic units: Paragraph, list item, table row, code block, Q&A pair, FAQ item

If ANY part of a block is in the content body → include the ENTIRE block.
Place anchors on block boundaries, never mid-block.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return JSON mapping sid -> list of spans:
{{"<sid>": [{{"s": "...", "e": "..."}}], ...}}

If all pages dropped: {{}}
"""


async def filter_and_segment_stream(
        svc: ModelServiceBase,
        *,
        objective: str,
        queries: List[str],
        sources_with_content: List[Dict[str, Any]],
        mode: str = "balanced",  # "balanced", "precision", "recall"
        on_thinking_fn: Optional[Any] = None,
        thinking_budget: int = 180,
        max_tokens: int = 700,
        role: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Filter and segment sources using 2-fold streaming (thinking + JSON).

    Args:
        svc: Model service
        objective: What we're trying to achieve
        queries: List of search queries
        sources_with_content: List of {sid, url, content, published_time_iso?, modified_time_iso?}
        mode: "balanced", "precision", or "recall"
        on_thinking_fn: Callback for thinking output
        thinking_budget: Token budget for thinking channel
        max_tokens: Total token budget

    Returns:
        Dict with 'agent_response' containing the spans dict (str sid -> list of span dicts)
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    # Reuse the existing instructions from the shared module
    if mode == "precision":
        core_instruction = content_filters.FILTER_AND_SEGMENT_HIGH_PRECISION(now_iso)
    elif mode == "recall":
        core_instruction = content_filters.FILTER_AND_SEGMENT_HIGH_RECALL(now_iso)
    else:  # balanced
        core_instruction = content_filters.FILTER_AND_SEGMENT_BALANCED(now_iso)

    # Add thinking budget note
    thinking_note = f"\n\nTHINKING BUDGET: Your thinking output is limited to {thinking_budget} tokens. Keep both Phase 1 and Phase 2 analysis brief.\n"

    # JSON shape hint for the protocol
    schema = (
        "{\n"
        "  \"<sid>\": [{\"s\": \"start anchor text\", \"e\": \"end anchor text or empty\"}],\n"
        "  \"<sid>\": [{\"s\": \"...\", \"e\": \"...\"}]\n"
        "}\n"
        "or {} if all pages dropped"
    )

    two_section_proto = _get_2section_protocol_filter_segmenter(schema)

    # Combine: core instruction + thinking budget + 2-fold protocol
    system_msg = create_cached_system_message([
        {"text": core_instruction, "cache": True},
        {"text": thinking_note, "cache": False},
        {"text": two_section_proto, "cache": True}
    ])

    # Prepare input context
    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries or []
    }

    # Prepare sources
    prepared_sources = []
    for row in sources_with_content:
        try:
            sid = int(row.get("sid"))
        except Exception:
            continue
        content = (row.get("content") or "").strip()
        if not (sid and content):
            continue

        prepared_sources.append({
            "sid": sid,
            "url": row.get("url"),
            "content": content,  # Keep full content, model will extract spans
            "published_time_iso": row.get("published_time_iso"),
            "modified_time_iso": row.get("modified_time_iso"),
        })

    if not prepared_sources:
        return {"agent_response": {}}

    user_msg = (
        "INPUT CONTEXT:\n" + json.dumps(input_ctx, ensure_ascii=False) + "\n\n"
        "SOURCES:\n" + json.dumps(prepared_sources, ensure_ascii=False) + "\n\n"
        "Return exactly two sections: first THINKING (with Phase 1 and Phase 2), then JSON."
    )
    role = role or "tool.sources.filter.by.content.and.segment"
    # Use the 2-fold streaming utility with NO schema_model (parse as raw dict)
    out = await _stream_agent_sections_to_json(
        svc,
        client_name=role,
        client_role=role,
        sys_prompt=system_msg,
        user_msg=user_msg,
        schema_model=None,  # Parse as raw dict, no Pydantic validation
        on_progress_delta=on_thinking_fn,
        ctx="filter.segmenter",
        max_tokens=max_tokens
    )

    if not out:
        return {"agent_response": {}}

    # The output should be a dict mapping sid -> list of span objects
    # Return as-is, validation happens in sources_filter_and_segment
    try:
        raw_response = out.get("agent_response") or {}

        # If it's already a dict, return it as-is
        # The validation will happen in sources_filter_and_segment
        if isinstance(raw_response, dict):
            return out
        else:
            out["agent_response"] = {}
            return out

    except Exception:
        return {"agent_response": {}}
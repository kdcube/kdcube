# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/web/filter_segmenter.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import json

from kdcube_ai_app.apps.chat.sdk.util import _today_str
from kdcube_ai_app.infra.accounting import with_accounting
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
        "\n\n[CRITICAL OUTPUT PROTOCOL — TWO SECTIONS, IN THIS ORDER]:\n"
        "• You MUST produce EXACTLY TWO SECTIONS in this order.\n"
        "• Use EACH START marker below EXACTLY ONCE.\n"
        "• NEVER write any END markers like <<< END ... >>>.\n"
        "• The SECOND section must be a fenced JSON block and contain ONLY JSON.\n\n"

        "⚠️ CRITICAL: THINKING BUDGET & JSON PRIORITY ⚠️\n"
        "• THINKING is strictly LIMITED and must be BRIEF (≤150 tokens).\n"
        "• JSON output is MANDATORY — if you run low on tokens, CUT THINKING SHORT and complete JSON.\n"
        "• Expected output: 6-10 sources max in JSON.\n"
        "• In THINKING: mention ONLY major decisions (e.g., 'kept 7/15 pages', 'extracted 50% avg').\n"
        "• DO NOT analyze each source individually in thinking.\n"
        "• DO NOT list every SID in thinking. And even do not mention SID, this is internal thing. Only what might be useful for user!\n"
        "• Keep it to 3-5 SHORT sentences total across both phases.\n"
        "• If uncertain whether to include detail → SKIP IT and prioritize JSON completion.\n\n"

        "CHANNEL 1 — THINKING CHANNEL (user-facing status):\n"
        "Marker:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "Immediately after this marker, write your Phase 1 and Phase 2 analysis.\n"
        "- Use the exact headers: 'Phase 1 - Filtering:' and 'Phase 2 - [Mode Name]:'\n"
        "- TOTAL: 3-5 short sentences covering BOTH phases (not per phase)\n"
        "- Mention aggregates only: 'kept X/Y pages', 'avg coverage Z%'\n"
        "- Plain language only (no JSON, no technical details, no SID lists)\n"
        "- Do NOT analyze each source separately\n"
        "- Do NOT emit any other BEGIN/END markers inside this channel\n"
        "- If approaching token limit → write '…' and move to JSON immediately\n\n"

        "CHANNEL 2 — STRUCTURED JSON CHANNEL (spans dict):\n"
        "Marker:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "Immediately after this marker, output ONLY a ```json fenced block with the result:\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"

        "[STRICT RULES FOR CHANNEL 2 (JSON)]:\n"
        "1. Channel 2 MUST contain ONLY a single JSON object.\n"
        "2. JSON MUST be inside the ```json fenced block shown above.\n"
        "3. DO NOT write any text, markdown, or comments before ```json.\n"
        "4. DO NOT write anything after the closing ``` (no prose, no markers).\n"
        "5. DO NOT write any other code fences.\n"
        "6. The JSON must be valid and match the expected structure.\n"
        "7. Structure: {\"<sid>\": [{\"s\": \"...\", \"e\": \"...\"}, {\"s\": \"...\", \"e\": \"...\"}], ...}\n"
        "8. Empty dict {} if all pages were dropped.\n\n"
    )

async def filter_and_segment_stream(
        svc: ModelServiceBase,
        *,
        objective: str,
        queries: List[str],
        sources_with_content: List[Dict[str, Any]],
        mode: str = "balanced",  # "balanced", "precision", "recall"
        on_progress_delta: Optional[Any] = None,
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
    thinking_note = f"\n\nTHINKING BUDGET: Your thinking output is limited to {thinking_budget} tokens. Keep both Phase 1 and Phase 2 analysis VERY brief.\n"

    # JSON shape hint for the protocol
    schema = (
        "{\n"
        "  \"<sid>\": [{\"s\": \"start anchor text\", \"e\": \"end anchor text or empty\"}],\n"
        "  \"<sid>\": [{\"s\": \"...\", \"e\": \"...\"}]\n"
        "}\n"
        "or {} if all pages dropped"
    )

    two_section_proto = _get_2section_protocol_filter_segmenter(schema)
    from kdcube_ai_app.infra.accounting import _get_context

    context = _get_context()
    context_snapshot = context.to_dict()

    timezone = context_snapshot.get("timezone")

    today = _today_str()
    from kdcube_ai_app.apps.chat.sdk.util import _now_up_to_minutes
    now = _now_up_to_minutes()

    TIMEZONE = timezone or "Europe/Berlin"
    time_evidence = (
        "AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)\n"
        f"Current UTC date: {today}\n"
        # "User timezone: Europe/Berlin\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    time_evidence_reminder = f"Very important: The user's timezone is {TIMEZONE}. Current UTC timestamp: {now}. Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future. When dealing with modern entities/companies/people, and the user asks for the 'latest', 'most recent', 'today's', etc. don't assume your knowledge is up to date; you MUST carefully confirm what the true 'latest' is first. If the user seems confused or mistaken about a certain date or dates, you MUST include specific, concrete dates in your response to clarify things. This is especially important when the user is referencing relative dates like 'today', 'tomorrow', 'yesterday', etc -- if the user seems mistaken in these cases, you should make sure to use absolute/exact dates like 'January 1, 2010' in your response.\n"

    # Combine: core instruction + thinking budget + 2-fold protocol
    system_msg = create_cached_system_message([
        {"text": time_evidence + "\n" + core_instruction, "cache": True},
        {"text": two_section_proto, "cache": True},
        {"text": time_evidence_reminder, "cache": False},
        {"text": thinking_note, "cache": False},
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
    from kdcube_ai_app.infra.accounting import _get_context

    context = _get_context()
    context_snapshot = context.to_dict()

    bundle_id = context_snapshot.get("app_bundle_id")

    async with with_accounting(
            bundle_id,
            agent=role,
            metadata={
                "agent": role,
                "agent_name": "Filter Segmenter LLM"
            }
    ):
        out = await _stream_agent_sections_to_json(
            svc,
            client_name=role,
            client_role=role,
            sys_prompt=system_msg,
            user_msg=user_msg,
            schema_model=None,  # Parse as raw dict, no Pydantic validation
            on_progress_delta=on_progress_delta,
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

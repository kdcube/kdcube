# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/tools/web/filter_segmenter_fast.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
import json
import re

from langchain_core.messages import HumanMessage

from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes, _json_loads_loose
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json
import kdcube_ai_app.apps.chat.sdk.tools.web.content_filters_fast as content_filters


def _strip_thinking_guidance(text: str) -> str:
    if not text:
        return text
    # Remove the dedicated THINKING section block
    text = re.sub(
        r"\n*════════{3,}\nTHINKING OUTPUT REQUIREMENT.*?(?=\n════════{3,}\nPHASE 1)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Remove explicit "Your thinking for Phase X" blocks
    text = re.sub(
        r"\n\*\*Your thinking for Phase 1:\*\*.*?(?=\n\s*\*\*Phase 1 output:\*\*|\n\s*════════{3,})",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"\n\*\*Your thinking for Phase 2:\*\*.*?(?=\n\s*════════{3,}|\n\s*\*\*Phase 2 output:\*\*)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Replace residual thinking references with neutral phrasing
    replacements = {
        "Signal this transition in your thinking:": "Signal this transition:",
        "After Phase 1 thinking, explicitly write:": "After Phase 1, explicitly write:",
        "Stop thinking about": "Stop considering",
        "Start thinking about": "Start focusing on",
        "Users see your thinking to understand your progress.": "",
        "DO NOT mention in Phase 2 thinking:": "DO NOT mention in Phase 2:",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _get_single_channel_protocol_filter_segmenter(json_shape_hint: str) -> str:
    return (
        "\n\n[CRITICAL OUTPUT PROTOCOL]:\n"
        "• Output ONLY a JSON object matching the schema below.\n"
        "• Do NOT include any thinking, commentary, or extra text.\n"
        "• Do NOT use any markers or additional code fences.\n"
        "• The response must be valid JSON that matches the structure exactly.\n\n"
        "EXPECTED JSON SHAPE:\n"
        f"{json_shape_hint}\n\n"
        "STRICT RULES:\n"
        "1. Output ONLY JSON (no markdown, no prose, no prefixes).\n"
        "2. Structure:\n"
        "   {\"phase1\": [<sid>, ...], \"phase2\": {\"<sid>\": [{\"s\": \"...\", \"e\": \"...\"}], ...}}\n"
        "3. If all pages are dropped: {\"phase1\": [], \"phase2\": {}}.\n"
        "4. If multiple pages are near-duplicates (≥90% overlap), keep ONLY the single best SID.\n"
        "5. In balanced and recall mode, prefer keep most information on the page in the spans you made.\n"
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
    Filter and segment sources using single-channel JSON output (no thinking channel).
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    input_definition_mode = "free" # "json"
    if mode == "precision":
        core_instruction = content_filters.FILTER_AND_SEGMENT_HIGH_PRECISION(now_iso, input_mode=input_definition_mode)
    elif mode == "recall":
        core_instruction = content_filters.FILTER_AND_SEGMENT_HIGH_RECALL(now_iso, input_mode=input_definition_mode)
    else:
        core_instruction = content_filters.FILTER_AND_SEGMENT_BALANCED(now_iso, input_mode=input_definition_mode)

    core_instruction = _strip_thinking_guidance(core_instruction)

    schema = (
        "{\n"
        "  \"phase1\": [12, 8],\n"
        "  \"phase2\": {\n"
        "    \"12\": [{\"s\": \"start anchor text for first segment\", \"e\": \"end anchor text or empty for first segment\"}],\n"
        "    \"8\": [{\"s\": \"...\", \"e\": \"...\"}, {\"s\": \"...\", \"e\": \"...\"}]\n"
        "  }\n"
        "}\n"
        "or {\"phase1\": [], \"phase2\": {}} if all pages dropped"
    )

    protocol = _get_single_channel_protocol_filter_segmenter(schema)
    from kdcube_ai_app.infra.accounting import _get_context

    context = _get_context()
    context_snapshot = context.to_dict()

    timezone = context_snapshot.get("timezone")
    today = _today_str()
    now = _now_up_to_minutes()

    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        f"Current UTC date: {today}\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    time_evidence_reminder = (
        f"Very important: The user's timezone is {timezone or 'Europe/Berlin'}. "
        f"Current UTC timestamp: {now}. Current UTC date: {today}. "
        "Any dates before this are in the past, and any dates after this are in the future. "
        "When dealing with modern entities/companies/people, and the user asks for the "
        "'latest', 'most recent', 'today's', etc. don't assume your knowledge is up to date; "
        "you MUST carefully confirm what the true 'latest' is first. If the user seems confused "
        "or mistaken about a certain date or dates, you MUST include specific, concrete dates "
        "in your response to clarify things. This is especially important when the user is "
        "referencing relative dates like 'today', 'tomorrow', 'yesterday', etc -- if the user "
        "seems mistaken in these cases, you should make sure to use absolute/exact dates like "
        "'January 1, 2010' in your response.\n"

        "[CRITICAL. CLEAN AND USEFUL OUTPUT]\n"
        "Your output gives us the instructions that we will use to locate, in original pages, snippets of data. We use the boundaries you produce to outline these snippets.\n"
        "The rest of the page outside the selected spans will be dropped. The information outside the boundaries won't be included in retrieval snippets.  If, on the page, the useful information is located outside of your 's'/'e' spans, we loose that information which is unacceptable.\n"
        # "Make sure your output follows the requirements, outlines the snippets that retain recall but DO NOT contain duplicated information. Boundaries that outline the snippets that contain duplicated information or lack valuable information are wrong.\n"
        "For BOUNDARIES 's' and 'e' you choose only short but distinctive phrases in the page text! Do not produce the book! \n"
        "You must read through all sources you see and solve both phases in the mind and only then generate the response. Duplicate or no you decide based on content of the source, the text snippet. Not based on the url!\n"
        # "Please choose, for your boundary, the line w/o the link or text with the special symbols or symbols that can ruin the json. It's better to provide the wider boundary using the closest clean distinctive text"
    )

    system_msg = create_cached_system_message([
        {"text": time_evidence + "\n" + core_instruction, "cache": True},
        {"text": protocol, "cache": True},
        {"text": time_evidence_reminder, "cache": False},
    ])

    input_ctx = {
        "objective": (objective or "").strip(),
        "queries": queries or [],
    }

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
            "content": content,
            "published_time_iso": row.get("published_time_iso"),
            "modified_time_iso": row.get("modified_time_iso"),
        })

    if not prepared_sources:
        return {"agent_response": {}}

    def _format_user_message(input_ctx: dict, prepared_sources: list) -> str:
        """Format input in a clear, structured way that aligns with instruction references."""

        # Header
        msg_parts = ["=" * 70]
        msg_parts.append("TASK SPECIFICATION")
        msg_parts.append("=" * 70)
        msg_parts.append("")

        # Search objective section
        msg_parts.append("📋 SEARCH OBJECTIVE:")
        msg_parts.append(f"   {input_ctx.get('objective', 'N/A')}")
        msg_parts.append("")

        # Search queries section (if present)
        queries = input_ctx.get('queries', [])
        if queries:
            msg_parts.append("🔍 SEARCH QUERIES USED:")
            for i, q in enumerate(queries, 1):
                msg_parts.append(f"   {i}. {q}")
            msg_parts.append("")

        # Sources section header
        msg_parts.append("=" * 70)
        msg_parts.append(f"SOURCE PAGES ({len(prepared_sources)} pages)")
        msg_parts.append("=" * 70)
        msg_parts.append("")

        # Format each source with clear structure
        for idx, src in enumerate(prepared_sources, 1):
            msg_parts.append(f"┌─ SOURCE #{idx} (SID: {src['sid']}) {'─' * 50}")
            msg_parts.append(f"│ URL: {src.get('url', 'N/A')}")

            if src.get('published_time_iso'):
                msg_parts.append(f"│ Published: {src['published_time_iso']}")
            if src.get('modified_time_iso'):
                msg_parts.append(f"│ Modified: {src['modified_time_iso']}")

            msg_parts.append("│")
            msg_parts.append("│ CONTENT:")

            # Add content with clear visual separation
            content = src.get('content', '').strip()
            if content:
                # Indent content for readability
                content_lines = content.split('\n')
                for line in content_lines[:3]:  # Show first few lines in summary
                    msg_parts.append(f"│ {line[:100]}")
                msg_parts.append(f"│")
                msg_parts.append(f"│ [Full content: {len(content)} characters]")
                msg_parts.append(f"│")
                # Add full content after visual break
                msg_parts.append(content)

            msg_parts.append(f"└{'─' * 65}")
            msg_parts.append("")

        # Closing instruction
        msg_parts.append("=" * 70)
        msg_parts.append("YOUR OUTPUT")
        msg_parts.append("=" * 70)
        msg_parts.append("Return ONLY the JSON object with the following structure:")
        msg_parts.append('{"phase1": [<sid>, ...], "phase2": {"<sid>": [{"s": "...", "e": "..."}], ...}}')
        msg_parts.append("")

        return "\n".join(msg_parts)

    def _format_user_message_json(input_ctx, prepared_sources):
        user_msg = (
                "INPUT CONTEXT:\n" + json.dumps(input_ctx, ensure_ascii=False) + "\n\n"
                "SOURCES:\n" + json.dumps(prepared_sources, ensure_ascii=False) + "\n\n"
                "Return ONLY the JSON object with phase1 + phase2."
        )
        return user_msg

    # Usage in your code:
    if input_definition_mode == "json":
        user_msg = _format_user_message_json(input_ctx, prepared_sources)
    else:
        user_msg = _format_user_message(input_ctx, prepared_sources)

    role = role or "tool.sources.filter.by.content.and.segment"

    context = _get_context()
    context_snapshot = context.to_dict()
    bundle_id = context_snapshot.get("app_bundle_id")

    async with with_accounting(
            bundle_id,
            agent=role,
            metadata={
                "agent": role,
                "agent_name": "Filter Segmenter LLM (fast)",
            }
    ):
        out = await stream_agent_to_json(
            svc,
            client_name=role,
            client_role=role,
            sys_prompt=system_msg,
            messages=[HumanMessage(content=user_msg)],
            schema_model=None,
            on_progress_delta=on_progress_delta,
            max_tokens=max_tokens,
        )

    if not out:
        return {"agent_response": {}}

    try:
        raw_text = (out.get("agent_response") or "").strip()
        parsed = _json_loads_loose(raw_text) if raw_text else {}
        if isinstance(parsed, dict):
            phase2 = parsed.get("phase2")
            if isinstance(phase2, dict):
                phase1 = parsed.get("phase1")
                if isinstance(phase1, list):
                    keep = {str(int(s)) for s in phase1 if isinstance(s, (int, str)) and str(s).strip()}
                    phase2 = {k: v for k, v in phase2.items() if str(k) in keep}
                out["agent_response"] = phase2
            else:
                out["agent_response"] = {}
        else:
            out["agent_response"] = {}
        return out
    except Exception:
        return {"agent_response": {}}

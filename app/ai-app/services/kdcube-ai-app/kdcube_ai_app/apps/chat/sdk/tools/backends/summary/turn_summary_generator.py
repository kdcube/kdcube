# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# sdk/tools/backends/summary/turn_summary_generator.py

from typing import Optional, List, Dict, Any, Tuple

from langchain_core.messages import HumanMessage, AIMessage

from kdcube_ai_app.apps.chat.sdk.tools.summary.contracts import TurnSummaryOut
from kdcube_ai_app.apps.chat.sdk.util import _now_str, _today_str, _json_loads_loose
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import stream_agent_to_json
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase
import kdcube_ai_app.apps.chat.sdk.viz.logging_helpers as logging_helpers


def _turn_summary_system_prompt(timezone: str) -> str:
    now = _now_str()
    today = _today_str()

    time_evidence = (
        "AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)\n"
        f"Current UTC date: {today}\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    time_evidence_reminder = (
        f"Very important: The user's timezone is {timezone}. "
        f"Current UTC timestamp: {now}. Current UTC date: {today}. "
        "Any dates before this are in the past, and any dates after this are in the future.\n"
    )

    guidance = (
        "You are a turn summarizer. Produce a compact JSON summary of THIS turn only.\n"
        "Inputs include the user message, provided context blocks, solver artifacts, and the final assistant answer.\n"
        "Summarize WHAT was requested and WHAT was delivered, not the process.\n\n"
        "If the assistant answer is provided as the literal token <NO ANSWER>, treat it as no assistant answer was produced.\n\n"
        "TURN LOG FIELD GUIDANCE:\n\n"
        "COMPLEXITY LEVEL (from execution trace signals):\n"
        "simple: Gate→Answer only | moderate: +Context retrieval | "
        "complex: +Solver (1-3 tools/codegen) | very_complex: +Coordinator (4+ tools/multi-deliverable)\n\n"
        "COMPLEXITY FACTORS (list 2-4 observed): multi_agent, context_retrieval, solver_invoked, "
        "tool_usage_N, solver codegen/react, multiple_deliverables, context_artifacts_used, clarification_rounds, iterative_refinement\n\n"
        "DOMAIN: if blended, keep multiple, separate with ;\n"
        "security, compliance, data_analysis, infrastructure, general, medical, science, research, etc.\n\n"
        "INQUIRY_TYPE (infer from user intent): if blended, keep multiple, separate with ;\n"
        "factual (who/what/when questions, lookups), analytical (why/how/compare/deeper reasoning), "
        "creative (generate/design/draft), procedural (steps/process/how-to guides), conversational (chat)\n\n"
        "USER_INQUIRY: ≤200 chars summary of user's prompt\n\n"
        "ASSISTANT_ANSWER: Inventorization summary of the assistant answer artifact ONLY (the final answer text). "
        "Important: if the assistant answer contained any suggestions to a user which user can later mention/answer, include this in this field. "
        "Use content description format + inventorization breakdown.\n\n"
        "DELIVERED_TO_USER: ≤300 chars summary of what the chatbot delivered to the user overall "
        "(solver deliverables + final answer response). Single item: 1-2 sentences. Multiple: bullet list "
        "(max 5, ~8 words each). Capture WHAT (substance), not HOW (process).\n\n"
        "═══ CONTENT DESCRIPTION (applies to both user_message_description AND assistant_answer) ═══\n"
        "Format: content_type + description (≤200 chars)\n\n"
        "Content types:\n"
        "- pure_text: Questions, explanations, analysis without embedded code/data/diagrams\n"
        "- diagram_code: Raw diagram code (Mermaid, PlantUML, GraphViz) as standalone block\n"
        "- code_block: Code snippet (Python, JS, SQL, etc.) as standalone block\n"
        "- structured_data: Tables, CSV, JSON, YAML, XML as text\n"
        "- mixed_content: Text containing embedded code/diagrams/data/snippets/tables (NEEDS EXTRACTION)\n"
        "- code_with_explanation: Code blocks with surrounding explanatory text (NEEDS EXTRACTION)\n"
        "- attachment_reference: Content mentions or references attachments\n"
        "- text_with_urls: Text containing URLs to external resources\n"
        "- reference_only: Brief pointer to other artifacts/deliverables\n\n"
        "Examples:\n"
        "✓ 'diagram_code: Raw Mermaid flowchart, 15 nodes, complex labels'\n"
        "✓ 'mixed_content: Python function embedded in usage questions'\n"
        "✓ 'code_block: SQL query for data extraction, ~20 lines'\n"
        "✓ 'structured_data: CSV table with 8 columns, 20 rows'\n"
        "✓ 'pure_text: Question about converting diagram to image'\n"
        "✓ 'code_with_explanation: Mermaid diagram wrapped in fix notes and rendering guidance'\n\n"
        "USER_MESSAGE_DESCRIPTION: Apply content description format to user's message. "
        "Critical for assessing if user content can be directly consumed by tools or needs extraction/transformation.\n\n"
        "ASSISTANT_ANSWER: Apply content description format to the final assistant answer. "
        "This MUST be an inventorization-style summary with semantic + structural signals "
        "(what it is about, what artifacts/data structures exist, and how it's formatted: tables, "
        "code blocks, lists, schemas, snippets, mixed content). "
        "Inventorization breakdown = telegraphic semantic + structural + inventory summary "
        "of the assistant answer artifact (topics/domains/intent; structure like tables/schemas/snippets/code blocks/lists/mixed content and what's it about; "
        "key items/sections/artifacts).\n"
        "Format:\n"
        "semantic:<...> | structural:<...> | inventory:<...>\n"
        "Critical for downstream agents to know if transformation/extraction needed before tool consumption.\n\n"
        "OUTPUT RULES:\n"
        "- Output MUST be valid JSON with ALL keys shown in the template (use empty strings or [] when unknown).\n"
        "- Keep arrays compact (0-6 items).\n"
    )

    json_shape_hint = (
        "{\n"
        '  "objective": "short goal",\n'
        '  "done": [],\n'
        '  "not_done": [],\n'
        '  "assumptions": [],\n'
        '  "risks": [],\n'
        '  "notes": "",\n'
        '  "user_inquiry": "Brief summary (≤200 chars) of what user prompted in this turn",\n'
        '  "user_message_description": "content_type: description ≤200 chars",\n'
        '  "assistant_answer": "content_type: description ≤200 chars + inventorization breakdown",\n'
        '  "delivered_to_user": "≤300 chars overall delivery summary",\n'
        '  "complexity": {"level": "simple|moderate|complex|very_complex", "factors": []},\n'
        '  "domain": "domain1;domain2",\n'
        '  "inquiry_type": "type1;type2",\n'
        '  "prefs": {\n'
        '    "assertions": [\n'
        '      {"key":"", "value":null, "desired":true, "scope":"conversation", "confidence":0.7, "reason":"nl-or-summary"}\n'
        '    ],\n'
        '    "exceptions": [\n'
        '      {"rule_key":"", "value":{}, "scope":"conversation", "confidence":0.7, "reason":"nl-or-summary"}\n'
        '    ]\n'
        "  }\n"
        "}"
    )

    base = "\n".join([time_evidence, time_evidence_reminder, guidance]).strip()
    return (
        base
        + "\n\nOUTPUT FORMAT:\n"
          "Return ONLY a JSON object matching this template:\n"
        + json_shape_hint
    )


def _summary_messages_with_answer(
        *,
        context_messages: List[HumanMessage | AIMessage],
        assistant_answer: str,
) -> List[HumanMessage | AIMessage]:
    messages = list(context_messages or [])
    answer_text = (assistant_answer or "").strip() or "<NO ANSWER>"
    messages.append(AIMessage(content=answer_text))
    return messages


async def stream_turn_summary(
        *,
        svc: ModelServiceBase,
        context_messages: List[HumanMessage | AIMessage],
        assistant_answer: str,
        timezone: str,
        max_tokens: int = 1500,
) -> Tuple[Dict[str, Any], str]:
    """
    Generate a structured turn summary using a single-channel JSON stream.
    Returns (summary_dict, internal_thinking).
    """
    sys_prompt = _turn_summary_system_prompt(timezone)
    role = "turn.summary"

    messages = _summary_messages_with_answer(
        context_messages=context_messages,
        assistant_answer=assistant_answer,
    )

    result = await stream_agent_to_json(
        svc,
        client_name=role,
        client_role=role,
        sys_prompt=sys_prompt,
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens,
    )
    logging_helpers.log_agent_packet(role, "out", result)
    summary_text = (result.get("agent_response") or "").strip()
    summary = _json_loads_loose(summary_text) or {}
    if summary:
        try:
            summary = TurnSummaryOut.model_validate(summary).model_dump()
        except Exception:
            pass
    internal = result.get("internal_thinking") or ""
    return summary, internal

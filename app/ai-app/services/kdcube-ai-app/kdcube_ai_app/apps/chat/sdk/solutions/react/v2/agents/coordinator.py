# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ver2/coordinator.py

import json
import logging
from typing import Dict, Any, List, Optional, Literal, Callable

from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.sdk.streaming.streaming import (
    _stream_agent_two_sections_to_json as _stream,
)
from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes, token_count
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_instruction_catalog_block
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.call import get_react_tools_catalog
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message, create_cached_human_message
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.agent_retry import retry_with_compaction

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import (
    URGENCY_SIGNALS,
    CLARIFICATION_QUALITY,
    TECH_EVOLUTION_CAVEAT,
    PROMPT_EXFILTRATION_GUARD,
    INTERNAL_AGENT_JOURNAL_GUARD,
    INTERNAL_NOTES_CONSUMER,
    ATTACHMENT_AWARENESS_COORDINATOR,
    ELABORATION_NO_CLARIFY,
    CITATION_TOKENS,
    PATHS_EXTENDED_GUIDE,
    USER_GENDER_ASSUMPTIONS,
)

log = logging.getLogger(__name__)

NextStepV2 = Literal["react_loop", "final_answer"]


class PlanV2(BaseModel):
    mode: Literal["active", "new", "update", "close"] = Field(
        default="new",
        description="Plan handling: active (reuse last), new (fresh), update (revise), close (discard active).",
    )
    steps: List[str] = Field(default_factory=list, description="Plan steps for this turn when mode=new|update.")
    plan_id: Optional[str] = Field(default=None, description="Optional explicit plan id. If absent, runtime will assign.")
    exploration_budget: int = Field(
        default=1,
        description="Total exploration budget for this turn (tool calls).",
    )
    exploitation_budget: int = Field(
        default=1,
        description="Total exploitation budget for this turn (tool calls).",
    )


class UnifiedCoordinatorOutV2(BaseModel):
    next_step: NextStepV2 = "react_loop"
    notes: str = ""
    plan: PlanV2 = Field(default_factory=PlanV2)
    clarification_questions: List[str] = Field(default_factory=list)


def _get_2section_protocol(json_shape_hint: str) -> str:
    return (
        "\n\n[CRITICAL OUTPUT PROTOCOL — TWO SECTIONS, IN THIS ORDER]:\n"
        "• You MUST produce EXACTLY TWO SECTIONS (two channels) in this order.\n"
        "• Use EACH START marker below EXACTLY ONCE.\n"
        "• NEVER write any END markers like <<< END ... >>>.\n"
        "• The SECOND section must be a fenced JSON block and contain ONLY JSON.\n\n"
        "CHANNEL 1 — THINKING CHANNEL (user-facing status):\n"
        "Marker:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "Immediately after this marker, write a VERY SHORT, non-technical status for the user.\n"
        "- 1–3 short sentences or up to 3 brief bullets.\n"
        "- Plain language only: no JSON, no schema talk, no field names.\n"
        "- Do NOT mention internal tooling, slots, or schemas.\n"
        "If you truly have nothing to add, output a single line with \"…\".\n\n"
        "CHANNEL 2 — STRUCTURED JSON CHANNEL (UnifiedCoordinatorOutV2):\n"
        "Marker:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "Immediately after this marker, output ONLY a ```json fenced block with a single\n"
        "UnifiedCoordinatorOutV2 object that matches the JSON shape hint below:\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"
        "[STRICT RULES FOR CHANNEL 2 (JSON)]:\n"
        "1. Channel 2 MUST contain ONLY a single JSON object.\n"
        "2. JSON MUST be inside the ```json fenced block shown above.\n"
        "3. DO NOT write any text before or after the JSON fence.\n"
        "4. The JSON must be valid and conform to the UnifiedCoordinatorOutV2 schema.\n\n"
    )

async def coordinator_planner_stream_v2(
    svc: ModelServiceBase,
    *,
    timezone: str,
    context_blocks: Optional[List[Dict[str, Any]]] = None,
    ctx_browser: Any = None,
    emit_status: Optional[Callable[[List[str]], Any]] = None,
    render_params: Optional[Dict[str, Any]] = None,
    sanitize_on_fail: bool = True,
    system_message_token_count_fn: Optional[Callable[[], int]] = None,
    tool_catalog_json: str = "[]",
    code_packages: Optional[str] = None,
    on_progress_delta=None,
    max_tokens: int | None = 1400,
) -> Dict[str, Any]:
    today = _today_str()
    now = _now_up_to_minutes()
    thinking_budget_tokens = min(220, max(80, int(0.12 * (max_tokens or 1200))))

    sys_1 = (
        "[ROLE]\n"
        "You are the Coordinator. Emit ONE JSON with:\n"
        "• plan, next_step, notes (optional), clarification_questions\n"
        "Do NOT run tools. Do NOT solve. Output must fit the schema.\n"
        "Full user input for the current turn is in [USER_MESSAGE] (primary source).\n"
        "Use [USER_MESSAGE] as authoritative user input for the current turn.\n"
        "You DO NOT solve the user request. Read your role in this instruction and follow it.\n"
        "\n"
        "[CRITICAL: READING CONVERSATION_HISTORY]\n"
        "Infer an EFFECTIVE OBJECTIVE from user input + historical context.\n"
        "Downstream react solver can access full context, artifacts, and sources_pool when needed.\n"
        "Do NOT ask for users to re-provide content just because full content is not visible.\n"
        "Ask clarifying questions ONLY if truly blocking.\n"
        "\n"
        f"{PROMPT_EXFILTRATION_GUARD}\n"
        f"{INTERNAL_AGENT_JOURNAL_GUARD}\n"
        f"{INTERNAL_NOTES_CONSUMER}\n"
        f"{ATTACHMENT_AWARENESS_COORDINATOR}\n"
        f"{CITATION_TOKENS}\n"
        f"{PATHS_EXTENDED_GUIDE}\n"
        f"{USER_GENDER_ASSUMPTIONS}\n"
        "\n"
        "[PLANS]\n"
        "Plan only the next feasible slice (this turn). Use 2–5 short plan steps when mode=new|update.\n"
        "If mode=active, keep the previous plan active and do NOT emit steps.\n"
        "If mode=close, discard the active plan and do NOT emit steps.\n"
        "If mode=update, emit updated steps and note in notes that the plan was updated.\n"
        "If next_step=final_answer, set plan.mode=close and leave steps empty.\n"
        "\n"
        "[BUDGETS]\n"
        "Provide total budgets for THIS TURN only: exploration + exploitation.\n"
        "Keep numbers small and realistic (0–3 typical), but include a small risk buffer\n"
        "if downstream may need an extra tool call due to mistakes or missing info.\n"
        "\n"
        "[OUTPUT ARTIFACTS]\n"
        "If final artifacts are required, mention them explicitly in plan steps and/or notes.\n"
        "\n"
        "[SKILL GUIDANCE (USE SKILL GALLERY)]\n"
        "• The tools/skills catalogs are in the system instruction under [AVAILABLE COMMON TOOLS] and [SKILL CATALOG].\n"
        "• If relevant for the task skills exist and next_step='react_loop', you MUST mention 1–3 skills in notes using SKx ids only.\n"
        "• Use compact purpose tags: \"SK1: URL sourcing, SK3: PDF layout\".\n"
        "• If next_step='final_answer', do NOT suggest skills that require tools. That agent is not able to use tools.\n"
        "\n"
        "[NEXT_STEP DECISION RULE]\n"
        "Choose react_loop when the task requires tools, browsing, file I/O, or citations beyond visible context.\n"
        "Choose final_answer ONLY when the answer can be written directly from visible context without tool use.\n"
        "The final_answer agent has NO tool access and does NOT explore; it produces a single final reply.\n"
        "Exploration/exploitation are React strategies only.\n"
        "• Do NOT include irrelevant skills; only list skills that materially improve accuracy or format quality. Note that some of the skills must apply early to build the solution properly from very beginning\n"
        "\n"
        f"{URGENCY_SIGNALS}\n"
        f"{CLARIFICATION_QUALITY}\n"
        f"{ELABORATION_NO_CLARIFY}\n"
        "• If essential details are missing and asking helps, provide ≤2 unblockers in clarification_questions.\n"
        "• Ask when:\n"
        "  - Missing critical parameters (format, constraints, audience, integration)\n"
        "  - Contradictory requirements that can't be resolved from context\n"
        "  - Ambiguous scope where multiple interpretations are equally valid\n"
        "  - User's requested format semantically doesn't match the task and context doesn't clarify intent\n"
        "• DO NOT ask when:\n"
        "  - Objective is already clear from CONVERSATION_HISTORY and actionable.\n"
        "  - User just answered clarification questions (check for '[Clarification loop]' note)\n"
        "  - Standard defaults exist or tools can discover the info\n"
        "  - Minor preferences or style choices\n"
        "  - Format mismatch is obvious from context\n"
        f"{TECH_EVOLUTION_CAVEAT}\n"
        "• You cannot ask paths/locations of the files/artifacts from user. We cannot reach them. We only can receive inline / uploaded artifacts."
        "• Prioritize: [BLOCKING] → [CRITICAL] → [IMPORTANT]; be specific; ≤25 words each; bundle related.\n"
        "\n"
    )

    json_hint = (
        "{\n"
        "  \"plan\": {\n"
        "    \"mode\": \"active|new|update|close\",\n"
        "    \"steps\": [\"bullet 1\", \"bullet 2\"],\n"
        "    \"plan_id\": \"optional-id\",\n"
        "    \"exploration_budget\": <num of steps to explore>,\n"
        "    \"exploitation_budget\": <num of steps to exploit>\n"
        "  },\n"
        "  \"notes\": \"<=120 words, concise, tool-agnostic\",\n"
        "  \"next_step\": \"react_loop | final_answer\",\n"
        "  \"clarification_questions\": []\n"
        "}\n"
    )

    two_section_proto = _get_2section_protocol(json_hint)
    sys_2 = (
        "[OUTPUT FORMAT]\n"
        f"Return exactly two sections: THINKING (≤{thinking_budget_tokens} tokens or '…') and JSON that conforms to UnifiedCoordinatorOutV2.\n"
        "No text after JSON.\n"
    )

    try:
        tool_catalog_list = json.loads(tool_catalog_json or "[]")
        if not isinstance(tool_catalog_list, list):
            tool_catalog_list = []
    except Exception:
        tool_catalog_list = []

    instruction_catalog_block = build_instruction_catalog_block(
        consumer="solver.coordinator.v2",
        tool_catalog=tool_catalog_list,
        react_tools=get_react_tools_catalog(),
    ) + "\n\n"

    TIMEZONE = timezone
    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        f"Current UTC date: {today}\n"
        "All relative dates  (today/yesterday/last year/next month and so on) MUST be interpreted against this context.\n"
    )
    time_evidence_reminder = (
        f"Very important: The user's timezone is {TIMEZONE}. Current UTC timestamp: {now}. "
        f"Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future.\n"
    )

    sys_3 = two_section_proto + "\n" + instruction_catalog_block + "\n" + (code_packages or "")
    system_text = time_evidence + "\n" + sys_1 + "\n" + sys_2 + "\n" + sys_3 + "\n" + time_evidence_reminder
    system_msg = create_cached_system_message(
        [
            {
                "text": system_text,
                "cache": True,
            },
        ]
    )

    if system_message_token_count_fn is None:
        system_message_token_count_fn = lambda: token_count(system_text)

    def _system_text_fn() -> str:
        return system_text

    prompt_tail = {
        "type": "text",
        "text": f"Produce two sections. THINKING part ≤{thinking_budget_tokens} tokens or '…'. Then JSON exactly as per shape.\n"
    }

    async def _call_coordinator(*, blocks):
        msg_blocks: List[Dict[str, Any]] = list(blocks or [])
        msg_blocks.append(prompt_tail)
        return await _stream(
            svc,
            client_name="solver.coordinator.v2",
            client_role="solver.coordinator.v2",
            sys_prompt=system_msg,
            user_msg=create_cached_human_message(msg_blocks),
            schema_model=UnifiedCoordinatorOutV2,
            on_progress_delta=on_progress_delta,
            max_tokens=max_tokens,
        )

    if ctx_browser:
        return await retry_with_compaction(
            ctx_browser=ctx_browser,
            system_text_fn=_system_text_fn,
            system_message_token_count_fn=system_message_token_count_fn,
            render_params=render_params or {"include_sources": True, "include_announce": True},
            agent_fn=_call_coordinator,
            emit_status=emit_status,
            sanitize_on_fail=sanitize_on_fail,
        )

    return await _call_coordinator(blocks=list(context_blocks or []))

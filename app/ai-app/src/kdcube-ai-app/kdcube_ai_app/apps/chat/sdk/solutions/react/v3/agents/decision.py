# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ver2/decision.py

import json
import logging
import re
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError, ServiceKind
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer_v3 import (
    ChannelSpec,
    stream_with_channels,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
    compose_decision_system_text,
    head_tail_preview,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import (
    ACTION_CAUSALITY_AND_STRATEGY,
    MULTI_ACTION_INDEPENDENCE_AND_GOOD_SHAPES,
)

_LOG = logging.getLogger("agent.react.v3.decision")

def _multi_action_enabled(mode: str) -> bool:
    return (mode or "").strip().lower() in {"on", "true", "1", "yes", "safe_fanout", "fanout"}


class ToolCallDecisionV2(BaseModel):
    tool_id: str = Field(..., description="Qualified tool ID")
    params: Dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    action: Literal["call_tool", "complete", "exit"]

    notes: str = ""

    # One action JSON object supports exactly one tool call object.
    # Multi-action output is represented by multiple <channel:action>
    # instances, not by arrays in this field.
    tool_call: Optional[ToolCallDecisionV2] = None

    final_answer: Optional[str] = None
    suggested_followups: Optional[List[str]] = None


_CHANNEL_BLOCK_RE = re.compile(
    r"<channel:action>(.*?)</channel:action>",
    re.I | re.S,
)
_FENCE_LINE_RE = re.compile(r"^\s*```(?:json)?\s*$", re.I)


def _is_valid_channel_block_start(text: str, idx: int, *, start: int = 0) -> bool:
    try:
        return (text[max(0, int(start or 0)):idx].count("`") % 2) == 0
    except Exception:
        return True


def _iter_json_texts(text: str) -> List[str]:
    src = (text or "").strip()
    if not src:
        return []
    decoder = json.JSONDecoder()
    out: List[str] = []
    idx = 0
    while idx < len(src):
        while idx < len(src) and src[idx].isspace():
            idx += 1
        if idx >= len(src):
            break
        try:
            _, end = decoder.raw_decode(src, idx)
        except Exception:
            break
        out.append(src[idx:end].strip())
        idx = end
    return out


def _iter_fenced_json_blocks(text: str) -> List[str]:
    lines = (text or "").strip().splitlines()
    out: List[str] = []
    idx = 0
    while idx < len(lines):
        if not _FENCE_LINE_RE.match(lines[idx] or ""):
            idx += 1
            continue
        idx += 1
        block: List[str] = []
        while idx < len(lines):
            if (lines[idx] or "").strip() == "```":
                out.append("\n".join(block).strip())
                idx += 1
                break
            block.append(lines[idx])
            idx += 1
    return [chunk for chunk in out if chunk]


def _extract_json_candidates(text: str) -> List[str]:
    body = (text or "").strip()
    if not body:
        return []
    direct = _iter_json_texts(body)
    if direct:
        return direct
    fenced = _iter_fenced_json_blocks(body)
    if fenced:
        out: List[str] = []
        for chunk in fenced:
            out.extend(_iter_json_texts(chunk))
        return out
    return _iter_json_texts(body)


def parse_react_decision_bundle_from_raw(
    *,
    full_raw: Optional[str],
    json_raw: Optional[str],
) -> Dict[str, Any]:
    candidates: List[str] = []
    seen: set[str] = set()
    if isinstance(full_raw, str) and full_raw.strip():
        validation_start = 0
        for match in _CHANNEL_BLOCK_RE.finditer(full_raw):
            if not _is_valid_channel_block_start(full_raw, match.start(), start=validation_start):
                continue
            body = match.group(1)
            for candidate in _extract_json_candidates(body):
                norm = candidate.strip()
                if norm and norm not in seen:
                    candidates.append(norm)
                    seen.add(norm)
            validation_start = match.end()
    if not candidates and isinstance(json_raw, str) and json_raw.strip():
        for candidate in _extract_json_candidates(json_raw):
            norm = candidate.strip()
            if norm and norm not in seen:
                candidates.append(norm)
                seen.add(norm)

    decisions: List[Dict[str, Any]] = []
    errors: List[str] = []
    error_items: List[Dict[str, Any]] = []
    for idx, raw_item in enumerate(candidates):
        try:
            parsed = json.loads(raw_item)
        except Exception as exc:
            error_text = f"json_decode_error:{type(exc).__name__}:{exc}"
            errors.append(error_text)
            error_items.append({
                "index": idx,
                "error": error_text,
                "raw_preview": _preview_channel_text(raw_item),
            })
            continue
        try:
            decisions.append(Action.model_validate(parsed).model_dump())
        except Exception as exc:
            error_text = f"decision_validate_error:{type(exc).__name__}:{exc}"
            errors.append(error_text)
            error_items.append({
                "index": idx,
                "error": error_text,
                "raw_preview": _preview_channel_text(raw_item),
            })
    return {
        "decisions": decisions,
        "errors": errors,
        "error_items": error_items,
        "candidate_count": len(candidates),
    }


def parse_single_react_decision_from_channel_text(
    channel_text: Optional[str],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    candidates = _extract_json_candidates(channel_text or "")
    if not candidates:
        return None, "no_json_candidate"
    if len(candidates) != 1:
        return None, f"expected_single_json_candidate:{len(candidates)}"
    raw_item = candidates[0]
    try:
        parsed = json.loads(raw_item)
    except Exception as exc:
        return None, f"json_decode_error:{type(exc).__name__}:{exc}"
    try:
        return Action.model_validate(parsed).model_dump(), None
    except Exception as exc:
        return None, f"decision_validate_error:{type(exc).__name__}:{exc}"


def _preview_channel_text(text: Optional[str], *, limit: int = 600) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "...(truncated)"


def build_decision_system_text(
    *,
    adapters: List[Dict[str, Any]],
    infra_adapters: Optional[List[Dict[str, Any]]] = None,
    workspace_implementation: str = "custom",
    additional_instructions: Optional[str] = None,
    instruction_body: Optional[str] = None,
    instruction_blocks: Optional[List[str]] = None,
    include_tool_catalog: bool = True,
    include_skill_gallery: bool = True,
    multi_action_mode: str = "off",
    skill_consumer: str = "solver.react.v2.decision.v2.strong",
) -> str:
    json_hint = (
        "{\n"
        "  \"action\": \"call_tool | complete | exit\",\n"
        "  \"notes\": \"Short user-visible progress note for tool rounds; empty for complete/exit\",\n"
        "  \"tool_call\": {\n"
        "    \"tool_id\": \"web_tools.web_search\",\n"
        "    \"params\": {<tool params according to tool documentation. to bind artifact content, set the param value to 'ref:<artifact_path_or_visible_file_path>'>},\n"
        "  },\n"
        "  \"final_answer\": \"(required for complete/exit)\",\n"
        "  \"suggested_followups\": [\"optional suggested follow-ups\"]\n"
        "}\n"
        "\n"
        "Each JSON object may contain at most ONE tool_call object.\n"
        "Do NOT emit a sequence/array/list of tool calls inside one action JSON object.\n"
        "When multi-action is enabled, emit each action in its own separate <channel:action> instance.\n"
    )

    if _multi_action_enabled(multi_action_mode):
        protocol = (
            "CRITICAL: you are the agent which must form output in a custom protocol you must obey. This is not similar to tool calling protocol.\n"
            "\n"
            f"{ACTION_CAUSALITY_AND_STRATEGY.strip()}\n"
            "\n"
            f"{MULTI_ACTION_INDEPENDENCE_AND_GOOD_SHAPES.strip()}\n"
            "\n"
            "[HOW THE STRATEGY ABOVE LOOKS IN THIS PROTOCOL'S CHANNEL FORMAT]\n"
            "The block above is the strategy. This protocol's technique is channels: <channel:thinking>, <channel:action>, <channel:code>, optional <channel:summary>. Each action you emit lives in its own <channel:action> instance. When the strategy permits multi-action, repeat <channel:action>; when not, emit one <channel:action> and stop.\n"
            "Allowed (multi-action, both independent, both consume a source visible BEFORE this round):\n"
            "<channel:thinking>...short status for the round...</channel:thinking>\n"
            "<channel:action>```json {{ \"action\":\"call_tool\", \"tool_call\":{{\"tool_id\":\"rendering_tools.write_pdf\", \"params\":{{\"path\":\"turn_<current>/outputs/report/report.pdf\", \"content\":\"ref:fi:turn_<earlier>.outputs/report/report.md\"}}}} }} ```</channel:action>\n"
            "<channel:action>```json {{ \"action\":\"call_tool\", \"tool_call\":{{\"tool_id\":\"rendering_tools.write_pptx\", \"params\":{{\"path\":\"turn_<current>/outputs/report/report.pptx\", \"content\":\"ref:fi:turn_<earlier>.outputs/report/report.md\"}}}} }} ```</channel:action>\n"
            "<channel:code></channel:code>\n"
            "Forbidden (write + render in the same round — the render consumes the just-written source which is NOT yet visible):\n"
            "<channel:action>```json {{ ...react.write canvas report.md... }} ```</channel:action>\n"
            "<channel:action>```json {{ ...rendering_tools.write_pdf content=ref:turn_<current>/outputs/report.md (BAD) ... }} ```</channel:action>\n"
            "Fix: write this round, render next round after the `fi:` ref is visible.\n"
            "\n"
            "[VISIBILITY & RENDER]\n"
            "Visibility rule: content meant for the user to see, download, approve, or use as a renderer source must be EXTERNAL — react.write channel=canvas or exec visibility=external. channel=internal is only for private scratch that will not be presented or rendered.\n"
            "Default write rule: reports, briefs, HTML, Markdown, slide source, DOCX/PDF/PPTX source, and anything under outputs/ that may become a deliverable must be written with react.write channel=canvas.\n"
            "Renderer source rule: rendering_tools.write_* produces user-visible artifacts; `content='ref:...'` MUST point to an external artifact whose `fi:` path was visible at the START of this response. If you just wrote the source earlier in this same response, it is NOT visible yet — write now, render in a later round.\n"
            "After react.write, stop. Review the visible write result next round, then render or patch if needed. Do not write a placeholder now to patch later — write the final content once.\n"
            "\n"
            "[CHANNELS — FORMAT MECHANICS]\n"
            "CRITICAL: the first literal channel in your response must be <channel:thinking>. Never emit legacy <thinking>...</thinking> tags.\n"
            "CRITICAL: you have 4 channel types. Three are required every round; summary is allowed ONLY on complete/exit final-answer rounds.\n"
            "Output protocol (strict): one round = at least one <channel:thinking>, one or more <channel:action> (multiple only when the independence gate above passes), and <channel:code> only when an exec action is in this round.\n"
            "<channel:thinking> ... </channel:thinking>\n"
            "<channel:action> ... </channel:action>\n"
            "<channel:code> code generated </channel:code>\n"
            "Do not include summary unless action is complete or exit. The optional <channel:summary> may appear exactly once, and only when the response contains a single complete/exit action and no tool-call actions.\n"
            "<channel:thinking>: short user-facing markdown status (1–2 sentences, no lists). It is shown to the user; do NOT use it to claim a pending action's result is in.\n"
            "Multiple <channel:thinking> blocks per response are allowed; emit additional ones only when each adds something worth saying.\n"
            "\n"
            "<channel:action> carries one action. One <channel:action>...</channel:action> instance means exactly one action.\n"
            "Inside each <channel:action>, output exactly one ```json fenced block with one action JSON object matching the shape hint below (no extra text):\n"
            "```json\n"
            f"{json_hint}\n"
            "```\n\n"
            "Never put > 1 actions into one <channel:action> instance.\n"
            "Never put > 1 JSON objects, > 1 fenced JSON blocks, or prose after the JSON inside one <channel:action> instance.\n"
            "DO NOT DO THIS: sequencing multiple ```json blocks inside one <channel:action>, like <channel:action>```json...```\\n```json...```</channel:action>. For each action, emit its own separate <channel:action>...</channel:action> instance.\n"
            "\n"
            "If you emit multiple actions in one round (after passing the independence gate above), use this shape — one <channel:action> per action:\n"
            "<channel:thinking>...short status for the round...</channel:thinking>\n"
            "<channel:action>```json {{ ...action A — fully determined by context visible before this response... }} ```</channel:action>\n"
            "<channel:action>```json {{ ...action B — independent of A, also fully determined by previously-visible context... }} ```</channel:action>\n"
            "<channel:code></channel:code>\n"
            "If one of the actions is exec_tools.execute_code_python, put its <channel:code> immediately after that exec action; the code binds only to the immediately preceding exec action. Otherwise leave <channel:code> empty.\n"
            "Exec default: emit exec ALONE in its round. Bundling exec with other actions in the same round is rarely correct; the other actions almost always end up depending on, or interfering with, exec's output. If you do bundle exec, the exec action must have params.contract AND raw Python in <channel:code> immediately after it; otherwise the exec is incomplete and will not run.\n"
            "Exec tool DOES NOT have a `code` parameter. Putting code in the tool_call params is WRONG. Code goes only in <channel:code>.\n"
            "\n"
            "[FINAL ANSWER — complete / exit]\n"
            "complete/exit closes the turn and streams a final user-facing answer. You may emit complete/exit only when every tool result the answer depends on is ALREADY VISIBLE in your timeline. If any required result is missing, complete is premature — emit only the tools now and complete in a later round.\n"
            "complete/exit must be the ONLY action in its round. Pairing it with any tool call would claim the work is done before that tool's result exists.\n"
            "For complete/exit JSON, set notes=\"\" and tool_call=null. Put the user response only in final_answer; the only extra final-only channel is summary.\n"
            "Incremental final-answer rule: prior same-turn completions and streamed timeline text are already visible to the user. final_answer closes the newest unresolved request; do not summarize the whole turn or replay earlier visible answers after a live followup. Mention earlier completed work only when the newest request depends on it, and then keep it to one short pointer.\n"
            "\n"
            "Final answer shape (only when action is complete or exit):\n"
            "<channel:thinking>...short final status...</channel:thinking>\n"
            "<channel:action>```json {{ ...one complete/exit action JSON object... }} ```</channel:action>\n"
            "<channel:code></channel:code>\n"
            "<channel:summary>Goal: ...\nOutcome: ...\nKey facts: ...\nRefs: ...</channel:summary>\n"
            "\n"
            "[CODE & SUMMARY CHANNEL DETAILS]\n"
            "In <channel:code>, output ONLY the raw Python code snippet (no fencing, no auxiliary text).\n"
            "Use non-empty <channel:code> only immediately after an exec_tools.execute_code_python action. If there is no exec action, omit <channel:code> or emit an empty <channel:code></channel:code> block.\n"
            "For call_tool-only rounds, omit <channel:summary> entirely. For complete/exit rounds, include exactly one <channel:summary> with: Goal, Outcome, Key facts, Refs. Scale the summary to the turn: for trivial exchanges (greeting, acknowledgment, tiny answer), one line or a few words per field. Refs should be logical paths for the user prompt, decisive tool calls/results, produced artifacts, and the assistant completion when known. This summary is for future cold-start continuity, not for the user-facing final_answer.\n"
            "\n"
            "[CHANNEL CITATION] (CRITICAL — streaming infra sensitive)\n"
            "Whenever you REFER to a channel BY NAME inside prose — in `notes`, in `thinking`, in `final_answer`, in a tool param, in code comments, or anywhere that is NOT the actual channel boundary — you MUST write it in BACKTICKS, e.g. `channel:thinking`, `channel:action`, `channel:code`, `channel:summary`.\n"
            "Do NOT write the angle-bracket form of a channel name anywhere except where you are actually opening or closing that channel. The streaming layer treats any literal channel-opening token as a channel boundary; writing one inside `notes`, `final_answer`, or a tool param will break the parse and corrupt the response.\n"
        )
    else:
        protocol = (
            "CRITICAL: you are the agent which must form output in a custom protocol you must obey. This is not similar to tool calling protocol.\n"
            "CRITICAL: This protocol is SINGLE-ACTION. Emit EXACTLY ONE <channel:action> per response. Emitting more than one action in the same response is a gross protocol violation.\n"
            "\n"
            f"{ACTION_CAUSALITY_AND_STRATEGY.strip()}\n"
            "\n"
            "[HOW THE STRATEGY ABOVE LOOKS IN THIS PROTOCOL'S CHANNEL FORMAT]\n"
            "The block above is the strategy. This protocol's technique is channels: <channel:thinking>, <channel:action>, <channel:code>, optional <channel:summary>. Single-action mode enforces the strategy structurally — exactly one <channel:action>. You only need to make sure you do not REFERENCE this round's action result anywhere in this same response (not in `thinking`, not in `notes`, not in `final_answer`, not in code).\n"
            "Allowed (one action, no result claim in same response):\n"
            "<channel:thinking>Creating the Excel file...</channel:thinking>\n"
            "<channel:action>```json {{ \"action\":\"call_tool\", \"tool_call\":{{\"tool_id\":\"exec_tools.execute_code_python\", \"params\":{{...}}}} }} ```</channel:action>\n"
            "<channel:code>...Python that produces report.xlsx...</channel:code>\n"
            "Forbidden (action emitted + result asserted in the same response):\n"
            "<channel:thinking>report.xlsx is ready — here is the summary...</channel:thinking>  (BAD: result not seen yet)\n"
            "<channel:action>```json {{ ...exec that produces report.xlsx... }} ```</channel:action>\n"
            "Fix: emit the action this round, stop; next round will see the `fi:...xlsx` ref and you can then say it is ready.\n"
            "\n"
            "[VISIBILITY & RENDER]\n"
            "Visibility rule: content meant for the user to see, download, approve, or use as a renderer source must be EXTERNAL — react.write channel=canvas or exec visibility=external. channel=internal is only for private scratch that will not be presented or rendered.\n"
            "Default write rule: reports, briefs, HTML, Markdown, slide source, DOCX/PDF/PPTX source, and anything under outputs/ that may become a deliverable must be written with react.write channel=canvas.\n"
            "Renderer source rule: rendering_tools.write_* `content='ref:...'` MUST point to an external artifact whose `fi:` path was visible at the START of this response. A source written or modified earlier in this same response is NOT visible yet — write now, render next round.\n"
            "After react.write, stop. Review the visible write result next round, then render or patch if needed. Do not write a placeholder now to patch later — write the final content once.\n"
            "\n"
            "[CHANNELS — FORMAT MECHANICS]\n"
            "The first literal channel in your response must be <channel:thinking>. Never emit legacy <thinking>...</thinking> tags.\n"
            "You have 4 channel types. Three are required every round; summary is allowed ONLY on complete/exit final-answer rounds.\n"
            "Output protocol (strict): one round = exactly one <channel:thinking>, exactly one <channel:action>, and <channel:code> (empty unless an exec action is in this round).\n"
            "<channel:thinking> ... </channel:thinking>\n"
            "<channel:action> ... </channel:action>\n"
            "<channel:code> code generated </channel:code>\n"
            "Do not include summary unless action is complete or exit. The optional <channel:summary> may appear exactly once, and only when the action is complete or exit.\n"
            "<channel:thinking>: short user-facing markdown status (1–2 sentences, no lists). It is shown to the user; do NOT use it to claim a pending action's result is in.\n"
            "\n"
            "<channel:action> carries one action. Inside the single <channel:action> instance, output exactly one ```json fenced block with an action JSON object matching the shape hint below (no extra text):\n"
            "```json\n"
            f"{json_hint}\n"
            "```\n\n"
            "CRITICAL: The runtime which reads your response will attempt to convert it to one round: one <channel:thinking>, one <channel:action>, one <channel:code>, and optional final-only <channel:summary>.\n"
            "DO NOT DO THIS: emit a sequence of channel groups (<channel:thinking></channel:thinking><channel:action></channel:action><channel:code></channel:code>) twice in the same response — this protocol allows exactly one of each per response.\n"
            "DO NOT DO THIS: include multiple JSON objects or fenced JSON blocks inside the single <channel:action> instance — e.g., <channel:action>```json...```\\n```json...```</channel:action>. This does not work in single-action mode. Emit exactly one tool call now and continue in a later round if more tools are needed.\n"
            "If you need a plan, use the plan tool (single action) or include a short note in `notes` — but you may not call more than one tool. Generating a second instance of any channel in the same response is a contract violation.\n"
            "\n"
            "Minimal valid shape:\n"
            "<channel:thinking>...short status...</channel:thinking>\n"
            "<channel:action>```json { ...one action JSON object... } ```</channel:action>\n"
            "<channel:code></channel:code>\n"
            "\n"
            "[FINAL ANSWER — complete / exit]\n"
            "complete/exit closes the turn and streams a final user-facing answer. You may emit complete/exit only when every tool result the answer depends on is ALREADY VISIBLE in your timeline. If any required result is missing, complete is premature — emit only the tool now and complete in a later round.\n"
            "complete/exit must be the ONLY action in its round. Pairing it with a tool call (a final_answer field alongside action=call_tool, or tool_call alongside action=complete) claims the work is done before that tool's result exists.\n"
            "For complete/exit JSON, set notes=\"\" and tool_call=null. Put the user response only in final_answer; the only extra final-only channel is summary.\n"
            "Incremental final-answer rule: prior same-turn completions and streamed timeline text are already visible to the user. final_answer closes the newest unresolved request; do not summarize the whole turn or replay earlier visible answers after a live followup. Mention earlier completed work only when the newest request depends on it, and then keep it to one short pointer.\n"
            "\n"
            "Final answer shape (only when action is complete or exit):\n"
            "<channel:thinking>...short final status...</channel:thinking>\n"
            "<channel:action>```json { ...one complete/exit action JSON object... } ```</channel:action>\n"
            "<channel:code></channel:code>\n"
            "<channel:summary>Goal: ...\nOutcome: ...\nKey facts: ...\nRefs: ...</channel:summary>\n"
            "\n"
            "[CODE & SUMMARY CHANNEL DETAILS]\n"
            "In <channel:code>, output ONLY the raw Python code snippet (no fencing, no auxiliary text).\n"
            "Use non-empty <channel:code> only immediately after an exec_tools.execute_code_python action; otherwise emit an empty <channel:code></channel:code> block.\n"
            "Exec tool DOES NOT have a `code` parameter. Putting code in the tool_call params is WRONG. Code goes only in <channel:code>.\n"
            "For call_tool rounds, omit <channel:summary> entirely. For complete/exit rounds, include exactly one <channel:summary> with: Goal, Outcome, Key facts, Refs. Scale the summary to the turn: for trivial exchanges (greeting, acknowledgment, tiny answer), one line or a few words per field. Refs should be logical paths for the user prompt, decisive tool calls/results, produced artifacts, and the assistant completion when known. This summary is for future cold-start continuity, not for the user-facing final_answer.\n"
            "\n"
            "[CHANNEL CITATION] (CRITICAL — streaming infra sensitive)\n"
            "Whenever you REFER to a channel BY NAME inside prose — in `notes`, in `thinking`, in `final_answer`, in a tool param, in code comments, or anywhere that is NOT the actual channel boundary — you MUST write it in BACKTICKS, e.g. `channel:thinking`, `channel:action`, `channel:code`, `channel:summary`.\n"
            "Do NOT write the angle-bracket form of a channel name anywhere except where you are actually opening or closing that channel. The streaming layer treats any literal channel-opening token as a channel boundary; writing one inside `notes`, `final_answer`, or a tool param will break the parse and corrupt the response.\n"
        )

    sys_msg = compose_decision_system_text(
        protocol=protocol,
        module_label="ReAct Action Module v3",
        adapters=adapters or [],
        infra_adapters=infra_adapters or [],
        workspace_implementation=workspace_implementation,
        additional_instructions=additional_instructions,
        skill_consumer=skill_consumer,
        instruction_body=instruction_body,
        instruction_blocks=instruction_blocks,
        include_tool_catalog=include_tool_catalog,
        include_skill_gallery=include_skill_gallery,
    )
    extra_instructions = str(additional_instructions or "").strip()
    if extra_instructions:
        head, tail = head_tail_preview(extra_instructions)
        _LOG.info(
            "[react.v3.decision] agent admin customization applied len=%s head=%r tail=%r",
            len(extra_instructions),
            head,
            tail,
        )
    else:
        _LOG.info("[react.v3.decision] agent admin customization not provided")
    return sys_msg


async def react_decision_stream_v2(
    svc: ModelServiceBase,
    *,
    agent_name: str,
    adapters: List[Dict[str, Any]],
    infra_adapters: Optional[List[Dict[str, Any]]] = None,
    workspace_implementation: str = "custom",
    additional_instructions: Optional[str] = None,
    instruction_body: Optional[str] = None,
    instruction_blocks: Optional[List[str]] = None,
    include_tool_catalog: bool = True,
    include_skill_gallery: bool = True,
    multi_action_mode: str = "off",
    on_progress_delta=None,
    on_raw_delta=None,
    subscribers: Optional[Dict[str, List[Any]]] = None,
    max_tokens: int = 6000,
    user_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    system_text = build_decision_system_text(
        adapters=adapters,
        infra_adapters=infra_adapters,
        workspace_implementation=workspace_implementation,
        additional_instructions=additional_instructions,
        instruction_body=instruction_body,
        instruction_blocks=instruction_blocks,
        include_tool_catalog=include_tool_catalog,
        include_skill_gallery=include_skill_gallery,
        multi_action_mode=multi_action_mode,
        skill_consumer=agent_name,
    )
    system_msg = create_cached_system_message([
        {"text": system_text, "cache": True},
    ])
    user_msg = create_cached_human_message(user_blocks)
    channels = [
        ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="action", format="json", model=Action, replace_citations=False, emit_marker="answer"),
        ChannelSpec(name="code", format="text", replace_citations=False, emit_marker="subsystem"),
        ChannelSpec(name="summary", format="markdown", replace_citations=False, emit_marker="subsystem"),
    ]

    async def _emit_delta(**kwargs):
        # Never stream structured JSON channel to the main stream; it is handled via subscribers only.
        if (kwargs.get("channel") or "") in {"action", "code", "summary"}:
            if kwargs.get("channel") == "code":
                pass
            return
        text = kwargs.get("text") or ""
        completed = bool(kwargs.get("completed"))
        if on_progress_delta is not None:
            try:
                await on_progress_delta(**kwargs)
            except TypeError:
                await on_progress_delta(text or "", completed=completed)

    async def _emit_raw_delta(piece: str):
        if not piece or on_raw_delta is None:
            return
        try:
            await on_raw_delta(piece)
        except TypeError:
            await on_raw_delta(text=piece, completed=False)

    results, meta = await stream_with_channels(
        svc,
        messages=[system_msg, user_msg],
        role=agent_name,
        channels=channels,
        emit=_emit_delta,
        agent=agent_name,
        artifact_name="react.decision",
        sources_list=None,
        subscribers=subscribers,
        raw_emit=_emit_raw_delta,
        max_tokens=max_tokens,
        temperature=0.6,
        return_full_raw=True,
    )

    service_error = (meta or {}).get("service_error") if isinstance(meta, dict) else None
    if service_error:
        # Infra constructs ServiceError; decision only propagates it.
        if isinstance(service_error, ServiceError):
            raise ServiceException(service_error)
        if isinstance(service_error, dict):
            raise ServiceException(ServiceError.model_validate(service_error))
        raise ServiceException(ServiceError(
            kind=ServiceKind.llm,
            service_name="react.decision",
            error_type=type(service_error).__name__,
            message=str(service_error),
        ))

    res_thinking = results.get("thinking")
    res_json = results.get("action")
    res_code = results.get("code")
    res_summary = results.get("summary")
    thinking_raw = res_thinking.raw if res_thinking else ""
    json_raw = res_json.raw if res_json else ""
    code_raw = res_code.raw if res_code else ""
    summary_raw = res_summary.raw if res_summary else ""
    err = res_json.error if res_json else None

    data = {}
    if res_json and res_json.obj is not None:
        try:
            data = res_json.obj.model_dump()
        except Exception:
            data = res_json.obj
    bundle_parse = {"decisions": [], "errors": [], "error_items": [], "candidate_count": 0}
    if _multi_action_enabled(multi_action_mode):
        json_instances = list(getattr(res_json, "instances", None) or []) if res_json else []
        if len(json_instances) > 1:
            decisions: List[Dict[str, Any]] = []
            errors: List[str] = []
            error_items: List[Dict[str, Any]] = []
            for idx, instance_text in enumerate(json_instances):
                parsed_decision, parse_error = parse_single_react_decision_from_channel_text(instance_text)
                if isinstance(parsed_decision, dict):
                    decisions.append(parsed_decision)
                    continue
                error_text = parse_error or "unknown_parse_error"
                errors.append(f"instance:{idx}:{error_text}")
                error_items.append({
                    "index": idx,
                    "error": error_text,
                    "raw_preview": _preview_channel_text(instance_text),
                })
            bundle_parse = {
                "decisions": decisions,
                "errors": errors,
                "error_items": error_items,
                "candidate_count": len(json_instances),
            }
        else:
            bundle_parse = parse_react_decision_bundle_from_raw(
                full_raw=(meta or {}).get("raw") if isinstance(meta, dict) else None,
                json_raw=json_raw,
            )
    normalized_bundle = list(bundle_parse.get("decisions") or [])
    if not normalized_bundle and isinstance(data, dict) and data:
        normalized_bundle = [data]
    if normalized_bundle and not data:
        data = normalized_bundle[0]
    if normalized_bundle:
        err = None
    ok_flag = (service_error is None) and (err is None)

    return {
        "agent_response": data,
        "agent_response_bundle": normalized_bundle,
        "log": {
            "error": err,
            "raw_data": json_raw,
            "service_error": service_error,
            "ok": ok_flag,
            "bundle_errors": list(bundle_parse.get("errors") or []),
            "bundle_error_items": list(bundle_parse.get("error_items") or []),
            "bundle_candidate_count": int(bundle_parse.get("candidate_count") or 0),
        },
        "raw": (meta or {}).get("raw") if isinstance(meta, dict) else None,
        "internal_thinking": thinking_raw,
        "working_summary": summary_raw,
        "channels": {
            "thinking": {
                "text": thinking_raw,
                "started_at": res_thinking.started_at if res_thinking else None,
                "finished_at": res_thinking.finished_at if res_thinking else None,
            },
            "action": {
                "text": json_raw,
                "started_at": res_json.started_at if res_json else None,
                "finished_at": res_json.finished_at if res_json else None,
            },
            "code": {
                "text": code_raw,
                "started_at": res_code.started_at if res_code else None,
                "finished_at": res_code.finished_at if res_code else None,
            },
            "summary": {
                "text": summary_raw,
                "started_at": res_summary.started_at if res_summary else None,
                "finished_at": res_summary.finished_at if res_summary else None,
            },
        },
    }

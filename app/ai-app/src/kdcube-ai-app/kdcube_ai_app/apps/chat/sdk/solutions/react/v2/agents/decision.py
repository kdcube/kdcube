# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ver2/decision.py

import logging
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from kdcube_ai_app.infra.service_hub.inventory import (
    ModelServiceBase,
    create_cached_system_message,
    create_cached_human_message,
)
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError, ServiceKind
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import (
    ChannelSpec,
    stream_with_channels,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt import (
    compose_decision_system_text,
    head_tail_preview,
)
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import (
    ACTION_CAUSALITY_AND_STRATEGY,
)

_LOG = logging.getLogger("agent.react.v2.decision")

class ToolCallDecisionV2(BaseModel):
    tool_id: str = Field(..., description="Qualified tool ID")
    params: Dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    action: Literal["call_tool", "complete", "exit"]

    notes: str = ""

    # One action JSON object supports exactly one tool call object.
    # Multi-action output, when supported by the runtime, is represented by
    # multiple <channel:action> instances, not by arrays in this field.
    tool_call: Optional[ToolCallDecisionV2] = None

    final_answer: Optional[str] = None
    suggested_followups: Optional[List[str]] = None

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
        "This protocol is SINGLE-ACTION: exactly one tool call per response. Continue with more tool calls in later rounds.\n"
    )

    # Protocol contract sketch.
    # v2 is single-action only. The contract: one <channel:thinking>, one
    # <channel:action>, one <channel:code> (empty unless exec), and an
    # optional final-only <channel:summary>. Tool results are visible only in
    # the NEXT round; the agent must never assert a result it has not seen.
    #
    # See v3/agents/decision.py (else branch) for the canonical version of
    # this prompt — they are kept in lockstep on purpose.
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
        "Visibility rule: content meant for the user to see, download, approve, or use as a renderer source must be EXTERNAL — `react.write channel=canvas` or exec `visibility=external`. `channel=internal` is only for private scratch that will not be presented or rendered.\n"
        "Default write rule: reports, briefs, HTML, Markdown, slide source, DOCX/PDF/PPTX source, and anything under `outputs/` that may become a deliverable must be written with `react.write channel=canvas`.\n"
        "Renderer source rule: `rendering_tools.write_*` `content='ref:...'` MUST point to an external artifact whose `fi:` path was visible at the START of this response. A source written or modified earlier in this same response is NOT visible yet — write now, render next round.\n"
        "After `react.write`, stop. Review the visible write result next round, then render or patch if needed. Do not write a placeholder now to patch later — write the final content once.\n"
        "\n"
        "[CHANNELS — FORMAT MECHANICS]\n"
        "The first literal channel in your response must be the opening `channel:thinking` tag. Never emit legacy <thinking>...</thinking> tags.\n"
        "You have 4 channel types. Three are required every round; summary is allowed ONLY on complete/exit final-answer rounds.\n"
        "Output protocol (strict): one round = exactly one `channel:thinking`, exactly one `channel:action`, and `channel:code` (empty unless an exec action is in this round).\n"
        "<channel:thinking> ... </channel:thinking>\n"
        "<channel:action> ... </channel:action>\n"
        "<channel:code> code generated </channel:code>\n"
        "Do not include summary unless action is complete or exit. The optional `channel:summary` may appear exactly once, and only when the action is complete or exit.\n"
        "`channel:thinking`: short user-facing markdown status (1–2 sentences, no lists). It is shown to the user; do NOT use it to claim a pending action's result is in.\n"
        "\n"
        "`channel:action` carries one action. Inside the single `channel:action` instance, output exactly one ```json fenced block with an action JSON object matching the shape hint below (no extra text):\n"
        "```json\n"
        f"{json_hint}\n"
        "```\n\n"
        "CRITICAL: The runtime which reads your response will attempt to convert it to one round: one `channel:thinking`, one `channel:action`, one `channel:code`, and optional final-only `channel:summary`.\n"
        "DO NOT DO THIS: emit a sequence of channel groups twice in the same response — this protocol allows exactly one of each per response.\n"
        "DO NOT DO THIS: include multiple JSON objects or fenced JSON blocks inside the single `channel:action` instance. This does not work in single-action mode. Emit exactly one tool call now and continue in a later round if more tools are needed.\n"
        "If you need a plan, use the plan tool (single action) or include a short note in `notes` — but you may not call more than one tool. Generating a second instance of any channel in the same response is a contract violation.\n"
        "\n"
        "Minimal valid shape:\n"
        "<channel:thinking>...short status...</channel:thinking>\n"
        "<channel:action>```json { ...one action JSON object... } ```</channel:action>\n"
        "<channel:code></channel:code>\n"
        "\n"
        "[FINAL ANSWER — complete / exit]\n"
        "complete/exit closes the turn and streams a final user-facing answer. You may emit complete/exit only when every tool result the answer depends on is ALREADY VISIBLE in your timeline. If any required result is missing, complete is premature — emit only the tool now and complete in a later round.\n"
        "complete/exit must be the ONLY action in its round. Pairing it with a tool call (a `final_answer` field alongside `action=call_tool`, or `tool_call` alongside `action=complete`) claims the work is done before that tool's result exists.\n"
        "For complete/exit JSON, set notes=\"\" and tool_call=null. Put the user response only in final_answer; the only extra final-only channel is summary.\n"
        "Incremental final-answer rule: prior same-turn completions and streamed timeline text are already visible to the user. final_answer closes the newest unresolved request; do not summarize the whole turn or replay earlier visible answers after a live followup. Mention earlier completed work only when the newest request depends on it, and then keep it to one short pointer.\n"
        "\n"
        "Final answer shape (only when action is complete or exit):\n"
        "<channel:thinking>...short final status...</channel:thinking>\n"
        "<channel:action>```json { ...one complete/exit action JSON object... } ```</channel:action>\n"
        "<channel:code></channel:code>\n"
        "<channel:summary>Goal: ...\nOutcome: ...\nKey facts: ...\nRefs: ...\nRetrieval-anchors:\n  phrases: [\"verbatim string the user might re-quote\", ...]\n  entities: [\"HighIDFProperNoun\", ...]</channel:summary>\n"
        "\n"
        "[CODE & SUMMARY CHANNEL DETAILS]\n"
        "In `channel:code`, output ONLY the raw Python code snippet (no fencing, no auxiliary text).\n"
        "Use non-empty `channel:code` only immediately after an `exec_tools.execute_code_python` action; otherwise emit an empty `channel:code` block.\n"
        "Exec tool DOES NOT have a `code` parameter. Putting code in the tool_call params is WRONG. Code goes only in `channel:code`.\n"
        "For call_tool rounds, omit `channel:summary` entirely. For complete/exit rounds, include exactly one `channel:summary` with: Goal, Outcome, Key facts, Refs, Retrieval-anchors. Scale the summary to the turn: for trivial exchanges (greeting, acknowledgment, tiny answer), one line or a few words per field. Refs should be logical paths for the user prompt, decisive tool calls/results, produced artifacts, and the assistant completion when known. Retrieval-anchors feed a lexical (BM25F-style) retrieval layer that runs ALONGSIDE semantic search: each anchor is indexed as a high-weight token, so future searches by the user's LITERAL phrasing find this turn even when the prose summary paraphrased it. Discipline: `phrases` = verbatim strings the user might re-quote (exact filenames, exact error messages, exact titles, the user's exact wording — never paraphrases); `entities` = high-IDF proper nouns (product/tool/project/person/bundle ids — would this token uniquely identify this turn among hundreds? if no, drop it; never generic nouns like \"file\"/\"data\"/\"report\"). Both keys are optional; emit empty lists or omit the block entirely for trivial turns. Concrete example for a turn that built a Q2 forecast spreadsheet and hit an openpyxl error while renaming a column: phrases: [\"Forecast-Q2-2026.xlsx\", \"openpyxl IndexError\", \"rename ARR contribution column\"]; entities: [\"Forecast-Q2-2026.xlsx\", \"openpyxl\", \"ARR contribution\"]. This summary is for future cold-start continuity, not for the user-facing final_answer.\n"
        "\n"
        "[CHANNEL CITATION] (CRITICAL — streaming infra sensitive)\n"
        "Whenever you REFER to a channel BY NAME inside prose — in `notes`, in `thinking`, in `final_answer`, in a tool param, in code comments, or anywhere that is NOT the actual channel boundary — you MUST write it in BACKTICKS, e.g. `channel:thinking`, `channel:action`, `channel:code`, `channel:summary`.\n"
        "Do NOT write the angle-bracket form of a channel name anywhere except where you are actually opening or closing that channel. The streaming layer treats any literal channel-opening token as a channel boundary; writing one inside `notes`, `final_answer`, or a tool param will break the parse and corrupt the response.\n"
    )
    sys_msg = compose_decision_system_text(
        protocol=protocol,
        module_label="ReAct Action Module v2",
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
            "[react.v2.decision] agent admin customization applied len=%s head=%r tail=%r",
            len(extra_instructions),
            head,
            tail,
        )
    else:
        _LOG.info("[react.v2.decision] agent admin customization not provided")
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
    ok_flag = (service_error is None) and (err is None)

    return {
        "agent_response": data,
        "log": {
            "error": err,
            "raw_data": json_raw,
            "service_error": service_error,
            "ok": ok_flag,
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

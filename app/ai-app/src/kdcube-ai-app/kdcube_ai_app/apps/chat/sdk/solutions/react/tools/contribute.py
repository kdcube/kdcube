# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import traceback
from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.solutions.react.subagents.events import (
    SUBAGENT_CONTRIBUTION_EVENT_KIND,
    contribution_refs_for_parent,
    publish_subagent_event,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    add_block,
    notice_block,
    tc_result_path,
    tool_call_block,
)

TOOL_SPEC = {
    "id": "react.contribute",
    "purpose": (
        "Send a report back to the agent that delegated this assignment. The report "
        "(and any refs) is authored onto the delegating conversation's event lane as a "
        "subagent.contribution event; the delegating agent folds it live or reads it "
        "when it next runs. Contribute when a deliverable or a decision-relevant "
        "partial result is ready — your final answer is delivered automatically when "
        "you finish, so contribute mid-run only what is useful before then. Refs must "
        "be logical paths from THIS conversation (e.g. files you produced); they are "
        "delivered in a form the delegating agent can pull directly."
    ),
    "args": {
        "report": (
            "str (FIRST FIELD). The report text: what was achieved, what the refs "
            "contain, what remains. Written for the delegating agent, not the user."
        ),
        "refs": (
            "[str] (SECOND FIELD, optional). Logical paths from this conversation "
            "backing the report. Use conv:fi: FILE refs (files you produced with "
            "react.write / exec / rendering) — those are directly pullable by the "
            "delegating agent. Other ref namespaces ride along as provenance only."
        ),
    },
    "returns": "delivery receipt {status, sequence, refs}",
}


async def handle_react_contribute(
    *, react: Any = None, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str
) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.contribute"
    params = tool_call.get("params") or {}
    runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "")

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": params,
        },
    )

    def _result(payload: Dict[str, Any]) -> Dict[str, Any]:
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "tool_id": tool_id,
            "mime": "application/json",
            "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
            "text": json.dumps(payload, ensure_ascii=False, indent=2),
            "meta": {"tool_call_id": tool_call_id},
        })
        state["last_tool_result"] = payload
        return state

    def _fail(code: str, message: str) -> Dict[str, Any]:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code=code,
            message=message,
            rel="result",
        )
        return _result({"status": "rejected", "code": code, "message": message})

    report = str(params.get("report") or "").strip()
    raw_refs = params.get("refs")
    if isinstance(raw_refs, str):
        raw_refs = [raw_refs]
    refs: List[str] = [str(r).strip() for r in (raw_refs or []) if str(r or "").strip()]
    if not report:
        return _fail("contribute_missing_report", "react.contribute requires a report text.")

    lane = getattr(runtime_ctx, "subagent_parent_lane", None)
    parent = getattr(runtime_ctx, "subagent_parent", None)
    if lane is None or not isinstance(parent, dict):
        return _fail(
            "contribute_unavailable",
            "This conversation has no delegating agent to contribute to.",
        )

    child_conversation_id = str(getattr(runtime_ctx, "conversation_id", "") or "")
    parent_refs = contribution_refs_for_parent(
        refs=refs, child_conversation_id=child_conversation_id
    )
    text_lines = ["[SUBAGENT CONTRIBUTION]", report]
    if parent_refs:
        text_lines.append("refs:")
        text_lines.extend(f"- {ref}" for ref in parent_refs)
    facts = {
        "child_conversation_id": child_conversation_id,
        "child_conversation_ref": f"conv_{child_conversation_id}",
        "child_turn_id": turn_id,
        "refs": parent_refs,
    }
    # The subagent envelope stamp (set by apply_child_runtime_overrides)
    # rides in the facts so clients anchor the contribution into the child's
    # thread without parsing text.
    stamp = getattr(runtime_ctx, "subagent_stamp", None)
    if isinstance(stamp, dict) and stamp:
        facts["subagent"] = dict(stamp)
    try:
        event = await publish_subagent_event(
            lane_source=lane,
            semantic_type=SUBAGENT_CONTRIBUTION_EVENT_KIND,
            text="\n".join(text_lines),
            facts=facts,
            author=f"agent:conv_{child_conversation_id}/{turn_id}",
            target_turn_id=str(parent.get("turn_id") or "") or None,
        )
        # The report is the child's message TO the delegating agent (the
        # child->parent channel). The latest one becomes the handoff the
        # converged completion carries, so the continuation-turn persona
        # speaks the helper's own words rather than a sliced deliverable.
        if runtime_ctx is not None:
            try:
                runtime_ctx.subagent_last_contribution_report = report
            except Exception:
                pass
    except Exception as exc:
        try:
            log = getattr(react, "log", None)
            if log is not None:
                log.log(f"[react.contribute] publish failed: {traceback.format_exc()}", level="ERROR")
        except Exception:
            pass
        return _fail("contribute_publish_failed", f"Contribution delivery failed: {exc}")

    return _result({
        "status": "delivered",
        "sequence": int(getattr(event, "sequence", 0) or 0),
        "refs": parent_refs,
    })

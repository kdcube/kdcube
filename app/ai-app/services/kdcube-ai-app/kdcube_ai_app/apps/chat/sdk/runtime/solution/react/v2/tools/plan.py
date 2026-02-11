# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.plan import create_plan_snapshot, build_plan_block
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
)

TOOL_SPEC = {
    "id": "react.plan",
    "purpose": (
        "Create or update the current plan for this turn. "
        "Use mode='new' to start a plan, 'update' to replace it, or 'close' to clear it. "
    "Plans are shown in ANNOUNCE and used for step acknowledgements."
    ),
    "args": {
        "mode": "str. One of: new | update | close.",
        "steps": "list[str]. Required for new/update. Each item is a plan step.",
    },
    "returns": "plan stored in timeline; state updated",
}


async def handle_react_plan(*, react: Any, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    root_notes = (last_decision.get("notes") or "").strip()
    params = tool_call.get("params") or {}
    mode = str(params.get("mode") or "new").strip().lower()
    steps = params.get("steps") or []
    turn_id = ctx_browser.runtime_ctx.turn_id or ""

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id="react.plan",
        payload={
            "tool_id": "react.plan",
            "tool_call_id": tool_call_id,
            "notes": root_notes,
            "params": params,
        },
    )

    if mode not in {"new", "update", "close"}:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_mode",
            message="react.plan mode must be one of: new | update | close",
            extra={"mode": mode},
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "bad_plan_mode", "managed": True}
        return state

    if mode in {"new", "update"}:
        if not isinstance(steps, list) or not all(isinstance(s, str) and s.strip() for s in steps):
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.plan_steps",
                message="react.plan requires non-empty steps list for mode=new/update.",
                extra={"mode": mode},
            )
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "bad_plan_steps", "managed": True}
            return state

        plan_obj = {"steps": [s.strip() for s in steps]}
        snap = create_plan_snapshot(plan=plan_obj, turn_id=turn_id, created_ts=ctx_browser.runtime_ctx.started_at or "")
        block = build_plan_block(snap=snap, turn_id=turn_id, ts=ctx_browser.runtime_ctx.started_at or "")
        add_block(ctx_browser, block)
        state["plan_steps"] = list(snap.steps or [])
        state["plan_status"] = {}
        return state

    # close
    state["plan_steps"] = []
    state["plan_status"] = {}
    notice_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        code="plan_closed",
        message="Plan closed by react.plan(mode=close).",
        extra={},
    )
    return state


__all__ = ["TOOL_SPEC", "handle_react_plan"]

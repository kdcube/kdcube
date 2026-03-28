# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations
from typing import Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.plan import (
    activate_plan_snapshot,
    apply_plan_status_updates,
    build_plan_block,
    close_plan_snapshot,
    create_plan_snapshot,
    latest_active_plan_snapshot,
    plan_snapshot_ref,
    supersede_plan_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    tc_result_path,
)

TOOL_SPEC = {
    "id": "react.plan",
    "purpose": (
        "Manage plan lineages for this turn. "
        "Use mode='new' to start a plan, 'activate' to make an older open plan current, "
        "'replace' to supersede a plan and start a new one, or 'close' to retire a plan. "
        "Plans are shown in ANNOUNCE and used for step acknowledgements."
    ),
    "args": {
        "mode": "str. One of: new | activate | replace | close.",
        "plan_id": (
            "str. Stable target plan id for activate/replace/close. "
            "Required for those modes."
        ),
        "steps": "list[str]. Required for new/replace. Each item is a plan step.",
    },
    "returns": "plan stored in timeline; state updated",
}

# Reserved for future structured acknowledgements when multi-tool emission is supported.
# Do not publish this spec in tools.__init__ or decision catalogs yet.
PLAN_ACK_TOOL_SPEC = {
    "id": "react.plan_ack",
    "purpose": (
        "Acknowledge progress on the current plan with structured step-status updates. "
        "This is an internal future tool and is not published to React yet."
    ),
    "args": {
        "updates": (
            "list[object]. Required. Each item: {step: int, status: done|failed|in_progress|pending}."
        ),
    },
    "returns": "plan acknowledgement stored in timeline; state updated",
}


def _add_plan_result_summary(
    *,
    ctx_browser: Any,
    tool_call_id: str,
    mode: str,
    plan_id: str = "",
    target_plan_id: str = "",
    latest_snapshot_ref: str = "",
    target_snapshot_ref: str = "",
) -> None:
    turn_id = ctx_browser.runtime_ctx.turn_id or ""
    lines: List[str] = []
    if mode:
        lines.append(f"mode: {mode}")
    if target_plan_id:
        lines.append(f"target_plan_id: {target_plan_id}")
    if target_snapshot_ref:
        lines.append(f"target_snapshot_ref: {target_snapshot_ref}")
    if plan_id:
        lines.append(f"plan_id: {plan_id}")
    if latest_snapshot_ref:
        lines.append(f"latest_snapshot_ref: {latest_snapshot_ref}")
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "text/markdown",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": "\n".join(lines).strip(),
        "meta": {
            "tool_call_id": tool_call_id,
            "render_role": "summary",
        },
    })


async def handle_react_plan(*, react: Any, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    params = tool_call.get("params") or {}
    mode = str(params.get("mode") or "new").strip().lower()
    steps = params.get("steps") or []
    requested_plan_id = str(params.get("plan_id") or "").strip()
    turn_id = ctx_browser.runtime_ctx.turn_id or ""
    started_at = ctx_browser.runtime_ctx.started_at or ""
    timeline_blocks = list(getattr(ctx_browser.timeline, "blocks", []) or [])

    if mode not in {"new", "activate", "replace", "close"}:
        tool_call_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            tool_id="react.plan",
            payload={
                "tool_id": "react.plan",
                "tool_call_id": tool_call_id,
                "params": params,
            },
        )
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_mode",
            message="react.plan mode must be one of: new | activate | replace | close",
            extra={"mode": mode},
            rel="call",
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "bad_plan_mode", "managed": True}
        return state

    if mode in {"activate", "replace", "close"} and not requested_plan_id:
        tool_call_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            tool_id="react.plan",
            payload={
                "tool_id": "react.plan",
                "tool_call_id": tool_call_id,
                "params": params,
            },
        )
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_target_required",
            message="react.plan requires explicit plan_id for mode=activate|replace|close.",
            extra={"mode": mode},
            rel="call",
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_plan_target", "managed": True}
        return state

    if mode in {"new", "replace"}:
        if not isinstance(steps, list) or not all(isinstance(s, str) and s.strip() for s in steps):
            tool_call_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                tool_id="react.plan",
                payload={
                    "tool_id": "react.plan",
                    "tool_call_id": tool_call_id,
                    "params": params,
                },
            )
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.plan_steps",
                message="react.plan requires non-empty steps list for mode=new|replace.",
                extra={"mode": mode},
                rel="call",
            )
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "bad_plan_steps", "managed": True}
            return state

    if mode == "new":
        plan_obj = {"steps": [s.strip() for s in steps]}
        new_snap = create_plan_snapshot(plan=plan_obj, turn_id=turn_id, created_ts=started_at)
        tool_call_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            tool_id="react.plan",
            payload={
                "tool_id": "react.plan",
                "tool_call_id": tool_call_id,
                "params": params,
                "new_plan_id": new_snap.plan_id,
                "new_snapshot_ref": plan_snapshot_ref(new_snap.plan_id),
            },
        )
        add_block(ctx_browser, build_plan_block(snap=new_snap, turn_id=turn_id, ts=started_at))
        _add_plan_result_summary(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            mode=mode,
            plan_id=new_snap.plan_id,
            latest_snapshot_ref=plan_snapshot_ref(new_snap.plan_id),
        )
        state["plan_id"] = new_snap.plan_id
        state["plan_ts"] = new_snap.created_ts
        state["plan_origin_turn_id"] = new_snap.origin_turn_id
        state["plan_last_ts"] = new_snap.last_ts
        state["plan_steps"] = list(new_snap.steps or [])
        state["plan_status"] = {}
        return state

    if mode == "activate":
        tool_call_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            tool_id="react.plan",
            payload={
                "tool_id": "react.plan",
                "tool_call_id": tool_call_id,
                "params": params,
                "target_plan_id": requested_plan_id,
                "target_snapshot_ref": plan_snapshot_ref(requested_plan_id),
            },
        )
        snap = activate_plan_snapshot(
            blocks=timeline_blocks,
            plan_id=requested_plan_id,
            turn_id=turn_id,
            ts=started_at,
        )
        if snap is None:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.plan_target_missing",
                message="react.plan(mode=activate) target plan_id was not found or not active.",
                extra={"plan_id": requested_plan_id},
                rel="call",
            )
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "plan_target_missing", "managed": True}
            return state
        add_block(ctx_browser, build_plan_block(snap=snap, turn_id=turn_id, ts=started_at))
        _add_plan_result_summary(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            mode=mode,
            plan_id=snap.plan_id,
            target_plan_id=requested_plan_id,
            latest_snapshot_ref=plan_snapshot_ref(snap.plan_id),
            target_snapshot_ref=plan_snapshot_ref(requested_plan_id),
        )
        state["plan_id"] = snap.plan_id
        state["plan_ts"] = snap.created_ts
        state["plan_origin_turn_id"] = snap.origin_turn_id
        state["plan_last_ts"] = snap.last_ts
        state["plan_steps"] = list(snap.steps or [])
        state["plan_status"] = dict(snap.status or {})
        return state

    if mode == "replace":
        plan_obj = {"steps": [s.strip() for s in steps]}
        new_snap = create_plan_snapshot(plan=plan_obj, turn_id=turn_id, created_ts=started_at)
        tool_payload = {
            "tool_id": "react.plan",
            "tool_call_id": tool_call_id,
            "params": params,
            "new_plan_id": new_snap.plan_id,
            "new_snapshot_ref": plan_snapshot_ref(new_snap.plan_id),
            "target_plan_id": requested_plan_id,
            "target_snapshot_ref": plan_snapshot_ref(requested_plan_id),
        }
        tool_call_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            tool_id="react.plan",
            payload=tool_payload,
        )
        old_snap = supersede_plan_snapshot(
            blocks=timeline_blocks,
            plan_id=requested_plan_id,
            turn_id=turn_id,
            ts=started_at,
            by_plan_id=new_snap.plan_id,
        )
        if old_snap is None:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="protocol_violation.plan_target_missing",
                message="react.plan(mode=replace) target plan_id was not found or not active.",
                extra={"plan_id": requested_plan_id},
                rel="call",
            )
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "plan_target_missing", "managed": True}
            return state
        add_block(ctx_browser, build_plan_block(snap=old_snap, turn_id=turn_id, ts=started_at))
        add_block(ctx_browser, build_plan_block(snap=new_snap, turn_id=turn_id, ts=started_at))
        _add_plan_result_summary(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            mode=mode,
            plan_id=new_snap.plan_id,
            target_plan_id=requested_plan_id,
            latest_snapshot_ref=plan_snapshot_ref(new_snap.plan_id),
            target_snapshot_ref=plan_snapshot_ref(requested_plan_id),
        )
        state["plan_id"] = new_snap.plan_id
        state["plan_ts"] = new_snap.created_ts
        state["plan_origin_turn_id"] = new_snap.origin_turn_id
        state["plan_last_ts"] = new_snap.last_ts
        state["plan_steps"] = list(new_snap.steps or [])
        state["plan_status"] = {}
        return state

    # close
    tool_payload = {
        "tool_id": "react.plan",
        "tool_call_id": tool_call_id,
        "params": params,
    }
    tool_payload["target_plan_id"] = requested_plan_id
    tool_payload["target_snapshot_ref"] = plan_snapshot_ref(requested_plan_id)
    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id="react.plan",
        payload=tool_payload,
    )
    snap = close_plan_snapshot(
        blocks=timeline_blocks,
        plan_id=requested_plan_id,
        turn_id=turn_id,
        ts=started_at,
    )
    if snap is None:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_target_missing",
            message="react.plan(mode=close) target plan_id was not found or not active.",
            extra={"plan_id": requested_plan_id},
            rel="call",
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "plan_target_missing", "managed": True}
        return state
    add_block(
        ctx_browser,
        build_plan_block(
            snap=snap,
            turn_id=turn_id,
            ts=started_at,
        ),
    )
    _add_plan_result_summary(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        mode=mode,
        plan_id=snap.plan_id,
        target_plan_id=requested_plan_id,
        latest_snapshot_ref=plan_snapshot_ref(snap.plan_id),
        target_snapshot_ref=plan_snapshot_ref(requested_plan_id),
    )
    refreshed_blocks = list(getattr(ctx_browser.timeline, "blocks", []) or [])
    active_after = latest_active_plan_snapshot(refreshed_blocks)
    if active_after:
        state["plan_id"] = active_after.plan_id
        state["plan_ts"] = active_after.created_ts
        state["plan_origin_turn_id"] = active_after.origin_turn_id
        state["plan_last_ts"] = active_after.last_ts
        state["plan_steps"] = list(active_after.steps or [])
        state["plan_status"] = dict(active_after.status or {})
    else:
        state["plan_id"] = ""
        state["plan_ts"] = ""
        state["plan_origin_turn_id"] = ""
        state["plan_last_ts"] = ""
        state["plan_steps"] = []
        state["plan_status"] = {}
    notice_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        code="plan_closed",
        message="Plan closed by react.plan(mode=close).",
        extra={"plan_id": requested_plan_id},
        rel="result",
    )
    return state


async def handle_react_plan_ack(*, react: Any, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    params = tool_call.get("params") or {}
    raw_updates = params.get("updates") or []
    turn_id = ctx_browser.runtime_ctx.turn_id or ""

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id="react.plan_ack",
        payload={
            "tool_id": "react.plan_ack",
            "tool_call_id": tool_call_id,
            "params": params,
        },
    )

    if not isinstance(raw_updates, list) or not raw_updates:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_ack_updates",
            message="react.plan_ack requires non-empty updates list.",
            extra={},
            rel="call",
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "bad_plan_ack_updates", "managed": True}
        return state

    updates: Dict[str, str] = {}
    allowed = {"done", "failed", "in_progress", "pending"}
    for item in raw_updates:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        status = str(item.get("status") or "").strip()
        if not isinstance(step, int) or step <= 0 or status not in allowed:
            continue
        updates[str(step)] = status
    if not updates:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.plan_ack_updates",
            message="react.plan_ack requires at least one valid {step, status} update.",
            extra={},
            rel="call",
        )
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "bad_plan_ack_updates", "managed": True}
        return state

    plan_steps = state.get("plan_steps") or []
    status_map = state.get("plan_status") or {}
    status_map, blocks = apply_plan_status_updates(
        updates=updates,
        plan_steps=plan_steps if isinstance(plan_steps, list) else [],
        status_map=status_map if isinstance(status_map, dict) else {},
        timeline_blocks=list(getattr(ctx_browser.timeline, "blocks", []) or []),
        turn_id=turn_id,
        iteration=int(state.get("iteration") or 0),
        ts=ctx_browser.runtime_ctx.started_at or "",
    )
    state["plan_status"] = status_map
    if blocks:
        ctx_browser.contribute(blocks=blocks)
    return state


__all__ = ["TOOL_SPEC", "handle_react_plan"]

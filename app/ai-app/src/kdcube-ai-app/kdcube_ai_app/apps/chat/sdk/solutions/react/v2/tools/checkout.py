# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from typing import Any, Dict

import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    add_block,
    notice_block,
    tool_call_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.workspace import (
    checkout_workspace_paths,
    normalize_checkout_requests,
)


TOOL_SPEC = {
    "id": "react.checkout",
    "purpose": (
        "Define the active current-turn workspace by materializing selected fi:<turn_id>.files refs "
        "into turn_<current_turn>/files in order. "
        "Use this when the current workspace itself must contain a runnable/searchable project snapshot, "
        "rather than only materializing historical side views with react.pull."
    ),
    "args": {
        "paths": (
            "ordered list[str] of fi:<turn_id>.files refs to apply into the current-turn workspace. "
            "Later entries override earlier ones if they overlap. "
            "For compatibility, params.version is still accepted as a whole-tree checkout of fi:<turn_id>.files/."
        ),
    },
    "returns": (
        "JSON object {checked_out_from, materialized, missing, errors}. "
        "Checkout replaces the current-turn files/ tree, then applies the requested refs in order. "
        "Historical refs remain available separately via react.pull."
    ),
}


async def handle_react_checkout(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.checkout"
    params = tool_call.get("params") or {}
    raw_paths = params.get("paths")
    if raw_paths is None and isinstance(params, list):
        raw_paths = params
    turn_id = str(getattr(getattr(ctx_browser, "runtime_ctx", None), "turn_id", "") or "").strip()

    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": tool_call.get("params") or {},
        },
    )

    requests, invalid = normalize_checkout_requests(
        raw_paths=raw_paths,
        legacy_version=str(params.get("version") or "").strip(),
        current_turn_id=turn_id,
    )
    if invalid:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.checkout_invalid_paths",
            message="react.checkout requires fi:<turn_id>.files refs in params.paths (or legacy params.version).",
            extra={"tool_id": tool_id, "invalid": invalid},
        )
        state["retry_decision"] = True
        return state

    if not requests:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.checkout_missing_paths",
            message="react.checkout requires params.paths with fi:<turn_id>.files refs (or legacy params.version).",
            extra={"tool_id": tool_id},
        )
        state["retry_decision"] = True
        return state

    result = await checkout_workspace_paths(
        ctx_browser=ctx_browser,
        requests=requests,
        outdir=pathlib.Path(str(state["outdir"])),
    )

    if "workspace_checkout_nonempty" in set(result.get("errors") or []):
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="react.checkout.nonempty",
            message=(
                "Current turn workspace already contains files. "
                "Use react.checkout before current-turn edits/writes when you want to seed the active workspace."
            ),
            extra={"checked_out_from": result.get("checked_out_from") or []},
        )
        state["retry_decision"] = True
        return state

    if result.get("missing") or result.get("errors"):
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="react.checkout.failed",
            message="Failed to fully materialize the requested workspace checkout.",
            extra={
                "checked_out_from": result.get("checked_out_from") or [],
                "missing": result.get("missing") or [],
                "errors": result.get("errors") or [],
            },
        )
        state["retry_decision"] = True
        return state

    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(result, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    add_block(ctx_browser, {
        "turn": turn_id,
        "turn_id": turn_id,
        "type": "react.workspace.checkout",
        "mime": "application/json",
        "path": f"ar:{turn_id}.react.workspace.checkout",
        "text": json.dumps(result, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    state["last_tool_result"] = result
    return state

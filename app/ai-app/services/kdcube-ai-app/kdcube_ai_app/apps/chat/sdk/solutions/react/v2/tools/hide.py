# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict

import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    tc_result_path,
)

TOOL_SPEC = {
    "id": "react.hide",
    "purpose": (
        "Hide a specific timeline path by replacing it with a short placeholder. "
        "Use to reduce visible context size; can be restored via react.read(path)."
        "Use only when the snippet is near the tail of your visible timeline of events. "
        "Enforced by RuntimeCtx.cache.editable_tail_size_in_tokens. "
        "This tool accepts a logical path (ar: fi: tc: so:), not a search query."
    ),
    "args": {
        "path": "str (FIRST FIELD). Logical block path to hide (ar: fi: tc: so:).",
        "replacement": "str (SECOND FIELD). Short replacement text; will auto-append 'retrieve back with react.read(path)'.",
    },
    "returns": "hide result (path + replaced count)",
    "constraints": [
        "`path` must appear first in the params JSON object.",
        "`replacement` must appear second in the params JSON object.",
    ],
}


async def handle_react_hide(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.hide"
    params = tool_call.get("params") or {}
    path = (params.get("path") or "").strip()
    replacement = (params.get("replacement") or "").strip()

    if not path:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_path", "managed": True}
        return state
    if not replacement:
        replacement = "[HIDDEN] retrieve back with react.read(path)"
    if "react.read" not in replacement:
        replacement = replacement.rstrip() + "\n\n(retrieve back with react.read(path))"

    turn_id = ctx_browser.runtime_ctx.turn_id
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

    replaced = 0
    tokens_hidden = 0
    status = "not_found"
    tail_tokens = None
    tail_limit = None
    cache_cfg = getattr(ctx_browser.runtime_ctx, "cache", None)
    if cache_cfg is not None:
        try:
            tail_limit = int(getattr(cache_cfg, "editable_tail_size_in_tokens", 0) or 0)
        except Exception:
            tail_limit = None
    if tail_limit is not None:
        try:
            tail_tokens = ctx_browser.timeline.tail_tokens_from_path(path)
        except Exception:
            tail_tokens = None
        if tail_tokens is not None and tail_tokens > tail_limit:
            status = "too_old"
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="hide_too_old",
                message=(
                    "hide only supports paths near the tail "
                    f"(tail_tokens={tail_tokens}, limit={tail_limit})."
                ),
                extra={"path": path, "tail_tokens": tail_tokens, "tail_limit": tail_limit},
            )
            payload = {
                "path": path,
                "status": status,
                "blocks_hidden": replaced,
                "tokens_hidden": tokens_hidden,
                "tail_tokens": tail_tokens,
                "tail_limit": tail_limit,
            }
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": "application/json",
                "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            })
            state["last_tool_result"] = payload
            return state
    try:
        res = ctx_browser.hide_paths(
            paths=[path],
            replacement_text=replacement,
        )
        if isinstance(res, dict):
            replaced = int(res.get("blocks_hidden") or 0)
            tokens_hidden = int(res.get("tokens_hidden") or 0)
            status = res.get("status") or status
    except Exception as exc:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="hide_failed",
            message=f"hide failed: {exc}",
            extra={"path": path},
        )

    payload = {
        "path": path,
        "status": status,
        "blocks_hidden": replaced,
        "tokens_hidden": tokens_hidden,
        "tail_tokens": tail_tokens,
        "tail_limit": tail_limit,
    }
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
    })
    state["last_tool_result"] = payload
    return state

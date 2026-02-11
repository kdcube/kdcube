# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict

import json

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import tool_call_block, notice_block, add_block

TOOL_SPEC = {
    "id": "react.memory_hide",
    "purpose": (
        "Hide a specific timeline path by replacing it with a short placeholder. "
        "Use to reduce visible context size; can be restored via react.read(path)."
        "Use only when the snippet is near the tail of your visible timeline of events."
    ),
    "args": {
        "path": "str (FIRST FIELD). The block path to hide (e.g., tc:<turn_id>.tool_calls.<id>.out.json or fi:<turn_id>.files/<...>).",
        "replacement": "str (SECOND FIELD). Short replacement text; will auto-append 'retrieve back with react.read(path)'.",
    },
    "returns": "hide result (path + replaced count)",
    "constraints": [
        "`path` must appear first in the params JSON object.",
        "`replacement` must appear second in the params JSON object.",
    ],
}


async def handle_react_memory_hide(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    root_notes = (last_decision.get("notes") or "").strip()
    tool_id = "react.memory_hide"
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
            "notes": root_notes,
            "params": tool_call.get("params") or {},
        },
    )

    replaced = 0
    tokens_hidden = 0
    status = "not_found"
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
            code="memory_hide_failed",
            message=f"memory_hide failed: {exc}",
            extra={"path": path},
        )

    payload = {
        "path": path,
        "status": status,
        "blocks_hidden": replaced,
        "tokens_hidden": tokens_hidden,
    }
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json" if turn_id else "",
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
    })
    state["last_tool_result"] = payload
    return state

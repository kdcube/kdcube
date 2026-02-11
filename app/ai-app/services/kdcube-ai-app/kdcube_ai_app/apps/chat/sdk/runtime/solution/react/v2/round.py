# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict

import kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.call as react_tools


@dataclass
class ReactRound:
    tool_id: str = ""
    tool_call_id: str = ""

    @classmethod
    async def execute(cls,
                      react,
                      state: Dict[str, Any]) -> Dict[str, Any]:
        decision = state.get("last_decision") or {}
        tool_call = decision.get("tool_call") or {}
        tool_id = (tool_call.get("tool_id") or "").strip()
        tool_call_id = state.pop("pending_tool_call_id", None) or tool_call.get("tool_call_id") or uuid.uuid4().hex[:12]
        if not tool_id:
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": "missing_tool_id", "managed": True}
            return state
        ctx_browser = react.ctx_browser
        if tool_id == "react.read":
            return await react_tools.handle_react_read(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.patch":
            return await react_tools.handle_react_patch(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.memory_read":
            return await react_tools.handle_react_memory_read(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.memory_hide":
            return await react_tools.handle_react_memory_hide(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.search_files":
            return await react_tools.handle_react_search_files(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.plan":
            return await react_tools.handle_react_plan(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        if tool_id == "react.write":
            return await react_tools.handle_react_write(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)

        return await react_tools.handle_external_tool(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)


ToolCallView = ReactRound

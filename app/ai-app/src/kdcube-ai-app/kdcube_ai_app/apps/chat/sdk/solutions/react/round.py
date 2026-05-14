# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import time
import uuid
import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, Optional

import kdcube_ai_app.apps.chat.sdk.solutions.react.call as react_tools
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import add_block
from kdcube_ai_app.apps.chat.sdk.util import isoz


@dataclass
class ReactRound:
    tool_id: str = ""
    tool_call_id: str = ""

    @classmethod
    def start(
        cls,
        *,
        ctx_browser: Any,
        tool_call_id: str,
        iteration: int,
    ) -> None:
        if not ctx_browser or not tool_call_id:
            return
        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        add_block(ctx_browser, {
            "type": "react.round.start",
            "author": "react",
            "turn_id": turn_id,
            "ts": ts,
            "mime": "text/plain",
            "path": f"ar:{turn_id}.react.round.start.{tool_call_id}" if turn_id else "",
            "text": "thinking",
            "meta": {
                "tool_call_id": tool_call_id,
                "iteration": iteration,
                "phase": "decision",
            },
            "call_id": tool_call_id,
        })

    @classmethod
    def thinking(
        cls,
        *,
        ctx_browser: Any,
        decision: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        title: str,
        iteration: int,
        tool_call_id: Optional[str] = None,
    ) -> None:
        if not ctx_browser:
            return
        thinking_info: Dict[str, Any] = {}
        if isinstance(decision, dict):
            channels = decision.get("channels") if isinstance(decision.get("channels"), dict) else {}
            thinking_info = channels.get("thinking") if isinstance(channels.get("thinking"), dict) else {}
            if text is None:
                text = thinking_info.get("text") or decision.get("internal_thinking")
        if not isinstance(text, str) or not text.strip():
            return
        def _to_iso(val: Any) -> str:
            if isinstance(val, (int, float)):
                ts_sec = val / 1000.0 if val > 1e12 else float(val)
                return _dt.datetime.fromtimestamp(ts_sec, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
            if isinstance(val, str):
                return isoz(val)
            return ""
        started_at = _to_iso(thinking_info.get("started_at"))
        finished_at = _to_iso(thinking_info.get("finished_at"))
        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        ts = started_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta: Dict[str, Any] = {
            "channel": "thinking",
            "title": title,
            "iteration": iteration,
        }
        call_id = str(tool_call_id or "").strip()
        if call_id:
            meta["tool_call_id"] = call_id
        if started_at:
            meta["started_at"] = started_at
        if finished_at:
            meta["finished_at"] = finished_at
        add_block(ctx_browser, {
            "type": "react.thinking",
            "author": "react",
            "turn_id": turn_id,
            "ts": ts,
            "mime": "text/markdown",
            "path": f"ar:{turn_id}.react.thinking.{iteration}" if turn_id else "",
            "text": text.strip(),
            "meta": meta,
            "call_id": call_id,
        })

    @classmethod
    def note(
        cls,
        *,
        ctx_browser: Any,
        notes: str,
        tool_call_id: str,
        tool_id: str,
        action: str,
        iteration: int,
        ts: Optional[str] = None,
    ) -> None:
        if not ctx_browser or not isinstance(notes, str) or not notes.strip():
            return
        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        ts = str(ts or "").strip() or (_dt.datetime.utcnow().isoformat() + "Z")
        add_block(ctx_browser, {
            "type": "react.notes",
            "author": "react",
            "turn_id": turn_id,
            "ts": ts,
            "mime": "text/markdown",
            "path": f"ar:{turn_id}.react.notes.{tool_call_id}" if turn_id else "",
            "text": notes.strip(),
            "meta": {
                "channel": "timeline_text",
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "action": action,
                "iteration": iteration,
            },
        })

    @classmethod
    def decision_raw(
        cls,
        *,
        ctx_browser: Any,
        decision: Optional[Dict[str, Any]] = None,
        iteration: int,
        reason: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        if not ctx_browser or not isinstance(decision, dict):
            return
        raw_text = (decision.get("raw") or "").strip()
        if not raw_text:
            raw_text = ((decision.get("log") or {}).get("raw_data") or "").strip()
        if not raw_text:
            return
        if not reason:
            channels = decision.get("channels") if isinstance(decision.get("channels"), dict) else {}
            json_chan = channels.get("ReactDecisionOutV2") if isinstance(channels.get("ReactDecisionOutV2"), dict) else {}
            if not isinstance(json_chan, dict) or not (json_chan.get("text") or "").strip():
                reason = "missing_channel.ReactDecisionOutV2"
        turn_id = (ctx_browser.runtime_ctx.turn_id or "")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta: Dict[str, Any] = {
            "channel": "raw",
            "iteration": iteration,
        }
        if reason:
            meta["reason"] = reason
        if tool_call_id:
            meta["tool_call_id"] = tool_call_id
        add_block(ctx_browser, {
            "type": "react.decision.raw",
            "author": "react",
            "turn_id": turn_id,
            "ts": ts,
            "mime": "application/json",
            "path": f"ar:{turn_id}.react.decision.raw.{iteration}" if turn_id else "",
            "text": raw_text,
            "meta": meta,
            **({"call_id": tool_call_id} if tool_call_id else {}),
        })

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
        runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
        sentinel = object()
        previous_iteration = sentinel
        if runtime_ctx is not None:
            previous_iteration = getattr(runtime_ctx, "_current_react_iteration", sentinel)
            try:
                raw_origin_iteration = state.get("pending_tool_origin_iteration")
                if raw_origin_iteration is None:
                    raw_state_iteration = int(state.get("iteration") or 0)
                    raw_origin_iteration = max(0, raw_state_iteration - 1)
                setattr(runtime_ctx, "_current_react_iteration", int(raw_origin_iteration))
            except Exception:
                pass
        try:
            if tool_id == "react.read":
                return await react_tools.handle_react_read(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.pull":
                return await react_tools.handle_react_pull(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.checkout":
                return await react_tools.handle_react_checkout(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.patch":
                return await react_tools.handle_react_patch(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.memsearch":
                return await react_tools.handle_react_memsearch(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.hide":
                return await react_tools.handle_react_hide(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.rg":
                return await react_tools.handle_react_rg(ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.plan":
                return await react_tools.handle_react_plan(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
            if tool_id == "react.write":
                return await react_tools.handle_react_write(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)

            return await react_tools.handle_external_tool(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
        finally:
            if runtime_ctx is not None:
                try:
                    if previous_iteration is sentinel:
                        delattr(runtime_ctx, "_current_react_iteration")
                    else:
                        setattr(runtime_ctx, "_current_react_iteration", previous_iteration)
                except Exception:
                    pass


ToolCallView = ReactRound

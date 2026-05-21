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


def _path_prefixes(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    prefixes: set[str] = set()
    for item in items:
        path = ""
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
        elif isinstance(item, str):
            path = item.strip()
        if not path:
            continue
        if ":" in path:
            prefixes.add(path.split(":", 1)[0] + ":")
        else:
            prefixes.add("plain")
    return sorted(prefixes)


def _tool_params_summary(params: Any) -> Dict[str, Any]:
    if isinstance(params, dict):
        keys = sorted(str(key) for key in params.keys())
        summary: Dict[str, Any] = {
            "shape": "object",
            "keys": keys[:40],
            "key_count": len(keys),
            "redacted": True,
        }
        paths = params.get("paths")
        if isinstance(paths, list):
            summary["paths_count"] = len(paths)
            summary["path_prefixes"] = _path_prefixes(paths)
        items = params.get("items")
        if isinstance(items, list):
            summary["items_count"] = len(items)
            item_prefixes = _path_prefixes(items)
            if item_prefixes:
                summary["item_path_prefixes"] = item_prefixes
        for key in ("query", "prompt", "message", "text", "content", "code"):
            value = params.get(key)
            if isinstance(value, str):
                summary[f"{key}_len"] = len(value)
        for key in ("limit", "top_k", "max_hits", "max_results", "line_start", "line_count"):
            value = params.get(key)
            if isinstance(value, (int, float, bool)):
                summary[key] = value
        return summary
    if isinstance(params, list):
        return {
            "shape": "array",
            "items_count": len(params),
            "path_prefixes": _path_prefixes(params),
            "redacted": True,
        }
    if params is None:
        return {"shape": "none", "redacted": True}
    return {"shape": type(params).__name__, "redacted": True}


def _tool_result_status(result_state: Any) -> tuple[str, int, str]:
    if not isinstance(result_state, dict):
        return "completed", 0, ""
    status = "completed"
    error_count = 0
    error_code = ""
    if result_state.get("exit_reason") == "error" or result_state.get("error"):
        status = "error"
        err = result_state.get("error")
        if isinstance(err, dict):
            error_code = str(err.get("code") or err.get("error") or "").strip()
    last_tool_result = result_state.get("last_tool_result")
    if isinstance(last_tool_result, list):
        for item in last_tool_result:
            if not isinstance(item, dict) or not item.get("error"):
                continue
            error_count += 1
            if not error_code:
                err = item.get("error")
                if isinstance(err, dict):
                    error_code = str(err.get("code") or err.get("error") or "").strip()
                else:
                    error_code = str(err or "").strip()
    if error_count:
        status = "error"
    return status, error_count, error_code


async def _emit_react_tool_call_event(
    *,
    react: Any,
    tool_id: str,
    tool_call_id: str,
    params: Any,
    iteration: Optional[int],
    status: str,
    duration_ms: int,
    result_state: Any = None,
    exception: Optional[BaseException] = None,
) -> None:
    comm = getattr(react, "comm", None)
    service_event = getattr(comm, "service_event", None) if comm is not None else None
    if not callable(service_event):
        return
    result_status, error_count, error_code = _tool_result_status(result_state)
    if status == "completed" and result_status == "error":
        status = "error"
    data: Dict[str, Any] = {
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "tool_family": "react" if tool_id.startswith("react.") else "external",
        "params": _tool_params_summary(params),
        "duration_ms": max(0, int(duration_ms)),
    }
    if iteration is not None:
        data["iteration"] = int(iteration)
    if error_count:
        data["error_count"] = error_count
    if error_code:
        data["error_code"] = error_code
    if exception is not None:
        data["exception_type"] = exception.__class__.__name__
    try:
        result = service_event(
            type="react.tool.call",
            step="react.tool.call",
            status=status,
            title="ReAct Tool Call",
            agent="react.tool",
            data=data,
            auto_markdown=False,
        )
        if hasattr(result, "__await__"):
            await result
    except Exception:
        return


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
            json_chan = channels.get("action") if isinstance(channels.get("action"), dict) else {}
            if not isinstance(json_chan, dict) or not (json_chan.get("text") or "").strip():
                reason = "missing_channel.action"
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
        tool_iteration: Optional[int] = None
        if runtime_ctx is not None:
            previous_iteration = getattr(runtime_ctx, "_current_react_iteration", sentinel)
            try:
                raw_origin_iteration = state.get("pending_tool_origin_iteration")
                if raw_origin_iteration is None:
                    raw_state_iteration = int(state.get("iteration") or 0)
                    raw_origin_iteration = max(0, raw_state_iteration - 1)
                tool_iteration = int(raw_origin_iteration)
                setattr(runtime_ctx, "_current_react_iteration", tool_iteration)
            except Exception:
                pass

        async def _dispatch_tool_call() -> Dict[str, Any]:
            if tool_id == "react.read":
                return await react_tools.handle_react_read(react=react, ctx_browser=ctx_browser, state=state, tool_call_id=tool_call_id)
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

        started_ms = int(time.time() * 1000)
        try:
            result_state = await _dispatch_tool_call()
        except Exception as exc:
            await _emit_react_tool_call_event(
                react=react,
                tool_id=tool_id,
                tool_call_id=tool_call_id,
                params=tool_call.get("params"),
                iteration=tool_iteration,
                status="error",
                duration_ms=int(time.time() * 1000) - started_ms,
                exception=exc,
            )
            raise
        else:
            status, _, _ = _tool_result_status(result_state)
            await _emit_react_tool_call_event(
                react=react,
                tool_id=tool_id,
                tool_call_id=tool_call_id,
                params=tool_call.get("params"),
                iteration=tool_iteration,
                status=status,
                duration_ms=int(time.time() * 1000) - started_ms,
                result_state=result_state,
            )
            return result_state
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

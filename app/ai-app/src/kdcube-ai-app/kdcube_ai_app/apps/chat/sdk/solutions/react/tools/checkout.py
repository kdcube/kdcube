# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from typing import Any, Dict

import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    localize_conversation_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    add_block,
    notice_block,
    tool_call_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    checkout_workspace_paths,
    latest_workspace_checkout_event,
    normalize_checkout_mode,
    normalize_checkout_requests,
)


TOOL_SPEC = {
    "id": "react.checkout",
    "purpose": (
        "Define the active current-turn project workspace by copying selected materialized "
        "conv:fi:conv_<conversation_id>.turn_<id>.git/projects refs into turn_<current>/git/projects in order. "
        "Use this when the current workspace itself must contain a runnable/searchable project snapshot, "
        "rather than only materializing historical side views with react.pull. "
        "For older refs that may not be local on this worker, call react.pull(paths=[...]) first. "
        "react.checkout accepts only conv:fi:...git/projects... workspace refs; external owner namespaces and event refs are not checkout paths."
    ),
    "args": {
        "mode": (
            "optional str: replace|overlay. "
            "replace clears current-turn git/projects/ before applying refs. "
            "overlay keeps existing current-turn git/projects/ and overwrites only the selected files."
        ),
        "paths": (
            "ordered list[str] of conv:fi:conv_<conversation_id>.turn_<id>.git/projects refs to apply into the current-turn workspace. "
            "The conv_<conversation_id> segment names the conversation the ref lives in; use refs exactly as supplied. "
            "Later entries override earlier ones if they overlap."
        ),
    },
    "returns": (
        "JSON object {mode, checked_out_from, checked_out, materialized, missing, errors}. "
        "materialized is a compact tree summary under the current-turn git/projects/ root, not a per-file manifest. "
        "replace clears current-turn git/projects/ before applying refs. "
        "overlay keeps current-turn git/projects/ and applies refs on top. "
        "Historical refs remain available separately via react.pull."
    ),
}


async def handle_react_checkout(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.checkout"
    raw_params = tool_call.get("params") or {}
    params = raw_params if isinstance(raw_params, dict) else {}
    raw_paths = params.get("paths")
    if raw_paths is None and isinstance(raw_params, list):
        raw_paths = raw_params
    raw_mode = str(params.get("mode") or "").strip().lower()
    turn_id = str(getattr(getattr(ctx_browser, "runtime_ctx", None), "turn_id", "") or "").strip()
    conversation_id = str(getattr(getattr(ctx_browser, "runtime_ctx", None), "conversation_id", "") or "").strip()
    if isinstance(raw_paths, list):
        # A ref qualified with the current conversation's scope segment resolves
        # exactly like its local form; refs scoped to another conversation keep
        # their segment and cross-conversation resolution.
        localized_paths = []
        for raw_path in raw_paths:
            if isinstance(raw_path, dict):
                localized = dict(raw_path)
                localized["path"] = localize_conversation_ref(str(raw_path.get("path") or "").strip(), conversation_id)
                localized_paths.append(localized)
            elif isinstance(raw_path, str):
                localized_paths.append(localize_conversation_ref(raw_path.strip(), conversation_id))
            else:
                localized_paths.append(raw_path)
        raw_paths = localized_paths

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

    def _fail(error_code: str, message: str, *, extra: Dict[str, Any] | None = None, retry: bool = True) -> Dict[str, Any]:
        payload = {
            "ok": False,
            "error": error_code,
            "message": message,
            **(extra or {}),
        }
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code=error_code,
            message=message,
            extra={"tool_id": tool_id, **(extra or {})},
        )
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "application/json",
            "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
            "text": json.dumps(payload, ensure_ascii=False, indent=2),
            "meta": {
                "tool_call_id": tool_call_id,
                "tool_id": tool_id,
            },
        })
        state["last_tool_result"] = payload
        if retry:
            state["retry_decision"] = True
        return state

    if raw_mode and raw_mode not in {"replace", "overlay"}:
        return _fail(
            "protocol_violation.checkout_invalid_mode",
            'react.checkout params.mode must be "replace" or "overlay".',
            extra={"mode": raw_mode},
        )
    mode = normalize_checkout_mode(raw_mode)

    requests, invalid = normalize_checkout_requests(
        raw_paths=raw_paths,
        current_turn_id=turn_id,
    )
    if invalid:
        return _fail(
            "protocol_violation.checkout_invalid_paths",
            "react.checkout requires conv:fi:conv_<conversation_id>.turn_<id>.git/projects refs in params.paths.",
            extra={"invalid": invalid},
        )

    if not requests:
        return _fail(
            "protocol_violation.checkout_missing_paths",
            "react.checkout requires params.paths with conv:fi:conv_<conversation_id>.turn_<id>.git/projects refs.",
        )

    result = await checkout_workspace_paths(
        ctx_browser=ctx_browser,
        requests=requests,
        outdir=pathlib.Path(str(state["outdir"])),
        mode=mode,
    )

    if "workspace_checkout_nonempty" in set(result.get("errors") or []):
        return _fail(
            "react.checkout.nonempty",
            (
                "Current turn workspace already contains files. "
                "Use react.checkout(mode=\"replace\") before current-turn edits/writes when you want to make an older ref the active editable workspace, "
                "or react.checkout(mode=\"overlay\") to import selected historical files into the existing workspace."
            ),
            extra={"checked_out_from": result.get("checked_out_from") or [], "mode": mode},
        )

    if result.get("missing") or result.get("errors"):
        return _fail(
            "react.checkout.failed",
            "Failed to copy the requested workspace checkout. Historical refs must be materialized locally first with react.pull.",
            extra={
                "mode": mode,
                "checked_out_from": result.get("checked_out_from") or [],
                "missing": result.get("missing") or [],
                "errors": result.get("errors") or [],
            },
        )

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
    prev_checkout = latest_workspace_checkout_event(getattr(getattr(ctx_browser, "timeline", None), "blocks", []) or [], turn_id=turn_id)
    if mode == "overlay":
        merged_sources = []
        seen = set()
        for item in list((prev_checkout or {}).get("checked_out_from") or []) + list(result.get("checked_out_from") or []):
            raw = str(item or "").strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            merged_sources.append(raw)
    else:
        merged_sources = [str(item).strip() for item in (result.get("checked_out_from") or []) if str(item).strip()]
    checkout_event_payload = dict(result)
    checkout_event_payload["checked_out_from"] = merged_sources
    add_block(ctx_browser, {
        "turn": turn_id,
        "turn_id": turn_id,
        "type": "react.workspace.checkout",
        "mime": "application/json",
        "path": f"conv:ar:{turn_id}.react.workspace.checkout",
        "text": json.dumps(checkout_event_payload, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    state["last_tool_result"] = result
    return state

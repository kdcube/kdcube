# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import build_artifact_meta_block
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import (
    build_timeline_payload,
    TimelineView,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    tc_result_path,
)

TOOL_SPEC = {
    "id": "react.memsearch",
    "purpose": (
        "Search conversation memory (semantic index) and return top matching turn snippets. "
        "Use when visible context is missing needed info. "
        "This tool resolves snippets from the TURN LOG event blocks (timeline) for each hit."
    ),
    "args": {
        "query": "str (FIRST FIELD). Natural-language query to search prior turns.",
        "targets": "list[str] (SECOND FIELD). Any of: assistant|user|attachment. Defaults to all.",
        "top_k": "int (optional). Max hits to return (default 5).",
        "days": "int (optional). Lookback window in days (default 365).",
    },
    "returns": "search hits (snippets + metadata from turn timeline blocks)",
    "constraints": [
        "`query` must appear first in the params JSON object.",
        "`targets` must appear second in the params JSON object.",
    ],
}


async def handle_react_memsearch(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.memsearch"
    params = tool_call.get("params") or {}
    query = (params.get("query") or "").strip()
    raw_targets = params.get("targets")
    if raw_targets is None:
        raw_targets = ["assistant", "user", "attachment"]
    targets = [t for t in (raw_targets or []) if isinstance(t, str) and t.strip()]
    top_k = int(params.get("top_k") or 5)
    days = int(params.get("days") or 365)

    if not query:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_query", "managed": True}
        return state

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

    def score_function(sim: float, rec: float, ts: str) -> float:
        return 0.8 * sim + 0.2 * rec

    search_hits_formatted: List[Dict[str, Any]] = []
    total_tokens = 0
    user = ctx_browser.runtime_ctx.user_id
    conversation_id = ctx_browser.runtime_ctx.conversation_id
    turn_id = ctx_browser.runtime_ctx.turn_id
    try:
        best_tid, hits = await ctx_browser.search(
            custom_score_fn=score_function,
            targets=targets,
            user=user,
            conv=conversation_id,
            scoring_mode="hybrid",
            half_life_days=7.0,
            top_k=top_k,
            days=days,
            with_payload=True,
        )
        from kdcube_ai_app.apps.chat.sdk.util import token_count
        for h in hits or []:
            tid = (h.get("turn_id") or "").strip()
            if not tid:
                continue
            try:
                turn_log = await ctx_browser.get_turn_log(turn_id=tid)
                blocks = list(turn_log.get("blocks") or [])
                timeline_payload = build_timeline_payload(
                    blocks=blocks,
                    sources_pool=turn_log.get("sources_pool") or [],
                )
                tv = TimelineView.from_payload(timeline_payload)
            except Exception:
                continue

            snippets: List[Dict[str, Any]] = []
            want_user = "user" in targets
            want_assistant = "assistant" in targets
            want_attachment = "attachment" in targets

            if want_user:
                path = f"ar:{tid}.user.prompt"
                art = tv.resolve_artifact(path)
                if isinstance(art, dict):
                    text = art.get("text") or ""
                    if text:
                        total_tokens += token_count(text)
                    snippets.append({
                        "role": "user",
                        "path": path,
                        "text": text,
                        "ts": art.get("ts") or "",
                    })
            if want_assistant:
                path = f"ar:{tid}.assistant.completion"
                art = tv.resolve_artifact(path)
                if isinstance(art, dict):
                    text = art.get("text") or ""
                    if text:
                        total_tokens += token_count(text)
                    snippets.append({
                        "role": "assistant",
                        "path": path,
                        "text": text,
                        "ts": art.get("ts") or "",
                    })
            if want_attachment:
                # Collect attachment meta blocks from blocks
                for blk in blocks:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") not in {"user.attachment.meta", "user.attachment"}:
                        continue
                    if (blk.get("path") or "").startswith(f"fi:{tid}.user.attachments/"):
                        text = (blk.get("text") or "").strip()
                        if text:
                            total_tokens += token_count(text)
                        snippets.append({
                            "role": "attachment",
                            "path": blk.get("path") or "",
                            "text": text,
                            "ts": blk.get("ts") or "",
                        })

            search_hits_formatted.append({
                "turn_id": tid,
                "snippets": snippets,
                "score": h.get("score"),
                "sim_score": h.get("sim"),
                "recency_score": h.get("rec"),
                "matched_via_role": h.get("matched_via_role"),
                "source_query": h.get("source_query"),
                "ts": h["ts"].isoformat() if hasattr(h.get("ts"), "isoformat") else h.get("ts"),
                "best_turn_id": best_tid,
            })
    except Exception as exc:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": f"memsearch_failed:{exc}", "managed": True}
        return state

    artifact_path = tc_result_path(turn_id=turn_id, call_id=tool_call_id)
    meta_block = build_artifact_meta_block(
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        artifact={"artifact_kind": "inline", "visibility": "internal", "tool_id": tool_id, "tool_call_id": tool_call_id, "value": {"mime": "application/json"}},
        artifact_path=artifact_path,
        physical_path="",
    )
    add_block(ctx_browser, meta_block)
    payload = {
        "hits": search_hits_formatted,
        "tokens": total_tokens,
    }
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": artifact_path,
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
    })
    state["last_tool_result"] = search_hits_formatted
    return state

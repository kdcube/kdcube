# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import (
    build_artifact_meta_block,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
)

TOOL_SPEC = {
    "id": "react.read",
    "purpose": (
        "Read artifacts or skills into the visible context so you can use them. "
        "Paths must be context paths (fi:/ar:/so:/sk:), not physical paths. "
        "Each path you read becomes visible in the timeline; skills are shown with ACTIVE 💡 banner."
    ),
    "args": {
        "paths": "list[str] context paths to read (files via fi:<turn_id>.files/<filepath>, sources via so:sources_pool[...], skills via sk:<skill_id or num>)",
    },
    "returns": "ok",
}


async def handle_react_read(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    root_notes = (last_decision.get("notes") or "").strip()
    tool_id = "react.read"
    params = tool_call.get("params") or {}
    raw_paths = params.get("paths")
    if raw_paths is None and isinstance(params, list):
        raw_paths = params
    raw_paths = raw_paths if isinstance(raw_paths, list) else []
    paths = [str(p).strip() for p in raw_paths if str(p).strip()]

    turn_id = (ctx_browser.runtime_ctx.turn_id or "")
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

    skill_paths = [p for p in paths if p.startswith("sk:") or p.startswith("SK") or p.startswith("skill:")]
    artifact_paths = [p for p in paths if p not in skill_paths]
    if skill_paths:
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
                import_skillset,
                build_skill_short_id_map,
                build_skills_instruction_block,
            )
            short_map = build_skill_short_id_map(consumer="solver.react.decision")
            skill_ids = import_skillset(skill_paths, short_id_map=short_map)
            loaded = state.setdefault("loaded_skills", set())
            if not isinstance(loaded, set):
                loaded = set(loaded)
                state["loaded_skills"] = loaded
            for sid in skill_ids:
                if not sid or sid in loaded:
                    continue
                loaded.add(sid)
                skill_text = build_skills_instruction_block([sid])
                add_block(ctx_browser, {
                    "turn": turn_id,
                    "type": "react.tool.result",
                    "call_id": tool_call_id,
                    "mime": "text/markdown",
                    "path": f"sk:{sid}",
                    "text": f"ACTIVE 💡{skill_text}",
                })
        except Exception:
            pass

    missing_artifacts: List[str] = []
    items: List[Dict[str, Any]] = []
    try:
        items = ctx_browser.timeline_artifacts(
            paths=artifact_paths,
        )
    except Exception:
        items = []
    if items:
        paths_seen = {item.get("context_path") for item in (items or [])}
        if paths_seen:
            try:
                ctx_browser.unhide_paths(paths=list(paths_seen))
            except Exception:
                pass
    total_tokens = 0
    for item in items or []:
        art = item.get("artifact") or {}
        ctx_path = item.get("context_path") or ""
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact={"artifact_kind": "inline", "visibility": "internal", "tool_id": tool_id, "tool_call_id": tool_call_id, "value": {"mime": None}},
            artifact_path=ctx_path,
            physical_path="",
        )
        add_block(ctx_browser, meta_block)
        art_text = art.get("text") if isinstance(art, dict) else None
        art_fmt = (art.get("format") or "text").lower() if isinstance(art, dict) else "text"
        if isinstance(art_text, str) and art_text.strip():
            try:
                from kdcube_ai_app.apps.chat.sdk.util import token_count
                total_tokens += token_count(art_text)
            except Exception:
                pass
            if art_fmt in {"json"}:
                mime = "application/json"
            elif art_fmt in {"html"}:
                mime = "text/html"
            else:
                mime = "text/markdown"
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": mime,
                "path": ctx_path or f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json",
                "text": art_text,
            })
    if items:
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "application/json",
            "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json" if turn_id else "",
            "text": json.dumps({"paths": artifact_paths, "tokens": total_tokens}, ensure_ascii=False),
        })
    if artifact_paths and not items:
        missing_artifacts.extend(artifact_paths)
    if missing_artifacts:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="read_paths_missing",
            message=f"react.read requested non-existent paths: {missing_artifacts}",
            extra={"missing": missing_artifacts, "tool_id": tool_id},
        )
    state["last_tool_result"] = []
    return state

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
    tc_result_path,
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
            "params": tool_call.get("params") or {},
        },
    )

    skill_paths = [p for p in paths if p.startswith("sk:") or p.startswith("SK") or p.startswith("skill:") or p.startswith("skills.")]
    artifact_paths = [p for p in paths if p not in skill_paths]
    if skill_paths:
        try:
            from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
                import_skillset,
                build_skill_short_id_map,
                build_skills_instruction_block,
            )
            short_map = build_skill_short_id_map(consumer="solver.react.decision.v2")
            normalized_skills: List[str] = []
            for raw in skill_paths:
                s = str(raw or "").strip()
                if not s:
                    continue
                if s.startswith("sk:"):
                    s = s[len("sk:"):].strip()
                if s.startswith("skill:"):
                    s = s[len("skill:"):].strip()
                if s.startswith("skills."):
                    s = s[len("skills."):].strip()
                if s.isdigit():
                    s = f"SK{s}"
                normalized_skills.append(s)
            skill_ids = import_skillset(normalized_skills, short_id_map=short_map)
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
    per_path: List[Dict[str, Any]] = []
    items_by_path = {item.get("context_path"): item for item in (items or []) if item.get("context_path")}
    for raw_path in artifact_paths:
        path = raw_path
        display_path = raw_path
        if path.startswith("so:"):
            path = path[len("so:"):]
            display_path = raw_path
        item = items_by_path.get(path)
        if not item:
            missing_artifacts.append(display_path or path)
            per_path.append({"path": display_path, "missing": True})
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": "text/markdown",
                "path": display_path or tc_result_path(turn_id=turn_id, call_id=tool_call_id),
                "text": f"{display_path}: artifact is missing.",
            })
            continue

        art = item.get("artifact") or {}
        ctx_path = item.get("context_path") or path
        if display_path.startswith("so:"):
            ctx_path = display_path
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact={"tool_id": tool_id, "tool_call_id": tool_call_id, "value": {}},
            artifact_path=ctx_path,
            physical_path="",
        )
        add_block(ctx_browser, meta_block)

        tokens = 0
        art_text = art.get("text") if isinstance(art, dict) else None
        art_base64 = art.get("base64") if isinstance(art, dict) else None
        art_fmt = (art.get("format") or "text").lower() if isinstance(art, dict) else "text"
        art_mime = art.get("mime") if isinstance(art, dict) else None
        if (not isinstance(art_text, str) or not art_text.strip()) and path.startswith("sources_pool["):
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import build_sources_pool_text
                sources = ctx_browser.timeline.resolve_sources_pool(path)
                if sources:
                    art_text = build_sources_pool_text(sources_pool=sources)
                    art_fmt = "text"
            except Exception:
                pass
        if isinstance(art_text, str) and art_text.strip():
            try:
                from kdcube_ai_app.apps.chat.sdk.util import token_count
                tokens = token_count(art_text)
                total_tokens += tokens
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
                "path": ctx_path or tc_result_path(turn_id=turn_id, call_id=tool_call_id),
                "text": art_text,
            })
        elif isinstance(art_base64, str) and art_base64:
            add_block(ctx_browser, {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "mime": art_mime or "application/octet-stream",
                "path": ctx_path or tc_result_path(turn_id=turn_id, call_id=tool_call_id),
                "base64": art_base64,
            })
        per_path_entry = {"path": ctx_path}
        if tokens:
            per_path_entry["tokens"] = tokens
        per_path.append(per_path_entry)

    if artifact_paths:
        summary = {"paths": per_path, "total_tokens": total_tokens}
        if missing_artifacts:
            summary["missing"] = missing_artifacts
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "mime": "application/json",
            "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
            "text": json.dumps(summary, ensure_ascii=False),
        })
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

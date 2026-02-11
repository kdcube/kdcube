# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import (
    build_artifact_meta_block,
    materialize_inline_artifact_to_file,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    is_safe_relpath,
    host_artifact_file,
    emit_hosted_files,
    infer_format_from_path
)

TOOL_SPEC = {
    "id": "react.write",
    "purpose": (
        "Author content and stream it to the user. "
        "If kind='display', content is streamed only; if kind='file', content is streamed and also shared as a file. "
        "Use channel='timeline_text' for short text; channel='canvas' for larger/visual content. "
        "Use channel='internal' to write user-invisible notes (they are stored in the timeline as react.note). "
        "When channel='canvas', the file extension MUST match a supported canvas format: "
        ".md/.markdown, .html/.htm, .mermaid/.mmd, .json, .yaml/.yml, .txt, .xml. "
        "react.write only writes text-based files. For PDFs/PPTX/DOCX/PNG, use rendering_tools.write_* "
        "or exec tools to generate the artifact."
        "Include citations with SIDs from sources_pool when using sources."
        "If you build your content based on prior artifacts or sources, ensure those are visible in the journal. Otherwise read them first via react.read."
        "For recorder to work properly, fill the function params in the order they are stated below"
        "This tool results in an artifact which is a file with visibility='external' and kind set to your choice"
        "Note if you use this tool to generate the content for rendering tools.write_* tools, you must read the relevant skill(s) to produce the proper content. Ensure you see the proper skills in the journal or load them first via react.read."
    ),
    "args": {
        "path": "str (FIRST FIELD). Filepath of this artifact.",
        "channel": "str (SECOND FIELD). 'canvas' (default) or 'timeline_text' or 'internal'.",
        "content": "str|object (THIRD FIELD). Content to record.",
        "kind": "str (FOURTH FIELD). 'display' or 'file'.",
    },
    "returns": "content captured",
    "constraints": [
        "`path` must appear first in the params JSON object.",
        "`channel` must appear second in the params JSON object.",
        "`content` must appear third in the params JSON object.",
        "`kind` must appear fourth in the params JSON object.",
    ],
}


async def handle_react_write(*, react: Any, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    root_notes = (last_decision.get("notes") or "").strip()
    tool_id = "react.write"
    params = tool_call.get("params") or {}
    artifact_name = str(params.get("path") or "").strip()
    fmt = infer_format_from_path(artifact_name)
    generated_data = params.get("content")
    kind = str(params.get("kind") or "display").strip().lower()
    channel = str(params.get("channel") or "canvas").strip().lower()
    visibility = "internal" if channel == "internal" else "external"

    if not artifact_name:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_artifact_name", "managed": True}
        return state
    phys_path, rel_path, rewritten = normalize_physical_path(
        artifact_name, turn_id=ctx_browser.runtime_ctx.turn_id or ""
    )
    if rewritten:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.path_rewritten",
            message=f"Path rewritten to current-turn path: {phys_path}",
            extra={"original": params.get("path"), "normalized": phys_path},
        )
    if not phys_path or not is_safe_relpath(rel_path):
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "unsafe_path", "managed": True}
        return state
    artifact_name = phys_path

    text = None
    if isinstance(generated_data, str):
        text = generated_data
    else:
        try:
            text = json.dumps(generated_data, ensure_ascii=False, indent=2)
        except Exception:
            text = str(generated_data)

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

    tokens_written = 0
    if isinstance(text, str) and text:
        try:
            from kdcube_ai_app.apps.chat.sdk.util import token_count
            tokens_written = token_count(text)
        except Exception:
            tokens_written = 0

    sources_used: List[Any] = []
    if isinstance(text, str) and text.strip():
        try:
            fmt_norm = (fmt or "").strip().lower()
            if fmt_norm in {"markdown", "md", "text", "html"}:
                sources_used.extend(extract_citation_sids_any(text))
            elif fmt_norm in {"json", "yaml", "yml"}:
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    sidecar = payload.get("citations")
                    if isinstance(sidecar, list):
                        buf = []
                        for it in sidecar:
                            if isinstance(it, dict) and isinstance(it.get("sids"), list):
                                for x in it["sids"]:
                                    if isinstance(x, int):
                                        buf.append(x)
                        if buf:
                            sources_used.extend(sorted(set(buf)))
        except Exception:
            pass

    artifact_view = build_artifact_view(
        turn_id=turn_id,
        is_current=True,
        artifact_id=rel_path or artifact_name,
        tool_id=tool_id,
        # Use rel_path for saving inline content under outdir/<turn_id>/files.
        value={"format": fmt or "markdown", "content": text, "path": (rel_path or artifact_name), "text": text},
        summary="",
        artifact_kind="display" if kind == "display" else "file",
        visibility=visibility,
        description=root_notes,
        channel=channel,
        sources_used=sources_used,
        inputs=tool_call.get("params") or {},
        call_record_rel=None,
        call_record_abs=None,
        error=None,
        content_lineage=[],
        tool_call_id=tool_call_id,
        tool_call_item_index=0,
        artifact_stats=None,
    )
    materialize_inline_artifact_to_file(
        artifact=artifact_view,
        outdir=pathlib.Path(state["outdir"]),
        turn_id=turn_id,
        filename_hint=rel_path or artifact_name,
        mime_hint=None,
        visibility=visibility,
        scratchpad=None,
    )
    artifact = artifact_view.raw
    # Ensure hosting reads the physical path (outdir/<turn_id>/files/...)
    if isinstance(artifact.get("value"), dict):
        artifact["value"]["path"] = artifact_name
    hosted = []
    if kind == "file" and visibility != "internal":
        hosted = await host_artifact_file(
            hosting_service=react.hosting_service,
            comm=react.comm,
            runtime_ctx=ctx_browser.runtime_ctx,
            artifact=artifact,
            outdir=pathlib.Path(state["outdir"]),
        )
        await emit_hosted_files(
            hosting_service=react.hosting_service,
            hosted=hosted,
            should_emit=(channel != "internal"),
        )
        abs_path = pathlib.Path(state["outdir"]) / artifact_name
        if not abs_path.exists():
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="react.write.hosting_failed",
                message="Hosting failed (file missing). User will not receive a downloadable file.",
                extra={"physical_path": artifact_name, "outdir": str(state.get("outdir") or "")},
            )
        elif (react.hosting_service and react.comm) and not hosted:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="react.write.hosting_failed",
                message="Hosting failed (no hosted result). User will not receive a downloadable file.",
                extra={"physical_path": artifact_name, "outdir": str(state.get("outdir") or "")},
            )
    artifact_rel = (rel_path or "").strip()
    artifact_path = f"fi:{turn_id}.files/{artifact_rel}" if (turn_id and artifact_rel) else ""
    physical_path = artifact_name
    edited = detect_edit(
        timeline=getattr(ctx_browser, "timeline", None),
        artifact_path=artifact_path,
        tool_call_id=tool_call_id,
    )
    meta_block = build_artifact_meta_block(
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        artifact=artifact,
        artifact_path=artifact_path,
        physical_path=physical_path,
        edited=edited,
    )
    add_block(ctx_browser, meta_block)
    if isinstance(text, str) and text.strip():
        fmt_norm = (fmt or "").strip().lower()
        if fmt_norm in {"json"}:
            mime = "application/json"
        elif fmt_norm in {"html"}:
            mime = "text/html"
        else:
            mime = "text/markdown"
        add_block(ctx_browser, {
            "turn": turn_id,
            "type": "react.note" if channel == "internal" else "react.tool.result",
            "call_id": tool_call_id,
            "mime": mime,
            "path": artifact_path,
            "text": text,
            "meta": {"channel": channel} if channel == "internal" else None,
        })
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json" if turn_id else "",
        "text": json.dumps({"path": artifact_path, "tokens": tokens_written}, ensure_ascii=False),
    })
    state["last_tool_result"] = []
    return state

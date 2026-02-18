# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import (
    build_artifact_meta_block,
    materialize_inline_artifact_to_file,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_citation_sids_any
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
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
        "Use channel='timeline_text' ONLY for short markdown text (status/brief summary); "
        "use channel='canvas' for LARGE content (even markdown) or any non‑markdown. "
        "Use channel='internal' to write user-invisible notes (they are stored in the timeline as react.note). "
        "The file extension MUST match the content format (e.g., HTML -> .html, Markdown -> .md). "
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
    ext_notice = None
    rewrite_notice = None
    if channel == "canvas":
        try:
            ext = pathlib.Path(artifact_name).suffix.lower()
        except Exception:
            ext = ""
        allowed_exts = {".md", ".markdown", ".html", ".htm", ".mermaid", ".mmd", ".json", ".yaml", ".yml", ".txt", ".xml"}
        if ext and ext not in allowed_exts:
            ext_notice = {
                "code": "protocol_violation.write_extension_mismatch",
                "message": (
                    "react.write(canvas) supports only text formats. "
                    "Use .md/.html/.mmd/.json/.yaml/.txt/.xml or use rendering_tools.write_* for binary files."
                ),
                "extra": {"path": artifact_name, "ext": ext},
            }
    phys_path, rel_path, rewritten = normalize_physical_path(
        artifact_name, turn_id=ctx_browser.runtime_ctx.turn_id or ""
    )
    if rewritten:
        rewrite_notice = {
            "code": "protocol_violation.path_rewritten",
            "message": f"Path rewritten to current-turn path: {phys_path}",
            "extra": {"original": params.get("path"), "normalized": phys_path},
        }
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
    artifact_rel = (rel_path or "").strip()
    artifact_path = f"fi:{turn_id}.files/{artifact_rel}" if (turn_id and artifact_rel) else ""
    display_params = dict(tool_call.get("params") or {})
    if "content" in display_params:
        raw_content = display_params.get("content")
        if isinstance(raw_content, str):
            content_str = raw_content
        else:
            try:
                content_str = json.dumps(raw_content, ensure_ascii=False)
            except Exception:
                content_str = str(raw_content)
        snippet = content_str[:100]
        suffix = f"... [see {artifact_path}]" if artifact_path else "... [see output artifact]"
        display_params["content"] = f"{snippet} {suffix}".strip()
    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": display_params,
        },
    )
    if rewrite_notice:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code=rewrite_notice["code"],
            message=rewrite_notice["message"],
            extra=rewrite_notice.get("extra"),
            rel="call",
        )
    if ext_notice:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code=ext_notice["code"],
            message=ext_notice["message"],
            extra=ext_notice.get("extra"),
            rel="call",
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
        description="",
        channel=channel,
        sources_used=sources_used,
        inputs=tool_call.get("params") or {},
        call_record_rel=None,
        call_record_abs=None,
        error=None,
        content_lineage=[],
        tool_call_id=tool_call_id,
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
    if visibility != "internal":
        hosted = await host_artifact_file(
            hosting_service=react.hosting_service,
            comm=react.comm,
            runtime_ctx=ctx_browser.runtime_ctx,
            artifact=artifact,
            outdir=pathlib.Path(state["outdir"]),
        )
        if (not hosted) and artifact_rel and artifact_rel != artifact_name:
            # Fallback: some deployments set outdir per-turn; try relpath lookup.
            try:
                if isinstance(artifact.get("value"), dict):
                    artifact["value"]["path"] = artifact_rel
                hosted = await host_artifact_file(
                    hosting_service=react.hosting_service,
                    comm=react.comm,
                    runtime_ctx=ctx_browser.runtime_ctx,
                    artifact=artifact,
                    outdir=pathlib.Path(state["outdir"]),
                )
            except Exception:
                pass
        await emit_hosted_files(
            hosting_service=react.hosting_service,
            hosted=hosted,
            should_emit=(kind == "file" and channel != "internal"),
        )
        abs_path = pathlib.Path(state["outdir"]) / artifact_name
        if kind != "display":
            if not abs_path.exists():
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="react.write.hosting_failed",
                    message="Hosting failed (file missing). User will not receive a downloadable file.",
                    extra={"physical_path": artifact_name, "outdir": str(state.get("outdir") or "")},
                    rel="result",
                )
            elif (react.hosting_service and react.comm) and not hosted:
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="react.write.hosting_failed",
                    message="Hosting failed (no hosted result). User will not receive a downloadable file.",
                    extra={"physical_path": artifact_name, "outdir": str(state.get("outdir") or "")},
                    rel="result",
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
        tokens=tokens_written,
    )
    add_block(ctx_browser, meta_block)
    meta_extra = {"tool_call_id": tool_call_id, "turn_id": turn_id}
    try:
        meta_text = meta_block.get("text") if isinstance(meta_block, dict) else None
        if isinstance(meta_text, str) and meta_text.strip():
            meta_extra["digest"] = meta_text
    except Exception:
        pass
    for key in ("hosted_uri", "rn", "key", "physical_path"):
        try:
            val = (artifact.get("value") or {}).get(key) or artifact.get(key)
        except Exception:
            val = None
        if val:
            meta_extra[key] = val
    if not meta_extra.get("physical_path"):
        legacy = (artifact.get("value") or {}).get("local_path") or artifact.get("local_path")
        if legacy:
            meta_extra["physical_path"] = legacy
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
            "meta": {
                **meta_extra,
                **({"channel": channel} if channel == "internal" else {}),
            },
        })
    state["last_tool_result"] = []
    return state

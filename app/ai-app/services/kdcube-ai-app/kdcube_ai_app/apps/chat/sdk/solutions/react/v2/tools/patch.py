# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import (
    build_artifact_meta_block,
    build_artifact_view,
    normalize_physical_path,
    detect_edit,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    notice_block,
    apply_unified_diff_to_file,
    run_post_patch_check,
    is_safe_relpath,
    add_block,
    host_artifact_file,
    emit_hosted_files,
    tc_result_path,
    infer_format_from_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import rehost_files_from_timeline

TOOL_SPEC = {
    "id": "react.patch",
    "purpose": (
        "Apply a text patch to an existing file and stream the patch to the user. "
        "If patch starts with ---/+++/@@ it is treated as unified diff, otherwise replaces the whole file. "
        "If kind='file' the updated file is shared; if kind='display' it is streamed only."
    ),
    "args": {
        "path": "str (FIRST FIELD). Filepath of the artifact to patch (relative under <turn_id>/files/).",
        "channel": "str (SECOND FIELD). 'canvas' (default) or 'timeline_text'.",
        "patch": "str (THIRD FIELD). Unified diff if starts with ---/+++/@@; otherwise full replacement.",
        "kind": "str (FOURTH FIELD). 'display' or 'file'.",
    },
    "returns": "patch applied",
    "constraints": [
        "`path` must appear first in the params JSON object.",
        "`channel` must appear second in the params JSON object.",
        "`patch` must appear third in the params JSON object.",
        "`kind` must appear fourth in the params JSON object.",
    ],
}


def _mime_for_format(fmt: str) -> str:
    fmt_norm = (fmt or "").strip().lower()
    if fmt_norm == "json":
        return "application/json"
    if fmt_norm == "html":
        return "text/html"
    if fmt_norm == "xml":
        return "application/xml"
    if fmt_norm == "yaml":
        return "application/yaml"
    if fmt_norm == "text":
        return "text/plain"
    if fmt_norm == "mermaid":
        return "text/plain"
    return "text/markdown"


async def handle_react_patch(*, react: Any, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.patch"
    params = tool_call.get("params") or {}
    artifact_name = str(params.get("path") or "").strip()
    patch_text = params.get("patch")
    channel = str(params.get("channel") or "canvas").strip().lower()
    kind = str(params.get("kind") or "display").strip().lower()

    if not artifact_name:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_artifact_name", "managed": True}
        return state
    original_path = artifact_name
    phys_path, rel_path, rewritten = normalize_physical_path(
        artifact_name, turn_id=ctx_browser.runtime_ctx.turn_id or ""
    )
    if rewritten:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="protocol_violation.path_rewritten",
            message="Path contained a turn/files prefix; rewritten to current-turn relative path.",
            extra={"original": params.get("path"), "normalized": phys_path},
            rel="call",
        )
    if not phys_path or not is_safe_relpath(rel_path):
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "unsafe_path", "managed": True}
        return state
    artifact_name = phys_path
    if not isinstance(patch_text, str) or not patch_text.strip():
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_patch", "managed": True}
        return state

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

    outdir = pathlib.Path(state["outdir"])
    abs_path = outdir / artifact_name
    source_abs = abs_path
    target_preexisting = abs_path.exists()
    # If patch referenced an older turn file, copy it into current turn namespace
    if original_path.startswith("turn_") and "/files/" in original_path:
        try:
            old_turn, old_rel = original_path.split("/files/", 1)
        except Exception:
            old_turn, old_rel = "", ""
        if old_turn and old_turn != turn_id:
            old_abs = outdir / old_turn / "files" / old_rel
            if not old_abs.exists():
                try:
                    await rehost_files_from_timeline(
                        ctx_browser=ctx_browser,
                        paths=[original_path],
                        outdir=outdir,
                    )
                except Exception:
                    pass
            if old_abs.exists() and not abs_path.exists():
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    abs_path.write_text(old_abs.read_text(encoding="utf-8"), encoding="utf-8")
                    source_abs = old_abs
                except Exception:
                    pass
            elif old_abs.exists() and not target_preexisting:
                source_abs = old_abs
    if not abs_path.exists():
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "patch_target_missing", "managed": True}
        return state

    is_unified = any(patch_text.lstrip().startswith(x) for x in ("---", "+++", "@@"))
    display_patch_text = patch_text
    if is_unified:
        patched, display_patch_text, err = apply_unified_diff_to_file(
            target_path=abs_path,
            patch_text=patch_text,
            source_path=source_abs,
        )
        if patched is None:
            state["exit_reason"] = "error"
            state["error"] = {"where": "tool_execution", "error": err or "patch_failed", "managed": True}
            return state
    else:
        patched = patch_text
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(patched, encoding="utf-8")
    ok, msg = run_post_patch_check(abs_path)
    if not ok:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="post_patch_check_failed",
            message=msg or "post-patch check failed",
            extra={"path": artifact_name},
            rel="result",
        )

    fmt = infer_format_from_path(rel_path or artifact_name)
    artifact_view = build_artifact_view(
        turn_id=turn_id,
        is_current=True,
        artifact_id=rel_path or artifact_name,
        tool_id=tool_id,
        value={"format": "patch", "content": display_patch_text, "path": (rel_path or artifact_name), "text": display_patch_text},
        summary="",
        artifact_kind="display" if kind == "display" else "file",
        visibility="external",
        description="",
        channel=channel,
        sources_used=[],
        inputs=tool_call.get("params") or {},
        call_record_rel=None,
        call_record_abs=None,
        error=None,
        content_lineage=[],
        tool_call_id=tool_call_id,
        artifact_stats=None,
    )
    artifact = artifact_view.raw
    if isinstance(artifact.get("value"), dict):
        artifact["value"]["path"] = artifact_name
        if kind == "file":
            artifact["value"]["type"] = "file"
            artifact["value"]["text"] = patched
            artifact["value"]["content"] = patched
            artifact["value"]["mime"] = _mime_for_format(fmt)
            artifact["path"] = artifact_name
    hosted = []
    if kind == "file":
        hosted = await host_artifact_file(
            hosting_service=react.hosting_service,
            comm=react.comm,
            runtime_ctx=ctx_browser.runtime_ctx,
            artifact=artifact,
            outdir=outdir,
        )
        if (not hosted) and rel_path and rel_path != artifact_name:
            try:
                if isinstance(artifact.get("value"), dict):
                    artifact["value"]["path"] = rel_path
                hosted = await host_artifact_file(
                    hosting_service=react.hosting_service,
                    comm=react.comm,
                    runtime_ctx=ctx_browser.runtime_ctx,
                    artifact=artifact,
                    outdir=outdir,
                )
            except Exception:
                pass
        await emit_hosted_files(
            hosting_service=react.hosting_service,
            hosted=hosted,
            should_emit=True,
        )
        if not abs_path.exists():
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="react.patch.hosting_failed",
                message="Hosting failed (file missing). User will not receive a downloadable file.",
                rel="result",
            )
        elif (react.hosting_service and react.comm) and not hosted:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="react.patch.hosting_failed",
                message="Hosting failed (no hosted result). User will not receive a downloadable file.",
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
    )
    add_block(ctx_browser, meta_block)
    meta_extra = {"tool_call_id": tool_call_id, "turn_id": turn_id, "tool_id": tool_id}
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
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "text/markdown",
        "path": artifact_path,
        "text": display_patch_text,
        "meta": {
            **meta_extra,
        },
    })
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps({"path": artifact_path, "ok": True}, ensure_ascii=False),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })

    state["last_tool_result"] = []
    return state

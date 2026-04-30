# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    tool_call_block,
    notice_block,
    add_block,
    tc_result_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools_.search_files import search_files
from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import _safe_relpath
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import physical_path_to_logical_path
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for


TOOL_SPEC = {
    "id": "react.search_files",
    "purpose": (
        "Search local files under OUT_DIR (default) or workdir by file name regex and/or text content regex. "
        "It does not load full file content. "
        "Results return `root` plus `hits`. Each hit includes `path`, which is relative to the searched root and does not include that root prefix; "
        "`size_bytes`; and, for OUT_DIR hits, `logical_path` so it can be read with react.read."
    ),
    "args": {
        "root": (
            "optional root selector. Omit or use 'outdir' to search the full OUT_DIR. "
            "Use 'outdir/<subdir>' to search a subtree under OUT_DIR. "
            "Use 'workdir' to search the full workdir, or 'workdir/<subdir>' to narrow it."
        ),
        "name_regex": "optional Python regex matched against the basename only, not the full path",
        "content_regex": (
            "optional Python regex matched against UTF-8-decoded file text. "
            "If set, each file is read up to max_bytes."
        ),
        "max_bytes": "optional per-file byte cap used only for content_regex searches; default 1000000",
        "max_hits": "optional maximum number of matching paths to return; default 200",
    },
    "returns": (
        "JSON object {root, hits}. "
        "Each hit has path (relative to searched root), size_bytes, and optional logical_path. "
        "OUT_DIR hits include logical_path for react.read; workdir hits do not."
    ),
}


async def handle_react_search_files(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.search_files"
    params = tool_call.get("params") or {}
    root_sel = (params.get("root") or "").strip()
    name_regex = params.get("name_regex")
    content_regex = params.get("content_regex")
    max_bytes = int(params.get("max_bytes") or 1_000_000)
    max_hits = int(params.get("max_hits") or 200)

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
    root_dir = outdir
    root_kind = "outdir"
    normalized_root = "outdir"
    root_virtual_prefix = ""
    artifact_outdir = artifact_outdir_for(outdir, create=False)
    if not artifact_outdir.exists():
        artifact_outdir = outdir
    if root_sel:
        root_sel_lc = root_sel.lower()
        if root_sel_lc == "outdir":
            root_dir = artifact_outdir
            normalized_root = "outdir"
            root_virtual_prefix = ""
        elif root_sel_lc.startswith("outdir/"):
            rel = root_sel[len("outdir/"):].lstrip("/")
            if not rel or not _safe_relpath(rel):
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="search_files_invalid_root",
                    message="invalid outdir root selector.",
                    extra={"tool_id": tool_id, "root": root_sel},
                    rel="result",
                )
                state["last_tool_result"] = []
                return state
            root_dir = artifact_outdir / rel
            normalized_root = f"outdir/{rel}"
            root_virtual_prefix = rel
        elif root_sel_lc == "workdir":
            workdir_raw = getattr(getattr(ctx_browser, "runtime_ctx", None), "workdir", "") or ""
            if not workdir_raw:
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="search_files_no_workdir",
                    message="workdir is not configured.",
                    extra={"tool_id": tool_id},
                    rel="result",
                )
                state["last_tool_result"] = []
                return state
            root_dir = pathlib.Path(workdir_raw)
            root_kind = "workdir"
            normalized_root = "workdir"
            root_virtual_prefix = ""
        elif root_sel_lc.startswith("workdir/"):
            workdir_raw = getattr(getattr(ctx_browser, "runtime_ctx", None), "workdir", "") or ""
            if not workdir_raw:
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="search_files_no_workdir",
                    message="workdir is not configured.",
                    extra={"tool_id": tool_id},
                    rel="result",
                )
                state["last_tool_result"] = []
                return state
            rel = root_sel[len("workdir/"):].lstrip("/")
            if not rel or not _safe_relpath(rel):
                notice_block(
                    ctx_browser=ctx_browser,
                    tool_call_id=tool_call_id,
                    code="search_files_invalid_root",
                    message="invalid workdir root selector.",
                    extra={"tool_id": tool_id, "root": root_sel},
                    rel="result",
                )
                state["last_tool_result"] = []
                return state
            root_dir = pathlib.Path(workdir_raw) / rel
            root_kind = "workdir"
            normalized_root = f"workdir/{rel}"
            root_virtual_prefix = rel
        else:
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="search_files_invalid_root",
                message="invalid root selector. Use outdir, outdir/<subdir>, workdir, or workdir/<subdir>.",
                extra={"tool_id": tool_id, "root": root_sel},
                rel="result",
            )
            state["last_tool_result"] = []
            return state
    try:
        raw_hits = search_files(
            root=str(root_dir),
            name_regex=name_regex,
            content_regex=content_regex,
            max_bytes=max_bytes,
            max_hits=max_hits,
        )
    except Exception as exc:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="search_files_failed",
            message=f"search_files failed: {exc}",
            extra={"tool_id": tool_id},
            rel="result",
        )
        state["last_tool_result"] = []
        return state

    hits: List[Dict[str, Any]] = []
    for row in raw_hits:
        rel_path = str((row or {}).get("path") or "").strip()
        if not rel_path:
            continue
        hit: Dict[str, Any] = {
            "path": rel_path,
            "size_bytes": int((row or {}).get("size_bytes") or 0),
        }
        full_path = rel_path
        if root_virtual_prefix:
            full_path = pathlib.PurePosixPath(root_virtual_prefix, rel_path).as_posix()
        if root_kind == "outdir":
            logical_path = physical_path_to_logical_path(full_path)
            hit["logical_path"] = logical_path
        hits.append(hit)

    payload = {
        "root": normalized_root,
        "hits": hits,
    }
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    state["last_tool_result"] = hits
    return state

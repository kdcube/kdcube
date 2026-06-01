# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import json
import os
import pathlib
import re

from kdcube_ai_app.apps.chat.sdk.util import count_text_lines, count_text_symbols, guess_mime_type
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    add_block,
    notice_block,
    tc_result_path,
    tool_call_block,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import _safe_relpath
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_ATTACHMENTS,
    ARTIFACT_NAMESPACE_FILES,
    ARTIFACT_NAMESPACE_OUTPUTS,
    ARTIFACT_NAMESPACE_SNAPSHOTS,
    build_physical_artifact_path,
    physical_path_to_logical_path,
    split_logical_artifact_ref,
    split_physical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for
from kdcube_ai_app.tools.content_type import is_text_mime_type


MAX_SCANNED_FILES = 2000


TOOL_SPEC = {
    "id": "react.rg",
    "purpose": (
        "Ripgrep-like safe search over files already materialized in the visible artifact workspace. "
        "Use it to discover files by name and/or locate text regions by regex before reading exact ranges. "
        "It does not search unmaterialized conversation history, hidden timeline memory, or knowledge space; "
        "pull/check out older files first when local search is needed. "
        "It does not load full file content into visible context. "
        "Results include file metadata, line counts for text files, line-numbered matches, and ready-to-pass "
        "`read_item` ranges for react.read."
    ),
    "args": {
        "root": (
            "optional root selector. Omit to search the full materialized artifact workspace. "
            "Use canonical turn_<id>/files/<path>, turn_<id>/outputs/<path>, "
            "turn_<id>/attachments/<path>, or supported fi:turn_<id>.* artifact paths "
            "to search a file or subtree. An fi:conv_<conversation_id>.turn_<id>... root belongs to another "
            "conversation and is resolved with that scope after the file has been pulled locally. "
            "Legacy outdir/<path> is accepted but should not be used in new calls."
        ),
        "name_regex": "optional Python regex matched against the basename only, not the full path",
        "pattern": (
            "optional Python regex matched against UTF-8-decoded text file lines. "
            "When set, hits include `matches` with line numbers, previews, and `read_item` ranges for react.read."
        ),
        "context_lines": "optional number of surrounding lines to include in each suggested read range; default 0",
        "max_matches": "optional maximum total content matches to return; default 200",
        "max_files": "optional maximum number of matching files to return; default 200",
        "max_bytes": "optional per-file scan byte cap. If reached, result explicitly reports scan_truncated=true.",
    },
    "returns": (
        "JSON object {root, hits}. Each hit has path (relative to searched root), size_bytes, optional "
        "text_symbols/line_count for text files, and logical_path when the hit is readable by react.read. "
        "Content hits include matches with line, preview, read_item, and scan_truncated metadata. "
        "Returned logical_path/read_item values are directly usable with react.read."
    ),
}


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _compile_regex(pattern: Any) -> Optional[re.Pattern[str]]:
    if not isinstance(pattern, str) or not pattern:
        return None
    return re.compile(pattern)


def _iter_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    if root.is_file():
        yield root
        return
    count = 0
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        dirnames.sort()
        filenames = sorted(f for f in filenames if not f.startswith("."))
        for fname in filenames:
            yield pathlib.Path(dirpath) / fname
            count += 1
            if count >= MAX_SCANNED_FILES:
                return


def _read_item_for_match(
    *,
    logical_path: str,
    line: int,
    context_lines: int,
) -> Optional[Dict[str, int | str]]:
    if not logical_path:
        return None
    line_start = max(1, line - max(0, context_lines))
    line_end = line + max(0, context_lines)
    return {
        "path": logical_path,
        "line_start": line_start,
        "line_count": max(1, line_end - line_start + 1),
    }


def _find_matches(
    *,
    path: pathlib.Path,
    pattern: re.Pattern[str],
    logical_path: str,
    context_lines: int,
    remaining_matches: int,
    max_bytes: Optional[int],
) -> Tuple[List[Dict[str, Any]], bool, int]:
    matches: List[Dict[str, Any]] = []
    scanned_bytes = 0
    scan_truncated = False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line_no, line in enumerate(fh, start=1):
                encoded_len = len(line.encode("utf-8", errors="ignore"))
                if max_bytes is not None and scanned_bytes + encoded_len > max_bytes:
                    scan_truncated = True
                    break
                scanned_bytes += encoded_len
                if not pattern.search(line):
                    continue
                preview = line.rstrip("\n\r")
                match: Dict[str, Any] = {
                    "line": line_no,
                    "preview": f"{line_no}: {preview}",
                    "context_lines": max(0, context_lines),
                }
                read_item = _read_item_for_match(
                    logical_path=logical_path,
                    line=line_no,
                    context_lines=context_lines,
                )
                if read_item:
                    match["read_item"] = read_item
                matches.append(match)
                if len(matches) >= remaining_matches:
                    break
    except Exception:
        return [], False, scanned_bytes
    return matches, scan_truncated, scanned_bytes


def _resolve_root(
    *,
    ctx_browser: Any,
    state: Dict[str, Any],
    tool_call_id: str,
    tool_id: str,
    root_sel: str,
) -> Optional[tuple[pathlib.Path, str, str, str]]:
    outdir = pathlib.Path(state["outdir"])
    root_kind = "outdir"
    normalized_root = "artifact_workspace"
    root_virtual_prefix = ""
    artifact_outdir = artifact_outdir_for(outdir, create=False)
    if not artifact_outdir.exists():
        artifact_outdir = outdir
    if not root_sel:
        return artifact_outdir, root_kind, normalized_root, root_virtual_prefix

    turn_id = str(getattr(getattr(ctx_browser, "runtime_ctx", None), "turn_id", "") or "").strip()
    root_sel_lc = root_sel.lower()
    rel = ""
    if root_sel_lc == "outdir":
        return artifact_outdir, root_kind, "outdir", root_virtual_prefix
    if root_sel_lc.startswith("outdir/"):
        # Backwards compatibility only.
        rel = root_sel[len("outdir/"):].lstrip("/")
    elif root_sel.startswith("fi:"):
        source_conversation_id, logical_turn, namespace, logical_rel = split_logical_artifact_ref(root_sel)
        if logical_turn and namespace in {
            ARTIFACT_NAMESPACE_FILES,
            ARTIFACT_NAMESPACE_OUTPUTS,
            ARTIFACT_NAMESPACE_SNAPSHOTS,
            ARTIFACT_NAMESPACE_ATTACHMENTS,
        } and logical_rel:
            rel = build_physical_artifact_path(
                turn_id=logical_turn,
                namespace=namespace,
                relpath=logical_rel,
                conversation_id=source_conversation_id,
            )
        else:
            code = "rg_invalid_root"
            message = (
                "invalid fi: root selector. Use a supported fi:turn_<id>.<namespace>/<path> "
                "artifact path such as files, outputs, or attachments."
            )
            rel = ""
    else:
        physical_turn, physical_namespace, physical_rel = split_physical_artifact_path(root_sel)
        if physical_turn and physical_namespace and physical_rel:
            rel = root_sel.lstrip("/")
        elif (
            root_sel.startswith(f"{ARTIFACT_NAMESPACE_FILES}/")
            or root_sel.startswith(f"{ARTIFACT_NAMESPACE_OUTPUTS}/")
            or root_sel.startswith(f"{ARTIFACT_NAMESPACE_SNAPSHOTS}/")
            or root_sel.startswith(f"{ARTIFACT_NAMESPACE_ATTACHMENTS}/")
        ):
            if not turn_id:
                code = "rg_invalid_root"
                message = "current-turn root selector requires a turn id."
                rel = ""
            else:
                namespace, namespace_rel = root_sel.split("/", 1)
                rel = build_physical_artifact_path(
                    turn_id=turn_id,
                    namespace=namespace,
                    relpath=namespace_rel,
                )
        else:
            code = "rg_invalid_root"
            message = (
                "invalid root selector. Omit root, or use canonical turn_<id>/files|outputs|attachments paths or a supported fi:turn_<id>.* artifact path."
            )

    if rel:
        if not _safe_relpath(rel):
            code = "rg_invalid_root"
            message = "invalid root selector."
        else:
            return artifact_outdir / rel, root_kind, root_sel, rel

    notice_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        code=code,
        message=message,
        extra={"tool_id": tool_id, "root": root_sel},
        rel="result",
    )
    return None


async def handle_react_rg(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.rg"
    params = tool_call.get("params") or {}
    root_sel = (params.get("root") or "").strip()
    context_lines = max(0, _positive_int(params.get("context_lines"), 0))
    max_matches = _positive_int(params.get("max_matches"), 200)
    max_files = _positive_int(params.get("max_files"), 200)
    max_bytes = _positive_int(params.get("max_bytes"), 0) or None

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

    resolved_root = _resolve_root(
        ctx_browser=ctx_browser,
        state=state,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        root_sel=root_sel,
    )
    if not resolved_root:
        state["last_tool_result"] = []
        return state
    root_dir, root_kind, normalized_root, root_virtual_prefix = resolved_root

    try:
        name_re = _compile_regex(params.get("name_regex"))
        content_re = _compile_regex(params.get("pattern"))
    except Exception as exc:
        notice_block(
            ctx_browser=ctx_browser,
            tool_call_id=tool_call_id,
            code="rg_invalid_regex",
            message=f"react.rg regex failed: {exc}",
            extra={"tool_id": tool_id},
            rel="result",
        )
        state["last_tool_result"] = []
        return state

    hits: List[Dict[str, Any]] = []
    read_items: List[Dict[str, Any]] = []
    scanned_files = 0
    scan_truncated = False
    matches_truncated = False
    files_truncated = False

    for abs_path in _iter_files(root_dir):
        scanned_files += 1
        try:
            rel_path = abs_path.name if root_dir.is_file() else abs_path.relative_to(root_dir).as_posix()
            size_bytes = int(abs_path.stat().st_size)
        except Exception:
            continue
        if name_re and not name_re.search(abs_path.name):
            continue
        mime = guess_mime_type(str(abs_path))
        is_text = is_text_mime_type(mime)

        full_path = rel_path
        if root_virtual_prefix and root_dir.is_file():
            full_path = root_virtual_prefix
        elif root_virtual_prefix:
            full_path = pathlib.PurePosixPath(root_virtual_prefix, rel_path).as_posix()
        logical_path = physical_path_to_logical_path(full_path) if root_kind == "outdir" else ""

        hit: Dict[str, Any] = {
            "path": rel_path,
            "size_bytes": size_bytes,
        }
        if logical_path:
            hit["logical_path"] = logical_path
        if is_text:
            text_symbols = count_text_symbols(abs_path)
            if text_symbols is not None:
                hit["text_symbols"] = int(text_symbols)
            line_count = count_text_lines(abs_path)
            if line_count is not None:
                hit["line_count"] = int(line_count)

        if content_re:
            if not is_text:
                continue
            remaining = max(0, max_matches - len(read_items))
            if remaining <= 0:
                matches_truncated = True
                break
            matches, file_scan_truncated, scanned_bytes = _find_matches(
                path=abs_path,
                pattern=content_re,
                logical_path=logical_path,
                context_lines=context_lines,
                remaining_matches=remaining,
                max_bytes=max_bytes,
            )
            hit["scanned_bytes"] = scanned_bytes
            if file_scan_truncated:
                hit["scan_truncated"] = True
                scan_truncated = True
            if not matches:
                continue
            hit["matches"] = matches
            for match in matches:
                read_item = match.get("read_item")
                if isinstance(read_item, dict):
                    read_items.append(read_item)

        hits.append(hit)
        if len(hits) >= max_files:
            files_truncated = True
            break

    payload: Dict[str, Any] = {
        "root": normalized_root,
        "hits": hits,
        "hit_count": len(hits),
        "scanned_files": scanned_files,
    }
    if content_re:
        payload["match_count"] = len(read_items)
        payload["read_items"] = read_items
        payload["context_lines"] = context_lines
    if max_bytes is not None:
        payload["max_bytes"] = max_bytes
    if scan_truncated:
        payload["scan_truncated"] = True
    if matches_truncated:
        payload["matches_truncated"] = True
    if files_truncated:
        payload["files_truncated"] = True

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

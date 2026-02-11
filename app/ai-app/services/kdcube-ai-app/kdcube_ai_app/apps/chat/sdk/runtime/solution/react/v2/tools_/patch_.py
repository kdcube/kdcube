# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import json
import os
import pathlib
import re
import time

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.tools.common import (
    tool_call_block,
    run_post_patch_check,
    notice_block,
    add_block,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.artifacts import build_artifact_meta_block

BEGIN_PATCH_MARKER = "*** Begin Patch"
END_PATCH_MARKER = "*** End Patch"
ADD_FILE_MARKER = "*** Add File: "
DELETE_FILE_MARKER = "*** Delete File: "
UPDATE_FILE_MARKER = "*** Update File: "
MOVE_TO_MARKER = "*** Move to: "
EOF_MARKER = "*** End of File"
CHANGE_CONTEXT_MARKER = "@@ "
EMPTY_CHANGE_CONTEXT_MARKER = "@@"
UNICODE_SPACES_RE = re.compile(r"[\u00A0\u2000-\u200A\u202F\u205F\u3000]")

DOC = {
    "id": "react.patch",
    "purpose": (
        "Apply a multi-file patch using the apply_patch format (*** Begin Patch/End Patch). "
        "Supports add, delete, update, and move hunks in a single patch. "
        "Paths inside the patch are resolved relative to OUT_DIR/<turn_id>/files/. "
        "Use this to modify multiple files at once or to add/delete/move files."
    ),
    "args": {
        "path": (
            "str (FIRST FIELD). Compatibility placeholder; ignored by react.apply_patch. "
            "Files are determined by the patch content itself."
        ),
        "channel": (
            "str (SECOND FIELD). Compatibility placeholder; ignored by react.apply_patch. "
            "Use 'canvas' or 'timeline_text' for UI consistency in logs."
        ),
        "patch": (
            "str (THIRD FIELD). The patch content. This SHOULD be in apply_patch format: "
            "*** Begin Patch, then one or more hunks using *** Add File / *** Delete File / *** Update File, "
            "optionally *** Move to, and ending with *** End Patch."
        ),
        "kind": (
            "str (FOURTH FIELD). Compatibility placeholder; ignored by react.apply_patch. "
            "Use 'display' or 'file' to match prior tooling conventions."
        ),
        "input": (
            "str (ALIAS). Preferred field for the patch content; if present it takes precedence over patch."
        ),
    },
    "returns": "summary of applied changes",
    "constraints": [
        "`path` must appear first in the params JSON object (compatibility).",
        "`channel` must appear second in the params JSON object (compatibility).",
        "`patch` must appear third in the params JSON object (or use `input`).",
        "`kind` must appear fourth in the params JSON object (compatibility).",
    ],
    "patch_format": {
        "start": "*** Begin Patch",
        "hunks": [
            "*** Add File: <path> (then lines starting with '+')",
            "*** Delete File: <path>",
            "*** Update File: <path> (optional *** Move to: <path>, then @@ context and +/-/ space lines)",
        ],
        "end": "*** End Patch",
    },
}




class PatchError(ValueError):
    pass


@dataclass
class AddFileHunk:
    path: str
    contents: str
    kind: str = "add"


@dataclass
class DeleteFileHunk:
    path: str
    kind: str = "delete"


@dataclass
class UpdateFileChunk:
    old_lines: List[str]
    new_lines: List[str]
    change_context: Optional[str] = None
    is_end_of_file: bool = False


@dataclass
class UpdateFileHunk:
    path: str
    chunks: List[UpdateFileChunk]
    move_path: Optional[str] = None
    kind: str = "update"


Hunk = Union[AddFileHunk, DeleteFileHunk, UpdateFileHunk]


@dataclass
class ApplyPatchSummary:
    added: List[str]
    modified: List[str]
    deleted: List[str]


@dataclass
class ApplyPatchResult:
    summary: ApplyPatchSummary
    text: str


def _is_safe_relpath(path_value: str) -> bool:
    try:
        p = pathlib.PurePosixPath(path_value)
        if path_value.startswith(("/", "\\")):
            return False
        if any(part == ".." for part in p.parts):
            return False
        return True
    except Exception:
        return False


def _normalize_unicode_spaces(value: str) -> str:
    return UNICODE_SPACES_RE.sub(" ", value)


def _expand_path(file_path: str) -> str:
    normalized = _normalize_unicode_spaces(file_path)
    if normalized == "~":
        return os.path.expanduser(normalized)
    if normalized.startswith("~/"):
        return os.path.expanduser(normalized)
    return normalized


def _resolve_patch_path(file_path: str, base_dir: pathlib.Path) -> Tuple[pathlib.Path, str]:
    expanded = _expand_path(file_path)
    if os.path.isabs(expanded):
        raise PatchError("Absolute paths are not allowed in apply_patch.")
    if not _is_safe_relpath(expanded):
        raise PatchError("Unsafe path in apply_patch.")
    base_resolved = base_dir.resolve()
    resolved = (base_dir / expanded).resolve()
    if str(resolved) != str(base_resolved) and not str(resolved).startswith(
        f"{base_resolved}{os.sep}"):
        raise PatchError("Path escapes base directory.")
    display = os.path.relpath(resolved, base_resolved)
    return resolved, display


def _ensure_dir(file_path: pathlib.Path) -> None:
    parent = file_path.parent
    if not parent or str(parent) == ".":
        return
    parent.mkdir(parents=True, exist_ok=True)


def _record_summary(summary: ApplyPatchSummary, seen: Dict[str, set], bucket: str, value: str) -> None:
    if value in seen[bucket]:
        return
    seen[bucket].add(value)
    getattr(summary, bucket).append(value)


def _format_summary(summary: ApplyPatchSummary) -> str:
    lines = ["Success. Updated the following files:"]
    for file_path in summary.added:
        lines.append(f"A {file_path}")
    for file_path in summary.modified:
        lines.append(f"M {file_path}")
    for file_path in summary.deleted:
        lines.append(f"D {file_path}")
    return "\n".join(lines)


def apply_patch(input_text: str, base_dir: pathlib.Path) -> ApplyPatchResult:
    hunks = _parse_patch_text(input_text)
    if not hunks:
        raise PatchError("No files were modified.")

    base_dir.mkdir(parents=True, exist_ok=True)

    summary = ApplyPatchSummary(added=[], modified=[], deleted=[])
    seen = {
        "added": set(),
        "modified": set(),
        "deleted": set(),
    }

    for hunk in hunks:
        if isinstance(hunk, AddFileHunk):
            target, display = _resolve_patch_path(hunk.path, base_dir)
            _ensure_dir(target)
            target.write_text(hunk.contents, encoding="utf-8")
            _record_summary(summary, seen, "added", display)
            continue

        if isinstance(hunk, DeleteFileHunk):
            target, display = _resolve_patch_path(hunk.path, base_dir)
            if not target.exists():
                raise PatchError(f"Delete failed: file not found {display}")
            target.unlink()
            _record_summary(summary, seen, "deleted", display)
            continue

        target, display = _resolve_patch_path(hunk.path, base_dir)
        applied = _apply_update_hunk(target, hunk.chunks)
        if hunk.move_path:
            move_target, move_display = _resolve_patch_path(hunk.move_path, base_dir)
            _ensure_dir(move_target)
            move_target.write_text(applied, encoding="utf-8")
            if target.exists():
                target.unlink()
            _record_summary(summary, seen, "modified", move_display)
        else:
            _ensure_dir(target)
            target.write_text(applied, encoding="utf-8")
            _record_summary(summary, seen, "modified", display)

    return ApplyPatchResult(summary=summary, text=_format_summary(summary))


def _parse_patch_text(input_text: str) -> List[Hunk]:
    trimmed = input_text.strip()
    if not trimmed:
        raise PatchError("Invalid patch: input is empty.")

    lines = trimmed.splitlines()
    validated = _check_patch_boundaries_lenient(lines)
    if len(validated) < 2:
        raise PatchError("Invalid patch: missing content.")

    hunks: List[Hunk] = []
    last_line_index = len(validated) - 1
    remaining = validated[1:last_line_index]
    line_number = 2

    while remaining:
        hunk, consumed = _parse_one_hunk(remaining, line_number)
        hunks.append(hunk)
        line_number += consumed
        remaining = remaining[consumed:]

    return hunks


def _check_patch_boundaries_lenient(lines: List[str]) -> List[str]:
    strict_error = _check_patch_boundaries_strict(lines)
    if not strict_error:
        return lines

    if len(lines) < 4:
        raise PatchError(strict_error)

    first = lines[0]
    last = lines[-1]
    if first in ("<<EOF", "<<'EOF'", '<<"EOF"') and last.endswith("EOF"):
        inner = lines[1:-1]
        inner_error = _check_patch_boundaries_strict(inner)
        if not inner_error:
            return inner
        raise PatchError(inner_error)

    raise PatchError(strict_error)


def _check_patch_boundaries_strict(lines: List[str]) -> Optional[str]:
    first_line = lines[0].strip() if lines else ""
    last_line = lines[-1].strip() if lines else ""
    if first_line == BEGIN_PATCH_MARKER and last_line == END_PATCH_MARKER:
        return None
    if first_line != BEGIN_PATCH_MARKER:
        return "The first line of the patch must be '*** Begin Patch'"
    return "The last line of the patch must be '*** End Patch'"


def _parse_one_hunk(lines: List[str], line_number: int) -> Tuple[Hunk, int]:
    if not lines:
        raise PatchError(f"Invalid patch hunk at line {line_number}: empty hunk")

    first_line = lines[0].strip()
    if first_line.startswith(ADD_FILE_MARKER):
        target_path = first_line[len(ADD_FILE_MARKER):]
        contents = ""
        consumed = 1
        for add_line in lines[1:]:
            if add_line.startswith("+"):
                contents += f"{add_line[1:]}\n"
                consumed += 1
            else:
                break
        return AddFileHunk(path=target_path, contents=contents), consumed

    if first_line.startswith(DELETE_FILE_MARKER):
        target_path = first_line[len(DELETE_FILE_MARKER):]
        return DeleteFileHunk(path=target_path), 1

    if first_line.startswith(UPDATE_FILE_MARKER):
        target_path = first_line[len(UPDATE_FILE_MARKER):]
        remaining = lines[1:]
        consumed = 1
        move_path = None

        move_candidate = remaining[0].strip() if remaining else ""
        if move_candidate.startswith(MOVE_TO_MARKER):
            move_path = move_candidate[len(MOVE_TO_MARKER):]
            remaining = remaining[1:]
            consumed += 1

        chunks: List[UpdateFileChunk] = []
        while remaining:
            if remaining[0].strip() == "":
                remaining = remaining[1:]
                consumed += 1
                continue
            if remaining[0].startswith("***"):
                break
            chunk, chunk_lines = _parse_update_file_chunk(remaining, line_number + consumed, not chunks)
            chunks.append(chunk)
            remaining = remaining[chunk_lines:]
            consumed += chunk_lines

        if not chunks:
            raise PatchError(
                f"Invalid patch hunk at line {line_number}: Update file hunk for path '{target_path}' is empty"
            )

        return UpdateFileHunk(path=target_path, move_path=move_path, chunks=chunks), consumed

    raise PatchError(
        f"Invalid patch hunk at line {line_number}: '{lines[0]}' is not a valid hunk header. "
        "Valid hunk headers: '*** Add File: {path}', '*** Delete File: {path}', '*** Update File: {path}'"
    )


def _parse_update_file_chunk(
    lines: List[str],
    line_number: int,
    allow_missing_context: bool,
) -> Tuple[UpdateFileChunk, int]:
    if not lines:
        raise PatchError(
            f"Invalid patch hunk at line {line_number}: Update hunk does not contain any lines"
        )

    change_context: Optional[str] = None
    start_index = 0
    if lines[0] == EMPTY_CHANGE_CONTEXT_MARKER:
        start_index = 1
    elif lines[0].startswith(CHANGE_CONTEXT_MARKER):
        change_context = lines[0][len(CHANGE_CONTEXT_MARKER):]
        start_index = 1
    elif not allow_missing_context:
        raise PatchError(
            f"Invalid patch hunk at line {line_number}: Expected update hunk to start with a @@ context marker, "
            f"got: '{lines[0]}'"
        )

    if start_index >= len(lines):
        raise PatchError(
            f"Invalid patch hunk at line {line_number + 1}: Update hunk does not contain any lines"
        )

    chunk = UpdateFileChunk(change_context=change_context, old_lines=[], new_lines=[], is_end_of_file=False)
    parsed_lines = 0

    for line in lines[start_index:]:
        if line == EOF_MARKER:
            if parsed_lines == 0:
                raise PatchError(
                    f"Invalid patch hunk at line {line_number + 1}: Update hunk does not contain any lines"
                )
            chunk.is_end_of_file = True
            parsed_lines += 1
            break

        if line == "":
            chunk.old_lines.append("")
            chunk.new_lines.append("")
            parsed_lines += 1
            continue

        marker = line[0]
        if marker == " ":
            content = line[1:]
            chunk.old_lines.append(content)
            chunk.new_lines.append(content)
            parsed_lines += 1
            continue
        if marker == "+":
            chunk.new_lines.append(line[1:])
            parsed_lines += 1
            continue
        if marker == "-":
            chunk.old_lines.append(line[1:])
            parsed_lines += 1
            continue

        if parsed_lines == 0:
            raise PatchError(
                f"Invalid patch hunk at line {line_number + 1}: Unexpected line found in update hunk: '{line}'. "
                "Every line should start with ' ' (context line), '+' (added line), or '-' (removed line)"
            )
        break

    return chunk, parsed_lines + start_index


def _apply_update_hunk(file_path: pathlib.Path, chunks: List[UpdateFileChunk]) -> str:
    try:
        original_contents = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise PatchError(f"Failed to read file to update {file_path}: {exc}")

    original_lines = original_contents.split("\n")
    if original_lines and original_lines[-1] == "":
        original_lines = original_lines[:-1]

    replacements = _compute_replacements(original_lines, str(file_path), chunks)
    new_lines = _apply_replacements(original_lines, replacements)
    if not new_lines or new_lines[-1] != "":
        new_lines = [*new_lines, ""]
    return "\n".join(new_lines)


def _compute_replacements(
    original_lines: List[str],
    file_path: str,
    chunks: List[UpdateFileChunk],
) -> List[Tuple[int, int, List[str]]]:
    replacements: List[Tuple[int, int, List[str]]] = []
    line_index = 0

    for chunk in chunks:
        if chunk.change_context is not None:
            ctx_index = _seek_sequence(original_lines, [chunk.change_context], line_index, False)
            if ctx_index is None:
                raise PatchError(f"Failed to find context '{chunk.change_context}' in {file_path}")
            line_index = ctx_index + 1

        if not chunk.old_lines:
            insertion_index = len(original_lines)
            if original_lines and original_lines[-1] == "":
                insertion_index = len(original_lines) - 1
            replacements.append((insertion_index, 0, chunk.new_lines))
            continue

        pattern = list(chunk.old_lines)
        new_slice = list(chunk.new_lines)
        found = _seek_sequence(original_lines, pattern, line_index, chunk.is_end_of_file)

        if found is None and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = _seek_sequence(original_lines, pattern, line_index, chunk.is_end_of_file)

        if found is None:
            raise PatchError(
                f"Failed to find expected lines in {file_path}:\n" + "\n".join(chunk.old_lines)
            )

        replacements.append((found, len(pattern), new_slice))
        line_index = found + len(pattern)

    replacements.sort(key=lambda item: item[0])
    return replacements


def _apply_replacements(
    lines: List[str],
    replacements: List[Tuple[int, int, List[str]]],
) -> List[str]:
    result = list(lines)
    for start_index, old_len, new_lines in reversed(replacements):
        for _ in range(old_len):
            if start_index < len(result):
                result.pop(start_index)
        for i, new_line in enumerate(new_lines):
            result.insert(start_index + i, new_line)
    return result


def _seek_sequence(lines: List[str], pattern: List[str], start: int, eof: bool) -> Optional[int]:
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None

    max_start = len(lines) - len(pattern)
    search_start = max_start if eof and len(lines) >= len(pattern) else start
    if search_start > max_start:
        return None

    for i in range(search_start, max_start + 1):
        if _lines_match(lines, pattern, i, lambda value: value):
            return i
    for i in range(search_start, max_start + 1):
        if _lines_match(lines, pattern, i, lambda value: value.rstrip()):
            return i
    for i in range(search_start, max_start + 1):
        if _lines_match(lines, pattern, i, lambda value: value.strip()):
            return i
    for i in range(search_start, max_start + 1):
        if _lines_match(lines, pattern, i, lambda value: _normalize_punctuation(value.strip())):
            return i

    return None


def _lines_match(
    lines: List[str],
    pattern: List[str],
    start: int,
    normalize,
) -> bool:
    for idx in range(len(pattern)):
        if normalize(lines[start + idx]) != normalize(pattern[idx]):
            return False
    return True


def _normalize_punctuation(value: str) -> str:
    out_chars: List[str] = []
    for char in value:
        if char in {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"}:
            out_chars.append("-")
        elif char in {"\u2018", "\u2019", "\u201A", "\u201B"}:
            out_chars.append("'")
        elif char in {"\u201C", "\u201D", "\u201E", "\u201F"}:
            out_chars.append('"')
        elif char in {
            "\u00A0",
            "\u2002",
            "\u2003",
            "\u2004",
            "\u2005",
            "\u2006",
            "\u2007",
            "\u2008",
            "\u2009",
            "\u200A",
            "\u202F",
            "\u205F",
            "\u3000",
        }:
            out_chars.append(" ")
        else:
            out_chars.append(char)
    return "".join(out_chars)


async def handle_react_apply_patch(*, react: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    tool_call = (state.get("last_decision") or {}).get("tool_call") or {}
    tool_id = "react.apply_patch"
    params = tool_call.get("params") or {}
    patch_text = params.get("input") or params.get("patch")

    if not isinstance(patch_text, str) or not patch_text.strip():
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": "missing_patch", "managed": True}
        return state

    turn_id = (react.scratchpad.turn_id if react.scratchpad else "") or ""
    tool_call_block(
        react=react,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "reasoning": tool_call.get("reasoning") or "",
            "params": tool_call.get("params") or {},
        },
    )

    outdir = pathlib.Path(state["outdir"])  # expected base output dir
    base_dir = outdir / turn_id / "files" if turn_id else outdir / "files"

    try:
        result = apply_patch(patch_text, base_dir)
    except PatchError as exc:
        state["exit_reason"] = "error"
        state["error"] = {"where": "tool_execution", "error": str(exc), "managed": True}
        return state

    notes: List[Dict[str, str]] = []
    changed_files: List[str] = [*result.summary.added, *result.summary.modified, *result.summary.deleted]
    for rel_path in [*result.summary.added, *result.summary.modified]:
        target_path = base_dir / rel_path
        ok, msg = run_post_patch_check(target_path)
        if not ok:
            notes.append({"path": str(target_path), "detail": msg})

    if notes:
        notice_block(
            react=react,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            code="post_patch_check_failed",
            message="post-patch check failed",
            extra={"notes": notes, "tool_id": tool_id},
        )

    # Emit meta blocks for changed files (internal visibility)
    for rel_path in changed_files:
        if not rel_path:
            continue
        artifact_path = f"fi:{turn_id}.files.{rel_path}" if turn_id else ""
        physical_path = f"{turn_id}/files/{rel_path}" if turn_id else ""
        meta_block = build_artifact_meta_block(
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            artifact={
                "artifact_kind": "file",
                "visibility": "internal",
                "tool_id": tool_id,
                "tool_call_id": tool_call_id,
                "value": {"path": rel_path, "mime": None},
            },
            artifact_path=artifact_path,
            physical_path=physical_path,
        )
        add_block(react, meta_block)

    add_block(react, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "text/markdown",
        "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.out.summary.txt" if turn_id else "",
        "text": result.text,
    })
    add_block(react, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.out.json" if turn_id else "",
        "text": json.dumps({
            "summary": {
                "added": result.summary.added,
                "modified": result.summary.modified,
                "deleted": result.summary.deleted,
            }
        }, ensure_ascii=False),
    })
    state["last_tool_result"] = []
    return state

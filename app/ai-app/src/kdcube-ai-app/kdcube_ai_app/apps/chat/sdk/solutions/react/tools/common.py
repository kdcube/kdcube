# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import time
import shutil
import subprocess
import tempfile
import logging
import os
import hashlib
import re
from typing import Any, Dict, Optional, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.util import count_text_lines, count_text_symbols, guess_mime_type
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
from kdcube_ai_app.tools.content_type import is_text_mime_type

_LOG = logging.getLogger("kdcube.react.artifacts")
_TOOL_LOG_DEFAULT_MAX_CHARS = 120_000
_TOOL_CALL_INLINE_STRING_LIMIT = 2_000
_TOOL_CALL_STRING_PREVIEW_CHARS = 600
_TOOL_CALL_MAX_DICT_KEYS = 80
_TOOL_CALL_MAX_LIST_ITEMS = 50


def _tool_log_max_chars() -> int:
    try:
        return max(4_000, int(os.getenv("KDCUBE_REACT_TOOL_LOG_MAX_CHARS", str(_TOOL_LOG_DEFAULT_MAX_CHARS))))
    except Exception:
        return _TOOL_LOG_DEFAULT_MAX_CHARS


def _clip_log_text(text: str, *, max_chars: Optional[int] = None) -> str:
    max_len = max_chars or _tool_log_max_chars()
    if len(text) <= max_len:
        return text
    omitted = len(text) - max_len
    return f"{text[:max_len]}\n[TRUNCATED by react tool logger: omitted {omitted} chars]"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return "<unserializable>"


def _hash_text(value: str) -> str:
    try:
        return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    except Exception:
        return ""


def _tool_call_omission_marker(*, value: str, key_path: str, call_path: str) -> Dict[str, Any]:
    preview = value[:_TOOL_CALL_STRING_PREVIEW_CHARS]
    if call_path:
        recover_with = (
            f"react.read(paths=[{json.dumps(call_path)}]) to load the saved full tool-call payload; "
            f"then inspect field {key_path!r}. Use stats_only/ranged read items only if that artifact is still capped."
        )
    else:
        recover_with = (
            "react.read on the matching tc:<turn>.<call>.call path to load the saved full tool-call payload; "
            f"then inspect field {key_path!r}. Use stats_only/ranged read items only if that artifact is still capped."
        )
    return {
        "preview": preview,
        "truncated": True,
        "omitted_text_symbols": max(0, len(value) - len(preview)),
        "text_symbols": len(value),
        "size_bytes": len(value.encode("utf-8", errors="ignore")),
        "sha1": _hash_text(value),
        "full_value_ref": call_path or "tc:<turn>.<call>.call",
        "full_value_field": key_path,
        "recovery_hint": "This is only a shortened preview. The saved tool-call artifact preserves the complete value.",
        "recover_with": recover_with,
        "field": key_path,
    }


def _preview_tool_call_value(value: Any, *, key_path: str, call_path: str) -> tuple[Any, bool, List[Dict[str, Any]]]:
    if isinstance(value, str):
        if len(value) <= _TOOL_CALL_INLINE_STRING_LIMIT:
            return value, False, []
        marker = _tool_call_omission_marker(value=value, key_path=key_path, call_path=call_path)
        return marker, True, [marker]

    if isinstance(value, dict):
        changed = False
        omitted: List[Dict[str, Any]] = []
        out: Dict[str, Any] = {}
        items = list(value.items())
        for idx, (k, v) in enumerate(items):
            if idx >= _TOOL_CALL_MAX_DICT_KEYS:
                changed = True
                out["__omitted_keys__"] = len(items) - idx
                break
            child_path = f"{key_path}.{k}" if key_path else str(k)
            child, child_changed, child_omitted = _preview_tool_call_value(v, key_path=child_path, call_path=call_path)
            out[k] = child
            changed = changed or child_changed
            omitted.extend(child_omitted)
        return out, changed, omitted

    if isinstance(value, list):
        changed = False
        omitted: List[Dict[str, Any]] = []
        out: List[Any] = []
        for idx, item in enumerate(value):
            if idx >= _TOOL_CALL_MAX_LIST_ITEMS:
                changed = True
                out.append({"__omitted_items__": len(value) - idx})
                break
            child_path = f"{key_path}[{idx}]"
            child, child_changed, child_omitted = _preview_tool_call_value(item, key_path=child_path, call_path=call_path)
            out.append(child)
            changed = changed or child_changed
            omitted.extend(child_omitted)
        return out, changed, omitted

    return value, False, []


def _preview_tool_call_payload(payload: Dict[str, Any], *, call_path: str) -> tuple[Dict[str, Any], bool, List[Dict[str, Any]]]:
    preview, changed, omitted = _preview_tool_call_value(payload, key_path="", call_path=call_path)
    if not isinstance(preview, dict):
        return payload, False, []
    if changed:
        preview = dict(preview)
        preview["tool_call_preview_capped"] = True
        preview["tool_call_payload_capped"] = True
        preview["full_payload_preserved"] = True
        preview["omitted_fields"] = [
            {
                "field": row.get("field"),
                "text_symbols": row.get("text_symbols"),
                "size_bytes": row.get("size_bytes"),
                "sha1": row.get("sha1"),
            }
            for row in omitted
            if isinstance(row, dict)
        ]
    return preview, changed, omitted


def enrich_artifact_file_metadata(
        *,
        artifact: Dict[str, Any],
        outdir: pathlib.Path,
        physical_path: Optional[str] = None,
        mime: Optional[str] = None,
) -> None:
    if not isinstance(artifact, dict):
        return
    value = artifact.get("value")
    if not isinstance(value, dict):
        value = {}
        artifact["value"] = value

    candidate = (
        physical_path
        or value.get("physical_path")
        or value.get("local_path")
        or value.get("path")
        or artifact.get("physical_path")
        or artifact.get("local_path")
        or artifact.get("path")
        or ""
    )
    abs_path: Optional[pathlib.Path] = None
    if isinstance(candidate, str) and candidate.strip():
        p = pathlib.Path(candidate.strip())
        abs_path = p if p.is_absolute() else resolve_artifact_path(outdir, candidate.strip())

    text_value = value.get("text") if isinstance(value.get("text"), str) else value.get("content")
    if value.get("size_bytes") is None:
        if abs_path and abs_path.exists() and abs_path.is_file():
            try:
                value["size_bytes"] = abs_path.stat().st_size
            except Exception:
                pass
        elif isinstance(text_value, str):
            value["size_bytes"] = len(text_value.encode("utf-8", errors="ignore"))

    mime_value = str(mime or value.get("mime") or artifact.get("mime") or "").strip()
    if not mime_value and abs_path:
        mime_value = guess_mime_type(str(abs_path))
    if value.get("text_symbols") is None and is_text_mime_type(mime_value):
        text_symbols = count_text_symbols(abs_path) if abs_path else None
        if text_symbols is None and isinstance(text_value, str):
            text_symbols = len(text_value)
        if text_symbols is not None:
            value["text_symbols"] = int(text_symbols)
    if value.get("line_count") is None and is_text_mime_type(mime_value):
        line_count = count_text_lines(abs_path) if abs_path else None
        if line_count is None and isinstance(text_value, str):
            line_count = len(text_value.splitlines())
        if line_count is not None:
            value["line_count"] = int(line_count)


def _turn_from_path(path: str) -> str:
    if not path:
        return ""
    if path.startswith("tc:"):
        rest = path[3:]
        return rest.split(".", 1)[0]
    if path.startswith("fi:"):
        rest = path[3:]
        return rest.split("/", 1)[0]
    return ""


def _tool_block_log_body(block: Dict[str, Any]) -> str:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return _clip_log_text(text)

    payload: Dict[str, Any] = {}
    for key in ("path", "mime", "ts", "meta"):
        value = block.get(key)
        if value not in (None, "", {}, []):
            payload[key] = value
    if "base64" in block:
        try:
            payload["base64"] = f"<omitted {len(str(block.get('base64') or ''))} chars>"
        except Exception:
            payload["base64"] = "<omitted>"
    return _clip_log_text(_safe_json(payload))


def _log_tool_block(block: Dict[str, Any]) -> None:
    try:
        btype = (block.get("type") or "").strip()
        if btype not in {"react.tool.call", "react.tool.result"}:
            return
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
        path = (block.get("path") or "").strip()
        turn_id = str(block.get("turn") or block.get("turn_id") or _turn_from_path(path) or "").strip()
        call_id = str(block.get("call_id") or meta.get("tool_call_id") or "").strip()
        tool_id = str(block.get("tool_id") or meta.get("tool_id") or "").strip()
        marker = "[react.tool.call]" if btype == "react.tool.call" else "[react.tool.result]"
        _LOG.info(
            "%s turn_id=%s call_id=%s tool_id=%s path=%s mime=%s\n%s",
            marker,
            turn_id,
            call_id,
            tool_id,
            path,
            block.get("mime") or "",
            _tool_block_log_body(block),
        )
    except Exception:
        pass


def add_block(ctx_browser, block: Dict[str, Any]) -> None:
    try:
        runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
        iteration = getattr(runtime_ctx, "_current_react_iteration", None) if runtime_ctx is not None else None
        if iteration is not None and (block.get("type") or "").strip() in {
            "react.tool.call",
            "react.tool.result",
            "react.tool.code",
            "react.notice",
        }:
            meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
            if "iteration" not in meta:
                block = dict(block)
                meta = dict(meta)
                meta["iteration"] = int(iteration)
                block["meta"] = meta
    except Exception:
        pass
    _log_tool_block(block)
    try:
        ctx_browser.contribute(blocks=[block])
    except Exception:
        pass


def tc_call_path(*, turn_id: str, call_id: str) -> str:
    if not turn_id or not call_id:
        return ""
    return f"tc:{turn_id}.{call_id}.call"


def tc_result_path(*, turn_id: str, call_id: str) -> str:
    if not turn_id or not call_id:
        return ""
    return f"tc:{turn_id}.{call_id}.result"


def tool_call_block(*, ctx_browser, tool_call_id: str, tool_id: str, payload: Dict[str, Any]) -> None:
    turn_id = (getattr(ctx_browser.runtime_ctx, "turn_id", "") or "")
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = dict(payload or {})
    payload.pop("notes", None)
    payload.setdefault("ts", ts)
    call_path = tc_call_path(turn_id=turn_id, call_id=tool_call_id)
    display_payload, capped, omitted = _preview_tool_call_payload(payload, call_path=call_path)
    meta = {
        "tool_call_id": tool_call_id,
    }
    if capped:
        meta["tool_call_preview_capped"] = True
        meta["tool_call_payload_capped"] = True
        meta["full_payload_preserved"] = True
        meta["omitted_fields"] = [
            {
                "field": row.get("field"),
                "text_symbols": row.get("text_symbols"),
                "size_bytes": row.get("size_bytes"),
                "sha1": row.get("sha1"),
            }
            for row in omitted
            if isinstance(row, dict)
        ]
    block = {
        "type": "react.tool.call",
        "call_id": tool_call_id,
        "tool_id": tool_id,
        "mime": "application/json",
        "path": call_path,
        "text": json.dumps(display_payload, ensure_ascii=False, indent=2),
        "ts": ts,
        "meta": meta,
    }
    if capped:
        block["payload"] = payload
    add_block(ctx_browser, {
        **block,
    })


def notice_block(
    *,
    ctx_browser: Any,
    tool_call_id: str,
    code: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
    rel: Optional[str] = None,
) -> None:
    payload = {"code": code, "message": message}
    if extra:
        payload.update(extra)
    try:
        ctx_browser.contribute_notice(
            code=code,
            message=message,
            extra=extra,
            call_id=tool_call_id,
            meta={"rel": rel} if rel else None,
        )
        return
    except Exception:
        pass
    turn_id = (getattr(ctx_browser.runtime_ctx, "turn_id", "") or "")
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.notice",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": f"tc:{turn_id}.{tool_call_id}.notice" if turn_id else "",
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
    })


_UNIFIED_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@(.*)$")


def normalize_unified_diff_hunk_counts(patch_text: str) -> str:
    """
    Recompute unified diff hunk line counts.

    LLMs often produce a semantically correct hunk with an incorrect
    ``@@ -a,b +c,d @@`` count. The system patch binary rejects that as
    malformed even when the context and edits are otherwise valid.
    """
    lines = patch_text.splitlines(keepends=True)
    out: List[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body):]
        match = _UNIFIED_HUNK_RE.match(line_body)
        if not match:
            out.append(line)
            idx += 1
            continue

        old_start = int(match.group(1))
        new_start = int(match.group(2))
        section = match.group(3) or ""
        hunk_lines: List[str] = []
        idx += 1
        while idx < len(lines):
            candidate = lines[idx]
            candidate_body = candidate.rstrip("\r\n")
            if _UNIFIED_HUNK_RE.match(candidate_body):
                break
            hunk_lines.append(candidate)
            idx += 1

        old_count = 0
        new_count = 0
        for hunk_line in hunk_lines:
            if hunk_line.startswith("\\"):
                continue
            if hunk_line.startswith("+"):
                new_count += 1
            elif hunk_line.startswith("-"):
                old_count += 1
            else:
                old_count += 1
                new_count += 1

        hunk_newline = newline or "\n"
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{section}{hunk_newline}")
        out.extend(hunk_lines)
    return "".join(out)


def apply_unified_diff(text: str, patch_text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Apply a unified diff to text. Returns (new_text, error_message).
    """
    try:
        orig = text.splitlines(keepends=True)
        diff = patch_text.splitlines(keepends=True)
        out: List[str] = []
        i = 0
        idx = 0
        while idx < len(diff):
            line = diff[idx]
            if line.startswith(("---", "+++")):
                idx += 1
                continue
            if line.startswith("@@"):
                try:
                    header = line
                    parts = header.split()
                    old = parts[1]
                    old_start = int(old.split(",")[0].lstrip("-"))
                except Exception:
                    return None, "invalid_hunk_header"
                target = max(old_start - 1, 0)
                if target < i:
                    return None, "hunk_out_of_order"
                out.extend(orig[i:target])
                i = target
                idx += 1
                while idx < len(diff):
                    dline = diff[idx]
                    if dline.startswith("@@"):
                        break
                    if dline.startswith("-"):
                        if i >= len(orig) or orig[i] != dline[1:]:
                            return None, "hunk_mismatch"
                        i += 1
                    elif dline.startswith("+"):
                        out.append(dline[1:])
                    else:
                        if i >= len(orig) or orig[i] != dline[1:]:
                            return None, "hunk_mismatch"
                        out.append(orig[i])
                        i += 1
                    idx += 1
                continue
            idx += 1
        out.extend(orig[i:])
        return "".join(out), None
    except Exception as exc:
        return None, f"apply_failed:{exc}"


def rewrite_unified_diff_paths(
    *,
    patch_text: str,
    source_path: pathlib.Path,
    target_path: pathlib.Path,
) -> str:
    stripped = patch_text.lstrip()
    if not stripped:
        return patch_text

    prefix = patch_text[:len(patch_text) - len(stripped)]
    source_text = str(source_path)
    target_text = str(target_path)
    lines = stripped.splitlines(keepends=True)

    if stripped.startswith("@@"):
        header = f"--- {source_text}\n+++ {target_text}\n"
        body = stripped if stripped.endswith("\n") else f"{stripped}\n"
        return prefix + header + body

    out: List[str] = []
    saw_old = False
    saw_new = False
    for line in lines:
        if line.startswith("--- ") and not saw_old:
            ending = "\n" if line.endswith("\n") else ""
            out.append(f"--- {source_text}{ending}")
            saw_old = True
            continue
        if line.startswith("+++ ") and saw_old and not saw_new:
            ending = "\n" if line.endswith("\n") else ""
            out.append(f"+++ {target_text}{ending}")
            saw_new = True
            continue
        out.append(line)

    if not saw_old:
        out.insert(0, f"--- {source_text}\n")
    if not saw_new:
        insert_at = 1 if out and out[0].startswith("--- ") else 0
        out.insert(insert_at, f"+++ {target_text}\n")

    rewritten = prefix + "".join(out)
    if rewritten and not rewritten.endswith("\n"):
        rewritten += "\n"
    return rewritten


def apply_unified_diff_to_file(
    *,
    target_path: pathlib.Path,
    patch_text: str,
    source_path: Optional[pathlib.Path] = None,
) -> tuple[Optional[str], str, Optional[str]]:
    rewritten_patch = rewrite_unified_diff_paths(
        patch_text=patch_text,
        source_path=source_path or target_path,
        target_path=target_path,
    )
    rewritten_patch = normalize_unified_diff_hunk_counts(rewritten_patch)

    patch_bin = shutil.which("patch")
    if patch_bin:
        try:
            with tempfile.TemporaryDirectory(prefix="react_patch_") as tmpdir:
                tmpdir_path = pathlib.Path(tmpdir)
                candidate_path = tmpdir_path / target_path.name
                patch_path = tmpdir_path / "patch.diff"
                candidate_path.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")
                patch_path.write_text(rewritten_patch, encoding="utf-8")
                proc = subprocess.run(
                    [
                        patch_bin,
                        "--quiet",
                        "--forward",
                        "--reject-file=-",
                        "--fuzz=3",
                        "-l",
                        str(candidate_path),
                        str(patch_path),
                    ],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode == 0:
                    return candidate_path.read_text(encoding="utf-8"), rewritten_patch, None
                msg = (proc.stderr or proc.stdout or "").strip()
                try:
                    original = target_path.read_text(encoding="utf-8")
                    patched, err = apply_unified_diff(original, rewritten_patch)
                    if patched is not None:
                        return patched, rewritten_patch, None
                    return None, rewritten_patch, err or msg or f"patch_failed:{proc.returncode}"
                except Exception:
                    return None, rewritten_patch, msg or f"patch_failed:{proc.returncode}"
        except Exception as exc:
            return None, rewritten_patch, f"patch_exec_failed:{exc}"

    try:
        original = target_path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, rewritten_patch, f"patch_target_unreadable:{exc}"
    patched, err = apply_unified_diff(original, rewritten_patch)
    return patched, rewritten_patch, err


def run_post_patch_check(file_path: pathlib.Path) -> tuple[bool, str]:
    try:
        ext = file_path.suffix.lower().lstrip(".")
        if not ext:
            return True, ""
        script_map = {
            "py": "check_python.sh",
            "json": "check_json.sh",
            "js": "check_js.sh",
            "jsx": "check_js.sh",
            "ts": "check_js.sh",
            "tsx": "check_tsx.sh",
            "html": "check_html.sh",
        }
        script = script_map.get(ext)
        if not script:
            return True, ""
        script_path = pathlib.Path(__file__).parent.parent / "scripts" / script
        if not script_path.exists():
            return True, ""
        import subprocess

        proc = subprocess.run([str(script_path), str(file_path)], capture_output=True, text=True)
        if proc.returncode == 0:
            return True, ""
        msg = (proc.stderr or proc.stdout or "").strip()
        return False, msg or "post_patch_check_failed"
    except Exception as exc:
        return False, f"post_patch_check_error:{exc}"


def is_safe_relpath(path_value: str) -> bool:
    try:
        p = pathlib.PurePosixPath(path_value)
        if path_value.startswith(("/", "\\")):
            return False
        if any(part == ".." for part in p.parts):
            return False
        return True
    except Exception:
        return False


async def host_artifact_file(
    *,
    hosting_service: Any,
    comm: Any,
    runtime_ctx: Any,
    artifact: Dict[str, Any],
    outdir: pathlib.Path,
) -> List[Dict[str, Any]]:
    """
    Best-effort hosting for file artifacts. Mutates artifact in-place with hosted_uri/rn/key/physical_path.
    Returns hosted file records (possibly empty).
    """
    artifact_value = artifact.get("value") if isinstance(artifact, dict) else {}
    if not isinstance(artifact_value, dict):
        artifact_value = {}
    artifact_log = {
        "artifact_id": artifact.get("artifact_id") or artifact.get("slot") or artifact.get("resource_id"),
        "tool_id": artifact.get("tool_id"),
        "filename": artifact_value.get("filename") or artifact.get("filename"),
        "path": artifact_value.get("path") or artifact_value.get("physical_path") or artifact.get("path"),
        "mime": artifact_value.get("mime") or artifact_value.get("mime_type") or artifact.get("mime"),
        "visibility": artifact.get("visibility"),
    }
    try:
        if not hosting_service or not comm:
            _LOG.warning(
                "[react.artifact.host.skip] reason=missing_runtime hosting_service=%s comm=%s artifact=%s",
                bool(hosting_service),
                bool(comm),
                artifact_log,
            )
            return []
        svc = comm.service or {}
        scope_log = {
            "tenant": svc.get("tenant") or "",
            "project": svc.get("project") or "",
            "user": svc.get("user") or getattr(comm, "user_id", None) or "",
            "conversation_id": svc.get("conversation_id") or getattr(runtime_ctx, "conversation_id", "") or "",
            "turn_id": getattr(runtime_ctx, "turn_id", "") or "",
            "request_id": svc.get("request_id") or "",
        }
        _LOG.info(
            "[react.artifact.host.start] scope=%s outdir=%s artifact=%s",
            scope_log,
            str(outdir),
            artifact_log,
        )
        hosted = await hosting_service.host_files_to_conversation(
            rid=svc.get("request_id") or "",
            files=[artifact],
            outdir=outdir,
            tenant=svc.get("tenant") or "",
            project=svc.get("project") or "",
            user=svc.get("user") or comm.user_id,
            conversation_id=(svc.get("conversation_id") or (getattr(runtime_ctx, "conversation_id", "") or "")),
            user_type=svc.get("user_type") or comm.user_type or "",
            turn_id=(getattr(runtime_ctx, "turn_id", "") or ""),
        )
        if not hosted:
            _LOG.warning(
                "[react.artifact.host.empty] scope=%s outdir=%s artifact=%s",
                scope_log,
                str(outdir),
                artifact_log,
            )
            return []
        h0 = hosted[0]
        hosted_uri = (h0.get("hosted_uri") or "").strip()
        hosted_key = (h0.get("key") or "").strip()
        hosted_rn = (h0.get("rn") or "").strip()
        hosted_physical = (h0.get("physical_path") or h0.get("local_path") or "").strip()
        hosted_size = h0.get("size")
        if isinstance(artifact.get("value"), dict):
            if hosted_uri:
                artifact["value"]["hosted_uri"] = hosted_uri
            if hosted_key:
                artifact["value"]["key"] = hosted_key
            if hosted_rn:
                artifact["value"]["rn"] = hosted_rn
            if hosted_physical:
                artifact["value"]["physical_path"] = hosted_physical
            if hosted_size is not None and artifact["value"].get("size_bytes") is None:
                artifact["value"]["size_bytes"] = hosted_size
            enrich_artifact_file_metadata(
                artifact=artifact,
                outdir=outdir,
                physical_path=hosted_physical or str(artifact["value"].get("path") or ""),
                mime=str(artifact_log.get("mime") or ""),
            )
        if hosted_uri:
            artifact["hosted_uri"] = hosted_uri
        if hosted_key:
            artifact["key"] = hosted_key
        if hosted_rn:
            artifact["rn"] = hosted_rn
        _LOG.info(
            "[react.artifact.host.success] scope=%s hosted_count=%s artifact=%s hosted=%s",
            scope_log,
            len(hosted or []),
            artifact_log,
            {
                "filename": h0.get("filename"),
                "key": h0.get("key"),
                "rn": h0.get("rn"),
                "hosted_uri": h0.get("hosted_uri"),
                "physical_path": h0.get("physical_path") or h0.get("local_path"),
                "size": h0.get("size"),
            },
        )
        return hosted
    except Exception as exc:
        _LOG.exception(
            "[react.artifact.host.error] artifact=%s outdir=%s error=%s",
            artifact_log,
            str(outdir),
            str(exc),
        )
        return []


async def emit_hosted_files(
    *,
    hosting_service: Any,
    hosted: List[Dict[str, Any]],
    should_emit: bool,
) -> None:
    if not hosting_service or not hosted or not should_emit:
        if hosted and should_emit and not hosting_service:
            _LOG.warning(
                "[react.artifact.emit.skip] reason=missing_hosting_service hosted_count=%s",
                len(hosted or []),
            )
        return
    try:
        _LOG.info(
            "[react.artifact.emit.start] hosted_count=%s files=%s",
            len(hosted or []),
            [
                {
                    "filename": item.get("filename"),
                    "key": item.get("key"),
                    "rn": item.get("rn"),
                    "hosted_uri": item.get("hosted_uri"),
                    "size": item.get("size"),
                }
                for item in list(hosted or [])[:10]
                if isinstance(item, dict)
            ],
        )
        await hosting_service.emit_solver_artifacts(files=hosted, citations=[])
        _LOG.info("[react.artifact.emit.success] hosted_count=%s", len(hosted or []))
    except Exception as exc:
        _LOG.exception(
            "[react.artifact.emit.error] hosted_count=%s error=%s",
            len(hosted or []),
            str(exc),
        )
        return

def infer_format_from_path(path: Optional[str]) -> str:
    if not isinstance(path, str) or not path.strip():
        return "markdown"
    p = path.strip().lower()
    if p.endswith(".md") or p.endswith(".markdown"):
        return "markdown"
    if p.endswith(".html") or p.endswith(".htm"):
        return "html"
    if p.endswith(".mermaid") or p.endswith(".mmd"):
        return "mermaid"
    if p.endswith(".json"):
        return "json"
    if p.endswith(".yaml") or p.endswith(".yml"):
        return "yaml"
    if p.endswith(".txt"):
        return "text"
    if p.endswith(".xml"):
        return "xml"
    return "markdown"

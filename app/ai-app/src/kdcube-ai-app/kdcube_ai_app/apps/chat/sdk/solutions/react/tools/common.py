# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import time
import shutil
import subprocess
import tempfile
import logging
import os
from typing import Any, Dict, Optional, List

import json
import pathlib

_LOG = logging.getLogger("kdcube.react.artifacts")
_TOOL_LOG_DEFAULT_MAX_CHARS = 120_000


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
    add_block(ctx_browser, {
        "type": "react.tool.call",
        "call_id": tool_call_id,
        "tool_id": tool_id,
        "mime": "application/json",
        "path": tc_call_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "ts": ts,
        "meta": {
            "tool_call_id": tool_call_id,
        },
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
        if isinstance(artifact.get("value"), dict):
            if hosted_uri:
                artifact["value"]["hosted_uri"] = hosted_uri
            if hosted_key:
                artifact["value"]["key"] = hosted_key
            if hosted_rn:
                artifact["value"]["rn"] = hosted_rn
            if hosted_physical:
                artifact["value"]["physical_path"] = hosted_physical
        if hosted_uri:
            artifact["hosted_uri"] = hosted_uri
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

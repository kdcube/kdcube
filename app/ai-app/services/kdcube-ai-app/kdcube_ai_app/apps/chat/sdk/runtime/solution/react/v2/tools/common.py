# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import time
from typing import Any, Dict, Optional, List

import json
import pathlib


def add_block(ctx_browser, block: Dict[str, Any]) -> None:
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
    })


def notice_block(
    *,
    ctx_browser: Any,
    tool_call_id: str,
    code: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
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
        "path": f"tc:{turn_id}.tool_calls.{tool_call_id}.notice.json" if turn_id else "",
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
    Best-effort hosting for file artifacts. Mutates artifact in-place with hosted_uri/rn/key/local_path.
    Returns hosted file records (possibly empty).
    """
    try:
        if not hosting_service or not comm:
            return []
        svc = comm.service or {}
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
            return []
        h0 = hosted[0]
        hosted_uri = (h0.get("hosted_uri") or "").strip()
        if isinstance(artifact.get("value"), dict) and hosted_uri:
            artifact["value"]["hosted_uri"] = hosted_uri
            artifact["value"]["key"] = h0.get("key")
            artifact["value"]["rn"] = h0.get("rn")
            artifact["value"]["local_path"] = h0.get("local_path")
        if hosted_uri:
            artifact["hosted_uri"] = hosted_uri
            artifact["rn"] = h0.get("rn")
        return hosted
    except Exception:
        return []


async def emit_hosted_files(
    *,
    hosting_service: Any,
    hosted: List[Dict[str, Any]],
    should_emit: bool,
) -> None:
    if not hosting_service or not hosted or not should_emit:
        return
    try:
        await hosting_service.emit_solver_artifacts(files=hosted, citations=[])
    except Exception:
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

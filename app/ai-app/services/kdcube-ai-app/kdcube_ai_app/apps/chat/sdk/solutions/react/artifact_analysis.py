# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/artifact_analysis.py

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import pathlib
from typing import Any, Dict, Optional

import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.infra.service_hub.multimodality import MODALITY_DOC_MIME, MODALITY_IMAGE_MIME, \
    MODALITY_MAX_IMAGE_BYTES, MODALITY_MAX_DOC_BYTES

logger = logging.getLogger(__name__)


def format_tool_error_message(raw: str, max_chars: int = 1000) -> str:
    msg = (raw or "").strip()
    if not msg:
        return ""
    msg = msg.replace("Error logs tail:\nError logs tail:", "Error logs tail:", 1)
    if len(msg) > max_chars:
        msg = msg[-max_chars:]
    return msg


def format_tool_error_for_journal(err_msg: str) -> str:
    if not err_msg:
        return ""
    if "Error logs tail:" in err_msg:
        tail = err_msg.split("Error logs tail:", 1)[1].strip()
        return f"LOGS: {tail}" if tail else "LOGS: (empty)"
    return err_msg


def _coerce_summary_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def _guess_mime_from_path(path: str) -> Optional[str]:
    if not path:
        return None
    guess, _ = mimetypes.guess_type(path)
    return guess


def prepare_summary_artifact(
    artifact: Dict[str, Any],
    base_dir: Optional[pathlib.Path],
) -> Optional[Dict[str, Any]]:
    if not isinstance(artifact, dict):
        return None

    art_type = (artifact.get("type") or "").strip().lower()
    output = artifact.get("output")
    filename = artifact.get("filename") or (output.get("filename") if isinstance(output, dict) else None)
    mime = (artifact.get("mime") or (output.get("mime") if isinstance(output, dict) else None) or "").strip().lower()
    if not mime:
        guess = _guess_mime_from_path((output or {}).get("path") if isinstance(output, dict) else "")
        mime = (guess or "").strip().lower()

    if art_type == "inline":
        if isinstance(output, dict):
            text = output.get("text")
            if text is None:
                text = output.get("value")
        else:
            text = output
        return {
            "type": "inline",
            "mime": mime or None,
            "text": _coerce_summary_text(text),
        }

    if art_type != "file":
        return None

    if isinstance(output, dict):
        relpath = output.get("path") or ""
        text = output.get("text")
    else:
        relpath = ""
        text = output

    file_path = None
    if relpath:
        p = pathlib.Path(relpath)
        if not p.is_absolute() and base_dir:
            p = base_dir / relpath
        file_path = p

    size_bytes = None
    base64_data = None
    read_error = None

    if file_path and file_path.exists() and file_path.is_file():
        try:
            size_bytes = file_path.stat().st_size
            if mime in MODALITY_IMAGE_MIME or mime in MODALITY_DOC_MIME:
                limit = MODALITY_MAX_IMAGE_BYTES if mime in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
                if size_bytes <= limit:
                    base64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
                else:
                    read_error = f"file too large to attach ({size_bytes} bytes > {limit})"
        except Exception as exc:
            read_error = f"file read failed: {exc}"
    elif relpath:
        read_error = "file not found on disk"

    return {
        "type": "file",
        "mime": mime or None,
        "text": _coerce_summary_text(text),
        "base64": base64_data,
        "filename": filename,
        "path": str(file_path) if file_path else relpath,
        "size_bytes": size_bytes,
        "read_error": read_error,
    }


def prepare_write_tool_summary_artifact(
    *,
    tool_id: str,
    output: Any,
    inputs: Optional[Dict[str, Any]],
    base_dir: Optional[pathlib.Path],
    surrogate_text: str,
    mime_hint: str,
) -> Optional[Dict[str, Any]]:
    if not tools_insights.is_write_tool(tool_id):
        return None

    file_path = ""
    if isinstance(output, dict) and isinstance(output.get("path"), str):
        file_path = output.get("path", "").strip()
    elif isinstance(output, str):
        file_path = output.strip()

    if not file_path:
        return None

    filename = pathlib.Path(file_path).name if file_path else ""
    artifact = {
        "type": "file",
        "output": {
            "path": file_path,
            "text": surrogate_text or "",
            "mime": mime_hint or tools_insights.default_mime_for_write_tool(tool_id),
            "filename": filename,
        },
        "mime": mime_hint or "",
        "filename": filename,
    }
    return prepare_summary_artifact(artifact, base_dir)


def analyze_write_tool_output(
    *,
    file_path: str,
    mime: str,
    output_dir: Optional[pathlib.Path],
    artifact_id: Optional[str] = None,
) -> Dict[str, Any]:
    stats: Dict[str, Any] = {}
    if not file_path:
        stats["write_error"] = "file_not_found"
        return stats

    try:
        p = pathlib.Path(file_path)
        if not p.is_absolute() and output_dir:
            p = output_dir / p
        if not p.exists() or not p.is_file():
            stats["write_error"] = "file_not_found"
            return stats
        size_bytes = p.stat().st_size
        stats["size_bytes"] = size_bytes
        if size_bytes == 0:
            stats["write_error"] = "empty_file"
            return stats
        mime_check = (mime or "").strip().lower()
        min_sizes = {
            "application/pdf": 500,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 1000,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": 2000,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 1000,
        }
        min_size = min_sizes.get(mime_check)
        if min_size and size_bytes < min_size:
            stats["write_warning"] = "file_unusually_small"
        if mime_check == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
                visible = [ws for ws in wb.worksheets if ws.sheet_state == "visible"]
                if not visible:
                    stats["write_error"] = "xlsx_no_visible_sheets"
            except Exception as exc:
                stats["write_error"] = f"xlsx_open_failed: {exc}"
    except Exception:
        stats["write_warning"] = "file_stat_failed"
    logger.info(
        "[artifact_analysis] artifact=%s path=%s stats=%s",
        artifact_id or "",
        file_path,
        stats,
    )
    return stats

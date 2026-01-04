# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/user_inputs.py

from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

from kdcube_ai_app.tools.file_text_extractor import DocumentTextExtractor, ExtractInfo
from kdcube_ai_app.apps.chat.sdk.tools.backends.summary_backends import (
    _SUMMARY_IMAGE_MIME,
    _SUMMARY_DOC_MIME,
    _SUMMARY_MAX_IMAGE_BYTES,
    _SUMMARY_MAX_DOC_BYTES,
)
from kdcube_ai_app.tools.content_type import is_text_mime_type


async def ingest_user_attachments(
    *,
    attachments: List[Dict[str, Any]],
    store: Any,
    max_mb: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not attachments:
        return []

    if max_mb is None:
        try:
            max_mb = int(os.environ.get("CHAT_MAX_UPLOAD_MB", "20"))
        except Exception:
            max_mb = 20

    max_bytes = max_mb * 1024 * 1024
    extractor = DocumentTextExtractor()

    out: List[Dict[str, Any]] = []
    for a in attachments or []:
        if not isinstance(a, dict):
            continue

        base = dict(a)
        name = (a.get("filename") or a.get("name") or "file").strip()
        mime = (a.get("mime") or a.get("mime_type") or "application/octet-stream").strip()
        src = a.get("hosted_uri") or a.get("source_path") or a.get("path") or a.get("key")

        data = None
        if src and store:
            try:
                data = await store.get_blob_bytes(src)
            except Exception as ex:
                base["error"] = f"read_failed: {ex}"

        if data is None:
            out.append(base)
            continue

        size = a.get("size") or a.get("size_bytes") or len(data)
        base["size"] = size
        base["size_bytes"] = size
        if size > max_bytes:
            base["error"] = f"size_limit: {size} > {max_bytes}"
            out.append(base)
            continue

        resolved_mime, resolved_ext, _ = extractor._resolve_mime_and_ext(data, name, mime)
        if is_text_mime_type(resolved_mime):
            try:
                text, info = extractor.extract(data, name, mime)
            except Exception as ex:
                base["error"] = f"extract_failed: {ex}"
                out.append(base)
                continue
        else:
            text = ""
            info = ExtractInfo(mime=resolved_mime, ext=resolved_ext, meta={}, warnings=[])

        base64_data = None
        if mime in _SUMMARY_IMAGE_MIME or mime in _SUMMARY_DOC_MIME:
            limit = _SUMMARY_MAX_IMAGE_BYTES if mime in _SUMMARY_IMAGE_MIME else _SUMMARY_MAX_DOC_BYTES
            if size <= limit:
                base64_data = base64.b64encode(data).decode("ascii")
            else:
                base["read_error"] = f"file too large to attach ({size} bytes > {limit})"

        base["filename"] = name
        if "name" in base:
            base.pop("name", None)
        base["mime"] = info.mime or mime
        base["ext"] = info.ext
        base["text"] = text
        base["warnings"] = info.warnings
        if base64_data:
            base["base64"] = base64_data
        if info.meta:
            base["meta"] = {**(base.get("meta") or {}), **info.meta}
        out.append(base)

    return out


def attachment_summary_text(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["ATTACHMENTS:"]
    for a in items:
        if not isinstance(a, dict):
            continue
        name = (a.get("artifact_name") or a.get("filename") or "attachment").strip()
        filename = (a.get("filename") or "").strip()
        mime = (a.get("mime") or a.get("mime_type") or "").strip()
        size = a.get("size") or a.get("size_bytes")
        summary = (a.get("summary") or "").strip()
        parts = [name]
        if filename:
            parts.append(f"filename={filename}")
        if mime:
            parts.append(f"mime={mime}")
        if size is not None:
            parts.append(f"size={size}")
        lines.append("- " + " | ".join(parts))
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines).strip()


def attachment_blocks(
    items: List[Dict[str, Any]],
    *,
    include_summary_text: bool = True,
    include_text: bool = False,
    include_modal: bool = True,
    max_text_chars: int = 4000,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        mime = (a.get("mime") or a.get("mime_type") or "").strip()
        base64_data = a.get("base64")
        if include_modal:
            if base64_data and mime in _SUMMARY_IMAGE_MIME:
                blocks.append({"type": "image", "data": base64_data, "media_type": mime, "cache": True})
            elif base64_data and mime in _SUMMARY_DOC_MIME:
                blocks.append({"type": "document", "data": base64_data, "media_type": mime, "cache": True})
        if include_text and is_text_mime_type(mime):
            text_val = (a.get("text") or "").strip()
            if text_val:
                if max_text_chars and len(text_val) > max_text_chars:
                    text_val = text_val[:max_text_chars] + "\n...[truncated]"
                blocks.append({"type": "text", "text": text_val, "cache": False})
        if include_summary_text:
            name = (a.get("artifact_name") or a.get("filename") or "attachment").strip()
            filename = (a.get("filename") or "").strip()
            size = a.get("size") or a.get("size_bytes")
            summary = (a.get("summary") or "").strip()
            parts = [f"attachment={name}"]
            if filename:
                parts.append(f"filename={filename}")
            if mime:
                parts.append(f"mime={mime}")
            if size is not None:
                parts.append(f"size={size}")
            text = " | ".join(parts)
            if summary:
                text = f"{text}\nsummary: {summary}"
            blocks.append({"type": "text", "text": text, "cache": False})

    # Enforce a max of 4 cached blocks per message (first wins).
    cached = 0
    for b in blocks:
        if b.get("cache"):
            cached += 1
            if cached > 4:
                b["cache"] = False
    return blocks


def attachment_summary_index_text(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return str(payload or "")[:2000]
    summary = (payload.get("summary") or "").strip()
    filename = (payload.get("filename") or "").strip()
    mime = (payload.get("mime") or "").strip()
    size = payload.get("size")
    parts = [p for p in (filename, mime) if p]
    if size is not None:
        parts.append(f"size={size}")
    header = " ".join(parts).strip()
    text = f"{header}\n{summary}".strip()
    return text[:4000]

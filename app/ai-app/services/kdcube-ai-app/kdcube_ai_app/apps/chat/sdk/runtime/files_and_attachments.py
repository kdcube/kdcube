# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
import json
from urllib.parse import urlparse
from datetime import datetime, timezone
import mimetypes
import pathlib

from kdcube_ai_app.apps.chat.sdk.util import estimate_b64_size
from kdcube_ai_app.infra.service_hub.multimodality import (
    MODALITY_IMAGE_MIME,
    MODALITY_DOC_MIME,
    MODALITY_MAX_IMAGE_BYTES,
    MODALITY_MAX_DOC_BYTES,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.artifact_analysis import prepare_summary_artifact
from kdcube_ai_app.apps.chat.sdk.tools.citations import extract_local_paths_any


def unwrap_llm_content_payload(payload: Any) -> Any:
    """
    If payload is a JSON object shaped like an LLM tool result and has
    tool.origin == "llm_tools.generate_content_llm", return its content.
    Otherwise return the original payload unchanged.
    """
    data = None
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, str):
        raw = payload.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                data = json.loads(raw)
            except Exception:
                data = None
    if not isinstance(data, dict):
        return payload
    if data.get("tool.origin") != "llm_tools.generate_content_llm":
        return payload
    content = data.get("content")
    if not isinstance(content, str):
        return payload
    return content


def collect_local_file_sources_from_content(
    content: Any,
    *,
    outdir: pathlib.Path,
) -> List[Dict[str, Any]]:
    """
    Collect local file references embedded in rendered content and
    return sources_pool-ready entries (url is a local file path).
    """
    text = unwrap_llm_content_payload(content)
    if not isinstance(text, str) or not text.strip():
        return []

    sources: List[Dict[str, Any]] = []
    for raw in extract_local_paths_any(text):
        candidate = (raw or "").strip()
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if not path.is_absolute():
            path = outdir / path
        try:
            path = path.resolve()
        except Exception:
            continue
        try:
            rel_path = path.relative_to(outdir)
        except Exception:
            continue
        if not path.exists() or not path.is_file():
            continue
        mime = mimetypes.guess_type(str(path))[0] or ""
        try:
            stat = path.stat()
            size_bytes = int(stat.st_size)
            modified_time_iso = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        except Exception:
            size_bytes = None
            modified_time_iso = ""
        row: Dict[str, Any] = {
            "url": str(rel_path),
            "title": path.name,
            "source_type": "file",
        }
        row["local_path"] = str(rel_path)
        if mime:
            row["mime"] = mime
        if isinstance(size_bytes, int):
            row["size_bytes"] = size_bytes
        if modified_time_iso:
            row["modified_time_iso"] = modified_time_iso
        sources.append(row)
    return sources


def strip_base64_from_value(val: Any) -> Any:
    if isinstance(val, list):
        out_list = []
        for item in val:
            if isinstance(item, dict):
                out_list.append({k: v for k, v in item.items() if k != "base64"})
            else:
                out_list.append(item)
        return out_list
    if isinstance(val, dict):
        return {k: v for k, v in val.items() if k != "base64"}
    return val


def strip_base64_from_tool_output(tool_id: str, obj: Any) -> Any:
    if tool_id == "generic_tools.web_search" and isinstance(obj, list):
        return strip_base64_from_value(obj)
    if tool_id == "generic_tools.fetch_url_contents" and isinstance(obj, dict):
        cleaned_obj: Dict[str, Any] = {}
        for url, entry in obj.items():
            if not isinstance(entry, dict):
                cleaned_obj[url] = entry
                continue
            cleaned_obj[url] = {k: v for k, v in entry.items() if k != "base64"}
        return cleaned_obj
    return strip_base64_from_value(obj)


def collect_multimodal_artifacts_from_tool_output(
    tool_id: str,
    obj: Any,
    *,
    max_items: int = 2,
) -> List[Dict[str, Any]]:
    if tool_id not in ("generic_tools.web_search", "generic_tools.fetch_url_contents"):
        return []

    rows: List[Dict[str, Any]] = []
    if tool_id == "generic_tools.web_search" and isinstance(obj, list):
        rows = [r for r in obj if isinstance(r, dict)]
    elif tool_id == "generic_tools.fetch_url_contents" and isinstance(obj, dict):
        for url, entry in obj.items():
            if not isinstance(entry, dict):
                continue
            rows.append({**entry, "url": url})

    collected: List[Dict[str, Any]] = []
    seen_mime: Set[str] = set()
    for row in rows:
        mime = (row.get("mime") or "").strip().lower()
        data_b64 = row.get("base64")
        if not mime or not data_b64:
            continue
        if mime not in MODALITY_IMAGE_MIME and mime not in MODALITY_DOC_MIME:
            continue
        if mime in seen_mime:
            continue
        size_bytes = row.get("size_bytes")
        if size_bytes is None:
            size_bytes = estimate_b64_size(data_b64)
        if size_bytes is None:
            continue
        limit = MODALITY_MAX_IMAGE_BYTES if mime in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
        if size_bytes > limit:
            continue
        filename = (row.get("filename") or "").strip()
        if not filename:
            url = row.get("url") if isinstance(row.get("url"), str) else ""
            filename = urlparse(url).path.split("/")[-1] if url else ""
        if not filename:
            filename = f"source_{row.get('sid') or len(collected) + 1}"
        collected.append({
            "type": "file",
            "mime": mime,
            "base64": data_b64,
            "text": row.get("text") or "",
            "filename": filename,
            "size_bytes": size_bytes,
        })
        seen_mime.add(mime)
        if len(collected) >= max_items:
            break
    return collected


def artifact_block_for_summary(
    artifact: Optional[Dict[str, Any]],
) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    if not isinstance(artifact, dict):
        return None, None, None

    art_type = (artifact.get("type") or "").strip().lower()
    mime = (artifact.get("mime") or "").strip().lower()
    text = artifact.get("text") or ""
    base64_data = artifact.get("base64")
    size_bytes = artifact.get("size_bytes")
    filename = artifact.get("filename")
    read_error = artifact.get("read_error")

    block = None
    modality_kind = None
    if base64_data and mime in MODALITY_IMAGE_MIME:
        modality_kind = "image"
        block = {"type": "image", "data": base64_data, "media_type": mime}
    elif base64_data and mime in MODALITY_DOC_MIME:
        modality_kind = "document"
        block = {"type": "document", "data": base64_data, "media_type": mime}
    elif text:
        modality_kind = "text"
        block = {"type": "text", "text": text}

    meta_lines = [
        "### Attached artifact (for validation)",
        f"- type: {art_type or 'unknown'}",
        f"- mime: {mime or 'unknown'}",
        f"- filename: {filename or 'unknown'}",
        f"- size_bytes: {size_bytes if isinstance(size_bytes, int) else 'unknown'}",
        f"- base64_attached: {'yes' if block else 'no'}",
        f"- text_surrogate_len: {len(text)}",
    ]
    if read_error:
        meta_lines.append(f"- read_error: {read_error}")
    if art_type == "file" and not block and mime and mime not in MODALITY_IMAGE_MIME and mime not in MODALITY_DOC_MIME:
        meta_lines.append("- note: mime not supported for vision; using text surrogate only")

    return block, "\n".join(meta_lines), modality_kind


def artifact_blocks_for_summary(
    artifacts: Optional[Any],
) -> Tuple[List[dict], Optional[str], Set[str]]:
    if isinstance(artifacts, dict):
        artifacts_list = [artifacts]
    elif isinstance(artifacts, list):
        artifacts_list = [a for a in artifacts if isinstance(a, dict)]
    else:
        artifacts_list = []

    blocks: List[dict] = []
    meta_lines: List[str] = []
    modality_kinds: Set[str] = set()
    for artifact in artifacts_list:
        block, meta, modality_kind = artifact_block_for_summary(artifact)
        if meta:
            meta_lines.append(meta)
        if block:
            blocks.append(block)
        if modality_kind:
            modality_kinds.add(modality_kind)

    meta_text = "\n\n".join(meta_lines) if meta_lines else None
    return blocks, meta_text, modality_kinds


def collect_modal_attachments_from_artifact_obj(
    obj: Any,
    *,
    outdir: Optional[Any] = None,
    max_items: int = 2,
) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []
    seen_mime: Set[str] = set()

    def _maybe_add(mime: str, data_b64: str, *, filename: str = "", summary: str = "", size_bytes: Optional[int] = None):
        if len(attachments) >= max_items:
            return
        mime_norm = (mime or "").strip().lower()
        if not mime_norm or not data_b64:
            return
        if mime_norm not in MODALITY_IMAGE_MIME and mime_norm not in MODALITY_DOC_MIME:
            return
        if mime_norm in seen_mime:
            return
        size = size_bytes if isinstance(size_bytes, int) else estimate_b64_size(data_b64)
        if size is None:
            return
        limit = MODALITY_MAX_IMAGE_BYTES if mime_norm in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
        if size > limit:
            return
        attachments.append({
            "mime": mime_norm,
            "base64": data_b64,
            "filename": filename or "",
            "summary": summary or "",
            "size_bytes": size,
        })
        seen_mime.add(mime_norm)

    if isinstance(obj, dict):
        tool_id = (obj.get("tool_id") or "").strip()
        artifact_kind = (obj.get("artifact_kind") or "").strip()
        value = obj.get("value")
        if tool_id in ("generic_tools.web_search", "generic_tools.fetch_url_contents") or artifact_kind == "search":
            if isinstance(value, list):
                for row in value:
                    if not isinstance(row, dict):
                        continue
                    _maybe_add(
                        row.get("mime") or "",
                        row.get("base64"),
                        filename=(row.get("filename") or ""),
                        summary=f"source sid={row.get('sid')} url={row.get('url')}".strip(),
                        size_bytes=row.get("size_bytes"),
                    )
            elif isinstance(value, dict):
                for url, entry in value.items():
                    if not isinstance(entry, dict):
                        continue
                    _maybe_add(
                        entry.get("mime") or "",
                        entry.get("base64"),
                        filename=(entry.get("filename") or ""),
                        summary=f"source url={url}".strip(),
                        size_bytes=entry.get("size_bytes"),
                    )
            return attachments

        if (obj.get("artifact_kind") == "file") or (obj.get("type") == "file"):
            if obj.get("type") == "file":
                summary_artifact = prepare_summary_artifact(obj, outdir)
            else:
                output = obj.get("value") if isinstance(obj.get("value"), dict) else {}
                file_artifact = {
                    "type": "file",
                    "output": output,
                    "mime": obj.get("mime") or output.get("mime"),
                    "filename": obj.get("filename") or output.get("filename"),
                }
                summary_artifact = prepare_summary_artifact(file_artifact, outdir)
            if summary_artifact:
                _maybe_add(
                    summary_artifact.get("mime") or "",
                    summary_artifact.get("base64"),
                    filename=summary_artifact.get("filename") or "",
                    summary=f"artifact={obj.get('artifact_id') or obj.get('resource_id') or ''}".strip(),
                    size_bytes=summary_artifact.get("size_bytes"),
                )
            return attachments

        if obj.get("base64") and obj.get("mime"):
            _maybe_add(
                obj.get("mime"),
                obj.get("base64"),
                filename=(obj.get("filename") or ""),
                summary=f"artifact={obj.get('artifact_id') or obj.get('resource_id') or ''}".strip(),
                size_bytes=obj.get("size_bytes"),
            )
    return attachments


def build_attachment_message_blocks(attachments: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    if not attachments:
        return blocks
    blocks.append({"text": f"ATTACHMENTS ({len(attachments)}):", "cache": False})
    for a in attachments:
        if not isinstance(a, dict):
            continue
        mime = (a.get("mime") or "").strip()
        data_b64 = a.get("base64")
        filename = (a.get("filename") or "").strip()
        summary = (a.get("summary") or "").strip()
        size = a.get("size") or a.get("size_bytes")
        if data_b64 and mime in MODALITY_IMAGE_MIME:
            blocks.append({"type": "image", "data": data_b64, "media_type": mime, "cache": False})
        elif data_b64 and mime in MODALITY_DOC_MIME:
            blocks.append({"type": "document", "data": data_b64, "media_type": mime, "cache": False})
        meta_parts = []
        if filename:
            meta_parts.append(f"filename={filename}")
        if mime:
            meta_parts.append(f"mime={mime}")
        if size is not None:
            meta_parts.append(f"size={size}")
        meta_line = " | ".join(meta_parts)
        if meta_line:
            blocks.append({"text": f"ATTACHMENT META: {meta_line}", "cache": False})
        if summary:
            blocks.append({"text": f"ATTACHMENT SUMMARY: {summary}", "cache": False})
    return blocks

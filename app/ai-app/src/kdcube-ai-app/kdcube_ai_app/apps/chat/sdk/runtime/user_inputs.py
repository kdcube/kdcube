# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/user_inputs.py

from __future__ import annotations

import base64
import datetime
import json
import re
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.multimodality import MODALITY_IMAGE_MIME, MODALITY_DOC_MIME, \
    MODALITY_MAX_DOC_BYTES, MODALITY_MAX_IMAGE_BYTES
from kdcube_ai_app.tools.file_text_extractor import DocumentTextExtractor, ExtractInfo

from kdcube_ai_app.tools.content_type import is_text_mime_type


async def ingest_user_attachments(
    *,
    attachments: List[Dict[str, Any]],
    store: Any,
) -> List[Dict[str, Any]]:
    if not attachments:
        return []

    extractor = DocumentTextExtractor()

    out: List[Dict[str, Any]] = []
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    for a in attachments or []:
        if not isinstance(a, dict):
            continue

        base = dict(a)
        if not base.get("ts"):
            base["ts"] = now_iso
        name = (a.get("filename") or a.get("name") or "file").strip()
        mime = (a.get("mime") or a.get("mime_type") or "application/octet-stream").strip()
        src = a.get("hosted_uri") or a.get("source_path") or a.get("path") or a.get("key")

        data = None
        if src and store:
            try:
                data = await store.get_blob_bytes(src)
            except Exception as ex:
                base["error"] = f"read_failed: {ex}"
        if data is None and a.get("base64"):
            try:
                data = base64.b64decode(str(a.get("base64") or ""), validate=True)
            except Exception as ex:
                base["error"] = f"base64_decode_failed: {ex}"

        if data is None:
            out.append(base)
            continue

        size = a.get("size") or a.get("size_bytes") or len(data)
        base["size"] = size
        base["size_bytes"] = size
        if mime in MODALITY_IMAGE_MIME and size > MODALITY_MAX_IMAGE_BYTES:
            base["error"] = f"size_limit: {size} > {MODALITY_MAX_IMAGE_BYTES}"
            out.append(base)
            continue
        if mime in MODALITY_DOC_MIME and size > MODALITY_MAX_DOC_BYTES:
            base["error"] = f"size_limit: {size} > {MODALITY_MAX_DOC_BYTES}"
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
        if mime in MODALITY_IMAGE_MIME or mime in MODALITY_DOC_MIME:
            limit = MODALITY_MAX_IMAGE_BYTES if mime in MODALITY_IMAGE_MIME else MODALITY_MAX_DOC_BYTES
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
    lines: List[str] = []
    used: Dict[str, int] = {}
    for a in items:
        if not isinstance(a, dict):
            continue
        raw_name = (a.get("artifact_name") or a.get("filename") or "attachment").strip()
        name = re.sub(r"[\\s./:]+", "_", raw_name)
        name = re.sub(r"[^A-Za-z0-9_-]+", "", name) or "attachment"
        name = name.lower()
        count = used.get(name, 0) + 1
        used[name] = count
        if count > 1:
            name = f"{name}_{count}"
        filename = (a.get("filename") or "").strip()
        mime = (a.get("mime") or a.get("mime_type") or "").strip()
        size = a.get("size") or a.get("size_bytes")
        summary = (a.get("summary") or "").strip()
        parts = []
        if filename:
            parts.append(f"filename={filename}")
        if mime:
            parts.append(f"mime={mime}")
        if size is not None:
            parts.append(f"size={size}")
        if summary:
            line = f"[user.attachments.{name}.summary] {summary}"
        else:
            line = f"[user.attachments.{name}.summary]"
        if parts:
            line += " | " + " | ".join(parts)
        lines.append(line)
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
            if base64_data and mime in MODALITY_IMAGE_MIME:
                blocks.append({"type": "image", "data": base64_data, "media_type": mime, "cache": True})
            elif base64_data and mime in MODALITY_DOC_MIME:
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


_USER_INPUT_BLOCK_TYPES = {
    "user.prompt",
    "user.followup",
    "user.followup.preserved",
    "user.steer",
    "user.steer.preserved",
}


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _block_meta(block: Dict[str, Any]) -> Dict[str, Any]:
    meta = block.get("meta")
    return meta if isinstance(meta, dict) else {}


def _block_batch_id(block: Dict[str, Any]) -> str:
    meta = _block_meta(block)
    for value in (
        meta.get("batch_id"),
        meta.get("batchId"),
        block.get("batch_id"),
        block.get("batchId"),
    ):
        s = _safe_str(value)
        if s:
            return s
    return ""


def _event_from_block(block: Dict[str, Any]) -> Dict[str, Any]:
    meta = _block_meta(block)
    event = meta.get("event")
    if isinstance(event, dict):
        return event
    text = block.get("text")
    if isinstance(text, str) and text.strip().startswith("{"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("event"), dict):
            return parsed["event"]
    return {}


def _event_payload_dict(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        inner = payload.get("event")
        if isinstance(inner, dict):
            return inner
        return payload
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_str(value)
        if text:
            return text
    return ""


def _clip(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", _safe_str(text))
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "..."
    return text


def user_input_text_with_context_chips(text: str, contexts: List[Dict[str, Any]]) -> str:
    """
    Preserve the widget's lightweight context-chip wire format for historical
    hydration while keeping a separate rendered index text for search.
    """
    base = _safe_str(text)
    clean_contexts: List[Dict[str, Any]] = []
    for idx, context in enumerate(contexts or []):
        if not isinstance(context, dict):
            continue
        label = _first_text(context.get("label"), context.get("ref"), context.get("kind"), "context")
        clean_contexts.append({
            "id": _first_text(context.get("id"), context.get("ref"), f"context-{idx}"),
            "label": label,
            **{k: v for k, v in context.items() if k not in {"id", "label"} and v is not None and v != ""},
        })
    if not clean_contexts:
        return base
    payload = json.dumps({"context": clean_contexts}, ensure_ascii=False)
    return f"{base}\n\n{payload}" if base else payload


def context_chip_from_event_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    btype = _safe_str(block.get("type"))
    if not btype or btype in _USER_INPUT_BLOCK_TYPES or btype.startswith("user.attachment."):
        return None
    if not btype.startswith("event."):
        return None

    meta = _block_meta(block)
    event = _event_from_block(block)
    body = _event_payload_dict(event)
    event_type = _first_text(meta.get("event_type"), event.get("type"), btype)
    event_source_id = _first_text(
        meta.get("event_source_id"),
        event.get("event_source_id"),
        body.get("event_source_id"),
    )
    ref = _first_text(
        body.get("object_ref"),
        body.get("ref"),
        body.get("hosted_uri"),
        body.get("logical_path"),
        event.get("object_ref"),
        event.get("ref"),
        event.get("hosted_uri"),
        event.get("logical_path"),
        meta.get("object_ref"),
        meta.get("ref"),
        meta.get("hosted_uri"),
        meta.get("logical_path"),
        block.get("path"),
    )
    label = _clip(_first_text(
        body.get("label"),
        body.get("title"),
        body.get("name"),
        body.get("summary"),
        body.get("description"),
        body.get("text"),
        ref,
        event_source_id,
        event_type,
        "context",
    ), 120)
    kind = _first_text(
        body.get("kind"),
        body.get("card_kind"),
        body.get("object_kind"),
        event_source_id,
        event_type,
    )
    summary = _clip(_first_text(
        body.get("summary"),
        body.get("description"),
        body.get("text"),
        meta.get("summary"),
    ), 300)
    chip: Dict[str, Any] = {
        "id": _first_text(ref, block.get("path"), event.get("id"), event_type),
        "label": label,
        "kind": kind,
        "event_type": event_type,
    }
    if event_source_id:
        chip["event_source_id"] = event_source_id
    if ref:
        chip["ref"] = ref
    if summary and summary != label:
        chip["summary"] = summary
    return chip


def attachment_info_from_user_attachment_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    btype = _safe_str(block.get("type"))
    if not btype.startswith("user.attachment."):
        return None
    meta = _block_meta(block)
    payload: Dict[str, Any] = {}
    text = block.get("text")
    if isinstance(text, str) and text.strip().startswith("{"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    event = _event_from_block(block)
    body = _event_payload_dict(event)
    filename = _first_text(
        payload.get("filename"),
        payload.get("name"),
        body.get("filename"),
        body.get("name"),
        meta.get("filename"),
        meta.get("name"),
        block.get("path"),
        "attachment",
    )
    mime = _first_text(payload.get("mime"), payload.get("mime_type"), body.get("mime"), body.get("mime_type"), meta.get("mime"))
    ref = _first_text(
        payload.get("hosted_uri"),
        payload.get("logical_path"),
        payload.get("artifact_path"),
        body.get("hosted_uri"),
        body.get("logical_path"),
        body.get("artifact_path"),
        meta.get("hosted_uri"),
        meta.get("logical_path"),
        meta.get("artifact_path"),
        block.get("path"),
    )
    info: Dict[str, Any] = {"label": _clip(filename, 160)}
    if mime:
        info["mime"] = mime
    if ref:
        info["ref"] = ref
    size = payload.get("size") or payload.get("size_bytes") or body.get("size") or body.get("size_bytes") or meta.get("size")
    if size is not None:
        info["size"] = size
    return info


def _attachment_identity_key(block: Dict[str, Any], info: Dict[str, Any]) -> str:
    meta = _block_meta(block)
    event = _event_from_block(block)
    label = _safe_str(info.get("label"))
    for value in (
        block.get("path"),
        meta.get("logical_path"),
        meta.get("artifact_path"),
        (_event_payload_dict(event) or {}).get("logical_path"),
        (_event_payload_dict(event) or {}).get("artifact_path"),
    ):
        text = _safe_str(value)
        if text:
            return text
    event_id = _safe_str((event or {}).get("id") or meta.get("event_id"))
    if event_id or label:
        return f"{event_id}:{label}"
    return _safe_str(info.get("ref"))


def _ref_quality(ref: Any) -> int:
    text = _safe_str(ref)
    if not text:
        return 0
    if text.startswith("fi:"):
        return 4
    if "://" not in text:
        return 3
    if text.startswith("file://"):
        return 1
    return 2


def _merge_attachment_info(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    if _ref_quality(incoming.get("ref")) > _ref_quality(merged.get("ref")):
        merged["ref"] = incoming.get("ref")
    for key in ("label", "mime", "size"):
        if merged.get(key) in (None, "") and incoming.get(key) not in (None, ""):
            merged[key] = incoming.get(key)
    return merged


def render_user_input_batch_index_text(
    *,
    text: str,
    contexts: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
) -> str:
    lines: List[str] = []
    message = _safe_str(text)
    lines.append("[user.message]")
    lines.append(message if message else "(no typed message)")
    for idx, context in enumerate(contexts or [], start=1):
        if not isinstance(context, dict):
            continue
        parts = [
            _safe_str(context.get("label")),
            f"kind={context.get('kind')}" if _safe_str(context.get("kind")) else "",
            f"ref={context.get('ref')}" if _safe_str(context.get("ref")) else "",
            f"source={context.get('event_source_id')}" if _safe_str(context.get("event_source_id")) else "",
            f"event={context.get('event_type')}" if _safe_str(context.get("event_type")) else "",
        ]
        line = f"[context.{idx}] " + " | ".join([p for p in parts if p])
        summary = _safe_str(context.get("summary"))
        if summary:
            line += f"\n{summary}"
        lines.append(line)
    for idx, attachment in enumerate(attachments or [], start=1):
        if not isinstance(attachment, dict):
            continue
        parts = [
            _safe_str(attachment.get("label")),
            f"mime={attachment.get('mime')}" if _safe_str(attachment.get("mime")) else "",
            f"ref={attachment.get('ref')}" if _safe_str(attachment.get("ref")) else "",
            f"size={attachment.get('size')}" if attachment.get("size") is not None else "",
        ]
        lines.append(f"[attachment.{idx}] " + " | ".join([p for p in parts if p]))
    return "\n".join(lines).strip()


def _user_event_type_for_block(block: Dict[str, Any]) -> Optional[str]:
    btype = _safe_str(block.get("type"))
    meta = _block_meta(block)
    if btype == "user.prompt":
        return _first_text(meta.get("event_type"), "event.user.prompt")
    if btype in {"user.followup", "user.followup.preserved"}:
        return "event.user.followup"
    if btype in {"user.steer", "user.steer.preserved"}:
        return "event.user.steer"
    return None


def iter_turn_user_input_entries(blocks: List[Dict[str, Any]], *, turn_id: str) -> List[Dict[str, Any]]:
    """
    Return durable user-input batches for a turn.

    A Send action can arrive as an accepted event batch: prompt/followup text,
    files, and context refs share the same batch_id. Index and hydration must
    treat that whole batch as the user's input, even when the typed text is
    intentionally empty.
    """
    by_batch_context: Dict[str, List[Dict[str, Any]]] = {}
    by_batch_attachment: Dict[str, List[Dict[str, Any]]] = {}
    by_batch_attachment_key: Dict[str, Dict[str, int]] = {}
    user_blocks: List[Dict[str, Any]] = []

    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        blk_tid = _safe_str(block.get("turn_id"))
        if blk_tid and blk_tid != turn_id:
            continue
        batch_id = _block_batch_id(block)
        btype = _safe_str(block.get("type"))
        if btype in _USER_INPUT_BLOCK_TYPES:
            user_blocks.append(block)
            continue
        if not batch_id:
            continue
        context = context_chip_from_event_block(block)
        if context:
            by_batch_context.setdefault(batch_id, []).append(context)
            continue
        attachment = attachment_info_from_user_attachment_block(block)
        if attachment:
            attachment_key = _attachment_identity_key(block, attachment)
            attachments = by_batch_attachment.setdefault(batch_id, [])
            key_map = by_batch_attachment_key.setdefault(batch_id, {})
            if attachment_key and attachment_key in key_map:
                idx = key_map[attachment_key]
                attachments[idx] = _merge_attachment_info(attachments[idx], attachment)
            else:
                if attachment_key:
                    key_map[attachment_key] = len(attachments)
                attachments.append(attachment)

    entries: List[Dict[str, Any]] = []
    for block in user_blocks:
        batch_id = _block_batch_id(block)
        contexts = by_batch_context.get(batch_id, []) if batch_id else []
        attachments = by_batch_attachment.get(batch_id, []) if batch_id else []
        text = _safe_str(block.get("text"))
        if not text and not contexts and not attachments:
            continue
        path = _safe_str(block.get("path"))
        if not path:
            suffix = batch_id or str(len(entries))
            path = f"ar:{turn_id}.user.input.{suffix}"
        meta = _block_meta(block)
        event_ids: List[str] = []
        if batch_id:
            event_ids = [
                _safe_str((_event_from_block(b) or {}).get("id"))
                for b in blocks or []
                if isinstance(b, dict)
                and _block_batch_id(b) == batch_id
                and _safe_str((_event_from_block(b) or {}).get("id"))
            ]
        display_text = user_input_text_with_context_chips(text, contexts)
        index_text = render_user_input_batch_index_text(
            text=text,
            contexts=contexts,
            attachments=attachments,
        )
        entries.append({
            "text": display_text,
            "plain_text": text,
            "index_text": index_text,
            "contexts": contexts,
            "attachments": attachments,
            "ts": _safe_str(block.get("ts")),
            "path": path,
            "user_event_type": _user_event_type_for_block(block),
            "batch_id": batch_id,
            "event_ids": event_ids,
            "meta": meta,
        })
    return entries

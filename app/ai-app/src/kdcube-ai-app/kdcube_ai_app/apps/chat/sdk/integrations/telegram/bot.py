from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import mimetypes
import pathlib
import re
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


MESSAGE_UPDATE_KEYS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
)

MESSAGE_ATTACHMENT_KEYS = (
    "document",
    "photo",
    "audio",
    "voice",
    "video",
    "video_note",
    "animation",
    "sticker",
    "location",
    "contact",
)

TELEGRAM_FILE_API_ROOT = "https://api.telegram.org/file/bot"
TELEGRAM_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
TELEGRAM_SEND_FILE_MAX_BYTES = 50 * 1024 * 1024
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_TEXT_LIMIT = 3300
log = logging.getLogger("kdcube.integrations.telegram")


@dataclass(frozen=True)
class TelegramMessage:
    kind: str
    text: str
    files: tuple[dict[str, Any], ...] = ()
    parse_mode: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "files": list(self.files), "parse_mode": self.parse_mode}


def summarize_telegram_update(update: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact, log-safe summary of a Telegram webhook update."""
    update_type = _detect_update_type(update)
    carrier = update.get(update_type) if update_type else None
    message = _message_from_carrier(carrier)

    chat = message.get("chat") if isinstance(message, Mapping) else {}
    sender = message.get("from") if isinstance(message, Mapping) else {}

    return {
        "update_id": update.get("update_id"),
        "update_type": update_type,
        "message_id": message.get("message_id") if isinstance(message, Mapping) else None,
        "chat_id": chat.get("id") if isinstance(chat, Mapping) else None,
        "chat_type": chat.get("type") if isinstance(chat, Mapping) else None,
        "user_id": sender.get("id") if isinstance(sender, Mapping) else None,
        "username": sender.get("username") if isinstance(sender, Mapping) else None,
        "text": _message_text(message),
        "attachments": _message_attachments(message),
    }


def _detect_update_type(update: Mapping[str, Any]) -> str | None:
    for key in MESSAGE_UPDATE_KEYS:
        if key in update:
            return key
    for key in update.keys():
        if key != "update_id":
            return str(key)
    return None


def _message_from_carrier(carrier: Any) -> Mapping[str, Any]:
    if not isinstance(carrier, Mapping):
        return {}
    if "message" in carrier and isinstance(carrier.get("message"), Mapping):
        return carrier["message"]
    return carrier


def _message_text(message: Mapping[str, Any]) -> str | None:
    if not isinstance(message, Mapping):
        return None
    value = message.get("text") or message.get("caption") or message.get("data")
    return str(value) if value is not None else None


def _message_attachments(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(message, Mapping):
        return []

    attachments: list[dict[str, Any]] = []
    for key in MESSAGE_ATTACHMENT_KEYS:
        value = message.get(key)
        if value is None:
            continue
        item: dict[str, Any] = {"type": key}
        if key == "photo" and isinstance(value, list):
            item["count"] = len(value)
            if value:
                selected = value[-1] if isinstance(value[-1], Mapping) else {}
                item["file_id"] = selected.get("file_id")
                item["file_unique_id"] = selected.get("file_unique_id")
                item["file_size"] = selected.get("file_size")
                item["width"] = selected.get("width")
                item["height"] = selected.get("height")
                item["mime_type"] = "image/jpeg"
        elif isinstance(value, Mapping):
            item["file_id"] = value.get("file_id")
            item["file_unique_id"] = value.get("file_unique_id")
            item["file_name"] = value.get("file_name")
            item["mime_type"] = value.get("mime_type")
            item["file_size"] = value.get("file_size")
        attachments.append({k: v for k, v in item.items() if v is not None})
    return attachments


async def hydrate_telegram_attachments(
    *,
    attachments: list[dict[str, Any]],
    bot_token: str,
    message_id: str | int | None = None,
) -> list[dict[str, Any]]:
    """Download Telegram file_id attachments into the normal bundle attachment shape."""
    if not attachments:
        return []
    token = str(bot_token or "").strip()
    hydrated: list[dict[str, Any]] = []
    for index, raw in enumerate(attachments):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        file_id = str(item.get("file_id") or "").strip()
        if not file_id:
            hydrated.append(item)
            continue
        if not token:
            item["error"] = "telegram_bot_token_missing"
            hydrated.append(item)
            continue
        declared_size = _as_int(item.get("file_size"))
        if declared_size and declared_size > TELEGRAM_ATTACHMENT_MAX_BYTES:
            item["error"] = f"telegram_file_too_large: {declared_size} > {TELEGRAM_ATTACHMENT_MAX_BYTES}"
            hydrated.append(item)
            continue
        try:
            file_info = await asyncio.to_thread(
                _telegram_api_json,
                bot_token=token,
                method="getFile",
                data={"file_id": file_id},
            )
            result = file_info.get("result") if isinstance(file_info, Mapping) else {}
            file_path = str(result.get("file_path") or "").strip() if isinstance(result, Mapping) else ""
            file_size = _as_int(result.get("file_size") if isinstance(result, Mapping) else None) or declared_size
            if file_size and file_size > TELEGRAM_ATTACHMENT_MAX_BYTES:
                item["error"] = f"telegram_file_too_large: {file_size} > {TELEGRAM_ATTACHMENT_MAX_BYTES}"
                hydrated.append(item)
                continue
            if not file_path:
                item["error"] = "telegram_file_path_missing"
                hydrated.append(item)
                continue
            data = await asyncio.to_thread(_download_telegram_file, bot_token=token, file_path=file_path)
        except Exception as exc:
            item["error"] = f"telegram_download_failed: {exc}"
            hydrated.append(item)
            continue
        if len(data) > TELEGRAM_ATTACHMENT_MAX_BYTES:
            item["error"] = f"telegram_file_too_large: {len(data)} > {TELEGRAM_ATTACHMENT_MAX_BYTES}"
            hydrated.append(item)
            continue
        filename = str(item.get("file_name") or "").strip()
        if not filename:
            suffix = pathlib.PurePosixPath(file_path).suffix
            if not suffix and str(item.get("type") or "").strip() == "photo":
                suffix = ".jpg"
            filename = pathlib.PurePosixPath(file_path).name or f"telegram_attachment_{message_id or 'message'}_{index + 1}{suffix}"
        mime = str(item.get("mime_type") or "").strip()
        if not mime:
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        item.update(
            {
                "filename": filename,
                "mime": mime,
                "mime_type": mime,
                "size": len(data),
                "size_bytes": len(data),
                "base64": base64.b64encode(data).decode("ascii"),
                "telegram_file_path": file_path,
                "summary": str(item.get("summary") or f"Telegram {item.get('type') or 'file'} attachment").strip(),
            }
        )
        item.pop("file_name", None)
        hydrated.append(item)
    return hydrated


def render_telegram_messages_from_timeline(
    *,
    timeline: Mapping[str, Any] | None = None,
    react_turn: Mapping[str, Any] | None = None,
    exclude_file_keys: set[str] | None = None,
    prefer_react_turn_answer: bool = False,
) -> list[TelegramMessage]:
    """Render a Telegram-safe response from timeline-like turn output."""
    timeline_payload = _timeline_payload(timeline or {})
    texts: list[str] = []
    if prefer_react_turn_answer and isinstance(react_turn, Mapping):
        text = str(react_turn.get("answer") or react_turn.get("final_answer") or "").strip()
        if text:
            texts = [text]
    if not texts:
        texts = _answer_texts_from_timeline(timeline_payload)
    if not texts and isinstance(react_turn, Mapping):
        text = str(react_turn.get("answer") or react_turn.get("final_answer") or "").strip()
        if text:
            texts = [text]
    messages = [
        TelegramMessage(kind="text", text=_markdown_to_telegram_html(chunk), parse_mode="HTML")
        for text in texts
        for chunk in _split_text_for_telegram(text)
        if chunk
    ]

    sources_text = _sources_text_from_timeline(timeline_payload, answer_texts=texts)
    if sources_text:
        messages.append(TelegramMessage(kind="text", text=_markdown_to_telegram_html(sources_text), parse_mode="HTML"))

    skipped_file_keys = set(str(item or "").strip() for item in (exclude_file_keys or set()) if str(item or "").strip())
    for file_item in _artifact_files_from_timeline(timeline_payload):
        if _file_delivery_key(file_item) in skipped_file_keys:
            continue
        messages.append(
            TelegramMessage(
                kind=_telegram_file_kind(file_item),
                text=_telegram_text(str(file_item.get("description") or file_item.get("filename") or "")),
                files=(file_item,),
            )
        )
    log.info(
        "[telegram.render] turn_id=%s texts=%s files=%s messages=%s file_items=%s",
        _timeline_turn_id(timeline_payload),
        len(texts),
        sum(1 for message in messages if message.files),
        len(messages),
        [
            {
                "filename": file_item.get("filename"),
                "mime_type": file_item.get("mime_type"),
                "size_bytes": file_item.get("size_bytes"),
                "logical_path": file_item.get("logical_path"),
                "hosted_uri": file_item.get("hosted_uri"),
                "rn": file_item.get("rn"),
                "key": file_item.get("key"),
            }
            for message in messages
            for file_item in (message.files or ())
        ],
    )
    return messages


def _timeline_payload(timeline: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(timeline, Mapping):
        return {}
    payload = timeline.get("payload")
    if isinstance(payload, Mapping) and isinstance(payload.get("blocks"), list):
        out = dict(payload)
        for key in ("tenant", "project", "user", "user_id", "conversation_id", "turn_id", "bundle_id"):
            if key not in out and timeline.get(key) is not None:
                out[key] = timeline[key]
        return out
    if isinstance(timeline.get("blocks"), list):
        return timeline
    text_payload = _json_object(timeline.get("text"))
    if isinstance(text_payload.get("blocks"), list):
        return text_payload
    return timeline


async def send_telegram_messages(
    *,
    bot_token: str,
    chat_id: str | int,
    messages: list[TelegramMessage],
) -> dict[str, Any]:
    if not str(bot_token or "").strip():
        return {"ok": False, "error": "telegram bot token is not configured", "sent": 0}
    if not str(chat_id or "").strip():
        return {"ok": False, "error": "telegram chat id is unavailable", "sent": 0}

    sent: list[dict[str, Any]] = []
    log.info(
        "[telegram.send] start chat_id=%s messages=%s files=%s",
        chat_id,
        len(messages),
        sum(1 for message in messages if message.files),
    )
    for message in messages:
        method = "sendMessage"
        parse_mode = str(getattr(message, "parse_mode", "") or "").strip()
        data = {
            "chat_id": str(chat_id),
            "text": message.text,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        log.info(
            "[telegram.send] message kind=%s text_chars=%s files=%s",
            message.kind,
            len(message.text or ""),
            len(message.files or ()),
        )
        if message.kind in {"document", "photo"}:
            file_item = message.files[0] if message.files else {}
            file_url = _public_file_url(file_item)
            if file_url:
                log.info(
                    "[telegram.send] using public file url method=%s filename=%s url=%s",
                    "sendPhoto" if message.kind == "photo" else "sendDocument",
                    file_item.get("filename"),
                    file_url,
                )
                method = "sendPhoto" if message.kind == "photo" else "sendDocument"
                data = {
                    "chat_id": str(chat_id),
                    "caption": message.text[:1024],
                    "photo" if message.kind == "photo" else "document": file_url,
                }
                if parse_mode:
                    data["parse_mode"] = parse_mode
            else:
                upload = await _telegram_upload_from_file_item(file_item)
                if upload:
                    method = "sendPhoto" if message.kind == "photo" else "sendDocument"
                    log.info(
                        "[telegram.send] uploading file method=%s filename=%s mime_type=%s bytes=%s",
                        method,
                        upload.get("filename"),
                        upload.get("mime_type"),
                        len(upload.get("data") or b""),
                    )
                    fields = {
                        "chat_id": str(chat_id),
                    }
                    if message.text:
                        fields["caption"] = message.text[:1024]
                    if parse_mode:
                        fields["parse_mode"] = parse_mode
                    try:
                        response = await asyncio.to_thread(
                            _post_telegram_multipart,
                            bot_token=bot_token,
                            method=method,
                            data=fields,
                            file_field="photo" if message.kind == "photo" else "document",
                            filename=upload["filename"],
                            file_data=upload["data"],
                            mime_type=upload["mime_type"],
                        )
                    except Exception as exc:
                        log.exception("[telegram.send] multipart upload exception filename=%s", upload.get("filename"))
                        return {"ok": False, "error": str(exc), "sent": len(sent), "responses": sent}
                    sent.append(response)
                    log.info(
                        "[telegram.send] multipart response method=%s filename=%s ok=%s response=%s",
                        method,
                        upload.get("filename"),
                        response.get("ok"),
                        _safe_telegram_response(response),
                    )
                    if not response.get("ok"):
                        return {"ok": False, "error": response, "sent": len(sent), "responses": sent}
                    continue
                log.warning(
                    "[telegram.send] file upload unavailable filename=%s logical_path=%s hosted_uri=%s key=%s",
                    file_item.get("filename"),
                    file_item.get("logical_path"),
                    file_item.get("hosted_uri"),
                    file_item.get("key"),
                )
                data["text"] = _telegram_text(
                    "\n".join(
                        part
                        for part in (
                            "Generated file is not yet publicly available for Telegram delivery.",
                            str(file_item.get("filename") or "").strip(),
                            str(file_item.get("logical_path") or "").strip(),
                        )
                        if part
                    )
                )
        try:
            response = await asyncio.to_thread(
                _post_telegram_form,
                bot_token=bot_token,
                method=method,
                data=data,
            )
        except Exception as exc:
            log.exception("[telegram.send] form send exception method=%s", method)
            return {"ok": False, "error": str(exc), "sent": len(sent), "responses": sent}
        sent.append(response)
        log.info(
            "[telegram.send] form response method=%s ok=%s response=%s",
            method,
            response.get("ok"),
            _safe_telegram_response(response),
        )
        if not response.get("ok"):
            return {"ok": False, "error": response, "sent": len(sent), "responses": sent}
    return {"ok": True, "sent": len(sent), "responses": sent}


async def edit_telegram_text_message(
    *,
    bot_token: str,
    chat_id: str | int,
    message_id: str | int,
    text: str,
    parse_mode: str = "",
) -> dict[str, Any]:
    if not str(bot_token or "").strip():
        return {"ok": False, "error": "telegram bot token is not configured"}
    if not str(chat_id or "").strip():
        return {"ok": False, "error": "telegram chat id is unavailable"}
    if not str(message_id or "").strip():
        return {"ok": False, "error": "telegram message id is unavailable"}
    payload = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "text": _telegram_message_text(text, parse_mode=parse_mode),
        "disable_web_page_preview": "true",
    }
    parse_mode = str(parse_mode or "").strip()
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        response = await asyncio.to_thread(
            _post_telegram_form,
            bot_token=bot_token,
            method="editMessageText",
            data=payload,
        )
    except Exception as exc:
        log.exception("[telegram.edit] form edit exception message_id=%s", message_id)
        return {"ok": False, "error": str(exc)}
    log.info(
        "[telegram.edit] form response ok=%s response=%s",
        response.get("ok"),
        _safe_telegram_response(response),
    )
    return response


def _answer_texts_from_timeline(timeline: Mapping[str, Any]) -> list[str]:
    blocks = timeline.get("blocks") if isinstance(timeline, Mapping) else None
    if not isinstance(blocks, list):
        return []
    turn_id = _timeline_turn_id(timeline)
    texts: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if not _matches_timeline_turn(block, turn_id):
            continue
        path = str(block.get("path") or "")
        block_type = str(block.get("type") or "")
        marker = str(block.get("marker") or "")
        if block_type.startswith("react.tool."):
            continue
        owner_turn_id = _path_turn_id(path)
        if turn_id and owner_turn_id and owner_turn_id != turn_id:
            continue
        if (
            "react.final_answer" not in path
            and "assistant.completion" not in path
            and block_type not in {"answer", "final_answer", "assistant.completion"}
            and marker != "answer"
        ):
            continue
        text = str(block.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def _split_text_for_telegram(text: str, *, limit: int = TELEGRAM_SAFE_TEXT_LIMIT) -> list[str]:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return []
    limit = max(1000, min(int(limit or TELEGRAM_SAFE_TEXT_LIMIT), TELEGRAM_MESSAGE_LIMIT - 300))
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    current = ""
    paragraphs = re.split(r"\n{2,}", value)
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > limit:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_paragraph_for_telegram(paragraph, limit=limit))
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph
    if current:
        chunks.append(current.strip())
    return chunks


def _split_long_paragraph_for_telegram(paragraph: str, *, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    lines = str(paragraph or "").split("\n")
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if len(line) > limit:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(line), limit):
                part = line[start:start + limit].strip()
                if part:
                    chunks.append(part)
            continue
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = line
    if current:
        chunks.append(current.strip())
    return chunks


def _sources_text_from_timeline(timeline: Mapping[str, Any], *, answer_texts: list[str]) -> str:
    sources_pool = timeline.get("sources_pool") if isinstance(timeline, Mapping) else None
    if not isinstance(sources_pool, list):
        return ""
    wanted = _source_ids_from_texts(answer_texts)
    if not wanted:
        return ""
    rows: list[Mapping[str, Any]] = []
    for row in sources_pool:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("source_type") or "").strip().lower() == "file":
            continue
        url = str(row.get("url") or row.get("href") or "").strip()
        if not url:
            continue
        sid = _as_int(row.get("sid"))
        if wanted and sid not in wanted:
            continue
        rows.append(row)
        if len(rows) >= 5:
            break
    if not rows:
        return ""
    lines = ["Sources:"]
    for row in rows:
        sid = _as_int(row.get("sid"))
        title = str(row.get("title") or row.get("url") or "").strip()
        url = str(row.get("url") or row.get("href") or "").strip()
        prefix = f"{sid}. " if sid is not None else "- "
        lines.append(f"{prefix}{title} - {url}" if title and title != url else f"{prefix}{url}")
    return "\n".join(lines)


def _artifact_files_from_timeline(timeline: Mapping[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    turn_id = _timeline_turn_id(timeline)

    sources_pool = timeline.get("sources_pool") if isinstance(timeline, Mapping) else None
    if isinstance(sources_pool, list):
        for row in sources_pool:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("source_type") or "").strip().lower() != "file":
                continue
            if not _matches_timeline_turn(row, turn_id):
                continue
            file_item = _file_item_from_meta(dict(row), logical_path=str(row.get("artifact_path") or ""))
            _append_file(files, seen, file_item)

    blocks = timeline.get("blocks") if isinstance(timeline, Mapping) else None
    if not isinstance(blocks, list):
        return files
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        path = str(block.get("path") or "").strip()
        if not path.startswith("fi:"):
            continue
        if not _matches_timeline_turn(block, turn_id):
            continue
        if ".user.attachments/" in path or ".external." in path:
            continue
        meta = _merged_block_file_meta(block)
        visibility = str(meta.get("visibility") or block.get("visibility") or "external").strip().lower()
        if visibility == "internal":
            continue
        if not (".files/" in path or ".outputs/" in path or meta.get("hosted_uri") or meta.get("url")):
            continue
        logical_path = str(meta.get("artifact_path") or path).strip()
        file_item = _file_item_from_meta(meta, logical_path=logical_path)
        _append_file(files, seen, file_item)
    return files


def _append_file(files: list[dict[str, Any]], seen: set[str], file_item: dict[str, Any]) -> None:
    if not file_item:
        return
    key = _file_delivery_key(file_item)
    if not key or key in seen:
        return
    seen.add(key)
    files.append(file_item)


def _file_delivery_key(file_item: Mapping[str, Any]) -> str:
    return str(
        file_item.get("url")
        or file_item.get("hosted_uri")
        or file_item.get("rn")
        or file_item.get("key")
        or file_item.get("logical_path")
        or file_item.get("physical_path")
        or file_item.get("filename")
        or ""
    ).strip()


def _file_item_from_meta(meta: Mapping[str, Any], *, logical_path: str) -> dict[str, Any]:
    url = str(
        meta.get("hosted_uri")
        or meta.get("url")
        or meta.get("rn")
        or meta.get("key")
        or ""
    ).strip()
    physical_path = str(meta.get("physical_path") or meta.get("local_path") or "").strip()
    filename = str(meta.get("filename") or "").strip()
    if not filename:
        filename = pathlib.PurePosixPath(physical_path or logical_path or url).name
    mime_type = str(meta.get("mime_type") or meta.get("mime") or "").strip()
    description = str(meta.get("description") or meta.get("title") or filename or "").strip()
    return {
        key: value
        for key, value in {
            "filename": filename,
            "mime_type": mime_type,
            "description": description,
            "logical_path": logical_path,
            "physical_path": physical_path,
            "url": url,
            "hosted_uri": str(meta.get("hosted_uri") or "").strip(),
            "rn": str(meta.get("rn") or "").strip(),
            "key": str(meta.get("key") or "").strip(),
            "base64": str(meta.get("base64") or "").strip(),
            "size_bytes": meta.get("size_bytes"),
        }.items()
        if value not in ("", None)
    }


def _telegram_file_kind(file_item: Mapping[str, Any]) -> str:
    mime = str(file_item.get("mime_type") or "").strip().lower()
    return "photo" if mime.startswith("image/") else "document"


def _public_file_url(file_item: Mapping[str, Any]) -> str:
    for key in ("url", "hosted_uri"):
        value = str(file_item.get(key) or "").strip()
        if value.startswith(("https://", "http://")):
            return value
    return ""


def _safe_telegram_response(response: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    out = {
        "ok": response.get("ok"),
        "error_code": response.get("error_code"),
        "description": response.get("description"),
    }
    result = response.get("result")
    if isinstance(result, Mapping):
        document = result.get("document") if isinstance(result.get("document"), Mapping) else {}
        photo = result.get("photo") if isinstance(result.get("photo"), list) else []
        out["result"] = {
            "message_id": result.get("message_id"),
            "date": result.get("date"),
            "has_document": bool(document),
            "document_file_name": document.get("file_name") if isinstance(document, Mapping) else None,
            "document_mime_type": document.get("mime_type") if isinstance(document, Mapping) else None,
            "photo_count": len(photo),
        }
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _merged_block_file_meta(block: Mapping[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    raw_meta = block.get("meta")
    if isinstance(raw_meta, Mapping):
        digest = _json_object(raw_meta.get("digest"))
        if digest:
            meta.update(digest)
        meta.update(dict(raw_meta))
    for key in ("mime", "mime_type", "visibility", "filename", "size_bytes"):
        if block.get(key) not in ("", None) and key not in meta:
            meta[key] = block[key]
    text_meta = _json_object(block.get("text"))
    if text_meta:
        meta.update(text_meta)
    return meta


def _timeline_turn_id(timeline: Mapping[str, Any]) -> str:
    if not isinstance(timeline, Mapping):
        return ""
    return str(timeline.get("turn_id") or timeline.get("current_turn_id") or "").strip()


def _path_turn_id(path: str) -> str:
    value = str(path or "").strip()
    if not value or ":" not in value:
        return ""
    head = value.split(":", 1)[1]
    return head.split(".", 1)[0].split("/", 1)[0].strip()


def _matches_timeline_turn(item: Mapping[str, Any], turn_id: str) -> bool:
    if not turn_id or not isinstance(item, Mapping):
        return True
    meta = item.get("meta")
    item_turn = str(item.get("turn_id") or (meta.get("turn_id") if isinstance(meta, Mapping) else "") or "").strip()
    if item_turn:
        return item_turn == turn_id
    values = [
        str(item.get(key) or "")
        for key in ("path", "artifact_path", "logical_path", "physical_path", "hosted_uri", "url", "rn", "key")
    ]
    if isinstance(meta, Mapping):
        values.extend(
            str(meta.get(key) or "")
            for key in ("artifact_path", "logical_path", "physical_path", "hosted_uri", "url", "rn", "key")
        )
    return any(turn_id in value for value in values)


async def _telegram_upload_from_file_item(file_item: Mapping[str, Any]) -> dict[str, Any] | None:
    data = await _file_item_bytes(file_item)
    if not data:
        log.warning(
            "[telegram.file] no bytes resolved filename=%s logical_path=%s hosted_uri=%s key=%s physical_path=%s",
            file_item.get("filename"),
            file_item.get("logical_path"),
            file_item.get("hosted_uri"),
            file_item.get("key"),
            file_item.get("physical_path"),
        )
        return None
    if len(data) > TELEGRAM_SEND_FILE_MAX_BYTES:
        log.warning(
            "[telegram.file] file too large filename=%s bytes=%s max=%s",
            file_item.get("filename"),
            len(data),
            TELEGRAM_SEND_FILE_MAX_BYTES,
        )
        return None
    filename = _file_item_filename(file_item)
    mime_type = str(file_item.get("mime_type") or file_item.get("mime") or "").strip()
    if not mime_type:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {"filename": filename, "mime_type": mime_type, "data": data}


async def _file_item_bytes(file_item: Mapping[str, Any]) -> bytes | None:
    inline = file_item.get("base64") or file_item.get("data_base64") or file_item.get("content_base64")
    if isinstance(inline, str) and inline.strip():
        try:
            data = base64.b64decode(inline.strip(), validate=False)
            log.info("[telegram.file] resolved inline base64 filename=%s bytes=%s", file_item.get("filename"), len(data))
            return data
        except Exception:
            log.exception("[telegram.file] failed inline base64 decode filename=%s", file_item.get("filename"))
            return None

    candidates = []
    for key in ("hosted_uri", "url", "key", "physical_path", "local_path"):
        value = str(file_item.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    for candidate in candidates:
        data = _read_local_file_candidate(candidate)
        if data is not None:
            log.info(
                "[telegram.file] resolved local candidate filename=%s candidate=%s bytes=%s",
                file_item.get("filename"),
                candidate,
                len(data),
            )
            return data
        data = await _read_storage_blob_candidate(candidate)
        if data is not None:
            log.info(
                "[telegram.file] resolved storage candidate filename=%s candidate=%s bytes=%s",
                file_item.get("filename"),
                candidate,
                len(data),
            )
            return data
        log.debug("[telegram.file] candidate unresolved filename=%s candidate=%s", file_item.get("filename"), candidate)
    return None


def _read_local_file_candidate(candidate: str) -> bytes | None:
    value = str(candidate or "").strip()
    if not value:
        return None
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme == "file":
            if parsed.netloc and parsed.netloc != "localhost":
                path = pathlib.Path(urllib.parse.unquote(f"/{parsed.netloc}{parsed.path}"))
            else:
                path = pathlib.Path(urllib.parse.unquote(parsed.path))
        elif not parsed.scheme and pathlib.Path(value).is_absolute():
            path = pathlib.Path(value)
        else:
            return None
        if not path.is_file():
            return None
        return path.read_bytes()
    except Exception:
        return None


async def _read_storage_blob_candidate(candidate: str) -> bytes | None:
    value = str(candidate or "").strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "rn:")):
        return None
    try:
        from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

        store = ConversationStore()
        return await store.get_blob_bytes(value)
    except Exception:
        return None


def _file_item_filename(file_item: Mapping[str, Any]) -> str:
    filename = str(file_item.get("filename") or "").strip()
    if filename:
        return filename
    for key in ("physical_path", "local_path", "hosted_uri", "url", "key", "logical_path"):
        value = str(file_item.get(key) or "").strip()
        if not value:
            continue
        parsed = urllib.parse.urlparse(value)
        path_value = urllib.parse.unquote(parsed.path) if parsed.scheme else value
        name = pathlib.PurePosixPath(path_value).name
        if name:
            return name
    return "artifact.bin"


def _source_ids_from_texts(texts: list[str]) -> set[int]:
    ids: set[int] = set()
    for text in texts or []:
        for raw in re.findall(r"\[(?:source\s*)?(\d+)\]", str(text or ""), flags=re.IGNORECASE):
            value = _as_int(raw)
            if value is not None:
                ids.add(value)
    return ids


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _telegram_text(text: str) -> str:
    value = _markdown_to_telegram_text(str(text or "")).strip()
    if len(value) <= 4096:
        return value
    return value[:3900].rstrip() + "\n\n[truncated]"


def _telegram_message_text(text: str, *, parse_mode: str = "") -> str:
    if not str(parse_mode or "").strip():
        return _telegram_text(text)
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) <= 4096:
        return value
    return value[:3900].rstrip()


def _markdown_to_telegram_html(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""
    rendered: list[str] = []
    lines = value.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            rendered.append(f"<pre>{html.escape(chr(10).join(code_lines), quote=False)}</pre>")
            continue
        if _is_markdown_table_line(stripped):
            table_lines: list[str] = []
            while index < len(lines) and _is_markdown_table_line(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            table_text = "\n".join(_render_markdown_table(table_lines))
            if table_text:
                rendered.append(f"<pre>{html.escape(table_text, quote=False)}</pre>")
            continue
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            quote_text = "\n".join(_markdown_inline_to_telegram_html(item) for item in quote_lines)
            rendered.append(f"<blockquote>{quote_text}</blockquote>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            rendered.append(f"<b>{_markdown_inline_to_telegram_html(heading.group(2))}</b>")
            index += 1
            continue
        if not stripped:
            rendered.append("")
            index += 1
            continue
        rendered.append(_markdown_inline_to_telegram_html(line.rstrip()))
        index += 1
    html_text = _compact_blank_lines("\n".join(rendered)).strip()
    return _telegram_message_text(html_text, parse_mode="HTML")


def _markdown_inline_to_telegram_html(text: str) -> str:
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return f"@@KDCUBECODE{len(code_spans) - 1}@@"

    without_code = re.sub(r"`([^`\n]+)`", _stash_code, str(text or ""))
    escaped = html.escape(without_code, quote=False)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__([^_]+)__", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", escaped)
    for index, code_html in enumerate(code_spans):
        escaped = escaped.replace(f"@@KDCUBECODE{index}@@", code_html)
    return escaped


def _markdown_to_telegram_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return ""
    lines = value.split("\n")
    rendered: list[str] = []
    index = 0
    in_code = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            index += 1
            continue
        if in_code:
            rendered.append(line.rstrip())
            index += 1
            continue
        if _is_markdown_table_line(stripped):
            table_lines: list[str] = []
            while index < len(lines) and _is_markdown_table_line(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            rendered.extend(_render_markdown_table(table_lines))
            continue
        rendered.append(_render_markdown_line(line))
        index += 1
    return _compact_blank_lines("\n".join(rendered))


def _render_markdown_line(line: str) -> str:
    text = str(line or "").rstrip()
    stripped = text.strip()
    if not stripped:
        return ""
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return ""
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"^\s*>\s?", "", text)
    text = re.sub(r"^\s*[-*+]\s+\[(?: |x|X)\]\s+", "- ", text)
    text = re.sub(r"^\s*[-*+]\s+", "- ", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"\1", text)
    return text.rstrip()


def _is_markdown_table_line(line: str) -> bool:
    if not line.startswith("|") or not line.endswith("|"):
        return False
    return line.count("|") >= 2


def _is_markdown_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in cells)


def _split_markdown_table_line(line: str) -> list[str]:
    return [_render_markdown_line(cell.strip()) for cell in line.strip().strip("|").split("|")]


def _render_markdown_table(lines: list[str]) -> list[str]:
    rows = [_split_markdown_table_line(line) for line in lines if line.strip()]
    rows = [row for row in rows if not _is_markdown_table_separator(row)]
    if not rows:
        return []
    header = rows[0]
    data_rows = rows[1:]
    if not data_rows:
        return [" | ".join(cell for cell in header if cell)]
    rendered: list[str] = []
    for row in data_rows:
        cells = [cell for cell in row]
        if not any(cells):
            continue
        label = cells[0].strip()
        body = [
            cell
            for idx, cell in enumerate(cells[1:], start=1)
            if cell and (idx >= len(header) or cell != header[idx])
        ]
        if label and label not in {"#", "No."}:
            rendered.append(f"{label}. {' - '.join(body)}" if body else label)
        else:
            rendered.append("- " + " - ".join(body or [cell for cell in cells if cell]))
    return rendered


def _compact_blank_lines(text: str) -> str:
    lines = text.split("\n")
    compacted: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            compacted.append(line.rstrip())
            continue
        blank += 1
        if blank <= 1:
            compacted.append("")
    return "\n".join(compacted).strip()


def _post_telegram_form(*, bot_token: str, method: str, data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(raw)
        except Exception:
            parsed_error = {"ok": False, "raw": raw}
        if isinstance(parsed_error, dict):
            parsed_error.setdefault("ok", False)
            parsed_error.setdefault("http_status", exc.code)
            parsed_error.setdefault("error", f"HTTP Error {exc.code}: {exc.reason}")
            return parsed_error
        return {
            "ok": False,
            "http_status": exc.code,
            "error": f"HTTP Error {exc.code}: {exc.reason}",
            "raw": parsed_error,
        }
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {"ok": False, "raw": raw}
    return parsed if isinstance(parsed, dict) else {"ok": False, "raw": parsed}


def _post_telegram_multipart(
    *,
    bot_token: str,
    method: str,
    data: dict[str, str],
    file_field: str,
    filename: str,
    file_data: bytes,
    mime_type: str,
) -> dict[str, Any]:
    boundary = f"kdcube-{uuid.uuid4().hex}"
    body = bytearray()

    def add_part(headers: list[str], payload: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        for header in headers:
            body.extend(header.encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(b"\r\n")
        body.extend(payload)
        body.extend(b"\r\n")

    for key, value in data.items():
        add_part(
            [f'Content-Disposition: form-data; name="{key}"'],
            str(value).encode("utf-8"),
        )
    safe_filename = pathlib.PurePath(filename or "artifact.bin").name or "artifact.bin"
    add_part(
        [
            f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_filename}"',
            f"Content-Type: {mime_type or 'application/octet-stream'}",
        ],
        file_data,
    )
    body.extend(f"--{boundary}--\r\n".encode("ascii"))

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {"ok": False, "raw": raw}
    return parsed if isinstance(parsed, dict) else {"ok": False, "raw": parsed}


def _telegram_api_json(*, bot_token: str, method: str, data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("invalid Telegram API response")
    if not parsed.get("ok"):
        raise RuntimeError(str(parsed))
    return parsed


def _download_telegram_file(*, bot_token: str, file_path: str) -> bytes:
    quoted_path = "/".join(urllib.parse.quote(part) for part in str(file_path or "").split("/"))
    request = urllib.request.Request(f"{TELEGRAM_FILE_API_ROOT}{bot_token}/{quoted_path}", method="GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(TELEGRAM_ATTACHMENT_MAX_BYTES + 1)

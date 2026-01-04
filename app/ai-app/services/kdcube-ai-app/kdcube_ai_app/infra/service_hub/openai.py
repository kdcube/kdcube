# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/openai.py

import asyncio
import base64
import hashlib
import io
from typing import Optional, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI

from kdcube_ai_app.infra.service_hub.message_utils import (
    extract_message_blocks,
    normalize_blocks,
    blocks_to_text,
)
from kdcube_ai_app.tools.content_type import extension_from_mime

_OPENAI_FILE_CACHE: dict[str, str] = {}


async def _openai_upload_document(
    client: ChatOpenAI,
    *,
    data_b64: str,
    media_type: str,
) -> Optional[str]:
    if not data_b64:
        return None

    cache_key = hashlib.sha256((media_type + ":" + data_b64).encode("utf-8")).hexdigest()
    cached = _OPENAI_FILE_CACHE.get(cache_key)
    if cached:
        return cached

    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return None

    ext = extension_from_mime(media_type)
    filename = f"attachment-{cache_key[:10]}.{ext}"
    fobj = io.BytesIO(raw)
    fobj.name = filename

    async_client = getattr(client, "root_async_client", None)
    sync_client = getattr(client, "root_client", None)
    if async_client is not None:
        created = await async_client.files.create(file=fobj, purpose="assistants")
    elif sync_client is not None:
        created = await asyncio.to_thread(sync_client.files.create, file=fobj, purpose="assistants")
    else:
        return None

    file_id = getattr(created, "id", None)
    if not file_id and isinstance(created, dict):
        file_id = created.get("id")
    if file_id:
        _OPENAI_FILE_CACHE[cache_key] = file_id
    return file_id


async def normalize_message_for_openai(
    client: ChatOpenAI,
    msg: BaseMessage,
) -> BaseMessage:
    blocks = extract_message_blocks(msg)
    if not blocks:
        return msg

    norm = normalize_blocks(blocks)
    text_only = blocks_to_text(blocks)

    if isinstance(msg, SystemMessage):
        return SystemMessage(content=text_only)
    if isinstance(msg, AIMessage):
        return AIMessage(content=text_only)

    if not isinstance(msg, HumanMessage):
        try:
            return type(msg)(content=text_only)
        except Exception:
            return msg

    content_parts: list[dict] = []
    for b in norm:
        btype = b.get("type", "text")
        if btype == "text":
            content_parts.append({"type": "text", "text": b.get("text", "")})
            continue
        if btype in ("image_url", "input_image"):
            content_parts.append(b)
            continue
        if btype == "image":
            src = b.get("source") or {}
            data = src.get("data")
            media_type = src.get("media_type") or "image/png"
            if data:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            else:
                content_parts.append({"type": "text", "text": f"[image omitted: {media_type}]"})
            continue
        if btype == "document":
            src = b.get("source") or {}
            data = src.get("data")
            media_type = src.get("media_type") or "application/pdf"
            file_id = await _openai_upload_document(client, data_b64=data, media_type=media_type)
            if file_id:
                content_parts.append({"type": "file", "file": {"file_id": file_id}})
            else:
                content_parts.append({"type": "text", "text": f"[document omitted: {media_type}]"})
            continue

        content_parts.append({"type": "text", "text": str(b)})

    if all(p.get("type") == "text" for p in content_parts):
        return HumanMessage(content=text_only)
    return HumanMessage(content=content_parts)


async def normalize_messages_for_openai(
    client: ChatOpenAI,
    messages: List[BaseMessage],
) -> List[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        out.append(await normalize_message_for_openai(client, m))
    return out

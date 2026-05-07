from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.ingress.chat_core import IngressConfig, RawAttachment
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType


def telegram_command_kind_and_text(text: str) -> tuple[str, str]:
    """Return the chat continuation kind encoded by Telegram slash commands."""
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return "", raw
    first, sep, rest = raw.partition(" ")
    command = first.split("@", 1)[0].strip().lower()
    if command in {"/steer", "/s"}:
        return "steer", rest.strip() if sep else ""
    if command in {"/followup", "/f"}:
        return "followup", rest.strip() if sep else ""
    return "", raw


def role_to_user_type(role: str) -> UserType:
    normalized = str(role or "").strip().lower()
    if normalized == "admin":
        return UserType.PRIVILEGED
    if normalized == "registered":
        return UserType.REGISTERED
    return UserType.ANONYMOUS


def raw_attachments_from_telegram(attachments: list[Mapping[str, Any]] | None) -> list[RawAttachment]:
    """Convert hydrated Telegram attachment dictionaries into chat-core attachments."""
    raw_attachments: list[RawAttachment] = []
    for index, item in enumerate(attachments or []):
        if not isinstance(item, Mapping):
            continue
        data = decode_inline_attachment_bytes(item)
        if data is None:
            continue
        filename = (
            str(item.get("filename") or item.get("file_name") or "").strip()
            or f"telegram_attachment_{index + 1}.bin"
        )
        mime = str(item.get("mime_type") or item.get("mime") or "").strip() or "application/octet-stream"
        meta = {
            str(k): v
            for k, v in item.items()
            if k not in {"base64", "filename", "file_name", "mime", "mime_type"}
        }
        meta["origin"] = "telegram"
        raw_attachments.append(RawAttachment(content=data, name=filename, mime=mime, meta=meta))
    return raw_attachments


def decode_inline_attachment_bytes(item: Mapping[str, Any]) -> bytes | None:
    value = item.get("base64")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return None


def telegram_request_context(*, timezone: str = "UTC") -> RequestContext:
    return RequestContext(
        client_ip="telegram",
        user_agent="Telegram Bot API",
        user_timezone=str(timezone or "UTC"),
    )


def telegram_user_session(
    *,
    conversation_id: str,
    user_id: str,
    username: str = "",
    role: str = "anonymous",
    request_context: RequestContext | None = None,
) -> UserSession:
    ctx = request_context or telegram_request_context()
    normalized_role = str(role or "anonymous").strip().lower() or "anonymous"
    return UserSession(
        session_id=str(conversation_id or "").strip(),
        user_type=role_to_user_type(normalized_role),
        user_id=str(user_id or "").strip(),
        username=str(username or "").strip(),
        roles=[normalized_role],
        permissions=[],
        timezone=ctx.user_timezone,
        request_context=ctx,
    )


def telegram_ingress_config(
    *,
    chat_id: str,
    update_id: str,
    message_id: str | int | None = None,
    entrypoint: str = "/telegram/webhook",
    component: str = "chat.telegram",
    instance_id: str = "telegram-webhook",
) -> IngressConfig:
    return IngressConfig(
        transport="telegram",
        entrypoint=entrypoint,
        component=component,
        instance_id=str(instance_id or "telegram-webhook"),
        stream_id=None,
        metadata={
            "source": "telegram",
            "chat_id": str(chat_id or "").strip(),
            "update_id": str(update_id or "").strip(),
            "message_id": message_id,
            "entrypoint": entrypoint,
        },
    )


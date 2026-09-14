from __future__ import annotations

import asyncio
import logging
from typing import Any

from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import (
    stable_secret_key,
)

log = logging.getLogger("kdcube.integrations.telegram.topics")


def normalize_message_thread_id(value: Any) -> str:
    """Return Telegram's positive topic identifier as a decimal string."""
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    return str(number) if number > 0 else ""


def telegram_topic_conversation_id(
    *,
    chat_id: str | int,
    message_thread_id: str | int,
    integration_id: str = "",
) -> str:
    """Build the stable conversation id owned by one configured bot topic."""
    try:
        chat = str(int(str(chat_id).strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError("telegram chat_id must be an integer") from exc
    thread = normalize_message_thread_id(message_thread_id)
    if chat == "0":
        raise ValueError("telegram chat_id must be non-zero")
    if not thread:
        raise ValueError("telegram message_thread_id must be a positive integer")
    integration = str(integration_id or "").strip()
    if integration:
        return (
            f"telegram_integration_{stable_secret_key(integration)}_"
            f"chat_{chat}_topic_{thread}"
        )
    return f"telegram_chat_{chat}_topic_{thread}"


def _validate_topic_name(name: str, *, required: bool) -> str:
    value = str(name or "").strip()
    if required and not value:
        raise ValueError("telegram topic name is required")
    if len(value) > 128:
        raise ValueError("telegram topic name must contain at most 128 characters")
    return value


async def _topic_api_call(
    *,
    bot_token: str,
    method: str,
    data: dict[str, str],
) -> dict[str, Any]:
    token = str(bot_token or "").strip()
    if not token:
        return {"ok": False, "error": "telegram bot token is not configured"}

    # Imported lazily so bot.py can use the pure topic helpers without a cycle.
    from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
        _post_telegram_form,
    )

    try:
        return await asyncio.to_thread(
            _post_telegram_form,
            bot_token=token,
            method=method,
            data=data,
        )
    except Exception:  # noqa: BLE001 - the boundary maps every transport failure
        # urllib exceptions can contain the request URL, whose path contains the
        # bot token. Keep both logs and returned errors independent of it.
        log.warning("[telegram.topic] api call failed method=%s", method)
        return {"ok": False, "error": "Telegram topic request failed."}


async def create_telegram_topic(
    *,
    bot_token: str,
    chat_id: str | int,
    name: str,
    icon_color: int | None = None,
    icon_custom_emoji_id: str = "",
) -> dict[str, Any]:
    """Create a Telegram forum topic in a private bot chat or supergroup."""
    chat = str(chat_id or "").strip()
    if not chat:
        return {"ok": False, "error": "telegram chat id is unavailable"}
    try:
        topic_name = _validate_topic_name(name, required=True)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    data = {"chat_id": chat, "name": topic_name}
    if icon_color is not None:
        try:
            data["icon_color"] = str(int(icon_color))
        except (TypeError, ValueError):
            return {"ok": False, "error": "telegram topic icon color must be an integer"}
    emoji = str(icon_custom_emoji_id or "").strip()
    if emoji:
        data["icon_custom_emoji_id"] = emoji
    return await _topic_api_call(
        bot_token=bot_token,
        method="createForumTopic",
        data=data,
    )


async def edit_telegram_topic(
    *,
    bot_token: str,
    chat_id: str | int,
    message_thread_id: str | int,
    name: str = "",
    icon_custom_emoji_id: str | None = None,
) -> dict[str, Any]:
    """Edit a Telegram topic name or icon."""
    chat = str(chat_id or "").strip()
    thread = normalize_message_thread_id(message_thread_id)
    if not chat:
        return {"ok": False, "error": "telegram chat id is unavailable"}
    if not thread:
        return {"ok": False, "error": "telegram message thread id is unavailable"}
    try:
        topic_name = _validate_topic_name(name, required=False)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not topic_name and icon_custom_emoji_id is None:
        return {"ok": False, "error": "telegram topic name or icon is required"}

    data = {"chat_id": chat, "message_thread_id": thread}
    if topic_name:
        data["name"] = topic_name
    if icon_custom_emoji_id is not None:
        data["icon_custom_emoji_id"] = str(icon_custom_emoji_id).strip()
    return await _topic_api_call(
        bot_token=bot_token,
        method="editForumTopic",
        data=data,
    )


async def delete_telegram_topic(
    *,
    bot_token: str,
    chat_id: str | int,
    message_thread_id: str | int,
) -> dict[str, Any]:
    """Delete a Telegram topic and its messages."""
    chat = str(chat_id or "").strip()
    thread = normalize_message_thread_id(message_thread_id)
    if not chat:
        return {"ok": False, "error": "telegram chat id is unavailable"}
    if not thread:
        return {"ok": False, "error": "telegram message thread id is unavailable"}
    return await _topic_api_call(
        bot_token=bot_token,
        method="deleteForumTopic",
        data={"chat_id": chat, "message_thread_id": thread},
    )


__all__ = [
    "create_telegram_topic",
    "delete_telegram_topic",
    "edit_telegram_topic",
    "normalize_message_thread_id",
    "telegram_topic_conversation_id",
]

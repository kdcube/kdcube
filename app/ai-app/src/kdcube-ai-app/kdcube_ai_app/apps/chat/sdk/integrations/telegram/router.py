from __future__ import annotations

import logging
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
    TelegramMessage,
    render_telegram_messages_from_timeline,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import (
    deliver_messages_preserving_progress_card,
)

log = logging.getLogger("kdcube.integrations.telegram.router")


def message_log_items(messages: list[Any] | None) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for message in messages or []:
        files = list(getattr(message, "files", ()) or ())
        items.append(
            {
                "kind": getattr(message, "kind", None),
                "text_chars": len(str(getattr(message, "text", "") or "")),
                "files": [
                    {
                        "filename": file_item.get("filename"),
                        "mime_type": file_item.get("mime_type") or file_item.get("mime"),
                        "size_bytes": file_item.get("size_bytes"),
                        "logical_path": file_item.get("logical_path"),
                        "hosted_uri": file_item.get("hosted_uri"),
                        "url": file_item.get("url"),
                        "key": file_item.get("key"),
                    }
                    for file_item in files
                    if isinstance(file_item, dict)
                ],
            }
        )
    return items


async def deliver_turn_to_telegram(
    *,
    bundle_id: str,
    bot_token: str,
    chat_id: str | int,
    message_thread_id: str | int | None = None,
    update_id: str = "",
    turn_result: Dict[str, Any],
    delivered_file_keys: set[str] | None = None,
    progress_message_id: str | int | None = None,
    progress_summary: str = "",
    send_responses: bool = True,
) -> Dict[str, Any]:
    telegram_messages = render_turn_messages(
        turn_result=turn_result,
        delivered_file_keys=delivered_file_keys,
    )
    log.info(
        "[%s] telegram response rendered | update_id=%s message_thread_id=%s source=%s messages=%s files=%s details=%s",
        bundle_id,
        update_id or "",
        message_thread_id,
        (
            "turn_log"
            if isinstance(turn_result, dict) and isinstance(turn_result.get("turn_log"), dict) and turn_result.get("turn_log")
            else "timeline"
        ),
        len(telegram_messages),
        sum(1 for message in telegram_messages if message.files),
        message_log_items(telegram_messages),
    )

    telegram_delivery = None
    delivery_result: dict[str, Any] = {}
    if telegram_messages and send_responses:
        delivery_result = await deliver_messages_preserving_progress_card(
            bot_token=bot_token,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            telegram_messages=telegram_messages,
            progress_message_id=progress_message_id,
            progress_summary=progress_summary,
        )
        telegram_delivery = delivery_result.get("telegram_delivery")
        log.info(
            "[%s] telegram delivery finished | update_id=%s messages=%s sent_after_progress=%s files=%s appended_final=%s ok=%s sent=%s error=%s",
            bundle_id,
            update_id or "",
            len(telegram_messages),
            len(delivery_result.get("messages_to_send") or []),
            sum(1 for message in telegram_messages if message.files),
            delivery_result.get("progress_final_appended"),
            (telegram_delivery or {}).get("ok") if isinstance(telegram_delivery, dict) else None,
            (telegram_delivery or {}).get("sent") if isinstance(telegram_delivery, dict) else None,
            (telegram_delivery or {}).get("error") if isinstance(telegram_delivery, dict) else None,
        )
    elif telegram_messages:
        log.info(
            "[%s] telegram delivery skipped by config | update_id=%s messages=%s files=%s",
            bundle_id,
            update_id or "",
            len(telegram_messages),
            sum(1 for message in telegram_messages if message.files),
        )

    return {
        "messages": [message.as_dict() for message in telegram_messages],
        "telegram_delivery": telegram_delivery,
        "sent_after_progress": len(delivery_result.get("messages_to_send") or []),
        "progress_final_appended": bool(delivery_result.get("progress_final_appended")),
    }


def render_turn_messages(
    *,
    turn_result: Dict[str, Any],
    delivered_file_keys: set[str] | None = None,
) -> list[TelegramMessage]:
    return render_telegram_messages_from_timeline(
        timeline=(
            turn_result.get("turn_log")
            if isinstance(turn_result, dict) and isinstance(turn_result.get("turn_log"), dict) and turn_result.get("turn_log")
            else turn_result.get("timeline") if isinstance(turn_result, dict) else None
        ),
        react_turn=turn_result,
        exclude_file_keys=delivered_file_keys or set(),
        prefer_react_turn_answer=True,
    )


async def deliver_react_turn_to_telegram(
    *,
    bundle_id: str,
    bot_token: str,
    chat_id: str | int,
    message_thread_id: str | int | None = None,
    update_id: str = "",
    react_turn: Dict[str, Any],
    delivered_file_keys: set[str] | None = None,
    progress_message_id: str | int | None = None,
    progress_summary: str = "",
    send_responses: bool = True,
) -> Dict[str, Any]:
    """Compatibility wrapper for the pre-framework-neutral SDK name."""
    return await deliver_turn_to_telegram(
        bundle_id=bundle_id,
        bot_token=bot_token,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        update_id=update_id,
        turn_result=react_turn,
        delivered_file_keys=delivered_file_keys,
        progress_message_id=progress_message_id,
        progress_summary=progress_summary,
        send_responses=send_responses,
    )


def render_react_turn_messages(
    *,
    react_turn: Dict[str, Any],
    delivered_file_keys: set[str] | None = None,
) -> list[TelegramMessage]:
    """Compatibility wrapper for the pre-framework-neutral SDK name."""
    return render_turn_messages(
        turn_result=react_turn,
        delivered_file_keys=delivered_file_keys,
    )

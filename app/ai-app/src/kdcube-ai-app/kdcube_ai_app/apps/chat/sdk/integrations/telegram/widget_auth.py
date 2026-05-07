from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable

from fastapi import HTTPException
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    INIT_DATA_HEADER,
    extract_telegram_init_data_from_request,
    validate_telegram_init_data,
)

_storage_for: Callable[[Any], Any] | None = None
_bot_token: Callable[[], str] | None = None


def configure_telegram_widget_auth(
    *,
    storage_for: Callable[[Any], Any],
    bot_token: Callable[[], str],
) -> None:
    """Bind bundle-owned Telegram user storage and bot token resolution."""
    global _storage_for, _bot_token
    _storage_for = storage_for
    _bot_token = bot_token


def _storage(entrypoint: Any) -> Any:
    if _storage_for is None:
        raise RuntimeError("telegram widget auth integration is not configured: storage_for is missing")
    return _storage_for(entrypoint)


def _token() -> str:
    if _bot_token is None:
        raise RuntimeError("telegram widget auth integration is not configured: bot_token is missing")
    return _bot_token()


@dataclass(frozen=True)
class TelegramWidgetIdentity:
    user_id: str
    fingerprint: str
    role: str
    telegram_user_id: str
    telegram_chat_id: str
    telegram_username: str
    mapping: Dict[str, Any]
    init_data: Dict[str, str]
    user: Dict[str, Any]

    def as_payload(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "fingerprint": self.fingerprint,
            "role": self.role,
            "telegram_user_id": self.telegram_user_id,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_username": self.telegram_username,
            "mapping": self.mapping,
            "user": self.user,
        }


def resolve_identity(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    allowed_roles: Iterable[str] | None = ("registered", "admin"),
    create_if_missing: bool = False,
) -> TelegramWidgetIdentity:
    init_data = str(telegram_init_data or "").strip() or extract_telegram_init_data_from_request(request)
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram initData is required")

    max_age = int(entrypoint.bundle_prop("integrations.telegram.web_app_auth_max_age_seconds", 86400) or 86400)
    verified = validate_telegram_init_data(
        init_data,
        bot_token=_token(),
        max_age_seconds=max_age,
    )
    user = verified.user
    params = verified.params
    telegram_user_id = str(user.get("id") or "").strip()
    telegram_username = str(user.get("username") or user.get("first_name") or "").strip()
    telegram_chat_id = ""
    chat = params.get("chat")
    if isinstance(chat, str) and chat.strip():
        try:
            parsed_chat = json.loads(chat)
            if isinstance(parsed_chat, dict):
                telegram_chat_id = str(parsed_chat.get("id") or "").strip()
        except Exception:
            telegram_chat_id = ""

    mapping = _storage(entrypoint).resolve_telegram_user(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username=telegram_username,
        create_if_missing=create_if_missing,
    )
    role = str(mapping.get("role") or "anonymous").strip().lower() or "anonymous"
    allowed = {str(item or "").strip().lower() for item in (allowed_roles or ()) if str(item or "").strip()}
    if allowed and role not in allowed:
        raise HTTPException(status_code=403, detail="Telegram user is pending admin approval")

    kdcube_user_id = str(mapping.get("kdcube_user_id") or "").strip()
    return TelegramWidgetIdentity(
        user_id=kdcube_user_id or f"telegram_{telegram_user_id}",
        fingerprint=f"telegram:{telegram_user_id}",
        role=role,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=str(mapping.get("telegram_chat_id") or telegram_chat_id or "").strip(),
        telegram_username=str(mapping.get("telegram_username") or telegram_username or "").strip(),
        mapping=mapping,
        init_data={str(key): str(value) for key, value in params.items()},
        user=user,
    )

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable

from fastapi import HTTPException
from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import integration_definition_value
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bundle_registry import register_config, resolve_config
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    INIT_DATA_HEADER,
    extract_telegram_init_data_from_request,
    validate_telegram_init_data,
)

_storage_for: Callable[[Any], Any] | None = None
_bot_token: Callable[..., Any] | None = None
_CONFIGS: Dict[str, Dict[str, Any]] = {}


def configure_telegram_widget_auth(
    *,
    storage_for: Callable[[Any], Any],
    bot_token: Callable[..., Any],
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned Telegram user storage and bot token resolution."""
    global _storage_for, _bot_token
    _storage_for = storage_for
    _bot_token = bot_token
    register_config(
        _CONFIGS,
        bundle_id=bundle_id,
        config={
            "storage_for": storage_for,
            "bot_token": bot_token,
        },
    )


def _config(entrypoint: Any = None) -> Dict[str, Any]:
    return resolve_config(_CONFIGS, entrypoint=entrypoint, label="telegram widget auth integration")


def _storage(entrypoint: Any) -> Any:
    storage_for = _config(entrypoint).get("storage_for")
    if storage_for is None:
        raise RuntimeError("telegram widget auth integration is not configured: storage_for is missing")
    return storage_for(entrypoint)


def _request_header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            return str(headers.get(name) or "").strip()
        except Exception:
            pass
    if isinstance(request, dict):
        raw_headers = request.get("headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if str(key or "").lower() == name.lower():
                    return str(value or "").strip()
    return ""


def _request_query_value(request: Any, *names: str) -> str:
    query = getattr(request, "query_params", None)
    for name in names:
        if query is not None:
            try:
                value = str(query.get(name) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        if isinstance(request, dict):
            raw_query = request.get("query") or request.get("query_params")
            if isinstance(raw_query, dict):
                for key, value in raw_query.items():
                    if str(key or "").lower() == name.lower() and str(value or "").strip():
                        return str(value or "").strip()
    return ""


def _auth_integration_id(request: Any) -> str:
    return (
        _request_header(request, "X-KDCube-Auth-Integration-ID")
        or _request_query_value(request, "integration_id", "auth_integration_id", "kdcube_auth_integration_id")
    )


async def _token(entrypoint: Any, *, integration_id: str = "") -> str:
    bot_token = _config(entrypoint).get("bot_token")
    if bot_token is None:
        raise RuntimeError("telegram widget auth integration is not configured: bot_token is missing")
    try:
        signature = inspect.signature(bot_token)
    except (TypeError, ValueError):
        value = bot_token(entrypoint)
        if inspect.isawaitable(value):
            value = await value
        return str(value or "")
    if len(signature.parameters) == 0:
        value = bot_token()
    else:
        try:
            value = bot_token(entrypoint, integration_id=integration_id)
        except TypeError:
            value = bot_token(entrypoint)
    if inspect.isawaitable(value):
        value = await value
    return str(value or "")


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


async def resolve_identity(
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
    integration_id = _auth_integration_id(request)

    max_age = int(
        integration_definition_value(
            entrypoint,
            provider="telegram",
            key="web_app_auth_max_age_seconds",
            default=86400,
            integration_id=integration_id,
        )
        or 86400
    )
    verified = validate_telegram_init_data(
        init_data,
        bot_token=await _token(entrypoint, integration_id=integration_id),
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

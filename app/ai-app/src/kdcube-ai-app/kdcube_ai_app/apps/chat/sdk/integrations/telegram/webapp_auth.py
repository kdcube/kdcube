from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl

from fastapi import HTTPException


INIT_DATA_HEADER = "X-Telegram-Init-Data"
_SECRET_KEY_CACHE: dict[str, bytes] = {}


@dataclass(frozen=True)
class TelegramWebAppInitData:
    """Verified Telegram Mini App initData payload."""

    params: dict[str, str]
    user: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {"params": dict(self.params), "user": dict(self.user)}


def extract_telegram_init_data_from_request(request: Any) -> str:
    """Read Telegram Mini App initData from a request header or TMA auth header."""
    if request is None:
        return ""
    try:
        headers = request.headers
    except Exception:
        return ""
    for header in (INIT_DATA_HEADER, INIT_DATA_HEADER.lower(), "Telegram-Init-Data"):
        value = str(headers.get(header) or "").strip()
        if value:
            return value
    authorization = str(headers.get("authorization") or headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("tma "):
        return authorization[4:].strip()
    return ""


def parse_telegram_init_user(params: Mapping[str, str]) -> dict[str, Any]:
    raw = str(params.get("user") or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Telegram initData is missing user")
    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Telegram initData user is invalid")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=401, detail="Telegram initData user is invalid")
    if not str(parsed.get("id") or "").strip():
        raise HTTPException(status_code=401, detail="Telegram initData user id is missing")
    return parsed


def validate_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int = 86400,
    now: int | None = None,
) -> TelegramWebAppInitData:
    """Validate Telegram Mini App initData using the bot token signature."""
    params = dict(parse_qsl(str(init_data or ""), keep_blank_values=True))
    received_hash = str(params.pop("hash", "") or "").strip()
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram initData hash is missing")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    expected_hash = hmac.new(
        _cached_secret_key(bot_token),
        check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Telegram initData signature is invalid")

    auth_date_raw = str(params.get("auth_date") or "").strip()
    if auth_date_raw and max_age_seconds > 0:
        try:
            auth_date = int(auth_date_raw)
        except ValueError:
            raise HTTPException(status_code=401, detail="Telegram initData auth_date is invalid")
        current = int(now if now is not None else time.time())
        if auth_date > current + 60:
            raise HTTPException(status_code=401, detail="Telegram initData auth_date is in the future")
        if current - auth_date > max_age_seconds:
            raise HTTPException(status_code=401, detail="Telegram initData is expired")

    return TelegramWebAppInitData(params=params, user=parse_telegram_init_user(params))


def _cached_secret_key(bot_token: str) -> bytes:
    token = str(bot_token or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="Telegram bot token is not configured")
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = _SECRET_KEY_CACHE.get(cache_key)
    if cached is None:
        cached = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        _SECRET_KEY_CACHE[cache_key] = cached
    return cached


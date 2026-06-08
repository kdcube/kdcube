from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone


_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _safe_prefix(prefix: str) -> str:
    safe_prefix = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(prefix or "").strip())
    return safe_prefix.strip("_-") or "id"


def _base36(value: int, *, width: int) -> str:
    value = max(0, int(value))
    chars: list[str] = []
    if value == 0:
        chars.append("0")
    while value:
        value, remainder = divmod(value, 36)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars)).rjust(width, "0")[-width:]


def timestamp_id(prefix: str) -> str:
    """Return a UTC timestamp-bearing id with no random suffix."""

    safe_prefix = _safe_prefix(prefix)
    ns = time.time_ns()
    seconds, nanos = divmod(ns, 1_000_000_000)
    current = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{safe_prefix}_{current:%Y-%m-%d-%H-%M-%S}-{nanos:09d}"


def timestamp_slug_id(prefix: str, *, slug_len: int = 4) -> str:
    """Return a compact human-facing id with second timestamp plus short slug."""

    safe_prefix = _safe_prefix(prefix)
    current = datetime.now(tz=timezone.utc)
    width = max(2, min(8, int(slug_len or 4)))
    slug = _base36(secrets.randbelow(36**width), width=width)
    return f"{safe_prefix}_{current:%Y-%m-%d-%H-%M-%S}_{slug}"


__all__ = ["timestamp_id", "timestamp_slug_id"]

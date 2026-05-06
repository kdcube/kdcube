# SPDX-License-Identifier: MIT

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def timestamped_id(prefix: str, *, now: datetime | None = None, suffix_chars: int = 4) -> str:
    """
    Build readable, UTC-timestamped runtime IDs.

    The timestamp makes fresh IDs easier to inspect in logs, but persisted
    ordering must still use database/event timestamps.
    """
    if suffix_chars <= 0:
        raise ValueError("suffix_chars must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    stamp = current.strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex((suffix_chars + 1) // 2)[:suffix_chars]
    return f"{prefix}_{stamp}_{suffix}"


def new_turn_id(*, now: datetime | None = None) -> str:
    return timestamped_id("turn", now=now)


def new_exec_id(*, now: datetime | None = None) -> str:
    return timestamped_id("exec", now=now)

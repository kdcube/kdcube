# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Shared encoding helpers for the delegated catalog/card Redis projections.

Cached values are compared inside Lua, so they are always encoded with the same
canonical separators and key order.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


def encode_cache_value(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def decode_cache_value(raw: Any) -> dict[str, Any] | None:
    """Parsed cached object, or ``None`` when the value is absent or unusable.

    Unusable cache data is not an error: the caller read-throughs to durable
    state and conditionally replaces the projection.
    """
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


__all__ = ["decode_cache_value", "encode_cache_value"]

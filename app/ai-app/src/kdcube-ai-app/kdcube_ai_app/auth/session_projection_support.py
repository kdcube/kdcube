# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Shared serialization primitives for Redis session projections."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def session_projection_generation_scope(generation_id: str) -> str:
    value = str(generation_id or "").strip()
    if not value:
        raise ValueError("session projection generation_id is required")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def decode_session_projection(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if not raw:
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


__all__ = [
    "decode_session_projection",
    "session_projection_generation_scope",
]

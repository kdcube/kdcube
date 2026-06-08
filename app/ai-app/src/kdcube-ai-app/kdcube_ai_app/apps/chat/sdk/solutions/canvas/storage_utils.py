from __future__ import annotations

from typing import Any


def safe_storage_segment(value: Any, *, default: str = "default") -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return safe.strip("._") or default


__all__ = ["safe_storage_segment"]

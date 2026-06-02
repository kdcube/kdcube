# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pathlib
from typing import Any

DEFAULT_VISIBLE_BINARY_BYTES = 10 * 1024 * 1024


def positive_int(value: Any) -> int:
    try:
        out = int(value)
    except Exception:
        return 0
    return out if out > 0 else 0


def auto_binary_visibility_limit(runtime_ctx: Any) -> dict[str, Any]:
    """Resolve the binary size cap for model-visible image/PDF blocks.

    This is a ReAct event/rendering heuristic, not external-tool orchestration.
    It combines the per-runtime read cap with session cache-truncation settings
    so binary blocks do not break the context/cache budget.
    """
    read_cap = positive_int(getattr(runtime_ctx, "read_visible_max_bytes", None)) or DEFAULT_VISIBLE_BINARY_BYTES
    session = getattr(runtime_ctx, "session", None)
    keep_images = positive_int(getattr(session, "cache_truncation_keep_recent_images", None))
    if session is not None and getattr(session, "cache_truncation_keep_recent_images", None) == 0:
        return {
            "bytes": 0,
            "source": "cache_truncation_keep_recent_images",
            "read_visible_max_bytes": read_cap,
            "cache_truncation_keep_recent_images": 0,
        }
    b64_sum = positive_int(getattr(session, "cache_truncation_max_image_pdf_b64_sum", None))
    b64_raw_cap = (b64_sum * 3) // 4 if b64_sum else 0
    candidates = [read_cap]
    if b64_raw_cap:
        candidates.append(b64_raw_cap)
    return {
        "bytes": min(candidates),
        "source": "min(read_visible_max_bytes, cache_truncation_max_image_pdf_b64_sum_as_bytes)"
        if b64_raw_cap
        else "read_visible_max_bytes",
        "read_visible_max_bytes": read_cap,
        "cache_truncation_max_image_pdf_b64_sum": b64_sum or None,
        "cache_truncation_keep_recent_images": keep_images or None,
    }


def should_attach_binary_to_prompt(*, runtime_ctx: Any, abs_path: pathlib.Path) -> tuple[bool, dict[str, Any]]:
    """Return whether a binary artifact can be attached to visible context."""
    try:
        size_bytes = abs_path.stat().st_size
    except Exception:
        return True, {}
    limit = auto_binary_visibility_limit(runtime_ctx)
    cap = int(limit.get("bytes") or 0)
    if cap <= 0 or size_bytes > cap:
        return False, {
            "multimodal_status": "too_large_for_visible_context",
            "size_bytes": size_bytes,
            "visible_image_limit_bytes": cap,
            "visible_image_limit_source": limit.get("source"),
            "read_visible_max_bytes": limit.get("read_visible_max_bytes"),
            "cache_truncation_max_image_pdf_b64_sum": limit.get("cache_truncation_max_image_pdf_b64_sum"),
            "recover_with": (
                "request a smaller screenshot/viewport, downsample/crop with exec, "
                "or inspect with react.read only if under byte caps"
            ),
        }
    return True, {
        "visible_image_limit_bytes": cap,
        "visible_image_limit_source": limit.get("source"),
    }

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Non-secret descriptor sync failures across provider and workspace boundaries."""

from __future__ import annotations

import re
from typing import Any, Mapping

_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")


def capability_sync_failure(value: Any = None, *, code: str = "agent_capability_sync_failed") -> dict[str, Any]:
    """Copy bounded operation codes and retryability; discard every other field."""
    source = value if isinstance(value, Mapping) else {}
    raw_code = source.get("error", source.get("code", code))
    chosen = raw_code if type(raw_code) is str and _CODE.fullmatch(raw_code) else code
    raw_reason = source.get("reason", chosen)
    reason = raw_reason if type(raw_reason) is str and _CODE.fullmatch(raw_reason) else chosen
    retryable = source.get("retryable")
    retryable = retryable if type(retryable) is bool else True
    status = source.get("status")
    status = status if type(status) is int and 400 <= status <= 599 else (503 if retryable else 409)
    return {"ok": False, "error": chosen, "reason": reason, "retryable": retryable, "status": status}


def capability_failure_from_exception(exc: BaseException) -> dict[str, Any]:
    """Never copy exception text, messages, payloads, URLs or tracebacks."""
    return capability_sync_failure({"error": getattr(exc, "code", "agent_capability_sync_failed"),
        "reason": getattr(exc, "reason", "agent_capability_sync_failed"),
        "retryable": getattr(exc, "retryable", True), "status": getattr(exc, "status", 503)})


def capability_failure_log(failure: Mapping[str, Any]) -> str:
    safe = capability_sync_failure(failure)
    return f"capability sync refused code={safe['error']} reason={safe['reason']} retryable={str(safe['retryable']).lower()}"

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
    status = source.get("status")
    valid_status = type(status) is int and 400 <= status <= 599
    retryable = source.get("retryable")
    retryable = retryable if type(retryable) is bool else (
        status >= 500 or status in (408, 409, 425, 429) if valid_status else True)
    status = status if valid_status else (503 if retryable else 409)
    failure = {"ok": False, "error": chosen, "reason": reason, "retryable": retryable, "status": status}
    for name in ("control_committed", "card_committed"):
        if type(source.get(name)) is bool:
            failure[name] = source[name]
    return failure


def capability_failure_from_exception(exc: BaseException) -> dict[str, Any]:
    """Never copy exception text, messages, payloads, URLs or tracebacks."""
    known = getattr(exc, "failure", None)
    committed = {name: known[name] for name in ("control_committed", "card_committed")
                 if isinstance(known, Mapping) and name in known}
    return capability_sync_failure({**committed, "error": getattr(exc, "code", "agent_capability_sync_failed"),
        "reason": getattr(exc, "reason", "agent_capability_sync_failed"),
        "retryable": getattr(exc, "retryable", True), "status": getattr(exc, "status", 503)})


def capability_failure_log(failure: Mapping[str, Any]) -> str:
    safe = capability_sync_failure(failure)
    return f"capability sync refused code={safe['error']} reason={safe['reason']} retryable={str(safe['retryable']).lower()}"

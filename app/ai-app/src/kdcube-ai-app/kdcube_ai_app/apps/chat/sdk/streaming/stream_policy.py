# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any


class StreamPolicyViolation(RuntimeError):
    """Raised by stream subscribers when buffered output must not be emitted."""

    def __init__(self, *, code: str, message: str = "", extra: dict[str, Any] | None = None) -> None:
        self.code = str(code or "stream_policy_violation").strip()
        self.message = str(message or self.code).strip()
        self.extra = dict(extra or {})
        super().__init__(self.message)


__all__ = ["StreamPolicyViolation"]

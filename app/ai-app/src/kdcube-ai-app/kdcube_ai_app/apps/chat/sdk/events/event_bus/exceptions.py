# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations


class ExternalEventLaneWakeIgnored(Exception):
    """Raised when a lane wake is valid but no processor turn should run."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = str(reason or "ignored")

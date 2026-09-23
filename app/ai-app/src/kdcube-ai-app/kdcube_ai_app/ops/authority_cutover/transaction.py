# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Transaction-bound pool adapter for authority cutover stores."""

from __future__ import annotations

from typing import Any


class _BorrowedConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def __aenter__(self) -> Any:
        return self._connection

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


class TransactionBoundPool:
    """Expose one borrowed connection through both accepted store contracts."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def acquire(self) -> _BorrowedConnection:
        return _BorrowedConnection(self._connection)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


__all__ = ["TransactionBoundPool"]

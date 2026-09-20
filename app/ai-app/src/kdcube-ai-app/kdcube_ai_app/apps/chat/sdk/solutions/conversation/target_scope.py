# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-local conversation targets resolved by Connection Hub admission."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_targets: ContextVar[tuple[str, ...] | None] = ContextVar(
    "conversation_read_targets", default=None
)


@contextmanager
def bind_conversation_targets(targets: tuple[str, ...]) -> Iterator[None]:
    token = _targets.set(tuple(targets))
    try:
        yield
    finally:
        _targets.reset(token)


def admitted_conversation_targets() -> tuple[str, ...] | None:
    return _targets.get()


__all__ = ["admitted_conversation_targets", "bind_conversation_targets"]

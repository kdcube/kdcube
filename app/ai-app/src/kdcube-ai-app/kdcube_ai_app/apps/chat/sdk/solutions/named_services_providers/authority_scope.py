# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-local claims admitted for one named-service invocation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_claims: ContextVar[tuple[str, ...] | None] = ContextVar(
    "named_service_admitted_claims",
    default=None,
)


@contextmanager
def bind_named_service_claims(claims: tuple[str, ...]) -> Iterator[None]:
    token = _claims.set(tuple(claims))
    try:
        yield
    finally:
        _claims.reset(token)


def admitted_named_service_claims() -> tuple[str, ...] | None:
    return _claims.get()


__all__ = ["admitted_named_service_claims", "bind_named_service_claims"]

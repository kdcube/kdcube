# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-local conversation targets resolved by Connection Hub admission."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Iterator

from connection_hub.delegated_credentials.application_resources import (
    APPLICATION_RESOURCE_PREFIX,
    ApplicationResource,
    ApplicationResourceError,
)

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


def conversation_target_applications(
    targets: Iterable[str],
    *,
    tenant: str,
    project: str,
) -> tuple[str, ...]:
    """Resolve admitted app-level selectors inside the active deployment.

    Legacy Cards contain exact bundle ids. Current Cards contain typed
    application resources. Conversation storage is app-scoped, so only a
    selector whose agent segment is ``*`` can authorize this provider; an
    exact-agent selector requires an agent-scoped storage target this provider
    does not currently expose.
    """

    applications: set[str] = set()
    for raw in targets:
        target = str(raw or "").strip()
        if not target:
            continue
        if not target.startswith(APPLICATION_RESOURCE_PREFIX):
            if not any(marker in target for marker in ("*", "?", "[", "]")):
                applications.add(target)
            continue
        try:
            resource = ApplicationResource.parse(target)
        except ApplicationResourceError:
            continue
        if (
            resource.tenant != str(tenant or "").strip()
            or resource.project != str(project or "").strip()
            or resource.agent != "*"
        ):
            continue
        applications.add(resource.application)
    return tuple(sorted(applications))


__all__ = [
    "admitted_conversation_targets",
    "bind_conversation_targets",
    "conversation_target_applications",
]

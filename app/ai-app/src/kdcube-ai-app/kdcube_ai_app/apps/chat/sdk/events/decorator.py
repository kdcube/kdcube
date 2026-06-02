# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
from typing import Any, Callable, TypeVar


EVENT_SOURCE_ATTR = "__kdcube_event_source__"

T = TypeVar("T")


@dataclass(frozen=True)
class EventSourceDeclaration:
    """Metadata declared by a tool or non-tool external event source."""

    event_source_id: str
    policies: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    description: str = ""
    version: str = ""
    kind: str = ""

    def with_event_source_id(self, event_source_id: str) -> "EventSourceDeclaration":
        return EventSourceDeclaration(
            event_source_id=event_source_id,
            policies=tuple(dict(spec) for spec in self.policies),
            description=self.description,
            version=self.version,
            kind=self.kind,
        )


def event_source_declaration(
    *,
    event_source_id: str,
    policies: Sequence[Mapping[str, Any]] | None = None,
    description: str = "",
    version: str = "",
    kind: str = "",
) -> EventSourceDeclaration:
    event_source_id = str(event_source_id or "").strip()
    if not event_source_id:
        raise ValueError("event_source_id must be non-empty")
    return EventSourceDeclaration(
        event_source_id=event_source_id,
        policies=_normalize_policy_specs(policies),
        description=str(description or "").strip(),
        version=str(version or "").strip(),
        kind=str(kind or "").strip(),
    )


def event_source(
    *,
    event_source_id: str,
    policies: Sequence[Mapping[str, Any]] | None = None,
    description: str = "",
    version: str = "",
    kind: str = "",
) -> Callable[[T], T]:
    """Attach event-source metadata to a tool function or declaration object."""

    declaration = event_source_declaration(
        event_source_id=event_source_id,
        policies=policies,
        description=description,
        version=version,
        kind=kind,
    )

    def _decorate(obj: T) -> T:
        setattr(obj, EVENT_SOURCE_ATTR, declaration)
        return obj

    return _decorate


def get_event_source_declaration(obj: Any) -> EventSourceDeclaration | None:
    if obj is None:
        return None
    declaration = getattr(obj, EVENT_SOURCE_ATTR, None)
    if declaration is None and hasattr(obj, "__func__"):
        declaration = getattr(getattr(obj, "__func__", None), EVENT_SOURCE_ATTR, None)
    if isinstance(declaration, EventSourceDeclaration):
        return declaration
    if isinstance(declaration, Mapping):
        try:
            return event_source_declaration(**declaration)
        except Exception:
            return None
    return None


def _normalize_policy_specs(policies: Sequence[Mapping[str, Any]] | None) -> tuple[dict[str, Any], ...]:
    if policies is None:
        return ()
    if isinstance(policies, Mapping) or isinstance(policies, (str, bytes)):
        raise ValueError("policies must be a list of policy binding objects")
    out: list[dict[str, Any]] = []
    for idx, spec in enumerate(policies):
        if not isinstance(spec, Mapping):
            raise ValueError(f"policy binding at index {idx} must be an object")
        react_phase = str(spec.get("react_phase") or "").strip()
        if not react_phase:
            raise ValueError(f"policy binding at index {idx} must define react_phase")
        event_policy_id = str(spec.get("event_policy_id") or "").strip()
        if not event_policy_id:
            raise ValueError(f"policy binding {react_phase!r} must define a non-empty string event_policy_id")
        normalized = dict(spec)
        normalized["react_phase"] = react_phase
        normalized["event_policy_id"] = event_policy_id
        out.append(normalized)
    return tuple(out)

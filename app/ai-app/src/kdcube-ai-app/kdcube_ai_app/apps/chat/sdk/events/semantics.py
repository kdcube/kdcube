# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any, Iterable


_ACTIVE_TURN_CONTROL_TYPE = "event.user.steer"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _semantic_type_candidates(event: Any) -> Iterable[Any]:
    yield _value(event, "type")

    payload = _mapping(_value(event, "payload"))
    accepted = _mapping(payload.get("event"))
    yield accepted.get("type")

    task_payload = _mapping(_value(event, "task_payload"))
    task_event = _mapping(task_payload.get("event"))
    yield task_event.get("type")

    request = _mapping(task_payload.get("request"))
    for item in request.get("external_events") or []:
        if isinstance(item, dict):
            yield item.get("type")

    yield _value(event, "kind")


def event_semantic_type(event: Any, *, fallback: str = "") -> str:
    """Return the authored event type hidden inside a retained lane envelope."""
    transport_types = {"", "external", "external_event"}
    transport_candidate = ""
    for candidate in _semantic_type_candidates(event):
        normalized = str(candidate or "").strip().lower()
        if not normalized:
            continue
        if normalized not in transport_types:
            return normalized
        if not transport_candidate:
            transport_candidate = normalized
    return transport_candidate or str(fallback or "").strip().lower()


def event_is_active_turn_control(event: Any) -> bool:
    """Whether an event controls an existing turn and must never start one."""
    return event_semantic_type(event) == _ACTIVE_TURN_CONTROL_TYPE


def active_turn_control_target(event: Any) -> str:
    """Return the server-observed turn fence carried by an active control."""
    for name in ("owner_turn_id", "active_turn_id_at_ingress", "target_turn_id"):
        value = str(_value(event, name) or "").strip()
        if value:
            return value

    task_payload = _mapping(_value(event, "task_payload"))
    continuation = _mapping(task_payload.get("continuation"))
    for name in ("owner_turn_id", "active_turn_id", "target_turn_id"):
        value = str(continuation.get(name) or "").strip()
        if value:
            return value
    return ""

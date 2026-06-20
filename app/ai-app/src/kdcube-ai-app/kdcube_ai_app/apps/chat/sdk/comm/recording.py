# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import EventFilterInput


DEFAULT_MAX_RECORDED_EVENTS = 1000


def now_ms() -> int:
    return int(time.time() * 1000)


def json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def portable_filter(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [portable_filter(v) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                return None
            pv = portable_filter(v)
            if pv is None and v is not None:
                return None
            out[k] = pv
        return out
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _metadata_agent_id(data: Optional[Dict[str, Any]]) -> Any:
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return metadata.get("agent_id")


def _event_value(event: EventFilterInput, key: str, data: Optional[Dict[str, Any]] = None) -> Any:
    if key == "types":
        return event.type
    if key == "route_key":
        return event.route_key
    if key == "route_keys":
        return event.route_key
    if key == "type":
        return event.type
    if key == "routes":
        return event.route
    if key == "route":
        return event.route
    if key == "socket_events" or key == "socket_event":
        return event.socket_event
    if key == "agents" or key == "agent":
        return event.agent
    if key in ("agent_ids", "agent_id", "metadata.agent_id"):
        return _metadata_agent_id(data)
    if key == "steps" or key == "step":
        return event.step
    if key == "statuses" or key == "status":
        return event.status
    if key == "broadcast":
        return event.broadcast
    return None


def _criteria_empty(criteria: dict[str, Any]) -> bool:
    for key, value in (criteria or {}).items():
        if key == "broadcast":
            if value is not None:
                return False
            continue
        if _as_list(value):
            return False
    return True


def _criteria_matches(criteria: dict[str, Any], event: EventFilterInput, data: Optional[Dict[str, Any]] = None) -> bool:
    if not criteria:
        return True
    for key, value in criteria.items():
        if key == "broadcast":
            if value is not None and bool(value) != bool(event.broadcast):
                return False
            continue
        values = _as_list(value)
        if not values:
            continue
        actual = _event_value(event, key, data=data)
        if actual not in values:
            return False
    return True


def _selector_from_shorthand(selector: Any) -> Any:
    if isinstance(selector, str):
        return {"include": {"types": [selector]}}
    if isinstance(selector, (list, tuple, set)):
        return {"include": {"types": list(selector)}}
    return selector


def selector_allows(
    selector: Any,
    *,
    user_type: str,
    user_id: str,
    event: EventFilterInput,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    if selector is None:
        return True

    if hasattr(selector, "allow_event"):
        return bool(selector.allow_event(user_type=user_type, user_id=user_id, event=event, data=data))

    if callable(selector):
        try:
            return bool(selector(user_type=user_type, user_id=user_id, event=event, data=data))
        except TypeError:
            return bool(selector(event, data))

    selector = _selector_from_shorthand(selector)
    if not isinstance(selector, dict):
        return True

    any_selectors = selector.get("any")
    if isinstance(any_selectors, list):
        return any(
            selector_allows(
                child,
                user_type=user_type,
                user_id=user_id,
                event=event,
                data=data,
            )
            for child in any_selectors
        )

    include = selector.get("include") or {}
    exclude = selector.get("exclude") or {}

    include_ok = True if _criteria_empty(include) else _criteria_matches(include, event, data=data)
    if not include_ok:
        return False
    if not _criteria_empty(exclude) and _criteria_matches(exclude, event, data=data):
        return False
    return True


def selector_privacy(selector: Any) -> dict[str, Any]:
    selector = _selector_from_shorthand(selector)
    if isinstance(selector, dict) and isinstance(selector.get("privacy"), dict):
        return selector["privacy"]
    return {}


def _numeric_metrics(value: Any) -> dict[str, float | int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float | int] = {}
    for k, v in value.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[str(k)] = v
    return out


def _record_data_for_type(envelope_type: str, data_payload: dict[str, Any], privacy: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    include_data = bool(privacy.get("include_data", False))
    data_keys = {str(k) for k in _as_list(privacy.get("data_keys"))}

    if envelope_type == "accounting.usage":
        allowed = {
            "breakdown",
            "cost_total_usd",
            "agent_costs",
            "service_type",
            "provider",
            "model_or_service",
        }
        return ({k: json_safe(v) for k, v in data_payload.items() if k in allowed}, False)

    if data_keys:
        return ({k: json_safe(v) for k, v in data_payload.items() if k in data_keys}, False)

    if include_data:
        return (json_safe(data_payload), False)

    return ({}, bool(data_payload))


def make_recorded_item(
    *,
    socket_event: str,
    data: dict,
    broadcast: bool,
    event: EventFilterInput,
    selector: Any = None,
) -> dict[str, Any]:
    envelope = data or {}
    service = envelope.get("service") if isinstance(envelope.get("service"), dict) else {}
    conversation = envelope.get("conversation") if isinstance(envelope.get("conversation"), dict) else {}
    event_meta = envelope.get("event") if isinstance(envelope.get("event"), dict) else {}
    data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    delta = envelope.get("delta") if isinstance(envelope.get("delta"), dict) else {}
    privacy = selector_privacy(selector)
    envelope_type = str(event.type or envelope.get("type") or "")

    recorded_data, data_redacted = _record_data_for_type(envelope_type, data_payload, privacy)
    metrics = _numeric_metrics(data_payload)

    if delta:
        text = str(delta.get("text") or envelope.get("text") or "")
        metrics["delta_text_len"] = len(text)
        recorded_data.setdefault("delta", {
            "marker": delta.get("marker"),
            "index": delta.get("index"),
            "completed": delta.get("completed"),
        })
        if bool(privacy.get("include_delta_text", False)):
            recorded_data["delta"]["text"] = text
            data_redacted = False
        elif text:
            data_redacted = True

    recorded_at_ms = now_ms()
    item = {
        "recorded_at_ms": recorded_at_ms,
        "socket_event": socket_event,
        "broadcast": bool(broadcast),
        "type": envelope_type,
        "route": event.route,
        "route_key": event.route_key,
        "service": json_safe(service),
        "conversation": json_safe(conversation),
        "metadata": json_safe(envelope.get("metadata") or {}),
        "event": json_safe({
            "agent": event_meta.get("agent"),
            "step": event_meta.get("step"),
            "status": event_meta.get("status"),
            "title": event_meta.get("title"),
        }),
        "data": recorded_data,
        "metrics": json_safe(metrics),
        "privacy": {
            "contains_content": bool(privacy.get("include_data", False) or privacy.get("include_delta_text", False)),
            "data_redacted": bool(data_redacted),
        },
    }

    source_ts = envelope.get("ts") or envelope.get("timestamp") or recorded_at_ms
    sig = {
        "source_ts": source_ts,
        "socket_event": item["socket_event"],
        "broadcast": item["broadcast"],
        "type": item["type"],
        "route": item["route"],
        "service": item["service"],
        "conversation": item["conversation"],
        "event": item["event"],
        "metrics": item["metrics"],
    }
    digest = hashlib.sha256(
        json.dumps(sig, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    item["record_id"] = f"commrec_{digest[:32]}"
    return item


def event_input_from_recorded_item(item: dict[str, Any]) -> EventFilterInput:
    ev = item.get("event") if isinstance(item.get("event"), dict) else {}
    return EventFilterInput(
        type=item.get("type") or "",
        route=item.get("route"),
        socket_event=item.get("socket_event") or "",
        agent=ev.get("agent"),
        step=ev.get("step"),
        status=ev.get("status"),
        broadcast=bool(item.get("broadcast")),
    )


def filter_recorded_items(
    items: Iterable[dict[str, Any]],
    selector: Any,
    *,
    user_type: str,
    user_id: str,
) -> list[dict[str, Any]]:
    if selector is None:
        return [deepcopy(it) for it in items]
    out = []
    for item in items:
        event = event_input_from_recorded_item(item)
        if selector_allows(selector, user_type=user_type, user_id=user_id, event=event, data=item):
            out.append(deepcopy(item))
    return out

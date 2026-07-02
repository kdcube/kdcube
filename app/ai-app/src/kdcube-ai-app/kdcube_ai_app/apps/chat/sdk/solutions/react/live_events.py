# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.core import event_source_id_for_external_kind


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _event_payload(event: Any) -> Dict[str, Any]:
    raw = getattr(event, "payload", None)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _source_default(runtime_ctx: Any, event_source_id: str) -> Any:
    event_sources = getattr(runtime_ctx, "event_sources", None)
    if event_sources is None:
        return None
    by_event_source_id = getattr(event_sources, "by_event_source_id", None)
    if not callable(by_event_source_id):
        return None
    try:
        return by_event_source_id(event_source_id)
    except Exception:
        return None


def _bool_from_any(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def recover_semantic_event_type(type_norm: Any, event: Any) -> str:
    """Recover the SEMANTIC external-event type (steer/followup) from a transport
    envelope. The transported lane ``kind`` is uniformly ``"external_event"`` for
    in-flight events; the semantic type lives nested in ``payload.event.type``
    (e.g. ``"event.user.steer"``). This lets the live interrupt/credit logic treat
    a "stop"/steer as a steer WITHOUT changing the transport envelope ``kind``.
    Already-specific types (steer/followup/message/...) are returned unchanged.
    """
    norm = str(type_norm or "").strip().lower()
    if norm not in {"external_event", "external", ""}:
        return norm
    payload = getattr(event, "payload", None)
    accepted = payload.get("event") if isinstance(payload, dict) else None
    nested = str((accepted or {}).get("type") or "").strip().lower() if isinstance(accepted, dict) else ""
    if "steer" in nested:
        return "steer"
    if "followup" in nested:
        return "followup"
    return norm


def compute_reactive_iteration_credit_cap(*, runtime_ctx: Any, base_max_iterations: int) -> int:
    enabled = bool(getattr(runtime_ctx, "reactive_event_iteration_credit_enabled", True))
    if not enabled:
        return 0
    raw_cap = getattr(runtime_ctx, "reactive_event_iteration_credit_cap", None)
    if raw_cap is None:
        return max(0, int(base_max_iterations or 0))
    return max(0, _as_int(raw_cap, base_max_iterations))


def resolve_reactive_iteration_credit(
    *,
    event_type: str,
    event: Any,
    runtime_ctx: Any,
) -> int:
    enabled = bool(getattr(runtime_ctx, "reactive_event_iteration_credit_enabled", True))
    if not enabled:
        return 0
    type_norm = str(event_type or getattr(event, "kind", "") or "").strip().lower()
    if type_norm == "steer":
        return 0

    payload = _event_payload(event)
    accepted_event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_payload = accepted_event.get("payload") if isinstance(accepted_event.get("payload"), dict) else {}
    event_source_id = str(accepted_event.get("event_source_id") or "").strip()
    if not event_source_id:
        event_source_id = event_source_id_for_external_kind(type_norm)
    source = _source_default(runtime_ctx, event_source_id)

    if type_norm == "followup":
        is_reactive = True
    elif type_norm == "external_event":
        # Authored external events are expensive when reactive because they run
        # ReAct. The transported occurrence must carry the effective decision;
        # source declarations may provide credit defaults, but they do not wake
        # ReAct by themselves.
        if "reactive" in accepted_event:
            is_reactive = _bool_from_any(accepted_event.get("reactive"), False)
        else:
            is_reactive = False
    else:
        if getattr(source, "reactive", None) is not None:
            is_reactive = bool(getattr(source, "reactive"))
        else:
            is_reactive = False
    if not is_reactive:
        return 0

    per_event = max(0, _as_int(getattr(runtime_ctx, "reactive_event_iteration_credit_per_event", 1), 1))
    source_credit = getattr(source, "iteration_credit", None)
    if source_credit is not None:
        per_event = max(0, _as_int(source_credit, per_event))
    if "iteration_credit" in event_payload:
        per_event = max(0, _as_int(event_payload.get("iteration_credit"), per_event))
    if "iteration_credit" in accepted_event:
        per_event = max(0, _as_int(accepted_event.get("iteration_credit"), per_event))
    return per_event


def sync_reactive_iteration_budget(*, state: Dict[str, Any], granted_credit: int) -> int:
    base = max(
        0,
        _as_int(
            state.get("base_max_iterations"),
            _as_int(state.get("max_iterations"), 0),
        ),
    )
    cap = max(0, _as_int(state.get("reactive_iteration_credit_cap"), 0))
    effective_credit = max(0, _as_int(granted_credit, 0))
    if cap:
        effective_credit = min(effective_credit, cap)
    else:
        effective_credit = 0
    state["base_max_iterations"] = base
    state["reactive_iteration_credit_cap"] = cap
    state["reactive_iteration_credit"] = effective_credit
    state["max_iterations"] = base + effective_credit
    return int(state["max_iterations"] or 0)

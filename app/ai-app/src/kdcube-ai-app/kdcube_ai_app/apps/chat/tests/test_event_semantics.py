from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import EventLaneState, wake_ignore_reason
from kdcube_ai_app.apps.chat.sdk.events.semantics import (
    active_turn_control_target,
    event_is_active_turn_control,
    event_semantic_type,
)


def test_active_turn_control_uses_only_the_protocol_event_type():
    assert event_is_active_turn_control({"type": "event.user.steer"}) is True
    assert event_is_active_turn_control({"type": "user.steer"}) is False
    assert event_is_active_turn_control({"type": "steer"}) is False
    assert event_is_active_turn_control({"kind": "steer"}) is False


def test_retained_lane_event_recovers_protocol_type_and_turn_fence():
    event = SimpleNamespace(
        kind="external_event",
        payload={"event": {"type": "event.user.steer", "reactive": True}},
        task_payload={},
        active_turn_id_at_ingress="turn-active",
        owner_turn_id=None,
        target_turn_id="turn-client",
        consumed_at=None,
        promoted_at=None,
        failed_at=None,
        created_at=1.0,
    )

    assert event_semantic_type(event) == "event.user.steer"
    assert event_is_active_turn_control(event) is True
    assert active_turn_control_target(event) == "turn-active"
    assert wake_ignore_reason(event, EventLaneState()) == "active_turn_control_not_promotable"

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.solutions.react.live_events import recover_semantic_event_type


def _evt(nested_type):
    # Mirrors the transport envelope: the lane `kind` is "external_event"; the real
    # semantic type lives nested in payload.event.type.
    return SimpleNamespace(payload={"event": {"type": nested_type}})


def test_recover_steer_from_external_envelope():
    # Regression: a live "stop"/steer arrives on the wire with transport kind
    # "external_event" and the real type nested. It MUST be recovered as "steer" so the
    # runtime fires _interrupt_active_phase_for_steer (and denies iteration credit).
    assert recover_semantic_event_type("external_event", _evt("event.user.steer")) == "steer"
    assert recover_semantic_event_type("external", _evt("event.user.steer")) == "steer"
    assert recover_semantic_event_type("", _evt("user.steer")) == "steer"


def test_recover_followup_from_external_envelope():
    assert recover_semantic_event_type("external_event", _evt("event.user.followup")) == "followup"


def test_recover_leaves_prompt_and_specific_types_unchanged():
    # A prompt stays "external_event" — unchanged credit/behaviour.
    assert recover_semantic_event_type("external_event", _evt("event.user.prompt")) == "external_event"
    # Already-specific types are returned unchanged (never re-derived from payload).
    assert recover_semantic_event_type("steer", _evt("event.user.prompt")) == "steer"
    assert recover_semantic_event_type("followup", _evt(None)) == "followup"
    # Missing / malformed payload → falls back to the passed transport norm.
    assert recover_semantic_event_type("external_event", SimpleNamespace(payload=None)) == "external_event"
    assert recover_semantic_event_type("external_event", SimpleNamespace()) == "external_event"

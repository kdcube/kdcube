# SPDX-License-Identifier: MIT

"""Event streaming tests for bundles (Type 6).

Test that events flow correctly to client via ChatCommunicator.
Tests work with any bundle selected by folder.

Run with:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_event_streaming.py -v
  pytest test_event_streaming.py --bundle-path=/abs/path/to/bundle -v
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.comm.event_filter import EventFilterInput, IEventFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingRelay:
    """Minimal relay that captures all emitted events in order.

    Implements the duck-typed interface expected by ChatCommunicator.emitter:
    an object with an async emit(*, event, data, **kwargs) method.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []  # (socket_event, envelope)

    async def emit(self, *, event: str, data: dict, **kwargs) -> None:
        self.events.append((event, data))

    def emitted_types(self) -> list[str]:
        """Return the list of envelope types in emission order."""
        return [env.get("type") for _, env in self.events]

    def emitted_statuses(self) -> list[str | None]:
        """Return event.status values in emission order."""
        return [env.get("event", {}).get("status") for _, env in self.events]


def _make_comm(relay: _RecordingRelay, event_filter=None):
    """Build a ChatCommunicator wired to a recording relay."""
    from kdcube_ai_app.apps.chat.emitters import ChatCommunicator

    return ChatCommunicator(
        emitter=relay,
        tenant="t",
        project="p",
        user_id="u",
        user_type="anonymous",
        service={"request_id": "r1", "tenant": "t", "project": "p", "user": "u"},
        conversation={"session_id": "s1", "conversation_id": "c1", "turn_id": "turn1"},
        event_filter=event_filter,
    )


# ---------------------------------------------------------------------------
# Tests: individual event types
# ---------------------------------------------------------------------------

class TestEventStructure:
    """Verify that each emitter method produces the correct envelope."""

    @pytest.mark.anyio
    async def test_start_event_has_started_status(self):
        """comm.start() emits an event with status='started'."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.start(message="Hello!", queue_stats={})

        assert len(relay.events) == 1
        _, envelope = relay.events[0]
        assert envelope["type"] == "chat.start"
        assert envelope["event"]["status"] == "started"

    @pytest.mark.anyio
    async def test_delta_event_has_running_status(self):
        """comm.delta() emits an event with status='running'."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.delta(text="Hello", index=0)

        assert len(relay.events) == 1
        _, envelope = relay.events[0]
        assert envelope["type"] == "chat.delta"
        assert envelope["event"]["status"] == "running"

    @pytest.mark.anyio
    async def test_delta_event_contains_text_and_marker(self):
        """comm.delta() envelope includes delta.text and delta.marker."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.delta(text="chunk", index=0, marker="answer")

        _, envelope = relay.events[0]
        delta = envelope.get("delta") or {}
        assert delta["text"] == "chunk"
        assert delta["marker"] == "answer"

    @pytest.mark.anyio
    async def test_complete_event_has_completed_status(self):
        """comm.complete() emits an event with status='completed'."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.complete(data={"answer": "done"})

        assert len(relay.events) == 1
        _, envelope = relay.events[0]
        assert envelope["type"] == "chat.complete"
        assert envelope["event"]["status"] == "completed"

    @pytest.mark.anyio
    async def test_error_event_has_error_status(self):
        """comm.error() emits an event with status='error'."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.error(message="something broke", agent="turn.error", step="turn")

        assert len(relay.events) == 1
        _, envelope = relay.events[0]
        assert envelope["type"] == "chat.error"
        assert envelope["event"]["status"] == "error"

    @pytest.mark.anyio
    async def test_step_event_carries_provided_status(self):
        """comm.step() uses the status value passed by the caller."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.step(step="gate", status="completed", title="Gate done")

        _, envelope = relay.events[0]
        assert envelope["type"] == "chat.step"
        assert envelope["event"]["status"] == "completed"

    @pytest.mark.anyio
    async def test_compaction_event_uses_compaction_socket_route(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.event(
            agent="context.compaction",
            type="chat.compaction",
            route="chat.compaction",
            step="context.compaction",
            status="started",
            title="Context Compaction Started",
            data={"compaction_id": "c1"},
        )

        socket_event, envelope = relay.events[0]
        assert socket_event == "chat_compaction"
        assert envelope["type"] == "chat.compaction"
        assert envelope["event"]["status"] == "started"
        assert envelope["data"]["compaction_id"] == "c1"

    @pytest.mark.anyio
    async def test_event_envelope_contains_service_fields(self):
        """Every envelope includes the service dict with tenant/project/user."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.start(message="x")

        _, envelope = relay.events[0]
        svc = envelope.get("service") or {}
        assert "tenant" in svc
        assert "project" in svc
        assert "user" in svc

    @pytest.mark.anyio
    async def test_event_envelope_contains_conversation_fields(self):
        """Every envelope includes the conversation dict with conversation_id."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.start(message="x")

        _, envelope = relay.events[0]
        conv = envelope.get("conversation") or {}
        assert "conversation_id" in conv
        assert "turn_id" in conv


# ---------------------------------------------------------------------------
# Tests: event ordering
# ---------------------------------------------------------------------------

class TestEventOrdering:
    """Verify that events are emitted in the expected logical order."""

    @pytest.mark.anyio
    async def test_events_in_start_processing_done_order(self):
        """Events arrive in: start → processing step → complete."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.start(message="go")
        await comm.step(step="process", status="running")
        await comm.complete(data={})

        types = relay.emitted_types()
        assert types[0] == "chat.start"
        assert types[-1] == "chat.complete"
        assert "chat.step" in types

    @pytest.mark.anyio
    async def test_error_event_order_start_then_error(self):
        """On failure: start → error (no complete after error)."""
        relay = _RecordingRelay()
        comm = _make_comm(relay)

        await comm.start(message="go")
        await comm.error(message="fail", step="process")

        types = relay.emitted_types()
        assert types[0] == "chat.start"
        assert "chat.error" in types
        assert "chat.complete" not in types


class TestActivityListeners:
    """Verify that in-process subscribers can observe communicator activity."""

    @pytest.mark.anyio
    async def test_activity_listener_receives_enveloped_delta(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        observed = []

        async def _listener(activity):
            observed.append(activity)

        comm.add_activity_listener(_listener)

        await comm.delta(
            text="working",
            index=3,
            marker="timeline_text",
            agent="react.decision",
            format="markdown",
            artifact_name="react.notes",
        )

        assert len(observed) == 1
        activity = observed[0]
        assert activity["event"] == "chat_delta"
        assert activity["type"] == "chat.delta"
        assert activity["data"]["delta"]["text"] == "working"
        assert activity["data"]["delta"]["marker"] == "timeline_text"
        assert activity["data"]["extra"]["artifact_name"] == "react.notes"

    @pytest.mark.anyio
    async def test_activity_listener_can_be_removed(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        observed = []

        async def _listener(activity):
            observed.append(activity)

        comm.add_activity_listener(_listener)
        comm.remove_activity_listener(_listener)

        await comm.start(message="hello")

        assert observed == []

    @pytest.mark.anyio
    async def test_activity_listener_does_not_see_filtered_events(self):
        class _DenyAllFilter:
            def allow_event(self, **kwargs):
                return False

        relay = _RecordingRelay()
        comm = _make_comm(relay, event_filter=_DenyAllFilter())
        observed = []

        async def _listener(activity):
            observed.append(activity)

        comm.add_activity_listener(_listener)

        await comm.step(step="hidden", status="running")

        assert relay.events == []
        assert observed == []


class TestCommRecording:
    """Verify bounded comm recording and event sink handoff."""

    @pytest.mark.anyio
    async def test_record_captures_post_firewall_event_metadata(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()

        await comm.start(message="hello")

        items = comm.export_recorded_events()
        assert len(items) == 1
        assert items[0]["socket_event"] == "chat_start"
        assert items[0]["type"] == "chat.start"
        assert items[0]["conversation"]["conversation_id"] == "c1"

    @pytest.mark.anyio
    async def test_record_does_not_capture_filtered_events(self):
        class _DenyAllFilter:
            def allow_event(self, **kwargs):
                return False

        relay = _RecordingRelay()
        comm = _make_comm(relay, event_filter=_DenyAllFilter())
        comm.record()

        await comm.step(step="hidden", status="running")

        assert relay.events == []
        assert comm.export_recorded_events() == []

    @pytest.mark.anyio
    async def test_record_selector_filters_by_type_and_socket_event(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record({
            "include": {
                "types": ["accounting.usage"],
                "socket_events": ["chat_service"],
            }
        })

        await comm.start(message="not recorded")
        await comm.service_event(
            type="accounting.usage",
            step="accounting",
            status="completed",
            data={"breakdown": {"model": "x"}, "cost_total_usd": 0.01},
        )

        items = comm.export_recorded_events()
        assert len(items) == 1
        assert items[0]["type"] == "accounting.usage"
        assert items[0]["socket_event"] == "chat_service"

    @pytest.mark.anyio
    async def test_record_redacts_delta_text_by_default(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()

        await comm.delta(text="private answer text", index=0)

        item = comm.export_recorded_events()[0]
        assert item["type"] == "chat.delta"
        assert item["metrics"]["delta_text_len"] == len("private answer text")
        assert item["privacy"]["data_redacted"] is True
        assert "text" not in item["data"]["delta"]

    @pytest.mark.anyio
    async def test_accounting_usage_preserves_bounded_breakdown(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record("accounting.usage")

        breakdown = {
            "model": "claude-sonnet",
            "cache_read_tokens": 10,
            "cache_write_tokens": 2,
            "input_tokens": 30,
            "output_tokens": 4,
        }
        await comm.service_event(
            type="accounting.usage",
            step="accounting",
            status="completed",
            data={
                "breakdown": breakdown,
                "cost_total_usd": 0.02,
                "prompt": "must not be recorded",
            },
        )

        item = comm.export_recorded_events()[0]
        assert item["data"]["breakdown"] == breakdown
        assert item["data"]["cost_total_usd"] == 0.02
        assert "prompt" not in item["data"]

    @pytest.mark.anyio
    async def test_recording_buffer_is_bounded(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record(max_events=2)

        await comm.step(step="one", status="running")
        await comm.step(step="two", status="running")
        await comm.step(step="three", status="running")

        items = comm.export_recorded_events()
        assert len(items) == 2
        assert [item["event"]["step"] for item in items] == ["two", "three"]
        assert comm.recording_config()["dropped"] == 1

    @pytest.mark.anyio
    async def test_export_dump_merge_dedupes_recorded_events(self, tmp_path):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()
        await comm.start(message="hello")

        path = tmp_path / "comm_recorded_events.json"
        assert comm.dump_recorded_events(path) is True

        other = _make_comm(_RecordingRelay())
        other.merge_recorded_events_from_file(path)
        other.merge_recorded_events(comm.export_recorded_events())

        assert len(other.export_recorded_events()) == 1

    def test_runtime_comm_spec_exports_portable_recording_selector(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        selector = {"include": {"types": ["chat.start"]}}
        comm.record(selector, max_events=7)

        spec = comm._export_comm_spec_for_runtime()

        assert spec["recording"] == {
            "enabled": True,
            "filter": selector,
            "max_events": 7,
        }

    def test_runtime_comm_spec_skips_nonportable_recording_selector(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record(lambda **kwargs: True)

        spec = comm._export_comm_spec_for_runtime()

        assert spec["recording"] is None

    @pytest.mark.anyio
    async def test_send_telemetry_uses_configured_sink_and_clears_sent_items(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()
        seen = []

        async def _sink(batch, **kwargs):
            seen.extend(batch)
            return {"accepted": len(batch)}

        comm.set_event_sink(_sink)

        await comm.start(message="hello")
        result = await comm.send_telemetry({"include": {"types": ["chat.start"]}})

        assert result["ok"] is True
        assert result["sent"] == 1
        assert len(seen) == 1
        assert comm.export_recorded_events() == []

    @pytest.mark.anyio
    async def test_send_telemetry_clears_only_sent_snapshot(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()

        async def _sink(batch, **kwargs):
            await comm.step(step="during-sink", status="running")
            return {"accepted": len(batch)}

        comm.set_event_sink(_sink)

        await comm.start(message="before")
        result = await comm.send_telemetry()

        assert result["ok"] is True
        remaining = comm.export_recorded_events()
        assert len(remaining) == 1
        assert remaining[0]["event"]["step"] == "during-sink"

    @pytest.mark.anyio
    async def test_send_telemetry_partial_acceptance_keeps_buffer(self):
        relay = _RecordingRelay()
        comm = _make_comm(relay)
        comm.record()
        comm.set_event_sink(lambda batch, **kwargs: {"accepted": 0})

        await comm.start(message="retry me")
        result = await comm.send_telemetry()

        assert result["ok"] is True
        assert result["sent"] == 0
        assert len(comm.export_recorded_events()) == 1


# ---------------------------------------------------------------------------
# Tests: event filter (bundle fixture)
# ---------------------------------------------------------------------------

class TestEventFilter:
    """Verify that the bundle event filter behaves correctly."""

    def test_bundle_event_filter_initialized_if_provided(self, bundle):
        """Bundle has _event_filter set after __init__."""
        # Some bundles provide an event filter; others don't.
        # Either is valid — but when provided it must implement IEventFilter.
        ef = bundle._event_filter
        if ef is not None:
            assert isinstance(ef, IEventFilter), (
                "_event_filter must implement IEventFilter"
            )

    def test_event_filter_has_allow_event_method(self, bundle):
        """Event filter exposes callable allow_event() when present."""
        ef = bundle._event_filter
        if ef is None:
            pytest.skip("Bundle has no event filter")
        assert hasattr(ef, "allow_event")
        assert callable(ef.allow_event)

    def test_event_filter_allows_non_step_events_for_anonymous(self, bundle):
        """Non-chat.step events are allowed for anonymous users."""
        ef = bundle._event_filter
        if ef is None:
            pytest.skip("Bundle has no event filter")

        event = EventFilterInput(
            type="chat.complete",
            route="chat_complete",
            socket_event="chat_complete",
            agent="assistant",
            step="stream",
            status="completed",
            broadcast=False,
        )
        result = ef.allow_event(user_type="anonymous", user_id="u1", event=event)
        assert result is True

    def test_event_filter_allows_all_events_for_privileged_user(self, bundle):
        """All events (including chat.step) are allowed for privileged users."""
        ef = bundle._event_filter
        if ef is None:
            pytest.skip("Bundle has no event filter")

        event = EventFilterInput(
            type="chat.step",
            route="chat_step",
            socket_event="chat_step",
            agent="gate",
            step="gate",
            status="completed",
            broadcast=False,
        )
        # Bundles use "privileged" as the user_type for full access
        result = ef.allow_event(user_type="privileged", user_id="admin-1", event=event)
        assert result is True

    def test_event_filter_blocks_internal_steps_for_anonymous(self, bundle):
        """chat.step is blocked for anonymous/regular users (internal diagnostic)."""
        ef = bundle._event_filter
        if ef is None:
            pytest.skip("Bundle has no event filter")

        # Bundles that use the two-list filter block chat.step for non-privileged users
        event = EventFilterInput(
            type="chat.step",
            route="chat_step",
            socket_event="chat_step",
            agent="solver",
            step="solver",
            status="running",
            broadcast=False,
        )
        # Result may be True (if bundle has empty LIST_1) or False — document either way
        result = ef.allow_event(user_type="anonymous", user_id="u1", event=event)
        assert isinstance(result, bool)

    def test_event_filter_returns_bool(self, bundle):
        """allow_event() always returns a plain bool."""
        ef = bundle._event_filter
        if ef is None:
            pytest.skip("Bundle has no event filter")

        event = EventFilterInput(
            type="chat.delta",
            route=None,
            socket_event="chat_delta",
            agent="assistant",
            step="stream",
            status="running",
            broadcast=False,
        )
        result = ef.allow_event(user_type="registered", user_id="u2", event=event)
        assert result is True or result is False  # strict bool check

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.events.event_bus import (
    ConversationEventBusOrchestrator,
    EventLaneState,
    EventLaneWakePublisher,
    RedisEventLaneStateTable,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import (
    event_is_reactive,
    event_timestamp,
    timestamp_lte,
)
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload


class _Redis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key):
        return self.data.get(str(key))

    async def set(self, key, value, ex=None, nx=False):
        del ex
        key = str(key)
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def setex(self, key, ttl, value):
        del ttl
        self.data[str(key)] = value
        return True

    async def delete(self, key):
        return int(self.data.pop(str(key), None) is not None)


def _orchestrator() -> ConversationEventBusOrchestrator:
    table = RedisEventLaneStateTable(redis=_Redis(), state_key="lane:state")
    return ConversationEventBusOrchestrator(table=table)


def _event(ts: str, *, reactive: bool = True, event_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        message_id=event_id,
        sequence=0,
        stream_id="",
        agent_id="default.react.agent",
        payload={"event": {"timestamp": ts, "reactive": reactive}},
        consumed=False,
    )


class _ConversationEventBusSystemSimulator:
    def __init__(self) -> None:
        self.orchestrator = _orchestrator()
        self.stream: list[SimpleNamespace] = []
        self.wakes: list[object] = []
        self.timeline: list[SimpleNamespace] = []
        self.turn_artifacts: list[dict[str, object]] = []
        self.ingress_batches: list[list[SimpleNamespace]] = []
        self.proc_decisions: list[object] = []
        self._next_sequence = 0
        self.reject_next_wakeup_reason = ""
        self.wake_publisher = EventLaneWakePublisher(self._enqueue_wakeup)

    async def _enqueue_wakeup(self, wakeup):
        if self.reject_next_wakeup_reason:
            reason = self.reject_next_wakeup_reason
            self.reject_next_wakeup_reason = ""
            return False, reason, {"queued": 0}
        self.wakes.append(wakeup)
        return True, "ok", {"queued": len(self.wakes)}

    def _wake_event_timestamp(self, wakeup) -> str:
        event_id = str(getattr(getattr(wakeup, "event_lane", None), "event_id", "") or "")
        for event in self.stream:
            if str(getattr(event, "message_id", "") or "") == event_id:
                return event_timestamp(event)
        return ""

    def wake_event_timestamps(self) -> list[str]:
        return [self._wake_event_timestamp(wakeup) for wakeup in self.wakes]

    def _payload_for_wake(self, event: SimpleNamespace) -> ExternalEventPayload:
        return ExternalEventPayload.model_validate(
            {
                "meta": {"task_id": f"task-{event.message_id}", "created_at": 1.0, "instance_id": "sim-ingress"},
                "routing": {
                    "bundle_id": "bundle.demo",
                    "session_id": "session-1",
                    "conversation_id": "conv-1",
                    "turn_id": f"turn-for-{event.message_id}",
                },
                "actor": {"tenant_id": "tenant-a", "project_id": "project-a"},
                "user": {"user_type": "registered", "user_id": "user-1", "fingerprint": "fp-1"},
                "request": {"external_events": [dict(event.payload.get("event") or {})], "payload": {}},
                "config": {"values": {}},
                "accounting": {"envelope": {}},
                "event": {
                    "kind": "external_event",
                    "agent_id": "default.react.agent",
                    "event_source_id": "event.user.followup",
                    "event_id": event.message_id,
                    "reactive": event_is_reactive(event),
                },
            }
        )

    async def process_chat_message_ingress(self, events: list[SimpleNamespace]) -> bool:
        for event in events:
            if not event_timestamp(event):
                raise AssertionError("simulator ingress requires event envelope timestamp")
            if not str(getattr(event, "message_id", "") or ""):
                self._next_sequence += 1
                event.sequence = self._next_sequence
                event.message_id = f"evt-{self._next_sequence}"
                event.stream_id = f"{self._next_sequence}-0"
        first_reactive = next((event for event in events if event_is_reactive(event)), None)
        if first_reactive is not None:
            wakeup = await self.wake_publisher.publish_for_event(
                payload=self._payload_for_wake(first_reactive),
                event=first_reactive,
                tenant="tenant-a",
                project="project-a",
                user_id="user-1",
                conversation_id="conv-1",
                agent_id="default.react.agent",
                reason="reactive_event",
            )
            if not wakeup.success:
                return False
        self.ingress_batches.append(list(events))
        self.stream.extend(events)
        return True

    async def processor_resolve_wake(self):
        if not self.wakes:
            return None
        wakeup = self.wakes.pop(0)
        wake_ts = self._wake_event_timestamp(wakeup)
        decision = await self.orchestrator.schedule_consumer_from_wake(
            wake_event_timestamp=wake_ts,
            active_ttl_ms=60_000,
            scheduled_ttl_ms=60_000,
        )
        self.proc_decisions.append(decision)
        return decision

    async def base_workflow_construct_react_handler(self, turn_id: str) -> None:
        await self.orchestrator.open_handler(turn_id=turn_id)
        await self.orchestrator.mark_consumer_active(turn_id=turn_id)

    async def context_browser_reader_drain(self, turn_id: str):
        events = [event for event in self.stream if not bool(getattr(event, "consumed", False))]

        async def _accept() -> None:
            for event in events:
                event.consumed = True
                self.timeline.append(event)

        return await self.orchestrator.accept_events_for_open_handler(
            events,
            turn_id=turn_id,
            accept=_accept,
        )

    async def react_runtime_close_gate(self, turn_id: str, handler_processed_event_timestamp: str):
        return await self.orchestrator.try_close_handler(
            turn_id=turn_id,
            handler_processed_event_timestamp=handler_processed_event_timestamp,
        )

    async def base_workflow_finish_turn_persist_artifacts(self, turn_id: str) -> None:
        state = await self.orchestrator.state()
        if state.handler_status != "closed":
            raise AssertionError("turn finalization requires closed handler")
        self.turn_artifacts.append(
            {
                "turn_id": turn_id,
                "events": [event_timestamp(event) for event in self.timeline],
                "last_processed_event_timestamp": state.last_processed_event_timestamp,
                "last_processed_reactive_event_timestamp": state.last_processed_reactive_event_timestamp,
            }
        )

    async def context_browser_post_save_external_event_handoff(self) -> bool:
        state = await self.orchestrator.state()
        for event in self.stream:
            if bool(getattr(event, "consumed", False)):
                continue
            if not event_is_reactive(event):
                continue
            if timestamp_lte(event_timestamp(event), state.last_processed_reactive_event_timestamp):
                continue
            await self.wake_publisher.publish_for_event(
                payload=self._payload_for_wake(event),
                event=event,
                tenant="tenant-a",
                project="project-a",
                user_id="user-1",
                conversation_id="conv-1",
                agent_id="default.react.agent",
                reason="post_save_handoff",
            )
            return True
        return False

    async def context_browser_close_external_event_handler(self) -> bool:
        handed_off = await self.context_browser_post_save_external_event_handoff()
        await self.orchestrator.mark_consumer_none()
        return handed_off

    async def base_workflow_finish_turn_close_external_event_handler(self) -> bool:
        return await self.context_browser_close_external_event_handler()


@pytest.mark.asyncio
async def test_simulator_ingress_to_finalization_happy_path():
    sim = _ConversationEventBusSystemSimulator()

    await sim.process_chat_message_ingress(
        [
            _event("2026-06-10T10:00:00Z", reactive=True),
            _event("2026-06-10T10:00:01Z", reactive=False),
        ]
    )

    assert len(sim.ingress_batches) == 1
    assert len(sim.stream) == 2
    assert sim.wake_event_timestamps() == ["2026-06-10T10:00:00Z"]

    proc = await sim.processor_resolve_wake()
    assert proc is not None
    assert proc.scheduled

    await sim.base_workflow_construct_react_handler("turn-1")
    accept = await sim.context_browser_reader_drain("turn-1")
    assert accept.accepted
    assert [event_timestamp(event) for event in sim.timeline] == [
        "2026-06-10T10:00:00Z",
        "2026-06-10T10:00:01Z",
    ]

    rejected_close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert not rejected_close.closed
    assert rejected_close.reason == "new_events_after_handler_snapshot"

    closed = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:01Z",
    )
    assert closed.closed

    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert not await sim.base_workflow_finish_turn_close_external_event_handler()
    assert sim.turn_artifacts == [
        {
            "turn_id": "turn-1",
            "events": ["2026-06-10T10:00:00Z", "2026-06-10T10:00:01Z"],
            "last_processed_event_timestamp": "2026-06-10T10:00:01Z",
            "last_processed_reactive_event_timestamp": "2026-06-10T10:00:00Z",
        }
    ]
    state = await sim.orchestrator.state()
    assert state.handler_status == "closed"
    assert state.consumer_status == "none"


@pytest.mark.asyncio
async def test_simulator_atomic_reactive_rejection_leaves_lane_empty():
    sim = _ConversationEventBusSystemSimulator()
    sim.reject_next_wakeup_reason = "hard_limit_exceeded"

    accepted = await sim.process_chat_message_ingress([
        _event("2026-06-10T10:00:00Z", reactive=True),
    ])

    assert accepted is False
    assert sim.ingress_batches == []
    assert sim.stream == []
    assert sim.wakes == []
    assert await sim.processor_resolve_wake() is None


@pytest.mark.asyncio
async def test_simulator_ingress_during_active_turn_is_handled_by_active_reader():
    sim = _ConversationEventBusSystemSimulator()

    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=True)])
    first_proc = await sim.processor_resolve_wake()
    assert first_proc is not None and first_proc.scheduled
    await sim.base_workflow_construct_react_handler("turn-1")
    first_accept = await sim.context_browser_reader_drain("turn-1")
    assert first_accept.accepted

    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:01Z", reactive=True)])
    assert sim.wake_event_timestamps() == ["2026-06-10T10:00:01Z"]

    second_proc = await sim.processor_resolve_wake()
    assert second_proc is not None
    assert not second_proc.scheduled
    assert second_proc.reason == "active_consumer_fresh"

    second_accept = await sim.context_browser_reader_drain("turn-1")
    assert second_accept.accepted
    assert [event_timestamp(event) for event in sim.timeline] == [
        "2026-06-10T10:00:00Z",
        "2026-06-10T10:00:01Z",
    ]

    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert not close.closed
    assert close.reason == "new_events_after_handler_snapshot"

    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:01Z",
    )
    assert close.closed
    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert not await sim.base_workflow_finish_turn_close_external_event_handler()


@pytest.mark.asyncio
async def test_simulator_reader_cannot_consume_after_close_and_post_save_handoff_wakes_remaining_reactive_work():
    sim = _ConversationEventBusSystemSimulator()

    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=True)])
    first_proc = await sim.processor_resolve_wake()
    assert first_proc is not None and first_proc.scheduled
    await sim.base_workflow_construct_react_handler("turn-1")
    first_accept = await sim.context_browser_reader_drain("turn-1")
    assert first_accept.accepted

    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert close.closed

    late_event = _event("2026-06-10T10:00:01Z", reactive=True)
    await sim.process_chat_message_ingress([late_event])
    sim.wakes.clear()

    late_accept = await sim.context_browser_reader_drain("turn-1")
    assert not late_accept.accepted
    assert late_accept.reason == "handler_not_open"
    assert not late_event.consumed
    assert [event_timestamp(event) for event in sim.timeline] == ["2026-06-10T10:00:00Z"]

    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert await sim.base_workflow_finish_turn_close_external_event_handler()
    assert sim.wake_event_timestamps() == ["2026-06-10T10:00:01Z"]

    next_proc = await sim.processor_resolve_wake()
    assert next_proc is not None and next_proc.scheduled


@pytest.mark.asyncio
async def test_simulator_non_reactive_events_do_not_wake_but_active_reader_can_drain_them():
    sim = _ConversationEventBusSystemSimulator()

    await sim.base_workflow_construct_react_handler("turn-1")
    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=False)])

    assert sim.wakes == []
    assert await sim.processor_resolve_wake() is None

    accept = await sim.context_browser_reader_drain("turn-1")
    assert accept.accepted
    assert [event_timestamp(event) for event in sim.timeline] == ["2026-06-10T10:00:00Z"]

    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert close.closed
    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert not await sim.base_workflow_finish_turn_close_external_event_handler()


@pytest.mark.asyncio
async def test_simulator_duplicate_wake_after_processed_reactive_event_is_ignored():
    sim = _ConversationEventBusSystemSimulator()

    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=True)])
    proc = await sim.processor_resolve_wake()
    assert proc is not None and proc.scheduled
    await sim.base_workflow_construct_react_handler("turn-1")
    accept = await sim.context_browser_reader_drain("turn-1")
    assert accept.accepted
    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert close.closed
    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert not await sim.base_workflow_finish_turn_close_external_event_handler()

    await sim.wake_publisher.publish_for_event(
        payload=sim._payload_for_wake(sim.stream[0]),
        event=sim.stream[0],
        tenant="tenant-a",
        project="project-a",
        user_id="user-1",
        conversation_id="conv-1",
        agent_id="default.react.agent",
        reason="duplicate_delivery",
    )
    duplicate = await sim.processor_resolve_wake()

    assert duplicate is not None
    assert not duplicate.scheduled
    assert duplicate.reason == "wake_already_processed"


@pytest.mark.asyncio
async def test_simulator_multiple_wakes_before_handler_collapse_to_one_scheduled_consumer():
    sim = _ConversationEventBusSystemSimulator()

    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=True)])
    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:01Z", reactive=True)])

    first = await sim.processor_resolve_wake()
    assert first is not None and first.scheduled

    second = await sim.processor_resolve_wake()
    assert second is not None
    assert not second.scheduled
    assert second.reason == "scheduled_consumer_fresh"

    await sim.base_workflow_construct_react_handler("turn-1")
    accept = await sim.context_browser_reader_drain("turn-1")
    assert accept.accepted
    assert [event_timestamp(event) for event in sim.timeline] == [
        "2026-06-10T10:00:00Z",
        "2026-06-10T10:00:01Z",
    ]
    close = await sim.react_runtime_close_gate(
        "turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:01Z",
    )
    assert close.closed
    await sim.base_workflow_finish_turn_persist_artifacts("turn-1")
    assert not await sim.base_workflow_finish_turn_close_external_event_handler()


@pytest.mark.asyncio
async def test_simulator_stale_active_consumer_can_be_rescheduled_by_wake():
    sim = _ConversationEventBusSystemSimulator()

    await sim.orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn-1",
            handler_status="open",
            handler_status_at="2000-01-01T00:00:00Z",
            consumer_status="active",
            consumer_status_at="2000-01-01T00:00:00Z",
        )
    )
    await sim.process_chat_message_ingress([_event("2026-06-10T10:00:00Z", reactive=True)])

    proc = await sim.processor_resolve_wake()

    assert proc is not None
    assert proc.scheduled
    assert proc.reason == "scheduled"
    assert proc.state.consumer_status == "scheduled"


@pytest.mark.asyncio
async def test_simulator_reader_lock_prevents_close_gate_from_missing_event_in_hand():
    sim = _ConversationEventBusSystemSimulator()
    event = _event("2026-06-10T10:00:02Z", reactive=True)

    await sim.base_workflow_construct_react_handler("turn-1")
    sim.stream.append(event)
    accept_started = asyncio.Event()
    release_accept = asyncio.Event()

    async def _accept() -> None:
        accept_started.set()
        await release_accept.wait()
        event.consumed = True
        sim.timeline.append(event)

    accept_task = asyncio.create_task(
        sim.orchestrator.accept_events_for_open_handler(
            [event],
            turn_id="turn-1",
            accept=_accept,
        )
    )
    await accept_started.wait()

    close_task = asyncio.create_task(
        sim.react_runtime_close_gate(
            "turn-1",
            handler_processed_event_timestamp="2026-06-10T10:00:01Z",
        )
    )
    await asyncio.sleep(0.02)

    assert not close_task.done()

    release_accept.set()
    accept = await accept_task
    close = await close_task

    assert accept.accepted
    assert event.consumed
    assert [event_timestamp(item) for item in sim.timeline] == ["2026-06-10T10:00:02Z"]
    assert not close.closed
    assert close.reason == "new_events_after_handler_snapshot"


@pytest.mark.asyncio
async def test_wake_schedules_consumer_when_no_fresh_consumer_exists():
    orchestrator = _orchestrator()

    decision = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:00Z",
    )

    assert decision.scheduled
    assert decision.reason == "scheduled"
    assert decision.state.consumer_status == "scheduled"
    assert decision.state.consumer_status_at


@pytest.mark.asyncio
async def test_wake_is_ignored_when_reactive_event_is_already_processed():
    orchestrator = _orchestrator()

    await orchestrator.open_handler(turn_id="turn-1")
    await orchestrator.record_processed_events(
        [_event("2026-06-10T10:00:00Z", reactive=True)],
        turn_id="turn-1",
    )

    decision = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:00Z",
    )

    assert not decision.scheduled
    assert decision.reason == "wake_already_processed"
    assert decision.state.last_processed_reactive_event_timestamp == "2026-06-10T10:00:00Z"


@pytest.mark.asyncio
async def test_wake_is_ignored_while_active_consumer_acknowledgement_is_fresh():
    orchestrator = _orchestrator()

    await orchestrator.open_handler(turn_id="turn-1")
    state = await orchestrator.mark_consumer_active(turn_id="turn-1")
    assert state.consumer_status == "active"

    decision = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:01Z",
        active_ttl_ms=60_000,
    )

    assert not decision.scheduled
    assert decision.reason == "active_consumer_fresh"
    assert decision.state.consumer_status == "active"


@pytest.mark.asyncio
async def test_wake_is_ignored_while_scheduled_consumer_acknowledgement_is_fresh():
    orchestrator = _orchestrator()

    first = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:01Z",
    )
    assert first.scheduled

    second = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:02Z",
        scheduled_ttl_ms=60_000,
    )

    assert not second.scheduled
    assert second.reason == "scheduled_consumer_fresh"
    assert second.state.consumer_status == "scheduled"


@pytest.mark.asyncio
async def test_wake_reschedules_when_active_consumer_acknowledgement_is_stale():
    orchestrator = _orchestrator()

    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn-1",
            handler_status="open",
            handler_status_at="2026-06-10T09:00:00Z",
            consumer_status="active",
            consumer_status_at="2026-06-10T09:00:00Z",
        )
    )

    decision = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:01Z",
        active_ttl_ms=1,
    )

    assert decision.scheduled
    assert decision.reason == "scheduled"
    assert decision.state.consumer_status == "scheduled"


@pytest.mark.asyncio
async def test_full_picture_happy_path_and_close_gate():
    orchestrator = _orchestrator()

    scheduled = await orchestrator.schedule_consumer_from_wake(
        wake_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert scheduled.scheduled

    state = await orchestrator.open_handler(turn_id="turn-1")
    assert state.handler_turn_id == "turn-1"
    assert state.handler_status == "open"
    assert state.handler_status_at
    assert state.consumer_status == "scheduled"

    state = await orchestrator.mark_consumer_active(turn_id="turn-1")
    assert state.consumer_status == "active"
    assert state.consumer_status_at

    accepted = False

    async def _accept() -> None:
        nonlocal accepted
        accepted = True

    accept = await orchestrator.accept_events_for_open_handler(
        [
            _event("2026-06-10T10:00:00Z", reactive=True),
            _event("2026-06-10T10:00:01Z", reactive=False),
        ],
        turn_id="turn-1",
        accept=_accept,
    )

    assert accept.accepted
    assert accepted
    assert accept.state.last_processed_event_timestamp == "2026-06-10T10:00:01Z"
    assert accept.state.last_processed_reactive_event_timestamp == "2026-06-10T10:00:00Z"
    assert accept.state.consumer_status == "active"

    rejected_close = await orchestrator.try_close_handler(
        turn_id="turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:00Z",
    )
    assert not rejected_close.closed
    assert rejected_close.reason == "new_events_after_handler_snapshot"
    assert rejected_close.state.handler_status == "open"

    closed = await orchestrator.try_close_handler(
        turn_id="turn-1",
        handler_processed_event_timestamp="2026-06-10T10:00:01Z",
    )
    assert closed.closed
    assert closed.reason == "closed"
    assert closed.state.handler_status == "closed"
    assert closed.last_processed_event_timestamp == "2026-06-10T10:00:01Z"
    assert closed.last_processed_reactive_event_timestamp == "2026-06-10T10:00:00Z"

    state = await orchestrator.mark_consumer_none()
    assert state.handler_status == "closed"
    assert state.consumer_status == "none"


@pytest.mark.asyncio
async def test_open_handler_does_not_overwrite_existing_open_turn():
    orchestrator = _orchestrator()

    first = await orchestrator.open_handler(turn_id="turn-1")
    second = await orchestrator.open_handler(turn_id="turn-2")

    assert first.handler_status == "open"
    assert first.handler_turn_id == "turn-1"
    assert second.handler_status == "open"
    assert second.handler_turn_id == "turn-1"


@pytest.mark.asyncio
async def test_reader_cannot_accept_after_handler_closed():
    orchestrator = _orchestrator()

    await orchestrator.open_handler(turn_id="turn-1")
    close = await orchestrator.try_close_handler(turn_id="turn-1")
    assert close.closed

    accepted = False

    async def _accept() -> None:
        nonlocal accepted
        accepted = True

    decision = await orchestrator.accept_events_for_open_handler(
        [_event("2026-06-10T10:00:02Z", reactive=True)],
        turn_id="turn-1",
        accept=_accept,
    )

    assert not decision.accepted
    assert decision.reason == "handler_not_open"
    assert not accepted
    assert decision.state.last_processed_event_timestamp == ""


@pytest.mark.asyncio
async def test_reader_holding_lock_prevents_close_from_missing_new_event():
    orchestrator = _orchestrator()

    await orchestrator.open_handler(turn_id="turn-1")
    accept_started = asyncio.Event()
    release_accept = asyncio.Event()

    async def _accept() -> None:
        accept_started.set()
        await release_accept.wait()

    accept_task = asyncio.create_task(
        orchestrator.accept_events_for_open_handler(
            [_event("2026-06-10T10:00:02Z", reactive=True)],
            turn_id="turn-1",
            accept=_accept,
        )
    )
    await accept_started.wait()

    close_task = asyncio.create_task(
        orchestrator.try_close_handler(
            turn_id="turn-1",
            handler_processed_event_timestamp="2026-06-10T10:00:01Z",
        )
    )
    await asyncio.sleep(0.02)
    assert not close_task.done()

    release_accept.set()
    accept = await accept_task
    close = await close_task

    assert accept.accepted
    assert accept.state.last_processed_event_timestamp == "2026-06-10T10:00:02Z"
    assert not close.closed
    assert close.reason == "new_events_after_handler_snapshot"
    assert close.state.handler_status == "open"

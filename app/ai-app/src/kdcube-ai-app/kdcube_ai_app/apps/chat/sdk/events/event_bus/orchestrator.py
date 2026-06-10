# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from .state import (
    EventLaneState,
    RedisEventLaneStateTable,
    event_is_reactive,
    event_timestamp,
    later_timestamp,
    timestamp_is_fresh,
    timestamp_lt,
    timestamp_lte,
    utc_timestamp,
)


@dataclass(frozen=True)
class EventBusScheduleDecision:
    scheduled: bool
    state: EventLaneState
    reason: str = ""


@dataclass(frozen=True)
class EventBusCloseDecision:
    closed: bool
    state: EventLaneState
    reason: str = ""
    last_processed_event_timestamp: str = ""
    last_processed_reactive_event_timestamp: str = ""


@dataclass(frozen=True)
class EventBusAcceptDecision:
    accepted: bool
    state: EventLaneState
    reason: str = ""


class ConversationEventBusOrchestrator:
    """Coordination API for the conversation external-event lane."""

    def __init__(self, *, table: RedisEventLaneStateTable) -> None:
        self.table = table

    @classmethod
    def for_source(cls, source: Any) -> "ConversationEventBusOrchestrator":
        return cls(table=RedisEventLaneStateTable.for_source(source))

    async def state(self) -> EventLaneState:
        return await self.table.get()

    async def open_handler(
        self,
        *,
        turn_id: str,
    ) -> EventLaneState:
        turn_id = str(turn_id or "").strip()
        now = utc_timestamp()

        def _mutate(state: EventLaneState) -> EventLaneState:
            if state.handler_status == "open" and state.handler_turn_id and state.handler_turn_id != turn_id:
                return state
            state.handler_turn_id = turn_id
            state.handler_status = "open"
            state.handler_status_at = now
            return state

        return await self.table.update(_mutate)

    async def try_close_handler(
        self,
        *,
        turn_id: str,
        handler_processed_event_timestamp: str = "",
    ) -> EventBusCloseDecision:
        turn_id = str(turn_id or "").strip()
        processed_ts = str(handler_processed_event_timestamp or "").strip()
        now = utc_timestamp()

        async with self.table.lock():
            state = await self.table.get()
            if state.handler_turn_id != turn_id or state.handler_status != "open":
                return EventBusCloseDecision(
                    closed=False,
                    state=state,
                    reason="handler_not_open",
                )
            if timestamp_lt(processed_ts, state.last_processed_event_timestamp):
                return EventBusCloseDecision(
                    closed=False,
                    state=state,
                    reason="new_events_after_handler_snapshot",
                )
            state.handler_status = "closed"
            state.handler_status_at = now
            await self.table.put(state)
            return EventBusCloseDecision(
                closed=True,
                state=state,
                reason="closed",
                last_processed_event_timestamp=state.last_processed_event_timestamp,
                last_processed_reactive_event_timestamp=state.last_processed_reactive_event_timestamp,
            )

    async def schedule_consumer_from_wake(
        self,
        *,
        wake_event_timestamp: str,
        active_ttl_ms: int = 30_000,
        scheduled_ttl_ms: int = 30_000,
    ) -> EventBusScheduleDecision:
        wake_ts = str(wake_event_timestamp or "").strip()
        now = utc_timestamp()

        async with self.table.lock():
            state = await self.table.get()
            if timestamp_lte(wake_ts, state.last_processed_reactive_event_timestamp):
                return EventBusScheduleDecision(
                    scheduled=False,
                    state=state,
                    reason="wake_already_processed",
                )
            if state.consumer_status == "active" and timestamp_is_fresh(
                now=now,
                since=state.consumer_status_at,
                ttl_ms=active_ttl_ms,
            ):
                return EventBusScheduleDecision(
                    scheduled=False,
                    state=state,
                    reason="active_consumer_fresh",
                )
            if state.consumer_status == "scheduled" and timestamp_is_fresh(
                now=now,
                since=state.consumer_status_at,
                ttl_ms=scheduled_ttl_ms,
            ):
                return EventBusScheduleDecision(
                    scheduled=False,
                    state=state,
                    reason="scheduled_consumer_fresh",
                )
            state.consumer_status = "scheduled"
            state.consumer_status_at = now
            await self.table.put(state)
            return EventBusScheduleDecision(
                scheduled=True,
                state=state,
                reason="scheduled",
            )

    async def mark_consumer_active(self, *, turn_id: str = "") -> EventLaneState:
        turn_id = str(turn_id or "").strip()
        now = utc_timestamp()

        def _mutate(state: EventLaneState) -> EventLaneState:
            if state.handler_status != "open":
                return state
            if turn_id and state.handler_turn_id and state.handler_turn_id != turn_id:
                return state
            state.consumer_status = "active"
            state.consumer_status_at = now
            return state

        return await self.table.update(_mutate)

    async def mark_consumer_none(self) -> EventLaneState:
        now = utc_timestamp()

        def _mutate(state: EventLaneState) -> EventLaneState:
            state.consumer_status = "none"
            state.consumer_status_at = now
            return state

        return await self.table.update(_mutate)

    async def record_processed_events(self, events: Sequence[Any], *, turn_id: str = "") -> EventLaneState:
        max_ts = ""
        max_reactive_ts = ""
        for event in events or []:
            ts = event_timestamp(event)
            max_ts = later_timestamp(max_ts, ts)
            if event_is_reactive(event):
                max_reactive_ts = later_timestamp(max_reactive_ts, ts)

        def _mutate(state: EventLaneState) -> EventLaneState:
            if state.handler_status != "open":
                return state
            if turn_id and state.handler_turn_id and state.handler_turn_id != str(turn_id or ""):
                return state
            state.last_processed_event_timestamp = later_timestamp(
                state.last_processed_event_timestamp,
                max_ts,
            )
            state.last_processed_reactive_event_timestamp = later_timestamp(
                state.last_processed_reactive_event_timestamp,
                max_reactive_ts,
            )
            state.consumer_status = "active"
            state.consumer_status_at = utc_timestamp()
            return state

        return await self.table.update(_mutate)

    async def accept_events_for_open_handler(
        self,
        events: Sequence[Any],
        *,
        turn_id: str = "",
        accept: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> EventBusAcceptDecision:
        turn_id = str(turn_id or "").strip()
        max_ts = ""
        max_reactive_ts = ""
        for event in events or []:
            ts = event_timestamp(event)
            max_ts = later_timestamp(max_ts, ts)
            if event_is_reactive(event):
                max_reactive_ts = later_timestamp(max_reactive_ts, ts)

        now = utc_timestamp()
        async with self.table.lock():
            state = await self.table.get()
            if state.handler_status != "open":
                return EventBusAcceptDecision(
                    accepted=False,
                    state=state,
                    reason="handler_not_open",
                )
            if turn_id and state.handler_turn_id and state.handler_turn_id != turn_id:
                return EventBusAcceptDecision(
                    accepted=False,
                    state=state,
                    reason="handler_turn_mismatch",
                )
            if accept is not None:
                await accept()
            state.last_processed_event_timestamp = later_timestamp(
                state.last_processed_event_timestamp,
                max_ts,
            )
            state.last_processed_reactive_event_timestamp = later_timestamp(
                state.last_processed_reactive_event_timestamp,
                max_reactive_ts,
            )
            state.consumer_status = "active"
            state.consumer_status_at = now
            await self.table.put(state)
            return EventBusAcceptDecision(
                accepted=True,
                state=state,
                reason="accepted",
            )

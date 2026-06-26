# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import (
    EventLaneState,
    RedisEventLaneStateTable,
    event_id,
    event_is_reactive,
    event_timestamp,
    later_timestamp,
    timestamp_is_fresh,
    timestamp_lt,
    timestamp_lte,
    utc_timestamp,
)

logger = logging.getLogger(__name__)


# A handler lane is owned by exactly one turn at a time. If that turn crashes or
# its worker reloads before calling try_close_handler(), the lane stays
# handler_status="open" under a now-dead handler_turn_id, and every later turn's
# open_handler() used to defer to it forever -> a permanent close-gate wedge.
#
# Liveness is a Reader/Consumer property, not a handler-status property. Only
# an active consumer heartbeat proves that the owner is still consuming. A
# scheduled wake may be written by a later user event before the next turn opens
# the handler, so it must not protect a stale handler owner.
def _consumer_turn_ttl_ms() -> int:
    try:
        raw = os.getenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS") or "30"
        seconds = int(float(raw or 30))
    except Exception:
        seconds = 30
    if seconds <= 0:
        seconds = 30
    return seconds * 1000


def _consumer_ack_is_fresh(status_at: str, now: str) -> bool:
    # Empty/missing/malformed status_at -> not fresh -> reclaimable (self-heal).
    # timestamp_is_fresh handles parse errors by returning age=inf -> False.
    return timestamp_is_fresh(now=now, since=status_at, ttl_ms=_consumer_turn_ttl_ms())


def _consumer_state_is_fresh(state: EventLaneState, now: str) -> bool:
    return (
        state.consumer_status == "active"
        and _consumer_ack_is_fresh(state.consumer_status_at, now)
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

    def __init__(
        self,
        *,
        table: RedisEventLaneStateTable,
        source: Any = None,
    ) -> None:
        self.table = table
        self.source = source
        # Transient signal from the most recent open_handler() call: True when
        # that call reclaimed a stale-open lane from a previous (crashed or
        # superseded) owner, with the previous owner's turn id. Not persisted;
        # the caller reads it right after open_handler() to surface a one-time
        # "previous response was interrupted, regenerating" notice to the user.
        self.last_open_reclaimed: bool = False
        self.last_open_reclaimed_prev_owner: str = ""

    @classmethod
    def for_source(cls, source: Any) -> "ConversationEventBusOrchestrator":
        return cls(table=RedisEventLaneStateTable.for_source(source), source=source)

    async def state(self) -> EventLaneState:
        return await self.table.get()

    async def open_handler(
        self,
        *,
        turn_id: str,
    ) -> EventLaneState:
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            return await self.table.get()

        now = utc_timestamp()
        self.last_open_reclaimed = False
        self.last_open_reclaimed_prev_owner = ""
        async with self.table.lock():
            state = await self.table.get()
            if state.handler_status == "open" and state.handler_turn_id and state.handler_turn_id != turn_id:
                if _consumer_state_is_fresh(state, now):
                    return state
                logger.warning(
                    "[event-bus] reclaiming stale-open handler turn=%s -> %s "
                    "(consumer_status=%s consumer_status_at=%s age > TTL)",
                    state.handler_turn_id,
                    turn_id,
                    state.consumer_status or "<empty>",
                    state.consumer_status_at or "<empty>",
                )
                self.last_open_reclaimed = True
                self.last_open_reclaimed_prev_owner = str(state.handler_turn_id or "")
            state.handler_turn_id = turn_id
            state.handler_status = "open"
            state.handler_status_at = now
            await self.table.put(state)
            return state

    async def try_close_handler(
        self,
        *,
        turn_id: str,
        handler_processed_event_timestamp: str = "",
        handler_processed_event_id: str = "",
    ) -> EventBusCloseDecision:
        turn_id = str(turn_id or "").strip()
        processed_ts = str(handler_processed_event_timestamp or "").strip()
        processed_event_id = str(handler_processed_event_id or "").strip()
        now = utc_timestamp()

        async with self.table.lock():
            state = await self.table.get()
            if state.handler_turn_id != turn_id or state.handler_status != "open":
                return EventBusCloseDecision(
                    closed=False,
                    state=state,
                    reason="handler_not_open",
                )
            processed_before_state = timestamp_lt(processed_ts, state.last_processed_event_timestamp)
            if (
                not processed_before_state
                and processed_ts
                and state.last_processed_event_timestamp
                and processed_ts == state.last_processed_event_timestamp
                and state.last_processed_event_id
                and processed_event_id
                and processed_event_id != state.last_processed_event_id
            ):
                processed_before_state = True
            if processed_before_state:
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

    async def mark_consumer_none(self, *, turn_id: str = "") -> EventLaneState:
        turn_id = str(turn_id or "").strip()
        now = utc_timestamp()

        def _mutate(state: EventLaneState) -> EventLaneState:
            if turn_id and state.handler_turn_id and state.handler_turn_id != turn_id:
                return state
            state.consumer_status = "none"
            state.consumer_status_at = now
            return state

        return await self.table.update(_mutate)

    async def record_processed_events(self, events: Sequence[Any], *, turn_id: str = "") -> EventLaneState:
        max_ts = ""
        max_event_id = ""
        max_reactive_ts = ""
        for event in events or []:
            ts = event_timestamp(event)
            next_max_ts = later_timestamp(max_ts, ts)
            if ts and next_max_ts == ts:
                max_event_id = event_id(event)
            max_ts = next_max_ts
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
            if max_ts and state.last_processed_event_timestamp == max_ts:
                state.last_processed_event_id = max_event_id
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
        max_event_id = ""
        max_reactive_ts = ""
        for event in events or []:
            ts = event_timestamp(event)
            next_max_ts = later_timestamp(max_ts, ts)
            if ts and next_max_ts == ts:
                max_event_id = event_id(event)
            max_ts = next_max_ts
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
            if max_ts and state.last_processed_event_timestamp == max_ts:
                state.last_processed_event_id = max_event_id
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

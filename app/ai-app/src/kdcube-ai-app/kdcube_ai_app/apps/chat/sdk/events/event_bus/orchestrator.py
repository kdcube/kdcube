# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from .state import (
    EventLaneState,
    RedisEventLaneStateTable,
    event_id,
    event_is_handler_probe,
    event_is_reactive,
    event_timestamp,
    later_timestamp,
    timestamp_is_fresh,
    timestamp_lt,
    timestamp_lte,
    utc_timestamp,
)

logger = logging.getLogger(__name__)

_DEFAULT_HANDLER_PROBE_TIMEOUT_MS = 750


# A handler lane is owned by exactly one turn at a time. If that turn crashes or
# its worker reloads before calling try_close_handler(), the lane stays
# handler_status="open" under a now-dead handler_turn_id, and every later turn's
# open_handler() used to defer to it forever -> a permanent close-gate wedge.
#
# Liveness is a Reader/Consumer property, not a handler-status property. The
# first-class check is a probe appended to the same conversation event lane. If
# a probe-capable source is unavailable, this TTL is only a conservative
# fallback against consumer_status_at, the last "I ate the stream" timestamp.
def _consumer_turn_ttl_ms() -> int:
    try:
        raw = os.getenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS") or "600"
        seconds = int(float(raw or 600))
    except Exception:
        seconds = 600
    if seconds <= 0:
        seconds = 600
    return seconds * 1000


def _consumer_ack_is_fresh(status_at: str, now: str) -> bool:
    # Empty/missing/malformed status_at -> not fresh -> reclaimable (self-heal).
    # timestamp_is_fresh handles parse errors by returning age=inf -> False.
    return timestamp_is_fresh(now=now, since=status_at, ttl_ms=_consumer_turn_ttl_ms())


def _consumer_state_is_fresh(state: EventLaneState, now: str) -> bool:
    return (
        state.consumer_status in {"active", "scheduled"}
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
        probe_timeout_ms: int = _DEFAULT_HANDLER_PROBE_TIMEOUT_MS,
    ) -> None:
        self.table = table
        self.source = source
        self.probe_timeout_ms = max(0, int(probe_timeout_ms or 0))

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

        while True:
            now = utc_timestamp()
            probe_owner_turn_id = ""
            async with self.table.lock():
                state = await self.table.get()
                if state.handler_status == "open" and state.handler_turn_id and state.handler_turn_id != turn_id:
                    probe_owner_turn_id = state.handler_turn_id
                    if not self._can_probe_open_handler():
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
                        state.handler_turn_id = turn_id
                        state.handler_status = "open"
                        state.handler_status_at = now
                        await self.table.put(state)
                        return state
                else:
                    state.handler_turn_id = turn_id
                    state.handler_status = "open"
                    state.handler_status_at = now
                    await self.table.put(state)
                    return state

            probe_alive = await self._probe_open_handler(
                owner_turn_id=probe_owner_turn_id,
                challenger_turn_id=turn_id,
            )

            now = utc_timestamp()
            async with self.table.lock():
                state = await self.table.get()
                if state.is_open_for(turn_id):
                    return state
                if (
                    state.handler_status == "open"
                    and state.handler_turn_id
                    and state.handler_turn_id != probe_owner_turn_id
                ):
                    return state
                if (
                    state.handler_status == "open"
                    and state.handler_turn_id == probe_owner_turn_id
                    and probe_alive
                ):
                    return state
                if (
                    state.handler_status == "open"
                    and state.handler_turn_id == probe_owner_turn_id
                ):
                    logger.warning(
                        "[event-bus] reclaiming stale-open handler turn=%s -> %s "
                        "(probe_ack=false consumer_status=%s consumer_status_at=%s)",
                        probe_owner_turn_id,
                        turn_id,
                        state.consumer_status or "<empty>",
                        state.consumer_status_at or "<empty>",
                    )
                state.handler_turn_id = turn_id
                state.handler_status = "open"
                state.handler_status_at = now
                await self.table.put(state)
                return state

    def _can_probe_open_handler(self) -> bool:
        source = self.source
        return bool(
            source is not None
            and callable(getattr(source, "publish_handler_probe", None))
            and callable(getattr(source, "wait_handler_probe_ack", None))
        )

    async def _probe_open_handler(self, *, owner_turn_id: str, challenger_turn_id: str) -> bool:
        source = self.source
        owner_turn_id = str(owner_turn_id or "").strip()
        if not owner_turn_id or not self._can_probe_open_handler():
            return False
        try:
            probe = await source.publish_handler_probe(
                for_turn=owner_turn_id,
                challenger_turn_id=str(challenger_turn_id or "").strip(),
            )
            probe_id = str(
                getattr(probe, "message_id", "")
                or getattr(probe, "event_id", "")
                or ""
            ).strip()
            return bool(
                await source.wait_handler_probe_ack(
                    probe_id=probe_id,
                    for_turn=owner_turn_id,
                    timeout_ms=self.probe_timeout_ms,
                )
            )
        except Exception:
            logger.exception(
                "[event-bus] handler probe failed owner_turn=%s challenger_turn=%s",
                owner_turn_id,
                challenger_turn_id,
            )
            return False

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
            if event_is_handler_probe(event):
                continue
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
            if event_is_handler_probe(event):
                continue
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

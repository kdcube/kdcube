from __future__ import annotations

import contextlib

import pytest

from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import ConversationEventBusOrchestrator
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import EventLaneState, utc_timestamp


class _MemoryLaneStateTable:
    def __init__(self, state: EventLaneState) -> None:
        self.state = state

    async def get(self) -> EventLaneState:
        return self.state

    async def put(self, state: EventLaneState) -> EventLaneState:
        self.state = state
        return state

    async def update(self, mutator):
        self.state = mutator(self.state) or self.state
        return self.state

    @contextlib.asynccontextmanager
    async def lock(self):
        yield "memory-lock"


@pytest.mark.asyncio
async def test_open_handler_reclaims_stale_owner_even_when_wake_is_scheduled():
    table = _MemoryLaneStateTable(
        EventLaneState(
            handler_turn_id="old-turn",
            handler_status="open",
            consumer_status="scheduled",
            consumer_status_at=utc_timestamp(),
        )
    )
    orchestrator = ConversationEventBusOrchestrator(table=table)

    state = await orchestrator.open_handler(turn_id="new-turn")

    assert state.handler_turn_id == "new-turn"
    assert state.handler_status == "open"
    assert orchestrator.last_open_reclaimed is True
    assert orchestrator.last_open_reclaimed_prev_owner == "old-turn"


@pytest.mark.asyncio
async def test_open_handler_keeps_fresh_active_owner():
    table = _MemoryLaneStateTable(
        EventLaneState(
            handler_turn_id="old-turn",
            handler_status="open",
            consumer_status="active",
            consumer_status_at=utc_timestamp(),
        )
    )
    orchestrator = ConversationEventBusOrchestrator(table=table)

    state = await orchestrator.open_handler(turn_id="new-turn")

    assert state.handler_turn_id == "old-turn"
    assert state.handler_status == "open"
    assert orchestrator.last_open_reclaimed is False


@pytest.mark.asyncio
async def test_open_handler_reclaims_stale_active_owner():
    table = _MemoryLaneStateTable(
        EventLaneState(
            handler_turn_id="old-turn",
            handler_status="open",
            consumer_status="active",
            consumer_status_at="1970-01-01T00:00:00Z",
        )
    )
    orchestrator = ConversationEventBusOrchestrator(table=table)

    state = await orchestrator.open_handler(turn_id="new-turn")

    assert state.handler_turn_id == "new-turn"
    assert state.handler_status == "open"
    assert orchestrator.last_open_reclaimed is True
    assert orchestrator.last_open_reclaimed_prev_owner == "old-turn"

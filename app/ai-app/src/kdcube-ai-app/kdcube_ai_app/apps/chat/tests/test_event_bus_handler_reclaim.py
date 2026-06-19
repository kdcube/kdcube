"""Unit tests for stale-open handler reclaim in the conversation event-bus.

A handler lane is owned by exactly one turn. If that turn crashes (or its worker
reloads) before calling ``try_close_handler()``, the lane is left
``handler_status="open"`` under a now-dead ``handler_turn_id``. ``open_handler()``
must defer ONLY to a genuinely live concurrent turn (one whose
``handler_status_at`` is within ``KDCUBE_HANDLER_TURN_TTL_SECONDS``); a stale lane
must be reclaimed by the incoming turn so the close gate can never wedge forever.

All identifiers here are synthetic (turn_OLD / turn_NEW / ...).
"""

from __future__ import annotations

import datetime as _dt

import pytest

from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import (
    ConversationEventBusOrchestrator,
    _handler_open_is_fresh,
    _handler_turn_ttl_ms,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import (
    EventLaneState,
    RedisEventLaneStateTable,
    utc_timestamp,
)


class _Redis:
    """Minimal in-memory async Redis stub mirroring test_event_bus_state.py."""

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


def _ago(seconds: float) -> str:
    """A UTC timestamp ``seconds`` in the past, in the same format as utc_timestamp()."""
    moment = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Helper-level coverage: _handler_turn_ttl_ms / _handler_open_is_fresh        #
# --------------------------------------------------------------------------- #

def test_handler_turn_ttl_ms_defaults_to_600s(monkeypatch):
    monkeypatch.delenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", raising=False)
    assert _handler_turn_ttl_ms() == 600_000


@pytest.mark.parametrize("bad", ["0", "-5", "", "not-a-number"])
def test_handler_turn_ttl_ms_falls_back_on_invalid(monkeypatch, bad):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", bad)
    assert _handler_turn_ttl_ms() == 600_000


def test_handler_turn_ttl_ms_honors_env(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    assert _handler_turn_ttl_ms() == 30_000


def test_handler_open_is_fresh_true_within_ttl(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "60")
    now = utc_timestamp()
    assert _handler_open_is_fresh(_ago(5), now) is True


def test_handler_open_is_fresh_false_when_older_than_ttl(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    now = utc_timestamp()
    assert _handler_open_is_fresh(_ago(120), now) is False


def test_handler_open_is_fresh_false_on_empty_status_at(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "600")
    now = utc_timestamp()
    # Empty/missing status_at -> age == inf -> not fresh -> reclaimable.
    assert _handler_open_is_fresh("", now) is False
    assert _handler_open_is_fresh("not-a-timestamp", now) is False


# --------------------------------------------------------------------------- #
# open_handler() behaviour                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_open_handler_reclaims_stale_open_lane(monkeypatch):
    # Short TTL keeps the test deterministic without sleeping.
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    orchestrator = _orchestrator()

    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_OLD",
            handler_status="open",
            handler_status_at=_ago(120),  # well beyond the 30s TTL -> stale
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    # status_at was advanced to "now" (no longer the stale timestamp).
    assert state.handler_status_at
    assert not state.handler_status_at.startswith("20") or state.handler_status_at != _ago(120)
    # The advanced timestamp is itself fresh under the TTL.
    assert _handler_open_is_fresh(state.handler_status_at, utc_timestamp()) is True


@pytest.mark.asyncio
async def test_open_handler_reclaims_lane_with_empty_status_at(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "600")
    orchestrator = _orchestrator()

    # Malformed/empty handler_status_at on an "open" lane -> treated as stale.
    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_OLD",
            handler_status="open",
            handler_status_at="",
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at  # now stamped


@pytest.mark.asyncio
async def test_open_handler_does_not_steal_fresh_open_lane(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "600")
    orchestrator = _orchestrator()

    fresh_at = _ago(2)  # well within the 600s TTL -> a live concurrent turn
    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_OTHER",
            handler_status="open",
            handler_status_at=fresh_at,
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    # Deferred: the live owner keeps the lane untouched.
    assert state.handler_turn_id == "turn_OTHER"
    assert state.handler_status == "open"
    assert state.handler_status_at == fresh_at


@pytest.mark.asyncio
async def test_open_handler_opens_empty_lane(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    orchestrator = _orchestrator()

    # Default empty lane (handler_status == "").
    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at


@pytest.mark.asyncio
async def test_open_handler_opens_closed_lane(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    orchestrator = _orchestrator()

    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_OLD",
            handler_status="closed",
            handler_status_at=_ago(1),
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at


@pytest.mark.asyncio
async def test_open_handler_is_idempotent_for_same_turn(monkeypatch):
    monkeypatch.setenv("KDCUBE_HANDLER_TURN_TTL_SECONDS", "30")
    orchestrator = _orchestrator()

    # An "open" lane already owned by the SAME incoming turn (even if its
    # status_at looks stale) is simply re-stamped, not deferred.
    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_NEW",
            handler_status="open",
            handler_status_at=_ago(120),
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert _handler_open_is_fresh(state.handler_status_at, utc_timestamp()) is True

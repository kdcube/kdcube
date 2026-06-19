"""Unit tests for stale-open handler reclaim in the conversation event bus.

A handler lane is owned by exactly one turn. If that turn crashes or its worker
reloads before calling ``try_close_handler()``, the lane can remain
``handler_status="open"`` under a now-dead ``handler_turn_id``.

Liveness is a Reader/Consumer property. ``open_handler()`` should defer to an
existing owner only when that owner proves it can still consume the same lane:

* first by acknowledging a probe appended to the lane, when the source supports
  the probe protocol;
* otherwise by a fresh ``consumer_status_at`` fallback.

``handler_status_at`` is not a liveness signal.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import (
    ConversationEventBusOrchestrator,
    _consumer_ack_is_fresh,
    _consumer_turn_ttl_ms,
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


class _ProbeSource:
    def __init__(self, *, ack: bool):
        self.ack = bool(ack)
        self.probes: list[dict[str, str]] = []

    async def publish_handler_probe(self, *, for_turn: str, challenger_turn_id: str = ""):
        probe_id = f"probe_{len(self.probes) + 1}"
        self.probes.append(
            {
                "probe_id": probe_id,
                "for_turn": str(for_turn or ""),
                "challenger_turn_id": str(challenger_turn_id or ""),
            }
        )
        return SimpleNamespace(message_id=probe_id)

    async def wait_handler_probe_ack(self, *, probe_id: str, for_turn: str, timeout_ms: int = 750):
        self.probes[-1]["wait_probe_id"] = str(probe_id or "")
        self.probes[-1]["wait_for_turn"] = str(for_turn or "")
        self.probes[-1]["timeout_ms"] = str(timeout_ms)
        return self.ack


def _orchestrator(*, source=None, probe_timeout_ms: int = 1) -> ConversationEventBusOrchestrator:
    table = RedisEventLaneStateTable(redis=_Redis(), state_key="lane:state")
    return ConversationEventBusOrchestrator(table=table, source=source, probe_timeout_ms=probe_timeout_ms)


def _ago(seconds: float) -> str:
    moment = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _open_state(
    *,
    handler_status_at: str = "",
    consumer_status: str = "",
    consumer_status_at: str = "",
) -> EventLaneState:
    return EventLaneState(
        handler_turn_id="turn_OLD",
        handler_status="open",
        handler_status_at=handler_status_at,
        consumer_status=consumer_status,
        consumer_status_at=consumer_status_at,
    )


# --------------------------------------------------------------------------- #
# Helper-level coverage: consumer freshness fallback                           #
# --------------------------------------------------------------------------- #


def test_consumer_turn_ttl_ms_defaults_to_600s(monkeypatch):
    monkeypatch.delenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", raising=False)
    assert _consumer_turn_ttl_ms() == 600_000


@pytest.mark.parametrize("bad", ["0", "-5", "", "not-a-number"])
def test_consumer_turn_ttl_ms_falls_back_on_invalid(monkeypatch, bad):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", bad)
    assert _consumer_turn_ttl_ms() == 600_000


def test_consumer_turn_ttl_ms_honors_consumer_env(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "30")
    assert _consumer_turn_ttl_ms() == 30_000


def test_consumer_ack_is_fresh_true_within_ttl(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "60")
    now = utc_timestamp()
    assert _consumer_ack_is_fresh(_ago(5), now) is True


def test_consumer_ack_is_fresh_false_when_older_than_ttl(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "30")
    now = utc_timestamp()
    assert _consumer_ack_is_fresh(_ago(120), now) is False


def test_consumer_ack_is_fresh_false_on_empty_or_malformed_status_at(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "600")
    now = utc_timestamp()
    assert _consumer_ack_is_fresh("", now) is False
    assert _consumer_ack_is_fresh("not-a-timestamp", now) is False


# --------------------------------------------------------------------------- #
# open_handler() behaviour                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_handler_reclaims_stale_open_lane_when_no_probe_and_consumer_stale(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "30")
    orchestrator = _orchestrator()
    await orchestrator.table.put(
        _open_state(
            handler_status_at=_ago(2),
            consumer_status="active",
            consumer_status_at=_ago(120),
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at


@pytest.mark.asyncio
async def test_open_handler_reclaims_lane_with_empty_consumer_status_at(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "600")
    orchestrator = _orchestrator()
    await orchestrator.table.put(
        _open_state(
            handler_status_at=_ago(2),
            consumer_status="active",
            consumer_status_at="",
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"


@pytest.mark.asyncio
async def test_open_handler_does_not_steal_fresh_consumer_when_probe_unavailable(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "600")
    orchestrator = _orchestrator()
    fresh_consumer_at = _ago(2)
    stale_handler_at = _ago(120)
    await orchestrator.table.put(
        _open_state(
            handler_status_at=stale_handler_at,
            consumer_status="active",
            consumer_status_at=fresh_consumer_at,
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_OLD"
    assert state.handler_status == "open"
    assert state.handler_status_at == stale_handler_at
    assert state.consumer_status_at == fresh_consumer_at


@pytest.mark.asyncio
async def test_open_handler_probe_ack_keeps_existing_owner_even_if_consumer_timestamp_stale(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "30")
    source = _ProbeSource(ack=True)
    orchestrator = _orchestrator(source=source)
    await orchestrator.table.put(
        _open_state(
            handler_status_at=_ago(120),
            consumer_status="active",
            consumer_status_at=_ago(120),
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_OLD"
    assert source.probes == [
        {
            "probe_id": "probe_1",
            "for_turn": "turn_OLD",
            "challenger_turn_id": "turn_NEW",
            "wait_probe_id": "probe_1",
            "wait_for_turn": "turn_OLD",
            "timeout_ms": "1",
        }
    ]


@pytest.mark.asyncio
async def test_open_handler_reclaims_when_probe_is_not_acknowledged_even_if_consumer_timestamp_fresh(monkeypatch):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", "600")
    source = _ProbeSource(ack=False)
    orchestrator = _orchestrator(source=source)
    await orchestrator.table.put(
        _open_state(
            handler_status_at=_ago(2),
            consumer_status="active",
            consumer_status_at=_ago(2),
        )
    )

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert source.probes[0]["for_turn"] == "turn_OLD"


@pytest.mark.asyncio
async def test_open_handler_opens_empty_lane():
    orchestrator = _orchestrator()

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at


@pytest.mark.asyncio
async def test_open_handler_opens_closed_lane():
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
async def test_open_handler_is_idempotent_for_same_turn():
    orchestrator = _orchestrator()
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
    assert state.handler_status_at


@pytest.mark.asyncio
async def test_mark_consumer_none_does_not_clear_newer_owner():
    orchestrator = _orchestrator()
    await orchestrator.table.put(
        EventLaneState(
            handler_turn_id="turn_NEW",
            handler_status="open",
            handler_status_at=_ago(1),
            consumer_status="active",
            consumer_status_at=_ago(1),
        )
    )

    state = await orchestrator.mark_consumer_none(turn_id="turn_OLD")

    assert state.handler_turn_id == "turn_NEW"
    assert state.consumer_status == "active"

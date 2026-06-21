"""Unit tests for stale-open handler reclaim in the conversation event bus.

A handler lane is owned by exactly one turn. If that turn crashes or its worker
reloads before calling ``try_close_handler()``, the lane can remain
``handler_status="open"`` under a now-dead ``handler_turn_id``.

Liveness is a Reader/Consumer property. ``open_handler()`` defers only to an
existing owner whose ``consumer_status_at`` is fresh. ``handler_status_at`` is
not a liveness signal.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import (
    ConversationEventBusOrchestrator,
    _consumer_ack_is_fresh,
    _consumer_turn_ttl_ms,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus.exceptions import (
    ExternalEventLaneTurnSuperseded,
)
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import (
    EventLaneState,
    RedisEventLaneStateTable,
    utc_timestamp,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


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


class _Source:
    def __init__(self, *, redis: _Redis, log_key: str = "lane"):
        self.redis = redis
        self.log_key = log_key


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


def test_consumer_turn_ttl_ms_defaults_to_30s(monkeypatch):
    monkeypatch.delenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", raising=False)
    assert _consumer_turn_ttl_ms() == 30_000


@pytest.mark.parametrize("bad", ["0", "-5", "", "not-a-number"])
def test_consumer_turn_ttl_ms_falls_back_on_invalid(monkeypatch, bad):
    monkeypatch.setenv("KDCUBE_EVENT_BUS_OPEN_HANDLER_CONSUMER_TTL_SECONDS", bad)
    assert _consumer_turn_ttl_ms() == 30_000


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
async def test_open_handler_reclaims_stale_open_lane_when_consumer_stale(monkeypatch):
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
    # Reclaim is signalled so the new turn can surface a one-time recovery notice.
    assert orchestrator.last_open_reclaimed is True
    assert orchestrator.last_open_reclaimed_prev_owner == "turn_OLD"


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
    assert orchestrator.last_open_reclaimed is True
    assert orchestrator.last_open_reclaimed_prev_owner == "turn_OLD"


@pytest.mark.asyncio
async def test_open_handler_does_not_steal_fresh_consumer(monkeypatch):
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
    # Deferring to a live owner is NOT a reclaim: no recovery notice.
    assert orchestrator.last_open_reclaimed is False


@pytest.mark.asyncio
async def test_open_handler_opens_empty_lane():
    orchestrator = _orchestrator()

    state = await orchestrator.open_handler(turn_id="turn_NEW")

    assert state.handler_turn_id == "turn_NEW"
    assert state.handler_status == "open"
    assert state.handler_status_at
    # Opening a free lane is the normal path: no prior owner, no notice.
    assert orchestrator.last_open_reclaimed is False


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


@pytest.mark.asyncio
async def test_browser_raises_superseded_when_handler_owner_changed():
    redis = _Redis()
    source = _Source(redis=redis)
    table = RedisEventLaneStateTable.for_source(source)
    await table.put(
        EventLaneState(
            handler_turn_id="turn_NEW",
            handler_status="open",
            handler_status_at=_ago(1),
            consumer_status="active",
            consumer_status_at=_ago(1),
        )
    )
    browser = ContextBrowser(
        runtime_ctx=RuntimeCtx(
            tenant="tenant",
            project="project",
            user_id="user",
            conversation_id="conversation",
            turn_id="turn_OLD",
            bundle_id="bundle@1",
            external_event_source=source,
        ),
    )

    with pytest.raises(ExternalEventLaneTurnSuperseded) as raised:
        await browser.open_external_event_handler()

    assert raised.value.turn_id == "turn_OLD"
    assert raised.value.owner_turn_id == "turn_NEW"
    assert raised.value.phase == "open_external_event_handler"

    with pytest.raises(ExternalEventLaneTurnSuperseded) as raised_again:
        await browser.assert_external_event_handler_current(phase="finish_turn")

    assert raised_again.value.turn_id == "turn_OLD"
    assert raised_again.value.owner_turn_id == "turn_NEW"

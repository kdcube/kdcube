# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The burst window counts admitted requests only (W435).

A registered user opening a Connection Hub Card got "Burst limit exceeded
(162/160)": the limiter recorded every request before checking it, so 429s
kept the window full and a retrying client stayed locked out. These tests run
the real admission script against a disposable Redis (KDCUBE_TEST_REDIS_URL).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.sessions import UserType
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitConfig, RateLimitError, RateLimiter

pytestmark = pytest.mark.skipif(
    not os.getenv("KDCUBE_TEST_REDIS_URL"), reason="requires a disposable Redis (KDCUBE_TEST_REDIS_URL)"
)


class _Monitor:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record_throttling_event(self, **values):
        self.events.append(values)


def _limiter(burst: int, hourly: int) -> tuple[RateLimiter, _Monitor]:
    import redis.asyncio as aioredis

    limiter = RateLimiter.__new__(RateLimiter)
    limiter.redis_url = os.environ["KDCUBE_TEST_REDIS_URL"]
    limiter.redis = aioredis.from_url(limiter.redis_url)
    limiter.gateway_config = SimpleNamespace(
        redis=SimpleNamespace(rate_limit_key_ttl=3600),
        profile=SimpleNamespace(value="test"),
    )
    monitor = _Monitor()
    limiter.monitor = monitor
    limiter.RATE_LIMIT_PREFIX = f"test:w435:{uuid.uuid4().hex}"
    limiter.limits = {UserType.REGISTERED: RateLimitConfig(requests_per_hour=hourly, burst_limit=burst, burst_window=60)}
    return limiter, monitor


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        session_id=f"session-{uuid.uuid4().hex}", rate_limit_subject="", user_type=UserType.REGISTERED,
    )


async def _attempt(limiter, session) -> bool:
    try:
        await limiter.check_and_record(session, SimpleNamespace(), "/api/integrations/bundles/x/operations/y")
        return True
    except RateLimitError:
        return False


def test_exactly_the_limit_is_admitted_and_refusals_are_not_recorded():
    async def run():
        limiter, monitor = _limiter(burst=5, hourly=100)
        session = _session()
        admitted = [await _attempt(limiter, session) for _ in range(5)]
        assert admitted == [True] * 5
        # Twenty retries after the limit: every one refused, none recorded.
        assert [await _attempt(limiter, session) for _ in range(20)] == [False] * 20
        key = f"{limiter.RATE_LIMIT_PREFIX}:{session.session_id}:burst"
        assert await limiter.redis.zcard(key) == 5
        assert len(monitor.events) == 20
        await limiter.redis.aclose()

    asyncio.run(run())


def test_a_refused_burst_does_not_spend_the_hourly_budget():
    async def run():
        limiter, _ = _limiter(burst=3, hourly=10)
        session = _session()
        for _ in range(3):
            assert await _attempt(limiter, session)
        for _ in range(10):
            assert not await _attempt(limiter, session)
        hour_keys = [k async for k in limiter.redis.scan_iter(f"{limiter.RATE_LIMIT_PREFIX}:*:hour:*")]
        assert len(hour_keys) == 1
        assert int(await limiter.redis.get(hour_keys[0])) == 3
        await limiter.redis.aclose()

    asyncio.run(run())


def test_concurrent_requests_in_one_instant_are_each_counted():
    async def run():
        limiter, _ = _limiter(burst=10, hourly=100)
        session = _session()
        results = await asyncio.gather(*[_attempt(limiter, session) for _ in range(25)])
        # Atomic admission: exactly the limit gets through, never more.
        assert sum(results) == 10
        key = f"{limiter.RATE_LIMIT_PREFIX}:{session.session_id}:burst"
        assert await limiter.redis.zcard(key) == 10
        await limiter.redis.aclose()

    asyncio.run(run())


def test_sessions_keep_separate_budgets():
    async def run():
        limiter, _ = _limiter(burst=2, hourly=100)
        first, second = _session(), _session()
        assert [await _attempt(limiter, first) for _ in range(3)] == [True, True, False]
        assert [await _attempt(limiter, second) for _ in range(2)] == [True, True]
        await limiter.redis.aclose()

    asyncio.run(run())


def test_an_hourly_refusal_admits_nothing_and_keeps_the_counter_ttls():
    async def run():
        limiter, _ = _limiter(burst=10, hourly=3)
        session = _session()
        assert [await _attempt(limiter, session) for _ in range(6)] == [True, True, True, False, False, False]
        burst_key = f"{limiter.RATE_LIMIT_PREFIX}:{session.session_id}:burst"
        # The hourly refusals left no burst entry behind.
        assert await limiter.redis.zcard(burst_key) == 3
        hour_keys = [k async for k in limiter.redis.scan_iter(f"{limiter.RATE_LIMIT_PREFIX}:*:hour:*")]
        assert int(await limiter.redis.get(hour_keys[0])) == 3
        assert 0 < await limiter.redis.ttl(burst_key) <= 60
        assert 3500 < await limiter.redis.ttl(hour_keys[0]) <= 3600
        await limiter.redis.aclose()

    asyncio.run(run())


def test_the_refusal_log_names_a_digest_never_the_raw_session(caplog):
    async def run():
        limiter, _ = _limiter(burst=1, hourly=100)
        session = _session()
        with caplog.at_level("WARNING", logger="kdcube_ai_app.infra.gateway.rate_limiter"):
            assert [await _attempt(limiter, session) for _ in range(2)] == [True, False]
        refusals = [r.getMessage() for r in caplog.records if "rate limit refused" in r.getMessage()]
        assert len(refusals) == 1
        assert session.session_id not in refusals[0]
        assert "burst=2/1" in refusals[0]
        await limiter.redis.aclose()

    asyncio.run(run())

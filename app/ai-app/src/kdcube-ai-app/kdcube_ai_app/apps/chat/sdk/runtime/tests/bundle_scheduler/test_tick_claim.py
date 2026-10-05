"""W549: a span=system/instance cron runs once per tick, however many processes schedule it.

Seen live on 2026-10-05 at 02:40Z: chat-proc runs two worker processes, each
with its own scheduler. The board's `event-archive` job finished in about
10 ms, the first process released the job lock, and the second process's tick
for the same fire time took the lock 84 ms later and ran the job again.

Each test runs two job loops (two "processes") against one Redis. The second
loop's Redis is slowed so its tick for the same fire time arrives after the
first run has finished. Set KDCUBE_TEST_REDIS_URL to run the same tests
against a real Redis; otherwise an in-memory fake with the same SET NX/EX,
GET and DELETE semantics is used.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime import bundle_scheduler as bs


class _FakeRedis:
    """SET NX EX, GET, DELETE with expiry, bytes values: what the scheduler uses."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[bytes, float]] = {}

    def _live(self, key: str):
        item = self.data.get(key)
        if item and item[1] <= time.monotonic():
            self.data.pop(key, None)
            return None
        return item

    async def set(self, key, value, ex=None, nx=False):
        if nx and self._live(key):
            return None
        self.data[key] = (str(value).encode(), time.monotonic() + (ex or 10**9))
        return True

    async def get(self, key):
        item = self._live(key)
        return item[0] if item else None

    async def delete(self, key):
        return 1 if self.data.pop(key, None) else 0

    async def expire(self, key, seconds):
        item = self._live(key)
        if not item:
            return False
        self.data[key] = (item[0], time.monotonic() + seconds)
        return True

    async def eval(self, script, numkeys, *args):  # renew/compare scripts, if any
        raise NotImplementedError


class _Slow:
    """The same Redis, seen by a process whose calls arrive `delay` seconds late."""

    def __init__(self, inner, delay: float) -> None:
        self.inner = inner
        self.delay = delay

    def __getattr__(self, name):
        target = getattr(self.inner, name)

        async def call(*args, **kwargs):
            await asyncio.sleep(self.delay)
            return await target(*args, **kwargs)

        return call


async def _redis():
    url = os.environ.get("KDCUBE_TEST_REDIS_URL")
    if not url:
        return _FakeRedis(), None
    import redis.asyncio as aioredis

    client = aioredis.from_url(url)
    await client.ping()
    return client, client


def _run(coro):
    return asyncio.run(coro)


async def _two_processes(*, span: str, job_seconds: float, ticks: list, late: float = 0.08):
    """Run two job loops over the given fire times; return the invocations and the redis."""

    shared, real = await _redis()
    prefix = f"w549test{uuid.uuid4().hex[:8]}"
    invocations: list[tuple[str, float]] = []
    fire_times = list(ticks)

    def next_run(expr, now, tz):
        for at in fire_times:
            if at > now:
                return at
        return now + timedelta(days=1)

    async def fake_invoke(**kwargs):
        invocations.append((kwargs["job_alias"], time.monotonic()))
        await asyncio.sleep(job_seconds)

    async def loop(client, instance):
        await bs._run_job_loop(
            bundle_id="bundle@1", job_alias=prefix, method_name="m", cron_expr="* * * * *", cron_tz="UTC",
            span=span, tenant="t", project="p", instance_id=instance, redis=client,
            bundle_spec=SimpleNamespace(), bundle_config=SimpleNamespace(),
        )

    with patch.object(bs, "_compute_next_run", next_run), patch.object(bs, "_invoke_job", fake_invoke):
        first = asyncio.create_task(loop(shared, "i1"))
        second = asyncio.create_task(loop(_Slow(shared, late), "i1"))
        last = max(fire_times)
        await asyncio.sleep(max(0.0, (last - datetime.now(timezone.utc)).total_seconds()) + job_seconds + late + 0.5)
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    if real is not None:
        await real.aclose()
    return invocations


@pytest.mark.parametrize("span", ["system", "instance"])
def test_a_fast_job_runs_once_per_tick_across_two_processes(span):
    at = datetime.now(timezone.utc) + timedelta(seconds=0.4)

    invocations = _run(_two_processes(span=span, job_seconds=0.0, ticks=[at]))

    assert len(invocations) == 1, f"{span}: ran {len(invocations)} times for one tick"


def test_the_next_tick_still_runs():
    now = datetime.now(timezone.utc)
    ticks = [now + timedelta(seconds=0.4), now + timedelta(seconds=1.2)]

    invocations = _run(_two_processes(span="system", job_seconds=0.0, ticks=ticks))

    assert len(invocations) == 2, "each tick runs exactly once"


def test_a_slow_job_still_excludes_the_next_tick_while_it_runs():
    now = datetime.now(timezone.utc)
    # The job runs past the next fire time: that tick finds the job lock held and is skipped, as before.
    ticks = [now + timedelta(seconds=0.4), now + timedelta(seconds=0.9)]

    invocations = _run(_two_processes(span="system", job_seconds=1.2, ticks=ticks))

    assert len(invocations) == 1, "no overlapping run of a long job"

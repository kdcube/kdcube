# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import gc
import warnings

import pytest

from kdcube_ai_app.infra.plugin import bundle_refs


class _FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    async def zadd(self, key, mapping):
        self.calls.append(("zadd", key, mapping))

    async def expire(self, key, ttl):
        self.calls.append(("expire", key, ttl))


@pytest.fixture(autouse=True)
def _clear_local_active():
    bundle_refs._LOCAL_ACTIVE.clear()
    yield
    bundle_refs._LOCAL_ACTIVE.clear()


def test_touch_bundle_ref_best_effort_without_running_loop_marks_local_and_emits_no_warning():
    redis = _FakeRedis()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bundle_refs.touch_bundle_ref_best_effort(
            redis,
            path="/tmp/bundles/demo",
            tenant="tenant-a",
            project="project-a",
            ttl_seconds=30,
        )
        gc.collect()

    assert "/tmp/bundles/demo" in bundle_refs.get_local_active_paths(ttl_seconds=30)
    assert redis.calls == []
    assert not any("never awaited" in str(w.message) for w in caught)


@pytest.mark.asyncio
async def test_touch_bundle_ref_best_effort_schedules_remote_update_when_loop_exists():
    redis = _FakeRedis()
    key = bundle_refs.refs_key("tenant-a", "project-a")

    bundle_refs.touch_bundle_ref_best_effort(
        redis,
        path="/tmp/bundles/demo",
        tenant="tenant-a",
        project="project-a",
        ttl_seconds=15,
    )
    await asyncio.sleep(0)

    assert "/tmp/bundles/demo" in bundle_refs.get_local_active_paths(ttl_seconds=15)
    assert redis.calls[0][0] == "zadd"
    assert redis.calls[0][1] == key
    assert isinstance(redis.calls[0][2]["/tmp/bundles/demo"], float)
    assert redis.calls[1] == ("expire", key, 30)

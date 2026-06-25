"""
Unit test for UserEconomicsRateLimiter.reset_quota_windows — full reset of the
rolling quota windows (month + day + hour). A dict-backed fake redis stores anchors
and hour buckets; a recording fake mirrors the durable store. After reset the month
and day anchors equal `now`, their periods start at `now`, and the hour buckets for
the last 60 minutes are deleted.
"""
from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
    UserEconomicsRateLimiter,
    subject_id_of,
    GLOBAL_BUNDLE_ID,
)


class _DictRedis:
    def __init__(self):
        self.store = {}
        self.deleted = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = str(value)
        return True

    async def setnx(self, key, value):
        if key in self.store:
            return False
        self.store[key] = str(value)
        return True

    async def expireat(self, key, ts):
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            self.deleted.append(k)
            if k in self.store:
                del self.store[k]
                n += 1
        return n


class _RecordingStore:
    def __init__(self):
        self.saved = []

    async def load(self, subject_id):
        return None

    async def save(self, subject_id, anchor_at):
        self.saved.append((subject_id, anchor_at))

    async def save_if_absent(self, subject_id, anchor_at):
        pass


_NOW = datetime(2026, 6, 25, 12, 30, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_reset_reanchors_month_day_and_clears_hour():
    r = _DictRedis()
    store = _RecordingStore()
    rl = UserEconomicsRateLimiter(r, rl_anchor_store=store)
    subject = subject_id_of("t", "p", "u1")

    # Seed an hour bucket inside the last-60-minute window.
    now_ts = int(_NOW.timestamp())
    min_now = now_ts // 60
    bucket_prefix = f"{rl.ns}:{GLOBAL_BUNDLE_ID}:{subject}:toks:hour:bucket"
    r.store[f"{bucket_prefix}:{min_now}"] = "5000"
    r.store[f"{bucket_prefix}:{min_now - 10}"] = "3000"

    out = await rl.reset_quota_windows(bundle_id=GLOBAL_BUNDLE_ID, subject_id=subject, now=_NOW)

    # month + day re-anchored to now (durable store overwritten with now)
    assert store.saved == [(subject, _NOW)]
    assert out["month_period_start"] == _NOW
    assert out["month_period_key"] == _NOW.strftime("%Y%m%d%H%M")
    assert out["day_period_start"] == _NOW
    assert (out["month_period_end"] - out["month_period_start"]).days == 30
    assert (out["day_period_end"] - out["day_period_start"]).days == 1

    # hour buckets in the last-60-minute window were deleted
    assert f"{bucket_prefix}:{min_now}" not in r.store
    assert f"{bucket_prefix}:{min_now - 10}" not in r.store
    # 60 bucket keys targeted for deletion (min_now-59 .. min_now)
    assert len([k for k in r.deleted if k.startswith(bucket_prefix)]) == 60


@pytest.mark.asyncio
async def test_reset_without_store_still_reanchors():
    r = _DictRedis()
    rl = UserEconomicsRateLimiter(r)  # no durable store
    subject = subject_id_of("t", "p", "u2")

    out = await rl.reset_quota_windows(bundle_id=GLOBAL_BUNDLE_ID, subject_id=subject, now=_NOW)

    assert out["month_period_start"] == _NOW
    assert out["day_period_start"] == _NOW
    # both redis anchors now hold now_ts
    now_ts = str(int(_NOW.timestamp()))
    assert any(k.endswith("month_anchor") and v == now_ts for k, v in r.store.items())
    assert any(k.endswith("day_anchor") and v == now_ts for k, v in r.store.items())

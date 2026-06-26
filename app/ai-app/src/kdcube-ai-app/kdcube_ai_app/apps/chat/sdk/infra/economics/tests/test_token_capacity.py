# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Unit golden for UserEconomicsRateLimiter.token_capacity_for_reservation.

Guards the settlement-time capacity contract: `available_tokens` is RAW capacity
(net of ALL active reservations, including this request's own), and the own hold
is returned separately as `own_reserved_tokens` for the caller to add back. This is
the same "available is raw; add own back" convention the budget snapshots follow.

Regression: previously `available` subtracted only OTHER reservations, leaving the
request's own reserved room inside `available`. The settlement allocation then added
`own_reserved` on top (`quota_capacity = available + own`), double-counting the
reservation and over-committing plan quota by its size (negative "remaining" in the UI).
"""

from __future__ import annotations

from datetime import datetime, timezone

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import UserEconomicsRateLimiter


class _FakeRedis:
    """Minimal redis for token_capacity_for_reservation's reads.

    month window: limit 66390, committed 63390, reserved_total 3000 (our own hold).
    """
    async def eval(self, *a, **k):
        return None

    async def hget(self, key, field):
        # reservation meta is "amount|...": our own hold is 3000 tokens
        return "3000|meta"

    async def mget(self, *keys):
        out = []
        for k in keys:
            if "toks_resv:month" in k:
                out.append("3000")        # reserved_total (month) — includes our own
            elif "toks_resv:day" in k or "toks_resv:hour" in k:
                out.append("0")
            elif "toks:month" in k:
                out.append("63390")       # committed (month)
            elif "toks:day" in k:
                out.append("0")
            else:
                out.append(None)
        return out


class _StubRL(UserEconomicsRateLimiter):
    """Stub the rolling-window helpers so only add_window's arithmetic is exercised."""
    async def _rolling_hour_stats(self, bucket_prefix, now, *, limit=None, reserved=0):
        return 0, 0

    async def _rolling_day_period(self, *, bundle_id, subject_id, now, create_if_missing=True):
        return None, None, "D"

    async def _rolling_month_period(self, *, bundle_id, subject_id, now, create_if_missing=True):
        return None, None, "M"


async def test_available_is_raw_net_of_own_reservation():
    rl = _StubRL(_FakeRedis())
    out = await rl.token_capacity_for_reservation(
        bundle_id="__project__",
        subject_id="t:p:u1",
        policy=QuotaPolicy(tokens_per_month=66390),   # only the month window is active
        reservation_id="turn-1",
        reserved_tokens=3000,
        now=datetime(2026, 6, 24, 8, 0, 0, tzinfo=timezone.utc),
    )
    # RAW capacity: 66390 − 63390 − 3000(all reservations, incl. own) == 0
    assert out["available_tokens"] == 0
    # our own hold is reported separately for the caller to add back
    assert out["own_reserved_tokens"] == 3000
    # add-back never exceeds true remaining (limit − committed == 3000); pre-fix this
    # summed to 6000 (double-counted the 3000 reservation -> negative "remaining").
    assert out["available_tokens"] + out["own_reserved_tokens"] == 66390 - 63390


async def test_available_unchanged_without_own_reservation():
    # No reservation_id -> own_reserved == 0; other == total, so the formula is the
    # same as before the fix. Confirms the change is inert for non-settle callers.
    rl = _StubRL(_FakeRedis())
    out = await rl.token_capacity_for_reservation(
        bundle_id="__project__",
        subject_id="t:p:u1",
        policy=QuotaPolicy(tokens_per_month=66390),
        reservation_id=None,
        reserved_tokens=0,
        now=datetime(2026, 6, 24, 8, 0, 0, tzinfo=timezone.utc),
    )
    # 66390 − 63390 − 3000(other, since own=0) == 0
    assert out["available_tokens"] == 0
    assert out["own_reserved_tokens"] == 0

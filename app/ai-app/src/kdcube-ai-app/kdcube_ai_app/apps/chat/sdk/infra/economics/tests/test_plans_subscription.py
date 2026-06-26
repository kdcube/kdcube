# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Unit tests for the plans-as-subscriptions work:

  - SubscriptionManager.assert_plan_change_allowed (resub matrix + operator override)
  - SubscriptionManager.ensure_baseline_subscription (idempotent; never clobbers an
    existing paid/stripe row)
  - RLMonthAnchorStore (subject parsing, load, persist-once UPDATE)
  - UserEconomicsRateLimiter._rolling_month_period DB-anchor flush-restore + write-through

All exercised with in-memory fakes — no real Postgres/Redis.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import (
    PlanChangeNotAllowed,
    RLMonthAnchorStore,
    Subscription,
    SubscriptionManager,
    SubscriptionPlan,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
    UserEconomicsRateLimiter,
    GLOBAL_BUNDLE_ID,
    subject_id_of,
    _k,
)


# ─── builders ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plan(plan_id: str, *, provider: str = "internal", price: int = 0, active: bool = True) -> SubscriptionPlan:
    n = _now()
    return SubscriptionPlan(
        tenant="t", project="p", plan_id=plan_id, provider=provider,
        stripe_price_id=("price_x" if provider == "stripe" else None),
        monthly_price_cents=price, active=active, metadata=None,
        created_at=n, updated_at=n, created_by=None, notes=None,
    )


def _sub(plan_id: str, *, provider: str = "internal", price: int = 0, status: str = "active") -> Subscription:
    n = _now()
    return Subscription(
        tenant="t", project="p", user_id="u1", plan_id=plan_id, status=status,
        monthly_price_cents=price, started_at=n, next_charge_at=None, last_charged_at=n,
        provider=provider, stripe_customer_id=None, stripe_subscription_id=None,
        created_at=n, updated_at=n,
    )


def _sub_row(**over) -> dict:
    n = _now()
    row = dict(
        tenant="t", project="p", user_id="u1", plan_id="free", status="active",
        monthly_price_cents=0, started_at=n, next_charge_at=None, last_charged_at=n,
        provider="internal", stripe_customer_id=None, stripe_subscription_id=None,
        created_at=n, updated_at=n,
    )
    row.update(over)
    return row


def _plan_row(**over) -> dict:
    n = _now()
    row = dict(
        tenant="t", project="p", plan_id="free", provider="internal",
        stripe_price_id=None, monthly_price_cents=0, active=True, metadata=None,
        created_at=n, updated_at=n, created_by=None, notes=None,
    )
    row.update(over)
    return row


# ─── fake asyncpg pool/conn ───────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, *, plan_row=None, insert_row=None, existing_row=None, anchor_row=None):
        self.plan_row = plan_row
        self.insert_row = insert_row
        self.existing_row = existing_row
        self.anchor_row = anchor_row
        self.executed: list = []

    async def fetchrow(self, sql, *args):
        if ".plans" in sql:
            return self.plan_row
        if "INSERT INTO" in sql:
            return self.insert_row
        if "rl_month_anchor_at" in sql and sql.strip().upper().startswith("SELECT"):
            return self.anchor_row
        if "SELECT * FROM" in sql and "user_plans" in sql:
            return self.existing_row
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *a):
                return False

        return _CM()


# ─── assert_plan_change_allowed matrix ────────────────────────────────────────

def _guard():
    return SubscriptionManager(pg_pool=None)


def test_resub_free_to_paid_ok():
    _guard().assert_plan_change_allowed(current=_sub("free"), target_plan=_plan("pro", price=999))


def test_resub_none_to_paid_ok():
    _guard().assert_plan_change_allowed(current=None, target_plan=_plan("pro", price=999))


def test_resub_paid_to_paid_ok():
    _guard().assert_plan_change_allowed(
        current=_sub("beta_30", price=300), target_plan=_plan("beta_40", price=1234)
    )


def test_resub_free_to_zero_denied():
    with pytest.raises(PlanChangeNotAllowed) as ei:
        _guard().assert_plan_change_allowed(current=_sub("free"), target_plan=_plan("free", price=0))
    assert ei.value.code == "free_requires_paid_plan"


def test_resub_none_to_zero_denied():
    with pytest.raises(PlanChangeNotAllowed) as ei:
        _guard().assert_plan_change_allowed(current=None, target_plan=_plan("free", price=0))
    assert ei.value.code == "free_requires_paid_plan"


def test_resub_admin_locked():
    with pytest.raises(PlanChangeNotAllowed) as ei:
        _guard().assert_plan_change_allowed(current=_sub("admin"), target_plan=_plan("pro", price=999))
    assert ei.value.code == "admin_plan_locked"


@pytest.mark.parametrize("bad", ["wallet", "anonymous"])
def test_resub_quota_only_target_denied(bad):
    with pytest.raises(PlanChangeNotAllowed) as ei:
        _guard().assert_plan_change_allowed(current=_sub("free"), target_plan=_plan(bad, price=0))
    assert ei.value.code == "target_not_subscribable"


# operator override: only the quota-only sanity check applies
def test_operator_can_grant_admin_zero_cost():
    _guard().assert_plan_change_allowed(current=None, target_plan=_plan("admin", price=0), operator=True)


def test_operator_can_move_admin_user():
    _guard().assert_plan_change_allowed(
        current=_sub("admin"), target_plan=_plan("pro", price=999), operator=True
    )


@pytest.mark.parametrize("bad", ["wallet", "anonymous"])
def test_operator_still_blocks_quota_only_target(bad):
    with pytest.raises(PlanChangeNotAllowed) as ei:
        _guard().assert_plan_change_allowed(current=None, target_plan=_plan(bad), operator=True)
    assert ei.value.code == "target_not_subscribable"


# ─── ensure_baseline_subscription ─────────────────────────────────────────────

async def test_ensure_baseline_inserts_zero_cost_internal_row():
    inserted = _sub_row(plan_id="free", monthly_price_cents=0, provider="internal")
    conn = _FakeConn(plan_row=_plan_row(plan_id="free"), insert_row=inserted)
    mgr = SubscriptionManager(pg_pool=_FakePool(conn))
    sub = await mgr.ensure_baseline_subscription(tenant="t", project="p", user_id="u1", plan_id="free")
    assert sub.plan_id == "free"
    assert sub.monthly_price_cents == 0
    assert sub.provider == "internal"


async def test_ensure_baseline_is_idempotent_never_clobbers_existing():
    # Insert returns None (ON CONFLICT DO NOTHING) -> the pre-existing row is returned untouched.
    existing_paid = _sub_row(plan_id="beta_30", monthly_price_cents=300, provider="stripe",
                             stripe_subscription_id="sub_123")
    conn = _FakeConn(plan_row=_plan_row(plan_id="free"), insert_row=None, existing_row=existing_paid)
    mgr = SubscriptionManager(pg_pool=_FakePool(conn))
    sub = await mgr.ensure_baseline_subscription(tenant="t", project="p", user_id="u1", plan_id="free")
    # The existing paid/stripe row is preserved, not overwritten with the free baseline.
    assert sub.plan_id == "beta_30"
    assert sub.provider == "stripe"
    assert sub.stripe_subscription_id == "sub_123"


async def test_ensure_baseline_rejects_non_internal_plan():
    conn = _FakeConn(plan_row=_plan_row(plan_id="pro", provider="stripe", monthly_price_cents=999))
    mgr = SubscriptionManager(pg_pool=_FakePool(conn))
    with pytest.raises(ValueError):
        await mgr.ensure_baseline_subscription(tenant="t", project="p", user_id="u1", plan_id="pro")


async def test_ensure_baseline_rejects_chargeable_plan():
    conn = _FakeConn(plan_row=_plan_row(plan_id="paidfree", provider="internal", monthly_price_cents=500))
    mgr = SubscriptionManager(pg_pool=_FakePool(conn))
    with pytest.raises(ValueError):
        await mgr.ensure_baseline_subscription(tenant="t", project="p", user_id="u1", plan_id="paidfree")


async def test_ensure_baseline_rejects_missing_plan():
    conn = _FakeConn(plan_row=None)
    mgr = SubscriptionManager(pg_pool=_FakePool(conn))
    with pytest.raises(ValueError):
        await mgr.ensure_baseline_subscription(tenant="t", project="p", user_id="u1", plan_id="nope")


# ─── RLMonthAnchorStore ───────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,expected", [
    ("t:p:u1", ("t", "p", "u1")),
    ("t:p:u:with:colons", ("t", "p", "u:with:colons")),  # maxsplit keeps user_id intact
    ("bad", None),
    ("", None),
    ("t:p:", None),  # empty user_id
])
def test_anchor_store_split_subject(subject, expected):
    assert RLMonthAnchorStore._split_subject(subject) == expected


async def test_anchor_store_load_returns_value():
    anchor = _now() - timedelta(days=10)
    conn = _FakeConn(anchor_row={"rl_month_anchor_at": anchor})
    store = RLMonthAnchorStore(_FakePool(conn))
    assert await store.load("t:p:u1") == anchor


async def test_anchor_store_load_bad_subject_is_none():
    conn = _FakeConn(anchor_row={"rl_month_anchor_at": _now()})
    store = RLMonthAnchorStore(_FakePool(conn))
    assert await store.load("garbage") is None


async def test_anchor_store_save_persists_once_update():
    conn = _FakeConn()
    store = RLMonthAnchorStore(_FakePool(conn))
    ts = _now()
    await store.save_if_absent("t:p:u1", ts)
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "UPDATE" in sql and "rl_month_anchor_at IS NULL" in sql
    assert args == ("t", "p", "u1", ts)


async def test_anchor_store_save_bad_subject_no_db():
    conn = _FakeConn()
    store = RLMonthAnchorStore(_FakePool(conn))
    await store.save_if_absent("garbage", _now())
    assert conn.executed == []


# ─── _rolling_month_period: DB-anchor flush-restore + write-through ────────────

class _LimiterRedis:
    def __init__(self):
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def setnx(self, key, val):
        if key in self.store:
            return False
        self.store[key] = val
        return True

    async def set(self, key, val):
        self.store[key] = val
        return True


class _RecordingStore:
    def __init__(self, load_value=None):
        self.load_value = load_value
        self.saved: list = []

    async def load(self, subject_id):
        return self.load_value

    async def save_if_absent(self, subject_id, anchor_at):
        self.saved.append((subject_id, anchor_at))


_SUBJ = subject_id_of("t", "p", "u1")
_ANCHOR_KEY = _k(UserEconomicsRateLimiter(_LimiterRedis()).ns, GLOBAL_BUNDLE_ID, _SUBJ, "month_anchor")


async def test_rolling_month_restores_from_db_on_redis_miss():
    now = _now()
    db_anchor = now - timedelta(days=100)  # 3 full 30-day periods elapsed
    r = _LimiterRedis()
    store = _RecordingStore(load_value=db_anchor)
    rl = UserEconomicsRateLimiter(r, rl_anchor_store=store)

    ps, pe, key = await rl._rolling_month_period(
        bundle_id=GLOBAL_BUNDLE_ID, subject_id=_SUBJ, now=now,
    )

    anchor_ts = int(db_anchor.timestamp())
    period_len = 30 * 24 * 60 * 60
    periods = (int(now.timestamp()) - anchor_ts) // period_len
    expected_start = anchor_ts + periods * period_len
    assert int(ps.timestamp()) == expected_start
    assert int(pe.timestamp()) == expected_start + period_len
    # Redis cache repopulated from the durable mirror; no fresh "now" anchor minted.
    assert r.store.get(_ANCHOR_KEY) == anchor_ts
    assert store.saved == []  # restore, not write-through


async def test_rolling_month_mints_and_writes_through_when_no_db_anchor():
    now = _now()
    r = _LimiterRedis()
    store = _RecordingStore(load_value=None)
    rl = UserEconomicsRateLimiter(r, rl_anchor_store=store)

    ps, pe, key = await rl._rolling_month_period(
        bundle_id=GLOBAL_BUNDLE_ID, subject_id=_SUBJ, now=now,
    )

    now_ts = int(now.timestamp())
    assert r.store.get(_ANCHOR_KEY) == now_ts          # minted at "now"
    assert int(ps.timestamp()) == now_ts
    assert len(store.saved) == 1                        # written through to the DB mirror
    saved_subject, saved_at = store.saved[0]
    assert saved_subject == _SUBJ
    assert int(saved_at.timestamp()) == now_ts


async def test_rolling_month_pure_redis_when_no_store():
    now = _now()
    r = _LimiterRedis()
    rl = UserEconomicsRateLimiter(r)  # no rl_anchor_store
    ps, pe, key = await rl._rolling_month_period(
        bundle_id=GLOBAL_BUNDLE_ID, subject_id=_SUBJ, now=now,
    )
    assert r.store.get(_ANCHOR_KEY) == int(now.timestamp())


async def test_rolling_month_no_create_returns_none_without_anchor():
    now = _now()
    r = _LimiterRedis()
    store = _RecordingStore(load_value=None)
    rl = UserEconomicsRateLimiter(r, rl_anchor_store=store)
    ps, pe, key = await rl._rolling_month_period(
        bundle_id=GLOBAL_BUNDLE_ID, subject_id=_SUBJ, now=now, create_if_missing=False,
    )
    assert (ps, pe, key) == (None, None, None)
    assert store.saved == []

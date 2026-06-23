# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Unit tests for the shared unified-split funding reserve+settle (funding_flow).

Deterministic usd_per_token; fakes record limiter calls. Verifies the single
split reserve (primary hold for the plan part + wallet hold for the over-quota
remainder), denial, and that the split allocation is wired to the correct limiter
on settlement.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy, EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
from kdcube_ai_app.apps.chat.sdk.infra.economics import funding_flow as ff


UPT = 1e-5  # usd per token (deterministic)


class _RL:
    def __init__(self, available_tokens=10**9):
        self.available_tokens = available_tokens
        self.commits = []
        self.releases = []

    async def token_capacity_for_reservation(self, **kw):
        return {"available_tokens": self.available_tokens, "own_reserved_tokens": int(kw.get("reserved_tokens") or 0)}

    async def commit_with_reservation(self, **kw):
        self.commits.append(kw)

    async def release_token_reservation(self, **kw):
        self.releases.append(("tok", kw))

    async def release(self, **kw):
        self.releases.append(("lock", kw))


class _Budget:
    def __init__(self, available_usd=1000.0, overdraft=None, reserve_fail=False):
        self.available_usd = available_usd
        self.overdraft = overdraft
        self.reserve_fail = reserve_fail
        self.reserved = []
        self.committed = []
        self.forced = []
        self.released = []

    async def get_app_budget_balance(self):
        return {"available_usd": self.available_usd, "overdraft_limit_usd": self.overdraft}

    async def reserve(self, **kw):
        if self.reserve_fail:
            raise BudgetInsufficientFunds("no funds")
        self.reserved.append(kw)
        return type("RR", (), {"reservation_id": kw["reservation_id"]})()

    async def commit_reserved_spend(self, **kw):
        self.committed.append(kw)

    async def force_project_spend(self, **kw):
        self.forced.append(kw)

    async def release_reservation(self, **kw):
        self.released.append(kw)


class _SubBudget(_Budget):
    async def get_subscription_budget_balance(self):
        return {"available_usd": self.available_usd}


class _Credits:
    def __init__(self, balance=10**9, reserve_ok=True, consume_uncovered=0, commit_uncovered=0):
        self.balance = balance
        self.reserve_ok = reserve_ok
        self.consume_uncovered = consume_uncovered
        self.commit_uncovered = commit_uncovered
        self.reserved = []
        self.committed = []
        self.consumed = []
        self.released = []

    async def get_lifetime_balance(self, **kw):
        return self.balance

    async def reserve_lifetime_tokens(self, **kw):
        self.reserved.append(kw)
        return self.reserve_ok

    async def commit_reserved_lifetime_tokens(self, **kw):
        self.committed.append(kw)
        return self.commit_uncovered

    async def consume_lifetime_tokens(self, **kw):
        self.consumed.append(kw)
        return self.consume_uncovered

    async def release_lifetime_token_reservation(self, **kw):
        self.released.append(kw)


class _CP:
    def __init__(self, credits):
        self.user_credits_mgr = credits


class _AdmitSplit:
    """Stub of a wallet-aware AdmitResult (the Lua already split + decided)."""
    def __init__(self, *, allowed=True, reserved=0, wallet_part=0, rid="scope-1", reason=None):
        self.allowed = allowed
        self.reserved_tokens = int(reserved)
        self.wallet_part = int(wallet_part)
        self.reservation_id = rid if (allowed and reserved > 0) else None
        self.reason = reason


def _ctx(*, rl=None, budget=None, sub=None, credits=None):
    credits = credits or _Credits()
    return ff.FundingContext(
        rl=rl or _RL(),
        budget_limiter=budget or _Budget(),
        cp_manager=_CP(credits),
        tenant="t", project="p", user_id="u1", subject_id="t:p:u1",
        bundle_id="b@1", rl_bundle_id="__project__", scope_id="scope-1",
        usd_per_token=UPT, now=datetime.now(timezone.utc),
        subscription_limiter=sub,
    )


async def _resv(ctx, *, admit, funding_source, budget_bypass=False, has_wallet=False):
    """reserve via the unified split and unwrap the reservation (asserts OK)."""
    out = await ff.reserve_funding(
        ctx, admit=admit, funding_source=funding_source,
        budget_bypass=budget_bypass, has_wallet=has_wallet,
    )
    assert out.status is ff.ReserveStatus.OK, f"expected OK, got {out.status}"
    return out.reservation


# --------------------------------------------------------------------------
# Unified split reserve (reserve_funding)
# --------------------------------------------------------------------------
async def test_reserve_funding_splits_plan_and_wallet_holds():
    # admit split 4000 plan / 6000 wallet (project primary): primary money hold for
    # plan_part, wallet hold for wallet_part.
    credits = _Credits()
    budget = _Budget(available_usd=1000.0)
    ctx = _ctx(budget=budget, credits=credits)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=4000, wallet_part=6000),
        funding_source="project", budget_bypass=False, has_wallet=True,
    )
    assert out.status is ff.ReserveStatus.OK
    res = out.reservation
    assert res.plan_reserved_tokens == 4000 and res.plan_reservation_active is True
    assert res.app_reservation_active is True and len(budget.reserved) == 1
    assert res.wallet_reservation_active is True and res.wallet_reserved_tokens == 6000
    assert len(credits.reserved) == 1


async def test_reserve_funding_plan_only_no_wallet_hold():
    credits = _Credits()
    budget = _Budget(available_usd=1000.0)
    ctx = _ctx(budget=budget, credits=credits)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=2000, wallet_part=0),
        funding_source="project", budget_bypass=False, has_wallet=True,
    )
    assert out.status is ff.ReserveStatus.OK
    assert out.reservation.wallet_reservation_active is False
    assert credits.reserved == []
    assert len(budget.reserved) == 1


async def test_reserve_funding_wallet_only_no_primary_hold():
    # plan_part=0 (e.g. quota+funds exhausted), wallet covers the whole turn.
    credits = _Credits()
    sub = _SubBudget(available_usd=0.0)
    ctx = _ctx(sub=sub, credits=credits)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=0, wallet_part=5000),
        funding_source="subscription", budget_bypass=False, has_wallet=True,
    )
    assert out.status is ff.ReserveStatus.OK
    res = out.reservation
    assert res.app_reservation_active is False and sub.reserved == []
    assert res.plan_reservation_active is False
    assert res.wallet_reservation_active is True and res.wallet_reserved_tokens == 5000


async def test_reserve_funding_denied_wallet_insufficient():
    ctx = _ctx()
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(allowed=False, reason="wallet_insufficient"),
        funding_source="subscription", budget_bypass=False, has_wallet=True,
    )
    assert out.status is ff.ReserveStatus.DENIED
    assert out.deny_code == "wallet_insufficient"


async def test_reserve_funding_denied_indivisible_gate_maps_to_rate_limited():
    ctx = _ctx()
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(allowed=False, reason="concurrency"),
        funding_source="project", budget_bypass=False, has_wallet=False,
    )
    assert out.status is ff.ReserveStatus.DENIED
    assert out.deny_code == "rate_limited"


async def test_reserve_funding_primary_hold_failure_denies():
    budget = _Budget(reserve_fail=True)
    ctx = _ctx(budget=budget)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=4000, wallet_part=0),
        funding_source="project", budget_bypass=False, has_wallet=False,
    )
    assert out.status is ff.ReserveStatus.DENIED
    assert out.deny_code == "project_reservation_failed"


async def test_reserve_funding_wallet_hold_failure_releases_primary_and_denies():
    credits = _Credits(reserve_ok=False)
    budget = _Budget(available_usd=1000.0)
    ctx = _ctx(budget=budget, credits=credits)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=4000, wallet_part=6000),
        funding_source="project", budget_bypass=False, has_wallet=True,
    )
    assert out.status is ff.ReserveStatus.DENIED
    assert out.deny_code == "wallet_reservation_failed"
    # the primary hold taken before the wallet failure was released
    assert len(budget.released) == 1


async def test_reserve_funding_bypass_takes_no_money_hold():
    budget = _Budget()
    ctx = _ctx(budget=budget)
    out = await ff.reserve_funding(
        ctx, admit=_AdmitSplit(reserved=2000, wallet_part=0),
        funding_source="project", budget_bypass=True, has_wallet=False,
    )
    assert out.status is ff.ReserveStatus.OK
    assert out.reservation.app_reservation_active is False
    assert budget.reserved == []


# --------------------------------------------------------------------------
# Settle
# --------------------------------------------------------------------------
async def test_settle_none_charges_project_last_resort():
    ctx = _ctx()
    res = ff.PlanFundingReservation(funding_source="none", budget_bypass=False, est_turn_tokens=2000)
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=1000, total_cost_usd=0.02,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=False, user_budget_tokens=None,
    )
    assert len(ctx.budget_limiter.forced) == 1
    assert ctx.budget_limiter.forced[0]["spent_usd"] == pytest.approx(0.02, rel=1e-6)
    assert "no_funding_source" in ctx.budget_limiter.forced[0]["note"]
    assert out.quota_commit_tokens == 1000


async def test_settle_project_commits_reservation_and_rl():
    ctx = _ctx(budget=_Budget(overdraft=None, available_usd=1000.0))
    res = await _resv(
        ctx, admit=_AdmitSplit(reserved=2000, wallet_part=0),
        funding_source="project", has_wallet=False,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=1500, total_cost_usd=0.015,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=False, user_budget_tokens=None,
    )
    assert len(ctx.budget_limiter.committed) == 1        # primary reservation committed
    assert ctx.budget_limiter.committed[0]["spent_usd"] == pytest.approx(0.015, rel=1e-6)
    assert len(ctx.rl.commits) == 1                      # RL quota committed
    assert out.quota_commit_tokens == 1500
    assert out.wallet_usd == 0.0


async def test_settle_bypass_forces_project_and_commits_rl():
    ctx = _ctx()
    res = await _resv(
        ctx, admit=_AdmitSplit(reserved=2000, wallet_part=0),
        funding_source="project", budget_bypass=True, has_wallet=False,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=1000, total_cost_usd=0.02,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=False, user_budget_tokens=None,
    )
    assert len(ctx.budget_limiter.forced) == 1
    assert ctx.budget_limiter.forced[0]["spent_usd"] == pytest.approx(0.02, rel=1e-6)
    assert len(ctx.rl.commits) == 1
    assert out.quota_commit_tokens == 1000


async def test_settle_wallet_shortfall_absorbed_by_project():
    # project primary partial (finite overdraft) + wallet overflow; wallet fully
    # covers its target here -> nothing uncovered (project absorbs 0).
    credits = _Credits(balance=10**9, commit_uncovered=0, consume_uncovered=0)
    budget = _Budget(overdraft=0.0, available_usd=0.05)
    ctx = _ctx(budget=budget, credits=credits)
    res = await _resv(
        ctx, admit=_AdmitSplit(reserved=4347, wallet_part=5653),
        funding_source="project", has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=10000, total_cost_usd=0.10,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    # wallet portion was committed against the reservation
    assert len(credits.committed) == 1
    # primary reservation committed for its covered share
    assert len(budget.committed) == 1
    assert out.allocation is not None
    assert out.allocation.wallet_tokens > 0
    # wallet fully covered its target -> nothing uncovered; consumed == target
    assert out.user_uncovered_tokens == 0
    assert out.user_uncovered_usd == 0.0
    assert out.wallet_consumed_tokens == int(out.allocation.wallet_tokens)


async def test_settle_exposes_wallet_uncovered_intermediates():
    # wallet can't fully cover its target -> the result exposes wallet_consumed /
    # user_uncovered (tokens + usd) for the caller's underfunded event. commit_uncovered
    # leaves a reservation-free remainder that consume_lifetime_tokens reports uncovered.
    credits = _Credits(balance=10**9, commit_uncovered=2000, consume_uncovered=2000)
    budget = _Budget(overdraft=0.0, available_usd=0.05)
    ctx = _ctx(budget=budget, credits=credits)
    res = await _resv(
        ctx, admit=_AdmitSplit(reserved=4347, wallet_part=5653),
        funding_source="project", has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=10000, total_cost_usd=0.10,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    target = int(out.allocation.wallet_tokens)
    assert target >= 2000
    assert out.user_uncovered_tokens == 2000
    assert out.wallet_consumed_tokens == target - 2000
    # invariant: consumed + uncovered == wallet target
    assert out.wallet_consumed_tokens + out.user_uncovered_tokens == target
    assert out.user_uncovered_usd == pytest.approx(out.total_cost_usd * 2000 / out.ranked_tokens, rel=1e-6)


async def test_settle_subscription_runtime_shortfall_absorbed_by_subscription_headroom():
    # subscription primary with funds beyond the pre-run plan share (headroom). A
    # runtime wallet shortfall (consume returns fewer tokens than the fresh balance
    # promised) is absorbed by the subscription budget's headroom, NOT the project.
    credits = _Credits(balance=10**9, commit_uncovered=2000, consume_uncovered=2000)
    sub = _SubBudget(available_usd=1.0)                  # plenty of headroom at settle
    ctx = _ctx(sub=sub, credits=credits)
    res = ff.PlanFundingReservation(
        funding_source="subscription", budget_bypass=False, est_turn_tokens=10000,
        app_reservation_id="scope-1", app_reserved_usd=0.04, app_reservation_active=True,
        plan_project_tokens_est=4000,
        wallet_reservation_id="w1", wallet_reserved_tokens=6000, wallet_reservation_active=True,
        has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=10000, total_cost_usd=0.10,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    assert out.allocation.primary_overage_headroom_tokens >= 2000
    assert out.user_uncovered_tokens == 2000             # still reported for the event
    # nothing falls to the project: the subscription budget absorbed the shortfall
    assert ctx.budget_limiter.forced == []
    assert len(sub.committed) == 1
    # subscription commit = plan share ($0.04) + the runtime shortfall ($0.02)
    assert sub.committed[0]["spent_usd"] == pytest.approx(0.06, rel=1e-6)
    assert out.primary_funding_usd == pytest.approx(0.06, rel=1e-6)
    assert out.project_absorption_usd == 0.0


async def test_settle_subscription_runtime_shortfall_falls_to_project_when_no_headroom():
    # subscription funds exhausted at the plan share (no headroom) -> the runtime
    # wallet shortfall falls through to the project as shortfall:wallet_subscription.
    credits = _Credits(balance=10**9, commit_uncovered=2000, consume_uncovered=2000)
    sub = _SubBudget(available_usd=0.0)                  # nothing beyond the reserved plan share
    ctx = _ctx(sub=sub, credits=credits)
    res = ff.PlanFundingReservation(
        funding_source="subscription", budget_bypass=False, est_turn_tokens=10000,
        app_reservation_id="scope-1", app_reserved_usd=0.04, app_reservation_active=True,
        plan_project_tokens_est=4000,
        wallet_reservation_id="w1", wallet_reserved_tokens=6000, wallet_reservation_active=True,
        has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=10000, total_cost_usd=0.10,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    assert out.allocation.primary_overage_headroom_tokens == 0
    assert out.user_uncovered_tokens == 2000
    forced_notes = [f["note"] for f in ctx.budget_limiter.forced]
    assert any("wallet_subscription" in n for n in forced_notes)
    assert ctx.budget_limiter.forced[0]["spent_usd"] == pytest.approx(0.02, rel=1e-6)
    # subscription committed only its plan share (~$0.04; token-capacity floor rounding)
    assert sub.committed[0]["spent_usd"] == pytest.approx(0.04, rel=1e-3)

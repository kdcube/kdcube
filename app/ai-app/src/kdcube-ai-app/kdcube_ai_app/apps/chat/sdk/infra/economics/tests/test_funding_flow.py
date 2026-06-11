# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Unit tests for the shared plan-lane funding reserve+settle (funding_flow).

Deterministic usd_per_token; fakes record limiter calls. Verifies primary cover
sizing, wallet overflow reservation, denial, and that the split allocation is
wired to the correct limiter on settlement.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy, EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
from kdcube_ai_app.apps.chat.sdk.infra.economics import funding_flow as ff

UPT = 1e-5  # usd per token (deterministic)


class _Admit:
    def __init__(self, reserved=2000, rid="scope-1"):
        self.reserved_tokens = reserved
        self.reservation_id = rid


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


async def _resv(*a, **k):
    """reserve and unwrap the PlanFundingReservation (asserts an OK outcome)."""
    out = await ff.reserve_plan_funding(*a, **k)
    assert out.status is ff.ReserveStatus.OK, f"expected OK, got {out.status}"
    return out.reservation


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


# --------------------------------------------------------------------------
# Reserve
# --------------------------------------------------------------------------
async def test_reserve_project_unlimited_covers_full_no_wallet():
    ctx = _ctx(budget=_Budget(overdraft=None))
    res = await _resv(
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=False,
        est_turn_tokens=2000, has_wallet=False, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": None, "available_usd": 0.0},
        personal_can_pay_turn=False,
    )
    assert res.plan_project_tokens_est == 2000          # unlimited overdraft -> full cover
    assert res.app_reservation_active is True
    assert len(ctx.budget_limiter.reserved) == 1
    assert ctx.cp_manager.user_credits_mgr.reserved == []  # no wallet overflow


async def test_reserve_project_finite_overdraft_reserves_wallet_overflow():
    # available+overdraft = $0.05 -> ~4347 tokens primary (well above the $0.01
    # min-reserve floor); the remaining ~5653 overflow to wallet.
    ctx = _ctx(budget=_Budget(overdraft=0.0))
    res = await _resv(
        ctx, admit=_Admit(reserved=10000), funding_source="project", budget_bypass=False,
        est_turn_tokens=10000, has_wallet=True, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": 0.0, "available_usd": 0.05},
        personal_can_pay_turn=True,
    )
    assert 0 < res.plan_project_tokens_est < 10000
    assert res.app_reservation_active is True
    assert res.wallet_reservation_active is True
    assert res.wallet_reserved_tokens == 10000 - res.plan_project_tokens_est


async def test_reserve_subscription_with_wallet_overflow():
    sub = _SubBudget(available_usd=0.05)
    ctx = _ctx(sub=sub)
    res = await _resv(
        ctx, admit=_Admit(reserved=10000), funding_source="subscription", budget_bypass=False,
        est_turn_tokens=10000, has_wallet=True, subscription_available_usd=0.05,
        project_budget_snapshot=None, personal_can_pay_turn=True,
    )
    assert 0 < res.plan_project_tokens_est < 10000
    assert len(sub.reserved) == 1                        # primary hold on subscription
    assert res.wallet_reservation_active is True


async def test_reserve_denies_when_primary_fails_and_no_wallet():
    ctx = _ctx(budget=_Budget(overdraft=0.0, reserve_fail=True))
    out = await ff.reserve_plan_funding(
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=False,
        est_turn_tokens=2000, has_wallet=False, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": 0.0, "available_usd": 1000.0},
        personal_can_pay_turn=False,
    )
    assert out.status is ff.ReserveStatus.DENIED
    assert "reservation_failed" in out.deny_code or out.deny_code == "no_funding_source"


async def test_reserve_switches_to_paid_when_primary_fails_and_fallback_allowed():
    # primary reserve fails, user can pay, allow_paid_lane_fallback -> SWITCH_TO_PAID
    ctx = _ctx(budget=_Budget(overdraft=0.0, reserve_fail=True))
    out = await ff.reserve_plan_funding(
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=False,
        est_turn_tokens=2000, has_wallet=True, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": 0.0, "available_usd": 1000.0},
        personal_can_pay_turn=True, allow_paid_lane_fallback=True,
    )
    assert out.status is ff.ReserveStatus.SWITCH_TO_PAID
    assert out.switch_reason == "app_budget_reservation_failed"
    assert ctx.budget_limiter.reserved == []   # no primary hold left
    assert ctx.cp_manager.user_credits_mgr.reserved == []  # no wallet hold (caller does it)


async def test_settle_wallet_primary_paid_lane():
    # funding_source="wallet" -> wallet pays everything; project absorbs uncovered.
    credits = _Credits(consume_uncovered=0)
    ctx = _ctx(credits=credits)
    res = ff.PlanFundingReservation(
        funding_source="wallet", budget_bypass=False, est_turn_tokens=2000,
        wallet_reservation_id="scope-1", wallet_reserved_tokens=2000, wallet_reservation_active=True,
        has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=1500, total_cost_usd=0.015,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    assert len(credits.committed) == 1                 # wallet reservation committed
    assert credits.committed[0]["tokens"] == 1500
    assert ctx.budget_limiter.forced == []             # no shortfall (fully covered)
    assert len(ctx.rl.commits) == 1                    # RL recorded (reservation-free)
    assert out.wallet_usd == pytest.approx(0.015, rel=1e-6)


async def test_settle_wallet_zero_cost_releases_reservation():
    # paid lane with zero actual cost (ranked_tokens=0): commit_reserved no-ops on
    # tokens<=0, so the wallet hold must be RELEASED, not left 'reserved' until TTL.
    credits = _Credits()
    ctx = _ctx(credits=credits)
    res = ff.PlanFundingReservation(
        funding_source="wallet", budget_bypass=False, est_turn_tokens=2000,
        wallet_reservation_id="scope-1", wallet_reserved_tokens=2000, wallet_reservation_active=True,
        has_wallet=True,
    )
    out = await ff.settle_plan_funding(
        ctx, res, ranked_tokens=0, total_cost_usd=0.0,
        effective_policy=QuotaPolicy(tokens_per_month=10**9),
        plan_has_lifetime_budget=True, user_budget_tokens=10**9,
    )
    assert credits.committed == []                     # nothing committed
    assert len(credits.released) == 1                  # hold released
    assert credits.released[0]["reservation_id"] == "scope-1"
    assert res.wallet_reservation_active is False


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


async def test_reserve_bypass_takes_no_money_hold():
    ctx = _ctx()
    res = await _resv(
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=True,
        est_turn_tokens=2000, has_wallet=False, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": None, "available_usd": 0.0},
        personal_can_pay_turn=False,
    )
    assert res.app_reservation_active is False
    assert ctx.budget_limiter.reserved == []


# --------------------------------------------------------------------------
# Settle
# --------------------------------------------------------------------------
async def test_settle_project_commits_reservation_and_rl():
    ctx = _ctx(budget=_Budget(overdraft=None, available_usd=1000.0))
    res = await _resv(
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=False,
        est_turn_tokens=2000, has_wallet=False, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": None, "available_usd": 1000.0},
        personal_can_pay_turn=False,
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
        ctx, admit=_Admit(reserved=2000), funding_source="project", budget_bypass=True,
        est_turn_tokens=2000, has_wallet=False, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": None, "available_usd": 0.0},
        personal_can_pay_turn=False,
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
    # finite overdraft -> primary partial, wallet overflow; wallet consume reports
    # uncovered tokens -> project absorbs as shortfall:wallet_plan
    credits = _Credits(balance=10**9, commit_uncovered=0, consume_uncovered=0)
    budget = _Budget(overdraft=0.0, available_usd=0.05)
    ctx = _ctx(budget=budget, credits=credits)
    res = await _resv(
        ctx, admit=_Admit(reserved=10000), funding_source="project", budget_bypass=False,
        est_turn_tokens=10000, has_wallet=True, subscription_available_usd=0.0,
        project_budget_snapshot={"overdraft_limit_usd": 0.0, "available_usd": 0.05},
        personal_can_pay_turn=True,
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

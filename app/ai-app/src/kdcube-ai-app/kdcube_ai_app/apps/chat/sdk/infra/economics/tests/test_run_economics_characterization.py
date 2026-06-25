# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Characterization (golden) tests for the economics of BaseEntrypointWithEconomics.run().

These pin the CURRENT pre-run + post-run economics behavior of run() — the chosen
lane/funding and the exact reservation/commit/settle calls it makes — so that a
later consolidation onto the shared funding_flow engine can be proven to preserve
behavior. They are deterministic and redis-free: every collaborator (rate limiter,
project budget, wallet credits, subscription, plan resolution) is a recording fake,
the actual work (execute_core) and accounting (run_accounting) are stubbed with a
fixed cost, and the resulting calls are captured as a normalized trace.

This is a safety net, not a correctness spec: the golden encodes "what run() does
today".
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from kdcube_ai_app.infra import accounting as acct
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import AdmitResult
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)


# --------------------------------------------------------------------------
# Recording fakes
# --------------------------------------------------------------------------
@dataclass
class _Snap:
    balance_usd: float = 0.0
    reserved_usd: float = 0.0
    available_usd: float = 1000.0


@dataclass
class _RR:
    reservation_id: Any
    reserved_usd: float
    expires_at: Any
    snapshot: _Snap


class _PlanBalance:
    def __init__(self, wallet: bool = False):
        self._wallet = wallet

    def has_lifetime_budget(self) -> bool:
        return self._wallet

    def has_plan_override(self) -> bool:
        return False

    def plan_override_is_active(self) -> bool:
        return False


class _Sub:
    def __init__(self, active: bool = True, plan_id: str = "payasyougo", provider: str = "internal"):
        self.status = "active" if active else "canceled"
        self.monthly_price_cents = 3000
        self.next_charge_at = None
        self.plan_id = plan_id
        self.provider = provider
        self.stripe_subscription_id = None
        self.last_charged_at = None


class _SubMgr:
    def __init__(self, sub=None):
        self.sub = sub

    async def get_subscription(self, **kw):
        return self.sub


class _Credits:
    def __init__(self, balance: int = 0, reserve_ok: bool = True):
        self.balance = balance
        self.reserve_ok = reserve_ok
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
        return 0

    async def consume_lifetime_tokens(self, **kw):
        self.consumed.append(kw)
        return 0

    async def release_lifetime_token_reservation(self, **kw):
        self.released.append(kw)


class _CP:
    def __init__(self, *, plan_balance=None, sub=None, wallet=0, policy=None, reserve_ok=True):
        self._plan_balance = plan_balance or _PlanBalance(wallet=bool(wallet))
        self.user_credits_mgr = _Credits(balance=wallet, reserve_ok=reserve_ok)
        self.subscription_mgr = _SubMgr(sub=sub)
        self._policy = policy or QuotaPolicy(
            max_concurrent=2, requests_per_day=100, tokens_per_month=10_000_000,
        )

    async def get_user_plan_balance(self, **kw):
        return self._plan_balance

    async def get_plan_quota_policy(self, **kw):
        return self._policy


class _RL:
    def __init__(self, allowed: bool = True, reason=None, deny_first: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.deny_first = deny_first   # deny the 1st admit (plan), allow the rest (paid re-admit)
        self.admit_calls = []
        self.commits = []
        self.releases = []
        self.ns = "ns"
        self.rolling_hour_now = 0   # post-commit rolling-hour value returned to the snapshot

    async def admit(self, **kw):
        idx = len(self.admit_calls)
        self.admit_calls.append(kw)
        base_allowed = self.allowed and not (self.deny_first and idx == 0)
        reserve = int(kw.get("reserve_tokens") or 0)
        snapshot = {"tok_month": 0, "tok_day": 0, "tok_hour": 0, "req_day": 0, "req_month": 0}
        if kw.get("wallet_aware"):
            # Model the wallet-aware Lua split: quota is unbounded in the fake, so the
            # plan part is the caller-sized want_resv; the wallet covers the remainder.
            r_total = int(kw.get("r_total") if kw.get("r_total") is not None else reserve)
            wallet_avail = int(kw.get("wallet_available_tokens") or 0)
            reserved = reserve if base_allowed else 0
            wallet_part = max(r_total - reserved, 0)
            allowed = base_allowed and (wallet_part <= wallet_avail)
            reason = None if allowed else ("wallet_insufficient" if base_allowed else (self.reason or "tokens_per_month"))
            return AdmitResult(
                allowed=allowed,
                reason=reason,
                lock_id=kw.get("lock_id") if allowed else None,
                snapshot=snapshot,
                reserved_tokens=reserved if allowed else 0,
                reservation_id=(kw.get("reservation_id") if (allowed and reserved > 0) else None),
                wallet_part=wallet_part if allowed else 0,
            )
        allowed = base_allowed
        return AdmitResult(
            allowed=allowed,
            reason=None if allowed else (self.reason or "tokens_per_month"),
            lock_id=kw.get("lock_id") if allowed else None,
            snapshot=snapshot,
            reserved_tokens=reserve if allowed else 0,
            reservation_id=(kw.get("reservation_id") if (allowed and reserve > 0) else None),
        )

    async def commit_with_reservation(self, **kw):
        self.commits.append(kw)

    async def token_capacity_for_reservation(self, **kw):
        return {"available_tokens": 10**9, "own_reserved_tokens": int(kw.get("reserved_tokens") or 0)}

    async def release_token_reservation(self, **kw):
        self.releases.append(("tok", kw))
        return 1

    async def release(self, **kw):
        self.releases.append(("lock", kw))
        return 0

    async def _rolling_hour_stats(self, prefix, now, *, limit=None, reserved=0):
        # already post-commit when run()'s post-run snapshot reads it
        return (int(self.rolling_hour_now), None)


class _Budget:
    def __init__(self, available_usd: float = 1000.0, overdraft=None, reserve_fail: bool = False):
        self.available_usd = available_usd
        self.overdraft = overdraft
        self.reserve_fail = reserve_fail
        self.reserved = []
        self.committed = []
        self.released = []
        self.forced = []

    async def get_app_budget_balance(self):
        return {"available_usd": self.available_usd, "overdraft_limit_usd": self.overdraft}

    async def reserve(self, **kw):
        if self.reserve_fail:
            raise BudgetInsufficientFunds("no funds")
        self.reserved.append(kw)
        return _RR(reservation_id=kw.get("reservation_id"), reserved_usd=float(kw.get("amount_usd") or 0.0),
                   expires_at=None, snapshot=_Snap(available_usd=self.available_usd))

    async def commit_reserved_spend(self, **kw):
        self.committed.append(kw)

    async def release_reservation(self, **kw):
        self.released.append(kw)

    async def force_project_spend(self, **kw):
        self.forced.append(kw)


class _SubBudget:
    """Fake SubscriptionBudgetLimiter (monkeypatched into run()'s import)."""
    instances: list = []
    available_usd: float = 10.0

    def __init__(self, **kw):
        self.available_usd = _SubBudget.available_usd
        self.reserved = []
        self.committed = []
        self.released = []
        _SubBudget.instances.append(self)

    async def reap_expired_reservations(self, **kw):
        return 0

    async def get_subscription_budget_balance(self):
        return {"available_usd": self.available_usd, "balance_usd": self.available_usd}

    async def reserve(self, **kw):
        self.reserved.append(kw)
        return _RR(kw.get("reservation_id"), float(kw.get("amount_usd") or 0.0), None, _Snap())

    async def commit_reserved_spend(self, **kw):
        self.committed.append(kw)

    async def release_reservation(self, **kw):
        self.released.append(kw)


class _Logger:
    def log(self, msg, level="INFO"):
        pass


class _Comm:
    def __init__(self):
        self.events = []

    async def service_event(self, **kw):
        self.events.append(kw)


class _Spec:
    id = "test-bundle@1"


class _Config:
    ai_bundle_spec = _Spec()


# --------------------------------------------------------------------------
# Entrypoint harness
# --------------------------------------------------------------------------
def _make_ep(
    *,
    role: str = "registered",
    wallet: int = 0,
    sub=None,
    budget: Optional[_Budget] = None,
    reserve_ok: bool = True,
    cost_usd: float = 0.03,
    ranked_tokens: int = 1000,
    policy=None,
    rl_deny_first: bool = False,
    monkeypatch=None,
):
    cp = _CP(sub=sub, wallet=wallet, plan_balance=_PlanBalance(wallet=bool(wallet)),
             reserve_ok=reserve_ok, policy=policy)
    rl = _RL(allowed=True, deny_first=rl_deny_first)
    budget = budget if budget is not None else _Budget()

    ep = object.__new__(BaseEntrypointWithEconomics)
    ep.cp_manager = cp
    ep.rl = rl
    ep.budget_limiter = budget
    ep.logger = _Logger()
    # comm/comm_context are read-only properties backed by these fields:
    ep._comm = _Comm()
    ep._comm_context = None
    ep._event_filter = None
    ep.config = _Config()
    ep.redis = None  # disables the quota distributed lock
    ep.pg_pool = None
    ep.bundle_props = {}
    # app_quota_policies is a computed property on the entrypoint (real plans).
    ep.subj = "t:p:u1"
    ep._turn_id = "turn_char_1"
    ep._app_state = {
        "user": "u1", "tenant": "t", "project": "p", "user_type": role,
        "conversation_id": "conv1", "session_id": "sess1",
    }

    async def _noop(*a, **k):
        return None

    async def _refresh(*a, **k):
        return None

    async def _ensure(*a, **k):
        return None

    async def _execute_core(*, state, thread_id, params):
        return {"answer": "ok"}

    async def _run_accounting(**kw):
        return (ranked_tokens, {"cost_total_usd": cost_usd})

    ep.refresh_bundle_props = _refresh
    ep.ensure_policies_initialized = _ensure
    ep.execute_core = _execute_core
    ep.run_accounting = _run_accounting
    ep._invoke_pre_run_hook = _noop
    ep._invoke_post_run_hook = _noop

    if monkeypatch is not None:
        monkeypatch.setattr(acct, "clear_turn_events", _noop, raising=False)

    return ep


def _trace(ep) -> dict:
    b = ep.budget_limiter
    c = ep.cp_manager.user_credits_mgr
    r = ep.rl

    def _usd(kw):
        v = kw.get("amount_usd")
        if v is None:
            v = kw.get("spent_usd")
        return round(float(v), 4) if v is not None else None

    return {
        "budget_reserved_usd": [_usd(k) for k in b.reserved],
        "budget_committed_usd": [_usd(k) for k in b.committed],
        "budget_forced": [(_usd(k), k.get("note") or k.get("notes")) for k in b.forced],
        "budget_released": len(b.released),
        "wallet_reserved_tokens": [int(k.get("tokens") or 0) for k in c.reserved],
        "wallet_committed_tokens": [int(k.get("tokens") or 0) for k in c.committed],
        "wallet_consumed_tokens": [int(k.get("tokens") or 0) for k in c.consumed],
        "wallet_released": len(c.released),
        "rl_committed_tokens": [int(k.get("tokens") or 0) for k in r.commits],
        "rl_released": [kind for (kind, _) in r.releases],
        "events": [e.get("type") for e in ep._comm.events],
    }


# --------------------------------------------------------------------------
# Golden scenarios — pin run()'s current economics so a later consolidation
# onto funding_flow can be proven behavior-preserving. Amounts depend on the
# (constant) reference usd-per-token; if it changes, refresh the goldens.
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_characterize_registered_project_unlimited(monkeypatch):
    # registered, project-funded, unlimited overdraft, no wallet -> project covers all.
    # Unified split reserves the full reservation floor ($2 -> est_turn tokens); settle
    # commits the actual cost ($0.03).
    ep = _make_ep(role="registered", wallet=0, budget=_Budget(overdraft=None),
                  cost_usd=0.03, ranked_tokens=1000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [2.0], "budget_committed_usd": [0.03],
        "budget_forced": [], "budget_released": 0,
        "wallet_reserved_tokens": [], "wallet_committed_tokens": [], "wallet_consumed_tokens": [],
        "wallet_released": 0, "rl_committed_tokens": [1000], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_registered_project_finite_plus_wallet_split(monkeypatch):
    # finite project ($0.05, overdraft 0) + wallet; project caps plan_part at ~2898
    # tokens, the wallet holds the rest of the reservation floor (R - plan_part). The
    # reservation now covers the whole turn, so settle commits the wallet hold (no
    # extra overage consume).
    ep = _make_ep(role="registered", wallet=200_000,
                  budget=_Budget(available_usd=0.05, overdraft=0.0),
                  cost_usd=0.25, ranked_tokens=16_000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [0.05], "budget_committed_usd": [0.0453],
        "budget_forced": [], "budget_released": 0,
        "wallet_reserved_tokens": [113045], "wallet_committed_tokens": [13102],
        "wallet_consumed_tokens": [], "wallet_released": 0,
        "rl_committed_tokens": [2898], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_project_shortfall_absorption(monkeypatch):
    # no wallet, actual cost ($2.50) exceeds the reservation floor ($2) -> the project
    # absorbs the overage at settle as shortfall:free_plan (no wallet to take it).
    ep = _make_ep(role="registered", wallet=0,
                  budget=_Budget(available_usd=1000.0, overdraft=None),
                  cost_usd=2.50, ranked_tokens=160_000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [2.0], "budget_committed_usd": [1.8116],
        "budget_forced": [(0.6884, "shortfall:free_plan")], "budget_released": 0,
        "wallet_reserved_tokens": [], "wallet_committed_tokens": [], "wallet_consumed_tokens": [],
        "wallet_released": 0, "rl_committed_tokens": [160000], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_privileged_bypass(monkeypatch):
    # privileged -> no pre-run hold; project charged after run via force_project_spend
    ep = _make_ep(role="privileged", wallet=0, budget=_Budget(overdraft=None),
                  cost_usd=0.03, ranked_tokens=1000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [], "budget_committed_usd": [],
        "budget_forced": [(0.03, "settle: admin bypass")], "budget_released": 0,
        "wallet_reserved_tokens": [], "wallet_committed_tokens": [], "wallet_consumed_tokens": [],
        "wallet_released": 0, "rl_committed_tokens": [1000], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_project_exhausted_wallet_covers(monkeypatch):
    # project funds exhausted ($0, overdraft 0) + wallet present -> plan_part=0, the
    # wallet covers the whole turn (unified split; no lane switch).
    ep = _make_ep(role="registered", wallet=200_000,
                  budget=_Budget(available_usd=0.0, overdraft=0.0),
                  cost_usd=0.05, ranked_tokens=2_000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [], "budget_committed_usd": [],
        "budget_forced": [], "budget_released": 0,
        "wallet_reserved_tokens": [115943], "wallet_committed_tokens": [2000],
        "wallet_consumed_tokens": [], "wallet_released": 0,
        # plan_part=0 (no project funds) -> no plan token quota consumed
        "rl_committed_tokens": [0], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_subscription_funding(monkeypatch):
    # active EXTERNAL subscription -> funding_source=subscription; reserve+commit on
    # the subscription budget, project untouched.
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription_budget as sub_budget_mod
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription as sub_mod
    _SubBudget.instances = []
    _SubBudget.available_usd = 10.0
    monkeypatch.setattr(sub_budget_mod, "SubscriptionBudgetLimiter", _SubBudget)
    monkeypatch.setattr(sub_mod, "build_subscription_period_descriptor",
                        lambda **kw: {"period_key": "pk", "period_start": None, "period_end": None})
    ep = _make_ep(role="registered", wallet=0, sub=_Sub(active=True, provider="stripe"),
                  budget=_Budget(overdraft=None), cost_usd=0.03, ranked_tokens=1000, monkeypatch=monkeypatch)
    await ep.run()
    sub_lim = _SubBudget.instances[-1]
    assert [round(float(k.get("amount_usd")), 4) for k in sub_lim.reserved] == [2.0]
    assert [round(float(k.get("spent_usd")), 4) for k in sub_lim.committed] == [0.03]
    assert ep.budget_limiter.forced == []        # project not touched
    assert ep.budget_limiter.committed == []
    assert [int(k.get("tokens") or 0) for k in ep.rl.commits] == [1000]
    assert [e.get("type") for e in ep._comm.events] == []


@pytest.mark.asyncio
async def test_characterize_internal_subscription_funds_from_project(monkeypatch):
    # active INTERNAL subscription -> funding_source=project: no subscription budget is
    # created; the turn reserves+commits on the project budget bounded by quota.
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription_budget as sub_budget_mod
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription as sub_mod
    _SubBudget.instances = []
    _SubBudget.available_usd = 10.0
    monkeypatch.setattr(sub_budget_mod, "SubscriptionBudgetLimiter", _SubBudget)
    monkeypatch.setattr(sub_mod, "build_subscription_period_descriptor",
                        lambda **kw: {"period_key": "pk", "period_start": None, "period_end": None})
    ep = _make_ep(role="registered", wallet=0, sub=_Sub(active=True, provider="internal"),
                  budget=_Budget(overdraft=None),
                  cost_usd=0.03, ranked_tokens=1000, monkeypatch=monkeypatch)
    await ep.run()
    # no subscription budget instantiated for an internal plan
    assert _SubBudget.instances == []
    # routes exactly like a registered project-funded turn
    assert _trace(ep) == {
        "budget_reserved_usd": [2.0], "budget_committed_usd": [0.03],
        "budget_forced": [], "budget_released": 0,
        "wallet_reserved_tokens": [], "wallet_committed_tokens": [], "wallet_consumed_tokens": [],
        "wallet_released": 0, "rl_committed_tokens": [1000], "rl_released": [], "events": [],
    }


@pytest.mark.asyncio
async def test_characterize_subscription_with_wallet_untouched(monkeypatch):
    # subscription user WITH a wallet: the subscription budget is the primary and its
    # funds + quota cover the whole turn (plan_part == R), so the wallet stays untouched
    # (wallet_part == 0). One admit, one split — no lane switch.
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription_budget as sub_budget_mod
    from kdcube_ai_app.apps.chat.sdk.infra.economics import subscription as sub_mod
    _SubBudget.instances = []
    _SubBudget.available_usd = 10.0
    monkeypatch.setattr(sub_budget_mod, "SubscriptionBudgetLimiter", _SubBudget)
    monkeypatch.setattr(sub_mod, "build_subscription_period_descriptor",
                        lambda **kw: {"period_key": "pk", "period_start": None, "period_end": None})
    ep = _make_ep(role="registered", wallet=200_000, sub=_Sub(active=True, provider="stripe"),
                  budget=_Budget(overdraft=None), cost_usd=0.03, ranked_tokens=1000,
                  monkeypatch=monkeypatch)
    await ep.run()
    sub_lim = _SubBudget.instances[-1]
    credits = ep.cp_manager.user_credits_mgr
    assert [round(float(k.get("amount_usd")), 4) for k in sub_lim.reserved] == [2.0]
    assert [round(float(k.get("spent_usd")), 4) for k in sub_lim.committed] == [0.03]
    assert ep.budget_limiter.forced == [] and ep.budget_limiter.committed == []  # project untouched
    assert credits.reserved == [] and credits.committed == [] and credits.consumed == []  # wallet untouched
    # subscription is the primary and covers the turn within quota -> RL commits the quota
    assert [int(k.get("tokens") or 0) for k in ep.rl.commits] == [1000]
    assert len(ep.rl.admit_calls) == 1  # single split admit, no lane switch
    assert [e.get("type") for e in ep._comm.events] == []


@pytest.mark.asyncio
async def test_characterize_post_run_warning(monkeypatch):
    # small token quota so remaining-after-commit < est_turn_tokens -> rate_limit.warning
    ep = _make_ep(role="registered", wallet=0, budget=_Budget(overdraft=None),
                  policy=QuotaPolicy(max_concurrent=2, requests_per_day=100, tokens_per_month=2500),
                  cost_usd=0.03, ranked_tokens=1000, monkeypatch=monkeypatch)
    await ep.run()
    assert _trace(ep) == {
        "budget_reserved_usd": [0.0431], "budget_committed_usd": [0.03],
        "budget_forced": [], "budget_released": 0,
        "wallet_reserved_tokens": [], "wallet_committed_tokens": [], "wallet_consumed_tokens": [],
        "wallet_released": 0, "rl_committed_tokens": [1000], "rl_released": [],
        "events": ["rate_limit.warning"],
    }
    # token budget is the binding constraint with a sub-turn remainder (1500 < est) and
    # requests are still available -> the warning surfaces the token balance rather than a
    # misleading "last message" (the low-tokens message was previously unreachable).
    warn = next(e for e in ep._comm.events if e.get("type") == "rate_limit.warning")
    assert "running low on tokens" in warn["data"]["rate_limit"]["user_message"].lower()


async def test_post_run_hour_snapshot_not_double_counted(monkeypatch):
    # Regression: settle commits this turn's tokens before run() reads the rolling-hour
    # window, so tok_h_now is already post-commit. The snapshot must not add the commit
    # again — doubling it produced false "tokens_per_hour exceeded" post-run violations
    # (routing a still-under-limit turn to the exhausted path).
    ep = _make_ep(role="registered", wallet=0, budget=_Budget(overdraft=None),
                  policy=QuotaPolicy(max_concurrent=2, requests_per_day=100, tokens_per_hour=17000),
                  cost_usd=0.03, ranked_tokens=16000, monkeypatch=monkeypatch)
    ep.rl.rolling_hour_now = 16000   # post-commit rolling-hour usage, under the 17000 limit
    await ep.run()
    types = [e.get("type") for e in ep._comm.events]
    # under the limit -> approaching warning, NOT a false post-run "exceeded"
    assert "analytics.rate_limit.post_run_exceeded" not in types
    warn = next(e for e in ep._comm.events if e.get("type") == "rate_limit.warning")
    # the hour snapshot is the post-commit read (16000), not 16000 + 16000 = 32000
    assert warn["data"]["snapshot"]["tok_hour"] == 16000

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/sdk/infra/economics/funding_flow.py
"""
Shared plan-lane funding reserve + settlement, extracted so a single
implementation backs BOTH the chat run() path and the reusable EconomicsGuard.

It mirrors BaseEntrypointWithEconomics.run()'s plan-lane money flow:
  reserve:  size the primary (project|subscription) cover + reserve it, then
            reserve wallet overflow for the remainder.
  settle:   read fresh capacities, split the actual usage with
            allocate_plan_wallet_settlement (primary + wallet + project
            absorption), commit each source, and commit RL token quota.

It deliberately contains NO event emission and NO UI insight — those are
run()-specific concerns. reserve_plan_funding does NOT itself switch lanes: it
returns a ReserveOutcome (OK / SWITCH_TO_PAID / DENIED) and the caller owns the
paid-lane switch (release RL + re-admit paid + reserve wallet primary).

Design: docs/economics/economic-enforcement-non-chat-v2-README.md (§4.1, §6)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import (
    QuotaPolicy,
    EconomicsLimitException,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
from kdcube_ai_app.apps.chat.sdk.infra.economics.settlement_allocation import (
    PlanWalletSettlementInput,
    PlanWalletSettlementAllocation,
    allocate_plan_wallet_settlement,
)

SAFETY_MARGIN = 1.15
# Budget rows store integer cents; a hold below $0.01 rounds to 0 and reserve()
# raises ValueError. Below this we treat the primary cover as exhausted.
_MIN_RESERVE_USD = 1.0 / 100


def _log_noop(stage: str, msg: str, level: str = "INFO", **kv) -> None:
    pass


@dataclass
class FundingContext:
    """Primitives + identity shared by reserve/settle. rl_bundle_id is the global
    (per tenant/project) rate-limit subject; bundle_id is the real bundle for
    budget attribution."""
    rl: Any
    budget_limiter: Any
    cp_manager: Any
    tenant: str
    project: str
    user_id: str
    subject_id: str
    bundle_id: str
    rl_bundle_id: str
    scope_id: str
    usd_per_token: float
    now: datetime
    subscription_limiter: Any = None
    log: Callable[..., None] = _log_noop


@dataclass
class PlanFundingReservation:
    """Holds the pre-run reservation handles consumed by settlement."""
    funding_source: str          # "project" | "subscription"
    budget_bypass: bool
    est_turn_tokens: int

    # RL plan-token reservation (from admit)
    plan_reservation_id: Optional[str] = None
    plan_reserved_tokens: int = 0
    plan_reservation_active: bool = False

    # primary (project/subscription) money hold
    app_reservation_id: Optional[UUID] = None
    app_reserved_usd: float = 0.0
    app_reservation_active: bool = False
    plan_project_tokens_est: int = 0

    # wallet overflow hold
    wallet_reservation_id: Optional[str] = None
    wallet_reserved_tokens: int = 0
    wallet_reservation_active: bool = False

    has_wallet: bool = False


@dataclass
class SettlementResult:
    ranked_tokens: int
    total_cost_usd: float
    primary_funding_usd: float = 0.0
    wallet_usd: float = 0.0
    project_absorption_usd: float = 0.0
    quota_commit_tokens: int = 0
    allocation: Optional[PlanWalletSettlementAllocation] = None
    extra_project_items: list = field(default_factory=list)


class ReserveStatus(Enum):
    OK = "ok"                          # reservation placed (plan lane); use .reservation
    SWITCH_TO_PAID = "switch_to_paid"  # primary can't cover; caller re-plans as paid lane
    DENIED = "denied"                  # cannot fund and cannot pay; caller denies


@dataclass
class ReserveOutcome:
    """Non-raising result of reserve_plan_funding. The caller decides what to do:
    OK -> proceed; SWITCH_TO_PAID -> release RL + re-admit paid + reserve wallet
    primary; DENIED -> raise/emit the denial."""
    status: ReserveStatus
    reservation: Optional[PlanFundingReservation] = None
    switch_reason: Optional[str] = None
    deny_code: Optional[str] = None
    deny_message: str = ""
    deny_data: dict = field(default_factory=dict)


def _ok(res: PlanFundingReservation) -> ReserveOutcome:
    return ReserveOutcome(status=ReserveStatus.OK, reservation=res)


def _switch(reason: str) -> ReserveOutcome:
    return ReserveOutcome(status=ReserveStatus.SWITCH_TO_PAID, switch_reason=reason)


def _denied(*, code: str, message: str, funding_source: str, est_turn_tokens: int) -> ReserveOutcome:
    return ReserveOutcome(
        status=ReserveStatus.DENIED, deny_code=code, deny_message=message,
        deny_data={
            "reason": code, "funding_source": funding_source,
            "min_tokens_required": int(est_turn_tokens), "lane": "deny",
        },
    )


def _cost_for_tokens(*, tokens: int, ranked_tokens: int, total_cost: float) -> float:
    if tokens <= 0 or ranked_tokens <= 0 or total_cost <= 0:
        return 0.0
    return float(total_cost) * (float(tokens) / float(ranked_tokens))


def _cap_tokens_for_usd(*, available_usd: float, usd_per_token: float) -> int:
    if usd_per_token <= 0:
        return 0
    cap_usd = max(float(available_usd or 0.0), 0.0)
    return int(cap_usd / (usd_per_token * SAFETY_MARGIN))


# ---------------------------------------------------------------------------
# Pre-run reservation
# ---------------------------------------------------------------------------
async def reserve_plan_funding(
    ctx: FundingContext,
    *,
    admit: Any,
    funding_source: str,
    budget_bypass: bool,
    est_turn_tokens: int,
    has_wallet: bool,
    subscription_available_usd: float,
    project_budget_snapshot: Optional[dict],
    personal_can_pay_turn: bool,
    allow_paid_lane_fallback: bool = False,
    ttl_sec: int = 900,
) -> ReserveOutcome:
    """
    Plan-lane reservation: size the primary cover, reserve it, then reserve
    wallet overflow. Mirrors run() (plan lane).

    Non-raising: returns a ReserveOutcome.
      - OK: reservation placed (use outcome.reservation).
      - SWITCH_TO_PAID: primary cannot cover and allow_paid_lane_fallback is set;
        the caller releases RL + re-admits the paid policy + reserves wallet as
        primary. Any partial primary money hold is released before returning.
      - DENIED: the user cannot fund and cannot pay personally.

    With allow_paid_lane_fallback=False (default, non-chat) the behavior matches
    the previous version: a primary-exhausted user with a wallet covers the whole
    request via wallet overflow WITHIN the plan lane (no lane switch).
    """
    usd_per_token = ctx.usd_per_token
    funding_limiter = ctx.subscription_limiter if funding_source == "subscription" else ctx.budget_limiter

    plan_reserved_tokens = int(getattr(admit, "reserved_tokens", 0) or 0)
    plan_reservation_id = getattr(admit, "reservation_id", None)
    plan_reservation_active = plan_reserved_tokens > 0 and plan_reservation_id is not None

    res = PlanFundingReservation(
        funding_source=funding_source,
        budget_bypass=budget_bypass,
        est_turn_tokens=int(est_turn_tokens),
        plan_reservation_id=plan_reservation_id,
        plan_reserved_tokens=plan_reserved_tokens,
        plan_reservation_active=plan_reservation_active,
        has_wallet=has_wallet,
    )

    # --- size the primary (project/subscription) cover -------------------
    plan_project_tokens_est = int(plan_reserved_tokens)
    if funding_source == "subscription":
        plan_project_tokens_est = int(est_turn_tokens)
        if has_wallet:
            cap_tokens = _cap_tokens_for_usd(available_usd=subscription_available_usd, usd_per_token=usd_per_token)
            plan_project_tokens_est = min(int(est_turn_tokens), int(cap_tokens))
    elif funding_source == "project" and project_budget_snapshot:
        od_lim = project_budget_snapshot.get("overdraft_limit_usd")
        if od_lim is not None:
            cap_usd = float(project_budget_snapshot.get("available_usd") or 0.0) + float(od_lim or 0.0)
            cap_tokens = _cap_tokens_for_usd(available_usd=cap_usd, usd_per_token=usd_per_token)
            plan_project_tokens_est = min(int(plan_project_tokens_est), int(cap_tokens))
        # od_lim is None => unlimited overdraft => keep full plan_project_tokens_est

    # sub-cent primary cover can't be reserved (cents granularity) -> treat as 0
    if plan_project_tokens_est > 0 and (float(plan_project_tokens_est) * usd_per_token * SAFETY_MARGIN) < _MIN_RESERVE_USD:
        ctx.log("reserve.plan", "primary cover below minimum chargeable; treating as exhausted", "WARN",
                plan_project_tokens_est=plan_project_tokens_est)
        plan_project_tokens_est = 0

    # --- reserve the primary cover ---------------------------------------
    if plan_project_tokens_est <= 0:
        if budget_bypass:
            pass  # admin: no money hold
        elif not personal_can_pay_turn:
            return _denied(
                code="plan_exhausted_no_personal",
                message="Plan funding exhausted and user cannot pay from personal credits.",
                funding_source=funding_source,
                est_turn_tokens=est_turn_tokens,
            )
        elif allow_paid_lane_fallback:
            # primary can't cover, but the user can pay -> caller switches to paid lane
            return _switch(
                "subscription_budget_zero_for_turn" if funding_source == "subscription"
                else "plan_tokens_exhausted_for_turn"
            )
        # else: wallet covers the whole request via overflow below (plan lane)
    elif not budget_bypass:
        app_reserved_usd = float(plan_project_tokens_est) * usd_per_token * SAFETY_MARGIN
        app_reservation_id = uuid4()
        reserve_kwargs = dict(
            reservation_id=app_reservation_id,
            bundle_id=ctx.bundle_id,
            provider=None,
            request_id=ctx.scope_id,
            amount_usd=float(app_reserved_usd),
            ttl_sec=int(ttl_sec),
            notes=f"plan reserve: scope={ctx.scope_id}, plan_cover_est={plan_project_tokens_est}",
        )
        if funding_source == "project":
            reserve_kwargs["user_id"] = ctx.user_id
        try:
            await funding_limiter.reserve(**reserve_kwargs)
            res.app_reservation_id = app_reservation_id
            res.app_reserved_usd = float(app_reserved_usd)
            res.app_reservation_active = True
        except BudgetInsufficientFunds as e:
            ctx.log("reserve.app", "primary reservation denied", "WARN", error=str(e))
            if not personal_can_pay_turn:
                return _denied(
                    code=f"{funding_source}_budget_reservation_failed_no_personal",
                    message=f"{funding_source} funding cannot reserve and user cannot pay.",
                    funding_source=funding_source,
                    est_turn_tokens=est_turn_tokens,
                )
            if allow_paid_lane_fallback:
                # primary reserve failed but the user can pay -> caller switches to paid lane
                return _switch(
                    "subscription_reservation_failed" if funding_source == "subscription"
                    else "app_budget_reservation_failed"
                )
            # fall back to wallet-only for this request (plan lane, primary=0)
            plan_project_tokens_est = 0

    res.plan_project_tokens_est = int(plan_project_tokens_est)

    # --- reserve wallet overflow -----------------------------------------
    # Subscription-only users (no wallet) don't overflow; everyone else does.
    if funding_source != "subscription" or has_wallet:
        overflow_tokens_est = max(int(est_turn_tokens) - int(plan_project_tokens_est), 0)
        if overflow_tokens_est > 0 and has_wallet:
            ok = await ctx.cp_manager.user_credits_mgr.reserve_lifetime_tokens(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                reservation_id=ctx.scope_id, tokens=int(overflow_tokens_est),
                ttl_sec=int(ttl_sec), bundle_id=ctx.bundle_id,
                notes=f"overflow reserve: scope={ctx.scope_id}, overflow={overflow_tokens_est}",
            )
            if not ok:
                # release the primary hold we already took before denying
                if res.app_reservation_active and res.app_reservation_id:
                    try:
                        if funding_source == "subscription" and ctx.subscription_limiter is not None:
                            await ctx.subscription_limiter.release_reservation(
                                reservation_id=res.app_reservation_id, project_budget=ctx.budget_limiter,
                            )
                        else:
                            await ctx.budget_limiter.release_reservation(reservation_id=res.app_reservation_id)
                    except Exception:
                        pass
                    res.app_reservation_active = False
                return _denied(
                    code="personal_reservation_failed_plan",
                    message="Insufficient personal credits to cover overflow.",
                    funding_source=funding_source,
                    est_turn_tokens=est_turn_tokens,
                )
            res.wallet_reservation_id = ctx.scope_id
            res.wallet_reserved_tokens = int(overflow_tokens_est)
            res.wallet_reservation_active = True
        elif overflow_tokens_est > 0 and not has_wallet and plan_project_tokens_est <= 0 and not budget_bypass:
            # no primary cover and no wallet -> nothing can pay
            return _denied(
                code="no_funding_source",
                message="No plan, subscription, project, or wallet funding can cover this request.",
                funding_source=funding_source,
                est_turn_tokens=est_turn_tokens,
            )

    return _ok(res)


# ---------------------------------------------------------------------------
# Post-run settlement
# ---------------------------------------------------------------------------
async def settle_plan_funding(
    ctx: FundingContext,
    res: PlanFundingReservation,
    *,
    ranked_tokens: int,
    total_cost_usd: float,
    effective_policy: QuotaPolicy,
    plan_has_lifetime_budget: bool,
    user_budget_tokens: Optional[int],
) -> SettlementResult:
    """
    Settle a completed plan-lane request: split actual usage across primary,
    wallet, and project absorption (via allocate_plan_wallet_settlement), commit
    each source, and commit RL token quota. Mirrors run() (plan lane).
    """
    ranked_tokens = int(ranked_tokens or 0)
    total_cost = float(total_cost_usd or 0.0)
    funding_source = res.funding_source
    out = SettlementResult(ranked_tokens=ranked_tokens, total_cost_usd=total_cost)

    if res.budget_bypass:
        if total_cost > 0:
            await ctx.budget_limiter.force_project_spend(
                spent_usd=float(total_cost), bundle_id=ctx.bundle_id, provider=None,
                request_id=ctx.scope_id, user_id=ctx.user_id, note="settle: admin bypass",
            )
        await _commit_rl(ctx, res, tokens=ranked_tokens)
        out.primary_funding_usd = float(total_cost)
        out.quota_commit_tokens = ranked_tokens
        return out

    if funding_source == "wallet":
        # paid lane: wallet is the primary funding; project absorbs any uncovered
        # shortfall (shortfall:wallet_paid). Wallet-paid tokens do NOT consume plan
        # quota, but we record the request + tokens against RL (reservation-free).
        user_uncovered = 0
        remaining = int(ranked_tokens)
        if res.wallet_reservation_active and res.wallet_reservation_id and res.wallet_reserved_tokens > 0:
            reserved_target = min(int(remaining), int(res.wallet_reserved_tokens))
            try:
                if reserved_target > 0:
                    reserved_uncovered = await ctx.cp_manager.user_credits_mgr.commit_reserved_lifetime_tokens(
                        tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                        reservation_id=str(res.wallet_reservation_id), tokens=int(reserved_target),
                    )
                    reserved_consumed = max(int(reserved_target) - int(reserved_uncovered or 0), 0)
                    remaining = max(remaining - reserved_consumed, 0)
                else:
                    # nothing consumed: commit_reserved_lifetime_tokens no-ops on tokens<=0
                    # and would leave the hold 'reserved' until TTL -> release it explicitly.
                    await ctx.cp_manager.user_credits_mgr.release_lifetime_token_reservation(
                        tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                        reservation_id=str(res.wallet_reservation_id), reason="zero actual cost",
                    )
            finally:
                res.wallet_reservation_active = False
        if remaining > 0:
            user_uncovered = await ctx.cp_manager.user_credits_mgr.consume_lifetime_tokens(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id, tokens=int(remaining),
            )
        user_uncovered = int(user_uncovered or 0)
        user_uncovered_usd = _cost_for_tokens(tokens=user_uncovered, ranked_tokens=ranked_tokens, total_cost=total_cost)
        if user_uncovered_usd > 0:
            await ctx.budget_limiter.force_project_spend(
                spent_usd=float(user_uncovered_usd), bundle_id=ctx.bundle_id, provider=None,
                request_id=ctx.scope_id, user_id=ctx.user_id, note="shortfall:wallet_paid",
            )
        await _commit_rl(ctx, res, tokens=ranked_tokens)
        out.wallet_usd = max(float(total_cost) - float(user_uncovered_usd), 0.0)
        out.project_absorption_usd = float(user_uncovered_usd)
        out.quota_commit_tokens = ranked_tokens
        return out

    if funding_source == "none":
        # no funding source resolved -> charge the project as a last resort (audit)
        if total_cost > 0:
            await ctx.budget_limiter.force_project_spend(
                spent_usd=float(total_cost), bundle_id=ctx.bundle_id, provider=None,
                request_id=ctx.scope_id, user_id=ctx.user_id, note="settle: no_funding_source",
            )
        await _commit_rl(ctx, res, tokens=ranked_tokens)
        out.primary_funding_usd = float(total_cost)
        out.quota_commit_tokens = ranked_tokens
        return out

    # --- fresh capacity reads (net of OTHER reservations; add back our own) ---
    quota_available_tokens: Optional[int] = None
    quota_reserved_tokens = 0
    try:
        cap = await ctx.rl.token_capacity_for_reservation(
            bundle_id=ctx.rl_bundle_id, subject_id=ctx.subject_id, policy=effective_policy,
            reservation_id=res.plan_reservation_id if res.plan_reservation_active else None,
            reserved_tokens=int(res.plan_reserved_tokens or 0) if res.plan_reservation_active else 0,
            now=ctx.now,
        )
        quota_available_tokens = cap.get("available_tokens")
        quota_reserved_tokens = int(cap.get("own_reserved_tokens") or 0)
    except Exception as ex:
        quota_available_tokens = 0 if res.plan_reservation_active else int(res.plan_project_tokens_est or 0)
        quota_reserved_tokens = int(res.plan_reserved_tokens or 0) if res.plan_reservation_active else 0
        ctx.log("charge.capacity", "fresh quota read failed; using reservation estimate", "WARN", error=str(ex))

    primary_available_usd: Optional[float] = 0.0
    if funding_source == "project":
        try:
            fresh = await ctx.budget_limiter.get_app_budget_balance()
            primary_available_usd = float(fresh.get("available_usd") or 0.0)
        except Exception as ex:
            primary_available_usd = 0.0 if res.app_reservation_active else None
            ctx.log("charge.capacity", "fresh project read failed", "WARN", error=str(ex))
    elif funding_source == "subscription" and ctx.subscription_limiter is not None:
        try:
            fresh = await ctx.subscription_limiter.get_subscription_budget_balance()
            primary_available_usd = float(fresh.get("available_usd") or 0.0)
        except Exception as ex:
            primary_available_usd = 0.0 if res.app_reservation_active else None
            ctx.log("charge.capacity", "fresh subscription read failed", "WARN", error=str(ex))

    wallet_available_tokens = 0
    if plan_has_lifetime_budget:
        try:
            bal = await ctx.cp_manager.user_credits_mgr.get_lifetime_balance(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
            )
            wallet_available_tokens = int(bal or 0)
        except Exception as ex:
            wallet_available_tokens = max(
                int(user_budget_tokens or 0)
                - (int(res.wallet_reserved_tokens or 0) if res.wallet_reservation_active else 0),
                0,
            )
            ctx.log("charge.capacity", "fresh wallet read failed", "WARN", error=str(ex))

    alloc = allocate_plan_wallet_settlement(
        PlanWalletSettlementInput(
            actual_tokens=ranked_tokens,
            actual_cost_usd=total_cost,
            quota_available_tokens=quota_available_tokens,
            quota_reserved_tokens=int(quota_reserved_tokens),
            primary_funding_available_usd=primary_available_usd,
            primary_funding_reserved_usd=float(res.app_reserved_usd or 0.0) if res.app_reservation_active else 0.0,
            primary_funding_reserved_tokens=int(res.plan_project_tokens_est or 0),
            wallet_available_tokens=int(wallet_available_tokens),
            wallet_reserved_tokens=int(res.wallet_reserved_tokens or 0) if res.wallet_reservation_active else 0,
        )
    )
    out.allocation = alloc
    plan_covered_usd = float(alloc.primary_funding_usd)
    project_absorption_usd = float(alloc.project_absorption_usd)
    plan_quota_commit_tokens = int(alloc.quota_tokens)
    user_target_tokens = int(alloc.wallet_tokens)

    # --- charge wallet (reserved part first, then reservation-free consume) ---
    user_uncovered_tokens = 0
    if user_target_tokens > 0:
        remaining = int(user_target_tokens)
        if res.wallet_reservation_active and res.wallet_reservation_id and res.wallet_reserved_tokens > 0:
            reserved_target = min(remaining, int(res.wallet_reserved_tokens))
            try:
                reserved_uncovered = await ctx.cp_manager.user_credits_mgr.commit_reserved_lifetime_tokens(
                    tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                    reservation_id=str(res.wallet_reservation_id), tokens=int(reserved_target),
                )
            finally:
                res.wallet_reservation_active = False
            reserved_consumed = max(int(reserved_target) - int(reserved_uncovered or 0), 0)
            remaining = max(remaining - reserved_consumed, 0)
        if remaining > 0:
            user_uncovered_tokens = await ctx.cp_manager.user_credits_mgr.consume_lifetime_tokens(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id, tokens=int(remaining),
            )
    elif res.wallet_reservation_active and res.wallet_reservation_id:
        try:
            await ctx.cp_manager.user_credits_mgr.release_lifetime_token_reservation(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                reservation_id=str(res.wallet_reservation_id), reason="settle: no_wallet_spend",
            )
        finally:
            res.wallet_reservation_active = False

    user_uncovered_tokens = int(user_uncovered_tokens or 0)
    user_uncovered_usd = _cost_for_tokens(tokens=user_uncovered_tokens, ranked_tokens=ranked_tokens, total_cost=total_cost)

    # wallet shortfall can re-consume any remaining plan quota room
    if user_uncovered_tokens > 0:
        extra_room = max(int(alloc.quota_capacity_tokens) - int(plan_quota_commit_tokens), 0)
        plan_quota_commit_tokens += min(int(user_uncovered_tokens), int(extra_room))

    # --- charge primary + project absorption ------------------------------
    extra_project_items: list[tuple[float, str]] = []
    app_spend_usd = float(plan_covered_usd)
    if funding_source == "subscription":
        if project_absorption_usd > 0:
            extra_project_items.append((float(project_absorption_usd), "shortfall:subscription_overage"))
        if user_uncovered_usd > 0:
            extra_project_items.append((float(user_uncovered_usd), "shortfall:wallet_subscription"))
    else:  # project
        if project_absorption_usd > 0:
            note = "shortfall:wallet_plan" if (res.has_wallet or user_target_tokens > 0) else "shortfall:free_plan"
            extra_project_items.append((float(project_absorption_usd), note))
        if user_uncovered_usd > 0:
            extra_project_items.append((float(user_uncovered_usd), "shortfall:wallet_plan"))

    if app_spend_usd > 0 or (res.app_reservation_active and res.app_reservation_id):
        if res.app_reservation_active and res.app_reservation_id:
            if funding_source == "subscription":
                await ctx.subscription_limiter.commit_reserved_spend(
                    reservation_id=res.app_reservation_id, spent_usd=float(app_spend_usd),
                    project_budget=ctx.budget_limiter,
                )
            else:
                await ctx.budget_limiter.commit_reserved_spend(
                    reservation_id=res.app_reservation_id, spent_usd=float(app_spend_usd),
                )
            res.app_reservation_active = False
        else:
            await ctx.budget_limiter.force_project_spend(
                spent_usd=float(app_spend_usd), bundle_id=ctx.bundle_id, provider=None,
                request_id=ctx.scope_id, user_id=ctx.user_id,
                note=("settle: subscription_cost; no_reservation" if funding_source == "subscription" else "settle: plan_cost"),
            )

    for spend_usd, note in extra_project_items:
        if spend_usd <= 0:
            continue
        await ctx.budget_limiter.force_project_spend(
            spent_usd=float(spend_usd), bundle_id=ctx.bundle_id, provider=None,
            request_id=ctx.scope_id, user_id=ctx.user_id, note=note,
        )

    await _commit_rl(ctx, res, tokens=int(plan_quota_commit_tokens))

    out.primary_funding_usd = float(plan_covered_usd)
    out.wallet_usd = float(alloc.wallet_usd)
    out.project_absorption_usd = float(project_absorption_usd)
    out.quota_commit_tokens = int(plan_quota_commit_tokens)
    out.extra_project_items = extra_project_items
    return out


async def _commit_rl(ctx: FundingContext, res: PlanFundingReservation, *, tokens: int) -> None:
    try:
        await ctx.rl.commit_with_reservation(
            bundle_id=ctx.rl_bundle_id, subject_id=ctx.subject_id,
            tokens=int(tokens), lock_id=ctx.scope_id,
            reservation_id=res.plan_reservation_id if res.plan_reservation_active else None,
            now=ctx.now, inc_request=1,
        )
        res.plan_reservation_active = False
        res.plan_reservation_id = None
    except Exception as ex:
        ctx.log("rl.commit", "RL commit failed", "WARN", error=str(ex))


# ---------------------------------------------------------------------------
# Cleanup (error/abort paths)
# ---------------------------------------------------------------------------
async def release_plan_funding(ctx: FundingContext, res: PlanFundingReservation) -> None:
    """Release any still-held reservations (used on failure before settlement)."""
    try:
        if res.app_reservation_active and res.app_reservation_id:
            if res.funding_source == "subscription" and ctx.subscription_limiter is not None:
                await ctx.subscription_limiter.release_reservation(
                    reservation_id=res.app_reservation_id, project_budget=ctx.budget_limiter,
                )
            else:
                await ctx.budget_limiter.release_reservation(reservation_id=res.app_reservation_id)
            res.app_reservation_active = False
    except Exception as ex:
        ctx.log("cleanup", "failed to release primary reservation", "WARN", error=str(ex))
    try:
        if res.wallet_reservation_active and res.wallet_reservation_id:
            await ctx.cp_manager.user_credits_mgr.release_lifetime_token_reservation(
                tenant=ctx.tenant, project=ctx.project, user_id=ctx.user_id,
                reservation_id=str(res.wallet_reservation_id), reason="cleanup",
            )
            res.wallet_reservation_active = False
    except Exception as ex:
        ctx.log("cleanup", "failed to release wallet reservation", "WARN", error=str(ex))
    try:
        if res.plan_reservation_active and res.plan_reservation_id:
            await ctx.rl.release_token_reservation(
                bundle_id=ctx.rl_bundle_id, subject_id=ctx.subject_id,
                reservation_id=res.plan_reservation_id, now=ctx.now,
            )
            res.plan_reservation_active = False
    except Exception as ex:
        ctx.log("cleanup", "failed to release plan RL reservation", "WARN", error=str(ex))
    try:
        await ctx.rl.release(bundle_id=ctx.rl_bundle_id, subject_id=ctx.subject_id, lock_id=ctx.scope_id)
    except Exception:
        pass

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/sdk/infra/economics/enforcement.py
"""
Reusable economics enforcement for accountable flows outside the chat run() path.

This module provides:
- EconomicsSubject / EconomicsEstimate / FlowPolicy / EconomicsDecision   (contracts)
- RoleResolver        — thin reuse of EconomicsRoleResolver (paid/registered)
- EconomicsGuard      — async context manager: verify quota at flow start,
                        reserve funding, bind accounting, settle on exit
- economic_preflight  — verify-only helper (admit, no reservation, no settle)

It is intentionally built ALONGSIDE BaseEntrypointWithEconomics.run() and reuses
its runtime primitives (cp_manager, rl, budget_limiter, run_accounting, comm,
logger) through the owning entrypoint. run() is NOT modified.

Design: docs/economics/economic-enforcement-non-chat-v2-README.md
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from uuid import uuid4

from kdcube_ai_app.infra import accounting as acct
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import (
    QuotaPolicy,
    EconomicsLimitException,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
    AdmitResult,
    subject_id_of,
    GLOBAL_BUNDLE_ID,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
from kdcube_ai_app.apps.chat.sdk.infra.economics.events_resources import (
    MSG_NO_FUNDING,
    MSG_SUBSCRIPTION_EXHAUSTED,
    MSG_PROJECT_EXHAUSTED,
    MSG_DENIED_GENERIC,
)

SAFETY_MARGIN = 1.15
# Budget rows store integer cents; a hold below $0.01 rounds to 0 and reserve()
# raises ValueError. Below this we skip the monetary hold and settle actuals.
_MIN_RESERVE_USD = 1.0 / 100

# Marks that an economics scope (a top-level EconomicsGuard, or chat run() once it
# opts in) is actively reserving+settling on this logical task. Nested guards check
# THIS, not the raw accounting context: background-job workers bind an accounting
# context (turn_id=turn_<job_id>) without doing any economics settlement, so the
# accounting context alone must NOT be treated as a settling parent.
_ECON_SCOPE_ACTIVE: ContextVar[Optional[str]] = ContextVar(
    "kdcube_econ_scope_active", default=None
)


def active_econ_scope() -> Optional[str]:
    """Return the scope_id of the economics scope currently settling, if any."""
    return _ECON_SCOPE_ACTIVE.get()


# ----------------------------------------------------------------------------
# Contracts
# ----------------------------------------------------------------------------
@dataclass
class EconomicsSubject:
    """Who pays. role is the RESOLVED economics role, never a hardcoded default."""
    tenant: str
    project: str
    user_id: str
    user_type: str
    timezone: Optional[str] = None


@dataclass
class EconomicsEstimate:
    """
    Drives the reservation size. reservation_usd is the primary, per-operation
    lever — most non-chat flows (embeddings, reconciler) cost pennies.
    """
    reservation_usd: Optional[float] = None
    input_text: Optional[str] = None
    output_budget_tokens: Optional[int] = None
    # floor used only when reservation_usd is not given (token-estimate path)
    min_tokens: int = 500


@dataclass
class FlowPolicy:
    enforce_concurrency: bool = False   # chat-only; non-chat flows keep this False
    reservation_ttl_sec: int = 900      # configurable per flow
    lock_ttl_sec: int = 180             # configurable per flow
    emit_user_events: bool = False      # background flows log only, no UI delivery
    # When the plan lane can't cover but the user can pay, switch to a wallet-only
    # paid lane (re-admit paid policy + reserve wallet) instead of denying.
    # Off by default (non-chat denies); chat / run()-on-engine sets it True.
    allow_paid_lane_fallback: bool = False
    # Serialize the admit->reserve planning window per user with a distributed
    # Redis lock (mirrors run()'s quota_lock). Only meaningful for RESERVING
    # surfaces (full guard) — closes the read-remaining-quota -> reserve TOCTOU
    # between concurrent turns of the same user. Off by default; needs redis on
    # the entrypoint. preflight-only flows have no reserve window and ignore it.
    enforce_quota_lock: bool = False
    quota_lock_ttl_sec: int = 60        # lock key expiry (safety net if holder dies)
    quota_lock_wait_sec: float = 5.0    # max spin-wait before denying as "system busy"


@dataclass
class EconomicsDecision:
    """Outcome of pre-run economics, made explicit (== the inline econ_ctx)."""
    lane: str                           # "plan" | "paid" | "bypass"
    plan_id: str
    funding_source: str                 # subscription | project | wallet | none
    funding_available_usd: float
    est_turn_tokens: int
    est_turn_usd: float
    budget_bypass: bool
    nested: bool = False                # degraded to preflight inside a parent ctx
    scope_id: Optional[str] = None
    admit: Optional[AdmitResult] = None
    # plan-lane reservation metadata (the actual hold lives in funding_flow's
    # PlanFundingReservation, kept on the guard as _funding_res; these are the
    # decision-level mirror used for tracing/assertions).
    app_reservation_source: Optional[str] = None     # which limiter holds it
    app_reservation_active: bool = False             # primary money hold was taken
    wallet_reservation_id: Optional[str] = None       # lifetime-token hold
    wallet_reserved_tokens: int = 0
    extra: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Role resolution (thin reuse of EconomicsRoleResolver)
# ----------------------------------------------------------------------------
class RoleResolver:
    """
    Re-derive paid/registered from economics state for detached flows.

    privileged/admin CANNOT be derived from economics state — it comes from the
    authenticated session and must be carried across the enqueue->worker boundary.
    Pass carried_role to preserve it.
    """

    def __init__(self, *, pg_pool, tenant: str, project: str):
        from kdcube_ai_app.apps.middleware.economics_role import EconomicsRoleResolver
        self._resolver = EconomicsRoleResolver(pg_pool=pg_pool, tenant=tenant, project=project)

    @staticmethod
    def _is_privileged(role: Optional[str]) -> bool:
        return str(role or "").strip().lower() in ("privileged", "admin")

    async def resolve(self, *, user_id: str, carried_role: Optional[str] = None) -> str:
        # Preserve privileged/admin exactly (not derivable from economics).
        if self._is_privileged(carried_role):
            return str(carried_role).strip().lower()
        resolved = await self._resolver.resolve_role_for_user_id(user_id)
        if resolved is None:
            return str(carried_role or "registered").strip().lower()
        # UserType -> plain string ("paid"/"registered")
        return str(getattr(resolved, "value", resolved)).strip().lower()


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------
def _usd_per_token() -> float:
    from kdcube_ai_app.infra.accounting.usage import (
        llm_output_price_usd_per_token,
        anthropic,
        sonnet_45,
    )
    return float(llm_output_price_usd_per_token(ref_provider=anthropic, ref_model=sonnet_45))


def _estimate_tokens(estimate: EconomicsEstimate, usd_per_token: float) -> int:
    if estimate.reservation_usd is not None and estimate.reservation_usd > 0:
        denom = max(usd_per_token * SAFETY_MARGIN, 1e-9)
        return max(int(estimate.min_tokens), int(math.ceil(estimate.reservation_usd / denom)))
    in_toks = 0
    if estimate.input_text:
        try:
            from kdcube_ai_app.apps.chat.sdk.util import token_count
            in_toks = int(token_count(estimate.input_text))
        except Exception:
            in_toks = max(1, int(len(estimate.input_text) / 4))
    out_toks = int(estimate.output_budget_tokens or 0)
    return max(int(estimate.min_tokens), in_toks + out_toks)


class EconomicsGuard:
    """
    Async context manager enforcing economics for a single accountable flow.

    Usage:
        async with EconomicsGuard(entrypoint, subject=subj, scope_id=sid,
                                  flow="memory.reconciler",
                                  estimate=EconomicsEstimate(reservation_usd=0.05)) as econ:
            result = await do_the_work()

    __aenter__:  estimate -> resolve plan/funding -> admit (verify quota at start)
                 -> reserve funding -> bind accounting. Raises EconomicsLimitException
                 if the flow is not economically feasible.
    __aexit__:   run_accounting(scope_id) -> settle (commit/release + shortfall).

    If an accountable accounting context is already bound, the guard degrades to
    preflight-only (verify, no reserve, no settle) to avoid double charging.
    """

    def __init__(
        self,
        entrypoint: Any,
        *,
        subject: EconomicsSubject,
        scope_id: str,
        flow: str,
        estimate: EconomicsEstimate,
        policy: Optional[FlowPolicy] = None,
        comm: Any = None,
    ):
        self.ep = entrypoint
        self.subject = subject
        self.scope_id = str(scope_id)
        self.flow = str(flow)
        self.estimate = estimate
        self.policy = policy or FlowPolicy()
        self.comm = comm if comm is not None else getattr(entrypoint, "comm", None)

        self.logger = getattr(entrypoint, "logger", None)
        self.cp = getattr(entrypoint, "cp_manager", None)
        self.rl = getattr(entrypoint, "rl", None)
        self.budget_limiter = getattr(entrypoint, "budget_limiter", None)
        self.redis = getattr(entrypoint, "redis", None)
        self.bundle_id = str(getattr(getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None), "id", "") or "")

        self.subj = subject_id_of(subject.tenant, subject.project, subject.user_id)
        self.usd_per_token = _usd_per_token()
        self.now = datetime.utcnow().replace(tzinfo=timezone.utc)

        self.decision: Optional[EconomicsDecision] = None
        self._acct_cm = None
        self._econ_token = None  # ContextVar token for the economics-scope marker
        self._subscription_limiter = None  # SubscriptionBudgetLimiter when applicable
        self._funding_ctx_obj = None       # funding_flow.FundingContext (plan lane)
        self._funding_res = None           # funding_flow.PlanFundingReservation (plan lane)
        # quota-lock state (distributed admit->reserve serialization)
        self._quota_lock_key: Optional[str] = None
        self._quota_lock_token: Optional[str] = None
        self._quota_lock_acquired: bool = False

    # -- logging / events -----------------------------------------------------
    def _log(self, stage: str, msg: str, level: str = "INFO", **kv):
        if self.logger is None:
            return
        try:
            self.logger.log(f"[econ-guard:{self.flow}] {stage} | {msg} | {kv}", level)
        except Exception:
            pass

    async def _emit_denial(self, *, code: str, title: str, data: dict) -> None:
        if not (self.policy.emit_user_events and self.comm is not None):
            return
        try:
            await self.comm.service_event(
                type="rate_limit.denied",
                step="rate_limit",
                status="error",
                title=title,
                agent=f"econ.{self.flow}",
                data={**data, "code": code, "show_in_timeline": False},
            )
        except Exception as e:
            self._log("telemetry", "failed to emit denial event", "WARN", error=str(e))

    async def _deny(self, *, code: str, title: str, message: str, user_message: str, data: dict) -> None:
        payload = {
            **data,
            "code": code,
            "flow": self.flow,
            "scope_id": self.scope_id,
            "subject_id": self.subj,
            "user_type": self.subject.user_type,
            "user_message": user_message,
            "notification_type": "error",
        }
        self._log("deny", message, "WARN", code=code)
        await self._emit_denial(code=code, title=title, data=payload)
        raise EconomicsLimitException(message, code=code, data=payload)

    # -- quota lock (distributed admit->reserve serialization) ----------------
    async def _quota_lock_try_acquire(self, key: str, token: str, ttl_sec: int) -> bool:
        r = self.redis
        if r is None:
            return False
        try:
            return bool(await r.set(key, token, nx=True, ex=ttl_sec))
        except TypeError:
            pass
        except Exception:
            return False
        try:
            return bool(await r.set(key, token, nx=True, px=int(ttl_sec * 1000)))
        except Exception:
            return False

    async def _quota_lock_release_key(self, key: str, token: str) -> None:
        r = self.redis
        if r is None:
            return
        # compare-and-del: only release the lock if WE still hold it (token match),
        # so a release after our TTL expired can't drop a successor's lock.
        lua = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            await r.eval(lua, 1, key, token)
            return
        except TypeError:
            try:
                await r.eval(lua, keys=[key], args=[token])
                return
            except Exception:
                pass
        except Exception:
            pass
        try:
            cur = await r.get(key)
            if cur is None:
                return
            if isinstance(cur, (bytes, bytearray)):
                cur = cur.decode("utf-8", errors="ignore")
            if str(cur) == str(token):
                await r.delete(key)
        except Exception:
            pass

    async def _acquire_quota_lock_or_deny(self, *, scope: str, budget_bypass: bool) -> None:
        # Only reserving surfaces opt in; admin bypass has no shared-pool reserve
        # window to serialize; without redis we degrade to no lock (log a warning).
        if not self.policy.enforce_quota_lock or budget_bypass:
            return
        if self.redis is None:
            self._log("quota_lock", "redis unavailable; quota_lock disabled", "WARN")
            return
        s = self.subject
        self._quota_lock_key = f"quota_lock:{s.tenant}:{s.project}:{s.user_id}:{scope}:{GLOBAL_BUNDLE_ID}"
        self._quota_lock_token = secrets.token_hex(16)
        ttl_sec = int(self.policy.quota_lock_ttl_sec)
        wait_total = float(self.policy.quota_lock_wait_sec)
        t0 = asyncio.get_event_loop().time()
        sleep = 0.05
        while True:
            if await self._quota_lock_try_acquire(self._quota_lock_key, self._quota_lock_token, ttl_sec=ttl_sec):
                self._quota_lock_acquired = True
                self._log("quota_lock", "acquired", key=self._quota_lock_key, scope=scope, ttl_sec=ttl_sec)
                return
            if (asyncio.get_event_loop().time() - t0) >= wait_total:
                self._log("quota_lock", "failed to acquire within wait window", "WARN",
                          key=self._quota_lock_key, scope=scope)
                await self._deny(
                    code="quota_lock_timeout",
                    title="System busy",
                    message=f"{self.flow}: quota_lock contended; concurrent planning in progress",
                    user_message="Too many concurrent requests are planning quotas right now. Please retry.",
                    data={"reason": "quota_lock_timeout", "lane": "deny", "scope": scope},
                )
            await asyncio.sleep(sleep)
            sleep = min(sleep * 1.5, 0.25)

    async def _release_quota_lock_if_held(self) -> None:
        if self._quota_lock_acquired and self._quota_lock_key and self._quota_lock_token:
            await self._quota_lock_release_key(self._quota_lock_key, self._quota_lock_token)
            self._log("quota_lock", "released", key=self._quota_lock_key)
        self._quota_lock_key = None
        self._quota_lock_token = None
        self._quota_lock_acquired = False

    # -- pre-run --------------------------------------------------------------
    async def __aenter__(self) -> EconomicsDecision:
        parent_scope = active_econ_scope()
        if parent_scope:
            # Already inside a settling economics scope (guard-in-guard): verify
            # only, the parent settles. NOTE: a bare accounting context (e.g. a
            # background-job worker binding turn_id) does NOT count here.
            self._log("nested", "degrading to preflight; parent settles", parent_scope=parent_scope)
            self.decision = await self._preflight(nested=True)
            return self.decision

        self.decision = await self._enter_top_level()
        return self.decision

    async def _resolve_plan_and_funding(self) -> dict:
        s = self.subject
        role = str(s.user_type or "anonymous").strip().lower()
        budget_bypass = role in ("privileged", "admin")

        est_turn_tokens = _estimate_tokens(self.estimate, self.usd_per_token)
        if self.estimate.reservation_usd and self.estimate.reservation_usd > 0:
            est_turn_usd = float(self.estimate.reservation_usd)
        else:
            est_turn_usd = float(est_turn_tokens) * self.usd_per_token * SAFETY_MARGIN

        plan_balance = await self.cp.get_user_plan_balance(
            tenant=s.tenant, project=s.project, user_id=s.user_id
        )
        wallet_tokens = 0
        if plan_balance and plan_balance.has_lifetime_budget():
            bal = await self.cp.user_credits_mgr.get_lifetime_balance(
                tenant=s.tenant, project=s.project, user_id=s.user_id
            )
            wallet_tokens = int(bal or 0)
        has_wallet = wallet_tokens > 0

        subscription = await self.cp.subscription_mgr.get_subscription(
            tenant=s.tenant, project=s.project, user_id=s.user_id
        )
        sub_due_at = getattr(subscription, "next_charge_at", None) if subscription else None
        sub_chargeable = bool(subscription and int(getattr(subscription, "monthly_price_cents", 0) or 0) > 0)
        sub_past_due = bool(sub_due_at and sub_due_at <= self.now)
        has_active_subscription = bool(
            subscription
            and getattr(subscription, "status", None) == "active"
            and sub_chargeable
            and not sub_past_due
        )

        # plan id (mirror run())
        if budget_bypass:
            plan_id = "admin"
        elif role == "anonymous":
            plan_id = "anonymous"
        elif has_active_subscription:
            plan_id = getattr(subscription, "plan_id", None) or "payasyougo"
        else:
            plan_id = "free"

        base_policy = await self.cp.get_plan_quota_policy(
            tenant=s.tenant, project=s.project, plan_id=plan_id
        )
        if not base_policy:
            base_policy = QuotaPolicy(max_concurrent=None)
        # Concurrency is chat-only: drop the concurrency dimension for non-chat flows.
        if not self.policy.enforce_concurrency:
            base_policy = dataclasses.replace(base_policy, max_concurrent=None)

        # funding source selection — mirror run()'s plan-funding eligibility.
        # NOTE: aligned with the economics fix that replaced the static
        # project_budget_user_types()={"registered"} with project_budget_allowed_for_plan(...).
        # Project budget backs the plan lane for ANY non-anonymous user (not just
        # "registered"), unless an active subscription pays or the user is wallet-only.
        plan_source = "subscription" if has_active_subscription else "role"
        resolver = getattr(self.ep, "project_budget_allowed_for_plan", None)
        if callable(resolver):
            project_budget_allowed = bool(resolver(
                user_type=role,
                plan_id=plan_id,
                plan_source=plan_source,
                has_wallet=has_wallet,
                has_active_subscription=has_active_subscription,
            ))
        else:
            # Fallback mirrors BaseEntrypointWithEconomics.project_budget_allowed_for_plan.
            wallet_first = getattr(self.ep, "wallet_users_use_project_budget_first", lambda: True)()
            project_budget_allowed = (
                not has_active_subscription
                and not (has_wallet and not wallet_first)
                and role != "anonymous"
            )

        return {
            "role": role,
            "budget_bypass": budget_bypass,
            "est_turn_tokens": est_turn_tokens,
            "est_turn_usd": est_turn_usd,
            "plan_balance": plan_balance,
            "wallet_tokens": wallet_tokens,
            "has_wallet": has_wallet,
            "subscription": subscription,
            "has_active_subscription": has_active_subscription,
            "plan_id": plan_id,
            "base_policy": base_policy,
            "project_budget_allowed": project_budget_allowed,
        }

    async def _admit(self, base_policy: QuotaPolicy, *, reserve_tokens: int = 0) -> AdmitResult:
        return await self.rl.admit(
            bundle_id=GLOBAL_BUNDLE_ID,
            subject_id=self.subj,
            policy=base_policy,
            lock_id=self.scope_id,
            lock_ttl_sec=int(self.policy.lock_ttl_sec),
            apply_plan_override=True,
            now=self.now,
            reserve_tokens=int(reserve_tokens or 0),
            reservation_id=self.scope_id,
            reservation_ttl_sec=int(self.policy.reservation_ttl_sec),
        )

    def _funding_ctx(self, subscription_limiter=None):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import FundingContext
        s = self.subject
        return FundingContext(
            rl=self.rl, budget_limiter=self.budget_limiter, cp_manager=self.cp,
            tenant=s.tenant, project=s.project, user_id=s.user_id, subject_id=self.subj,
            bundle_id=self.bundle_id, rl_bundle_id=GLOBAL_BUNDLE_ID, scope_id=self.scope_id,
            usd_per_token=self.usd_per_token, now=self.now,
            subscription_limiter=subscription_limiter,
            log=lambda stage, msg, level="INFO", **kv: self._log(stage, msg, level, **kv),
        )

    async def _preflight(self, *, nested: bool) -> EconomicsDecision:
        """Verify-only: resolve + admit, no reservation, no accounting binding."""
        r = await self._resolve_plan_and_funding()
        admit = await self._admit(r["base_policy"])
        if not admit.allowed and not r["budget_bypass"]:
            await self._deny(
                code="rate_limited",
                title="Rate limit exceeded",
                message=f"{self.flow}: rate limited: {admit.reason or 'unknown'}",
                user_message=MSG_DENIED_GENERIC,
                data={"reason": admit.reason, "snapshot": admit.snapshot, "lane": "deny"},
            )
        funding_source, available_usd = self._funding_summary(r)
        if not nested and not r["budget_bypass"] and funding_source == "none":
            await self._deny(
                code="no_funding_source",
                title="No funding source",
                message=f"{self.flow}: no funding source for user",
                user_message=MSG_NO_FUNDING,
                data={"reason": "no_funding_source", "funding_source": "none"},
            )
        return EconomicsDecision(
            lane="bypass" if r["budget_bypass"] else ("paid" if funding_source == "wallet" else "plan"),
            plan_id=r["plan_id"],
            funding_source=funding_source,
            funding_available_usd=available_usd,
            est_turn_tokens=int(r["est_turn_tokens"]),
            est_turn_usd=float(r["est_turn_usd"]),
            budget_bypass=r["budget_bypass"],
            nested=nested,
            scope_id=self.scope_id,
            admit=admit,
        )

    def _funding_summary(self, r: dict) -> Tuple[str, float]:
        if r["budget_bypass"]:
            return "project", float("inf")
        if r["has_active_subscription"]:
            return "subscription", float("inf")  # availability checked at reserve time
        if r["project_budget_allowed"]:
            return "project", float("inf")
        if r["has_wallet"]:
            return "wallet", float(r["wallet_tokens"]) * self.usd_per_token
        return "none", 0.0

    async def _enter_top_level(self) -> EconomicsDecision:
        r = await self._resolve_plan_and_funding()
        est_turn_tokens = int(r["est_turn_tokens"])
        budget_bypass = r["budget_bypass"]
        funding_source, available_usd = self._funding_summary(r)

        # Serialize the admit->reserve planning window per user (reserving surfaces
        # only; no-op unless policy.enforce_quota_lock + redis). Acquired BEFORE
        # admit (which reserves RL tokens) and released as soon as all holds are
        # taken (or on any deny) — never held across the LLM work. Mirrors run().
        _, quota_scope = r["base_policy"].effective_allowed_tokens()
        await self._acquire_quota_lock_or_deny(scope=str(quota_scope or "month"), budget_bypass=budget_bypass)
        try:
            # 1) verify quota AND reserve RL plan tokens at the start (full run() parity)
            admit = await self._admit(r["base_policy"], reserve_tokens=est_turn_tokens)

            est_turn_usd = float(r["est_turn_usd"])
            decision = EconomicsDecision(
                lane="bypass" if budget_bypass else ("paid" if funding_source == "wallet" else "plan"),
                plan_id=r["plan_id"],
                funding_source=funding_source,
                funding_available_usd=available_usd,
                est_turn_tokens=est_turn_tokens,
                est_turn_usd=est_turn_usd,
                budget_bypass=budget_bypass,
                nested=False,
                scope_id=self.scope_id,
                admit=admit,
                extra={
                    "effective_policy": r["base_policy"],
                    "plan_balance": r["plan_balance"],
                    "wallet_tokens": int(r["wallet_tokens"]),
                },
            )

            if not admit.allowed and not budget_bypass:
                # Plan policy rate-limited. If the flow allows a paid-lane fallback
                # and the user has a wallet to back it, switch to the paid lane
                # instead of denying (run() parity: admit-denied -> paid for payers).
                # Otherwise deny. _switch_to_paid raises on its own failures.
                if self.policy.allow_paid_lane_fallback and r["has_wallet"]:
                    await self._switch_to_paid(decision, r, reason="plan_admit_rate_limited")
                else:
                    await self._deny(
                        code="rate_limited",
                        title="Rate limit exceeded",
                        message=f"{self.flow}: rate limited: {admit.reason or 'unknown'}",
                        user_message=MSG_DENIED_GENERIC,
                        data={"reason": admit.reason, "snapshot": admit.snapshot, "lane": "deny"},
                    )
            else:
                if funding_source == "none" and not budget_bypass:
                    await self._deny(
                        code="no_funding_source",
                        title="No funding source",
                        message=f"{self.flow}: no funding source for user",
                        user_message=MSG_NO_FUNDING,
                        data={"reason": "no_funding_source", "funding_source": "none"},
                    )

                # 2) reserve funding via the shared plan-lane flow (primary + wallet overflow)
                from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import ReserveStatus
                try:
                    if funding_source in ("project", "subscription") or budget_bypass:
                        outcome = await self._reserve_plan_lane(decision, r, admit=admit, funding_source=funding_source)
                        if outcome.status is ReserveStatus.DENIED:
                            await self._deny(
                                code=outcome.deny_code or "no_funding_source",
                                title="Insufficient funds",
                                message=outcome.deny_message or f"{self.flow}: cannot fund request",
                                user_message=MSG_NO_FUNDING,
                                data=outcome.deny_data or {"reason": "no_funding_source"},
                            )
                        elif outcome.status is ReserveStatus.SWITCH_TO_PAID:
                            await self._switch_to_paid(decision, r, reason=outcome.switch_reason or "plan_tokens_exhausted_for_turn")
                        # OK -> _reserve_plan_lane stored _funding_res
                    elif funding_source == "wallet":
                        # wallet-primary edge (anonymous + wallet) — not a plan lane
                        await self._reserve_wallet_or_deny(decision, r, reserve_usd=est_turn_usd, exhausted=None)
                except EconomicsLimitException as exc:
                    await self._on_funding_denied(exc)
                    raise
        finally:
            await self._release_quota_lock_if_held()

        # 3) bind accounting (Variant A) + mark active economics scope
        self._bind_accounting()
        self._econ_token = _ECON_SCOPE_ACTIVE.set(self.scope_id)
        self._log(
            "enter", "economics ok",
            plan_id=decision.plan_id, funding_source=decision.funding_source, lane=decision.lane,
            est_turn_usd=round(est_turn_usd, 6),
        )
        return decision

    async def _reserve_plan_lane(self, decision: EconomicsDecision, r: dict, *, admit: AdmitResult, funding_source: str):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import reserve_plan_funding, ReserveStatus

        est_turn_tokens = int(decision.est_turn_tokens)
        budget_bypass = decision.budget_bypass

        sub_limiter = None
        sub_available = 0.0
        project_snapshot = None
        if funding_source == "subscription":
            sub_limiter = await self._get_subscription_limiter(r["subscription"])
            if sub_limiter is None:
                await self._deny(
                    code="subscription_unavailable", title="Subscription unavailable",
                    message=f"{self.flow}: subscription budget unavailable",
                    user_message=MSG_SUBSCRIPTION_EXHAUSTED,
                    data={"reason": "subscription_unavailable", "funding_source": "subscription"},
                )
            try:
                snap = await sub_limiter.get_subscription_budget_balance()
                sub_available = float(snap.get("available_usd") or 0.0)
            except Exception:
                sub_available = 0.0
        if funding_source == "project":
            try:
                project_snapshot = await self.budget_limiter.get_app_budget_balance()
            except Exception:
                project_snapshot = None

        wallet_can_pay = int(r["wallet_tokens"]) >= est_turn_tokens
        sub_can_pay = funding_source == "subscription" and sub_available >= float(decision.est_turn_usd)
        personal_can_pay = bool(wallet_can_pay or sub_can_pay)

        ctx = self._funding_ctx(sub_limiter)
        outcome = await reserve_plan_funding(
            ctx, admit=admit,
            funding_source=("project" if budget_bypass else funding_source),
            budget_bypass=budget_bypass, est_turn_tokens=est_turn_tokens,
            has_wallet=bool(r["has_wallet"]), subscription_available_usd=sub_available,
            project_budget_snapshot=project_snapshot, personal_can_pay_turn=personal_can_pay,
            allow_paid_lane_fallback=bool(self.policy.allow_paid_lane_fallback),
            ttl_sec=int(self.policy.reservation_ttl_sec),
        )
        if outcome.status is ReserveStatus.OK:
            res = outcome.reservation
            self._funding_ctx_obj = ctx
            self._funding_res = res
            decision.app_reservation_source = res.funding_source
            decision.app_reservation_active = res.app_reservation_active
        return outcome

    def _paid_policy(self, r: dict) -> QuotaPolicy:
        """Policy for the paid (wallet-only) lane — payasyougo service limits."""
        pols = getattr(self.ep, "app_quota_policies", None) or {}
        return pols.get("payasyougo") or r.get("base_policy")

    async def _switch_to_paid(self, decision: EconomicsDecision, r: dict, *, reason: str) -> None:
        """Release the plan RL token reservation, re-admit against the paid policy,
        and reserve wallet as the primary funding (wallet-only paid lane). Triggered
        either by plan-admit rate-limit or by plan-funding exhaustion (ReserveStatus
        .SWITCH_TO_PAID). Mirrors run()'s _switch_plan_to_paid_or_die money flow; the
        switch is logged only (no rate_limit.lane_switch SSE event is emitted here —
        UX events stay in run())."""
        # release the plan-lane RL token reservation + lock taken by the plan admit
        try:
            await self.rl.release_token_reservation(
                bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj,
                reservation_id=self.scope_id, now=self.now,
            )
            await self.rl.release(bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj, lock_id=self.scope_id)
        except Exception:
            pass
        # the plan-lane money flow took no hold before signalling a switch
        self._funding_res = None
        self._funding_ctx_obj = None

        paid_policy = self._paid_policy(r)
        paid_admit = await self._admit(paid_policy, reserve_tokens=int(decision.est_turn_tokens))
        if not paid_admit.allowed:
            await self._deny(
                code="paid_admit_denied_after_switch",
                title="Rate limit exceeded",
                message=f"{self.flow}: paid lane admit denied after switch: {paid_admit.reason or 'unknown'}",
                user_message=MSG_DENIED_GENERIC,
                data={"reason": paid_admit.reason, "snapshot": paid_admit.snapshot,
                      "lane": "deny", "switch_reason": reason},
            )
        decision.admit = paid_admit
        if isinstance(decision.extra, dict):
            decision.extra["effective_policy"] = paid_policy
        self._log("lane_switch", "switched plan -> paid", reason=reason)
        # reserve wallet as primary (existing wallet-primary path; sets lane=paid)
        await self._reserve_wallet_or_deny(
            decision, r, reserve_usd=float(decision.est_turn_usd), exhausted=None,
        )

    async def _on_funding_denied(self, exc: Exception) -> None:
        """Log + optionally emit a denial, then release the RL token reservation
        taken by admit (the funding flow releases its own money holds before raising)."""
        code = getattr(exc, "code", "denied")
        self._log("deny", str(exc), "WARN", code=code)
        await self._emit_denial(
            code=code, title="Insufficient funds",
            data={**(getattr(exc, "data", {}) or {}), "flow": self.flow,
                  "scope_id": self.scope_id, "subject_id": self.subj},
        )
        try:
            if self._funding_res is not None and self._funding_ctx_obj is not None:
                from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import release_plan_funding
                await release_plan_funding(self._funding_ctx_obj, self._funding_res)
            else:
                await self.rl.release_token_reservation(
                    bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj,
                    reservation_id=self.scope_id, now=self.now,
                )
                await self.rl.release(bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj, lock_id=self.scope_id)
        except Exception:
            pass

    async def _reserve_wallet_or_deny(self, decision: EconomicsDecision, r: dict, *, reserve_usd: float, exhausted: Optional[str]) -> None:
        s = self.subject
        if not r["has_wallet"]:
            if exhausted == "subscription":
                await self._deny(
                    code="subscription_budget_exhausted",
                    title="Subscription balance exhausted",
                    message=f"{self.flow}: subscription exhausted, no wallet",
                    user_message=MSG_SUBSCRIPTION_EXHAUSTED,
                    data={"reason": "subscription_budget_exhausted", "funding_source": "subscription"},
                )
            if exhausted == "project":
                await self._deny(
                    code="project_budget_exhausted",
                    title="Project budget exhausted",
                    message=f"{self.flow}: project exhausted, no wallet",
                    user_message=MSG_PROJECT_EXHAUSTED,
                    data={"reason": "project_budget_exhausted", "funding_source": "project"},
                )
            await self._deny(
                code="no_funding_source",
                title="No funding source",
                message=f"{self.flow}: no funding source",
                user_message=MSG_NO_FUNDING,
                data={"reason": "no_funding_source", "funding_source": "none"},
            )

        tokens = int(decision.est_turn_tokens)
        ok = await self.cp.user_credits_mgr.reserve_lifetime_tokens(
            tenant=s.tenant, project=s.project, user_id=s.user_id,
            reservation_id=self.scope_id, tokens=tokens,
            ttl_sec=int(self.policy.reservation_ttl_sec), bundle_id=self.bundle_id,
            notes=f"{self.flow} wallet reserve: scope={self.scope_id}",
        )
        if not ok:
            await self._deny(
                code="personal_reservation_failed",
                title="Insufficient personal credits",
                message=f"{self.flow}: wallet reservation failed",
                user_message=MSG_NO_FUNDING,
                data={"reason": "personal_reservation_failed", "funding_source": "wallet"},
            )
        decision.funding_source = "wallet"
        decision.lane = "paid"
        decision.wallet_reservation_id = self.scope_id
        decision.wallet_reserved_tokens = tokens

    async def _get_subscription_limiter(self, subscription: Any):
        if self._subscription_limiter is not None:
            return self._subscription_limiter
        try:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
            from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
            s = self.subject
            period = build_subscription_period_descriptor(
                tenant=s.tenant, project=s.project, user_id=s.user_id,
                provider=getattr(subscription, "provider", "internal") or "internal",
                stripe_subscription_id=getattr(subscription, "stripe_subscription_id", None),
                period_end=getattr(subscription, "next_charge_at", None),
                period_start=getattr(subscription, "last_charged_at", None),
            )
            self._subscription_limiter = SubscriptionBudgetLimiter(
                pg_pool=self.ep.pg_pool, tenant=s.tenant, project=s.project,
                user_id=s.user_id, period_key=period["period_key"],
                period_start=period["period_start"], period_end=period["period_end"],
            )
        except Exception as e:
            self._log("subscription", "failed to build subscription limiter", "WARN", error=str(e))
            self._subscription_limiter = None
        return self._subscription_limiter

    # -- accounting binding (Variant A) --------------------------------------
    @staticmethod
    def _ensure_accounting_storage() -> None:
        """Mirror run(): ensure a file backend is present so model-service events
        are persisted and readable by settlement (works in detached workers)."""
        try:
            storage = acct._get_storage()
            if storage is None or storage.__class__.__name__ == "NoOpAccountingStorage":
                from kdcube_ai_app.apps.chat.sdk.config import get_settings
                from kdcube_ai_app.storage.storage import create_storage_backend
                acct.AccountingSystem.init_storage(
                    create_storage_backend(get_settings().STORAGE_PATH), enabled=True
                )
        except Exception:
            pass

    def _bind_accounting(self) -> None:
        s = self.subject
        self._ensure_accounting_storage()
        self._acct_cm = acct.with_accounting(
            self.flow,
            user_id=s.user_id,
            user_type=s.user_type,
            tenant_id=s.tenant,
            project_id=s.project,
            request_id=self.scope_id,
            app_bundle_id=self.bundle_id,
            timezone=s.timezone,
            conversation_id=self.scope_id,   # synthetic; gives the file fallback an id prefix
            turn_id=self.scope_id,
            metadata={
                "flow": self.flow,
                "scope_id": self.scope_id,
                "conversation_id": self.scope_id,
                "turn_id": self.scope_id,
                "bundle_id": self.bundle_id,
            },
        )
        self._acct_cm.__enter__()

    def _unbind_accounting(self) -> None:
        if self._acct_cm is not None:
            try:
                self._acct_cm.__exit__(None, None, None)
            finally:
                self._acct_cm = None

    # -- post-run -------------------------------------------------------------
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        d = self.decision
        if d is None or d.nested:
            return False  # nested: parent settles; nothing bound here

        try:
            ranked_tokens, cost_result = await self._run_accounting()
            total_cost = float((cost_result or {}).get("cost_total_usd") or 0.0)
            await self._settle(d, ranked_tokens=int(ranked_tokens or 0), total_cost=total_cost)
            self._log(
                "settle", "settled",
                funding_source=d.funding_source,
                ranked_tokens=int(ranked_tokens or 0),
                total_cost=round(total_cost, 6),
            )
        except Exception as e:
            self._log("settle", "settlement failed; releasing holds", "ERROR", error=str(e))
            await self._cleanup_release(d)
        finally:
            if self._econ_token is not None:
                try:
                    _ECON_SCOPE_ACTIVE.reset(self._econ_token)
                finally:
                    self._econ_token = None
            self._unbind_accounting()
            await self._clear_turn_events()
        return False  # never suppress the body's exception

    async def _run_accounting(self) -> Tuple[int, dict]:
        usage_from = self.now.date().isoformat()
        ranked, result = await self.ep.run_accounting(
            tenant=self.subject.tenant,
            project=self.subject.project,
            user_id=self.subject.user_id,
            user_type=self.subject.user_type,
            thread_id=self.scope_id,
            turn_id=self.scope_id,
            usage_from=usage_from,
        )
        return int(ranked or 0), (result or {})

    async def _settle(self, d: EconomicsDecision, *, ranked_tokens: int, total_cost: float) -> None:
        # Plan lane (project/subscription/bypass) -> shared funding_flow settlement
        # (primary + wallet overflow + project absorption, via allocate_plan_wallet_settlement).
        if self._funding_res is not None and self._funding_ctx_obj is not None:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import settle_plan_funding
            plan_balance = d.extra.get("plan_balance")
            wallet_tokens = int(d.extra.get("wallet_tokens") or 0)
            await settle_plan_funding(
                self._funding_ctx_obj, self._funding_res,
                ranked_tokens=int(ranked_tokens), total_cost_usd=float(total_cost),
                effective_policy=d.extra.get("effective_policy"),
                plan_has_lifetime_budget=bool(plan_balance and plan_balance.has_lifetime_budget()),
                user_budget_tokens=(wallet_tokens or None),
            )
            return
        # wallet-primary edge (no plan-lane reservation taken)
        await self._settle_wallet(d, ranked_tokens=ranked_tokens, total_cost=total_cost)

    async def _settle_wallet(self, d: EconomicsDecision, *, ranked_tokens: int, total_cost: float) -> None:
        s = self.subject
        try:
            await self.rl.commit_with_reservation(
                bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj,
                tokens=int(ranked_tokens), lock_id=self.scope_id,
                reservation_id=self.scope_id, now=self.now, inc_request=1,
            )
        except Exception as e:
            self._log("rl.commit", "failed to commit rl usage", "WARN", error=str(e))
        if d.wallet_reservation_id:
            if int(ranked_tokens) <= 0:
                # Nothing consumed (e.g. free-tier provider): commit_reserved_lifetime_tokens
                # no-ops on tokens<=0 and would leave the hold 'reserved' until TTL —
                # release it so the wallet balance frees immediately.
                await self.cp.user_credits_mgr.release_lifetime_token_reservation(
                    tenant=s.tenant, project=s.project, user_id=s.user_id,
                    reservation_id=d.wallet_reservation_id, reason=f"{self.flow}: zero actual cost",
                )
                return
            uncovered = await self.cp.user_credits_mgr.commit_reserved_lifetime_tokens(
                tenant=s.tenant, project=s.project, user_id=s.user_id,
                reservation_id=d.wallet_reservation_id, tokens=int(ranked_tokens),
            )
            uncovered = int(uncovered or 0)
            if uncovered > 0 and total_cost > 0 and ranked_tokens > 0:
                from kdcube_ai_app.apps.chat.sdk.util import safe_frac
                shortfall = float(total_cost) * safe_frac(float(uncovered), float(ranked_tokens))
                if shortfall > 0:
                    await self.budget_limiter.force_project_spend(
                        spent_usd=shortfall, bundle_id=self.bundle_id, provider=None,
                        request_id=self.scope_id, user_id=s.user_id, note=f"{self.flow}: shortfall:wallet_paid",
                    )

    async def _cleanup_release(self, d: EconomicsDecision) -> None:
        if self._funding_res is not None and self._funding_ctx_obj is not None:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import release_plan_funding
            await release_plan_funding(self._funding_ctx_obj, self._funding_res)
            return
        s = self.subject
        try:
            if d.wallet_reservation_id:
                await self.cp.user_credits_mgr.release_lifetime_token_reservation(
                    tenant=s.tenant, project=s.project, user_id=s.user_id,
                    reservation_id=d.wallet_reservation_id, reason=f"{self.flow}: cleanup",
                )
        except Exception as e:
            self._log("cleanup", "failed to release wallet reservation", "WARN", error=str(e))
        try:
            await self.rl.release_token_reservation(
                bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj, reservation_id=self.scope_id, now=self.now,
            )
            await self.rl.release(bundle_id=GLOBAL_BUNDLE_ID, subject_id=self.subj, lock_id=self.scope_id)
        except Exception:
            pass

    async def _clear_turn_events(self) -> None:
        try:
            await acct.clear_turn_events(
                tenant=self.subject.tenant, project=self.subject.project,
                conversation_id=self.scope_id, turn_id=self.scope_id,
            )
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Verify-only helper
# ----------------------------------------------------------------------------
async def economic_preflight(
    entrypoint: Any,
    *,
    subject: EconomicsSubject,
    estimate: EconomicsEstimate,
    flow: str,
    policy: Optional[FlowPolicy] = None,
) -> EconomicsDecision:
    """
    Verify the flow is economically feasible without reserving or settling.
    Raises EconomicsLimitException if not. Use for nested or cheap flows where
    the caller degrades gracefully (e.g. memory search -> BM25).
    """
    guard = EconomicsGuard(
        entrypoint, subject=subject, scope_id=f"preflight_{uuid4().hex}",
        flow=flow, estimate=estimate, policy=policy,
    )
    return await guard._preflight(nested=bool(active_econ_scope()))

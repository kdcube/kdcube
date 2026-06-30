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

import dataclasses
import json
import logging
import math
from contextlib import contextmanager
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
from kdcube_ai_app.apps.chat.sdk.infra.economics.plan_resolution import (
    resolve_plan_id,
    subscription_is_active,
    subscription_provides_plan_budget,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.quota_lock import QuotaLock, quota_lock_key
from kdcube_ai_app.apps.chat.sdk.infra.economics.events_resources import (
    MSG_NO_FUNDING,
    MSG_SUBSCRIPTION_EXHAUSTED,
    MSG_PROJECT_EXHAUSTED,
    MSG_DENIED_GENERIC,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
    project_execution_authority,
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
_TRACE_LOGGER = logging.getLogger(__name__)
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def active_econ_scope() -> Optional[str]:
    """Return the scope_id of the economics scope currently settling, if any."""
    return _ECON_SCOPE_ACTIVE.get()


@contextmanager
def bind_economics_scope(scope_id: str):
    """Mark the current logical execution as already settled by a parent flow."""
    token = _ECON_SCOPE_ACTIVE.set(str(scope_id or ""))
    try:
        yield _ECON_SCOPE_ACTIVE.get()
    finally:
        _ECON_SCOPE_ACTIVE.reset(token)


def _level_no(level: str) -> int:
    return _LOG_LEVELS.get(str(level or "INFO").upper(), logging.INFO)


# ----------------------------------------------------------------------------
# Contracts
# ----------------------------------------------------------------------------
@dataclass
class EconomicsSubject:
    """Who pays.

    ``user_id`` is the billing/quota subject. The subject carries explicit
    authority facts; it does not carry a compact ``user_type`` lane. That old
    lane is derived only at legacy accounting/API boundaries from:

    - ``roles`` / ``permissions``: proven authority claims for the subject;
    - ``budget_bypass``: explicit authority decision for admin/system bypass;
    - ``is_anonymous`` / missing user_id: anonymous execution;
    - subscription, wallet and plan rows: paid/project/wallet funding state.
    """
    tenant: str
    project: str
    user_id: str
    timezone: Optional[str] = None
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    budget_bypass: Optional[bool] = None
    is_anonymous: Optional[bool] = None
    provenance: dict[str, Any] = field(default_factory=dict)


def _subject_roles(subject: EconomicsSubject) -> set[str]:
    return {str(role or "").strip() for role in (subject.roles or ()) if str(role or "").strip()}


def _subject_is_anonymous(subject: EconomicsSubject) -> bool:
    if subject.is_anonymous is not None:
        return bool(subject.is_anonymous)
    user_id = str(subject.user_id or "").strip()
    return not user_id or user_id == "anonymous"


def _subject_budget_bypass(subject: EconomicsSubject) -> bool:
    if subject.budget_bypass is not None:
        return bool(subject.budget_bypass)
    return authority_has_platform_privilege(_subject_roles(subject))


def economics_subject_from_authority_context(
    *,
    tenant: str,
    project: str,
    identity_authority: dict[str, Any] | None = None,
    actor_user_id: str = "",
    fallback_user_id: str = "",
    fallback_roles: tuple[Any, ...] | list[Any] | set[Any] | None = None,
    fallback_permissions: tuple[Any, ...] | list[Any] | set[Any] | None = None,
    timezone: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> EconomicsSubject:
    """Build an economics subject from a Connection Hub authority envelope.

    Components should prefer this helper over interpreting platform identity,
    linked actors, or admin/bypass roles locally. Connection Hub owns that
    projection; economics only consumes the already-projected result.
    """

    projected = project_execution_authority(
        identity_authority,
        actor_user_id=actor_user_id,
        fallback_user_id=fallback_user_id,
        fallback_roles=fallback_roles,
        fallback_permissions=fallback_permissions,
    )
    subject_provenance = projected.to_provenance()
    if provenance:
        subject_provenance.update(provenance)
        subject_provenance.setdefault("identity_authority", dict(projected.source))
        subject_provenance.setdefault("actor_user_id", projected.actor_user_id)
        subject_provenance.setdefault("economics_user_id", projected.economics_user_id)
    return EconomicsSubject(
        tenant=str(tenant or ""),
        project=str(project or ""),
        user_id=projected.economics_user_id,
        timezone=timezone,
        roles=projected.roles,
        permissions=projected.permissions,
        budget_bypass=projected.budget_bypass,
        is_anonymous=projected.is_anonymous,
        provenance=subject_provenance,
    )


def _subject_plan_role(subject: EconomicsSubject, *, budget_bypass: bool) -> str:
    if budget_bypass:
        return "admin"
    if _subject_is_anonymous(subject):
        return "anonymous"
    # Paid/project/wallet funding is resolved from economics state below. The
    # subject role used for plan lookup only needs to distinguish known users
    # from anonymous and bypass users.
    return "registered"


def _subject_accounting_user_type(subject: EconomicsSubject) -> str:
    """Compatibility label for legacy accounting APIs.

    This value is derivative and must not be treated as authority. It preserves
    the old economics run-accounting contract while the caller-visible subject
    remains fact-based.
    """
    if _subject_budget_bypass(subject):
        return "privileged"
    if _subject_is_anonymous(subject):
        return "anonymous"
    return "registered"


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
        self._funding_ctx_obj = None       # funding_flow.FundingContext (all lanes)
        self._funding_res = None           # funding_flow.PlanFundingReservation (all lanes)
        # quota-lock state (distributed admit->reserve serialization)
        self._quota_lock = QuotaLock(self.redis)

    # -- logging / events -----------------------------------------------------
    def _log(self, stage: str, msg: str, level: str = "INFO", **kv):
        payload = {
            "stage": str(stage or ""),
            "message": str(msg or ""),
            "flow": self.flow,
            "scope_id": self.scope_id,
            "subject_id": self.subj,
            "tenant": self.subject.tenant,
            "project": self.subject.project,
            "user_id": self.subject.user_id,
            "accounting_user_type": _subject_accounting_user_type(self.subject),
            **(kv or {}),
        }
        try:
            trace = "[economics.enforcement] " + json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            trace = f"[economics.enforcement] stage={stage} flow={self.flow} scope_id={self.scope_id} message={msg} data={kv!r}"
        _TRACE_LOGGER.log(_level_no(level), trace)
        if self.logger is None:
            return
        try:
            self.logger.log(trace, level)
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
            "accounting_user_type": _subject_accounting_user_type(self.subject),
            "user_message": user_message,
            "notification_type": "error",
        }
        self._log("deny", message, "WARN", code=code)
        await self._emit_denial(code=code, title=title, data=payload)
        raise EconomicsLimitException(message, code=code, data=payload)

    # -- quota lock (distributed admit->reserve serialization) ----------------
    # Redis mechanics + spin-wait live in the shared funding_flow QuotaLock; the
    # guard owns the policy gating and the denial payload it raises on timeout.
    async def _acquire_quota_lock_or_deny(self, *, scope: str, budget_bypass: bool) -> None:
        # Only reserving surfaces opt in; admin bypass has no shared-pool reserve
        # window to serialize; without redis we degrade to no lock (log a warning).
        if not self.policy.enforce_quota_lock or budget_bypass:
            return
        if self.redis is None:
            self._log("quota_lock", "redis unavailable; quota_lock disabled", "WARN")
            return
        s = self.subject
        ttl_sec = int(self.policy.quota_lock_ttl_sec)
        key = quota_lock_key(s.tenant, s.project, s.user_id, scope, GLOBAL_BUNDLE_ID)
        if await self._quota_lock.acquire_blocking(
            key, ttl_sec=ttl_sec, wait_total_sec=float(self.policy.quota_lock_wait_sec),
        ):
            self._log("quota_lock", "acquired", key=key, scope=scope, ttl_sec=ttl_sec)
            return
        self._log("quota_lock", "failed to acquire within wait window", "WARN", key=key, scope=scope)
        await self._deny(
            code="quota_lock_timeout",
            title="System busy",
            message=f"{self.flow}: quota_lock contended; concurrent planning in progress",
            user_message="Too many concurrent requests are planning quotas right now. Please retry.",
            data={"reason": "quota_lock_timeout", "lane": "deny", "scope": scope},
        )

    async def _release_quota_lock_if_held(self) -> None:
        key = self._quota_lock.key
        if await self._quota_lock.release_if_held():
            self._log("quota_lock", "released", key=key)

    # -- pre-run --------------------------------------------------------------
    async def __aenter__(self) -> EconomicsDecision:
        parent_scope = active_econ_scope()
        if parent_scope:
            # Already inside a settling economics scope (guard-in-guard): verify
            # only, the active guard settles. NOTE: a bare accounting context (e.g. a
            # background-job worker binding turn_id) does NOT count here.
            self._log("nested", "degrading to preflight; active guard settles", parent_scope=parent_scope)
            self.decision = await self._preflight(nested=True)
            return self.decision

        self.decision = await self._enter_top_level()
        return self.decision

    async def _resolve_plan_and_funding(self) -> dict:
        s = self.subject
        budget_bypass = _subject_budget_bypass(s)
        role = _subject_plan_role(s, budget_bypass=budget_bypass)

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
        has_active_subscription = subscription_is_active(subscription, self.now)
        # Only external subscriptions fund a separate plan budget; internal plans draw
        # from the project budget bounded by quota (the plan_id still sets the quota).
        funds_from_subscription = subscription_provides_plan_budget(subscription, self.now)

        # plan id (shared resolver; budget_bypass == role in admin/privileged)
        plan_id, _ = resolve_plan_id(
            role=role,
            has_active_subscription=has_active_subscription,
            subscription=subscription,
        )

        base_policy = await self.cp.get_plan_quota_policy(
            tenant=s.tenant, project=s.project, plan_id=plan_id
        )
        if not base_policy:
            base_policy = QuotaPolicy(max_concurrent=None)
        # Concurrency is chat-only: drop the concurrency dimension for non-chat flows.
        if not self.policy.enforce_concurrency:
            base_policy = dataclasses.replace(base_policy, max_concurrent=None)

        # funding source selection — mirror run()'s plan-funding eligibility.
        # Project budget backs the plan lane for any non-anonymous projected
        # subject unless an active subscription pays or the user is wallet-only.
        plan_source = "subscription" if has_active_subscription else "role"
        resolver = getattr(self.ep, "project_budget_allowed_for_plan", None)
        if callable(resolver):
            project_budget_allowed = bool(resolver(
                is_anonymous=bool(s.is_anonymous),
                plan_id=plan_id,
                plan_source=plan_source,
                has_wallet=has_wallet,
                has_active_subscription=funds_from_subscription,
            ))
        else:
            # Fallback mirrors BaseEntrypointWithEconomics.project_budget_allowed_for_plan.
            wallet_first = getattr(self.ep, "wallet_users_use_project_budget_first", lambda: True)()
            project_budget_allowed = (
                not funds_from_subscription
                and not (has_wallet and not wallet_first)
                and role != "anonymous"
            )

        return {
            "role": role,
            "accounting_user_type": _subject_accounting_user_type(s),
            "roles": sorted(_subject_roles(s)),
            "budget_bypass": budget_bypass,
            "est_turn_tokens": est_turn_tokens,
            "est_turn_usd": est_turn_usd,
            "plan_balance": plan_balance,
            "wallet_tokens": wallet_tokens,
            "has_wallet": has_wallet,
            "subscription": subscription,
            "has_active_subscription": has_active_subscription,
            "funds_from_subscription": funds_from_subscription,
            "plan_id": plan_id,
            "base_policy": base_policy,
            "project_budget_allowed": project_budget_allowed,
        }

    async def _admit(
        self, base_policy: QuotaPolicy, *, reserve_tokens: int = 0,
        wallet_aware: bool = False, has_wallet: bool = False,
        wallet_available_tokens: int = 0, r_total: Optional[int] = None,
    ) -> AdmitResult:
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
            wallet_aware=bool(wallet_aware),
            has_wallet=bool(has_wallet),
            wallet_available_tokens=int(wallet_available_tokens or 0),
            r_total=r_total,
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
        self._log(
            "preflight_start",
            "verify-only economics check",
            nested=bool(nested),
            estimate_reservation_usd=self.estimate.reservation_usd,
            estimate_min_tokens=self.estimate.min_tokens,
        )
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
        decision = EconomicsDecision(
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
        self._log(
            "preflight_ok",
            "economics feasible",
            nested=bool(nested),
            lane=decision.lane,
            plan_id=decision.plan_id,
            funding_source=decision.funding_source,
            est_turn_usd=round(float(decision.est_turn_usd or 0.0), 6),
            est_turn_tokens=int(decision.est_turn_tokens or 0),
        )
        return decision

    def _funding_summary(self, r: dict) -> Tuple[str, float]:
        if r["budget_bypass"]:
            return "project", float("inf")
        if r["funds_from_subscription"]:
            return "subscription", float("inf")  # availability checked at reserve time
        if r["project_budget_allowed"]:
            return "project", float("inf")
        return "none", 0.0

    async def _enter_top_level(self) -> EconomicsDecision:
        r = await self._resolve_plan_and_funding()
        est_turn_tokens = int(r["est_turn_tokens"])
        budget_bypass = r["budget_bypass"]
        funding_source, available_usd = self._funding_summary(r)
        self._log(
            "plan_resolved",
            "resolved economics plan and funding lane",
            role=r.get("role"),
            plan_id=r.get("plan_id"),
            funding_source=funding_source,
            budget_bypass=bool(budget_bypass),
            est_turn_usd=round(float(r.get("est_turn_usd") or 0.0), 6),
            est_turn_tokens=est_turn_tokens,
            has_wallet=bool(r.get("has_wallet")),
            has_active_subscription=bool(r.get("has_active_subscription")),
            project_budget_allowed=bool(r.get("project_budget_allowed")),
        )

        # Serialize the admit->reserve planning window per user (reserving surfaces
        # only; no-op unless policy.enforce_quota_lock + redis). Acquired BEFORE
        # admit (which reserves RL tokens) and released as soon as all holds are
        # taken (or on any deny) — never held across the LLM work. Mirrors run().
        _, quota_scope = r["base_policy"].effective_allowed_tokens()
        await self._acquire_quota_lock_or_deny(scope=str(quota_scope or "month"), budget_bypass=budget_bypass)
        try:
            # 1) size the primary funds (P) to bound the plan reserve, then admit:
            #    the wallet-aware admit reserves plan_part=min(R,Q,P) atomically and
            #    decides admit/deny (the wallet covers the over-quota/over-funds
            #    remainder). One split, one reservation.
            sub_limiter = None
            primary_cap_tokens = est_turn_tokens  # bypass / unlimited default
            if not budget_bypass:
                primary_cap_tokens, sub_limiter = await self._primary_cap_tokens(r, funding_source)
            want_resv = min(int(est_turn_tokens), int(primary_cap_tokens))

            # The wallet always covers the over-quota/over-funds remainder (the unified
            # split's wallet_part) — identical to chat run(). No per-surface gate.
            eff_has_wallet = bool(r["has_wallet"])
            eff_wallet_tokens = int(r["wallet_tokens"])

            admit = await self._admit(
                r["base_policy"], reserve_tokens=want_resv,
                wallet_aware=True, r_total=est_turn_tokens,
                has_wallet=eff_has_wallet,
                wallet_available_tokens=eff_wallet_tokens,
            )
            self._log(
                "admit",
                "quota admit evaluated (split)",
                allowed=bool(getattr(admit, "allowed", False)),
                reason=getattr(admit, "reason", None),
                reserved_tokens=int(getattr(admit, "reserved_tokens", 0) or 0),
                wallet_part=int(getattr(admit, "wallet_part", 0) or 0),
                reservation_id=getattr(admit, "reservation_id", None),
            )

            est_turn_usd = float(r["est_turn_usd"])
            decision = EconomicsDecision(
                lane="bypass" if budget_bypass else "plan",
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
                # Quota/funds exhausted and the wallet cannot cover the remainder, or
                # an indivisible (requests/concurrency) gate with no wallet. Deny.
                if ("wallet_insufficient" in str(admit.reason or "")
                        and funding_source in ("project", "subscription")
                        and int(primary_cap_tokens) == 0):
                    # Primary budget fully exhausted: its zero cap clamped want_resv to 0,
                    # so the deny surfaces here at admit instead of at reserve_funding. Emit
                    # the same primary-exhausted signal the reserve path would.
                    await self._deny(
                        code=f"{funding_source}_reservation_failed",
                        title="Insufficient funds",
                        message=f"{self.flow}: {funding_source} budget exhausted and user cannot pay",
                        user_message=MSG_NO_FUNDING,
                        data={"reason": f"{funding_source}_budget_exhausted",
                              "funding_source": funding_source,
                              "snapshot": admit.snapshot, "lane": "deny"},
                    )
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

                # 2) single split reservation via the shared funding_flow (primary + wallet)
                from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import (
                    reserve_funding, ReserveStatus,
                )
                try:
                    ctx = self._funding_ctx(sub_limiter)
                    outcome = await reserve_funding(
                        ctx, admit=admit,
                        funding_source=("project" if budget_bypass else funding_source),
                        budget_bypass=budget_bypass, has_wallet=eff_has_wallet,
                        ttl_sec=int(self.policy.reservation_ttl_sec),
                    )
                    if outcome.status is ReserveStatus.OK:
                        res = outcome.reservation
                        self._funding_ctx_obj = ctx
                        self._funding_res = res
                        decision.app_reservation_source = res.funding_source
                        decision.app_reservation_active = res.app_reservation_active
                        if res.wallet_reservation_active:
                            decision.wallet_reservation_id = res.wallet_reservation_id
                            decision.wallet_reserved_tokens = int(res.wallet_reserved_tokens or 0)
                        self._log(
                            "reserve_ok",
                            "reserved funding (split)",
                            lane=decision.lane,
                            funding_source=decision.funding_source,
                            app_reservation_active=bool(res.app_reservation_active),
                            plan_reservation_active=bool(res.plan_reservation_active),
                            wallet_reservation_active=bool(res.wallet_reservation_active),
                        )
                    else:  # ReserveStatus.DENIED
                        await self._deny(
                            code=outcome.deny_code or "no_funding_source",
                            title="Insufficient funds",
                            message=outcome.deny_message or f"{self.flow}: cannot fund request",
                            user_message=MSG_NO_FUNDING,
                            data=outcome.deny_data or {"reason": "no_funding_source"},
                        )
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

    async def _primary_cap_tokens(self, r: dict, funding_source: str):
        """Size the primary funds (project/subscription) in token-equivalent so the
        wallet-aware admit reserves at most what the plan can actually pay; returns
        (cap_tokens, subscription_limiter). The subscription limiter is built here and
        reused for the FundingContext when funding_source == 'subscription'."""
        from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import _cap_tokens_for_usd
        est = int(r["est_turn_tokens"])
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
                avail = float(snap.get("available_usd") or 0.0)
            except Exception:
                avail = 0.0
            return _cap_tokens_for_usd(available_usd=avail, usd_per_token=self.usd_per_token), sub_limiter
        if funding_source == "project":
            try:
                snap = await self.budget_limiter.get_app_budget_balance()
            except Exception:
                snap = None
            if snap is not None:
                od = snap.get("overdraft_limit_usd")
                if od is None:
                    return est, None  # unlimited overdraft -> no fund clamp
                cap_usd = float(snap.get("available_usd") or 0.0) + float(od or 0.0)
                return _cap_tokens_for_usd(available_usd=cap_usd, usd_per_token=self.usd_per_token), None
            return est, None
        return 0, None  # funding_source == "none"

    async def _on_funding_denied(self, exc: Exception) -> None:
        """Log + optionally emit a denial, then release the RL token reservation
        taken by admit (the funding flow releases its own money holds before raising)."""
        code = getattr(exc, "code", "denied")
        self._log("deny_cleanup", "releasing holds after economics denial", "WARN", code=code, error=str(exc))
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
        metadata = {
            "flow": self.flow,
            "scope_id": self.scope_id,
            "conversation_id": self.scope_id,
            "turn_id": self.scope_id,
            "bundle_id": self.bundle_id,
        }
        if isinstance(s.provenance, dict) and s.provenance:
            metadata["provenance"] = dict(s.provenance)
        self._ensure_accounting_storage()
        self._acct_cm = acct.with_accounting(
            self.flow,
            user_id=s.user_id,
            user_type=_subject_accounting_user_type(s),
            tenant_id=s.tenant,
            project_id=s.project,
            request_id=self.scope_id,
            app_bundle_id=self.bundle_id,
            timezone=s.timezone,
            conversation_id=self.scope_id,   # synthetic; gives the file fallback an id prefix
            turn_id=self.scope_id,
            metadata=metadata,
        )
        self._acct_cm.__enter__()
        self._log(
            "accounting_bound",
            "accounting context bound",
            request_id=self.scope_id,
            conversation_id=self.scope_id,
            turn_id=self.scope_id,
            bundle_id=self.bundle_id,
        )

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
            return False  # nested: active guard settles; nothing bound here

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
        self._log("accounting_run_start", "collecting usage for settlement", usage_from=usage_from)
        ranked, result = await self.ep.run_accounting(
            tenant=self.subject.tenant,
            project=self.subject.project,
            user_id=self.subject.user_id,
            user_type=_subject_accounting_user_type(self.subject),
            thread_id=self.scope_id,
            turn_id=self.scope_id,
            usage_from=usage_from,
            emit_turn_event=False,
        )
        self._log(
            "accounting_run_done",
            "usage collected",
            ranked_tokens=int(ranked or 0),
            cost_total_usd=round(float((result or {}).get("cost_total_usd") or 0.0), 6),
        )
        return int(ranked or 0), (result or {})

    async def _settle(self, d: EconomicsDecision, *, ranked_tokens: int, total_cost: float) -> None:
        # All lanes (plan/subscription/project/bypass + paid wallet/subscription)
        # settle through the shared funding_flow — single settlement owner. The paid
        # reserve builds the same PlanFundingReservation handle as the plan lane.
        self._log(
            "settle_start",
            "settling economics reservation",
            lane=d.lane,
            funding_source=d.funding_source,
            ranked_tokens=int(ranked_tokens or 0),
            total_cost_usd=round(float(total_cost or 0.0), 6),
        )
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
        self._log("settle", "no funding reservation to settle", "WARN")

    async def _cleanup_release(self, d: EconomicsDecision) -> None:
        # All lanes hold a PlanFundingReservation -> release everything (app/wallet/RL
        # holds + lock) through the shared funding_flow cleanup.
        if self._funding_res is not None and self._funding_ctx_obj is not None:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import release_plan_funding
            await release_plan_funding(self._funding_ctx_obj, self._funding_res)
            return
        # defensive: nothing reserved -> drop any RL lock/reservation tied to scope
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

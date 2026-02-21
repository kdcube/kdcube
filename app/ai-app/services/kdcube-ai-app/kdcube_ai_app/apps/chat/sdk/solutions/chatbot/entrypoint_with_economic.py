# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/sdk/solutions/chatbot/entrypoint_with_economics.py

from __future__ import annotations

from typing import Any, Optional, Dict

import asyncio
import dataclasses
import json
import math
import secrets
from datetime import datetime, timezone
from uuid import uuid4, UUID

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.service_hub.inventory import Config, _mid


class BaseEntrypointWithEconomics(BaseEntrypoint):
    """
    BaseEntrypoint with optional economics wiring.
    This class only initializes managers; policy logic remains bundle-specific.
    """

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
        event_filter: Optional[Any] = None,
        ctx_client: Optional[Any] = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
            event_filter=event_filter,
            ctx_client=ctx_client,
        )

        self.cp_manager = None
        self.rl = None
        self.budget_limiter = None
        self._policies_initialized = False

        if self.redis or self.pg_pool:
            from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager
            from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import UserEconomicsRateLimiter
            from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter

            self.cp_manager = ControlPlaneManager(
                pg_pool=self.pg_pool,
                redis=self.redis,
                cache_ttl=60,
                plan_balance_cache_ttl=10,
            )
            self.rl = UserEconomicsRateLimiter(
                self.redis,
                user_balance_snapshot_mgr=self.cp_manager.plan_balance_snapshot_mgr,
            )
            self.budget_limiter = ProjectBudgetLimiter(
                redis=self.redis,
                pg_pool=self.pg_pool,
                tenant=self.settings.TENANT,
                project=self.settings.PROJECT,
            )

    @property
    def bundle_props_defaults(self) -> Dict[str, Any]:
        base = dict(super().bundle_props_defaults or {})
        econ = dict(base.get("economics") or {})
        econ.setdefault("reservation_amount_dollars", 2.0)
        base["economics"] = econ
        return base


    async def ensure_policies_initialized(self):
        """
        Ensure policies are seeded from bundle configuration (one-time operation).
        Optional hook for bundles that seed policies from config.
        Run from a master bundle only.
        Override in subclasses as needed.
        """
        if self._policies_initialized:
            return

        tenant = self.settings.TENANT
        project = self.settings.PROJECT
        bundle_id = self.config.ai_bundle_spec.id

        self._policies_initialized = \
            await self.cp_manager.tenant_project_plan_quota_policies_initialize_from_master_app(tenant=tenant,
                                                                                                  project=project,
                                                                                                  bundle_id=bundle_id,
                                                                                                  app_quota_policies=self.app_quota_policies,
                                                                                                  app_budget_policies=self.app_budget_policies)

    @property
    def app_quota_policies(self):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy

        anonymous_policy = QuotaPolicy(
            max_concurrent=1,
            requests_per_day=2,
            requests_per_month=60,
            total_requests=None,
            tokens_per_hour=150_000,
            tokens_per_day=1_500_000,
            tokens_per_month=20_000_000,
        )
        return {
            "anonymous": anonymous_policy,
            "free": QuotaPolicy(
                max_concurrent=2,
                requests_per_day=100,
                requests_per_month=30000,
                total_requests=None,
                tokens_per_hour=133_333,
                tokens_per_day=333_333,
                tokens_per_month=666_666,
            ),
            "payasyougo": QuotaPolicy(
                max_concurrent=4,
                requests_per_day=200,
                requests_per_month=6000,
                total_requests=None,
            ),
            "admin": QuotaPolicy(
                max_concurrent=10,
            )
        }

    @property
    def app_budget_policies(self):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import ProviderBudgetPolicy

        return {
            "anthropic": ProviderBudgetPolicy(
                provider="anthropic",
                usd_per_hour=10.0,
                usd_per_day=200.0,
                usd_per_month=5000.0,
            ),
            "openai": ProviderBudgetPolicy(
                provider="openai",
                usd_per_hour=5.0,
                usd_per_day=100.0,
                usd_per_month=2000.0,
            ),
            "brave": ProviderBudgetPolicy(
                provider="brave",
                usd_per_hour=1.0,
                usd_per_day=20.0,
                usd_per_month=500.0,
            ),
            "duckduckgo": ProviderBudgetPolicy(
                provider="duckduckgo",
                usd_per_hour=None,
                usd_per_day=None,
                usd_per_month=None,
            ),
        }

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        raise NotImplementedError("execute_core() must be implemented by subclasses")

    async def pre_run_hook(self, *, state: Dict[str, Any], econ_ctx: Dict[str, Any]) -> None:
        return None

    async def post_run_hook(self, *, state: Dict[str, Any], result: Dict[str, Any], econ_ctx: Dict[str, Any]) -> None:
        return None

    def project_budget_user_types(self) -> set[str]:
        """
        User types allowed to use project budget when no active subscription.
        Override in subclasses to customize.
        """
        return {"registered"}

    def wallet_users_use_project_budget_first(self) -> bool:
        """
        If True, wallet users still consume project budget for plan quota,
        and wallet is used only for overflow. Override to switch wallet-only.
        """
        return True

    def budget_bypass_user_types(self) -> set[str]:
        """
        User types that bypass budget checks/limits but still run under quota policy.
        """
        return {"privileged", "admin"}

    async def run(self, **params) -> Dict[str, Any]:
        """
        Economics-aware run() with strict two-source funding and atomic reservations.
        """
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
            AdmitResult,
            QuotaInsight,
            compute_quota_insight,
            subject_id_of,
            GLOBAL_BUNDLE_ID,
        )
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy, EconomicsLimitException
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import _merge_policy_with_plan_override
        from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
        from kdcube_ai_app.infra.accounting.usage import llm_output_price_usd_per_token, anthropic, sonnet_45
        from kdcube_ai_app.apps.chat.sdk.util import safe_frac, token_count
        from kdcube_ai_app.infra import accounting as acct

        SAFETY_MARGIN = 1.15
        EST_TURN_TOKENS_FLOOR = 2000
        DEFAULT_OUTPUT_BUDGET = 4000
        est_turn_tokens = int(EST_TURN_TOKENS_FLOOR)
        lock_released = False

        bundle_id = self.config.ai_bundle_spec.id
        rl_bundle_id = GLOBAL_BUNDLE_ID

        def _j(obj) -> str:
            try:
                return json.dumps(obj, ensure_ascii=False, default=str)
            except Exception:
                return str(obj)

        def _log(stage: str, msg: str, level: str = "INFO", **kv):
            payload = {"stage": stage, **kv}
            self.logger.log(f"[run] {stage} | {msg} | { _j(payload) }", level)

        _log("economics", "--- START PRE-RUN ECONOMICS ---")

        async def _emit_event(*, type: str, status: str, title: str, data: dict):
            try:
                await self.comm.service_event(
                    type=type,
                    step="rate_limit",
                    status=status,
                    title=title,
                    agent="bundle.rate_limiter",
                    data=data,
                )
            except Exception as e:
                _log("telemetry", "Failed to emit service_event", "WARN", error=str(e), event_type=type)

        async def _emit_analytics_event(*, type: str, status: str, title: str, data: dict):
            try:
                await self.comm.service_event(
                    type=type,
                    step="analytics",
                    status=status,
                    title=title,
                    agent="bundle.rate_limiter",
                    data=data,
                )
            except Exception as e:
                _log("telemetry", "Failed to emit analytics event", "WARN", error=str(e), event_type=type)

        async def _econ_fail(*, code: str, title: str, message: str, event_type: str, data: dict):
            payload = dict(data)
            payload["code"] = code
            payload["show_in_timeline"] = False
            await _emit_event(type=event_type, status="error", title=title, data=payload)
            raise EconomicsLimitException(message, code=code, data=payload)

        async def _record_app_analytics_by_provider(
            *,
            app_spend_usd: float,
            total_cost_usd: float,
            cost_breakdown: list[dict],
            now: datetime,
        ) -> None:
            if app_spend_usd <= 0 or total_cost_usd <= 0:
                return
            try:
                for item in (cost_breakdown or []):
                    provider = item.get("provider")
                    provider_cost = float(item.get("cost_usd") or 0.0)
                    if not provider or provider_cost <= 0:
                        continue
                    provider_app_cost = float(app_spend_usd) * safe_frac(provider_cost, total_cost_usd)
                    if provider_app_cost > 0:
                        await self.budget_limiter.record_budget_analytics_only(
                            bundle_id=bundle_id,
                            provider=str(provider),
                            spent_usd=float(provider_app_cost),
                            now=now,
                        )
            except Exception as e:
                _log("analytics", "Provider analytics write failed (best-effort)", "WARN", error=str(e))

        async def _quota_lock_try_acquire(key: str, token: str, ttl_sec: int) -> bool:
            r = self.redis
            if r is None:
                return False
            try:
                ok = await r.set(key, token, nx=True, ex=ttl_sec)
                return bool(ok)
            except TypeError:
                pass
            except Exception:
                return False
            try:
                ok = await r.set(key, token, nx=True, px=int(ttl_sec * 1000))
                return bool(ok)
            except Exception:
                return False

        async def _quota_lock_release(key: str, token: str) -> None:
            r = self.redis
            if r is None:
                return
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

        quota_lock_key = None
        quota_lock_token = None
        quota_lock_acquired = False

        async def _acquire_quota_lock_or_deny(*, scope: str) -> None:
            nonlocal quota_lock_key, quota_lock_token, quota_lock_acquired
            if self.redis is None:
                _log("quota_lock", "Redis unavailable; quota_lock disabled", "WARN")
                return
            quota_lock_key = f"quota_lock:{tenant}:{project}:{user_id}:{scope}:{rl_bundle_id}"
            quota_lock_token = secrets.token_hex(16)
            ttl_sec = 60
            wait_total_sec = 5.0
            t0 = asyncio.get_event_loop().time()
            sleep = 0.05
            while True:
                ok = await _quota_lock_try_acquire(quota_lock_key, quota_lock_token, ttl_sec=ttl_sec)
                if ok:
                    quota_lock_acquired = True
                    _log("quota_lock", "Acquired quota_lock", key=quota_lock_key, scope=scope, ttl_sec=ttl_sec)
                    return
                if (asyncio.get_event_loop().time() - t0) >= wait_total_sec:
                    _log("quota_lock", "Failed to acquire quota_lock within wait window", "WARN", key=quota_lock_key, scope=scope)
                    await _econ_fail(
                        code="quota_lock_timeout",
                        title="System busy",
                        message="Too many concurrent requests are planning quotas right now. Please retry.",
                        event_type="rate_limit.denied",
                        data={
                            "reason": "quota_lock_timeout",
                            "bundle_id": bundle_id,
                            "subject_id": self.subj,
                            "user_type": user_type,
                            "lane": "deny",
                            "scope": scope,
                        },
                    )
                await asyncio.sleep(sleep)
                sleep = min(sleep * 1.5, 0.25)

        async def _release_quota_lock_if_held() -> None:
            nonlocal quota_lock_key, quota_lock_token, quota_lock_acquired
            if quota_lock_acquired and quota_lock_key and quota_lock_token:
                await _quota_lock_release(quota_lock_key, quota_lock_token)
                _log("quota_lock", "Released quota_lock", key=quota_lock_key)
            quota_lock_key = None
            quota_lock_token = None
            quota_lock_acquired = False

        await self.ensure_policies_initialized()

        self._turn_id = self._turn_id or _mid("turn")
        turn_id = self._turn_id
        lock_id = turn_id

        state = dict(getattr(self, "_app_state", {}) or {})
        state["turn_id"] = turn_id
        if params.get("text"):
            state["text"] = params["text"]
        if "attachments" in params:
            state["attachments"] = params.get("attachments") or []

        tenant = state.get("tenant")
        project = state.get("project")
        user_id = state.get("user") or state.get("fingerprint")
        user_type = state.get("user_type") or "anonymous"
        role = user_type
        budget_bypass = role in self.budget_bypass_user_types()
        thread_id = state.get("conversation_id") or state.get("session_id") or "default"

        self.subj = subject_id_of(tenant, project, user_id)

        await self.refresh_bundle_props(state=state)
        econ_props = (self.bundle_props or {}).get("economics") or {}

        _log(
            "init",
            "Initialized run()",
            tenant=tenant, project=project,
            user_id=user_id, user_type=role,
            thread_id=thread_id, turn_id=turn_id, bundle_id=bundle_id, rl_bundle_id=rl_bundle_id,
            text_len=len((state.get("text") or "")),
        )
        if budget_bypass:
            _log("budget.bypass", "Budget bypass enabled for user type", user_type=role)

        input_text = state.get("text") or ""
        usd_per_token = float(llm_output_price_usd_per_token(ref_provider=anthropic, ref_model=sonnet_45))
        try:
            input_tokens_est = int(token_count(input_text))
        except Exception:
            input_tokens_est = max(1, int(len(input_text) / 4))
        output_budget = params.get("max_tokens") or params.get("max_output_tokens") or DEFAULT_OUTPUT_BUDGET
        try:
            output_budget = int(output_budget)
        except Exception:
            output_budget = DEFAULT_OUTPUT_BUDGET
        output_budget = max(500, min(output_budget, DEFAULT_OUTPUT_BUDGET))
        reservation_amount_dollars = None
        try:
            raw_reservation = econ_props.get("reservation_amount_dollars")
            if raw_reservation is not None:
                reservation_amount_dollars = float(raw_reservation)
        except Exception:
            reservation_amount_dollars = None

        if reservation_amount_dollars is not None and reservation_amount_dollars > 0:
            est_turn_tokens = int(
                math.ceil(reservation_amount_dollars / max(float(usd_per_token) * float(SAFETY_MARGIN), 1e-9))
            )
            est_turn_tokens = max(int(EST_TURN_TOKENS_FLOOR), int(est_turn_tokens))
        else:
            est_turn_tokens = max(int(EST_TURN_TOKENS_FLOOR), int(input_tokens_est + output_budget))
        _log(
            "estimate",
            "Estimated per-turn tokens (pre-plan)",
            input_tokens_est=input_tokens_est,
            output_budget=output_budget,
            est_turn_tokens=est_turn_tokens,
            reservation_amount_dollars=reservation_amount_dollars,
        )

        plan_balance = await self.cp_manager.get_user_plan_balance(tenant=tenant, project=project, user_id=user_id)
        _log(
            "plan_balance",
            "Fetched user plan overrides/balance",
            has_plan_balance=bool(plan_balance),
            has_plan_override=bool(plan_balance and plan_balance.has_plan_override()),
            plan_override_active=bool(plan_balance and plan_balance.plan_override_is_active()),
            has_lifetime_budget=bool(plan_balance and plan_balance.has_lifetime_budget()),
            plan_expires_at=getattr(plan_balance, "expires_at", None),
            plan_tokens_per_month=getattr(plan_balance, "tokens_per_month", None),
        )

        user_budget_tokens: Optional[int] = None
        if plan_balance and plan_balance.has_lifetime_budget():
            bal = await self.cp_manager.user_credits_mgr.get_lifetime_balance(tenant=tenant, project=project, user_id=user_id)
            user_budget_tokens = int(bal or 0)

        _log("user_budget", "Computed user lifetime budget", user_budget_tokens=user_budget_tokens)

        subscription = await self.cp_manager.subscription_mgr.get_subscription(
            tenant=tenant,
            project=project,
            user_id=user_id,
        )
        sub_now = datetime.utcnow().replace(tzinfo=timezone.utc)
        sub_due_at = getattr(subscription, "next_charge_at", None) if subscription else None
        sub_chargeable = bool(subscription and int(getattr(subscription, "monthly_price_cents", 0) or 0) > 0)
        sub_past_due = bool(sub_due_at and sub_due_at <= sub_now)
        has_active_subscription = bool(
            subscription
            and getattr(subscription, "status", None) == "active"
            and sub_chargeable
            and not sub_past_due
        )
        has_wallet = bool(user_budget_tokens and int(user_budget_tokens) > 0)

        plan_id = None
        plan_source = None
        if role in ("privileged", "admin"):
            plan_id = "admin"
            plan_source = "role"
        elif role == "anonymous":
            plan_id = "anonymous"
            plan_source = "role"
        elif has_active_subscription:
            plan_id = getattr(subscription, "plan_id", None) or "payasyougo"
            plan_source = "subscription"
        else:
            plan_id = "free"
            plan_source = "role"

        _log(
            "plan",
            "Resolved plan for user",
            plan_id=plan_id,
            plan_source=plan_source,
            user_type=role,
        )

        base_policy = await self.cp_manager.get_plan_quota_policy(
            tenant=tenant, project=project, plan_id=plan_id
        )
        if not base_policy:
            base_policy = (
                self.app_quota_policies.get(plan_id)
                or self.app_quota_policies.get("free")
                or self.app_quota_policies["anonymous"]
            )
            _log(
                "policy.base",
                "No policy in DB; using fallback",
                "WARN",
                plan_id=plan_id,
                base_policy=dataclasses.asdict(base_policy),
            )
        else:
            _log(
                "policy.base",
                "Loaded base policy from control plane",
                plan_id=plan_id,
                base_policy=dataclasses.asdict(base_policy),
            )

        payg_policy: QuotaPolicy | None = None
        if has_wallet or plan_id == "payasyougo":
            payg_policy = await self.cp_manager.get_plan_quota_policy(
                tenant=tenant, project=project, plan_id="payasyougo"
            )
            if not payg_policy:
                payg_policy = self.app_quota_policies.get("payasyougo") or base_policy

        if plan_id == "free" and has_wallet and not has_active_subscription:
            payg_policy = payg_policy or base_policy

            base_policy = QuotaPolicy(
                max_concurrent=int(getattr(payg_policy, "max_concurrent", None) or getattr(base_policy, "max_concurrent", 1) or 1),
                requests_per_day=getattr(payg_policy, "requests_per_day", None),
                requests_per_month=getattr(payg_policy, "requests_per_month", None),
                total_requests=getattr(payg_policy, "total_requests", None),
                tokens_per_hour=getattr(base_policy, "tokens_per_hour", None),
                tokens_per_day=getattr(base_policy, "tokens_per_day", None),
                tokens_per_month=getattr(base_policy, "tokens_per_month", None),
            )
            _log(
                "policy.base",
                "Adjusted base policy for wallet-backed free user (payg service limits + free token limits)",
                plan_id=plan_id,
                base_policy=dataclasses.asdict(base_policy),
            )

        allow_paid_lane_fallback = bool(has_wallet)

        policy_for_est = base_policy
        if plan_balance and plan_balance.plan_override_is_active():
            policy_for_est = _merge_policy_with_plan_override(base_policy, plan_balance)
        est_limit_tokens, _ = policy_for_est.effective_allowed_tokens()
        if est_limit_tokens is not None and int(est_limit_tokens) > 0:
            if not has_wallet and not has_active_subscription:
                est_turn_tokens = min(int(est_turn_tokens), int(est_limit_tokens))
        _log(
            "estimate",
            "Estimated per-turn tokens",
            est_turn_tokens=est_turn_tokens,
            est_limit_tokens=est_limit_tokens,
        )

        subscription_budget = None
        subscription_budget_limiter = None
        subscription_available_usd = 0.0
        subscription_balance_usd = 0.0
        subscription_period_key = None

        if has_active_subscription and not budget_bypass:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
            from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
            period_desc = build_subscription_period_descriptor(
                tenant=tenant,
                project=project,
                user_id=user_id,
                provider=getattr(subscription, "provider", "internal") or "internal",
                stripe_subscription_id=getattr(subscription, "stripe_subscription_id", None),
                period_end=getattr(subscription, "next_charge_at", None),
                period_start=getattr(subscription, "last_charged_at", None),
            )
            subscription_period_key = period_desc["period_key"]
            subscription_budget_limiter = SubscriptionBudgetLimiter(
                pg_pool=self.pg_pool,
                tenant=tenant,
                project=project,
                user_id=user_id,
                period_key=period_desc["period_key"],
                period_start=period_desc["period_start"],
                period_end=period_desc["period_end"],
            )
            try:
                expired = await subscription_budget_limiter.reap_expired_reservations(
                    project_budget=self.budget_limiter,
                )
                if expired:
                    _log(
                        "subscription_budget",
                        "Reaped expired subscription reservations",
                        expired=int(expired),
                        period_key=period_desc["period_key"],
                    )
            except Exception as e:
                _log(
                    "subscription_budget",
                    "Failed to reap expired subscription reservations",
                    "WARN",
                    error=str(e),
                    period_key=period_desc["period_key"],
                )
            subscription_budget = await subscription_budget_limiter.get_subscription_budget_balance()
            subscription_available_usd = float(subscription_budget.get("available_usd") or 0.0)
            subscription_balance_usd = float(subscription_budget.get("balance_usd") or 0.0)
            _log(
                "subscription_budget",
                "Fetched subscription budget balance",
                subscription_balance_usd=subscription_balance_usd,
                subscription_available_usd=subscription_available_usd,
                snapshot=subscription_budget,
            )

        project_budget_allowed = user_type in self.project_budget_user_types()
        if has_wallet and not has_active_subscription and not self.wallet_users_use_project_budget_first():
            project_budget_allowed = False

        project_budget = None
        project_available_usd = 0.0
        project_balance_usd = 0.0
        if not budget_bypass and not has_active_subscription and project_budget_allowed:
            project_budget = await self.budget_limiter.get_app_budget_balance()
            project_available_usd = float(project_budget.get("available_usd") or 0.0)
            project_balance_usd = float(project_budget.get("balance_usd") or 0.0)

            _log(
                "project_budget",
                "Fetched project budget balance",
                project_balance_usd=project_balance_usd,
                project_available_usd=project_available_usd,
                snapshot=project_budget,
            )

        if budget_bypass:
            funding_source = "project"
            funding_label = "project budget (bypass)"
            funding_limiter = self.budget_limiter
            funding_budget = project_budget
            funding_available_usd = float("inf")
            funding_balance_usd = float("inf")
        elif has_active_subscription:
            funding_source = "subscription"
            funding_label = "subscription balance"
            funding_limiter = subscription_budget_limiter
            funding_budget = subscription_budget
            funding_available_usd = subscription_available_usd
            funding_balance_usd = subscription_balance_usd
        elif project_budget_allowed:
            funding_source = "project"
            funding_label = "project budget"
            funding_limiter = self.budget_limiter
            funding_budget = project_budget
            funding_available_usd = project_available_usd
            funding_balance_usd = project_balance_usd
        else:
            funding_source = "none"
            funding_label = "no funding source"
            funding_limiter = None
            funding_budget = None
            funding_available_usd = 0.0
            funding_balance_usd = 0.0

        est_turn_usd = float(est_turn_tokens) * usd_per_token * SAFETY_MARGIN

        wallet_can_pay_turn = user_budget_tokens is not None and user_budget_tokens >= int(est_turn_tokens)
        subscription_can_pay_turn = bool(has_active_subscription and subscription_available_usd >= est_turn_usd)
        personal_can_pay_turn = wallet_can_pay_turn or subscription_can_pay_turn

        lane: str | None = None
        admit: AdmitResult | None = None
        plan_admit_now = datetime.utcnow().replace(tzinfo=timezone.utc)
        plan_reservation_id = None
        plan_reservation_active = False
        paid_policy: QuotaPolicy | None = None
        paid_funding_source: str | None = None

        def _build_paid_policy() -> QuotaPolicy:
            policy_src = payg_policy or base_policy
            return QuotaPolicy(
                max_concurrent=int(getattr(policy_src, "max_concurrent", 1) or 1),
                requests_per_day=getattr(policy_src, "requests_per_day", None),
                requests_per_month=getattr(policy_src, "requests_per_month", None),
                total_requests=getattr(policy_src, "total_requests", None),
                tokens_per_hour=None, tokens_per_day=None, tokens_per_month=None,
            )

        async def _admit_plan() -> AdmitResult:
            try:
                return await self.rl.admit(
                    bundle_id=rl_bundle_id,
                    subject_id=self.subj,
                    policy=base_policy,
                    lock_id=lock_id, lock_ttl_sec=180,
                    apply_plan_override=True,
                    reserve_tokens=est_turn_tokens,
                    reservation_id=turn_id,
                    reservation_ttl_sec=900,
                    now=plan_admit_now,
                )
            except TypeError:
                _log("admit.plan", "rl.admit lacks apply_plan_override; calling without", "WARN")
                return await self.rl.admit(
                    bundle_id=rl_bundle_id, subject_id=self.subj, policy=base_policy,
                    lock_id=lock_id, lock_ttl_sec=180,
                )

        async def _admit_paid(p: QuotaPolicy) -> AdmitResult:
            try:
                return await self.rl.admit(
                    bundle_id=rl_bundle_id, subject_id=self.subj, policy=p,
                    lock_id=lock_id, lock_ttl_sec=180,
                    apply_plan_override=False,
                )
            except TypeError:
                _log("admit.paid", "rl.admit lacks apply_plan_override; calling without", "WARN")
                return await self.rl.admit(
                    bundle_id=rl_bundle_id, subject_id=self.subj, policy=p,
                    lock_id=lock_id, lock_ttl_sec=180,
                )

        async def _switch_plan_to_paid_or_die(*, switch_reason: str) -> None:
            nonlocal lane, paid_policy, admit, effective_policy
            nonlocal lock_released, plan_reservation_id, plan_reservation_active, plan_reserved_tokens

            if plan_reservation_id:
                try:
                    await self.rl.release_token_reservation(
                        bundle_id=rl_bundle_id,
                        subject_id=self.subj,
                        reservation_id=plan_reservation_id,
                        now=plan_admit_now,
                    )
                finally:
                    plan_reservation_id = None
                    plan_reservation_active = False
                    plan_reserved_tokens = 0

            if not lock_released:
                await self.rl.release(bundle_id=rl_bundle_id, subject_id=self.subj, lock_id=lock_id)
                lock_released = True

            lane = "paid"
            paid_policy = _build_paid_policy()
            admit = await _admit_paid(paid_policy)
            effective_policy = paid_policy
            lock_released = False

            if not admit.allowed:
                await _econ_fail(
                    code="paid_admit_denied_after_switch",
                    title="Rate limit exceeded",
                    message=f"Paid lane admit denied after switch: {admit.reason or 'unknown'}",
                    event_type="rate_limit.denied",
                    data={
                        "reason": admit.reason,
                        "bundle_id": bundle_id,
                        "subject_id": self.subj,
                        "user_type": user_type,
                        "snapshot": admit.snapshot,
                        "lane": "paid",
                        "switch_reason": switch_reason,
                    },
                )

        if (not budget_bypass) and funding_available_usd <= 0.0:
            if not personal_can_pay_turn or not allow_paid_lane_fallback:
                user_budget_tokens_int = int(user_budget_tokens or 0)
                if funding_source == "none":
                    await _econ_fail(
                        code="no_funding_source",
                        title="No funding source",
                        message="No funding source available for this user type (subscription inactive and project funding not allowed).",
                        event_type="rate_limit.no_funding",
                        data={
                            "reason": "no_funding_source",
                            "bundle_id": bundle_id,
                            "subject_id": self.subj,
                            "user_type": user_type,
                            "funding_source": funding_source,
                            "user_budget_tokens": user_budget_tokens_int,
                            "user_budget_usd": user_budget_tokens_int * usd_per_token,
                        },
                    )
                await _econ_fail(
                    code=f"{funding_source}_budget_exhausted",
                    title=f"{funding_label.title()} exhausted",
                    message=f"{funding_label.title()} exhausted and user has insufficient personal credits (available_usd={funding_available_usd:.2f}, user_budget_tokens={user_budget_tokens}).",
                    event_type="rate_limit.project_exhausted" if funding_source == "project" else "rate_limit.subscription_exhausted",
                    data={
                        "reason": f"{funding_source}_budget_exhausted",
                        "bundle_id": bundle_id,
                        "subject_id": self.subj,
                        "user_type": user_type,
                        "funding_source": funding_source,
                        "funding_available_usd": funding_available_usd,
                        "funding_budget": funding_budget,
                        "user_budget_tokens": user_budget_tokens_int,
                        "user_budget_usd": user_budget_tokens_int * usd_per_token,
                        "min_tokens_required": int(est_turn_tokens),
                        "min_usd_required": int(est_turn_tokens) * usd_per_token,
                        "tokens_short": max(0, int(est_turn_tokens) - user_budget_tokens_int),
                        "usd_short": max(0.0, (int(est_turn_tokens) - user_budget_tokens_int) * usd_per_token),
                        "has_personal_budget": bool(plan_balance and plan_balance.has_lifetime_budget()),
                        "min_user_tokens": int(est_turn_tokens),
                    },
                )
            lane = "paid"
            paid_policy = _build_paid_policy()
            admit = await _admit_paid(paid_policy)
            lock_released = False
        else:
            plan_admit = await _admit_plan()
            lock_released = False
            _log("admit.plan", "Plan admit result (legacy plan lane)", allowed=plan_admit.allowed, reason=plan_admit.reason, snapshot=plan_admit.snapshot)

            if plan_admit.allowed:
                lane = "plan"
                admit = plan_admit
            else:
                if not personal_can_pay_turn or not allow_paid_lane_fallback:
                    insight = compute_quota_insight(
                        policy=base_policy,
                        snapshot=plan_admit.snapshot,
                        reason=plan_admit.reason,
                        used_plan_override=plan_admit.used_plan_override,
                        user_budget_tokens=user_budget_tokens,
                    )
                    payload = dataclasses.asdict(insight)
                    retry_after_hours = None
                    if insight.retry_after_sec:
                        retry_after_hours = math.ceil(int(insight.retry_after_sec) / 3600)
                    payload["retry_after_hours"] = retry_after_hours

                    await _econ_fail(
                        code="rate_limited",
                        title="Rate limit exceeded",
                        message=f"Rate limited: {plan_admit.reason or 'unknown'}",
                        event_type="rate_limit.denied",
                        data={
                            "reason": plan_admit.reason,
                            "bundle_id": bundle_id,
                            "subject_id": self.subj,
                            "user_type": user_type,
                            "snapshot": plan_admit.snapshot,
                            "rate_limit": payload,
                            "lane": "deny",
                        },
                    )

                lane = "paid"
                paid_policy = _build_paid_policy()
                admit = await _admit_paid(paid_policy)
                lock_released = False

        _log("admit.final", "Final admit", lane=lane, allowed=admit.allowed, reason=admit.reason, snapshot=admit.snapshot)

        if not admit.allowed:
            effective_policy_for_insight = paid_policy if lane == "paid" and paid_policy else base_policy
            insight = compute_quota_insight(
                policy=effective_policy_for_insight,
                snapshot=admit.snapshot,
                reason=admit.reason,
                used_plan_override=admit.used_plan_override,
                user_budget_tokens=user_budget_tokens,
            )
            payload = dataclasses.asdict(insight)
            retry_after_hours = None
            if insight.retry_after_sec:
                retry_after_hours = math.ceil(int(insight.retry_after_sec) / 3600)
            payload["retry_after_hours"] = retry_after_hours

            await _econ_fail(
                code="rate_limited",
                title="Rate limit exceeded",
                message=f"Rate limited: {admit.reason or 'unknown'}",
                event_type="rate_limit.denied",
                data={
                    "reason": admit.reason,
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "snapshot": admit.snapshot,
                    "rate_limit": payload,
                    "lane": "deny",
                },
            )

        if lane == "paid":
            effective_policy = paid_policy
            _log("policy.effective", "Using PAID effective policy", effective_policy=dataclasses.asdict(effective_policy))
        else:
            if admit.used_plan_override and admit.effective_policy:
                effective_policy = QuotaPolicy(**(admit.effective_policy or {}))
                _log("policy.effective", "Using effective policy from RL (plan override applied)", effective_policy=dataclasses.asdict(effective_policy))
            else:
                if plan_balance and plan_balance.plan_override_is_active():
                    effective_policy = _merge_policy_with_plan_override(base_policy, plan_balance)
                else:
                    effective_policy = base_policy
                _log("policy.effective", "Using merged policy", effective_policy=dataclasses.asdict(effective_policy))

        insight: QuotaInsight = compute_quota_insight(
            policy=effective_policy,
            snapshot=admit.snapshot,
            reason=admit.reason,
            used_plan_override=admit.used_plan_override,
            user_budget_tokens=user_budget_tokens,
        )
        _log("insight", "Computed quota insight", lane=lane, insight=dataclasses.asdict(insight))

        if (
            (insight.messages_remaining is not None and insight.messages_remaining == 1)
            or (insight.total_token_remaining is not None and insight.total_token_remaining < int(est_turn_tokens))
        ):
            await _emit_event(
                type="rate_limit.warning",
                status="running",
                title="Approaching quota",
                data={
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "snapshot": admit.snapshot,
                    "rate_limit": dataclasses.asdict(insight),
                    "lane": lane,
                },
            )

        app_reservation_id: UUID | None = None
        app_reserved_usd: float = 0.0
        app_reservation_active: bool = False

        personal_reservation_id: str | None = None
        personal_reserved_tokens: int = 0
        personal_reservation_active: bool = False

        plan_reserved_tokens = 0
        plan_project_tokens_est = 0

        async def _cleanup_reservations(reason: str) -> None:
            try:
                await _release_quota_lock_if_held()
            except Exception as ex:
                _log("quota_lock", f"Failed to release quota_lock ({reason})", "WARN", error=str(ex))

            try:
                if app_reservation_active and app_reservation_id:
                    if not funding_limiter:
                        _log(
                            "reserve.app",
                            f"Skipped {funding_label} reservation release ({reason}); no funding limiter",
                            "WARN",
                            reservation_id=str(app_reservation_id),
                        )
                    elif funding_source == "subscription":
                        await funding_limiter.release_reservation(
                            reservation_id=app_reservation_id,
                            note=None,
                            project_budget=self.budget_limiter,
                        )
                    else:
                        await funding_limiter.release_reservation(reservation_id=app_reservation_id, note=None)
                    if funding_limiter:
                        _log(
                            "reserve.app",
                            f"Released {funding_label} reservation ({reason})",
                            reservation_id=str(app_reservation_id),
                        )
            except Exception as ex:
                _log("reserve.app", f"Failed to release {funding_label} reservation ({reason})", "WARN", error=str(ex))

            try:
                if personal_reservation_active and personal_reservation_id:
                    await self.cp_manager.user_credits_mgr.release_lifetime_token_reservation(
                        tenant=tenant, project=project, user_id=user_id,
                        reservation_id=personal_reservation_id,
                        reason=f"run: cleanup {reason}",
                    )
                    _log("reserve.personal", f"Released personal reservation ({reason})", reservation_id=personal_reservation_id)
            except Exception as ex:
                _log("reserve.personal", f"Failed to release personal reservation ({reason})", "WARN", error=str(ex))

            try:
                if plan_reservation_active and plan_reservation_id:
                    await self.rl.release_token_reservation(
                        bundle_id=rl_bundle_id, subject_id=self.subj,
                        reservation_id=plan_reservation_id,
                        now=plan_admit_now,
                    )
                    _log("rl.reserve", f"Released plan token reservation ({reason})", reservation_id=plan_reservation_id)
            except Exception as ex:
                _log("rl.reserve", f"Failed to release plan reservation ({reason})", "WARN", error=str(ex))

            try:
                if not lock_released:
                    await self.rl.release(bundle_id=rl_bundle_id, subject_id=self.subj, lock_id=lock_id)
                    _log("rl.release", f"Released lock ({reason})", lock_id=lock_id)
            except Exception as ex:
                _log("rl.release", f"Failed to release lock ({reason})", "WARN", error=str(ex))

        try:
            if lane == "plan":
                plan_limit, scope = effective_policy.effective_allowed_tokens()
                scope_for_lock = str(scope or "month")
                await _acquire_quota_lock_or_deny(scope=scope_for_lock)

                try:
                    plan_reserved_tokens = int(getattr(admit, "reserved_tokens", 0) or 0)
                    plan_reservation_id = getattr(admit, "reservation_id", None)
                    plan_reservation_active = (lane == "plan" and plan_reserved_tokens > 0 and plan_reservation_id is not None)

                    plan_project_tokens_est = int(plan_reserved_tokens)
                    if funding_source == "subscription":
                        plan_project_tokens_est = int(est_turn_tokens)
                        if has_wallet:
                            cap_usd = float(subscription_available_usd or 0.0)
                            if cap_usd < 0:
                                cap_usd = 0.0
                            if usd_per_token > 0:
                                cap_tokens = int(cap_usd / (usd_per_token * SAFETY_MARGIN))
                            else:
                                cap_tokens = 0
                            plan_project_tokens_est = min(int(est_turn_tokens), int(cap_tokens))
                    elif funding_source == "project" and project_budget:
                        od_lim = project_budget.get("overdraft_limit_usd")
                        if od_lim is not None:
                            cap_usd = float(project_budget.get("available_usd") or 0.0) + float(od_lim or 0.0)
                            if cap_usd < 0:
                                cap_usd = 0.0
                            if usd_per_token > 0:
                                cap_tokens = int(cap_usd / (usd_per_token * SAFETY_MARGIN))
                                plan_project_tokens_est = min(int(plan_project_tokens_est), int(cap_tokens))
                        # od_lim == None => unlimited, keep full plan_project_tokens_est
                    if funding_source == "subscription":
                        overflow_tokens_est = max(int(est_turn_tokens) - int(plan_project_tokens_est), 0)
                    else:
                        overflow_tokens_est = max(int(est_turn_tokens) - int(plan_project_tokens_est), 0)
                    if plan_limit is None:
                        plan_remaining = est_turn_tokens
                        tokens_spent_stat = f'Tokens spent from plan in this month: {int(admit.snapshot.get("tok_month", 0) or 0)}'
                    else:
                        tok_so_far = int(admit.snapshot.get(f"tok_{scope}", 0) or 0)
                        tokens_spent_stat = f"Tokens spent from plan in this {scope}: {tok_so_far}"
                        plan_remaining = max(int(plan_limit) - int(tok_so_far), 0)

                    _log(
                        "reserve.plan",
                        "Reservation plan (plan lane)",
                        est_turn_tokens=est_turn_tokens,
                        tokens_spent_stat=tokens_spent_stat,
                        plan_limit=plan_limit,
                        plan_remaining=plan_remaining,
                        plan_covered_tokens_est=plan_project_tokens_est,
                        overflow_tokens_est=overflow_tokens_est,
                        usd_per_token=usd_per_token,
                    )

                    if plan_project_tokens_est <= 0:
                        if budget_bypass:
                            _log(
                                "reserve.plan",
                                "Budget bypass: zero plan reservation; skipping paid switch",
                                "WARN",
                                est_turn_tokens=est_turn_tokens,
                                plan_reserved_tokens=plan_reserved_tokens,
                            )
                        elif not personal_can_pay_turn:
                            await _econ_fail(
                                code="plan_exhausted_no_personal",
                                title="Tier exhausted",
                                message="Tier exhausted and user cannot pay from personal credits.",
                                event_type="rate_limit.denied",
                                data={
                                    "reason": "plan_exhausted",
                                    "bundle_id": bundle_id,
                                    "subject_id": self.subj,
                                    "user_type": user_type,
                                    "snapshot": admit.snapshot,
                                    "lane": "deny",
                                },
                            )
                        elif funding_source == "subscription" and has_wallet:
                            await _emit_event(
                                type="rate_limit.lane_switch",
                                status="running",
                                title="Switching to paid lane (wallet funding)",
                                data={
                                    "reason": "subscription_budget_zero_for_turn",
                                    "bundle_id": bundle_id,
                                    "subject_id": self.subj,
                                    "user_type": user_type,
                                    "snapshot": admit.snapshot,
                                    "lane_from": "plan",
                                    "lane_to": "paid",
                                },
                            )
                            await _switch_plan_to_paid_or_die(switch_reason="subscription_budget_zero_for_turn")
                        elif allow_paid_lane_fallback:
                            await _emit_event(
                                type="rate_limit.lane_switch",
                                status="running",
                                title="Switching to personal credits",
                                data={
                                    "reason": "plan_tokens_exhausted_for_turn",
                                    "bundle_id": bundle_id,
                                    "subject_id": self.subj,
                                    "user_type": user_type,
                                    "snapshot": admit.snapshot,
                                    "lane_from": "plan",
                                    "lane_to": "paid",
                                },
                            )

                            await _switch_plan_to_paid_or_die(switch_reason="plan_tokens_exhausted_for_turn")
                        else:
                            _log(
                                "reserve.plan",
                                "Plan budget exhausted; using wallet for overflow (no paid-lane switch)",
                                "WARN",
                                est_turn_tokens=est_turn_tokens,
                                plan_reserved_tokens=plan_reserved_tokens,
                            )
                    else:
                        if not budget_bypass:
                            app_reserved_usd = float(plan_project_tokens_est) * float(usd_per_token) * SAFETY_MARGIN
                            app_reservation_id = uuid4()

                            try:
                                reserve_kwargs = dict(
                                    reservation_id=app_reservation_id,
                                    bundle_id=bundle_id,
                                    provider=None,
                                    request_id=turn_id,
                                    amount_usd=float(app_reserved_usd),
                                    ttl_sec=900,
                                    notes=f"plan reserve: est_turn={est_turn_tokens}, plan_cover_est={plan_project_tokens_est}, ref=anthropic/claude-sonnet-4-5-20250929",
                                )
                                if funding_source == "project":
                                    reserve_kwargs["user_id"] = user_id

                                rr = await funding_limiter.reserve(**reserve_kwargs)
                                app_reservation_active = True
                                _log(
                                    "reserve.app",
                                    f"Reserved {funding_label} (plan lane)",
                                    reservation_id=str(rr.reservation_id),
                                    app_reserved_usd=rr.reserved_usd,
                                    expires_at=rr.expires_at,
                                    snapshot=dataclasses.asdict(rr.snapshot),
                                )
                            except BudgetInsufficientFunds as e:
                                _log("reserve.app", f"{funding_label.title()} reservation denied", "WARN", error=str(e), app_reserved_usd=app_reserved_usd)

                                if not personal_can_pay_turn:
                                    await _econ_fail(
                                        code=f"{funding_source}_budget_reservation_failed_no_personal",
                                        title=f"Insufficient {funding_label}",
                                        message=f"{funding_label.title()} cannot reserve plan funds and user cannot pay.",
                                        event_type="rate_limit.project_exhausted" if funding_source == "project" else "rate_limit.subscription_exhausted",
                                        data={
                                            "reason": f"{funding_source}_budget_reservation_failed",
                                            "bundle_id": bundle_id,
                                            "subject_id": self.subj,
                                            "user_type": user_type,
                                            "funding_source": funding_source,
                                            "funding_budget": funding_budget,
                                            "app_reserved_usd": app_reserved_usd,
                                            "user_budget_tokens": user_budget_tokens,
                                        },
                                    )

                                if funding_source == "subscription" and has_wallet:
                                    await _emit_event(
                                        type="rate_limit.lane_switch",
                                        status="running",
                                        title="Switching to paid lane (wallet funding)",
                                        data={
                                            "reason": "subscription_reservation_failed",
                                            "bundle_id": bundle_id,
                                            "subject_id": self.subj,
                                            "user_type": user_type,
                                            "snapshot": admit.snapshot,
                                            "lane_from": "plan",
                                            "lane_to": "paid",
                                        },
                                    )
                                    await _switch_plan_to_paid_or_die(switch_reason="subscription_reservation_failed")
                                elif allow_paid_lane_fallback:
                                    await _emit_event(
                                        type="rate_limit.lane_switch",
                                        status="running",
                                        title="Switching to personal credits",
                                        data={
                                            "reason": "app_budget_reservation_failed",
                                            "bundle_id": bundle_id,
                                            "subject_id": self.subj,
                                            "user_type": user_type,
                                            "snapshot": admit.snapshot,
                                            "lane_from": "plan",
                                            "lane_to": "paid",
                                        },
                                    )
                                    await _switch_plan_to_paid_or_die(switch_reason="app_budget_reservation_failed")
                                else:
                                    # Fall back to wallet-only for this turn
                                    plan_project_tokens_est = 0
                                    app_reserved_usd = 0.0

                    if lane == "plan" and (funding_source != "subscription" or has_wallet):
                        overflow_tokens_est = max(int(est_turn_tokens) - int(plan_project_tokens_est), 0)
                        if overflow_tokens_est > 0:
                            ok = await self.cp_manager.user_credits_mgr.reserve_lifetime_tokens(
                                tenant=tenant,
                                project=project,
                                user_id=user_id,
                                reservation_id=turn_id,
                                tokens=int(overflow_tokens_est),
                                ttl_sec=900,
                                bundle_id=bundle_id,
                                notes=f"auto-reserve: lane=plan, overflow={overflow_tokens_est}, est_turn={est_turn_tokens}",
                            )
                            if not ok:
                                await _econ_fail(
                                    code="personal_reservation_failed_plan",
                                    title="Insufficient personal credits",
                                    message="Insufficient personal credits to cover overflow.",
                                    event_type="rate_limit.denied",
                                    data={
                                        "reason": "personal_reservation_failed",
                                        "bundle_id": bundle_id,
                                        "subject_id": self.subj,
                                        "user_type": user_type,
                                        "tokens_required": int(overflow_tokens_est),
                                        "lane": lane,
                                    },
                                )
                            personal_reservation_id = turn_id
                            personal_reserved_tokens = int(overflow_tokens_est)
                            personal_reservation_active = True
                            _log("reserve.personal", "Reserved personal overflow tokens", reservation_id=turn_id, tokens_reserved=personal_reserved_tokens)
                finally:
                    try:
                        await _release_quota_lock_if_held()
                    except Exception as ex:
                        _log("quota_lock", "Failed to release quota_lock", "WARN", error=str(ex))

            if lane == "paid" and not budget_bypass:
                if has_active_subscription and funding_limiter:
                    app_reserved_usd = float(est_turn_tokens) * float(usd_per_token) * SAFETY_MARGIN
                    app_reservation_id = uuid4()
                    try:
                        rr = await funding_limiter.reserve(
                            reservation_id=app_reservation_id,
                            bundle_id=bundle_id,
                            provider=None,
                            request_id=turn_id,
                            amount_usd=float(app_reserved_usd),
                            ttl_sec=900,
                            notes=f"paid reserve: est_turn={est_turn_tokens}, ref=anthropic/claude-sonnet-4-5-20250929",
                        )
                        app_reservation_active = True
                        paid_funding_source = "subscription"
                        _log(
                            "reserve.app",
                            "Reserved subscription balance (paid lane)",
                            reservation_id=str(rr.reservation_id),
                            app_reserved_usd=rr.reserved_usd,
                            expires_at=rr.expires_at,
                            snapshot=dataclasses.asdict(rr.snapshot),
                        )
                    except BudgetInsufficientFunds as e:
                        _log(
                            "reserve.app",
                            "Subscription reservation denied (paid lane)",
                            "WARN",
                            error=str(e),
                            app_reserved_usd=app_reserved_usd,
                        )
                        if not wallet_can_pay_turn:
                            await _econ_fail(
                                code="subscription_reservation_failed_paid",
                                title="Insufficient subscription balance",
                                message="Subscription balance cannot cover this request and no wallet credits are available.",
                                event_type="rate_limit.subscription_exhausted",
                                data={
                                    "reason": "subscription_reservation_failed",
                                    "bundle_id": bundle_id,
                                    "subject_id": self.subj,
                                    "user_type": user_type,
                                    "tokens_required": int(est_turn_tokens),
                                    "lane": lane,
                                },
                            )

                if not app_reservation_active:
                    if not (plan_balance and plan_balance.has_lifetime_budget()):
                        await _econ_fail(
                            code="paid_lane_requires_personal_budget",
                            title="Insufficient personal credits",
                            message="Paid lane requires wallet credits.",
                            event_type="rate_limit.denied",
                            data={
                                "reason": "no_personal_budget",
                                "bundle_id": bundle_id,
                                "subject_id": self.subj,
                                "user_type": user_type,
                                "lane": lane,
                            },
                        )

                    ok = await self.cp_manager.user_credits_mgr.reserve_lifetime_tokens(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        reservation_id=turn_id,
                        tokens=int(est_turn_tokens),
                        ttl_sec=900,
                        bundle_id=bundle_id,
                        notes=f"auto-reserve paid: lane=paid, est_turn={est_turn_tokens}",
                    )
                    if not ok:
                        await _econ_fail(
                            code="personal_reservation_failed_paid",
                            title="Insufficient personal credits",
                            message="Insufficient wallet credits to run this request.",
                            event_type="rate_limit.denied",
                            data={
                                "reason": "personal_reservation_failed",
                                "bundle_id": bundle_id,
                                "subject_id": self.subj,
                                "user_type": user_type,
                                "tokens_required": int(est_turn_tokens),
                                "lane": lane,
                            },
                        )

                    personal_reservation_id = turn_id
                    personal_reserved_tokens = int(est_turn_tokens)
                    personal_reservation_active = True
                    paid_funding_source = "wallet"
                    _log(
                        "reserve.personal",
                        "Reserved personal tokens (paid lane)",
                        reservation_id=turn_id,
                        tokens_reserved=personal_reserved_tokens,
                    )
        except EconomicsLimitException:
            await _cleanup_reservations("pre_run_fail")
            raise
        except Exception as e:
            _log("error", "Exception during pre-run reservations", "ERROR", error=str(e))
            await _cleanup_reservations("pre_run_fail")
            raise

        econ_ctx = {
            "lane": lane,
            "admit": admit,
            "base_policy": base_policy,
            "effective_policy": effective_policy,
            "paid_policy": paid_policy,
            "plan_balance": plan_balance,
            "user_budget_tokens": user_budget_tokens,
            "budget_bypass": budget_bypass,
            "subscription": subscription.__dict__ if subscription else None,
            "subscription_budget": subscription_budget,
            "subscription_available_usd": subscription_available_usd,
            "subscription_period_key": subscription_period_key,
            "project_budget": project_budget,
            "project_available_usd": project_available_usd,
            "funding_source": funding_source,
            "funding_budget": funding_budget,
            "funding_available_usd": funding_available_usd,
            "funding_balance_usd": funding_balance_usd,
            "plan_reserved_tokens": plan_reserved_tokens,
            "plan_reservation_id": plan_reservation_id,
            "plan_reservation_active": plan_reservation_active,
            "app_reservation_id": app_reservation_id,
            "app_reservation_active": app_reservation_active,
            "personal_reservation_id": personal_reservation_id,
            "personal_reservation_active": personal_reservation_active,
            "lock_id": lock_id,
            "lock_released": lock_released,
            "plan_admit_now": plan_admit_now,
        }

        await self.pre_run_hook(state=state, econ_ctx=econ_ctx)

        result = None
        admit_snapshot_pre = dict(admit.snapshot or {})

        try:
            usage_from = datetime.utcnow().date().isoformat()
            _log("exec", "Invoking execute_core", lane=lane, usage_from=usage_from)
            _log("economics", "--- END PRE-RUN ECONOMICS ---")

            result = await self.execute_core(state=state, thread_id=thread_id, params=params)

            _log("economics", "--- START POST-RUN ECONOMICS ---")
            _log("exec", "execute_core completed", lane=lane)
            _log("accounting", "Applying accounting", lane=lane)
            ranked_tokens, cost_result = await self.run_accounting(
                tenant=tenant, project=project, user_id=user_id, user_type=user_type,
                thread_id=thread_id, turn_id=turn_id, usage_from=usage_from,
            )

            ranked_tokens = int(ranked_tokens or 0)
            cost_breakdown = cost_result.get("cost_breakdown") or []
            total_cost = float(cost_result.get("cost_total_usd") or 0.0)

            _log(
                "accounting",
                "Accounting applied",
                lane=lane,
                ranked_tokens=ranked_tokens,
                total_cost=total_cost,
                cost_breakdown=cost_breakdown,
            )

            now = datetime.utcnow().replace(tzinfo=timezone.utc)

            use_subscription_funding = (
                funding_source == "subscription"
                and not budget_bypass
                and not personal_reservation_active
                and (lane != "paid" or paid_funding_source == "subscription")
            )
            def _post_run_snapshot(snapshot: dict, *, ranked: int, reserved: int, lane_name: str) -> dict:
                post = dict(snapshot or {})
                for key in ("req_day", "req_month", "req_total"):
                    post[key] = int(post.get(key, 0) or 0) + 1
                if lane_name == "plan":
                    for key in ("tok_hour", "tok_day", "tok_month"):
                        base = int(post.get(key, 0) or 0)
                        post[key] = max(base - int(reserved or 0), 0) + int(ranked or 0)
                else:
                    for key in ("tok_hour", "tok_day", "tok_month"):
                        post[key] = int(post.get(key, 0) or 0) + int(ranked or 0)
                post["in_flight"] = max(int(post.get("in_flight", 0) or 0) - 1, 0)
                return post

            def _post_run_violations(policy: QuotaPolicy, snapshot: dict) -> list[str]:
                violations: list[str] = []
                def over(limit: Optional[int], used: int) -> bool:
                    return limit is not None and used > int(limit)
                if over(policy.requests_per_day, int(snapshot.get("req_day", 0) or 0)):
                    violations.append("requests_per_day")
                if over(policy.requests_per_month, int(snapshot.get("req_month", 0) or 0)):
                    violations.append("requests_per_month")
                if over(policy.total_requests, int(snapshot.get("req_total", 0) or 0)):
                    violations.append("total_requests")
                if over(policy.tokens_per_hour, int(snapshot.get("tok_hour", 0) or 0)):
                    violations.append("tokens_per_hour")
                if over(policy.tokens_per_day, int(snapshot.get("tok_day", 0) or 0)):
                    violations.append("tokens_per_day")
                if over(policy.tokens_per_month, int(snapshot.get("tok_month", 0) or 0)):
                    violations.append("tokens_per_month")
                return violations

            post_run_snapshot = None
            post_run_violations = []
            if effective_policy and not budget_bypass:
                post_run_snapshot = _post_run_snapshot(
                    admit_snapshot_pre,
                    ranked=int(ranked_tokens),
                    reserved=int(plan_reserved_tokens),
                    lane_name=lane,
                )
                post_run_violations = _post_run_violations(effective_policy, post_run_snapshot)
                try:
                    if getattr(effective_policy, "tokens_per_hour", None) is not None:
                        prefix = f"{self.rl.ns}:{rl_bundle_id}:{self.subj}:toks:hour:bucket"
                        tok_h_now, reset_at = await self.rl._rolling_hour_stats(
                            prefix,
                            now,
                            limit=getattr(effective_policy, "tokens_per_hour", None),
                            reserved=0,
                        )
                        post_run_snapshot["tok_hour"] = int(tok_h_now or 0)
                        if reset_at:
                            post_run_snapshot["tok_hour_reset_at"] = int(reset_at)
                except Exception as ex:
                    _log("rate_limit", "Failed to compute rolling-hour reset", "WARN", error=str(ex))

            if budget_bypass or use_subscription_funding:
                plan_covered_tokens = int(ranked_tokens)
            elif lane == "plan":
                plan_covered_tokens = min(int(ranked_tokens), int(plan_project_tokens_est))
            else:
                plan_covered_tokens = 0

            overflow_tokens = max(int(ranked_tokens) - int(plan_covered_tokens), 0)

            plan_covered_usd = 0.0
            overflow_usd = 0.0
            if ranked_tokens > 0 and total_cost > 0:
                plan_covered_usd = float(total_cost) * safe_frac(float(plan_covered_tokens), float(ranked_tokens))
                overflow_usd = float(total_cost) * safe_frac(float(overflow_tokens), float(ranked_tokens))

            if use_subscription_funding and total_cost > 0:
                plan_covered_usd = float(total_cost)
                overflow_usd = 0.0

            _log(
                "charge.split",
                "Computed actual split",
                ranked_tokens=ranked_tokens,
                plan_covered_tokens=plan_covered_tokens,
                overflow_tokens=overflow_tokens,
                total_cost=total_cost,
                plan_covered_usd=plan_covered_usd,
                overflow_usd=overflow_usd,
            )

            if use_subscription_funding:
                user_target_tokens = 0
            elif lane == "paid":
                user_target_tokens = int(ranked_tokens)
            else:
                if funding_source == "project" and not has_wallet:
                    user_target_tokens = 0
                else:
                    user_target_tokens = int(overflow_tokens)

            if (
                user_target_tokens > 0
                and personal_reservation_active
                and personal_reserved_tokens > 0
                and int(user_target_tokens) > int(personal_reserved_tokens)
            ):
                _log(
                    "reserve.personal",
                    "Actual personal spend exceeded reserved amount",
                    "WARN",
                    lane=lane,
                    user_target_tokens=int(user_target_tokens),
                    personal_reserved_tokens=int(personal_reserved_tokens),
                    overflow_tokens=int(user_target_tokens) - int(personal_reserved_tokens),
                )

            user_uncovered_tokens = 0
            if user_target_tokens > 0 and not budget_bypass:
                rid = str(personal_reservation_id or turn_id)
                try:
                    user_uncovered_tokens = await self.cp_manager.user_credits_mgr.commit_reserved_lifetime_tokens(
                        tenant=tenant, project=project, user_id=user_id,
                        reservation_id=rid,
                        tokens=int(user_target_tokens),
                    )
                finally:
                    if personal_reservation_active:
                        personal_reservation_active = False

            if personal_reservation_active and user_target_tokens <= 0 and personal_reservation_id:
                try:
                    await self.cp_manager.user_credits_mgr.release_lifetime_token_reservation(
                        tenant=tenant, project=project, user_id=user_id,
                        reservation_id=personal_reservation_id,
                        reason="run: no_user_spend",
                    )
                finally:
                    personal_reservation_active = False

            user_uncovered_tokens = int(user_uncovered_tokens or 0)
            user_uncovered_usd = 0.0
            if user_uncovered_tokens > 0 and ranked_tokens > 0 and total_cost > 0:
                user_uncovered_usd = float(total_cost) * safe_frac(float(user_uncovered_tokens), float(ranked_tokens))

            if user_uncovered_tokens > 0 and not budget_bypass:
                await _emit_event(
                    type="economics.user_underfunded_absorbed",
                    status="running",
                    title=f"Personal credits underfunded; absorbed by {funding_label}",
                    data={
                        "bundle_id": bundle_id,
                        "subject_id": self.subj,
                        "user_type": user_type,
                        "lane": lane,
                        "ranked_tokens": ranked_tokens,
                        "user_target_tokens": int(user_target_tokens),
                        "user_uncovered_tokens": int(user_uncovered_tokens),
                        "user_uncovered_usd": float(user_uncovered_usd),
                        "funding_source": funding_source,
                    },
                )
                _log(
                    "charge.user",
                    f"User underfunded post-fact; {funding_label} will absorb remainder",
                    "WARN",
                    lane=lane,
                    user_target_tokens=int(user_target_tokens),
                    user_uncovered_tokens=int(user_uncovered_tokens),
                    user_uncovered_usd=float(user_uncovered_usd),
                )

            extra_project_items: list[tuple[float, str]] = []

            if budget_bypass:
                app_spend_usd = float(total_cost)
                app_note = "post-run settle: admin bypass"
            elif use_subscription_funding:
                app_spend_usd = float(plan_covered_usd)
                app_note = "post-run settle: subscription_cost"
                if app_reservation_active and app_reserved_usd is not None and float(app_spend_usd) > float(app_reserved_usd):
                    over = float(app_spend_usd) - float(app_reserved_usd)
                    app_spend_usd = float(app_reserved_usd)
                    extra_project_items.append((float(over), "shortfall:subscription_overage"))
                if user_uncovered_usd > 0:
                    extra_project_items.append((float(user_uncovered_usd), "shortfall:wallet_subscription"))
            elif lane == "plan":
                app_spend_usd = float(plan_covered_usd)
                app_note = "post-run settle: plan_cost"
                if user_uncovered_usd > 0:
                    extra_project_items.append((float(user_uncovered_usd), "shortfall:wallet_plan"))
                if funding_source == "project" and not has_wallet and float(overflow_usd) > 0:
                    extra_project_items.append((float(overflow_usd), "shortfall:free_plan"))
            else:
                app_spend_usd = 0.0
                app_note = "shortfall:wallet_paid"
                if user_uncovered_usd > 0:
                    extra_project_items.append((float(user_uncovered_usd), "shortfall:wallet_paid"))

            if app_spend_usd > 0 or (app_reservation_active and app_reservation_id):
                if not funding_limiter:
                    await self.budget_limiter.force_project_spend(
                        spent_usd=float(app_spend_usd),
                        bundle_id=rl_bundle_id,
                        provider=None,
                        request_id=turn_id,
                        user_id=user_id,
                        note=f"{app_note}; no_funding_source",
                    )
                    _log(
                        "charge.app",
                        "Force-deducted project spend (no funding source configured)",
                        "WARN",
                        spent_usd=float(app_spend_usd),
                        plan_covered_usd=float(plan_covered_usd),
                        user_uncovered_usd=float(user_uncovered_usd),
                    )
                elif app_reservation_active and app_reservation_id:
                    if funding_source == "subscription":
                        await funding_limiter.commit_reserved_spend(
                            reservation_id=app_reservation_id,
                            spent_usd=float(app_spend_usd),
                            project_budget=self.budget_limiter,
                        )
                    else:
                        await funding_limiter.commit_reserved_spend(
                            reservation_id=app_reservation_id,
                            spent_usd=float(app_spend_usd),
                        )
                    app_reservation_active = False
                    _log(
                        "charge.app",
                        f"Committed {funding_label} reservation (post-run settle)",
                        reservation_id=str(app_reservation_id),
                        spent_usd=float(app_spend_usd),
                        plan_covered_usd=float(plan_covered_usd),
                        user_uncovered_usd=float(user_uncovered_usd),
                    )
                else:
                    if funding_source == "project":
                        await self.budget_limiter.force_project_spend(
                            spent_usd=float(app_spend_usd),
                            bundle_id=bundle_id,
                            provider=None,
                            request_id=turn_id,
                            user_id=user_id,
                            note=app_note,
                        )
                    else:
                        # Subscriptions never go negative. If we get here without a reservation,
                        # charge the project budget as an overage for auditability.
                        await self.budget_limiter.force_project_spend(
                            spent_usd=float(app_spend_usd),
                            bundle_id=bundle_id,
                            provider=None,
                            request_id=turn_id,
                            user_id=user_id,
                            note="shortfall:subscription_overage; no_reservation",
                        )
                        app_note = "shortfall:subscription_overage; no_reservation"
                    _log(
                        "charge.app",
                        "Force-deducted project spend (no reservation)",
                        spent_usd=float(app_spend_usd),
                        plan_covered_usd=float(plan_covered_usd),
                        user_uncovered_usd=float(user_uncovered_usd),
                    )

            for extra_spend_usd, extra_note in extra_project_items:
                if extra_spend_usd <= 0:
                    continue
                await self.budget_limiter.force_project_spend(
                    spent_usd=float(extra_spend_usd),
                    bundle_id=bundle_id,
                    provider=None,
                    request_id=turn_id,
                    user_id=user_id,
                    note=extra_note,
                )
                _log(
                    "charge.app",
                    "Project budget absorbed shortfall",
                    spent_usd=float(extra_spend_usd),
                    note=extra_note,
                    funding_source=funding_source,
                )

            if app_spend_usd > 0 and total_cost > 0:
                await _record_app_analytics_by_provider(
                    app_spend_usd=float(app_spend_usd),
                    total_cost_usd=float(total_cost),
                    cost_breakdown=cost_breakdown,
                    now=now,
                )
            if total_cost > 0:
                for extra_spend_usd, _extra_note in extra_project_items:
                    if extra_spend_usd <= 0:
                        continue
                    await _record_app_analytics_by_provider(
                        app_spend_usd=float(extra_spend_usd),
                        total_cost_usd=float(total_cost),
                        cost_breakdown=cost_breakdown,
                        now=now,
                    )

            if not lock_released:
                tokens_to_commit = int(ranked_tokens) if ranked_tokens > 0 else int(plan_covered_tokens)
                if lane == "plan":
                    await self.rl.commit_with_reservation(
                        bundle_id=rl_bundle_id,
                        subject_id=self.subj,
                        tokens=tokens_to_commit,
                        lock_id=lock_id,
                        reservation_id=plan_reservation_id,
                        now=plan_admit_now,
                        inc_request=1,
                    )
                    lock_released = True
                    plan_reservation_active = False
                    plan_reservation_id = None
                    plan_reserved_tokens = 0
                    _log(
                        "rl.commit",
                        "RL committed (actual tokens) and lock released (reservation finalized)",
                        tokens=tokens_to_commit,
                    )
                elif lane == "paid":
                    await self.rl.commit_with_reservation(
                        bundle_id=rl_bundle_id,
                        subject_id=self.subj,
                        tokens=tokens_to_commit,
                        lock_id=lock_id,
                        reservation_id=None,
                        now=plan_admit_now,
                        inc_request=1,
                    )
                    lock_released = True
                    _log(
                        "rl.commit",
                        "RL committed (paid lane)",
                        tokens=tokens_to_commit,
                    )
                else:
                    await self.rl.release(bundle_id=rl_bundle_id, subject_id=self.subj, lock_id=lock_id)
                    lock_released = True
                    _log("rl.release", "RL lock released", lane=lane)

            if post_run_violations and not budget_bypass:
                post_reason = "|".join(post_run_violations)
                post_insight = compute_quota_insight(
                    policy=effective_policy,
                    snapshot=post_run_snapshot or {},
                    reason=post_reason,
                    used_plan_override=admit.used_plan_override if admit else False,
                    user_budget_tokens=user_budget_tokens,
                    now=now,
                )
                payload = dataclasses.asdict(post_insight)
                payload.update({
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "lane": lane,
                    "ranked_tokens": int(ranked_tokens),
                    "snapshot": post_run_snapshot,
                    "reason": post_reason,
                })
                await _emit_analytics_event(
                    type="analytics.rate_limit.post_run_exceeded",
                    status="completed",
                    title="Post-run limit exceeded",
                    data=payload,
                )
                await _econ_fail(
                    code="post_run_limit_exceeded",
                    title="Limit exceeded",
                    message="Request exceeded your plan limit after completion. Please try again later.",
                    event_type="rate_limit.post_run_exceeded",
                    data=payload,
                )

        except EconomicsLimitException:
            raise

        except Exception as e:
            _log("error", "Exception in run()", "ERROR", error=str(e))
            raise

        finally:
            await _cleanup_reservations("finally")

            try:
                if acct is not None:
                    await acct.clear_turn_events(tenant=tenant, project=project, conversation_id=thread_id, turn_id=turn_id)
                    _log("cleanup", "Cleared accounting turn cache", tenant=tenant, project=project, thread_id=thread_id, turn_id=turn_id)
            except Exception as e:
                _log("cleanup", "Failed to clear accounting turn cache", "WARN", error=str(e))

        await self.post_run_hook(state=state, result=result, econ_ctx=econ_ctx)
        _log("done", "run() completed successfully", lane=lane)
        _log("economics", "--- END POST-RUN ECONOMICS ---")
        return self.project_app_state(result)

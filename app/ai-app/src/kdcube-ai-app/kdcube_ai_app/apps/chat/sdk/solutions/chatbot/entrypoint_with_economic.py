# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/sdk/solutions/chatbot/entrypoint_with_economics.py

from __future__ import annotations

from typing import Any, Optional, Dict

import asyncio
import dataclasses
import inspect
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4, UUID

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.infra.economics.defaults import DEFAULT_QUOTA_POLICIES
from kdcube_ai_app.apps.chat.sdk.infra.economics.plan_resolution import (
    resolve_plan_id,
    subscription_is_active,
    subscription_provides_plan_budget,
)
from kdcube_ai_app.apps.chat.sdk.config_scopes import economics_reservation_default
from kdcube_ai_app.infra.plugin.bundle_loader import on_reactive_event
from kdcube_ai_app.infra.service_hub.inventory import Config, _mid
from kdcube_ai_app.apps.chat.sdk.infra.economics.events_resources import (
    msg_denied_quota_reset,
    msg_denied_quota_insufficient_for_request,
    MSG_DENIED_LOCK_TIMEOUT,
    MSG_DENIED_CONCURRENCY,
    MSG_DENIED_TOKEN_LIMIT,
    MSG_DENIED_REQUEST_LIMIT,
    MSG_DENIED_GENERIC,
    msg_warning_last_msg_reset,
    MSG_WARNING_LAST_MSG_SOON,
    MSG_WARNING_ONE_REQUEST_REMAINING,
    msg_warning_low_tokens,
    MSG_WARNING_APPROACHING,
    msg_warning_approaching_approx,
    MSG_NO_FUNDING,
    MSG_SUBSCRIPTION_EXHAUSTED,
    MSG_PROJECT_EXHAUSTED,
)


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
        comm_context: ExternalEventPayload = None,
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

        self._bind_economics_runtime()

    def _bind_economics_runtime(self) -> None:
        self.cp_manager = None
        self.rl = None
        self.budget_limiter = None

        if self.redis is None and self.pg_pool is None:
            return

        from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import UserEconomicsRateLimiter
        from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter

        self.cp_manager = ControlPlaneManager(
            pg_pool=self.pg_pool,
            redis=self.redis,
            cache_ttl=60,
            plan_balance_cache_ttl=10,
        )
        from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import RLMonthAnchorStore
        self.rl = UserEconomicsRateLimiter(
            self.redis,
            user_balance_snapshot_mgr=self.cp_manager.plan_balance_snapshot_mgr,
            rl_anchor_store=RLMonthAnchorStore(self.pg_pool),
        )
        self.budget_limiter = ProjectBudgetLimiter(
            redis=self.redis,
            pg_pool=self.pg_pool,
            tenant=self.settings.TENANT,
            project=self.settings.PROJECT,
        )

    def rebind_request_context(
        self,
        *,
        comm_context: Optional[ExternalEventPayload] = None,
        pg_pool: Any = None,
        redis: Any = None,
    ) -> None:
        super().rebind_request_context(
            comm_context=comm_context,
            pg_pool=pg_pool,
            redis=redis,
        )
        if pg_pool is not None or redis is not None:
            self._bind_economics_runtime()

    async def ensure_policies_initialized(self):
        """
        Deprecated no-op. Default economics is now seeded at deploy time by the
        postgres-setup job from the economics.yaml descriptor (see
        docs/economics/economics-descriptor-seeding-README.md), not from bundle
        runtime. Kept as a shim so existing bundle callers do not break.
        """
        self._policies_initialized = True
        return True

    @property
    def app_quota_policies(self):
        """
        Platform default quota policies for the four baked-in plans
        (anonymous/free/wallet/admin). Sourced from the shared defaults
        module — the single source of truth shared with the deploy-time seeder.
        Subclasses may still override. Used as a defensive runtime fallback and
        by the non-chat enforcement engine.
        """
        return dict(DEFAULT_QUOTA_POLICIES)

    # ------------------------------------------------------------------ #
    # Economics-guarded embedding facade (feature-neutral).
    #
    # The build logic lives in solutions/search_service/ as plain
    # functions over `entrypoint`. This base only exposes a one-line delegator
    # so any economics entrypoint — memory, canvas, news, … — can call
    # search_model_service() without touching feature-specific code.
    # ------------------------------------------------------------------ #

    def search_model_service(self, *, flow: str):
        """Model-service facade for searchable components.

        Components receive this single dependency. Query and document embeddings
        are guarded and settled by the facade; query callers may degrade to
        lexical search, while index/write callers should let failures propagate.
        """
        from kdcube_ai_app.apps.chat.sdk.solutions.search_service import make_search_model_service

        return make_search_model_service(self, flow=flow)

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        raise NotImplementedError("execute_core() must be implemented by subclasses")

    async def pre_run_hook(self, *, state: Dict[str, Any], econ_ctx: Dict[str, Any]) -> None:
        await super().pre_run_hook(state=state)

    async def post_run_hook(self, *, state: Dict[str, Any], result: Dict[str, Any], econ_ctx: Dict[str, Any]) -> None:
        return None

    @staticmethod
    def _callable_accepts_kwarg(fn: Any, name: str) -> bool:
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return True
        return name in params or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

    async def _invoke_pre_run_hook(self, *, state: Dict[str, Any], econ_ctx: Dict[str, Any]) -> None:
        kwargs: Dict[str, Any] = {"state": state}
        if self._callable_accepts_kwarg(self.pre_run_hook, "econ_ctx"):
            kwargs["econ_ctx"] = econ_ctx
        await self.pre_run_hook(**kwargs)

    async def _invoke_post_run_hook(
        self,
        *,
        state: Dict[str, Any],
        result: Dict[str, Any],
        econ_ctx: Dict[str, Any],
    ) -> None:
        kwargs: Dict[str, Any] = {"state": state, "result": result}
        if self._callable_accepts_kwarg(self.post_run_hook, "econ_ctx"):
            kwargs["econ_ctx"] = econ_ctx
        await self.post_run_hook(**kwargs)

    def project_budget_allowed_for_plan(
        self,
        *,
        user_type: str,
        plan_id: Optional[str],
        plan_source: Optional[str],
        has_wallet: bool,
        has_active_subscription: bool,
    ) -> bool:
        """
        Return whether the current plan lane may use project budget.

        Runtime user type is not a plan identifier. A known user can be routed
        as paid because they have wallet credits, while still having a
        role/default plan lane backed by project budget. Anonymous traffic has
        no project-backed plan lane by default.
        """
        del plan_id, plan_source
        if has_active_subscription:
            return False
        if has_wallet and not self.wallet_users_use_project_budget_first():
            return False
        return str(user_type or "").lower() != "anonymous"

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

    @on_reactive_event
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
        from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import bind_economics_scope
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import _merge_policy_with_plan_override
        from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds
        from kdcube_ai_app.apps.chat.sdk.infra.economics.settlement_allocation import (
            PlanWalletSettlementInput,
            allocate_plan_wallet_settlement,
        )
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

        def _policy_for_insight(*, admit_result: AdmitResult | None, fallback_policy: QuotaPolicy) -> QuotaPolicy:
            if admit_result and getattr(admit_result, "used_plan_override", False) and getattr(admit_result, "effective_policy", None):
                return QuotaPolicy(**(admit_result.effective_policy or {}))
            if fallback_policy is base_policy and plan_balance and plan_balance.plan_override_is_active():
                return _merge_policy_with_plan_override(base_policy, plan_balance)
            return fallback_policy

        def _format_reset_time(
            *,
            retry_after_sec: int,
            now: Optional[datetime] = None,
            user_timezone: Optional[str] = None,
        ) -> str:
            try:
                tz = ZoneInfo(user_timezone) if user_timezone else timezone.utc
            except ZoneInfoNotFoundError:
                tz = timezone.utc
            base = (now or datetime.now(timezone.utc)).astimezone(tz)
            reset_at = base + timedelta(seconds=retry_after_sec)
            time_str = reset_at.strftime("%-I:%M %p")
            if reset_at.date() == base.date():
                return f"today at {time_str}"
            tomorrow = (base + timedelta(days=1)).date()
            if reset_at.date() == tomorrow:
                return f"tomorrow at {time_str}"
            date_str = reset_at.strftime("%B %-d")
            return f"on {date_str} at {time_str}"

        def _build_user_message(
            *,
            reason: Optional[str],
            reset_text: Optional[str],
            needed_tokens: Optional[int] = None,
            remaining_tokens: Optional[int] = None,
        ) -> str:
            if reset_text:
                if (
                    needed_tokens is not None
                    and remaining_tokens is not None
                    and remaining_tokens > 0
                    and remaining_tokens < needed_tokens
                ):
                    needed_k = max(1, needed_tokens // 1000)
                    remaining_k = max(0, remaining_tokens // 1000)
                    return msg_denied_quota_insufficient_for_request(needed_k, remaining_k, reset_text)
                return msg_denied_quota_reset(reset_text)
            if reason == "quota_lock_timeout":
                return MSG_DENIED_LOCK_TIMEOUT
            if reason in ("concurrency", "max_concurrent") or (reason and "concurrent" in reason):
                return MSG_DENIED_CONCURRENCY
            if reason and "token" in reason:
                return MSG_DENIED_TOKEN_LIMIT
            if reason and "request" in reason:
                return MSG_DENIED_REQUEST_LIMIT
            return MSG_DENIED_GENERIC

        def _retry_after_for_turn_shortfall(
            *,
            policy: QuotaPolicy,
            snapshot: dict | None,
            needed_tokens: int,
            now: Optional[datetime] = None,
        ) -> tuple[Optional[int], Optional[str]]:
            if not snapshot or int(needed_tokens or 0) <= 0:
                return None, None

            now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
            now_ts = int(now.timestamp())
            candidates: list[tuple[str, int]] = []

            def _ttl_hour() -> int:
                reset_at = int(snapshot.get("tok_hour_reset_at") or 0)
                if reset_at > now_ts:
                    return max(reset_at - now_ts, 0)
                end = datetime(now.year, now.month, now.day, now.hour, tzinfo=timezone.utc) + timedelta(hours=1)
                return max(int(end.timestamp()) - now_ts, 0)

            def _ttl_day() -> int:
                reset_at = int(snapshot.get("day_reset_at") or 0)
                if reset_at > now_ts:
                    return max(reset_at - now_ts, 0)
                return 24 * 60 * 60  # fallback: 24 hours from now

            def _ttl_month() -> int:
                reset_at = int(snapshot.get("month_reset_at") or 0)
                if reset_at > now_ts:
                    return max(reset_at - now_ts, 0)
                if now.month == 12:
                    end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
                else:
                    end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
                return max(int(end.timestamp()) - now_ts, 0)

            tok_hour = int(snapshot.get("tok_hour", 0) or 0)
            tok_day = int(snapshot.get("tok_day", 0) or 0)
            tok_month = int(snapshot.get("tok_month", 0) or 0)

            if policy.tokens_per_hour is not None:
                rem = max(int(policy.tokens_per_hour) - tok_hour, 0)
                if rem < int(needed_tokens):
                    candidates.append(("hour", _ttl_hour()))
            if policy.tokens_per_day is not None:
                rem = max(int(policy.tokens_per_day) - tok_day, 0)
                if rem < int(needed_tokens):
                    candidates.append(("day", _ttl_day()))
            if policy.tokens_per_month is not None:
                rem = max(int(policy.tokens_per_month) - tok_month, 0)
                if rem < int(needed_tokens):
                    candidates.append(("month", _ttl_month()))

            if not candidates:
                return None, None

            scope, ttl = max(candidates, key=lambda item: item[1])
            return ttl, scope

        def _build_rate_limit_payload(
            *,
            policy: QuotaPolicy,
            snapshot: dict | None,
            reason: Optional[str],
            used_plan_override: bool = False,
            needed_tokens: Optional[int] = None,
            remaining_tokens: Optional[int] = None,
            now: Optional[datetime] = None,
        ) -> dict:
            insight = compute_quota_insight(
                policy=policy,
                snapshot=snapshot or {},
                reason=reason,
                used_plan_override=used_plan_override,
                user_budget_tokens=user_budget_tokens,
                now=now,
            )
            payload = dataclasses.asdict(insight)

            retry_after_sec = payload.get("retry_after_sec")
            retry_scope = payload.get("retry_scope")
            if (not retry_after_sec) and needed_tokens and int(needed_tokens) > 0:
                retry_after_sec, retry_scope = _retry_after_for_turn_shortfall(
                    policy=policy,
                    snapshot=snapshot or {},
                    needed_tokens=int(needed_tokens),
                    now=now,
                )
                payload["retry_after_sec"] = retry_after_sec
                payload["retry_scope"] = retry_scope

            retry_after_hours = None
            reset_text = None
            if retry_after_sec:
                retry_after_hours = math.ceil(int(retry_after_sec) / 3600)
                reset_text = _format_reset_time(
                    retry_after_sec=int(retry_after_sec),
                    now=now,
                    user_timezone=getattr(getattr(getattr(self, "comm_context", None), "user", None), "timezone", None),
                )
            payload["retry_after_hours"] = retry_after_hours
            payload["reset_text"] = reset_text
            payload["user_message"] = _build_user_message(
                reason=reason,
                reset_text=reset_text,
                needed_tokens=int(needed_tokens) if needed_tokens is not None else None,
                remaining_tokens=remaining_tokens if remaining_tokens is not None else insight.total_token_remaining,
            )
            payload["notification_type"] = "error"
            if needed_tokens is not None:
                payload["needed_tokens"] = int(needed_tokens)
            return payload

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

        # Redis mechanics + spin-wait live in the shared funding_flow QuotaLock; run()
        # owns the redis-absent gate, the denial it raises on timeout, and its logs.
        from kdcube_ai_app.apps.chat.sdk.infra.economics.quota_lock import (
            QuotaLock, quota_lock_key,
        )
        _quota_lock = QuotaLock(self.redis)

        async def _acquire_quota_lock_or_deny(*, scope: str) -> None:
            if self.redis is None:
                _log("quota_lock", "Redis unavailable; quota_lock disabled", "WARN")
                return
            key = quota_lock_key(tenant, project, user_id, scope, rl_bundle_id)
            ttl_sec = 60
            if await _quota_lock.acquire_blocking(key, ttl_sec=ttl_sec, wait_total_sec=5.0):
                _log("quota_lock", "Acquired quota_lock", key=key, scope=scope, ttl_sec=ttl_sec)
                return
            _log("quota_lock", "Failed to acquire quota_lock within wait window", "WARN", key=key, scope=scope)
            await _econ_fail(
                code="quota_lock_timeout",
                title="System busy",
                message="Too many concurrent requests are planning quotas right now. Please retry.",
                event_type="rate_limit.denied",
                data={
                    "reason": "quota_lock_timeout",
                    "user_message": _build_user_message(reason="quota_lock_timeout", reset_text=None),
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "lane": "deny",
                    "scope": scope,
                },
            )

        async def _release_quota_lock_if_held() -> None:
            key = _quota_lock.key
            if await _quota_lock.release_if_held():
                _log("quota_lock", "Released quota_lock", key=key)

        self._turn_id = self._turn_id or _mid("turn")
        turn_id = self._turn_id
        lock_id = turn_id

        state = dict(getattr(self, "_app_state", {}) or {})
        state["turn_id"] = turn_id
        if "external_events" in params:
            state["external_events"] = params.get("external_events") or []

        tenant = state.get("tenant")
        project = state.get("project")
        user_id = state.get("user") or state.get("fingerprint")
        user_type = state.get("user_type") or "anonymous"
        agent_id = normalize_agent_id(
            state.get("agent_id")
            or getattr(getattr(getattr(self, "comm_context", None), "event", None), "agent_id", None),
            default=DEFAULT_REACT_AGENT_ID,
        )
        state["agent_id"] = agent_id
        try:
            acct.set_context(agent_id=agent_id)
        except Exception:
            pass
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
            event_count=len(state.get("external_events") or []),
        )
        if budget_bypass:
            _log("budget.bypass", "Budget bypass enabled for user type", user_type=role)

        input_text = ""
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
        # Chat reservation floor. Bundle prop economics.reservation.chat (scalar
        # USD; legacy economics.reservation_amount_dollars also accepted) wins:
        # a positive value enables the floor, a value <= 0 disables it (→ token
        # based estimate). If the bundle does not define it, inherit the platform
        # default from economics.yaml (which already returns positive-or-None).
        reservation_amount_dollars = None
        try:
            res_node = (econ_props.get("reservation") or {}).get("chat")
            if isinstance(res_node, dict):  # tolerate {amount: ...}
                res_node = res_node.get("amount")
            b_amount = res_node if res_node is not None else econ_props.get("reservation_amount_dollars")
            if b_amount is not None:
                b_amount = float(b_amount)
                reservation_amount_dollars = b_amount if b_amount > 0 else None
            else:
                reservation_amount_dollars = economics_reservation_default("chat")
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
        has_active_subscription = subscription_is_active(subscription, sub_now)
        # Only external subscriptions fund a separate guaranteed plan budget. Internal
        # plans draw from the project budget bounded by quota, so they route through the
        # project funding branch even though they are an active subscription for plan
        # resolution (their plan_id still sets the quota).
        funds_from_subscription = subscription_provides_plan_budget(subscription, sub_now)
        has_wallet = bool(user_budget_tokens and int(user_budget_tokens) > 0)

        plan_id, plan_source = resolve_plan_id(
            role=role,
            has_active_subscription=has_active_subscription,
            subscription=subscription,
        )

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
        if has_wallet or plan_id == "wallet":
            payg_policy = await self.cp_manager.get_plan_quota_policy(
                tenant=tenant, project=project, plan_id="wallet"
            )
            if not payg_policy:
                payg_policy = self.app_quota_policies.get("wallet") or base_policy

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

        if funds_from_subscription and not budget_bypass:
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

        project_budget_allowed = self.project_budget_allowed_for_plan(
            user_type=str(user_type or ""),
            plan_id=plan_id,
            plan_source=plan_source,
            has_wallet=has_wallet,
            has_active_subscription=funds_from_subscription,
        )

        project_budget = None
        project_available_usd = 0.0
        project_balance_usd = 0.0
        if not budget_bypass and not funds_from_subscription and project_budget_allowed:
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
        elif funds_from_subscription:
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

        lane = "plan"
        admit: AdmitResult | None = None
        plan_admit_now = datetime.utcnow().replace(tzinfo=timezone.utc)
        plan_reservation_id = None
        plan_reservation_active = False

        # Unified split: the primary funds (project/subscription) cap how much of the
        # turn the plan can pre-reserve; the wallet covers the over-quota/over-funds
        # remainder. P_tokens bounds want_resv = min(R, P_tokens) by funds, then the
        # wallet-aware admit clamps it further by the live quota (Q) inside the Lua.
        def _primary_tokens_cap() -> int:
            if budget_bypass:
                return int(est_turn_tokens)
            denom = usd_per_token * SAFETY_MARGIN
            if denom <= 0:
                return 0
            if funding_source == "subscription":
                return int(max(float(subscription_available_usd or 0.0), 0.0) / denom)
            if funding_source == "project":
                if project_budget is not None:
                    od_lim = project_budget.get("overdraft_limit_usd")
                    if od_lim is None:
                        return int(est_turn_tokens)  # unlimited overdraft -> no fund clamp
                    cap_usd = float(project_budget.get("available_usd") or 0.0) + float(od_lim or 0.0)
                    return int(max(cap_usd, 0.0) / denom)
                return int(est_turn_tokens)
            return 0  # funding_source == "none"

        async def _admit_split(want_resv: int) -> AdmitResult:
            return await self.rl.admit(
                bundle_id=rl_bundle_id,
                subject_id=self.subj,
                policy=base_policy,
                lock_id=lock_id, lock_ttl_sec=180,
                apply_plan_override=True,
                reserve_tokens=int(want_resv),
                reservation_id=turn_id,
                reservation_ttl_sec=900,
                now=plan_admit_now,
                wallet_aware=True,
                has_wallet=bool(has_wallet),
                wallet_available_tokens=int(user_budget_tokens or 0),
                r_total=int(est_turn_tokens),
            )

        _primary_cap_tokens = _primary_tokens_cap()
        want_resv = min(int(est_turn_tokens), int(_primary_cap_tokens))
        admit = await _admit_split(want_resv)
        lock_released = False
        _log(
            "admit.split", "Unified split admit",
            allowed=admit.allowed, reason=admit.reason,
            reserved_tokens=getattr(admit, "reserved_tokens", 0),
            wallet_part=getattr(admit, "wallet_part", 0),
            primary_cap_tokens=_primary_cap_tokens, want_resv=want_resv,
            snapshot=admit.snapshot,
        )

        if not admit.allowed:
            payload = _build_rate_limit_payload(
                policy=_policy_for_insight(admit_result=admit, fallback_policy=base_policy),
                snapshot=admit.snapshot,
                reason=admit.reason,
                used_plan_override=admit.used_plan_override,
                needed_tokens=int(est_turn_tokens),
            )
            _deny_event = "rate_limit.denied"
            if "wallet_insufficient" in str(admit.reason or ""):
                if (not budget_bypass) and funding_source in ("project", "subscription") and int(_primary_cap_tokens) == 0:
                    # The primary budget is fully exhausted: its zero cap clamped want_resv
                    # to 0, so the deny surfaces here at admit instead of at reserve_funding.
                    # Emit the same primary-exhausted signal the reserve path would.
                    _deny_code = f"{funding_source}_budget_reservation_failed_no_personal"
                    _deny_title = f"Insufficient {funding_label}"
                    _deny_msg = f"{funding_label.title()} exhausted and personal credits cannot cover the turn."
                    _deny_event = "rate_limit.project_exhausted" if funding_source == "project" else "rate_limit.subscription_exhausted"
                else:
                    _deny_code = "plan_exhausted_no_personal"
                    _deny_title = "Tier exhausted"
                    _deny_msg = "Plan quota/funds exhausted and personal credits cannot cover the remainder."
            else:
                _deny_code = "rate_limited"
                _deny_title = "Rate limit exceeded"
                _deny_msg = f"Rate limited: {admit.reason or 'unknown'}"
            await _econ_fail(
                code=_deny_code,
                title=_deny_title,
                message=_deny_msg,
                event_type=_deny_event,
                data={
                    "reason": admit.reason,
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "funding_source": funding_source,
                    "snapshot": admit.snapshot,
                    "rate_limit": payload,
                    "lane": "deny",
                },
            )

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
            est_tokens_per_turn=est_turn_tokens,
        )
        _log("insight", "Computed quota insight", lane=lane, insight=dataclasses.asdict(insight))

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
            plan_limit, scope = effective_policy.effective_allowed_tokens()
            scope_for_lock = str(scope or "month")
            await _acquire_quota_lock_or_deny(scope=scope_for_lock)

            try:
                from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import (
                    FundingContext, reserve_funding, ReserveStatus,
                )

                # Single split reservation: the money flow is owned by the shared
                # funding_flow.reserve_funding. run() keeps only the quota-lock + denial SSE shell.
                _reserve_ctx = FundingContext(
                    rl=self.rl, budget_limiter=self.budget_limiter, cp_manager=self.cp_manager,
                    tenant=tenant, project=project, user_id=user_id, subject_id=self.subj,
                    bundle_id=bundle_id, rl_bundle_id=rl_bundle_id, scope_id=turn_id,
                    usd_per_token=usd_per_token, now=plan_admit_now,
                    subscription_limiter=subscription_budget_limiter,
                    log=lambda stage, msg, level="INFO", **kv: _log(stage, msg, level, **kv),
                )
                _outcome = await reserve_funding(
                    _reserve_ctx, admit=admit, funding_source=funding_source,
                    budget_bypass=budget_bypass, has_wallet=has_wallet, ttl_sec=900,
                )

                if _outcome.status is ReserveStatus.OK:
                    _res = _outcome.reservation
                    plan_reserved_tokens = int(_res.plan_reserved_tokens or 0)
                    plan_reservation_id = _res.plan_reservation_id
                    plan_reservation_active = bool(_res.plan_reservation_active)
                    plan_project_tokens_est = int(_res.plan_project_tokens_est or 0)
                    app_reservation_id = _res.app_reservation_id
                    app_reserved_usd = float(_res.app_reserved_usd or 0.0)
                    app_reservation_active = bool(_res.app_reservation_active)
                    if _res.wallet_reservation_active:
                        personal_reservation_id = _res.wallet_reservation_id
                        personal_reserved_tokens = int(_res.wallet_reserved_tokens or 0)
                        personal_reservation_active = True
                        _log("reserve.personal", "Reserved personal overflow tokens",
                             reservation_id=personal_reservation_id, tokens_reserved=personal_reserved_tokens)

                else:  # ReserveStatus.DENIED
                    _deny_code = _outcome.deny_code or "no_funding_source"
                    if _deny_code == f"{funding_source}_reservation_failed":
                        _app_reserved_usd_msg = float(est_turn_tokens) * float(usd_per_token) * SAFETY_MARGIN
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
                                "app_reserved_usd": _app_reserved_usd_msg,
                                "user_budget_tokens": user_budget_tokens,
                            },
                        )
                    elif _deny_code == "wallet_reservation_failed":
                        payload = _build_rate_limit_payload(
                            policy=_policy_for_insight(admit_result=admit, fallback_policy=effective_policy),
                            snapshot=admit.snapshot,
                            reason=admit.reason or "wallet_overflow",
                            used_plan_override=admit.used_plan_override,
                            needed_tokens=int(est_turn_tokens),
                            remaining_tokens=int(plan_project_tokens_est),
                        )
                        await _econ_fail(
                            code="personal_reservation_failed",
                            title="Insufficient personal credits",
                            message="Insufficient personal credits to cover the over-quota remainder.",
                            event_type="rate_limit.denied",
                            data={
                                "reason": "personal_reservation_failed",
                                "bundle_id": bundle_id,
                                "subject_id": self.subj,
                                "user_type": user_type,
                                "tokens_required": int(est_turn_tokens),
                                "rate_limit": payload,
                                "lane": lane,
                            },
                        )
                    else:  # no_funding_source (and any unmapped deny)
                        await _econ_fail(
                            code="no_funding_source",
                            title="No funding source",
                            message="No plan or project funding source is available for this user type.",
                            event_type="rate_limit.no_funding",
                            data={
                                "reason": "no_funding_source",
                                "bundle_id": bundle_id,
                                "subject_id": self.subj,
                                "user_type": user_type,
                                "funding_source": funding_source,
                                "user_message": MSG_NO_FUNDING,
                                "notification_type": "error",
                            },
                        )
            finally:
                try:
                    await _release_quota_lock_if_held()
                except Exception as ex:
                    _log("quota_lock", "Failed to release quota_lock", "WARN", error=str(ex))
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

        await self._invoke_pre_run_hook(state=state, econ_ctx=econ_ctx)

        result = None
        admit_snapshot_pre = dict(admit.snapshot or {})

        try:
            usage_from = datetime.utcnow().date().isoformat()
            _log("exec", "Invoking execute_core", lane=lane, usage_from=usage_from)
            _log("economics", "--- END PRE-RUN ECONOMICS ---")

            parent_scope_id = str(turn_id or thread_id or "")
            with bind_economics_scope(parent_scope_id):
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

            # ---- post-run settlement: delegate the money core to funding_flow ----
            # Every turn (project/subscription, bypass) settles through the shared
            # funding_flow split — single settlement owner. run() keeps only the
            # observability shell below (charge.split log, underfunded event, post-run
            # quota snapshot/warning, per-provider analytics).
            from kdcube_ai_app.apps.chat.sdk.infra.economics.funding_flow import (
                FundingContext, PlanFundingReservation, settle_plan_funding,
            )

            # snapshot the pre-commit RL reservation size (funding_flow finalizes it)
            plan_reserved_tokens_pre = int(plan_reserved_tokens or 0)

            _settle_res = PlanFundingReservation(
                funding_source=funding_source,
                budget_bypass=budget_bypass,
                est_turn_tokens=int(est_turn_tokens),
                plan_reservation_id=plan_reservation_id,
                plan_reserved_tokens=int(plan_reserved_tokens or 0),
                plan_reservation_active=bool(plan_reservation_active),
                app_reservation_id=app_reservation_id,
                app_reserved_usd=float(app_reserved_usd or 0.0),
                app_reservation_active=bool(app_reservation_active),
                plan_project_tokens_est=int(plan_project_tokens_est or 0),
                wallet_reservation_id=personal_reservation_id,
                wallet_reserved_tokens=int(personal_reserved_tokens or 0),
                wallet_reservation_active=bool(personal_reservation_active),
                has_wallet=bool(has_wallet),
            )
            _settle_ctx = FundingContext(
                rl=self.rl,
                budget_limiter=self.budget_limiter,
                cp_manager=self.cp_manager,
                tenant=tenant,
                project=project,
                user_id=user_id,
                subject_id=self.subj,
                bundle_id=bundle_id,
                rl_bundle_id=rl_bundle_id,
                scope_id=turn_id,
                usd_per_token=usd_per_token,
                now=plan_admit_now,
                subscription_limiter=subscription_budget_limiter,
                log=lambda stage, msg, level="INFO", **kv: _log(stage, msg, level, **kv),
            )
            settlement = await settle_plan_funding(
                _settle_ctx,
                _settle_res,
                ranked_tokens=int(ranked_tokens),
                total_cost_usd=float(total_cost),
                effective_policy=effective_policy,
                plan_has_lifetime_budget=bool(plan_balance and plan_balance.has_lifetime_budget()),
                user_budget_tokens=user_budget_tokens,
            )

            # funding_flow finalized the RL reservation + lock and committed every hold;
            # mark run()'s inline handles consumed so the finally-cleanup is a no-op.
            lock_released = True
            plan_reservation_active = False
            plan_reservation_id = None
            plan_reserved_tokens = 0
            app_reservation_active = False
            personal_reservation_active = False

            # settlement result -> the observability vars run() keeps
            plan_settlement_allocation = settlement.allocation
            plan_covered_usd = float(settlement.primary_funding_usd)
            project_absorption_usd = float(settlement.project_absorption_usd)
            plan_quota_commit_tokens = int(settlement.quota_commit_tokens)
            wallet_consumed_tokens = int(settlement.wallet_consumed_tokens)
            user_uncovered_tokens = int(settlement.user_uncovered_tokens)
            user_uncovered_usd = float(settlement.user_uncovered_usd)
            extra_project_items = list(settlement.extra_project_items or [])
            app_spend_usd = float(settlement.primary_funding_usd)
            user_target_tokens = int(wallet_consumed_tokens) + int(user_uncovered_tokens)

            _log(
                "charge.split",
                "Computed actual split (settled via funding_flow)",
                ranked_tokens=ranked_tokens,
                funding_source=funding_source,
                plan_covered_usd=plan_covered_usd,
                project_absorption_usd=project_absorption_usd,
                quota_tokens=plan_quota_commit_tokens if lane == "plan" else None,
                wallet_tokens=int(plan_settlement_allocation.wallet_tokens) if plan_settlement_allocation else None,
                wallet_consumed_tokens=wallet_consumed_tokens,
                user_uncovered_tokens=user_uncovered_tokens,
                user_uncovered_usd=user_uncovered_usd,
                total_cost=total_cost,
            )

            post_run_snapshot = None
            post_run_violations = []
            if effective_policy and not budget_bypass:
                post_run_snapshot = _post_run_snapshot(
                    admit_snapshot_pre,
                    ranked=int(plan_quota_commit_tokens),
                    reserved=int(plan_reserved_tokens_pre),
                    lane_name=lane,
                )
                try:
                    if getattr(effective_policy, "tokens_per_hour", None) is not None:
                        prefix = f"{self.rl.ns}:{rl_bundle_id}:{self.subj}:toks:hour:bucket"
                        tok_h_now, reset_at = await self.rl._rolling_hour_stats(
                            prefix,
                            now,
                            limit=getattr(effective_policy, "tokens_per_hour", None),
                            reserved=0,
                        )
                        # settle (settle_plan_funding) has already committed this turn's tokens
                        # before we read the rolling window, so tok_h_now is the post-commit value.
                        # Do NOT add the commit again — that double-counted the rolling hour and
                        # produced false post-run "tokens_per_hour exceeded" violations (routing the
                        # turn to the exhausted path even when usage was still under the limit).
                        post_run_snapshot["tok_hour"] = int(tok_h_now or 0)
                        if reset_at:
                            post_run_snapshot["tok_hour_reset_at"] = int(reset_at)
                except Exception as ex:
                    _log("rate_limit", "Failed to compute rolling-hour reset", "WARN", error=str(ex))
                post_run_violations = _post_run_violations(effective_policy, post_run_snapshot)

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
                        "wallet_consumed_tokens": int(wallet_consumed_tokens),
                        "user_uncovered_tokens": int(user_uncovered_tokens),
                        "user_uncovered_usd": float(user_uncovered_usd),
                        "funding_source": funding_source,
                    },
                )
                _log(
                    "charge.user",
                    f"User underfunded post-fact; {funding_label} absorbed remainder",
                    "WARN",
                    lane=lane,
                    user_target_tokens=int(user_target_tokens),
                    wallet_consumed_tokens=int(wallet_consumed_tokens),
                    user_uncovered_tokens=int(user_uncovered_tokens),
                    user_uncovered_usd=float(user_uncovered_usd),
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

            if not post_run_violations and post_run_snapshot and not budget_bypass:
                post_insight = compute_quota_insight(
                    policy=effective_policy,
                    snapshot=post_run_snapshot,
                    reason=None,
                    used_plan_override=admit.used_plan_override if admit else False,
                    user_budget_tokens=user_budget_tokens,
                    est_tokens_per_turn=est_turn_tokens,
                )
                _log("post_run.insight", "Computed post-run quota insight", insight=dataclasses.asdict(post_insight))
                pr_mr = post_insight.messages_remaining
                pr_tok = post_insight.total_token_remaining
                if (
                    (pr_mr is not None and pr_mr <= 1)
                    or (pr_tok is not None and pr_tok < int(est_turn_tokens))
                ):
                    if pr_mr is not None and pr_mr == 0:
                        rem = post_insight.remaining
                        req_candidates = [rem.get(k) for k in ("requests_per_day", "requests_per_month", "total_requests") if rem.get(k) is not None]
                        request_remaining = min(req_candidates) if req_candidates else None
                        request_exhausted = request_remaining is not None and request_remaining <= 0
                        if (not request_exhausted) and pr_tok is not None and pr_tok > 0:
                            # Token budget is the binding constraint and a sub-turn remainder is
                            # left (requests still available): surface the token balance instead of
                            # a bare "last message". Without this the low-tokens message is
                            # unreachable — messages_remaining is always 0 once the spendable token
                            # balance drops below one turn's estimate (total_token_remaining < est).
                            warning_user_message = msg_warning_low_tokens(max(int(pr_tok) // 1000, 1))
                        else:
                            # Synthesize the exhausted dimension so _format_reset_time can produce a specific time
                            exhausted = []
                            if rem.get("requests_per_day") == 0 and getattr(effective_policy, "requests_per_day", None) is not None:
                                exhausted.append("requests_per_day")
                            if rem.get("requests_per_month") == 0 and getattr(effective_policy, "requests_per_month", None) is not None:
                                exhausted.append("requests_per_month")
                            if rem.get("tokens_per_hour") == 0 and getattr(effective_policy, "tokens_per_hour", None) is not None:
                                exhausted.append("tokens_per_hour")
                            if rem.get("tokens_per_day") == 0 and getattr(effective_policy, "tokens_per_day", None) is not None:
                                exhausted.append("tokens_per_day")
                            exhausted_insight = compute_quota_insight(
                                policy=effective_policy,
                                snapshot=post_run_snapshot,
                                reason="|".join(exhausted) if exhausted else None,
                                used_plan_override=admit.used_plan_override if admit else False,
                                user_budget_tokens=user_budget_tokens,
                                est_tokens_per_turn=est_turn_tokens,
                            )
                            reset_text = _format_reset_time(
                                retry_after_sec=exhausted_insight.retry_after_sec,
                                now=now,
                                user_timezone=getattr(self.comm_context.user, "timezone", None) if self.comm_context and self.comm_context.user else None,
                            ) if exhausted_insight.retry_after_sec else None
                            if reset_text:
                                warning_user_message = msg_warning_last_msg_reset(reset_text)
                            else:
                                warning_user_message = MSG_WARNING_LAST_MSG_SOON
                    elif pr_mr is not None and pr_mr == 1:
                        # Check if the binding constraint is requests or tokens
                        rem = post_insight.remaining
                        req_candidates = [rem.get(k) for k in ("requests_per_day", "requests_per_month", "total_requests") if rem.get(k) is not None]
                        request_remaining = min(req_candidates) if req_candidates else None
                        if request_remaining is not None and request_remaining <= 1:
                            warning_user_message = MSG_WARNING_ONE_REQUEST_REMAINING
                        else:
                            # Token-limited: post-run simulation can be off by ~est_turn_tokens,
                            # so show approximate count rather than promising an exact value.
                            approx = pr_mr if pr_mr is not None and pr_mr >= 1 else 1
                            warning_user_message = msg_warning_approaching_approx(approx)
                    elif pr_tok is not None:
                        warning_user_message = msg_warning_low_tokens(pr_tok // 1000)
                    else:
                        warning_user_message = MSG_WARNING_APPROACHING
                    warning_rate_limit = dataclasses.asdict(post_insight)
                    warning_rate_limit["user_message"] = warning_user_message
                    warning_rate_limit["notification_type"] = "warning"
                    await _emit_event(
                        type="rate_limit.warning",
                        status="completed",
                        title="Approaching quota",
                        data={
                            "bundle_id": bundle_id,
                            "subject_id": self.subj,
                            "user_type": user_type,
                            "snapshot": post_run_snapshot,
                            "rate_limit": warning_rate_limit,
                            "lane": lane,
                        },
                    )

            if post_run_violations and not budget_bypass:
                post_reason = "|".join(post_run_violations)
                post_needed_tokens = int(plan_quota_commit_tokens) if lane == "plan" else int(ranked_tokens)
                rate_limit_payload = _build_rate_limit_payload(
                    policy=effective_policy,
                    snapshot=post_run_snapshot or {},
                    reason=post_reason,
                    used_plan_override=admit.used_plan_override if admit else False,
                    now=now,
                    needed_tokens=post_needed_tokens,
                )
                rate_limit_payload["notification_type"] = "warning"
                payload = {
                    "bundle_id": bundle_id,
                    "subject_id": self.subj,
                    "user_type": user_type,
                    "lane": lane,
                    "ranked_tokens": int(ranked_tokens),
                    "quota_tokens": int(plan_quota_commit_tokens) if lane == "plan" else None,
                    "snapshot": post_run_snapshot,
                    "reason": post_reason,
                    "rate_limit": rate_limit_payload,
                }
                await _emit_analytics_event(
                    type="analytics.rate_limit.post_run_exceeded",
                    status="completed",
                    title="Post-run limit exceeded",
                    data=payload,
                )
                _log(
                    "rate_limit",
                    "Post-run quota exceeded after successful settlement; recorded warning only",
                    "WARN",
                    reason=post_reason,
                    ranked_tokens=int(ranked_tokens),
                    quota_tokens=int(plan_quota_commit_tokens) if lane == "plan" else None,
                    snapshot=post_run_snapshot,
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

        await self._invoke_post_run_hook(state=state, result=result, econ_ctx=econ_ctx)
        _log("done", "run() completed successfully", lane=lane)
        _log("economics", "--- END POST-RUN ECONOMICS ---")
        return self.project_app_state(result)

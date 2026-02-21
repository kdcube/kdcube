# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/api/control_plane/control_plane.py

"""
Control Plane API

Provides REST endpoints for managing:
1. User plan balance (plan overrides + lifetime credits)
2. Plan quota policies (base limits by plan id)
3. Application budget policies (spending limits per provider - NO bundle_id!)

Includes Stripe webhook integration for automated credit purchases.

Admin-only access with similar patterns to OPEX API.
"""

from typing import Optional
import json
import logging
import os

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, APIRouter, Query, Header, Response
from datetime import datetime, timedelta, timezone

from kdcube_ai_app.apps.chat.api.resolvers import auth_without_pressure
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsWebhookHandler
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import UserEconomicsRateLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.infra.accounting.usage import (
    llm_output_price_usd_per_token,
    quote_tokens_for_usd,
    quote_usd_for_tokens,
    anthropic,
    sonnet_45,
)


logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

REF_PROVIDER = anthropic
REF_MODEL = sonnet_45
DEFAULT_PLAN_FREE = "free"
DEFAULT_PLAN_PAYG = "payasyougo"
DEFAULT_PLAN_ADMIN = "admin"
DEFAULT_PLAN_ANON = "anonymous"

def _tokens_from_usd(usd_amount: Optional[float]) -> Optional[int]:
    if usd_amount is None:
        return None
    tokens, _ = quote_tokens_for_usd(
        usd_amount=float(usd_amount),
        ref_provider=REF_PROVIDER,
        ref_model=REF_MODEL,
    )
    return int(tokens)

def _usd_from_tokens(tokens: Optional[int]) -> Optional[float]:
    if tokens is None:
        return None
    return round(
        float(
            quote_usd_for_tokens(
                tokens=int(tokens),
                ref_provider=REF_PROVIDER,
                ref_model=REF_MODEL,
            )
        ),
        2,
    )

def _usd_from_cents(cents: Optional[int]) -> Optional[float]:
    if cents is None:
        return None
    return float(cents) / 100.0

def _normalize_role(role: Optional[str]) -> Optional[str]:
    if not role:
        return None
    return role.strip().lower()

async def _resolve_role_from_session(
        redis,
        *,
        tenant: str,
        project: str,
        user_id: str,
) -> Optional[str]:
    if not redis or not user_id:
        return None
    try:
        from kdcube_ai_app.infra.namespaces import REDIS, ns_key
        prefix = ns_key(REDIS.SESSION, tenant=tenant, project=project)
        keys = [
            f"{prefix}:paid:{user_id}",
            f"{prefix}:registered:{user_id}",
        ]
        for key in keys:
            raw = await redis.get(key)
            if not raw:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="ignore")
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            session_role = payload.get("user_type") or payload.get("role")
            if session_role:
                return str(session_role).strip().lower()
    except Exception:
        return None
    return None

async def _resolve_plan_id_for_user(
        *,
        mgr,
        redis,
        tenant: str,
        project: str,
        user_id: str,
        role: Optional[str],
        explicit_plan_id: Optional[str],
) -> tuple[str, str]:
    if explicit_plan_id:
        return explicit_plan_id, "explicit"

    role_norm = _normalize_role(role)
    if not role_norm:
        role_norm = await _resolve_role_from_session(
            redis,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )
    if role_norm in ("privileged", "admin"):
        return DEFAULT_PLAN_ADMIN, "role"
    if role_norm == "anonymous":
        return DEFAULT_PLAN_ANON, "role"

    sub = await mgr.subscription_mgr.get_subscription(
        tenant=tenant,
        project=project,
        user_id=user_id,
    )
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    sub_due_at = getattr(sub, "next_charge_at", None) if sub else None
    sub_chargeable = bool(sub and int(getattr(sub, "monthly_price_cents", 0) or 0) > 0)
    sub_past_due = bool(sub_due_at and sub_due_at <= now)
    has_active_subscription = bool(
        sub
        and getattr(sub, "status", None) == "active"
        and sub_chargeable
        and not sub_past_due
    )
    if has_active_subscription:
        return (getattr(sub, "plan_id", None) or DEFAULT_PLAN_PAYG), "subscription"

    return DEFAULT_PLAN_FREE, "role"

# ============================================================================
# Request/Response Models
# ============================================================================

class CreateSubscriptionRequest(BaseModel):
    user_id: str
    plan_id: str = Field(..., description="Subscription plan id")
    provider: str = Field("stripe", description="stripe|internal")

    # Stripe params
    stripe_price_id: str | None = None
    stripe_customer_id: str | None = None
    monthly_price_cents_hint: int | None = None

class UpsertSubscriptionPlanRequest(BaseModel):
    plan_id: str = Field(..., description="Plan identifier")
    provider: str = Field("internal", description="internal|stripe")
    stripe_price_id: Optional[str] = Field(None, description="Stripe price id (required for stripe plans)")
    monthly_price_cents: int = Field(0, ge=0, description="Monthly price in cents")
    active: bool = Field(True)
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class InternalRenewOnceRequest(BaseModel):
    user_id: str = Field(..., description="User id to renew")
    charge_at: datetime | None = Field(None, description="Optional charge timestamp (default now UTC)")
    idempotency_key: str | None = Field(None, description="Optional explicit idempotency key")

class TopUpSubscriptionBudgetRequest(BaseModel):
    user_id: str
    usd_amount: float = Field(..., gt=0)
    notes: Optional[str] = None
    force_topup: bool = Field(False, description="Allow multiple topups within the same billing period")

class ReapSubscriptionReservationsRequest(BaseModel):
    user_id: str
    period_key: Optional[str] = Field(
        None,
        description="Optional explicit period_key; if omitted, uses the active subscription period",
    )
    limit: int = Field(500, ge=1, le=2000)

class ReapAllSubscriptionReservationsRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="Optional user id to scope the sweep")
    limit_periods: int = Field(500, ge=1, le=5000)
    per_period_limit: int = Field(500, ge=1, le=2000)

class SweepSubscriptionRolloversRequest(BaseModel):
    user_id: Optional[str] = None
    limit: int = Field(200, ge=1, le=2000)

class GrantTrialRequest(BaseModel):
    """
    Grant temporary plan override (7-day trial).

    **IMPORTANT:** This OVERRIDES the user's base plan, does NOT add to it.

    Example:
    - Free user normally has: 10 req/day
    - You grant trial: 100 req/day for 7 days
    - During trial: User gets exactly 100 req/day (NOT 110)
    - After trial expires: User reverts to 10 req/day

    These quotas RESET daily/monthly like plan limits.
    """
    user_id: str = Field(..., description="User ID")
    days: int = Field(7, description="Trial duration in days")
    requests_per_day: int = Field(100, description="Requests/day during trial (OVERRIDES base)")
    requests_per_month: Optional[int] = Field(None, description="Requests/month during trial")
    tokens_per_hour: Optional[int] = Field(None, description="Tokens/hour during trial (OVERRIDES base)")
    tokens_per_day: Optional[int] = Field(None, description="Tokens/day during trial (OVERRIDES base)")
    tokens_per_month: Optional[int] = Field(None, description="Tokens/month during trial")
    usd_per_hour: Optional[float] = Field(None, description="USD/hour (converted to tokens, overrides tokens_per_hour)")
    usd_per_day: Optional[float] = Field(None, description="USD/day (converted to tokens, overrides tokens_per_day)")
    usd_per_month: Optional[float] = Field(None, description="USD/month (converted to tokens, overrides tokens_per_month)")
    max_concurrent: Optional[int] = Field(None, description="Max concurrent during trial")
    notes: Optional[str] = Field(None, description="Notes")


class UpdatePlanOverrideRequest(BaseModel):
    """
    Update user's plan override.

    Like trial but more flexible - can set exact limits and expiry.
    Supports PARTIAL UPDATES - only updates fields you provide!

    **IMPORTANT:** This OVERRIDES the user's base plan, does NOT add to it.
    """
    user_id: str = Field(..., description="User ID")
    requests_per_day: Optional[int] = Field(None, description="Requests/day (OVERRIDES base)")
    requests_per_month: Optional[int] = Field(None, description="Requests/month (OVERRIDES base)")
    tokens_per_hour: Optional[int] = Field(None, description="Tokens/hour (OVERRIDES base)")
    tokens_per_day: Optional[int] = Field(None, description="Tokens/day (OVERRIDES base)")
    tokens_per_month: Optional[int] = Field(None, description="Tokens/month (OVERRIDES base)")
    usd_per_hour: Optional[float] = Field(None, description="USD/hour (converted to tokens, overrides tokens_per_hour)")
    usd_per_day: Optional[float] = Field(None, description="USD/day (converted to tokens, overrides tokens_per_day)")
    usd_per_month: Optional[float] = Field(None, description="USD/month (converted to tokens, overrides tokens_per_month)")
    max_concurrent: Optional[int] = Field(None, description="Max concurrent (OVERRIDES base)")
    expires_in_days: Optional[int] = Field(30, description="Days until expiration (None = never)")
    notes: Optional[str] = Field(None, description="Notes")


class AddLifetimeCreditsRequest(BaseModel):
    """
    Add purchased credits in USD (converted to lifetime tokens).

    Balance depletes on use, does NOT reset.
    Completely separate from plan quotas.
    """
    user_id: str = Field(..., description="User ID")
    usd_amount: float = Field(..., gt=0, description="Amount in USD")
    ref_provider: str = Field(default="anthropic", description="Reference model provider")
    ref_model: str = Field(default="claude-sonnet-4-5-20250929", description="Reference model")
    purchase_id: Optional[str] = Field(None, description="Payment/transaction ID")
    notes: Optional[str] = Field(None, description="Purchase notes")


class WalletRefundRequest(BaseModel):
    """Refund lifetime credits by Stripe payment_intent_id (partial allowed)."""
    user_id: str = Field(..., description="User ID")
    payment_intent_id: str = Field(..., description="Stripe payment_intent id")
    usd_amount: Optional[float] = Field(None, gt=0, description="Refund amount in USD (default = full refundable)")
    notes: Optional[str] = Field(None, description="Refund notes")


class CancelSubscriptionRequest(BaseModel):
    """Cancel a Stripe subscription (at period end)."""
    user_id: Optional[str] = Field(None, description="User ID (optional if stripe_subscription_id provided)")
    stripe_subscription_id: Optional[str] = Field(None, description="Stripe subscription id")
    notes: Optional[str] = Field(None, description="Cancel notes")


class StripeReconcileRequest(BaseModel):
    """Reconcile pending Stripe-related requests (refunds/cancels)."""
    kind: str = Field("all", description="all|wallet_refund|subscription_cancel")
    limit: int = Field(200, ge=1, le=2000)


class SetQuotaPolicyRequest(BaseModel):
    """Set quota policy for a plan (base limits - NO bundle_id!)."""
    plan_id: str = Field(..., description="Plan id (free, payasyougo, admin, etc.)")
    max_concurrent: Optional[int] = Field(None, description="Max concurrent requests")
    requests_per_day: Optional[int] = Field(None, description="Requests per day")
    requests_per_month: Optional[int] = Field(None, description="Requests per month")
    total_requests: Optional[int] = Field(None, description="Total requests")
    tokens_per_hour: Optional[int] = Field(None, description="Tokens per hour")
    tokens_per_day: Optional[int] = Field(None, description="Tokens per day")
    tokens_per_month: Optional[int] = Field(None, description="Tokens per month")
    usd_per_hour: Optional[float] = Field(None, description="USD per hour (converted to tokens)")
    usd_per_day: Optional[float] = Field(None, description="USD per day (converted to tokens)")
    usd_per_month: Optional[float] = Field(None, description="USD per month (converted to tokens)")
    notes: Optional[str] = Field(None, description="Notes")


class SetBudgetPolicyRequest(BaseModel):
    """Set budget policy for a provider (app spending limits )."""
    provider: str = Field(..., description="Provider (anthropic, openai, etc.)")
    usd_per_hour: Optional[float] = Field(None, description="USD per hour")
    usd_per_day: Optional[float] = Field(None, description="USD per day")
    usd_per_month: Optional[float] = Field(None, description="USD per month")
    notes: Optional[str] = Field(None, description="Notes")


class TopUpAppBudgetRequest(BaseModel):
    """Top up application budget (company money)."""
    usd_amount: float = Field(..., gt=0)
    notes: Optional[str] = Field(None)


# ============================================================================
# Helper Functions
# ============================================================================

def _get_control_plane_manager(ctx):
    """
    Get or create ControlPlaneManager instance.
    Matches pattern from opex.py for consistency.
    """
    from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager

    # Try to reuse cached instance
    mgr = getattr(ctx.state, "control_plane_manager", None)
    if mgr:
        return mgr

    # Get PostgreSQL pool and Redis from middleware
    pg_pool = getattr(ctx.state, "pg_pool", None)
    redis = getattr(ctx.state.middleware, "redis", None)

    if not pg_pool or not redis:
        raise HTTPException(
            status_code=503,
            detail="Control plane dependencies not initialized"
        )

    mgr = ControlPlaneManager(pg_pool=pg_pool, redis=redis)

    # Cache on router state
    ctx.state.control_plane_manager = mgr
    return mgr


# ============================================================================
# PLAN BALANCE (Plan Overrides + Lifetime Budget)
# ============================================================================

@router.post("/plan-override/grant-trial", status_code=201)
async def grant_trial_bonus(
        payload: GrantTrialRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Grant 7-day trial with temporary plan override.

    **How it works:**
    1. User's base plan: Free (10 req/day)
    2. You grant trial: 100 req/day for 7 days
    3. During days 1-7: User gets exactly 100 req/day (plan is OVERRIDDEN)
    4. Day 8+: User reverts to base plan (10 req/day)

    **Use Cases:**
    - New user registration bonus
    - Marketing campaigns
    - User retention
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.days)

        tokens_per_hour = _tokens_from_usd(payload.usd_per_hour) if payload.usd_per_hour is not None else payload.tokens_per_hour
        tokens_per_day = _tokens_from_usd(payload.usd_per_day) if payload.usd_per_day is not None else payload.tokens_per_day
        tokens_per_month = _tokens_from_usd(payload.usd_per_month) if payload.usd_per_month is not None else payload.tokens_per_month

        plan_override_balance = await mgr.update_user_plan_override(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            requests_per_day=payload.requests_per_day,
            requests_per_month=payload.requests_per_month,
            tokens_per_hour=tokens_per_hour,
            tokens_per_day=tokens_per_day,
            tokens_per_month=tokens_per_month,
            max_concurrent=payload.max_concurrent,
            expires_at=expires_at,
            purchase_notes=payload.notes or f"{payload.days}-day trial",
        )

        logger.info(
            f"[grant_trial] {payload.user_id}: {payload.days} days, "
            f"{payload.requests_per_day} req/day (OVERRIDE) by {session.username}"
        )

        return {
            "status": "ok",
            "message": f"Trial granted to {payload.user_id}",
            "plan_override_balance": {
                "user_id": plan_override_balance.user_id,
                "requests_per_day": plan_override_balance.requests_per_day,
                "tokens_per_hour": plan_override_balance.tokens_per_hour,
                "tokens_per_day": plan_override_balance.tokens_per_day,
                "tokens_per_hour_usd": _usd_from_tokens(plan_override_balance.tokens_per_hour),
                "tokens_per_day_usd": _usd_from_tokens(plan_override_balance.tokens_per_day),
                "tokens_per_month_usd": _usd_from_tokens(plan_override_balance.tokens_per_month),
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
                "expires_at": plan_override_balance.expires_at.isoformat() if plan_override_balance.expires_at else None,
            }
        }
    except Exception as e:
        logger.exception(f"[grant_trial] Failed for {payload.user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plan-override/update", status_code=201)
async def update_plan_override(
        payload: UpdatePlanOverrideRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Update user's plan override (supports PARTIAL updates).

    **How it works:**
    - Sets specific limits for a period
    - OVERRIDES base plan (does not add)
    - Resets daily/monthly like plan quotas
    - Expires after X days

    **Partial Updates:**
    - Only updates fields you provide
    - Other fields stay unchanged (COALESCE in SQL)

    **Use Cases:**
    - Promotional campaigns
    - Beta tester credits
    - Apology/compensation
    - Extending existing trials
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        expires_at = None
        if payload.expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

        tokens_per_hour = _tokens_from_usd(payload.usd_per_hour) if payload.usd_per_hour is not None else payload.tokens_per_hour
        tokens_per_day = _tokens_from_usd(payload.usd_per_day) if payload.usd_per_day is not None else payload.tokens_per_day
        tokens_per_month = _tokens_from_usd(payload.usd_per_month) if payload.usd_per_month is not None else payload.tokens_per_month

        plan_override_balance = await mgr.update_user_plan_override(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            requests_per_day=payload.requests_per_day,
            requests_per_month=payload.requests_per_month,
            tokens_per_hour=tokens_per_hour,
            tokens_per_day=tokens_per_day,
            tokens_per_month=tokens_per_month,
            max_concurrent=payload.max_concurrent,
            expires_at=expires_at,
            purchase_notes=payload.notes or "Admin plan override update",
        )

        logger.info(f"[update_plan_override] {payload.user_id} by {session.username}")

        return {
            "status": "ok",
            "message": f"Plan override updated for {payload.user_id}",
            "plan_override_balance": {
                "user_id": plan_override_balance.user_id,
                "requests_per_day": plan_override_balance.requests_per_day,
                "tokens_per_hour": plan_override_balance.tokens_per_hour,
                "tokens_per_day": plan_override_balance.tokens_per_day,
                "tokens_per_hour_usd": _usd_from_tokens(plan_override_balance.tokens_per_hour),
                "tokens_per_day_usd": _usd_from_tokens(plan_override_balance.tokens_per_day),
                "tokens_per_month_usd": _usd_from_tokens(plan_override_balance.tokens_per_month),
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
                "expires_at": plan_override_balance.expires_at.isoformat() if plan_override_balance.expires_at else None,
            }
        }
    except Exception as e:
        logger.exception(f"[update_plan_override] Failed for {payload.user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan-override/user/{user_id}")
async def get_user_plan_override_balance(
        user_id: str,
        include_expired: bool = Query(False),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get user's plan balance (plan override + lifetime budget).

    Shows currently active plan override with expiration date.
    Also shows lifetime budget if user has purchased credits.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        # Load snapshot with expired data available, so we can decide what to show.
        plan_override_balance = await mgr.get_user_plan_balance(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
            include_expired=True,
        )

        if not plan_override_balance:
            return {
                "status": "ok",
                "user_id": user_id,
                "has_plan_override": False,
                "has_lifetime_budget": False,
                "message": "User has no plan balance"
            }

        override_expired = plan_override_balance.is_plan_override_expired()
        override_active = plan_override_balance.plan_override_is_active()

        # If caller does NOT want expired overrides, hide them
        if not include_expired and override_expired:
            override_active = False

        lifetime_payload = None
        if plan_override_balance.has_lifetime_budget():
            purchased = int(plan_override_balance.lifetime_tokens_purchased or 0)
            consumed = int(plan_override_balance.lifetime_tokens_consumed or 0)
            gross_remaining = max(purchased - consumed, 0)

            available = await mgr.user_credits_mgr.get_lifetime_balance(
                tenant=settings.TENANT, project=settings.PROJECT, user_id=user_id
            )
            available = int(available or 0)

            reserved = max(gross_remaining - available, 0)

            usd_per_token = llm_output_price_usd_per_token(REF_PROVIDER, REF_MODEL)
            available_usd = round(available * usd_per_token, 2)

            lifetime_payload = {
                "tokens_purchased": purchased,
                "tokens_consumed": consumed,
                "tokens_gross_remaining": gross_remaining,   # purchased-consumed
                "tokens_reserved": reserved,                 # in-flight gates
                "tokens_available": available,               # spendable now
                "available_usd": available_usd,
                # last purchase snapshot (credits purchase)
                "purchase_amount_usd": float(plan_override_balance.last_purchase_amount_usd)
                if plan_override_balance.last_purchase_amount_usd else None,
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
            }

        return {
            "status": "ok",
            "user_id": user_id,
            "has_plan_override": override_active,
            "has_lifetime_budget": plan_override_balance.has_lifetime_budget(),
            "plan_override": {
                "requests_per_day": plan_override_balance.requests_per_day,
                "requests_per_month": plan_override_balance.requests_per_month,
                "tokens_per_hour": plan_override_balance.tokens_per_hour,
                "tokens_per_day": plan_override_balance.tokens_per_day,
                "tokens_per_month": plan_override_balance.tokens_per_month,
                "usd_per_hour": _usd_from_tokens(plan_override_balance.tokens_per_hour),
                "usd_per_day": _usd_from_tokens(plan_override_balance.tokens_per_day),
                "usd_per_month": _usd_from_tokens(plan_override_balance.tokens_per_month),
                "max_concurrent": plan_override_balance.max_concurrent,
                "expires_at": plan_override_balance.expires_at.isoformat() if plan_override_balance.expires_at else None,
                # override notes are grant_notes now
                "notes": plan_override_balance.grant_notes,
                "is_expired": override_expired,
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
            } if (override_active or include_expired) else None,
            "lifetime_budget": lifetime_payload,
        }

    except Exception as e:
        logger.exception(f"[get_user_plan_override_balance] Failed for {user_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/plan-override/user/{user_id}")
async def deactivate_plan_override(
        user_id: str,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Deactivate (soft delete) user's plan balance.

    **WARNING:** This clears BOTH plan override AND lifetime budget!

    **Use Cases:**
    1. **Refund Processing**: Remove all credits after issuing refund
    2. **Abuse Prevention**: Revoke all credits from fraudulent accounts
    3. **Policy Violation**: Remove all credits for TOS violations
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        await mgr.deactivate_plan_override(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
        )

        logger.info(
            f"[deactivate_plan_override] {settings.TENANT}/{settings.PROJECT}/{user_id}: "
            f"deactivated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Plan override deactivated for user {user_id}",
        }
    except Exception as e:
        logger.exception(f"[deactivate_plan_override] Failed for {user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to deactivate: {str(e)}")


@router.post("/plan-override/add-lifetime-credits", status_code=201)
async def add_lifetime_credits(
        payload: AddLifetimeCreditsRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Add purchased credits in USD (converted to lifetime tokens).

    User's purchased credits - separate from plan override.
    Balance depletes on use, does NOT reset.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        tokens_added, usd_per_token = quote_tokens_for_usd(
            usd_amount=payload.usd_amount,
            ref_provider=payload.ref_provider,
            ref_model=payload.ref_model,
        )

        await mgr.add_user_credits_usd(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            usd_amount=payload.usd_amount,
            ref_provider=payload.ref_provider,
            ref_model=payload.ref_model,
            purchase_id=payload.purchase_id,
            notes=payload.notes,
        )

        # available balance (excludes reservations)
        balance_tokens = await mgr.user_credits_mgr.get_lifetime_balance(
            tenant=settings.TENANT, project=settings.PROJECT, user_id=payload.user_id
        )
        balance_tokens = int(balance_tokens or 0)
        balance_usd = round(balance_tokens * usd_per_token, 2)

        logger.info(f"[add_lifetime_credits] {payload.user_id}: +${payload.usd_amount} by {session.username}")

        return {
            "success": True,
            "user_id": payload.user_id,
            "usd_amount": payload.usd_amount,
            "tokens_added": tokens_added,
            "new_balance_tokens": balance_tokens,
            "new_balance_usd": balance_usd,
            "reference_model": f"{payload.ref_provider}/{payload.ref_model}",
        }

    except Exception as e:
        logger.error(f"Failed to add lifetime credits for {payload.user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wallet/refund")
async def refund_wallet(
        payload: WalletRefundRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    """
    Refund lifetime credits (Stripe payment_intent). Immediate credit removal; Stripe confirmation finalizes.
    """
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)
    from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsAdminService

    svc = StripeEconomicsAdminService(
        pg_pool=pg_pool,
        user_credits_mgr=mgr.user_credits_mgr,
        subscription_mgr=mgr.subscription_mgr,
        ref_provider=REF_PROVIDER,
        ref_model=REF_MODEL,
    )

    try:
        res = await svc.request_wallet_refund(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            payment_intent_id=payload.payment_intent_id,
            usd_amount=payload.usd_amount,
            notes=payload.notes,
            actor=session.username or session.user_id,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Wallet refund failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan-override/lifetime-balance/{user_id}")
async def get_lifetime_balance(
        user_id: str,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get user's lifetime purchased balance (tokens + USD equivalent).
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        # Get lifetime balance from UserCreditsManager
        balance_tokens = await mgr.user_credits_mgr.get_lifetime_balance(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
        )

        if balance_tokens is None:
            return {
                "user_id": user_id,
                "has_purchased_credits": False,
                "balance_tokens": 0,
                "balance_usd": 0,
                "message": "User has no purchased credits"
            }

        # Convert to USD
        balance_usd = quote_usd_for_tokens(
            tokens=int(balance_tokens or 0),
            ref_provider=REF_PROVIDER,
            ref_model=REF_MODEL,
        )

        return {
            "user_id": user_id,
            "has_purchased_credits": True,
            "balance_tokens": balance_tokens,
            "balance_usd": round(balance_usd, 2),
            "minimum_required_tokens": 50_000,
            "can_use_budget": balance_tokens >= 50_000,
        }

    except Exception as e:
        logger.error(f"Failed to get balance for {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscriptions/user/{user_id}")
async def get_subscription(user_id: str, session: UserSession = Depends(auth_without_pressure())):
    settings = get_settings()
    mgr = _get_control_plane_manager(router)

    sub = await mgr.subscription_mgr.get_subscription(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
    )
    subscription_balance = None

    pg_pool = getattr(router.state, "pg_pool", None)
    if sub and pg_pool:
        from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
        period_desc = build_subscription_period_descriptor(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
            provider=getattr(sub, "provider", "internal") or "internal",
            stripe_subscription_id=getattr(sub, "stripe_subscription_id", None),
            period_end=getattr(sub, "next_charge_at", None),
            period_start=getattr(sub, "last_charged_at", None),
        )
        limiter = SubscriptionBudgetLimiter(
            pg_pool=pg_pool,
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
            period_key=period_desc["period_key"],
            period_start=period_desc["period_start"],
            period_end=period_desc["period_end"],
        )
        bal = await limiter.get_subscription_budget_balance()
        subscription_balance = dict(bal)
        subscription_balance["balance_tokens"] = _tokens_from_usd(bal.get("balance_usd"))
        subscription_balance["reserved_tokens"] = _tokens_from_usd(bal.get("reserved_usd"))
        subscription_balance["available_tokens"] = _tokens_from_usd(bal.get("available_usd"))
        subscription_balance["reference_model"] = f"{REF_PROVIDER}/{REF_MODEL}"

    return {
        "status": "ok",
        "subscription": sub.__dict__ if sub else None,
        "subscription_balance": subscription_balance,
    }


@router.get("/subscriptions/list")
async def list_subscriptions(
        provider: str | None = Query(None),
        user_id: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    mgr = _get_control_plane_manager(router)

    subs = await mgr.subscription_mgr.list_subscriptions(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        provider=provider,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return {"status": "ok", "count": len(subs), "subscriptions": [s.__dict__ for s in subs]}

@router.get("/subscriptions/plans")
async def list_subscription_plans(
        provider: str | None = Query(None),
        active_only: bool = Query(True),
        limit: int = Query(200, ge=1, le=5000),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    mgr = _get_control_plane_manager(router)
    plans = await mgr.subscription_mgr.list_plans(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        provider=provider,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return {"status": "ok", "count": len(plans), "plans": [p.__dict__ for p in plans]}

@router.post("/subscriptions/plans")
async def upsert_subscription_plan(
        payload: UpsertSubscriptionPlanRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    mgr = _get_control_plane_manager(router)
    plan = await mgr.subscription_mgr.upsert_plan(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        plan_id=payload.plan_id,
        provider=payload.provider,
        stripe_price_id=payload.stripe_price_id,
        monthly_price_cents=int(payload.monthly_price_cents or 0),
        active=bool(payload.active),
        metadata=payload.metadata,
        created_by=session.username or session.user_id,
        notes=payload.notes,
    )
    return {"status": "ok", "plan": plan.__dict__}

@router.get("/subscriptions/periods/{user_id}")
async def list_subscription_periods(
        user_id: str,
        status: str | None = Query("closed"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    if status and status not in ("open", "closed", "all"):
        raise HTTPException(status_code=400, detail="status must be open, closed, or all")

    schema = SubscriptionBudgetLimiter.CONTROL_PLANE_SCHEMA
    table = SubscriptionBudgetLimiter.BUDGET_TABLE

    clauses = ["tenant=$1", "project=$2", "user_id=$3"]
    args: list = [settings.TENANT, settings.PROJECT, user_id]
    idx = 4
    if status and status != "all":
        clauses.append(f"status=${idx}")
        args.append(status)
        idx += 1

    sql = f"""
        SELECT *
        FROM {schema}.{table}
        WHERE {" AND ".join(clauses)}
        ORDER BY period_end DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    args.extend([limit, offset])

    async with pg_pool.acquire() as c:
        rows = await c.fetch(sql, *args)

    periods = []
    for row in rows:
        bal_c = int(row["balance_cents"] or 0)
        res_c = int(row["reserved_cents"] or 0)
        topup_c = int(row["topup_cents"] or 0)
        rolled_c = int(row["rolled_over_cents"] or 0)
        spent_c = topup_c - rolled_c - bal_c
        available_c = bal_c - res_c

        periods.append({
            "period_key": row["period_key"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "status": row["status"],
            "balance_usd": _usd_from_cents(bal_c),
            "reserved_usd": _usd_from_cents(res_c),
            "available_usd": _usd_from_cents(available_c),
            "topup_usd": _usd_from_cents(topup_c),
            "rolled_over_usd": _usd_from_cents(rolled_c),
            "spent_usd": _usd_from_cents(spent_c),
            "closed_at": row["closed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "notes": row["notes"],
        })

    return {"status": "ok", "count": len(periods), "periods": periods}

@router.get("/subscriptions/ledger/{user_id}")
async def list_subscription_ledger(
        user_id: str,
        period_key: str = Query(...),
        limit: int = Query(200, ge=1, le=2000),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    schema = SubscriptionBudgetLimiter.CONTROL_PLANE_SCHEMA
    table = SubscriptionBudgetLimiter.LEDGER_TABLE

    sql = f"""
        SELECT id, period_key, amount_cents, kind, note,
               reservation_id, bundle_id, provider, request_id, created_at
        FROM {schema}.{table}
        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
        ORDER BY created_at DESC
        LIMIT $5 OFFSET $6
    """
    async with pg_pool.acquire() as c:
        rows = await c.fetch(sql, settings.TENANT, settings.PROJECT, user_id, period_key, limit, offset)

    ledger = []
    for row in rows:
        amount_cents = int(row["amount_cents"] or 0)
        ledger.append({
            "id": row["id"],
            "period_key": row["period_key"],
            "amount_cents": amount_cents,
            "amount_usd": _usd_from_cents(amount_cents),
            "kind": row["kind"],
            "note": row["note"],
            "reservation_id": row["reservation_id"],
            "bundle_id": row["bundle_id"],
            "provider": row["provider"],
            "request_id": row["request_id"],
            "created_at": row["created_at"],
        })

    return {"status": "ok", "count": len(ledger), "ledger": ledger}

@router.post("/subscriptions/reservations/reap")
async def reap_subscription_reservations(
        payload: ReapSubscriptionReservationsRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state, "redis", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    period_desc = None
    if payload.period_key:
        schema = SubscriptionBudgetLimiter.CONTROL_PLANE_SCHEMA
        table = SubscriptionBudgetLimiter.BUDGET_TABLE
        sql = f"""
            SELECT period_key, period_start, period_end
            FROM {schema}.{table}
            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
        """
        async with pg_pool.acquire() as c:
            row = await c.fetchrow(sql, settings.TENANT, settings.PROJECT, payload.user_id, payload.period_key)
        if not row:
            raise HTTPException(status_code=404, detail="subscription period not found")
        period_desc = {
            "period_key": row["period_key"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
        }
    else:
        mgr = _get_control_plane_manager(router)
        sub = await mgr.subscription_mgr.get_subscription(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
        )
        if not sub:
            raise HTTPException(status_code=404, detail="subscription not found")
        from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
        period_desc = build_subscription_period_descriptor(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            provider=getattr(sub, "provider", "internal") or "internal",
            stripe_subscription_id=getattr(sub, "stripe_subscription_id", None),
            period_end=getattr(sub, "next_charge_at", None),
            period_start=getattr(sub, "last_charged_at", None),
        )

    limiter = SubscriptionBudgetLimiter(
        pg_pool=pg_pool,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=payload.user_id,
        period_key=period_desc["period_key"],
        period_start=period_desc["period_start"],
        period_end=period_desc["period_end"],
    )
    project_budget = ProjectBudgetLimiter(redis, pg_pool, tenant=settings.TENANT, project=settings.PROJECT)
    expired = await limiter.reap_expired_reservations(limit=payload.limit, project_budget=project_budget)
    bal = await limiter.get_subscription_budget_balance()

    return {
        "status": "ok",
        "user_id": payload.user_id,
        "period_key": period_desc["period_key"],
        "expired": int(expired),
        "subscription_balance": bal,
    }

@router.post("/subscriptions/reservations/reap-all")
async def reap_subscription_reservations_all(
        payload: ReapAllSubscriptionReservationsRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state, "redis", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    schema = SubscriptionBudgetLimiter.CONTROL_PLANE_SCHEMA
    res_table = SubscriptionBudgetLimiter.RESERVATIONS_TABLE
    bud_table = SubscriptionBudgetLimiter.BUDGET_TABLE

    clauses = ["r.tenant=$1", "r.project=$2", "r.status='active'", "r.expires_at <= $3"]
    args: list = [settings.TENANT, settings.PROJECT, datetime.utcnow().replace(tzinfo=timezone.utc)]
    idx = 4
    if payload.user_id:
        clauses.append(f"r.user_id=${idx}")
        args.append(payload.user_id)
        idx += 1

    sql = f"""
        SELECT r.user_id, r.period_key, b.period_start, b.period_end
        FROM {schema}.{res_table} r
        JOIN {schema}.{bud_table} b
          ON b.tenant = r.tenant
         AND b.project = r.project
         AND b.user_id = r.user_id
         AND b.period_key = r.period_key
        WHERE {" AND ".join(clauses)}
        GROUP BY r.user_id, r.period_key, b.period_start, b.period_end
        ORDER BY MIN(r.expires_at) ASC
        LIMIT {int(payload.limit_periods)}
    """

    async with pg_pool.acquire() as c:
        rows = await c.fetch(sql, *args)

    if not rows:
        return {"status": "ok", "periods_processed": 0, "expired": 0}

    project_budget = ProjectBudgetLimiter(redis, pg_pool, tenant=settings.TENANT, project=settings.PROJECT)
    periods_processed = 0
    expired_total = 0
    processed_keys: list[str] = []

    for row in rows:
        limiter = SubscriptionBudgetLimiter(
            pg_pool=pg_pool,
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=str(row["user_id"]),
            period_key=str(row["period_key"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
        )
        expired = await limiter.reap_expired_reservations(
            limit=payload.per_period_limit,
            project_budget=project_budget,
        )
        expired_total += int(expired or 0)
        periods_processed += 1
        processed_keys.append(str(row["period_key"]))

    return {
        "status": "ok",
        "periods_processed": periods_processed,
        "expired": expired_total,
        "period_keys": processed_keys,
    }

@router.post("/subscriptions/internal/renew-once")
async def renew_internal_subscription_once(
        payload: InternalRenewOnceRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)

    try:
        res = await mgr.subscription_mgr.renew_internal_subscription_once(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            subscription_budget=None,
            charged_at=payload.charge_at,
            idempotency_key=payload.idempotency_key,
            actor=session.username or session.user_id,
        )
        return {
            "status": res.status,
            "action": res.action,
            "message": res.message,
            "external_id": res.external_id,
            "user_id": res.user_id,
            "usd_amount": res.usd_amount,
            "charged_at": res.charged_at.isoformat(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscriptions/cancel")
async def cancel_subscription(
        payload: CancelSubscriptionRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)

    if not payload.user_id and not payload.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="user_id or stripe_subscription_id is required")

    # Internal subscriptions: cancel immediately (no Stripe)
    if payload.user_id:
        sub = await mgr.subscription_mgr.get_subscription(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
        )
        if sub and sub.provider == "internal":
            # Direct update for internal
            async with pg_pool.acquire() as conn:
                await conn.execute(f"""
                    UPDATE {mgr.subscription_mgr.CP}.{mgr.subscription_mgr.TABLE}
                    SET status='canceled', next_charge_at=NULL, updated_at=NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND provider='internal'
                """, settings.TENANT, settings.PROJECT, payload.user_id)
            return {"status": "ok", "action": "applied", "message": "Internal subscription canceled"}

    from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsAdminService
    svc = StripeEconomicsAdminService(
        pg_pool=pg_pool,
        user_credits_mgr=mgr.user_credits_mgr,
        subscription_mgr=mgr.subscription_mgr,
        ref_provider=REF_PROVIDER,
        ref_model=REF_MODEL,
    )

    try:
        res = await svc.request_subscription_cancel(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            stripe_subscription_id=payload.stripe_subscription_id,
            notes=payload.notes,
            actor=session.username or session.user_id,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Subscription cancel failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/subscriptions/budget/topup")
async def topup_subscription_budget(
        payload: TopUpSubscriptionBudgetRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    mgr = _get_control_plane_manager(router)
    sub = await mgr.subscription_mgr.get_subscription(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=payload.user_id,
    )
    if not sub:
        raise HTTPException(status_code=404, detail="subscription not found")
    from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
    period_desc = build_subscription_period_descriptor(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=payload.user_id,
        provider=getattr(sub, "provider", "internal") or "internal",
        stripe_subscription_id=getattr(sub, "stripe_subscription_id", None),
        period_end=getattr(sub, "next_charge_at", None),
        period_start=getattr(sub, "last_charged_at", None),
    )

    limiter = SubscriptionBudgetLimiter(
        pg_pool=pg_pool,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=payload.user_id,
        period_key=period_desc["period_key"],
        period_start=period_desc["period_start"],
        period_end=period_desc["period_end"],
    )

    notes = payload.notes or f"admin topup by {session.username or session.user_id}"
    if payload.force_topup:
        notes = f"{notes} [force]" if notes else f"admin force topup by {session.username or session.user_id}"

    res = await limiter.topup_subscription_budget(
        usd_amount=float(payload.usd_amount),
        notes=notes,
        request_id=f"admin:subscription_topup:{payload.user_id}",
        allow_multiple_topups=bool(payload.force_topup),
    )
    return {"status": "ok", "force_topup": bool(payload.force_topup), **res}

@router.post("/subscriptions/rollover/sweep")
async def sweep_subscription_rollovers(
        payload: SweepSubscriptionRolloversRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)
    now = datetime.now(timezone.utc)

    if payload.user_id:
        sub = await mgr.subscription_mgr.get_subscription(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
        )
        if not sub:
            raise HTTPException(status_code=404, detail="subscription not found")
        if not sub.next_charge_at or sub.next_charge_at > now:
            return {
                "status": "ok",
                "action": "not_due",
                "message": "subscription not due for rollover",
                "user_id": payload.user_id,
                "next_charge_at": sub.next_charge_at.isoformat() if sub.next_charge_at else None,
            }

        from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
        period_desc = build_subscription_period_descriptor(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            provider=getattr(sub, "provider", "internal") or "internal",
            stripe_subscription_id=getattr(sub, "stripe_subscription_id", None),
            period_end=sub.next_charge_at,
            period_start=sub.last_charged_at,
        )
        subscription_budget = SubscriptionBudgetLimiter(
            pg_pool=pg_pool,
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            period_key=period_desc["period_key"],
            period_start=period_desc["period_start"],
            period_end=period_desc["period_end"],
        )
        project_budget = ProjectBudgetLimiter(redis, pg_pool, tenant=settings.TENANT, project=settings.PROJECT)
        period_key = period_desc["period_key"]

        res = await mgr.subscription_mgr.rollover_unused_balance_once(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            subscription_budget=subscription_budget,
            project_budget=project_budget,
            period_key=period_key,
            period_end=period_desc["period_end"],
            actor=session.username or session.user_id,
        )
        return {"status": "ok", **res, "user_id": payload.user_id}

    def project_budget_factory(tenant: str, project: str) -> ProjectBudgetLimiter:
        return ProjectBudgetLimiter(redis, pg_pool, tenant=tenant, project=project)

    res = await mgr.subscription_mgr.sweep_due_subscription_rollovers(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        now=now,
        limit=payload.limit,
        project_budget_factory=project_budget_factory,
        actor=session.username or session.user_id,
    )
    return res


# ============================================================================
# Stripe
# ============================================================================
@router.post("/subscriptions/create", status_code=201)
async def create_subscription(
        payload: CreateSubscriptionRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)

    tenant, project = settings.TENANT, settings.PROJECT

    plan = await mgr.subscription_mgr.get_plan(
        tenant=tenant,
        project=project,
        plan_id=payload.plan_id,
        include_inactive=True,
    )
    if not plan:
        raise HTTPException(status_code=404, detail="subscription plan not found")

    if payload.provider.lower() == "internal":
        if plan.provider != "internal":
            raise HTTPException(status_code=400, detail="plan provider mismatch (expected internal)")

        sub = await mgr.subscription_mgr.ensure_subscription_for_user(
            tenant=tenant,
            project=project,
            user_id=payload.user_id,
            plan_id=plan.plan_id,
        )
        return {
            "status": "ok",
            "provider": "internal",
            "subscription": {
                "plan_id": sub.plan_id,
                "status": sub.status,
            },
        }

    # stripe
    if plan.provider != "stripe":
        raise HTTPException(status_code=400, detail="plan provider mismatch (expected stripe)")

    from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeSubscriptionService
    svc = StripeSubscriptionService(
        pg_pool=pg_pool,
        subscription_mgr=mgr.subscription_mgr,
        default_tenant=tenant,
        default_project=project,
    )

    res = await svc.create_subscription(
        tenant=tenant,
        project=project,
        user_id=payload.user_id,
        plan_id=plan.plan_id,
        stripe_price_id=payload.stripe_price_id,
        stripe_customer_id=payload.stripe_customer_id,
        monthly_price_cents_hint=payload.monthly_price_cents_hint,
        metadata={"created_by": session.username or session.user_id},
    )

    return {
        "status": res.status,
        "action": res.action,
        "message": res.message,
        "stripe_customer_id": res.stripe_customer_id,
        "stripe_subscription_id": res.stripe_subscription_id,
        "stripe_latest_invoice_id": res.stripe_latest_invoice_id,
        "stripe_payment_intent_id": res.stripe_payment_intent_id,
    }

@router.post("/webhooks/stripe")
async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(None, alias="Stripe-Signature"),
):
    settings = get_settings()

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    mgr = _get_control_plane_manager(router)
    user_credits_mgr = mgr.user_credits_mgr
    subscription_mgr = mgr.subscription_mgr

    def subscription_budget_factory(tenant: str, project: str, user_id: str, period_key: str, period_start: datetime, period_end: datetime) -> SubscriptionBudgetLimiter:
        return SubscriptionBudgetLimiter(
            pg_pool=pg_pool,
            tenant=tenant,
            project=project,
            user_id=user_id,
            period_key=period_key,
            period_start=period_start,
            period_end=period_end,
        )
    def project_budget_factory(tenant: str, project: str) -> ProjectBudgetLimiter:
        return ProjectBudgetLimiter(redis, pg_pool, tenant=tenant, project=project)

    handler = StripeEconomicsWebhookHandler(
        pg_pool=pg_pool,
        user_credits_mgr=user_credits_mgr,
        subscription_budget_factory=subscription_budget_factory,
        project_budget_factory=project_budget_factory,
        subscription_mgr=subscription_mgr,
        default_tenant=settings.TENANT,
        default_project=settings.PROJECT,
        stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
    )

    body = await request.body()
    result = await handler.handle_webhook(body=body, stripe_signature=stripe_signature)

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result

@router.post("/stripe/reconcile")
async def reconcile_stripe_requests(
        payload: StripeReconcileRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)
    from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsAdminService

    svc = StripeEconomicsAdminService(
        pg_pool=pg_pool,
        user_credits_mgr=mgr.user_credits_mgr,
        subscription_mgr=mgr.subscription_mgr,
        ref_provider=REF_PROVIDER,
        ref_model=REF_MODEL,
    )

    try:
        res = await svc.reconcile_pending_requests(kind=payload.kind, limit=payload.limit)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Stripe reconcile failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stripe/pending")
async def list_pending_stripe_requests(
        kind: str = Query("all", description="all|wallet_refund|subscription_cancel"),
        limit: int = Query(200, ge=1, le=2000),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    kind = (kind or "all").lower()
    if kind not in ("all", "wallet_refund", "subscription_cancel"):
        raise HTTPException(status_code=400, detail="kind must be all|wallet_refund|subscription_cancel")

    schema = "kdcube_control_plane"
    tbl = "external_economics_events"
    where = ["source='internal'", "status='pending'", "tenant=$1", "project=$2"]
    args: list = [settings.TENANT, settings.PROJECT]
    idx = 3
    if kind != "all":
        where.append(f"kind=${idx}")
        args.append(kind)
        idx += 1

    sql = f"""
        SELECT kind, external_id, tenant, project, user_id,
               amount_cents, tokens, currency, status,
               metadata, created_at, updated_at
        FROM {schema}.{tbl}
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    args.extend([limit, offset])

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = []
    for r in rows:
        amt_c = r["amount_cents"]
        items.append({
            "kind": r["kind"],
            "external_id": r["external_id"],
            "tenant": r["tenant"],
            "project": r["project"],
            "user_id": r["user_id"],
            "amount_cents": int(amt_c) if amt_c is not None else None,
            "amount_usd": _usd_from_cents(int(amt_c)) if amt_c is not None else None,
            "tokens": int(r["tokens"]) if r["tokens"] is not None else None,
            "currency": r["currency"],
            "status": r["status"],
            "metadata": r["metadata"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })

    return {"status": "ok", "count": len(items), "items": items}


@router.get("/economics/pending")
async def list_pending_economics_events(
        kind: Optional[str] = Query(None, description="optional kind filter"),
        user_id: Optional[str] = Query(None),
        limit: int = Query(200, ge=1, le=2000),
        offset: int = Query(0, ge=0),
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    schema = "kdcube_control_plane"
    tbl = "external_economics_events"

    where = ["source='internal'", "status='pending'", "tenant=$1", "project=$2"]
    args: list = [settings.TENANT, settings.PROJECT]
    idx = 3
    if kind:
        where.append(f"kind=${idx}")
        args.append(kind)
        idx += 1
    if user_id:
        where.append(f"user_id=${idx}")
        args.append(user_id)
        idx += 1

    sql = f"""
        SELECT kind, external_id, tenant, project, user_id,
               amount_cents, tokens, currency, status,
               metadata, created_at, updated_at
        FROM {schema}.{tbl}
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    args.extend([limit, offset])

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    items = []
    for r in rows:
        amt_c = r["amount_cents"]
        items.append({
            "kind": r["kind"],
            "external_id": r["external_id"],
            "tenant": r["tenant"],
            "project": r["project"],
            "user_id": r["user_id"],
            "amount_cents": int(amt_c) if amt_c is not None else None,
            "amount_usd": _usd_from_cents(int(amt_c)) if amt_c is not None else None,
            "tokens": int(r["tokens"]) if r["tokens"] is not None else None,
            "currency": r["currency"],
            "status": r["status"],
            "metadata": r["metadata"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })

    return {"status": "ok", "count": len(items), "items": items}

# ============================================================================
# Policy Management Endpoints - Admin Only
# ============================================================================

@router.get("/policies/quota")
async def list_quota_policies(
        tenant: Optional[str] = Query(None),
        project: Optional[str] = Query(None),
        limit: int = Query(100),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    List plan quota policies (base policies by plan id).

    Shows configured policies for different plans (free, payasyougo, admin, etc.).
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        policies = await mgr.list_plan_quota_policies(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            limit=limit,
        )

        result = []
        for p in policies:
            result.append({
                "tenant": p.tenant,
                "project": p.project,
                "plan_id": p.plan_id,
                "max_concurrent": p.max_concurrent,
                "requests_per_day": p.requests_per_day,
                "requests_per_month": p.requests_per_month,
                "tokens_per_hour": p.tokens_per_hour,
                "tokens_per_day": p.tokens_per_day,
                "tokens_per_month": p.tokens_per_month,
                "usd_per_hour": _usd_from_tokens(p.tokens_per_hour),
                "usd_per_day": _usd_from_tokens(p.tokens_per_day),
                "usd_per_month": _usd_from_tokens(p.tokens_per_month),
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
                "notes": p.notes,
            })

        return {
            "status": "ok",
            "count": len(result),
            "policies": result,
        }
    except Exception as e:
        logger.exception("[list_quota_policies] Failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policies/quota", status_code=201)
async def set_quota_policy(
        payload: SetQuotaPolicyRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Set the quota policy for a plan.

    Defines base rate limits for different plans.
    Supports partial updates via COALESCE.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        tokens_per_hour = _tokens_from_usd(payload.usd_per_hour) if payload.usd_per_hour is not None else payload.tokens_per_hour
        tokens_per_day = _tokens_from_usd(payload.usd_per_day) if payload.usd_per_day is not None else payload.tokens_per_day
        tokens_per_month = _tokens_from_usd(payload.usd_per_month) if payload.usd_per_month is not None else payload.tokens_per_month

        policy = await mgr.set_tenant_project_plan_quota_policy(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            plan_id=payload.plan_id,
            max_concurrent=payload.max_concurrent,
            requests_per_day=payload.requests_per_day,
            requests_per_month=payload.requests_per_month,
            total_requests=payload.total_requests,
            tokens_per_hour=tokens_per_hour,
            tokens_per_day=tokens_per_day,
            tokens_per_month=tokens_per_month,
            created_by=session.username or session.user_id,
            notes=payload.notes,
        )

        logger.info(
            f"[set_quota_policy] {settings.TENANT}/{settings.PROJECT}/{payload.plan_id}: "
            f"policy updated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Quota policy set for {payload.plan_id}",
            "policy": {
                "plan_id": policy.plan_id,
                "requests_per_day": policy.requests_per_day,
                "tokens_per_day": policy.tokens_per_day,
                "tokens_per_day_usd": _usd_from_tokens(policy.tokens_per_day),
                "reference_model": f"{REF_PROVIDER}/{REF_MODEL}",
            }
        }
    except Exception as e:
        logger.exception(f"[set_quota_policy] Failed for {payload.plan_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/policies/budget")
async def list_budget_policies(
        tenant: Optional[str] = Query(None),
        project: Optional[str] = Query(None),
        limit: int = Query(100),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    List application budget policies (spending limits per provider).

    Shows configured spending limits for AI providers (Anthropic, OpenAI, etc.).

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        policies = await mgr.list_tenant_project_budget_policies(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            limit=limit,
        )

        result = []
        for p in policies:
            result.append({
                "tenant": p.tenant,
                "project": p.project,
                "provider": p.provider,
                "usd_per_hour": float(p.usd_per_hour) if p.usd_per_hour else None,
                "usd_per_day": float(p.usd_per_day) if p.usd_per_day else None,
                "usd_per_month": float(p.usd_per_month) if p.usd_per_month else None,
                "notes": p.notes,
            })

        return {
            "status": "ok",
            "count": len(result),
            "policies": result,
        }
    except Exception as e:
        logger.exception("[list_budget_policies] Failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policies/budget", status_code=201)
async def set_budget_policy(
        payload: SetBudgetPolicyRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Set budget policy for a provider.

    Configures spending limits to prevent runaway costs.
    Supports partial updates via COALESCE.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        policy = await mgr.set_tenant_project_budget_policy(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            provider=payload.provider,
            usd_per_hour=payload.usd_per_hour,
            usd_per_day=payload.usd_per_day,
            usd_per_month=payload.usd_per_month,
            created_by=session.username or session.user_id,
            notes=payload.notes,
        )

        logger.info(
            f"[set_budget_policy] {settings.TENANT}/{settings.PROJECT}/{payload.provider}: "
            f"policy updated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Budget policy set for {payload.provider}",
            "policy": {
                "provider": policy.provider,
                "usd_per_day": float(policy.usd_per_day) if policy.usd_per_day else None,
            }
        }
    except Exception as e:
        logger.exception(f"[set_budget_policy] Failed for {payload.provider}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# User Quota Breakdown & Utilities
# ============================================================================

@router.get("/users/{user_id}/quota-breakdown")
async def get_user_quota_breakdown(
        user_id: str,
        plan_id: Optional[str] = Query(None, description="Plan id override (free, payasyougo, admin, etc.)"),
        role: Optional[str] = Query(None, description="Optional role hint (registered, paid, privileged, anonymous)"),
        user_type: Optional[str] = Query(None, description="Deprecated: role hint (registered, paid, privileged, anonymous)"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get the detailed quota breakdown for a user.

    Shows base plan, plan override, effective policy, and current usage.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()
        redis = getattr(router.state.middleware, "redis", None)

        tenant = settings.TENANT
        project = settings.PROJECT

        role_hint = role or user_type
        resolved_plan_id, plan_source = await _resolve_plan_id_for_user(
            mgr=mgr,
            redis=redis,
            tenant=tenant,
            project=project,
            user_id=user_id,
            role=role_hint,
            explicit_plan_id=plan_id,
        )

        # Get base policy
        base_policy = await mgr.get_plan_quota_policy(
            tenant=tenant,
            project=project,
            plan_id=resolved_plan_id,
        )

        if not base_policy:
            raise HTTPException(
                status_code=404,
                detail=f"No policy found for plan_id={resolved_plan_id}"
            )

        # Get plan balance
        plan_override_balance = await mgr.get_user_plan_balance(
            tenant=tenant,
            project=project,
            user_id=user_id,
        )

        # Build rate limiter to read counters
        if not redis:
            raise HTTPException(status_code=503, detail="Redis not available")

        rl = UserEconomicsRateLimiter(redis)

        # Get usage breakdown (handles "*" for all bundles)
        usage_breakdown = await rl.breakdown(
            tenant=tenant,
            project=project,
            user_id=user_id,
            bundle_ids=["*"],
        )

        # Use totals for display
        totals = usage_breakdown["totals"]
        req_day = totals["requests_today"]
        req_month = totals["requests_this_month"]
        req_total = totals["requests_total"]
        tok_day = totals["tokens_today"]
        tok_month = totals["tokens_this_month"]

        # Merge policies (OVERRIDE semantics)
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import _merge_policy_with_plan_override

        # Convert plan_override_balance to QuotaPolicy with override semantics
        if plan_override_balance:
            effective_policy = _merge_policy_with_plan_override(base_policy, plan_override_balance)
        else:
            effective_policy = base_policy

        # Calculate remaining
        def calc_remaining(limit, used):
            if limit is None:
                return None
            return max(limit - used, 0)

        remaining_req_day = calc_remaining(effective_policy.requests_per_day, req_day)
        remaining_req_month = calc_remaining(effective_policy.requests_per_month, req_month)
        remaining_tok_day = calc_remaining(effective_policy.tokens_per_day, tok_day)
        remaining_tok_month = calc_remaining(effective_policy.tokens_per_month, tok_month)

        # Calculate percentage used
        percentage_used = None
        if effective_policy.requests_per_day and effective_policy.requests_per_day > 0:
            percentage_used = round((req_day / effective_policy.requests_per_day) * 100, 1)

        return {
            "status": "ok",
            "user_id": user_id,
            "role": role_hint,
            "plan_id": resolved_plan_id,
            "plan_source": plan_source,
            "bundle_breakdown": usage_breakdown.get("bundles"),
            "base_policy": {
                "max_concurrent": base_policy.max_concurrent,
                "requests_per_day": base_policy.requests_per_day,
                "requests_per_month": base_policy.requests_per_month,
                "tokens_per_day": base_policy.tokens_per_day,
                "tokens_per_month": base_policy.tokens_per_month,
            },
            "plan_override": {
                "has_override": plan_override_balance is not None and plan_override_balance.has_plan_override(),
                "max_concurrent": plan_override_balance.max_concurrent if plan_override_balance else None,
                "requests_per_day": plan_override_balance.requests_per_day if plan_override_balance else None,
                "requests_per_month": plan_override_balance.requests_per_month if plan_override_balance else None,
                "tokens_per_day": plan_override_balance.tokens_per_day if plan_override_balance else None,
                "tokens_per_month": plan_override_balance.tokens_per_month if plan_override_balance else None,
                "expires_at": plan_override_balance.expires_at.isoformat() if plan_override_balance and plan_override_balance.expires_at else None,
                "purchase_notes": plan_override_balance.grant_notes if plan_override_balance else None,
            } if plan_override_balance else None,
            "effective_policy": {
                "max_concurrent": effective_policy.max_concurrent,
                "requests_per_day": effective_policy.requests_per_day,
                "requests_per_month": effective_policy.requests_per_month,
                "tokens_per_day": effective_policy.tokens_per_day,
                "tokens_per_month": effective_policy.tokens_per_month,
            },
            "current_usage": {
                "requests_today": req_day,
                "requests_this_month": req_month,
                "requests_total": req_total,
                "tokens_today": tok_day,
                "tokens_this_month": tok_month,
                "concurrent": 0,  # Can't aggregate concurrent across bundles
            },
            "remaining": {
                "requests_today": remaining_req_day,
                "requests_this_month": remaining_req_month,
                "tokens_today": remaining_tok_day,
                "tokens_this_month": remaining_tok_month,
                "percentage_used": percentage_used,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[get_user_quota_breakdown] Failed for user {user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get quota breakdown: {str(e)}")

@router.get("/users/{user_id}/budget-breakdown")
async def get_user_budget_breakdown(
        user_id: str,
        plan_id: Optional[str] = Query(None, description="Plan id override (free, payasyougo, admin, etc.)"),
        role: Optional[str] = Query(None, description="Optional role hint (registered, paid, privileged, anonymous)"),
        user_type: Optional[str] = Query(None, description="Deprecated: role hint (registered, paid, privileged, anonymous)"),
        bundle_id: Optional[str] = Query(None, description="Optional bundle id for per-bundle windows"),
        include_expired_override: bool = Query(True),
        reservations_limit: int = Query(50, ge=0, le=500),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Full user budget breakdown (quota + plan override + usage + lifetime credits + reservations).
    REST layer is SQL-free: all orchestration lives in sdk/infra/economics/user_budget.py
    """
    mgr = _get_control_plane_manager(router)
    settings = get_settings()

    redis = getattr(router.state.middleware, "redis", None)
    role_hint = role or user_type
    resolved_plan_id, plan_source = await _resolve_plan_id_for_user(
        mgr=mgr,
        redis=redis,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
        role=role_hint,
        explicit_plan_id=plan_id,
    )

    base_policy = await mgr.get_plan_quota_policy(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        plan_id=resolved_plan_id,
    )
    if not base_policy:
        raise HTTPException(status_code=404, detail=f"No policy found for plan_id={resolved_plan_id}")

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import UserBudgetBreakdownService

    svc = UserBudgetBreakdownService(
        pg_pool=pg_pool,
        redis=redis,
        credits_mgr=mgr.user_credits_mgr,  # reuse existing manager instance
        subscription_mgr=mgr.subscription_mgr,
    )

    return await svc.get_user_budget_breakdown(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
        role=role_hint,
        plan_id=resolved_plan_id,
        plan_source=plan_source,
        base_policy=base_policy,
        include_expired_override=include_expired_override,
        reservations_limit=reservations_limit,
        bundle_ids=[bundle_id] if bundle_id else None,
    )

# ============================================================================
# App Budget Operations
# ============================================================================

@router.post("/app-budget/topup")
async def topup_app_budget(
        payload: TopUpAppBudgetRequest,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Top up application budget (company money).

    This is the TENANT/PROJECT budget used to pay for plan-funded requests.
    """
    try:
        settings = get_settings()

        pg_pool = getattr(router.state, "pg_pool", None)
        redis = getattr(router.state.middleware, "redis", None)

        limiter = ProjectBudgetLimiter(redis, pg_pool, tenant=settings.TENANT, project=settings.PROJECT)
        result = await limiter.topup_app_budget(
            usd_amount=payload.usd_amount,
            notes=payload.notes or f"Top-up by {session.username}",
            user_id=session.user_id,
        )

        logger.info(f"[topup_app_budget] {settings.TENANT}/{settings.PROJECT}: +${payload.usd_amount} by {session.username}")

        return {
            "status": "ok",
            "new_balance_usd": result["new_balance_usd"],
            "lifetime_added_usd": result["lifetime_added_usd"],
        }
    except Exception as e:
        logger.exception("[topup_app_budget] Failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/app-budget/balance")
async def get_app_budget_balance(
        session: UserSession = Depends(auth_without_pressure())
):
    """Get current app budget balance."""
    try:
        pg_pool = getattr(router.state, "pg_pool", None)
        redis = getattr(router.state.middleware, "redis", None)

        settings = get_settings()
        limiter = ProjectBudgetLimiter(redis, pg_pool, tenant=settings.TENANT, project=settings.PROJECT)

        balance = await limiter.get_app_budget_balance()
        spending = await limiter.get_spending_by_bundle()

        return {
            "status": "ok",
            "balance": balance,
            "current_month_spending": spending["totals"],
            "by_bundle": spending["bundles"],
        }
    except Exception as e:
        logger.exception("[get_app_budget_balance] Failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/app-budget/absorption-report")
async def get_app_budget_absorption_report(
        period: str = Query("month", description="day|month"),
        group_by: str = Query("none", description="none|user|bundle"),
        days: int = Query(90, ge=1, le=730),
        format: str = Query("json", description="json|csv"),
        session: UserSession = Depends(auth_without_pressure()),
):
    """
    Report when project budget absorbed wallet/plan shortfalls.
    Aggregated by period (day or month).
    """
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    period = (period or "month").lower()
    if period not in ("day", "month"):
        raise HTTPException(status_code=400, detail="period must be day|month")
    group_by = (group_by or "none").lower()
    if group_by not in ("none", "user", "bundle"):
        raise HTTPException(status_code=400, detail="group_by must be none|user|bundle")
    format = (format or "json").lower()
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format must be json|csv")

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    start = now - timedelta(days=int(days))

    period_sql = "date_trunc('day', created_at)" if period == "day" else "date_trunc('month', created_at)"
    group_sql = ""
    group_select = ""
    if group_by == "user":
        group_sql = ", user_id"
        group_select = ", user_id AS group_key"
    elif group_by == "bundle":
        group_sql = ", bundle_id"
        group_select = ", bundle_id AS group_key"
    sql = f"""
        SELECT
            {period_sql} AS period_start
            {group_select},
            SUM(CASE WHEN note LIKE 'shortfall:wallet_subscription%' THEN -amount_cents ELSE 0 END) AS wallet_subscription_shortfall_cents,
            SUM(CASE WHEN note LIKE 'shortfall:wallet_paid%' THEN -amount_cents ELSE 0 END) AS wallet_paid_shortfall_cents,
            SUM(CASE WHEN note LIKE 'shortfall:wallet_plan%' THEN -amount_cents ELSE 0 END) AS wallet_plan_shortfall_cents,
            SUM(CASE WHEN note LIKE 'shortfall:subscription_overage%' THEN -amount_cents ELSE 0 END) AS subscription_overage_shortfall_cents,
            SUM(CASE WHEN note LIKE 'shortfall:free_plan%' THEN -amount_cents ELSE 0 END) AS free_plan_shortfall_cents,
            SUM(-amount_cents) AS total_shortfall_cents,
            COUNT(*) AS events
        FROM kdcube_control_plane.tenant_project_budget_ledger
        WHERE tenant=$1 AND project=$2
          AND kind='spend'
          AND note LIKE 'shortfall:%'
          AND created_at >= $3 AND created_at <= $4
        GROUP BY period_start{group_sql}
        ORDER BY period_start DESC
    """

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, settings.TENANT, settings.PROJECT, start, now)

    items = []
    for r in rows:
        items.append({
            "period_start": r["period_start"],
            "group_key": r["group_key"] if group_by != "none" else None,
            "total_shortfall_usd": _usd_from_cents(int(r["total_shortfall_cents"] or 0)),
            "wallet_subscription_shortfall_usd": _usd_from_cents(int(r["wallet_subscription_shortfall_cents"] or 0)),
            "wallet_paid_shortfall_usd": _usd_from_cents(int(r["wallet_paid_shortfall_cents"] or 0)),
            "wallet_plan_shortfall_usd": _usd_from_cents(int(r["wallet_plan_shortfall_cents"] or 0)),
            "subscription_overage_shortfall_usd": _usd_from_cents(int(r["subscription_overage_shortfall_cents"] or 0)),
            "free_plan_shortfall_usd": _usd_from_cents(int(r["free_plan_shortfall_cents"] or 0)),
            "events": int(r["events"] or 0),
        })

    if format == "csv":
        header = [
            "period_start",
            "group_key" if group_by != "none" else None,
            "total_shortfall_usd",
            "wallet_subscription_shortfall_usd",
            "wallet_paid_shortfall_usd",
            "wallet_plan_shortfall_usd",
            "subscription_overage_shortfall_usd",
            "free_plan_shortfall_usd",
            "events",
        ]
        header = [h for h in header if h]
        lines = [",".join(header)]
        for row in items:
            cols = [
                str(row["period_start"]),
                str(row.get("group_key") or "") if group_by != "none" else None,
                f'{row["total_shortfall_usd"]:.2f}',
                f'{row["wallet_subscription_shortfall_usd"]:.2f}',
                f'{row["wallet_paid_shortfall_usd"]:.2f}',
                f'{row["wallet_plan_shortfall_usd"]:.2f}',
                f'{row["subscription_overage_shortfall_usd"]:.2f}',
                f'{row["free_plan_shortfall_usd"]:.2f}',
                str(row["events"]),
            ]
            cols = [c for c in cols if c is not None]
            lines.append(",".join(cols))
        csv_body = "\n".join(lines)
        return Response(content=csv_body, media_type="text/csv")

    return {"status": "ok", "period": period, "days": int(days), "group_by": group_by, "items": items}


@router.get("/economics/request-lineage")
async def get_request_lineage(
        request_id: str = Query(..., description="Turn ID / request_id"),
        session: UserSession = Depends(auth_without_pressure()),
):
    """
    Show money lineage for a single request_id (turn_id).
    Pulls budget reservations/ledgers and wallet reservation rows.
    """
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    if not pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    schema = "kdcube_control_plane"
    async with pg_pool.acquire() as conn:
        proj_resv = await conn.fetch(f"""
            SELECT reservation_id, amount_cents, actual_spent_cents, status,
                   created_at, expires_at, committed_at, released_at,
                   notes, bundle_id, provider, user_id, request_id
            FROM {schema}.tenant_project_budget_reservations
            WHERE tenant=$1 AND project=$2 AND request_id=$3
            ORDER BY created_at ASC
        """, settings.TENANT, settings.PROJECT, request_id)

        proj_ledger = await conn.fetch(f"""
            SELECT id, amount_cents, kind, note, bundle_id, provider, user_id, request_id, created_at
            FROM {schema}.tenant_project_budget_ledger
            WHERE tenant=$1 AND project=$2 AND request_id=$3
            ORDER BY created_at ASC
        """, settings.TENANT, settings.PROJECT, request_id)

        sub_resv = await conn.fetch(f"""
            SELECT reservation_id, user_id, period_key, amount_cents, actual_spent_cents, status,
                   created_at, expires_at, committed_at, released_at,
                   notes, bundle_id, provider, request_id
            FROM {schema}.user_subscription_period_reservations
            WHERE tenant=$1 AND project=$2 AND request_id=$3
            ORDER BY created_at ASC
        """, settings.TENANT, settings.PROJECT, request_id)

        sub_ledger = await conn.fetch(f"""
            SELECT id, user_id, period_key, amount_cents, kind, note,
                   bundle_id, provider, request_id, created_at
            FROM {schema}.user_subscription_period_ledger
            WHERE tenant=$1 AND project=$2 AND request_id=$3
            ORDER BY created_at ASC
        """, settings.TENANT, settings.PROJECT, request_id)

        wallet_resv = await conn.fetch(f"""
            SELECT reservation_id, user_id, bundle_id, notes,
                   tokens_reserved, tokens_used, status,
                   created_at, expires_at, committed_at, released_at
            FROM {schema}.user_token_reservations
            WHERE tenant=$1 AND project=$2 AND reservation_id=$3
            ORDER BY created_at ASC
        """, settings.TENANT, settings.PROJECT, request_id)

    def _row_to_dict(r):
        return dict(r)

    return {
        "status": "ok",
        "request_id": request_id,
        "project_budget": {
            "reservations": [_row_to_dict(r) for r in proj_resv],
            "ledger": [
                {
                    **_row_to_dict(r),
                    "amount_usd": _usd_from_cents(int(r["amount_cents"] or 0)),
                } for r in proj_ledger
            ],
        },
        "subscription_budget": {
            "reservations": [_row_to_dict(r) for r in sub_resv],
            "ledger": [
                {
                    **_row_to_dict(r),
                    "amount_usd": _usd_from_cents(int(r["amount_cents"] or 0)),
                } for r in sub_ledger
            ],
        },
        "wallet": {
            "reservations": [_row_to_dict(r) for r in wallet_resv],
        },
        "notes": [
            "request_id is the turn_id used across ledger and reservation tables.",
            "Accounting cost breakdown is not stored in control-plane tables; use logs or accounting storage.",
        ],
    }


# ============================================================================
# Health & Admin Utilities
# ============================================================================

@router.get("/health")
async def health_check(
        session: UserSession = Depends(auth_without_pressure())
):
    """Health check for control plane."""
    try:
        mgr = _get_control_plane_manager(router)
        return {
            "status": "ok",
            "service": "control_plane",
            "postgresql": "ready" if mgr._pg_pool else "not initialized",
            "redis": "ready" if mgr._redis else "not initialized",
        }
    except Exception as e:
        logger.exception("[health_check] Failed")
        return {
            "status": "error",
            "service": "control_plane",
            "error": str(e)
        }


@router.get("/economics/reference")
async def get_economics_reference(
        session: UserSession = Depends(auth_without_pressure())
):
    """Return USD/token reference for admin UI conversions."""
    try:
        usd_per_token = llm_output_price_usd_per_token(REF_PROVIDER, REF_MODEL)
        return {
            "status": "ok",
            "reference_provider": REF_PROVIDER,
            "reference_model": REF_MODEL,
            "usd_per_token": float(usd_per_token),
        }
    except Exception as e:
        logger.exception("[get_economics_reference] Failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear-cache")
async def admin_clear_cache(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Clear cached control plane manager (forces recreation with fresh connections).

    Useful after configuration changes or to troubleshoot caching issues.
    """
    try:
        if hasattr(request.app.state, "control_plane_manager"):
            delattr(request.app.state, "control_plane_manager")

        return {
            "status": "ok",
            "message": "Control plane cache cleared"
        }
    except Exception as e:
        logger.exception("[admin_clear_cache] Failed")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")

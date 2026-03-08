# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/api/economics/stripe_router.py

from typing import Optional
import json
import logging
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, APIRouter, Query, Header
import stripe

from kdcube_ai_app.apps.chat.api.resolvers import auth_without_pressure
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import (
    StripeEconomicsWebhookHandler,
    StripeEconomicsAdminService,
    StripeSubscriptionService,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.infra.accounting.usage import (
    anthropic,
    sonnet_45,
)

logger = logging.getLogger(__name__)

router = APIRouter()

REF_PROVIDER = anthropic
REF_MODEL = sonnet_45

def _usd_from_cents(cents: Optional[int]) -> Optional[float]:
    if cents is None:
        return None
    return float(cents) / 100.0

def _get_stripe():
    api_key = get_secret("STRIPE_SECRET_KEY") or get_secret("STRIPE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Stripe API key not configured")
    stripe.api_key = api_key
    return stripe

def _get_control_plane_manager(ctx):
    from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager
    mgr = getattr(ctx.state, "control_plane_manager", None)
    if mgr:
        return mgr
    pg_pool = getattr(ctx.state, "pg_pool", None)
    redis = getattr(ctx.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(
            status_code=503,
            detail="Control plane dependencies not initialized"
        )
    mgr = ControlPlaneManager(pg_pool=pg_pool, redis=redis)
    ctx.state.control_plane_manager = mgr
    return mgr

async def _resolve_stripe_customer(mgr, stripe_client, tenant: str, project: str, user_id: str) -> str:
    """Helper to find or create a Stripe Customer and link it to our DB."""
    sub = await mgr.subscription_mgr.get_subscription(
        tenant=tenant, project=project, user_id=user_id
    )
    stripe_customer_id = getattr(sub, "stripe_customer_id", None) if sub else None

    if not stripe_customer_id:
        md = {
            "tenant": tenant,
            "project": project,
            "user_id": user_id,
        }
        customer = stripe_client.Customer.create(
            metadata=md,
            description=f"User {user_id} from {tenant}/{project}"
        )
        stripe_customer_id = customer.id
        
        pg_pool = getattr(router.state, "pg_pool", None)
        if pg_pool:
            async with pg_pool.acquire() as conn:
                await conn.execute(f"""
                    INSERT INTO kdcube_control_plane.user_subscriptions (
                        tenant, project, user_id, status, provider, stripe_customer_id, updated_at
                    ) VALUES ($1, $2, $3, 'active', 'stripe', $4, NOW())
                    ON CONFLICT (tenant, project, user_id)
                    DO UPDATE SET 
                        stripe_customer_id = EXCLUDED.stripe_customer_id,
                        updated_at = NOW()
                    WHERE kdcube_control_plane.user_subscriptions.stripe_customer_id IS NULL
                """, tenant, project, user_id, stripe_customer_id)
                
    return stripe_customer_id

# ============================================================================
# Request Models
# ============================================================================

class CreateCheckoutTopupRequest(BaseModel):
    amount_usd: float = Field(..., gt=0.5, description="Amount in USD (min $0.50)")
    success_url: str = Field(..., description="URL to redirect after successful payment")
    cancel_url: str = Field(..., description="URL to redirect after cancelled payment")

class CreateCheckoutSubscriptionRequest(BaseModel):
    plan_id: str = Field(..., description="Internal plan ID")
    success_url: str = Field(..., description="URL to redirect after successful payment")
    cancel_url: str = Field(..., description="URL to redirect after cancelled payment")

class CreateSubscriptionRequest(BaseModel):
    user_id: str
    plan_id: str = Field(..., description="Subscription plan id")
    provider: str = Field("stripe", description="stripe|internal")

    # Stripe params
    stripe_price_id: str | None = None
    stripe_customer_id: str | None = None
    monthly_price_cents_hint: int | None = None

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

# ============================================================================
# Stripe Endpoints
# ============================================================================

@router.post("/checkout/topup", status_code=201)
async def create_checkout_topup(
        payload: CreateCheckoutTopupRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    """Create a Stripe Checkout Session for a one-time lifetime credits purchase."""
    settings = get_settings()
    mgr = _get_control_plane_manager(router)
    stripe_client = _get_stripe()
    
    tenant, project = settings.TENANT, settings.PROJECT
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    stripe_customer_id = await _resolve_stripe_customer(mgr, stripe_client, tenant, project, user_id)

    md = {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "kdcube_invoice_kind": "wallet_topup",
    }

    try:
        amount_cents = int(round(payload.amount_usd * 100))
        checkout_session = stripe_client.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"AI Tokens Top-up (${payload.amount_usd})",
                        "description": f"Lifetime credits for user {user_id}",
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            metadata=md,
            payment_intent_data={"metadata": md},
        )
        return {"status": "ok", "session_id": checkout_session.id, "checkout_url": checkout_session.url}
    except Exception as e:
        logger.exception("Failed to create Stripe checkout session (topup)")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/checkout/subscription", status_code=201)
async def create_checkout_subscription(
        payload: CreateCheckoutSubscriptionRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    """Create a Stripe Checkout Session for a recurring subscription."""
    settings = get_settings()
    mgr = _get_control_plane_manager(router)
    stripe_client = _get_stripe()
    
    tenant, project = settings.TENANT, settings.PROJECT
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    plan = await mgr.subscription_mgr.get_plan(
        tenant=tenant, project=project, plan_id=payload.plan_id, include_inactive=False
    )
    if not plan or not plan.stripe_price_id:
        raise HTTPException(status_code=404, detail=f"Active Stripe plan not found: {payload.plan_id}")

    stripe_customer_id = await _resolve_stripe_customer(mgr, stripe_client, tenant, project, user_id)

    md = {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "plan_id": plan.plan_id,
        "kdcube_invoice_kind": "subscription",
    }

    try:
        checkout_session = stripe_client.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            mode="subscription",
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            metadata=md,
            subscription_data={"metadata": md},
        )
        return {"status": "ok", "session_id": checkout_session.id, "checkout_url": checkout_session.url}
    except Exception as e:
        logger.exception("Failed to create Stripe checkout session (subscription)")
        raise HTTPException(status_code=500, detail=str(e))

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
        stripe_webhook_secret=get_secret("STRIPE_WEBHOOK_SECRET"),
    )

    body = await request.body()
    result = await handler.handle_webhook(body=body, stripe_signature=stripe_signature)

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result

@router.post("/wallet/refund")
async def refund_wallet(
        payload: WalletRefundRequest,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)
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

    if payload.user_id:
        sub = await mgr.subscription_mgr.get_subscription(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
        )
        if sub and sub.provider == "internal":
            async with pg_pool.acquire() as conn:
                await conn.execute(f"""
                    UPDATE {mgr.subscription_mgr.CP}.{mgr.subscription_mgr.TABLE}
                    SET status='canceled', next_charge_at=NULL, updated_at=NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND provider='internal'
                """, settings.TENANT, settings.PROJECT, payload.user_id)
            return {"status": "ok", "action": "applied", "message": "Internal subscription canceled"}

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

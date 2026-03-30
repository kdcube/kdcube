# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/admin.py

import logging
from typing import Optional

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Query, APIRouter

from kdcube_ai_app.apps.chat.ingress.resolvers import auth_without_pressure
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import (
    StripeEconomicsAdminService,
    StripeSubscriptionService,
)

from .stripe_router import router, _get_control_plane_manager, _usd_from_cents, REF_PROVIDER, REF_MODEL

logger = logging.getLogger(__name__)

admin_router = APIRouter()


class CreateSubscriptionRequest(BaseModel):
    user_id: str
    plan_id: str = Field(..., description="Subscription plan id")
    provider: str = Field("stripe", description="stripe|internal")
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


@admin_router.post("/admin/subscriptions/create", status_code=201)
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


@admin_router.post("/admin/stripe/wallet/refund")
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


@admin_router.post("/admin/subscriptions/cancel")
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


@admin_router.post("/admin/stripe/reconcile")
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


@admin_router.get("/admin/stripe/pending")
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

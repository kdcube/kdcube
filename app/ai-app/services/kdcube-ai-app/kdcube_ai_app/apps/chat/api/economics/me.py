# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/api/economics/me.py

import logging

from fastapi import Depends, HTTPException, Query, APIRouter

from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.api.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsAdminService

from .stripe_router import router, _get_stripe, _get_control_plane_manager, REF_PROVIDER, REF_MODEL

logger = logging.getLogger(__name__)

me_router = APIRouter()


@me_router.get("/me/budget-breakdown")
async def get_my_budget_breakdown(
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Budget breakdown for the currently authenticated user."""
    settings = get_settings()
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)

    from kdcube_ai_app.apps.chat.api.control_plane.control_plane import _resolve_plan_id_for_user
    resolved_plan_id, plan_source = await _resolve_plan_id_for_user(
        mgr=mgr,
        redis=redis,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
        role=None,
        explicit_plan_id=None,
    )

    base_policy = await mgr.get_plan_quota_policy(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        plan_id=resolved_plan_id,
    )
    if not base_policy:
        raise HTTPException(status_code=404, detail=f"No quota policy for plan_id={resolved_plan_id}")

    from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import UserBudgetBreakdownService
    svc = UserBudgetBreakdownService(
        pg_pool=pg_pool,
        redis=redis,
        credits_mgr=mgr.user_credits_mgr,
        subscription_mgr=mgr.subscription_mgr,
    )

    return await svc.get_user_budget_breakdown(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
        role=None,
        plan_id=resolved_plan_id,
        plan_source=plan_source,
        base_policy=base_policy,
        include_expired_override=True,
        reservations_limit=50,
        bundle_ids=None,
    )


@me_router.get("/me/subscription")
async def get_my_subscription(
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Subscription record for the currently authenticated user."""
    settings = get_settings()
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    mgr = _get_control_plane_manager(router)
    sub = await mgr.subscription_mgr.get_subscription(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
    )
    return {
        "status": "ok",
        "subscription": sub.__dict__ if sub else None,
    }


@me_router.get("/me/subscription-plans")
async def list_my_subscription_plans(
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """List active Stripe subscription plans available to the user."""
    settings = get_settings()
    mgr = _get_control_plane_manager(router)
    plans = await mgr.subscription_mgr.list_plans(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        provider="stripe",
        active_only=True,
        limit=200,
        offset=0,
    )
    return {"status": "ok", "count": len(plans), "plans": [p.__dict__ for p in plans]}


@me_router.post("/me/subscription/cancel")
async def cancel_my_subscription(
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Cancel the authenticated user's subscription at end of current billing period."""
    settings = get_settings()
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)
    if not pg_pool or not redis:
        raise HTTPException(status_code=503, detail="Dependencies not initialized")

    mgr = _get_control_plane_manager(router)
    sub = await mgr.subscription_mgr.get_subscription(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
    )
    if not sub or sub.status != 'active':
        raise HTTPException(status_code=404, detail="No active subscription found")

    if sub.provider == "internal":
        async with pg_pool.acquire() as conn:
            await conn.execute(f"""
                UPDATE {mgr.subscription_mgr.CP}.{mgr.subscription_mgr.TABLE}
                SET status='canceled', next_charge_at=NULL, updated_at=NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND provider='internal'
            """, settings.TENANT, settings.PROJECT, user_id)
        return {"status": "ok", "action": "applied", "message": "Subscription canceled"}

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
            user_id=user_id,
            actor=session.username or user_id,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("User subscription cancel failed")
        raise HTTPException(status_code=500, detail=str(e))


@me_router.post("/me/stripe/customer-portal")
async def create_my_customer_portal_session(
        return_url: str = Query(..., description="URL to return to after the portal session"),
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Create a Stripe Customer Portal session for the authenticated user."""
    settings = get_settings()
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    mgr = _get_control_plane_manager(router)
    stripe_client = _get_stripe()

    sub = await mgr.subscription_mgr.get_subscription(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        user_id=user_id,
    )
    stripe_customer_id = getattr(sub, "stripe_customer_id", None) if sub else None
    if not stripe_customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer found for this account")

    try:
        portal_session = stripe_client.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=return_url,
        )
        return {"status": "ok", "portal_url": portal_session.url}
    except Exception as e:
        logger.exception("Failed to create Stripe customer portal session")
        raise HTTPException(status_code=500, detail=str(e))

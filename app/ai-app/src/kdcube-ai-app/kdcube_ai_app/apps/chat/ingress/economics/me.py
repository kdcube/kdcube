# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/me.py

import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query, APIRouter

from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.ingress.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import GLOBAL_BUNDLE_ID
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsAdminService

from .stripe_router import router, _get_stripe, _get_control_plane_manager, REF_PROVIDER, REF_MODEL

logger = logging.getLogger(__name__)

me_router = APIRouter()


@me_router.get("/me/budget-breakdown")
async def get_my_budget_breakdown(
        bundle_id: str | None = Query(
            None,
            description="Optional bundle id. Defaults to __project__ so usage matches tenant/project-wide quota enforcement.",
        ),
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

    from kdcube_ai_app.apps.chat.ingress.control_plane.control_plane import _resolve_plan_id_for_user
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

    # This widget defaults to project-wide economics scope. We intentionally use
    # __project__ here so the breakdown matches runtime enforcement aggregated
    # across apps/bundles in the workspace, not the currently selected default bundle.
    resolved_bundle_id = bundle_id or GLOBAL_BUNDLE_ID

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
        bundle_ids=[resolved_bundle_id] if resolved_bundle_id else None,
    )


@me_router.get("/me/cost-breakdown")
async def get_my_cost_breakdown(
        date_from: str | None = Query(
            None, description="Start date YYYY-MM-DD (default: first of current month, UTC)"
        ),
        date_to: str | None = Query(
            None, description="End date YYYY-MM-DD inclusive (default: today, UTC)"
        ),
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Real (ledger-derived) spend for the authenticated user, broken down by model.

    Unlike /me/budget-breakdown (which reports quota-equivalent dollars: blended
    tokens priced at the reference model's OUTPUT rate), this reports actual spend
    computed per model with real input/output/cache rates from the price table.
    """
    settings = get_settings()
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    today = datetime.now(timezone.utc).date()
    df = date_from or today.replace(day=1).isoformat()
    dt = date_to or today.isoformat()

    # Authoritative source: the SQL usage ledger. Fall back to the file-based
    # accounting scan only if the DB is unavailable or has no rows for the window
    # (e.g. before the migration / sink is deployed).
    pg_pool = getattr(router.state, "pg_pool", None)
    res = None
    if pg_pool is not None:
        try:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.usage_ledger import UsageLedgerStore
            store = UsageLedgerStore(pg_pool, tenant=settings.TENANT, project=settings.PROJECT)
            if await store.has_data(date_from=df, date_to=dt):
                res = await store.cost_for_user(user_id=user_id, date_from=df, date_to=dt)
        except Exception:
            logger.exception("SQL usage ledger read failed; falling back to file accounting")
            res = None

    if res is None:
        from kdcube_ai_app.apps.chat.ingress.opex.cost import build_rate_calculator, cost_for_user
        calc = build_rate_calculator()
        try:
            res = await cost_for_user(
                calc,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                user_id=user_id,
                date_from=df,
                date_to=dt,
            )
        except Exception as e:
            logger.exception("User cost-breakdown failed")
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", **res}


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
    stripe_client = await _get_stripe()

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

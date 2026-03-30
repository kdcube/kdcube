# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/stripe_router.py

from typing import Optional
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import HTTPException, APIRouter, FastAPI
import stripe

from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.infra.accounting.usage import anthropic, sonnet_45

logger = logging.getLogger(__name__)

_stripe_reconcile_task: Optional[asyncio.Task] = None
_subscription_rollover_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def stripe_lifespan(app: FastAPI):
    """
    Stripe lifespan: start reconciliation and subscription rollover tasks on startup, stop on shutdown.
    Registered via app.add_event_handler in economics/__init__.py.
    """
    global _stripe_reconcile_task, _subscription_rollover_task
    from kdcube_ai_app.apps.chat.ingress.economics import routines

    if _stripe_reconcile_task is None and routines.stripe_reconcile_enabled():
        _stripe_reconcile_task = asyncio.create_task(routines.stripe_reconcile_scheduler_loop())
        logger.info("[Stripe Reconcile] Background scheduler task started")

    if _subscription_rollover_task is None and routines.subscription_rollover_enabled():
        _subscription_rollover_task = asyncio.create_task(routines.subscription_rollover_scheduler_loop())
        logger.info("[Subscription Rollover] Background scheduler task started")

    try:
        yield
    finally:
        for task, name in [
            (_stripe_reconcile_task, "Stripe Reconcile"),
            (_subscription_rollover_task, "Subscription Rollover"),
        ]:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                logger.info("[%s] Background scheduler task stopped", name)
        _stripe_reconcile_task = None
        _subscription_rollover_task = None


# State holder + lifespan registration
router = APIRouter(lifespan=stripe_lifespan)

REF_PROVIDER = anthropic
REF_MODEL = sonnet_45


def _usd_from_cents(cents: Optional[int]) -> Optional[float]:
    if cents is None:
        return None
    return float(cents) / 100.0


def _get_stripe():
    api_key = get_secret("services.stripe.secret_key")
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
    """Find or create a Stripe Customer and link it to our DB."""
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

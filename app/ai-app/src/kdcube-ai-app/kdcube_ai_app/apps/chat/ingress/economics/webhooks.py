# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/webhooks.py

import logging
from datetime import datetime

from fastapi import HTTPException, Request, APIRouter, Header

from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import StripeEconomicsWebhookHandler
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter

from .stripe_router import router, _get_control_plane_manager

logger = logging.getLogger(__name__)

webhook_router = APIRouter()


@webhook_router.post("/webhooks/stripe")
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

    def subscription_budget_factory(
        tenant: str, project: str, user_id: str,
        period_key: str, period_start: datetime, period_end: datetime,
    ) -> SubscriptionBudgetLimiter:
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
        stripe_webhook_secret=get_secret("services.stripe.webhook_secret"),
    )

    body = await request.body()
    result = await handler.handle_webhook(body=body, stripe_signature=stripe_signature)

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result

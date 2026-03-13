# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/api/economics/checkout.py

import logging

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, APIRouter

from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.api.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.config import get_settings

from .stripe_router import router, _get_stripe, _get_control_plane_manager, _resolve_stripe_customer

logger = logging.getLogger(__name__)

checkout_router = APIRouter()


class CreateCheckoutTopupRequest(BaseModel):
    amount_usd: float = Field(..., gt=0.5, description="Amount in USD (min $0.50)")
    success_url: str = Field(..., description="URL to redirect after successful payment")
    cancel_url: str = Field(..., description="URL to redirect after cancelled payment")


class CreateCheckoutSubscriptionRequest(BaseModel):
    plan_id: str = Field(..., description="Internal plan ID")
    success_url: str = Field(..., description="URL to redirect after successful payment")
    cancel_url: str = Field(..., description="URL to redirect after cancelled payment")


@checkout_router.post("/stripe/checkout/topup", status_code=201)
async def create_checkout_topup(
        payload: CreateCheckoutTopupRequest,
        session: UserSession = Depends(require_auth(RequireUser())),
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
            invoice_creation={"enabled": True},
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            metadata=md,
            payment_intent_data={"metadata": md},
        )
        return {"status": "ok", "session_id": checkout_session.id, "checkout_url": checkout_session.url}
    except Exception as e:
        logger.exception("Failed to create Stripe checkout session (topup)")
        raise HTTPException(status_code=500, detail=str(e))


@checkout_router.post("/stripe/checkout/subscription", status_code=201)
async def create_checkout_subscription(
        payload: CreateCheckoutSubscriptionRequest,
        session: UserSession = Depends(require_auth(RequireUser())),
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

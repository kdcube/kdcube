# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/checkout.py

import logging

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, APIRouter

from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.ingress.resolvers import require_auth
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.config_scopes import economics_stripe_checkout
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import TOPUP_NET_AMOUNT_METADATA_KEY
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import PlanChangeNotAllowed

from .stripe_router import router, _get_stripe, _get_control_plane_manager, _resolve_stripe_customer

logger = logging.getLogger(__name__)

checkout_router = APIRouter()


def checkout_tax_options() -> dict:
    """Checkout Session parameters for Stripe Tax, from economics.yaml
    `stripe.checkout`. Empty when tax is off (the default).

    `automatic_tax` travels with `billing_address_collection` and
    `customer_update.address`: both sessions attach a Customer, and Stripe can
    only compute tax for it from an address that is collected and written back.
    """
    cfg = economics_stripe_checkout()
    opts: dict = {}
    if bool(cfg.get("automatic_tax", False)):
        opts.update(
            automatic_tax={"enabled": True},
            billing_address_collection="required",
            customer_update={"address": "auto"},
        )
    if bool(cfg.get("tax_id_collection", False)):
        opts["tax_id_collection"] = {"enabled": True}
        # Stripe saves a collected tax id onto the attached Customer.
        opts.setdefault("customer_update", {})["name"] = "auto"
    return opts


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
    stripe_client = await _get_stripe()

    tenant, project = settings.TENANT, settings.PROJECT
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    stripe_customer_id = await _resolve_stripe_customer(mgr, stripe_client, tenant, project, user_id)

    # The pre-tax price. With Stripe Tax on, the PaymentIntent collects price +
    # tax; the webhook credits this figure, carried in the PaymentIntent metadata.
    amount_cents = int(round(payload.amount_usd * 100))
    tax_options = checkout_tax_options()

    md = {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "kdcube_invoice_kind": "wallet_topup",
        TOPUP_NET_AMOUNT_METADATA_KEY: str(amount_cents),
    }

    price_data = {
        "currency": "usd",
        "product_data": {
            "name": f"AI Tokens Top-up (${payload.amount_usd})",
            "description": f"Lifetime credits for user {user_id}",
        },
        "unit_amount": amount_cents,
    }
    if "automatic_tax" in tax_options:
        # The top-up is priced exclusive of tax; Stripe rejects an inline price
        # without a tax_behavior once automatic_tax is on.
        price_data["tax_behavior"] = "exclusive"

    try:
        checkout_session = stripe_client.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": price_data,
                "quantity": 1,
            }],
            mode="payment",
            invoice_creation={"enabled": True},
            **tax_options,
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
    stripe_client = await _get_stripe()

    tenant, project = settings.TENANT, settings.PROJECT
    user_id = session.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in session")

    plan = await mgr.subscription_mgr.get_plan(
        tenant=tenant, project=project, plan_id=payload.plan_id, include_inactive=False
    )
    if not plan or not plan.stripe_price_id:
        raise HTTPException(status_code=404, detail=f"Active Stripe plan not found: {payload.plan_id}")

    current = await mgr.subscription_mgr.get_subscription(
        tenant=tenant, project=project, user_id=user_id
    )
    try:
        mgr.subscription_mgr.assert_plan_change_allowed(current=current, target_plan=plan)
    except PlanChangeNotAllowed as e:
        raise HTTPException(status_code=409, detail={"code": e.code, "message": e.message})

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
            # A catalog plan's tax behaviour lives on its Stripe Price.
            **checkout_tax_options(),
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            metadata=md,
            subscription_data={"metadata": md},
        )
        return {"status": "ok", "session_id": checkout_session.id, "checkout_url": checkout_session.url}
    except Exception as e:
        logger.exception("Failed to create Stripe checkout session (subscription)")
        raise HTTPException(status_code=500, detail=str(e))

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/api/control_plane/control_plane.py

"""
Control Plane API

Provides REST endpoints for managing:
1. User quota replenishments (purchased/granted credits)
2. User quota policies (base policies by user type)
3. Application budget policies (spending limits per provider)

Includes Stripe webhook integration for automated credit purchases.

Admin-only access with similar patterns to OPEX API.
"""

from typing import Optional, List, Dict, Any
import logging
import hmac
import hashlib
import os
import json

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, APIRouter, Query, Header
from datetime import datetime, timedelta, timezone

from kdcube_ai_app.apps.chat.api.resolvers import auth_without_pressure
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.sessions import UserSession

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# ============================================================================
# Request/Response Models
# ============================================================================

class GrantTrialRequest(BaseModel):
    """
    Request to grant trial bonus to a user.

    **Example Use Case: 7-Day Trial**
    ```json
    {
        "user_id": "user123",
        "days": 7,
        "additional_requests_per_day": 100,
        "additional_tokens_per_day": 1000000,
        "bundle_id": "*",
        "notes": "Welcome trial for new user"
    }
    ```
    """
    user_id: str = Field(..., description="User ID")
    days: int = Field(7, description="Trial duration in days")
    additional_requests_per_day: int = Field(100, description="Additional requests per day")
    additional_tokens_per_day: int = Field(1_000_000, description="Additional tokens per day")
    bundle_id: str = Field("*", description="Bundle ID (* for all bundles)")
    notes: Optional[str] = Field(None, description="Notes about this grant")

class TopUpRequest(BaseModel):
    """
    Request to top up user credits manually.

    **Example Use Case: Manual Credit Purchase**
    ```json
    {
        "user_id": "user456",
        "bundle_id": "*",
        "additional_requests_per_day": 50,
        "additional_tokens_per_day": 5000000,
        "expires_in_days": 30,
        "purchase_amount_usd": 10.00,
        "notes": "Purchased $10 credits package"
    }
    ```

    **Example Use Case: VIP User Grant (Never Expires)**
    ```json
    {
        "user_id": "vip_user",
        "bundle_id": "*",
        "additional_requests_per_day": 1000,
        "additional_tokens_per_day": 100000000,
        "expires_in_days": null,
        "notes": "Permanent VIP credits"
    }
    ```
    """
    user_id: str = Field(..., description="User ID")
    bundle_id: str = Field("*", description="Bundle ID (* for all bundles)")
    additional_requests_per_day: Optional[int] = Field(None, description="Additional requests per day")
    additional_requests_per_month: Optional[int] = Field(None, description="Additional requests per month")
    additional_tokens_per_day: Optional[int] = Field(None, description="Additional tokens per day")
    additional_tokens_per_month: Optional[int] = Field(None, description="Additional tokens per month")
    additional_max_concurrent: Optional[int] = Field(None, description="Additional concurrent slots")
    expires_in_days: Optional[int] = Field(30, description="Days until expiration (None = never)")
    purchase_amount_usd: Optional[float] = Field(None, description="Purchase amount in USD")
    notes: Optional[str] = Field(None, description="Notes about this top-up")

class SetQuotaPolicyRequest(BaseModel):
    """
    Request to set quota policy for a user type.

    **Example Use Case: Free Tier Policy**
    ```json
    {
        "user_type": "free",
        "bundle_id": "*",
        "max_concurrent": 1,
        "requests_per_day": 10,
        "tokens_per_day": 100000,
        "notes": "Free tier limits"
    }
    ```

    **Example Use Case: Premium Tier Policy**
    ```json
    {
        "user_type": "premium",
        "bundle_id": "*",
        "max_concurrent": 10,
        "requests_per_day": 1000,
        "tokens_per_day": 50000000,
        "notes": "Premium tier limits"
    }
    ```
    """
    user_type: str = Field(..., description="User type (free, paid, premium, etc.)")
    bundle_id: str = Field("*", description="Bundle ID (* for all bundles)")
    max_concurrent: Optional[int] = Field(None, description="Max concurrent requests")
    requests_per_day: Optional[int] = Field(None, description="Requests per day")
    requests_per_month: Optional[int] = Field(None, description="Requests per month")
    total_requests: Optional[int] = Field(None, description="Total requests")
    tokens_per_hour: Optional[int] = Field(None, description="Tokens per hour")
    tokens_per_day: Optional[int] = Field(None, description="Tokens per day")
    tokens_per_month: Optional[int] = Field(None, description="Tokens per month")
    notes: Optional[str] = Field(None, description="Notes")

class SetBudgetPolicyRequest(BaseModel):
    """
    Request to set budget policy for a provider.

    **Example Use Case: Anthropic Daily Budget**
    ```json
    {
        "bundle_id": "kdcube.codegen.orchestrator",
        "provider": "anthropic",
        "usd_per_day": 200.00,
        "notes": "Anthropic daily spending limit"
    }
    ```

    **Example Use Case: OpenAI Monthly Budget**
    ```json
    {
        "bundle_id": "kdcube.codegen.orchestrator",
        "provider": "openai",
        "usd_per_month": 5000.00,
        "notes": "OpenAI monthly budget"
    }
    ```
    """
    bundle_id: str = Field(..., description="Bundle ID")
    provider: str = Field(..., description="Provider (anthropic, openai, brave, etc.)")
    usd_per_hour: Optional[float] = Field(None, description="USD per hour")
    usd_per_day: Optional[float] = Field(None, description="USD per day")
    usd_per_month: Optional[float] = Field(None, description="USD per month")
    notes: Optional[str] = Field(None, description="Notes")

# ============================================================================
# Helper Functions
# ============================================================================

def _get_control_plane_manager(router):
    """
    Get or create ControlPlaneManager instance.
    Matches pattern from opex.py for consistency.
    """
    from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager

    # Try to reuse cached instance
    mgr = getattr(router.state, "control_plane_manager", None)
    if mgr:
        return mgr

    # Create new manager
    settings = get_settings()

    # Get PostgreSQL pool and Redis from middleware (same pattern as opex.py)
    pg_pool = getattr(router.state, "pg_pool", None)
    redis = getattr(router.state.middleware, "redis", None)

    if not pg_pool or not redis:
        raise HTTPException(
            status_code=503,
            detail="Control plane dependencies not initialized"
        )

    mgr = ControlPlaneManager(pg_pool=pg_pool, redis=redis)

    # Cache on app state
    router.state.control_plane_manager = mgr
    return mgr

def _infer_bundle_id(router, bundle_id: Optional[str] = None) -> str:
    """
    Infer bundle ID from request or use default.
    Matches pattern from integrations.py.
    """
    if bundle_id and bundle_id != "*":
        return bundle_id

    # Try to get from app state (set during initialization)
    default_bundle = getattr(router.state, "default_bundle_id", None)
    if default_bundle:
        return default_bundle

    # Fallback to environment/registry
    from kdcube_ai_app.infra.plugin.bundle_registry import get_default_id
    return get_default_id() or "kdcube.codegen.orchestrator"

# ============================================================================
# Replenishment Endpoints - Admin Only
# ============================================================================

@router.post("/replenishments/grant-trial", status_code=201)
async def grant_trial_bonus(
        payload: GrantTrialRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Grant trial bonus to a user.

    **Use Cases:**
    1. **New User Registration**: Automatically grant 7-day trial when user signs up
    2. **Marketing Campaign**: Give extended trials during promotions
    3. **User Retention**: Re-engage churned users with trial credits
    4. **Support Compensation**: Resolve user issues with trial credits

    **Example: 7-Day Welcome Trial**
    ```bash
    POST /api/admin/control-plane/replenishments/grant-trial
    {
        "user_id": "user123",
        "days": 7,
        "additional_requests_per_day": 100,
        "additional_tokens_per_day": 1000000,
        "bundle_id": "*",
        "notes": "7-day welcome trial"
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.days)

        replenishment = await mgr.create_replenishment(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            bundle_id=payload.bundle_id,
            additional_requests_per_day=payload.additional_requests_per_day,
            additional_tokens_per_day=payload.additional_tokens_per_day,
            expires_at=expires_at,
            purchase_notes=payload.notes or f"{payload.days}-day trial bonus",
        )

        logger.info(
            f"[grant_trial_bonus] {settings.TENANT}/{settings.PROJECT}/{payload.user_id}: "
            f"{payload.days} days, {payload.additional_requests_per_day} req/day, "
            f"{payload.additional_tokens_per_day} tok/day"
        )

        return {
            "status": "ok",
            "message": f"Trial bonus granted to user {payload.user_id}",
            "replenishment": {
                "user_id": replenishment.user_id,
                "bundle_id": replenishment.bundle_id,
                "additional_requests_per_day": replenishment.additional_requests_per_day,
                "additional_tokens_per_day": replenishment.additional_tokens_per_day,
                "expires_at": replenishment.expires_at.isoformat() if replenishment.expires_at else None,
            }
        }
    except Exception as e:
        logger.exception(f"[grant_trial_bonus] Failed for user {payload.user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to grant trial: {str(e)}")

@router.post("/replenishments/top-up", status_code=201)
async def top_up_user_credits(
        payload: TopUpRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Top up user credits manually.

    **Use Cases:**
    1. **Manual Credit Purchase**: Process payments outside Stripe
    2. **Customer Support Compensation**: Resolve issues with credits
    3. **VIP User Grant**: Permanent credits for special users
    4. **Special Promotions**: Holiday bonuses, referral rewards
    5. **Beta Tester Credits**: Extra credits for testing new features

    **Example 1: Standard Credit Purchase (30 days)**
    ```bash
    POST /api/admin/control-plane/replenishments/top-up
    {
        "user_id": "user456",
        "bundle_id": "*",
        "additional_requests_per_day": 50,
        "additional_tokens_per_day": 5000000,
        "expires_in_days": 30,
        "purchase_amount_usd": 10.00,
        "notes": "$10 credits package"
    }
    ```

    **Example 2: VIP User (Never Expires)**
    ```bash
    POST /api/admin/control-plane/replenishments/top-up
    {
        "user_id": "vip_user",
        "bundle_id": "*",
        "additional_requests_per_day": 1000,
        "additional_tokens_per_day": 100000000,
        "expires_in_days": null,
        "notes": "Permanent VIP credits"
    }
    ```

    **Example 3: Support Compensation (7 days)**
    ```bash
    POST /api/admin/control-plane/replenishments/top-up
    {
        "user_id": "user789",
        "bundle_id": "kdcube.codegen.orchestrator",
        "additional_requests_per_day": 20,
        "expires_in_days": 7,
        "notes": "Apology for service disruption"
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        expires_at = None
        if payload.expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)

        replenishment = await mgr.create_replenishment(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=payload.user_id,
            bundle_id=payload.bundle_id,
            additional_requests_per_day=payload.additional_requests_per_day,
            additional_requests_per_month=payload.additional_requests_per_month,
            additional_tokens_per_day=payload.additional_tokens_per_day,
            additional_tokens_per_month=payload.additional_tokens_per_month,
            additional_max_concurrent=payload.additional_max_concurrent,
            expires_at=expires_at,
            purchase_amount_usd=payload.purchase_amount_usd,
            purchase_notes=payload.notes or "Manual top-up",
        )

        logger.info(
            f"[top_up_user_credits] {settings.TENANT}/{settings.PROJECT}/{payload.user_id}: "
            f"bundle={payload.bundle_id}, expires={expires_at.isoformat() if expires_at else 'never'}"
        )

        return {
            "status": "ok",
            "message": f"Credits topped up for user {payload.user_id}",
            "replenishment": {
                "user_id": replenishment.user_id,
                "bundle_id": replenishment.bundle_id,
                "additional_requests_per_day": replenishment.additional_requests_per_day,
                "additional_tokens_per_day": replenishment.additional_tokens_per_day,
                "expires_at": replenishment.expires_at.isoformat() if replenishment.expires_at else None,
            }
        }
    except Exception as e:
        logger.exception(f"[top_up_user_credits] Failed for user {payload.user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to top up credits: {str(e)}")

@router.get("/replenishments/user/{user_id}")
async def get_user_remaining_credits(
        user_id: str,
        request: Request,
        include_expired: bool = Query(False, description="Include expired credits"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get remaining credits for a specific user.

    Shows all active credit packages with expiration dates and amounts.

    **Use Cases:**
    1. **Admin Dashboard**: Monitor user credit balances
    2. **User Support**: Check credit status during support tickets
    3. **Usage Analytics**: Track credit consumption patterns

    **Example Response:**
    ```json
    {
        "status": "ok",
        "user_id": "user123",
        "credit_count": 2,
        "credits": [
            {
                "bundle_id": "*",
                "additional_requests_per_day": 100,
                "additional_tokens_per_day": 1000000,
                "expires_at": "2025-01-24T00:00:00Z",
                "purchase_id": null,
                "purchase_amount_usd": null,
                "is_expired": false
            },
            {
                "bundle_id": "kdcube.codegen.orchestrator",
                "additional_requests_per_day": 50,
                "additional_tokens_per_day": 5000000,
                "expires_at": "2025-02-17T00:00:00Z",
                "purchase_id": "pi_stripe_123",
                "purchase_amount_usd": 10.00,
                "is_expired": false
            }
        ]
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        replenishments = await mgr.list_user_replenishments(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
            include_expired=include_expired,
        )

        credits = []
        for r in replenishments:
            credits.append({
                "bundle_id": r.bundle_id,
                "additional_requests_per_day": r.additional_requests_per_day,
                "additional_requests_per_month": r.additional_requests_per_month,
                "additional_tokens_per_day": r.additional_tokens_per_day,
                "additional_tokens_per_month": r.additional_tokens_per_month,
                "additional_max_concurrent": r.additional_max_concurrent,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "purchase_id": r.purchase_id,
                "purchase_amount_usd": float(r.purchase_amount_usd) if r.purchase_amount_usd else None,
                "purchase_notes": r.purchase_notes,
                "is_expired": r.is_expired(),
                "is_active": r.active,
            })

        return {
            "status": "ok",
            "user_id": user_id,
            "credit_count": len(credits),
            "credits": credits,
        }
    except Exception as e:
        logger.exception(f"[get_user_remaining_credits] Failed for user {user_id}")
        raise HTTPException(status_code=500, detail=f"Failed to get user credits: {str(e)}")

@router.get("/replenishments/users")
async def list_users_with_credits(
        request: Request,
        tenant: Optional[str] = Query(None, description="Filter by tenant"),
        project: Optional[str] = Query(None, description="Filter by project"),
        limit: int = Query(100, description="Max results"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    List all users who have active credits.

    Returns aggregated view of users with credits for admin dashboard.

    **Use Cases:**
    1. **Admin Dashboard**: Overview of all users with purchased credits
    2. **Revenue Tracking**: Monitor credit sales and usage
    3. **User Segmentation**: Identify paying vs. free users
    4. **Support Prioritization**: See which users have active subscriptions

    **Example Response:**
    ```json
    {
        "status": "ok",
        "user_count": 42,
        "users": [
            {
                "tenant": "my-tenant",
                "project": "my-project",
                "user_id": "user123",
                "credit_count": 2,
                "total_additional_requests_per_day": 150,
                "total_additional_tokens_per_day": 6000000,
                "earliest_expiry": "2025-01-24T00:00:00Z",
                "latest_purchase": "2025-01-17T10:30:00Z"
            }
        ]
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        users = await mgr.list_users_with_credits(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            limit=limit,
        )

        # Format results
        result = []
        for user in users:
            result.append({
                "tenant": user["tenant"],
                "project": user["project"],
                "user_id": user["user_id"],
                "credit_count": user["credit_count"],
                "total_additional_requests_per_day": user["total_additional_requests_per_day"],
                "total_additional_tokens_per_day": user["total_additional_tokens_per_day"],
                "earliest_expiry": user["earliest_expiry"].isoformat() if user["earliest_expiry"] else None,
                "latest_purchase": user["latest_purchase"].isoformat() if user["latest_purchase"] else None,
            })

        return {
            "status": "ok",
            "user_count": len(result),
            "users": result,
        }
    except Exception as e:
        logger.exception("[list_users_with_credits] Failed")
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(e)}")

@router.get("/replenishments")
async def list_all_replenishments(
        request: Request,
        tenant: Optional[str] = Query(None, description="Filter by tenant"),
        project: Optional[str] = Query(None, description="Filter by project"),
        user_id: Optional[str] = Query(None, description="Filter by user ID"),
        bundle_id: Optional[str] = Query(None, description="Filter by bundle ID"),
        include_expired: bool = Query(False, description="Include expired"),
        limit: int = Query(100, description="Max results"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    List all replenishments with filters.

    Comprehensive view of all credit grants for advanced admin operations.

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        replenishments = await mgr.list_all_replenishments(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            user_id=user_id,
            bundle_id=bundle_id,
            include_expired=include_expired,
            limit=limit,
        )

        result = []
        for r in replenishments:
            result.append({
                "tenant": r.tenant,
                "project": r.project,
                "user_id": r.user_id,
                "bundle_id": r.bundle_id,
                "additional_requests_per_day": r.additional_requests_per_day,
                "additional_tokens_per_day": r.additional_tokens_per_day,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "purchase_id": r.purchase_id,
                "purchase_amount_usd": float(r.purchase_amount_usd) if r.purchase_amount_usd else None,
                "is_expired": r.is_expired(),
            })

        return {
            "status": "ok",
            "count": len(result),
            "replenishments": result,
        }
    except Exception as e:
        logger.exception("[list_all_replenishments] Failed")
        raise HTTPException(status_code=500, detail=f"Failed to list replenishments: {str(e)}")

@router.delete("/replenishments/{user_id}/{bundle_id}")
async def deactivate_replenishment(
        user_id: str,
        bundle_id: str,
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Deactivate (soft delete) a replenishment.

    **Use Cases:**
    1. **Refund Processing**: Remove credits after issuing refund
    2. **Abuse Prevention**: Revoke credits from fraudulent accounts
    3. **Policy Violation**: Remove credits for TOS violations

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        await mgr.deactivate_replenishment(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_id=user_id,
            bundle_id=bundle_id,
        )

        logger.info(
            f"[deactivate_replenishment] {settings.TENANT}/{settings.PROJECT}/{user_id}/{bundle_id}: "
            f"deactivated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Replenishment deactivated for user {user_id}",
        }
    except Exception as e:
        logger.exception(f"[deactivate_replenishment] Failed for {user_id}/{bundle_id}")
        raise HTTPException(status_code=500, detail=f"Failed to deactivate: {str(e)}")

# ============================================================================
# Stripe Webhook - Public Endpoint (No Auth)
# ============================================================================

@router.post("/webhooks/stripe")
async def stripe_webhook(
        request: Request,
        stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature")
):
    """
    Stripe webhook endpoint for automated credit purchases.

    **Webhook Events Handled:**
    - `payment_intent.succeeded` - Grant credits after successful payment

    **Setup Instructions:**
    1. Configure webhook in Stripe Dashboard → Webhooks
    2. Set webhook URL: `https://your-domain.com/webhooks/stripe`
    3. Set environment variable: `STRIPE_WEBHOOK_SECRET=whsec_xxx`
    4. Subscribe to events: `payment_intent.succeeded`

    **Payment Intent Metadata Format:**
    Include these fields in Stripe payment metadata:
    ```json
    {
        "user_id": "user123",
        "tenant": "my-tenant",
        "project": "my-project",
        "package": "pro",
        "additional_requests_per_day": "200",
        "additional_tokens_per_day": "20000000",
        "expires_in_days": "30"
    }
    ```

    **Credit Packages:**
    - **basic**: $10, 50 req/day, 5M tok/day, 30 days
    - **pro**: $30, 200 req/day, 20M tok/day, 30 days
    - **enterprise**: $100, 1000 req/day, 100M tok/day, 30 days

    **Stripe Integration Example:**
    ```javascript
    // Frontend: Create payment intent
    const paymentIntent = await stripe.paymentIntents.create({
        amount: 3000, // $30 in cents
        currency: 'usd',
        metadata: {
            user_id: 'user123',
            package: 'pro'
        }
    });

    // Stripe sends webhook → This endpoint grants credits
    ```

    **Security:** Verifies Stripe signature using HMAC SHA256.

    **Public endpoint** - No authentication required (Stripe signature verification instead).
    """
    try:
        # Get webhook secret from environment
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            logger.warning("[stripe_webhook] STRIPE_WEBHOOK_SECRET not set, skipping verification")

        # Read raw body
        body = await request.body()

        # Verify signature if secret is set
        if webhook_secret and stripe_signature:
            try:
                # Simple HMAC verification (Stripe format: t=timestamp,v1=signature)
                expected_sig = hmac.new(
                    webhook_secret.encode(),
                    body,
                    hashlib.sha256
                ).hexdigest()

                # Parse Stripe signature
                sig_parts = dict(part.split("=") for part in stripe_signature.split(","))
                provided_sig = sig_parts.get("v1", "")

                if not hmac.compare_digest(expected_sig, provided_sig):
                    logger.error("[stripe_webhook] Signature verification failed")
                    raise HTTPException(status_code=401, detail="Invalid signature")
            except Exception as e:
                logger.error(f"[stripe_webhook] Signature verification error: {e}")
                raise HTTPException(status_code=401, detail="Invalid signature")

        # Parse event
        event = json.loads(body.decode())

        # Handle payment success
        if event["type"] == "payment_intent.succeeded":
            payment = event["data"]["object"]
            metadata = payment.get("metadata", {})

            # Extract metadata
            user_id = metadata.get("user_id")
            tenant = metadata.get("tenant")
            project = metadata.get("project")
            package = metadata.get("package")

            if not user_id:
                logger.warning("[stripe_webhook] Missing user_id in payment metadata")
                return {"status": "ok", "message": "Missing user_id"}

            # Define credit packages
            packages = {
                "basic": {
                    "additional_requests_per_day": 50,
                    "additional_tokens_per_day": 5_000_000,
                    "expires_in_days": 30,
                },
                "pro": {
                    "additional_requests_per_day": 200,
                    "additional_tokens_per_day": 20_000_000,
                    "expires_in_days": 30,
                },
                "enterprise": {
                    "additional_requests_per_day": 1000,
                    "additional_tokens_per_day": 100_000_000,
                    "expires_in_days": 30,
                },
            }

            # Get package or custom amounts
            if package and package in packages:
                pkg = packages[package]
                additional_requests_per_day = pkg["additional_requests_per_day"]
                additional_tokens_per_day = pkg["additional_tokens_per_day"]
                expires_in_days = pkg["expires_in_days"]
            else:
                # Custom amounts from metadata
                additional_requests_per_day = int(metadata.get("additional_requests_per_day", 50))
                additional_tokens_per_day = int(metadata.get("additional_tokens_per_day", 5_000_000))
                expires_in_days = int(metadata.get("expires_in_days", 30))

            # Get control plane manager
            mgr = _get_control_plane_manager(router)
            settings = get_settings()

            # Use metadata or fallback to settings
            tenant = tenant or settings.TENANT
            project = project or settings.PROJECT

            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

            # Create replenishment
            await mgr.create_replenishment(
                tenant=tenant,
                project=project,
                user_id=user_id,
                bundle_id="*",  # Apply to all bundles
                additional_requests_per_day=additional_requests_per_day,
                additional_tokens_per_day=additional_tokens_per_day,
                expires_at=expires_at,
                purchase_id=payment["id"],
                purchase_amount_usd=payment["amount"] / 100,  # Cents to dollars
                purchase_notes=f"Stripe payment - {package or 'custom'} package",
            )

            logger.info(
                f"[stripe_webhook] Credits granted to {user_id}: "
                f"{additional_requests_per_day} req/day, {additional_tokens_per_day} tok/day, "
                f"payment_id={payment['id']}"
            )

            return {
                "status": "ok",
                "message": f"Credits granted to user {user_id}",
            }

        # Other event types
        return {
            "status": "ok",
            "message": f"Event {event['type']} received but not processed",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[stripe_webhook] Failed to process webhook")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

# ============================================================================
# Policy Management Endpoints - Admin Only
# ============================================================================

@router.get("/policies/quota")
async def list_quota_policies(
        request: Request,
        tenant: Optional[str] = Query(None),
        project: Optional[str] = Query(None),
        limit: int = Query(100),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    List user quota policies (base policies by user type).

    Shows configured policies for different user tiers (free, paid, premium, etc.).

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        policies = await mgr.list_quota_policies(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            limit=limit,
        )

        result = []
        for p in policies:
            result.append({
                "tenant": p.tenant,
                "project": p.project,
                "user_type": p.user_type,
                "bundle_id": p.bundle_id,
                "max_concurrent": p.max_concurrent,
                "requests_per_day": p.requests_per_day,
                "requests_per_month": p.requests_per_month,
                "tokens_per_day": p.tokens_per_day,
                "tokens_per_month": p.tokens_per_month,
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
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Set quota policy for a user type.

    Defines base rate limits for different user tiers.

    **Use Cases:**
    1. **Free Tier**: Set restrictive limits for free users
    2. **Paid Tier**: Configure reasonable limits for paying users
    3. **Premium Tier**: Set high limits for premium subscribers
    4. **Bundle-Specific**: Different limits per application bundle

    **Example: Free Tier Policy**
    ```bash
    POST /api/admin/control-plane/policies/quota
    {
        "user_type": "free",
        "bundle_id": "*",
        "max_concurrent": 1,
        "requests_per_day": 10,
        "tokens_per_day": 100000,
        "notes": "Free tier limits"
    }
    ```

    **Example: Premium Tier Policy**
    ```bash
    POST /api/admin/control-plane/policies/quota
    {
        "user_type": "premium",
        "bundle_id": "*",
        "max_concurrent": 10,
        "requests_per_day": 1000,
        "tokens_per_day": 50000000,
        "notes": "Premium tier limits"
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        policy = await mgr.set_user_quota_policy(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            user_type=payload.user_type,
            bundle_id=payload.bundle_id,
            max_concurrent=payload.max_concurrent,
            requests_per_day=payload.requests_per_day,
            requests_per_month=payload.requests_per_month,
            total_requests=payload.total_requests,
            tokens_per_hour=payload.tokens_per_hour,
            tokens_per_day=payload.tokens_per_day,
            tokens_per_month=payload.tokens_per_month,
            created_by=session.username or session.user_id,
            notes=payload.notes,
        )

        logger.info(
            f"[set_quota_policy] {settings.TENANT}/{settings.PROJECT}/{payload.user_type}: "
            f"policy updated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Quota policy set for {payload.user_type}",
            "policy": {
                "user_type": policy.user_type,
                "bundle_id": policy.bundle_id,
                "requests_per_day": policy.requests_per_day,
                "tokens_per_day": policy.tokens_per_day,
            }
        }
    except Exception as e:
        logger.exception(f"[set_quota_policy] Failed for {payload.user_type}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/policies/budget")
async def list_budget_policies(
        request: Request,
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

        policies = await mgr.list_budget_policies(
            tenant=tenant or settings.TENANT,
            project=project or settings.PROJECT,
            limit=limit,
        )

        result = []
        for p in policies:
            result.append({
                "tenant": p.tenant,
                "project": p.project,
                "bundle_id": p.bundle_id,
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
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Set budget policy for a provider.

    Configures spending limits to prevent runaway costs.

    **Use Cases:**
    1. **Cost Control**: Set daily/monthly spending caps per provider
    2. **Development vs Production**: Different budgets per environment
    3. **Multi-Provider Strategy**: Allocate budget across providers
    4. **Emergency Brake**: Hard limits to prevent bill shock

    **Example: Anthropic Daily Budget**
    ```bash
    POST /api/admin/control-plane/policies/budget
    {
        "bundle_id": "kdcube.codegen.orchestrator",
        "provider": "anthropic",
        "usd_per_day": 200.00,
        "notes": "Anthropic daily spending limit"
    }
    ```

    **Example: OpenAI Monthly Budget**
    ```bash
    POST /api/admin/control-plane/policies/budget
    {
        "bundle_id": "kdcube.codegen.orchestrator",
        "provider": "openai",
        "usd_per_month": 5000.00,
        "notes": "OpenAI monthly budget"
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        # Infer bundle_id using helper
        bundle_id = _infer_bundle_id(request, payload.bundle_id)

        policy = await mgr.set_budget_policy(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            bundle_id=bundle_id,
            provider=payload.provider,
            usd_per_hour=payload.usd_per_hour,
            usd_per_day=payload.usd_per_day,
            usd_per_month=payload.usd_per_month,
            created_by=session.username or session.user_id,
            notes=payload.notes,
        )

        logger.info(
            f"[set_budget_policy] {settings.TENANT}/{settings.PROJECT}/{bundle_id}/{payload.provider}: "
            f"policy updated by {session.username or session.user_id}"
        )

        return {
            "status": "ok",
            "message": f"Budget policy set for {payload.provider}",
            "policy": {
                "bundle_id": policy.bundle_id,
                "provider": policy.provider,
                "usd_per_day": float(policy.usd_per_day) if policy.usd_per_day else None,
            }
        }
    except Exception as e:
        logger.exception(f"[set_budget_policy] Failed for {payload.provider}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Health & Admin Utilities
# ============================================================================

@router.get("/users/{user_id}/quota-breakdown")
async def get_user_quota_breakdown(
        user_id: str,
        request: Request,
        user_type: str = Query(..., description="User type (free, paid, premium, etc.)"),
        bundle_id: str = Query("*", description="Bundle ID"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get detailed quota breakdown for a user showing:
    - Base policy (from user type/tier)
    - Replenishment (purchased/granted credits)
    - Effective policy (base + replenishment)
    - Current usage
    - Remaining quota

    **Use Cases:**
    1. **User Support**: Show customer exactly what they have
    2. **Admin Dashboard**: Monitor user quota status
    3. **Transparency**: Clear breakdown of tier vs. purchased credits

    **Example Response:**
    ```json
    {
        "user_id": "user123",
        "user_type": "free",
        "bundle_id": "*",
        "base_policy": {
            "max_concurrent": 1,
            "requests_per_day": 10,
            "tokens_per_day": 1000000
        },
        "replenishment": {
            "additional_requests_per_day": 50,
            "additional_tokens_per_day": 5000000,
            "expires_at": "2025-01-24T00:00:00Z"
        },
        "effective_policy": {
            "max_concurrent": 1,
            "requests_per_day": 60,
            "tokens_per_day": 6000000
        },
        "current_usage": {
            "requests_today": 5,
            "tokens_today": 300000,
            "concurrent": 0
        },
        "remaining": {
            "requests_today": 55,
            "tokens_today": 5700000,
            "percentage_used": 8.3
        }
    }
    ```

    **Admin only.** Requires authentication.
    """
    try:
        mgr = _get_control_plane_manager(router)
        settings = get_settings()

        tenant = settings.TENANT
        project = settings.PROJECT

        # 1. Get base policy
        base_policy = await mgr.get_user_quota_policy(
            tenant=tenant,
            project=project,
            user_type=user_type,
            bundle_id=bundle_id,
        )

        if not base_policy:
            raise HTTPException(
                status_code=404,
                detail=f"No policy found for user_type={user_type}, bundle_id={bundle_id}"
            )

        # 2. Get replenishment
        replenishment = await mgr.get_replenishment(
            tenant=tenant,
            project=project,
            user_id=user_id,
            bundle_id=bundle_id,
        )

        # 3. Compute effective policy
        from kdcube_ai_app.apps.chat.sdk.infra.rate_limit.limiter import subject_id_of
        subject_id = subject_id_of(tenant, project, user_id)

        # Get current usage from Redis
        from kdcube_ai_app.apps.chat.sdk.infra.rate_limit.limiter import RateLimiter
        from datetime import datetime, timezone

        # Build rate limiter to read counters
        redis = getattr(request.app.state.middleware, "redis", None)
        if not redis:
            raise HTTPException(status_code=503, detail="Redis not available")

        rl = RateLimiter(redis)

        # Read current counters
        now = datetime.now(timezone.utc)
        ymd = now.strftime("%Y%m%d")
        ym = now.strftime("%Y%m")

        k_req_d = f"kdcube:rl:{bundle_id}:{subject_id}:reqs:day:{ymd}"
        k_req_m = f"kdcube:rl:{bundle_id}:{subject_id}:reqs:month:{ym}"
        k_req_t = f"kdcube:rl:{bundle_id}:{subject_id}:reqs:total"
        k_tok_d = f"kdcube:rl:{bundle_id}:{subject_id}:toks:day:{ymd}"
        k_tok_m = f"kdcube:rl:{bundle_id}:{subject_id}:toks:month:{ym}"
        k_locks = f"kdcube:rl:{bundle_id}:{subject_id}:locks"

        vals = await redis.mget(k_req_d, k_req_m, k_req_t, k_tok_d, k_tok_m)
        req_day = int(vals[0] or 0)
        req_month = int(vals[1] or 0)
        req_total = int(vals[2] or 0)
        tok_day = int(vals[3] or 0)
        tok_month = int(vals[4] or 0)

        # Get concurrent count
        concurrent = await redis.zcard(k_locks)

        # Helper to add limits
        def add_limit(base, additional):
            if base is None:
                return None
            if additional is None:
                return base
            return base + additional

        # Build effective policy
        effective_requests_per_day = add_limit(
            base_policy.requests_per_day,
            replenishment.additional_requests_per_day if replenishment else None
        )
        effective_requests_per_month = add_limit(
            base_policy.requests_per_month,
            replenishment.additional_requests_per_month if replenishment else None
        )
        effective_tokens_per_day = add_limit(
            base_policy.tokens_per_day,
            replenishment.additional_tokens_per_day if replenishment else None
        )
        effective_tokens_per_month = add_limit(
            base_policy.tokens_per_month,
            replenishment.additional_tokens_per_month if replenishment else None
        )
        effective_max_concurrent = add_limit(
            base_policy.max_concurrent,
            replenishment.additional_max_concurrent if replenishment else None
        )

        # Calculate remaining
        def calc_remaining(limit, used):
            if limit is None:
                return None
            return max(limit - used, 0)

        remaining_req_day = calc_remaining(effective_requests_per_day, req_day)
        remaining_req_month = calc_remaining(effective_requests_per_month, req_month)
        remaining_tok_day = calc_remaining(effective_tokens_per_day, tok_day)
        remaining_tok_month = calc_remaining(effective_tokens_per_month, tok_month)

        # Calculate percentage used (for primary quotas)
        percentage_used = None
        if effective_requests_per_day and effective_requests_per_day > 0:
            percentage_used = round((req_day / effective_requests_per_day) * 100, 1)

        return {
            "status": "ok",
            "user_id": user_id,
            "user_type": user_type,
            "bundle_id": bundle_id,
            "base_policy": {
                "max_concurrent": base_policy.max_concurrent,
                "requests_per_day": base_policy.requests_per_day,
                "requests_per_month": base_policy.requests_per_month,
                "tokens_per_day": base_policy.tokens_per_day,
                "tokens_per_month": base_policy.tokens_per_month,
            },
            "replenishment": {
                "has_credits": replenishment is not None and replenishment.is_valid(),
                "additional_max_concurrent": replenishment.additional_max_concurrent if replenishment else None,
                "additional_requests_per_day": replenishment.additional_requests_per_day if replenishment else None,
                "additional_requests_per_month": replenishment.additional_requests_per_month if replenishment else None,
                "additional_tokens_per_day": replenishment.additional_tokens_per_day if replenishment else None,
                "additional_tokens_per_month": replenishment.additional_tokens_per_month if replenishment else None,
                "expires_at": replenishment.expires_at.isoformat() if replenishment and replenishment.expires_at else None,
                "purchase_notes": replenishment.purchase_notes if replenishment else None,
            } if replenishment else None,
            "effective_policy": {
                "max_concurrent": effective_max_concurrent,
                "requests_per_day": effective_requests_per_day,
                "requests_per_month": effective_requests_per_month,
                "tokens_per_day": effective_tokens_per_day,
                "tokens_per_month": effective_tokens_per_month,
            },
            "current_usage": {
                "requests_today": req_day,
                "requests_this_month": req_month,
                "requests_total": req_total,
                "tokens_today": tok_day,
                "tokens_this_month": tok_month,
                "concurrent": concurrent,
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

@router.get("/health")
async def health_check(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Health check for control plane.

    **Admin only.** Requires authentication.
    """
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

@router.post("/clear-cache")
async def admin_clear_cache(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Clear cached control plane manager (forces recreation with fresh connections).

    Useful after configuration changes or to troubleshoot caching issues.

    **Admin only.** Requires authentication.
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
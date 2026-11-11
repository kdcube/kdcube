# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/stripe.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Callable

import asyncpg

from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager, build_subscription_period_descriptor
from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import UserCreditsManager
from kdcube_ai_app.apps.chat.sdk.infra.economics.external_events import (
    ExternalEconomicsLedger, LedgerKey,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.plan_resolution import plan_is_entitlement_only
from kdcube_ai_app.infra.accounting.usage import quote_tokens_for_usd
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.infra.channel.email import send_admin_email
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema

logger = logging.getLogger(__name__)

SubscriptionBudgetFactory = Callable[[str, str, str, str, datetime, datetime], SubscriptionBudgetLimiter]
ProjectBudgetFactory = Callable[[str, str], ProjectBudgetLimiter]


# ---------------- Stripe Customer identity ----------------
#
# A Stripe Customer the platform creates carries an opaque reference, never the
# platform user id: Stripe needs to know which tenant/project a customer belongs
# to, not who the user is. The mapping of record is ours -- the
# `user_plans.stripe_customer_id` row written when the customer is created.
# Customers created before this change keep `user_id` in their metadata and
# keep resolving through it.

#: Metadata key carrying the opaque customer reference.
CUSTOMER_REF_METADATA_KEY = "kdcube_customer_ref"
#: The reference is keyed by a sub-key derived from this platform secret, so an
#: operator holding the secret can compute a user's reference, and the reference
#: alone reveals nothing.
_CUSTOMER_REF_SECRET = "platform.services.session_token.secret"
_CUSTOMER_REF_LABEL = b"kdcube.stripe.customer_ref.v1"
_CUSTOMER_REF_HEX_DIGITS = 16


def stripe_customer_ref(secret: str, user_id: str) -> str:
    """Deterministic opaque reference for `user_id` under `secret`."""
    import hashlib
    import hmac

    sub_key = hmac.new(str(secret).encode("utf-8"), _CUSTOMER_REF_LABEL, hashlib.sha256).digest()
    digest = hmac.new(sub_key, str(user_id).strip().encode("utf-8"), hashlib.sha256).hexdigest()
    return "cu_" + digest[:_CUSTOMER_REF_HEX_DIGITS]


async def _customer_ref_for(user_id: str) -> str:
    try:
        secret = await get_secret(_CUSTOMER_REF_SECRET, default=None)
    except Exception:
        secret = None
    if str(secret or "").strip():
        return stripe_customer_ref(str(secret).strip(), user_id)
    # Without the secret the reference is random: still opaque, and the
    # user_plans row still maps the customer to the user.
    import secrets as _secrets

    return "cr_" + _secrets.token_hex(_CUSTOMER_REF_HEX_DIGITS // 2)


async def stripe_customer_fields(tenant: str, project: str, user_id: str) -> Dict[str, Any]:
    """Keyword arguments for `stripe.Customer.create`: routing metadata and a
    description, with no platform user id in either."""
    ref = await _customer_ref_for(user_id)
    return {
        "metadata": {"tenant": tenant, "project": project, CUSTOMER_REF_METADATA_KEY: ref},
        "description": f"Customer {ref} from {tenant}/{project}",
    }


def _configured_stripe_secret(key: str) -> str:
    settings = get_settings()
    if key == "stripe.secret_key":
        return str(
            os.getenv("STRIPE_SECRET_KEY")
            or os.getenv("STRIPE_API_KEY")
            or getattr(settings, "STRIPE_SECRET_KEY", "")
            or ""
        ).strip()
    if key == "stripe.webhook_secret":
        return str(
            os.getenv("STRIPE_WEBHOOK_SECRET")
            or getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
            or ""
        ).strip()
    return ""


async def _resolve_stripe_secret_async(key: str) -> str:
    """Resolve a Stripe secret secrets-manager-first, mirroring the ingress
    _get_stripe() path (bundle scope `b:` then global), and falling back to the
    env/settings reader. Async because the secrets-manager lookup is async; the
    SDK economics services are constructed synchronously, so callers resolve the
    key lazily on first async use rather than in __init__."""
    name = {
        "stripe.secret_key": "platform.services.stripe.secret_key",
        "stripe.webhook_secret": "platform.services.stripe.webhook_secret",
    }.get(key, key)
    try:
        bundle_name = name.removeprefix("platform.")
        val = await get_secret(f"b:{bundle_name}") or await get_secret(name)
    except Exception:
        val = None
    val = str(val).strip() if val else ""
    return val or _configured_stripe_secret(key)


def _stripe_to_plain(obj: Any) -> Any:
    # Recursively convert a stripe StripeObject (any version) to plain
    # dict/list. Newer stripe-python makes StripeObject a dict subclass; older
    # versions expose neither .get nor to_dict_recursive and only carry an
    # internal _data mapping. Try, in order: dict / list / to_dict_recursive /
    # _data, then pass through scalars.
    if isinstance(obj, dict):
        return {k: _stripe_to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_stripe_to_plain(v) for v in obj]
    conv = getattr(obj, "to_dict_recursive", None)
    if callable(conv):
        try:
            return _stripe_to_plain(conv())
        except Exception:
            pass
    data = getattr(obj, "_data", None)
    if isinstance(data, dict):
        return {k: _stripe_to_plain(v) for k, v in data.items()}
    return obj



def _extract_stripe_data_list(obj: Dict[str, Any], key: str) -> list[Dict[str, Any]]:
    """Safely extracts a list of data items from a Stripe list object (like lines, items, charges)."""
    if not isinstance(obj, dict):
        return []
    val = obj.get(key)
    if isinstance(val, dict):
        return val.get("data") or []
    # Sometimes the top-level object IS the list object, so we fall back to obj.get("data")
    return obj.get("data") or []

def _extract_stripe_timestamp(obj: Dict[str, Any], key: str) -> Optional[datetime]:
    """Safely extracts a timestamp field and converts it to a UTC datetime."""
    if not isinstance(obj, dict):
        return None
    ts = obj.get(key)
    if ts is not None:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None

def map_stripe_subscription_status_to_cp(status: str) -> str:
    # CP constraint: active|canceled|suspended
    s = (status or "").lower()
    if s in ("active", "trialing"):
        return "active"
    if s in ("canceled",):
        return "canceled"
    return "suspended"


#: Metadata key carrying a wallet top-up's pre-tax price, in cents. Stamped onto
#: the Checkout Session and its PaymentIntent by `create_checkout_topup`
#: (apps/chat/ingress/economics/checkout.py) and read back by
#: `net_topup_cents` when the payment succeeds.
TOPUP_NET_AMOUNT_METADATA_KEY = "kdcube_net_amount_cents"


def net_topup_cents(meta: Dict[str, str], gross_cents: int, *, reference: str = "") -> int:
    """The amount to credit for a wallet top-up whose PaymentIntent collected
    `gross_cents`.

    With Stripe Tax on and tax-exclusive prices, a PaymentIntent's
    `amount_received` is subtotal + tax: a customer buying a 500-cent pack in a
    19% VAT jurisdiction pays 595 cents. The 95 cents are tax owed to a revenue
    authority and are not model budget. The credit therefore comes from our
    record of what was sold -- the pre-tax price stamped into the metadata at
    checkout -- not from what Stripe collected.

    Never returns more than `gross_cents`. Falls back to `gross_cents` (and
    logs) when the metadata carries no usable figure, which is the case for a
    PaymentIntent created before the figure was stamped; for an untaxed payment
    the two are equal.
    """
    raw = (meta or {}).get(TOPUP_NET_AMOUNT_METADATA_KEY)
    ref = reference or "payment_intent"
    if raw is None or str(raw).strip() == "":
        logger.info(
            "[topup-net] %s carries no %s; crediting the amount received (%d cents)",
            ref, TOPUP_NET_AMOUNT_METADATA_KEY, gross_cents,
        )
        return gross_cents
    try:
        net = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "[topup-net] %s has a non-numeric %s=%r; crediting the amount received (%d cents)",
            ref, TOPUP_NET_AMOUNT_METADATA_KEY, raw, gross_cents,
        )
        return gross_cents
    if net <= 0:
        logger.warning(
            "[topup-net] %s declares a net of %d cents; crediting the amount received (%d cents)",
            ref, net, gross_cents,
        )
        return gross_cents
    if net > gross_cents:
        logger.warning(
            "[topup-net] %s declares a net of %d cents but only %d cents were received; "
            "crediting the amount received",
            ref, net, gross_cents,
        )
        return gross_cents
    return net


@dataclass(frozen=True)
class StripeHandleResult:
    status: str
    action: str
    message: str
    kind: Optional[str] = None
    external_id: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None
    user_id: Optional[str] = None

@dataclass(frozen=True)
class StripeCreateSubscriptionResult:
    status: str
    action: str
    message: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_latest_invoice_id: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None
    user_id: Optional[str] = None

class StripeSubscriptionService:
    """
    Stripe subscription creation (admin-driven for now).
    Keeps Stripe-related logic out of routes.
    """

    def __init__(
            self,
            *,
            pg_pool: asyncpg.Pool,
            subscription_mgr: SubscriptionManager,
            default_tenant: str,
            default_project: str,
            stripe_api_key: Optional[str] = None,
    ):
        self.pg_pool = pg_pool
        self.subscription_mgr = subscription_mgr
        self.default_tenant = default_tenant
        self.default_project = default_project
        self.stripe_api_key = stripe_api_key or _configured_stripe_secret("stripe.secret_key")

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            raise RuntimeError("Stripe API key not configured (platform.services.stripe.secret_key)")
        stripe.api_key = self.stripe_api_key
        return stripe

    async def _ensure_stripe_api_key(self) -> None:
        if not self.stripe_api_key:
            self.stripe_api_key = await _resolve_stripe_secret_async("stripe.secret_key")

    @staticmethod
    def _map_stripe_subscription_status_to_cp(status: str) -> str:
        return map_stripe_subscription_status_to_cp(status)

    async def create_subscription(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            plan_id: str,
            stripe_price_id: Optional[str] = None,
            stripe_customer_id: Optional[str] = None,
            monthly_price_cents_hint: Optional[int] = None,
            metadata: Optional[Dict[str, str]] = None,
    ) -> StripeCreateSubscriptionResult:
        """
        Creates a Stripe subscription and stores a best-effort snapshot in user_plans.
        NOTE: If the customer has no default payment method, the subscription may start as 'incomplete'
        until payment is completed.
        """
        plan = await self.subscription_mgr.get_plan(
            tenant=tenant, project=project, plan_id=plan_id, include_inactive=True
        )
        if not plan:
            raise ValueError(f"subscription plan not found: {plan_id}")
        if plan.provider != "stripe":
            raise ValueError(f"plan provider must be stripe: {plan_id}")

        await self._ensure_stripe_api_key()
        stripe = self._stripe()

        md = dict(metadata or {})
        md.setdefault("tenant", tenant or self.default_tenant)
        md.setdefault("project", project or self.default_project)
        md.setdefault("user_id", user_id)
        md.setdefault("plan_id", plan.plan_id)
        md.setdefault("kdcube_invoice_kind", "subscription")

        stripe_price_id = stripe_price_id or plan.stripe_price_id
        if not stripe_price_id:
            raise ValueError("stripe_price_id is required (plan missing stripe_price_id)")

        # Cancel existing Stripe subscription if user is switching plans.
        # Without this, both subscriptions remain active in Stripe and the user
        # gets double-charged at the next billing cycle.
        # We also rollover the unused balance synchronously here, before cancelling in Stripe,
        # to avoid the race condition where customer.subscription.deleted webhook arrives
        # after the new subscription has already overwritten the DB record.
        existing_sub = await self.subscription_mgr.get_subscription(
            tenant=tenant, project=project, user_id=user_id
        )
        if (
            existing_sub
            and existing_sub.provider == "stripe"
            and existing_sub.stripe_subscription_id
            and existing_sub.status == "active"
        ):
            old_stripe_sub_id = existing_sub.stripe_subscription_id

            # Rollover unused balance to project budget before cancelling.
            if existing_sub.next_charge_at:
                try:
                    from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
                    period_desc = build_subscription_period_descriptor(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        provider=existing_sub.provider,
                        stripe_subscription_id=old_stripe_sub_id,
                        period_end=existing_sub.next_charge_at,
                        period_start=existing_sub.last_charged_at,
                    )
                    sub_budget = SubscriptionBudgetLimiter(
                        pg_pool=self.pg_pool,
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        period_key=period_desc["period_key"],
                        period_start=period_desc["period_start"],
                        period_end=period_desc["period_end"],
                    )
                    proj_budget = ProjectBudgetLimiter(
                        redis=None, pg_pool=self.pg_pool, tenant=tenant, project=project
                    )
                    await self.subscription_mgr.rollover_unused_balance_once(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        subscription_budget=sub_budget,
                        project_budget=proj_budget,
                        period_key=period_desc["period_key"],
                        period_end=period_desc["period_end"],
                        actor="plan_change",
                    )
                    logger.info(
                        "Rolled over unused balance for subscription %s (user %s, plan change to %s)",
                        old_stripe_sub_id, user_id, plan_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to rollover unused balance for subscription %s (user %s) before plan change",
                        old_stripe_sub_id, user_id,
                    )

            try:
                stripe.Subscription.cancel(old_stripe_sub_id)
                logger.info(
                    "Cancelled old Stripe subscription %s for user %s (plan change to %s)",
                    old_stripe_sub_id, user_id, plan_id,
                )
            except Exception:
                logger.exception(
                    "Failed to cancel old Stripe subscription %s before creating new one for user %s",
                    old_stripe_sub_id, user_id,
                )

        # Ensure customer
        customer_id = stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(**await stripe_customer_fields(tenant, project, user_id))
            customer_id = str(customer["id"])

        # If you want invoice.paid to be resolvable without relying on invoice.metadata,
        # it’s enough that subscription/customer have metadata; webhook will fetch them.
        sub = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": stripe_price_id}],
            metadata=md,
            payment_behavior="default_incomplete",
            expand=["latest_invoice.payment_intent"],
        )

        sub_id = str(sub.get("id"))
        sub_status = self._map_stripe_subscription_status_to_cp(str(sub.get("status") or ""))

        # next charge info (Stripe gives current_period_end)
        next_charge_at = None
        cpe = sub.get("current_period_end")
        if cpe:
            next_charge_at = datetime.fromtimestamp(int(cpe), tz=timezone.utc)

        # price cents
        price_cents = int(monthly_price_cents_hint or 0)
        if price_cents <= 0 and plan.monthly_price_cents:
            price_cents = int(plan.monthly_price_cents)
        if price_cents <= 0:
            try:
                price_obj = stripe.Price.retrieve(stripe_price_id)
                unit_amount = price_obj.get("unit_amount")
                if unit_amount:
                    price_cents = int(unit_amount)
            except Exception:
                price_cents = 0

        latest_invoice = sub.get("latest_invoice") or {}
        inv_id = str(latest_invoice.get("id") or "") or None
        pi = (latest_invoice.get("payment_intent") or {})
        pi_id = str(pi.get("id") or "") or None

        # Store snapshot (best effort). Webhook invoice.paid will do the authoritative “charged” update.
        _schema = _project_schema(tenant, project)
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"""
                    INSERT INTO {_schema}.{SubscriptionManager.TABLE} (
                      tenant, project, user_id,
                      plan_id, status, monthly_price_cents,
                      started_at, next_charge_at, last_charged_at,
                      provider, stripe_customer_id, stripe_subscription_id
                    ) VALUES (
                      $1,$2,$3,
                      $4,$5,$6,
                      NOW(), $7, NULL,
                      'stripe', $8, $9
                    )
                    ON CONFLICT (tenant, project, user_id)
                    DO UPDATE SET
                      provider='stripe',
                      plan_id=COALESCE(EXCLUDED.plan_id, {_schema}.{SubscriptionManager.TABLE}.plan_id),
                      status=EXCLUDED.status,
                      monthly_price_cents=CASE
                        WHEN EXCLUDED.monthly_price_cents > 0 THEN EXCLUDED.monthly_price_cents
                        ELSE {_schema}.{SubscriptionManager.TABLE}.monthly_price_cents
                      END,
                      next_charge_at=COALESCE(EXCLUDED.next_charge_at, {_schema}.{SubscriptionManager.TABLE}.next_charge_at),
                      stripe_customer_id=COALESCE(EXCLUDED.stripe_customer_id, {_schema}.{SubscriptionManager.TABLE}.stripe_customer_id),
                      stripe_subscription_id=COALESCE(EXCLUDED.stripe_subscription_id, {_schema}.{SubscriptionManager.TABLE}.stripe_subscription_id),
                      updated_at=NOW()
                """, tenant, project, user_id, plan.plan_id, sub_status, price_cents, next_charge_at, customer_id, sub_id)

        return StripeCreateSubscriptionResult(
            status="ok",
            action="created",
            message=f"Stripe subscription created: {sub_id} (status={sub_status})",
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
            stripe_latest_invoice_id=inv_id,
            stripe_payment_intent_id=pi_id,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )

class StripeEconomicsWebhookHandler:
    """
    Stripe webhook handler that:
      - verifies Stripe signatures
      - provides idempotency via ExternalEconomicsLedger (sdk/infra/economics/external_events.py)
      - applies effects via managers:
          * UserCreditsManager (lifetime credits)
          * SubscriptionBudgetLimiter (per-user subscription balance)
      - maintains subscription state in user_plans (DB upsert)

    Supported:
      - payment_intent.succeeded -> add lifetime user tokens
      - invoice.paid -> if subscription invoice -> top up subscription balance + update user_plans
    """

    def __init__(
        self,
        *,
        pg_pool: asyncpg.Pool,
        user_credits_mgr: UserCreditsManager,
        subscription_budget_factory: SubscriptionBudgetFactory,
        project_budget_factory: Optional[ProjectBudgetFactory] = None,
        subscription_mgr: SubscriptionManager,
        default_tenant: str,
        default_project: str,
        stripe_webhook_secret: Optional[str] = None,
        ref_provider: str = "anthropic",
        ref_model: str = "claude-sonnet-4-5-20250929",
    ):
        self.pg_pool = pg_pool
        self.ledger = ExternalEconomicsLedger(pg_pool=pg_pool)
        self.user_credits_mgr = user_credits_mgr
        self.subscription_budget_factory = subscription_budget_factory
        self.project_budget_factory = project_budget_factory
        self.subscription_mgr = subscription_mgr

        self.default_tenant = default_tenant
        self.default_project = default_project

        self.webhook_secret = stripe_webhook_secret or _configured_stripe_secret("stripe.webhook_secret")
        self.stripe_api_key = _configured_stripe_secret("stripe.secret_key")
        self.ref_provider = ref_provider
        self.ref_model = ref_model

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            return None
        stripe.api_key = self.stripe_api_key
        return stripe

    async def _ensure_stripe_api_key(self) -> None:
        if not self.stripe_api_key:
            self.stripe_api_key = await _resolve_stripe_secret_async("stripe.secret_key")

    def _merge_meta(self, *metas: Dict[str, str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for m in metas:
            if not m:
                continue
            for k, v in m.items():
                if v is None:
                    continue
                # keep first non-empty (invoice metadata wins, then lines, then subscription/customer)
                if k not in out or out[k] == "":
                    out[k] = str(v)
        return out

    def _meta_from_invoice_lines(self, invoice: Dict[str, Any]) -> Dict[str, str]:
        try:
            lines = _extract_stripe_data_list(invoice, "lines")
            if not lines:
                return {}
            m = lines[0].get("metadata") or {}
            if not isinstance(m, dict):
                return {}
            return {str(k): str(v) for k, v in m.items() if v is not None}
        except Exception:
            return {}

    def _price_id_from_invoice_lines(self, invoice: Dict[str, Any]) -> Optional[str]:
        try:
            lines = _extract_stripe_data_list(invoice, "lines")
            for line in lines:
                pricing = line.get("pricing") or {}
                price_details = pricing.get("price_details") or {}
                if price_details.get("price"):
                    return str(price_details.get("price"))

                price = line.get("price") or {}
                if isinstance(price, dict) and price.get("id"):
                    return str(price.get("id"))

                plan = line.get("plan") or {}
                if isinstance(plan, dict) and plan.get("id"):
                    return str(plan.get("id"))
        except Exception:
            return None
        return None

    def _fetch_subscription_meta(self, subscription_id: Optional[str]) -> Dict[str, str]:
        if not subscription_id:
            return {}
        stripe = self._stripe()
        if not stripe:
            return {}
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            return self._meta(sub)
        except Exception:
            logger.exception("stripe fetch subscription metadata failed: %s", subscription_id)
            return {}

    def _fetch_customer_meta(self, customer_id: Optional[str]) -> Dict[str, str]:
        if not customer_id:
            return {}
        stripe = self._stripe()
        if not stripe:
            return {}
        try:
            cust = stripe.Customer.retrieve(customer_id)
            return self._meta(cust)
        except Exception:
            logger.exception("stripe fetch customer metadata failed: %s", customer_id)
            return {}

    def _resolve_invoice_context(self, invoice: Dict[str, Any]) -> Tuple[Dict[str, str], str, str, Optional[str]]:
        inv_meta = self._meta(invoice)
        line_meta = self._meta_from_invoice_lines(invoice)

        # Fetch from API if needed (only if API key exists)
        sub_meta = {}
        cust_meta = {}

        # only fetch if user_id missing after invoice+lines
        tmp = self._merge_meta(inv_meta, line_meta)
        if (not tmp.get("user_id")) or (not tmp.get("kdcube_invoice_kind")):
            sub_meta = self._fetch_subscription_meta(invoice.get("subscription"))
            tmp = self._merge_meta(inv_meta, line_meta, sub_meta)

        if (not tmp.get("user_id")) or (not tmp.get("kdcube_invoice_kind")):
            cust_meta = self._fetch_customer_meta(invoice.get("customer"))
            tmp = self._merge_meta(inv_meta, line_meta, sub_meta, cust_meta)

        tenant, project, user_id = self._resolve_tenant_project_user(tmp)
        return tmp, tenant, project, user_id

    async def handle_webhook(self, *, body: bytes, stripe_signature: Optional[str]) -> Dict[str, Any]:
        event = self._verify_and_parse(body=body, stripe_signature=stripe_signature)
        res = await self.process_event(event)
        return {
            "status": res.status,
            "action": res.action,
            "message": res.message,
            "kind": res.kind,
            "external_id": res.external_id,
            "tenant": res.tenant,
            "project": res.project,
            "user_id": res.user_id,
        }

    async def process_event(self, event: Dict[str, Any]) -> StripeHandleResult:
        """
        Process a Stripe event (either from webhook or from API).
        Idempotency is handled by ExternalEconomicsLedger.
        """
        await self._ensure_stripe_api_key()
        # reconcile passes a stripe.Event (StripeObject, no .get); webhook passes a dict
        if not isinstance(event, dict):
            event = _stripe_to_plain(event)
        etype = event.get("type")
        obj = (event.get("data") or {}).get("object") or {}

        try:
            if etype == "payment_intent.succeeded":
                return await self._handle_payment_intent_succeeded(event, obj)
            elif etype == "invoice.paid":
                return await self._handle_invoice_paid(event, obj)
            elif etype == "checkout.session.completed":
                return await self._handle_checkout_session_completed(event, obj)
            elif etype in ("refund.updated", "refund.created"):
                return await self._handle_refund_updated(event, obj)
            elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
                return await self._handle_subscription_event(event, obj)
            else:
                return StripeHandleResult(status="ok", action="unsupported", message=f"Event {etype} not processed")
        except Exception as e:
            logger.exception("Stripe event processing failed: type=%s", etype)
            return StripeHandleResult(status="error", action="failed", message=str(e))

    # ---------------- verification ----------------

    def _verify_and_parse(self, *, body: bytes, stripe_signature: Optional[str]) -> Dict[str, Any]:
        if not self.webhook_secret:
            raise RuntimeError("platform.services.stripe.webhook_secret not configured")

        if not stripe_signature:
            raise ValueError("Missing Stripe-Signature header")

        import stripe
        # Verify the signature, but hand the rest of the handler a plain dict.
        # construct_event() returns a StripeObject (stripe.Event), and in current
        # stripe-python StripeObject has no .get() — attribute access for 'get'
        # routes through __getattr__ and raises AttributeError. process_event and
        # every _handle_* walk the event via dict .get(), so parse the
        # already-verified raw body ourselves. construct_event still raises on a
        # bad signature; we just discard its object return.
        stripe.Webhook.construct_event(payload=body, sig_header=stripe_signature, secret=self.webhook_secret)
        return json.loads(body.decode("utf-8"))

    # ---------------- helpers ----------------

    def _meta(self, obj: Dict[str, Any]) -> Dict[str, str]:
        m = obj.get("metadata") or {}
        if not isinstance(m, dict):
            return {}
        return {str(k): str(v) for k, v in m.items() if v is not None}

    def _resolve_tenant_project_user(self, meta: Dict[str, str]) -> Tuple[str, str, Optional[str]]:
        tenant = meta.get("tenant") or self.default_tenant
        project = meta.get("project") or self.default_project
        user_id = meta.get("user_id")
        return tenant, project, user_id

    def _is_subscription_invoice(self, invoice: Dict[str, Any], meta: Dict[str, str]) -> bool:
        # Your "special invoice type"
        if (meta.get("kdcube_invoice_kind") or "").lower() == "subscription":
            return True

        # Optional fallback: Stripe billing reason
        if (invoice.get("billing_reason") or "") in ("subscription_cycle", "subscription_update"):
            return True

        return False

    # ---------------- event handlers ----------------

    async def _handle_checkout_session_completed(self, event: Dict[str, Any], session_obj: Dict[str, Any]) -> StripeHandleResult:
        meta = self._meta(session_obj)
        tenant, project, user_id = self._resolve_tenant_project_user(meta)
        if not user_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing user_id in session metadata")

        stripe_sub_id = session_obj.get("subscription")
        stripe_cust_id = session_obj.get("customer")
        session_id = str(session_obj.get("id") or "")
        stripe_event_id = str(event.get("id") or "")

        # If it's a subscription session, we want to capture the sub_id early
        if stripe_sub_id:
            plan_id = meta.get("plan_id")
            async with self.pg_pool.acquire() as conn:
                async with conn.transaction():
                    # Idempotency for the session itself
                    key = LedgerKey(source="stripe", kind="checkout_completed",
                                    external_id=session_id)
                    claim = await self.ledger.claim(
                        conn=conn, key=key, tenant=tenant, project=project,
                        user_id=user_id, amount_cents=None, tokens=None,
                        currency=None, provider_event_id=stripe_event_id,
                        metadata=meta,
                    )
                    if claim.already_applied:
                        return StripeHandleResult(status="ok", action="duplicate",
                                                  message="Already applied")

                    # Update subscription snapshot with the real ID and plan_id
                    await conn.execute(f"""
                        UPDATE {_project_schema(tenant, project)}.user_plans
                        SET stripe_subscription_id = COALESCE(stripe_subscription_id, $4),
                            stripe_customer_id = COALESCE(stripe_customer_id, $5),
                            plan_id = COALESCE(plan_id, $6),
                            provider = 'stripe',
                            status = 'active',
                            updated_at = NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                    """, tenant, project, user_id, stripe_sub_id, stripe_cust_id, plan_id)

                    await self.ledger.mark_applied(conn=conn, key=key, tenant=tenant,
                                                   project=project)

        return StripeHandleResult(
            status="ok", action="applied", message="Checkout session completed",
            tenant=tenant, project=project, user_id=user_id
        )

    async def _handle_payment_intent_succeeded(self, event: Dict[str, Any], pi: Dict[str, Any]) -> StripeHandleResult:
        meta = self._meta(pi)
        tenant, project, user_id = self._resolve_tenant_project_user(meta)
        if not user_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing user_id in payment_intent metadata")

        currency = (pi.get("currency") or "usd").lower()
        amount_cents = int(pi.get("amount_received") or pi.get("amount") or 0)
        if currency != "usd" or amount_cents <= 0:
            return StripeHandleResult(status="ok", action="ignored", message=f"Unsupported currency/amount ({currency},{amount_cents})")

        payment_intent_id = str(pi.get("id") or "")
        stripe_event_id = str(event.get("id") or "")
        if not payment_intent_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing payment_intent id")

        # Credit the pre-tax price, never the tax Stripe collected on top of it.
        # The credit, the token quote and the ledger row all read amount_cents.
        gross_cents = amount_cents
        amount_cents = net_topup_cents(meta, gross_cents, reference=payment_intent_id)
        if amount_cents != gross_cents:
            # The ledger row records the credited (net) amount; keep the gross
            # and the tax beside it so a Stripe payout still reconciles.
            meta = dict(meta)
            meta["kdcube_gross_amount_cents"] = str(gross_cents)
            meta["kdcube_tax_cents"] = str(gross_cents - amount_cents)

        usd_amount = amount_cents / 100.0
        tokens_added, _ = quote_tokens_for_usd(
            usd_amount=usd_amount,
            ref_provider=self.ref_provider,
            ref_model=self.ref_model,
        )
        notes = meta.get("notes") or meta.get("package") or "Stripe payment_intent.succeeded"

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                key = LedgerKey(source="stripe", kind="user_credits",
                                external_id=payment_intent_id)
                async with self.ledger.apply_once(
                    conn=conn, key=key, tenant=tenant, project=project,
                    user_id=user_id, amount_cents=amount_cents,
                    tokens=int(tokens_added), currency=currency,
                    provider_event_id=stripe_event_id, metadata=meta,
                ) as claim:
                    if claim.already_applied:
                        return StripeHandleResult(
                            status="ok", action="duplicate",
                            message="Already applied (idempotent)",
                            kind="user_credits", external_id=payment_intent_id,
                            tenant=tenant, project=project, user_id=user_id,
                        )
                    # APPLY via UserCreditsManager inside SAME transaction
                    await self.user_credits_mgr.add_lifetime_credits(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        usd_amount=float(usd_amount),
                        purchase_id=payment_intent_id,
                        notes=notes,
                        conn=conn,
                    )

        return StripeHandleResult(
            status="ok",
            action="applied",
            message=f"Added user credits: +{int(tokens_added)} tokens (~${usd_amount:.2f})",
            kind="user_credits",
            external_id=payment_intent_id,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )

    async def _user_for_stripe_customer(self, tenant: str, project: str, customer_id: Any) -> Optional[str]:
        """Our side of the customer mapping: the user whose `user_plans` row holds
        this Stripe customer id. Customers carry no user id in their metadata."""
        if not customer_id or self.pg_pool is None:
            return None
        try:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT user_id FROM {_project_schema(tenant, project)}.{SubscriptionManager.TABLE} "
                    "WHERE tenant=$1 AND project=$2 AND stripe_customer_id=$3 LIMIT 1",
                    tenant, project, str(customer_id),
                )
        except Exception:
            logger.exception("user_plans lookup by Stripe customer failed: %s", customer_id)
            return None
        return str(row["user_id"]) if row and row["user_id"] else None

    async def _handle_invoice_paid(self, event: Dict[str, Any], invoice: Dict[str, Any]) -> StripeHandleResult:
        meta, tenant, project, user_id = self._resolve_invoice_context(invoice)
        if not user_id:
            user_id = await self._user_for_stripe_customer(tenant, project, invoice.get("customer"))
            if user_id:
                meta = {**meta, "user_id": user_id}
        if not user_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing user_id (invoice/lines/subscription/customer metadata)")

        if not self._is_subscription_invoice(invoice, meta):
            return StripeHandleResult(status="ok", action="ignored", message="invoice.paid not marked as subscription invoice")

        currency = (invoice.get("currency") or "usd").lower()
        amount_cents = int(invoice.get("amount_paid") or invoice.get("amount_due") or invoice.get("total") or 0)
        if currency != "usd" or amount_cents <= 0:
            return StripeHandleResult(status="ok", action="ignored", message=f"Unsupported currency/amount ({currency},{amount_cents})")

        invoice_id = str(invoice.get("id") or "")
        stripe_event_id = str(event.get("id") or "")
        if not invoice_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing invoice id")

        usd_amount = amount_cents / 100.0
        plan_id = (meta.get("plan_id") or "").strip() or None
        price_id = self._price_id_from_invoice_lines(invoice)
        plan = None
        if plan_id:
            plan = await self.subscription_mgr.get_plan(
                tenant=tenant, project=project, plan_id=plan_id, include_inactive=True
            )
        if not plan and price_id:
            plan = await self.subscription_mgr.get_plan_by_stripe_price_id(
                tenant=tenant, project=project, stripe_price_id=price_id, include_inactive=True
            )
        if plan:
            plan_id = plan.plan_id
            monthly_price_cents = int(plan.monthly_price_cents or amount_cents)
        else:
            monthly_price_cents = int(amount_cents)

        stripe_customer_id = invoice.get("customer")
        stripe_subscription_id = invoice.get("parent").get("subscription_details").get("subscription")

        next_charge_at = None
        # Prefer subscription.current_period_end if we can (more reliable), else fallback to invoice lines period end
        stripe = self._stripe()
        if stripe and stripe_subscription_id:
            try:
                sub = stripe.Subscription.retrieve(stripe_subscription_id)
                cpe = sub.get("current_period_end")
                if cpe:
                    next_charge_at = datetime.fromtimestamp(int(cpe), tz=timezone.utc)
            except Exception:
                logger.exception("Failed to fetch subscription current_period_end: %s", stripe_subscription_id)

        if not next_charge_at:
            try:
                lines = _extract_stripe_data_list(invoice, "lines")
                if lines:
                    period_end = ((lines[0].get("period") or {}).get("end"))
                    if period_end:
                        next_charge_at = datetime.fromtimestamp(int(period_end), tz=timezone.utc)
            except Exception:
                next_charge_at = None

        notes = meta.get("notes") or f"Stripe subscription invoice.paid {invoice_id}"
        request_id = f"stripe:invoice:{invoice_id}"

        period_start = None
        try:
            lines = _extract_stripe_data_list(invoice, "lines")
            if lines:
                period_start_ts = ((lines[0].get("period") or {}).get("start"))
                if period_start_ts:
                    period_start = datetime.fromtimestamp(int(period_start_ts), tz=timezone.utc)
        except Exception:
            period_start = None

        prev_sub = await self.subscription_mgr.get_subscription(
            tenant=tenant, project=project, user_id=user_id
        )
        period_desc_new = build_subscription_period_descriptor(
            tenant=tenant,
            project=project,
            user_id=user_id,
            provider="stripe",
            stripe_subscription_id=stripe_subscription_id,
            period_end=next_charge_at,
            period_start=period_start or (prev_sub.last_charged_at if prev_sub else None),
        )
        topup_external_id = period_desc_new["period_key"]

        budget = self.subscription_budget_factory(
            tenant, project, user_id,
            period_desc_new["period_key"],
            period_desc_new["period_start"],
            period_desc_new["period_end"],
        )
        project_budget = self.project_budget_factory(tenant, project) if self.project_budget_factory else ProjectBudgetLimiter(
            redis=None, pg_pool=self.pg_pool, tenant=tenant, project=project
        )

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                key = LedgerKey(source="stripe", kind="subscription_topup",
                                external_id=topup_external_id)
                async with self.ledger.apply_once(
                    conn=conn, key=key, tenant=tenant, project=project,
                    user_id=user_id, amount_cents=amount_cents, tokens=None,
                    currency=currency, provider_event_id=stripe_event_id,
                    metadata={
                        **meta,
                        "plan_id": plan_id,
                        "stripe_price_id": price_id,
                        "period_end": period_desc_new["period_end"].isoformat(),
                        "period_key": period_desc_new["period_key"],
                    },
                ) as claim:
                    if claim.already_applied:
                        return StripeHandleResult(
                            status="ok", action="duplicate",
                            message="Already applied (idempotent)",
                            kind="subscription_topup", external_id=topup_external_id,
                            tenant=tenant, project=project, user_id=user_id,
                        )
                    prev_sub = await self.subscription_mgr.get_subscription(
                        tenant=tenant, project=project, user_id=user_id, conn=conn
                    )
                    if prev_sub and prev_sub.next_charge_at:
                        # Ensure we rollover the EXACT old period.
                        # If prev_sub.next_charge_at was somehow already advanced to the new period end,
                        # we must reconstruct the old period end from the invoice's period_start.
                        old_period_end = prev_sub.next_charge_at
                        if period_start and next_charge_at and old_period_end >= next_charge_at:
                            old_period_end = period_start

                        prev_desc = build_subscription_period_descriptor(
                            tenant=tenant,
                            project=project,
                            user_id=user_id,
                            provider=prev_sub.provider,
                            stripe_subscription_id=prev_sub.stripe_subscription_id,
                            period_end=old_period_end,
                            period_start=prev_sub.last_charged_at,
                        )
                        period_key = prev_desc["period_key"]
                        prev_budget = self.subscription_budget_factory(
                            tenant, project, user_id,
                            prev_desc["period_key"],
                            prev_desc["period_start"],
                            prev_desc["period_end"],
                        )

                        await self.subscription_mgr.rollover_unused_balance_once(
                            tenant=tenant,
                            project=project,
                            user_id=user_id,
                            subscription_budget=prev_budget,
                            project_budget=project_budget,
                            period_key=period_key,
                            period_end=prev_desc["period_end"],
                            actor="stripe",
                            conn=conn,
                        )

                    # APPLY via SubscriptionBudgetLimiter inside SAME transaction (+ledger).
                    # An entitlement-only plan sells access, not model budget: the
                    # payment is recorded on the ledger row (amount_cents) and the
                    # subscription snapshot is updated, but no plan budget is funded.
                    entitlement_only = plan_is_entitlement_only(plan)
                    if entitlement_only:
                        logger.info(
                            "[invoice.paid] plan %s is entitlement-only; invoice %s funds no plan budget",
                            plan_id, invoice_id,
                        )
                    else:
                        await budget.topup_subscription_budget(
                            usd_amount=float(usd_amount),
                            notes=notes,
                            request_id=request_id,
                            conn=conn,
                        )

                    # Update subscription snapshot
                    now = datetime.now(timezone.utc)
                    await self.subscription_mgr.upsert_from_stripe_invoice_paid(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        monthly_price_cents=int(monthly_price_cents),
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        next_charge_at=next_charge_at,
                        charged_at=now,
                        plan_id=plan_id,
                        conn=conn,
                    )

        # Cancel the old Stripe subscription if the user switched plans via Checkout.
        # prev_sub here holds the state before this invoice's upsert (fetched at tx open time).
        # If the subscription IDs differ, a new subscription replaced the old one — cancel it now.
        if (
            prev_sub
            and prev_sub.stripe_subscription_id
            and stripe_subscription_id
            and prev_sub.stripe_subscription_id != stripe_subscription_id
        ):
            stripe_client = self._stripe()
            if stripe_client:
                try:
                    stripe_client.Subscription.cancel(prev_sub.stripe_subscription_id)
                    logger.info(
                        "[invoice.paid] Cancelled old Stripe subscription %s for user %s (switched to %s)",
                        prev_sub.stripe_subscription_id, user_id, stripe_subscription_id,
                    )
                except Exception:
                    logger.exception(
                        "[invoice.paid] Failed to cancel old Stripe subscription %s for user %s",
                        prev_sub.stripe_subscription_id, user_id,
                    )

        return StripeHandleResult(
            status="ok",
            action="applied",
            message=(
                f"Subscription invoice recorded for entitlement-only plan {plan_id}: no plan budget funded"
                if entitlement_only
                else f"Subscription invoice credited subscription balance: +${usd_amount:.2f}"
            ),
            kind="subscription_topup",
            external_id=topup_external_id,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )

    async def _handle_refund_updated(self, event: Dict[str, Any], refund: Dict[str, Any]) -> StripeHandleResult:
        refund_id = str(refund.get("id") or "")
        if not refund_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing refund id")

        meta = self._meta(refund)
        refund_status = str(refund.get("status") or "")
        payment_intent_id = str(refund.get("payment_intent") or "")
        amount_cents = int(refund.get("amount") or 0)
        tenant = meta.get("tenant") or self.default_tenant
        project = meta.get("project") or self.default_project

        # record stripe event idempotently
        stripe_event_id = str(event.get("id") or "")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                ext_key = LedgerKey(source="stripe", kind="wallet_refund",
                                    external_id=refund_id)
                claim = await self.ledger.claim(
                    conn=conn, key=ext_key, tenant=tenant, project=project,
                    user_id=meta.get("user_id"), amount_cents=amount_cents,
                    tokens=None, currency=(refund.get("currency") or "usd"),
                    provider_event_id=stripe_event_id, metadata=meta,
                )
                if claim.already_applied:
                    return StripeHandleResult(status="ok", action="duplicate",
                                              message="Already applied (idempotent)")

                request_id = meta.get("refund_request_id")
                if not request_id and payment_intent_id and amount_cents > 0:
                    request_id = f"wallet_refund:{payment_intent_id}:{amount_cents}"

                if request_id:
                    int_key = LedgerKey(source="internal", kind="wallet_refund",
                                        external_id=request_id)
                    internal = await self.ledger.fetch_for_update(
                        conn=conn, key=int_key, tenant=tenant, project=project)
                else:
                    int_key, internal = None, None

                if internal and refund_status in ("succeeded",):
                    if str(internal["status"]) != "applied":
                        await self.ledger.mark_applied(
                            conn=conn, key=int_key, tenant=tenant, project=project)
                elif internal and refund_status in ("failed", "canceled"):
                    if str(internal["status"]) == "pending":
                        tokens = int(internal["tokens"] or 0)
                        usd_amount = float(int(internal["amount_cents"] or 0)) / 100.0
                        if tokens > 0 and usd_amount > 0:
                            await self.user_credits_mgr.restore_lifetime_credits(
                                tenant=str(internal["tenant"]),
                                project=str(internal["project"]),
                                user_id=str(internal["user_id"]),
                                usd_amount=usd_amount,
                                conn=conn,
                            )
                        await self.ledger.mark_failed(
                            conn=conn, key=int_key, tenant=tenant, project=project,
                            error=f"stripe refund status={refund_status}")

                if refund_status in ("succeeded", "failed", "canceled"):
                    await self.ledger.mark_applied(
                        conn=conn, key=ext_key, tenant=tenant, project=project)

        if refund_status in ("succeeded", "failed", "canceled"):
            await send_admin_email(
                subject=f"Wallet refund {refund_status}",
                body=(
                    f"Refund update from Stripe\n"
                    f"status={refund_status}\n"
                    f"refund_id={refund_id}\n"
                    f"payment_intent_id={payment_intent_id}\n"
                    f"amount_cents={amount_cents}\n"
                    f"user_id={meta.get('user_id')}\n"
                    f"tenant={meta.get('tenant')}\n"
                    f"project={meta.get('project')}"
                ),
            )

        return StripeHandleResult(
            status="ok",
            action="applied" if refund_status in ("succeeded", "failed", "canceled") else "pending",
            message=f"Refund event processed: status={refund_status}",
            kind="wallet_refund",
            external_id=refund_id,
            tenant=meta.get("tenant"),
            project=meta.get("project"),
            user_id=meta.get("user_id"),
        )

    async def _handle_subscription_event(self, event: Dict[str, Any], sub: Dict[str, Any]) -> StripeHandleResult:
        stripe_sub_id = str(sub.get("id") or "")
        if not stripe_sub_id:
            return StripeHandleResult(status="ok", action="ignored", message="Missing subscription id")

        meta = self._meta(sub)
        tenant = meta.get("tenant") or self.default_tenant
        project = meta.get("project") or self.default_project
        user_id = meta.get("user_id")

        # fallback lookup by stripe id if metadata missing
        if not user_id:
            existing = await self.subscription_mgr.get_subscription_by_stripe_id(tenant=tenant, project=project, stripe_subscription_id=stripe_sub_id)
            if existing:
                tenant, project, user_id = existing.tenant, existing.project, existing.user_id

        stripe_status = str(sub.get("status") or "")
        cp_status = map_stripe_subscription_status_to_cp(stripe_status)
        items_data = _extract_stripe_data_list(sub, "items")
        cpe = items_data[0] if items_data else {}
        next_charge_at = _extract_stripe_timestamp(cpe, "current_period_end")

        stripe_event_id = str(event.get("id") or "")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                ext_key = LedgerKey(source="stripe", kind="subscription_status",
                                    external_id=stripe_event_id or stripe_sub_id)
                claim = await self.ledger.claim(
                    conn=conn, key=ext_key, tenant=tenant, project=project,
                    user_id=user_id, amount_cents=None, tokens=None, currency=None,
                    provider_event_id=stripe_event_id, metadata=meta,
                )
                if claim.already_applied:
                    return StripeHandleResult(status="ok", action="duplicate",
                                              message="Already applied (idempotent)")

                if cp_status == "canceled":
                    # Rollover logic before final cancellation
                    existing_sub = await self.subscription_mgr.get_subscription_by_stripe_id(
                        tenant=tenant,
                        project=project,
                        stripe_subscription_id=stripe_sub_id,
                        conn=conn,
                    )
                    if existing_sub and existing_sub.next_charge_at and user_id:
                        try:
                            # Build period descriptor for the CURRENT (now final) period
                            period_desc = build_subscription_period_descriptor(
                                tenant=tenant,
                                project=project,
                                user_id=user_id,
                                provider=existing_sub.provider,
                                stripe_subscription_id=stripe_sub_id,
                                period_end=existing_sub.next_charge_at,
                                period_start=existing_sub.last_charged_at,
                            )
                            
                            budget = self.subscription_budget_factory(
                                tenant, project, user_id,
                                period_desc["period_key"],
                                period_desc["period_start"],
                                period_desc["period_end"],
                            )
                            project_budget = self.project_budget_factory(tenant, project) if self.project_budget_factory else ProjectBudgetLimiter(
                                redis=None, pg_pool=self.pg_pool, tenant=tenant, project=project
                            )

                            await self.subscription_mgr.rollover_unused_balance_once(
                                tenant=tenant,
                                project=project,
                                user_id=user_id,
                                subscription_budget=budget,
                                project_budget=project_budget,
                                period_key=period_desc["period_key"],
                                period_end=period_desc["period_end"],
                                actor="stripe_cancel",
                                conn=conn,
                            )
                        except Exception:
                            logger.exception("Rollover failed during subscription cancellation for user %s", user_id)

                    await self.subscription_mgr.update_status_by_stripe_id(
                        tenant=tenant,
                        project=project,
                        stripe_subscription_id=stripe_sub_id,
                        status="canceled",
                        next_charge_at=None,
                        conn=conn,
                    )
                    # mark cancel request (if any)
                    int_key = LedgerKey(source="internal", kind="subscription_cancel",
                                        external_id=stripe_sub_id)
                    internal = await self.ledger.fetch_for_update(
                        conn=conn, key=int_key, tenant=tenant, project=project)
                    if internal and str(internal["status"]) != "applied":
                        await self.ledger.mark_applied(
                            conn=conn, key=int_key, tenant=tenant, project=project)
                else:
                    # keep status updated for suspended/active.
                    # DO NOT update next_charge_at here! Advancing next_charge_at before
                    # invoice.paid tops up the new budget causes a "0 balance" gap.
                    # invoice.paid will definitively update next_charge_at.
                    await self.subscription_mgr.update_status_by_stripe_id(
                        tenant=tenant,
                        project=project,
                        stripe_subscription_id=stripe_sub_id,
                        status=cp_status,
                        next_charge_at=None,
                        conn=conn,
                    )

                await self.ledger.mark_applied(conn=conn, key=ext_key, tenant=tenant,
                                               project=project)

        if cp_status == "canceled":
            await send_admin_email(
                subject="Subscription canceled (Stripe)",
                body=(
                    f"Stripe subscription canceled\n"
                    f"stripe_subscription_id={stripe_sub_id}\n"
                    f"user_id={user_id}\n"
                    f"tenant={tenant}\n"
                    f"project={project}\n"
                    f"stripe_status={stripe_status}"
                ),
            )

        return StripeHandleResult(
            status="ok",
            action="applied",
            message=f"Subscription event processed: status={cp_status}",
            kind="subscription_status",
            external_id=stripe_sub_id,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )


# =============================================================================
# Admin Stripe operations (refunds + subscription cancel + reconcile)
# =============================================================================

class StripeEconomicsAdminService:
    def __init__(
        self,
        *,
        pg_pool: asyncpg.Pool,
        user_credits_mgr: UserCreditsManager,
        subscription_mgr: SubscriptionManager,
        stripe_api_key: Optional[str] = None,
        ref_provider: str = "anthropic",
        ref_model: str = "claude-sonnet-4-5-20250929",
    ):
        self.pg_pool = pg_pool
        self.ledger = ExternalEconomicsLedger(pg_pool=pg_pool)
        self.user_credits_mgr = user_credits_mgr
        self.subscription_mgr = subscription_mgr
        self.stripe_api_key = stripe_api_key or _configured_stripe_secret("stripe.secret_key")
        self.ref_provider = ref_provider
        self.ref_model = ref_model

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            raise RuntimeError("Stripe API key not configured (platform.services.stripe.secret_key)")
        stripe.api_key = self.stripe_api_key
        return stripe

    async def _ensure_stripe_api_key(self) -> None:
        if not self.stripe_api_key:
            self.stripe_api_key = await _resolve_stripe_secret_async("stripe.secret_key")

    async def request_wallet_refund(
        self,
        *,
        tenant: str,
        project: str,
        user_id: str,
        payment_intent_id: str,
        usd_amount: Optional[float] = None,
        notes: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_stripe_api_key()
        stripe = self._stripe()
        pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["charges"])

        currency = (pi.get("currency") or "usd").lower()
        if currency != "usd":
            raise ValueError(f"Unsupported currency for refund: {currency}")

        amount_received = int(pi.get("amount_received") or pi.get("amount") or 0)
        charges = _extract_stripe_data_list(pi, "charges")
        amount_refunded = sum(int(c.get("amount_refunded") or 0) for c in charges)
        max_refundable = max(0, amount_received - amount_refunded)
        if max_refundable <= 0:
            raise ValueError("Nothing refundable on this payment_intent")

        if usd_amount is None:
            refund_cents = max_refundable
        else:
            refund_cents = int(round(float(usd_amount) * 100))
            if refund_cents <= 0:
                raise ValueError("usd_amount must be > 0")
            if refund_cents > max_refundable:
                raise ValueError(f"Requested refund exceeds refundable amount: {refund_cents} > {max_refundable}")

        refund_usd = float(refund_cents) / 100.0

        # If payment intent has metadata user_id, validate
        meta = (pi.get("metadata") or {})
        pi_user_id = meta.get("user_id")
        if pi_user_id and str(pi_user_id) != str(user_id):
            raise ValueError("payment_intent user_id does not match requested user_id")

        # The wallet was credited the top-up's pre-tax price, not the amount
        # received (see net_topup_cents). A refund returns money in proportion,
        # so it debits the wallet in the same proportion. Untaxed: identical.
        credited_cents = net_topup_cents(meta, amount_received, reference=payment_intent_id)
        if credited_cents >= amount_received:
            credit_cents = refund_cents
        else:
            credit_cents = int(round(refund_cents * credited_cents / amount_received))
        credit_usd = float(credit_cents) / 100.0

        tokens_refund, _ = quote_tokens_for_usd(
            usd_amount=credit_usd,
            ref_provider=self.ref_provider,
            ref_model=self.ref_model,
        )
        tokens_refund = int(tokens_refund)
        if tokens_refund <= 0:
            raise ValueError("Refund amount too small to convert to tokens")

        external_id = f"wallet_refund:{payment_intent_id}:{refund_cents}"

        key = LedgerKey(source="internal", kind="wallet_refund",
                        external_id=external_id)
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # amount_cents is the WALLET effect: the failure paths restore it.
                claim = await self.ledger.claim(
                    conn=conn, key=key, tenant=tenant, project=project,
                    user_id=user_id, amount_cents=credit_cents, tokens=tokens_refund,
                    currency="usd", provider_event_id=None,
                    metadata={
                        "payment_intent_id": payment_intent_id,
                        "refund_cents": refund_cents,
                        "refund_usd": refund_usd,
                        "credit_cents": credit_cents,
                        "tokens": tokens_refund,
                        "notes": notes,
                        "requested_by": actor or "unknown",
                    },
                )

                if claim.already_applied:
                    return {
                        "status": "ok",
                        "action": "duplicate",
                        "message": "Refund already applied",
                        "external_id": external_id,
                    }

                # If not created now and still pending, then it's a real concurrent duplicate
                if claim.concurrent_duplicate:
                    return {
                        "status": "ok",
                        "action": "duplicate",
                        "message": "Refund already pending",
                        "external_id": external_id,
                    }

                if claim.status == "failed":
                    raise ValueError("Previous refund attempt failed; use a new request or investigate")

                await self.user_credits_mgr.refund_lifetime_credits(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    usd_amount=credit_usd,
                    conn=conn,
                )

        # Stripe call outside transaction
        try:
            refund = stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=refund_cents,
                metadata={
                    "tenant": tenant,
                    "project": project,
                    "user_id": user_id,
                    "kdcube_refund_kind": "wallet",
                    "refund_request_id": external_id,
                    "refund_usd": str(refund_usd),
                    "refund_tokens": str(tokens_refund),
                    "notes": notes or "",
                },
                idempotency_key=external_id,
            )
        except Exception as e:
            # restore credits + mark failed
            async with self.pg_pool.acquire() as conn:
                async with conn.transaction():
                    await self.user_credits_mgr.restore_lifetime_credits(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        usd_amount=credit_usd,
                        conn=conn,
                    )
                    await self.ledger.mark_failed(
                        conn=conn, key=key, tenant=tenant, project=project,
                        error=str(e))
            await send_admin_email(
                subject="Wallet refund FAILED",
                body=(
                    f"Refund request failed\n"
                    f"user_id={user_id}\n"
                    f"payment_intent_id={payment_intent_id}\n"
                    f"refund_usd=${refund_usd:.2f}\n"
                    f"refund_tokens={tokens_refund}\n"
                    f"external_id={external_id}\n"
                    f"error={e}"
                ),
            )
            raise

        # record refund id for reconciliation
        refund_id = str(refund.get("id") or "")
        refund_status = str(refund.get("status") or "")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self.ledger.merge_metadata(
                    conn=conn, key=key, tenant=tenant, project=project,
                    metadata={
                        "stripe_refund_id": refund_id,
                        "stripe_refund_status": refund_status,
                    },
                )

        await send_admin_email(
            subject="Wallet refund requested",
            body=(
                f"Refund requested (pending)\n"
                f"user_id={user_id}\n"
                f"payment_intent_id={payment_intent_id}\n"
                f"refund_usd=${refund_usd:.2f}\n"
                f"refund_tokens={tokens_refund}\n"
                f"stripe_refund_id={refund_id}\n"
                f"external_id={external_id}\n"
                f"notes={notes or ''}"
            ),
        )

        return {
            "status": "ok",
            "action": "pending",
            "message": "Refund requested; awaiting Stripe confirmation",
            "external_id": external_id,
            "stripe_refund_id": refund_id,
            "refund_usd": refund_usd,
            "credit_usd": credit_usd,
            "refund_tokens": tokens_refund,
        }

    async def request_subscription_cancel(
        self,
        *,
        tenant: str,
        project: str,
        user_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        actor: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_stripe_api_key()
        stripe = self._stripe()

        sub = None
        if stripe_subscription_id:
            sub = await self.subscription_mgr.get_subscription_by_stripe_id(
                tenant=tenant,
                project=project,
                stripe_subscription_id=stripe_subscription_id,
            )
        if not sub and user_id:
            sub = await self.subscription_mgr.get_subscription(tenant=tenant, project=project, user_id=user_id)
        if not sub:
            raise ValueError("subscription not found")
        if sub.provider != "stripe":
            raise ValueError("only stripe subscriptions can be canceled here")

        stripe_sub_id = stripe_subscription_id or sub.stripe_subscription_id
        if not stripe_sub_id:
            raise ValueError("stripe_subscription_id not found")

        external_id = str(stripe_sub_id)

        key = LedgerKey(source="internal", kind="subscription_cancel",
                        external_id=external_id)
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                claim = await self.ledger.claim(
                    conn=conn, key=key, tenant=sub.tenant, project=sub.project,
                    user_id=sub.user_id, amount_cents=None, tokens=None,
                    currency="usd", provider_event_id=None,
                    metadata={
                        "stripe_subscription_id": stripe_sub_id,
                        "notes": notes,
                        "requested_by": actor or "unknown",
                    },
                )
                if claim.already_applied:
                    return {"status": "ok", "action": "duplicate", "message": "Cancellation already applied"}

                if claim.concurrent_duplicate:
                    return {"status": "ok", "action": "duplicate", "message": "Cancellation already pending"}

                if claim.status == "failed":
                    raise ValueError("Previous cancel attempt failed; investigate before retrying")

        # Request cancel at period end
        try:
            stripe_sub = stripe.Subscription.modify(
                stripe_sub_id,
                cancel_at_period_end=True,
                metadata={
                    "tenant": sub.tenant,
                    "project": sub.project,
                    "user_id": sub.user_id,
                    "kdcube_cancel_kind": "subscription",
                    "cancel_request_id": external_id,
                    "notes": notes or "",
                },
            )
        except Exception as e:
            async with self.pg_pool.acquire() as conn:
                async with conn.transaction():
                    await self.ledger.mark_failed(
                        conn=conn, key=key, tenant=sub.tenant, project=sub.project,
                        error=str(e))
            await send_admin_email(
                subject="Subscription cancel FAILED",
                body=(
                    f"Stripe cancel request failed\n"
                    f"stripe_subscription_id={stripe_sub_id}\n"
                    f"user_id={sub.user_id}\n"
                    f"tenant={sub.tenant}\n"
                    f"project={sub.project}\n"
                    f"error={e}"
                ),
            )
            raise

        cancel_at = stripe_sub.get("cancel_at_period_end")
        status_stripe = stripe_sub.get("status")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self.ledger.merge_metadata(
                    conn=conn, key=key, tenant=sub.tenant, project=sub.project,
                    metadata={
                        "stripe_status": status_stripe,
                        "cancel_at_period_end": bool(cancel_at),
                    },
                )

        await send_admin_email(
            subject="Subscription cancel requested",
            body=(
                f"Cancellation requested (pending)\n"
                f"stripe_subscription_id={stripe_sub_id}\n"
                f"user_id={sub.user_id}\n"
                f"tenant={sub.tenant}\n"
                f"project={sub.project}\n"
                f"cancel_at_period_end={bool(cancel_at)}\n"
                f"stripe_status={status_stripe}\n"
                f"notes={notes or ''}"
            ),
        )

        return {
            "status": "ok",
            "action": "pending",
            "message": "Cancellation requested; awaiting Stripe confirmation",
            "stripe_subscription_id": stripe_sub_id,
            "cancel_at_period_end": bool(cancel_at),
            "stripe_status": status_stripe,
        }

    async def reconcile_pending_requests(
        self,
        *,
        tenant: str,
        project: str,
        kind: str = "all",
        limit: int = 200,
    ) -> Dict[str, Any]:
        await self._ensure_stripe_api_key()
        stripe = self._stripe()
        kind = (kind or "all").lower()
        if kind not in ("all", "wallet_refund", "subscription_cancel"):
            raise ValueError("kind must be all|wallet_refund|subscription_cancel")

        kinds = None if kind == "all" else (kind,)
        rows = await self.ledger.list_by_status(
            tenant=tenant, project=project, source="internal",
            status="pending", kinds=kinds, limit=int(limit))

        reconciled = 0
        applied = 0
        failed = 0

        for row in rows:
            k = str(row["kind"])
            external_id = str(row["external_id"])
            row_key = LedgerKey(source="internal", kind=k, external_id=external_id)
            raw_metadata = row["metadata"]
            
            metadata = {}
            if isinstance(raw_metadata, dict):
                metadata = raw_metadata
            elif isinstance(raw_metadata, list):
                # Handle the case where metadata is a list of items (can be dicts or JSON strings)
                for item in raw_metadata:
                    if isinstance(item, dict):
                        metadata.update(item)
                    elif isinstance(item, str):
                        try:
                            d = json.loads(item)
                            if isinstance(d, dict):
                                metadata.update(d)
                        except Exception:
                            pass
            elif isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except Exception:
                    metadata = {}

            if k == "wallet_refund":
                refund_id = metadata.get("stripe_refund_id")
                if not refund_id:
                    continue
                refund = stripe.Refund.retrieve(refund_id)
                status = str(refund.get("status") or "")

                if status in ("succeeded",):
                    async with self.pg_pool.acquire() as conn:
                        async with conn.transaction():
                            await self.ledger.mark_applied(
                                conn=conn, key=row_key, tenant=row["tenant"],
                                project=row["project"])
                    applied += 1
                    reconciled += 1
                    await send_admin_email(
                        subject="Wallet refund reconciled (succeeded)",
                        body=(
                            f"Refund reconciled\n"
                            f"refund_id={refund_id}\n"
                            f"external_id={external_id}\n"
                            f"user_id={row['user_id']}\n"
                            f"tenant={row['tenant']}\n"
                            f"project={row['project']}"
                        ),
                    )
                elif status in ("failed", "canceled"):
                    tokens = int(row["tokens"] or 0)
                    usd_amount = float(row["amount_cents"] or 0) / 100.0
                    async with self.pg_pool.acquire() as conn:
                        async with conn.transaction():
                            await self.user_credits_mgr.restore_lifetime_credits(
                                tenant=row["tenant"],
                                project=row["project"],
                                user_id=row["user_id"],
                                usd_amount=usd_amount,
                                conn=conn,
                            )
                            await self.ledger.mark_failed(
                                conn=conn, key=row_key, tenant=row["tenant"],
                                project=row["project"],
                                error=f"stripe refund status={status}")
                    failed += 1
                    reconciled += 1
                    await send_admin_email(
                        subject=f"Wallet refund reconciled ({status})",
                        body=(
                            f"Refund reconciled as {status}\n"
                            f"refund_id={refund_id}\n"
                            f"external_id={external_id}\n"
                            f"user_id={row['user_id']}\n"
                            f"tenant={row['tenant']}\n"
                            f"project={row['project']}"
                        ),
                    )
                else:
                    continue

            elif k == "subscription_cancel":
                stripe_sub_id = metadata.get("stripe_subscription_id") or external_id
                if not stripe_sub_id:
                    continue
                try:
                    sub = stripe.Subscription.retrieve(stripe_sub_id)
                except Exception:
                    # if subscription missing, mark failed
                    async with self.pg_pool.acquire() as conn:
                        async with conn.transaction():
                            await self.ledger.mark_failed(
                                conn=conn, key=row_key, tenant=row["tenant"],
                                project=row["project"],
                                error="stripe subscription not found")
                    failed += 1
                    reconciled += 1
                    continue

                cp_status = map_stripe_subscription_status_to_cp(sub.get("status"))
                current_period_end = sub.get("current_period_end")
                next_charge_at = datetime.fromtimestamp(int(current_period_end), tz=timezone.utc) if current_period_end else None

                if cp_status == "canceled":
                    async with self.pg_pool.acquire() as conn:
                        async with conn.transaction():
                            await self.subscription_mgr.update_status_by_stripe_id(
                                tenant=row["tenant"],
                                project=row["project"],
                                stripe_subscription_id=stripe_sub_id,
                                status="canceled",
                                next_charge_at=None,
                                conn=conn,
                            )
                            await self.ledger.mark_applied(
                                conn=conn, key=row_key, tenant=row["tenant"],
                                project=row["project"])
                    applied += 1
                    reconciled += 1
                    await send_admin_email(
                        subject="Subscription cancel reconciled (canceled)",
                        body=(
                            f"Subscription cancel reconciled\n"
                            f"stripe_subscription_id={stripe_sub_id}\n"
                            f"external_id={external_id}\n"
                            f"user_id={row['user_id']}\n"
                            f"tenant={row['tenant']}\n"
                            f"project={row['project']}"
                        ),
                    )
                else:
                    # still active or suspended => keep pending
                    continue

        return {
            "status": "ok",
            "reconciled": reconciled,
            "applied": applied,
            "failed": failed,
        }

    async def reconcile_stripe_events(
        self,
        *,
        handler: StripeEconomicsWebhookHandler,
        since_timestamp: int,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Fetch recent events from Stripe API since since_timestamp and process them.
        Returns result summary and the latest_event_timestamp for state tracking.
        """
        await self._ensure_stripe_api_key()
        stripe = self._stripe()
        import time
        
        # Stripe events are available for 30 days
        max_back_seconds = 30 * 24 * 3600
        min_allowed_timestamp = int(time.time()) - max_back_seconds
        actual_start_time = max(since_timestamp, min_allowed_timestamp)

        logger.info("[Stripe Reconcile] Fetching events created since %s", 
                    datetime.fromtimestamp(actual_start_time, tz=timezone.utc))

        events = stripe.Event.list(created={"gt": actual_start_time}, limit=limit)
        
        reconciled = 0
        applied = 0
        skipped = 0
        errors = 0
        latest_ts = actual_start_time

        # auto_paging_iter handles pagination automatically
        for event in events.auto_paging_iter():
            reconciled += 1
            if event.created > latest_ts:
                latest_ts = event.created
                
            res = await handler.process_event(event)
            if res.status == "ok":
                if res.action == "applied":
                    applied += 1
                else:
                    skipped += 1
            else:
                errors += 1
                logger.error("[Stripe Reconcile] Failed to process event %s (%s): %s", 
                             event.id, event.type, res.message)

        return {
            "status": "ok",
            "reconciled": reconciled,
            "applied": applied,
            "skipped": skipped,
            "errors": errors,
            "latest_event_timestamp": latest_ts
        }

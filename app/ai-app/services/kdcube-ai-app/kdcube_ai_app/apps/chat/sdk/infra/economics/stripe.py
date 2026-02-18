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
from kdcube_ai_app.infra.accounting.usage import quote_tokens_for_usd
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.infra.channel.email import send_admin_email

logger = logging.getLogger(__name__)

SubscriptionBudgetFactory = Callable[[str, str, str, str, datetime, datetime], SubscriptionBudgetLimiter]
ProjectBudgetFactory = Callable[[str, str], ProjectBudgetLimiter]


def map_stripe_subscription_status_to_cp(status: str) -> str:
    # CP constraint: active|canceled|suspended
    s = (status or "").lower()
    if s in ("active", "trialing"):
        return "active"
    if s in ("canceled",):
        return "canceled"
    return "suspended"


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
        self.stripe_api_key = stripe_api_key or os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            raise RuntimeError("Stripe API key not configured (STRIPE_SECRET_KEY)")
        stripe.api_key = self.stripe_api_key
        return stripe

    @staticmethod
    def _map_stripe_subscription_status_to_cp(status: str) -> str:
        return map_stripe_subscription_status_to_cp(status)

    async def create_subscription(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            tier: str,
            stripe_price_id: str,
            stripe_customer_id: Optional[str] = None,
            monthly_price_cents_hint: Optional[int] = None,
            metadata: Optional[Dict[str, str]] = None,
    ) -> StripeCreateSubscriptionResult:
        """
        Creates a Stripe subscription and stores a best-effort snapshot in user_subscriptions.
        NOTE: If the customer has no default payment method, the subscription may start as 'incomplete'
        until payment is completed.
        """
        tier = (tier or "paid").lower()
        if tier not in ("free", "paid", "premium", "admin"):
            tier = "paid"

        stripe = self._stripe()

        md = dict(metadata or {})
        md.setdefault("tenant", tenant or self.default_tenant)
        md.setdefault("project", project or self.default_project)
        md.setdefault("user_id", user_id)
        md.setdefault("tier", tier)
        md.setdefault("kdcube_invoice_kind", "subscription")

        # Ensure customer
        customer_id = stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(metadata=md)
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
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"""
                    INSERT INTO {SubscriptionManager.CP}.{SubscriptionManager.TABLE} (
                      tenant, project, user_id,
                      tier, status, monthly_price_cents,
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
                      tier=EXCLUDED.tier,
                      status=EXCLUDED.status,
                      monthly_price_cents=CASE
                        WHEN EXCLUDED.monthly_price_cents > 0 THEN EXCLUDED.monthly_price_cents
                        ELSE {SubscriptionManager.CP}.{SubscriptionManager.TABLE}.monthly_price_cents
                      END,
                      next_charge_at=COALESCE(EXCLUDED.next_charge_at, {SubscriptionManager.CP}.{SubscriptionManager.TABLE}.next_charge_at),
                      stripe_customer_id=COALESCE(EXCLUDED.stripe_customer_id, {SubscriptionManager.CP}.{SubscriptionManager.TABLE}.stripe_customer_id),
                      stripe_subscription_id=COALESCE(EXCLUDED.stripe_subscription_id, {SubscriptionManager.CP}.{SubscriptionManager.TABLE}.stripe_subscription_id),
                      updated_at=NOW()
                """, tenant, project, user_id, tier, sub_status, price_cents, next_charge_at, customer_id, sub_id)

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
      - provides idempotency via kdcube_control_plane.external_economics_events
      - applies effects via managers:
          * UserCreditsManager (lifetime credits)
          * SubscriptionBudgetLimiter (per-user subscription balance)
      - maintains subscription state in user_subscriptions (DB upsert)

    Supported:
      - payment_intent.succeeded -> add lifetime user tokens
      - invoice.paid -> if subscription invoice -> top up subscription balance + update user_subscriptions
    """

    CP = "kdcube_control_plane"

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
        self.user_credits_mgr = user_credits_mgr
        self.subscription_budget_factory = subscription_budget_factory
        self.project_budget_factory = project_budget_factory
        self.subscription_mgr = subscription_mgr

        self.default_tenant = default_tenant
        self.default_project = default_project

        self.webhook_secret = stripe_webhook_secret or os.getenv("STRIPE_WEBHOOK_SECRET")
        self.stripe_api_key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
        self.ref_provider = ref_provider
        self.ref_model = ref_model

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            return None
        stripe.api_key = self.stripe_api_key
        return stripe

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
            lines = (invoice.get("lines") or {}).get("data") or []
            if not lines:
                return {}
            m = lines[0].get("metadata") or {}
            if not isinstance(m, dict):
                return {}
            return {str(k): str(v) for k, v in m.items() if v is not None}
        except Exception:
            return {}

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
        etype = event.get("type")
        obj = (event.get("data") or {}).get("object") or {}

        try:
            if etype == "payment_intent.succeeded":
                res = await self._handle_payment_intent_succeeded(event, obj)
            elif etype == "invoice.paid":
                res = await self._handle_invoice_paid(event, obj)
            elif etype in ("refund.updated", "refund.created"):
                res = await self._handle_refund_updated(event, obj)
            elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
                res = await self._handle_subscription_event(event, obj)
            else:
                res = StripeHandleResult(status="ok", action="unsupported", message=f"Event {etype} not processed")
        except Exception as e:
            logger.exception("Stripe webhook failed: type=%s", etype)
            return {"status": "error", "action": "failed", "message": str(e), "event_type": etype}

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

    # ---------------- verification ----------------

    def _verify_and_parse(self, *, body: bytes, stripe_signature: Optional[str]) -> Dict[str, Any]:
        if not self.webhook_secret:
            logger.warning("STRIPE_WEBHOOK_SECRET not set: parsing Stripe event WITHOUT verification")
            return json.loads(body.decode("utf-8"))

        if not stripe_signature:
            raise ValueError("Missing Stripe-Signature header")

        import stripe
        return stripe.Webhook.construct_event(payload=body, sig_header=stripe_signature, secret=self.webhook_secret)

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

    # ---------------- idempotency internals ----------------

    async def _lock_or_create_ext_event(
        self,
        *,
        conn: asyncpg.Connection,
        kind: str,
        external_id: str,
        tenant: str,
        project: str,
        user_id: Optional[str],
        amount_cents: Optional[int],
        tokens: Optional[int],
        currency: Optional[str],
        stripe_event_id: Optional[str],
        metadata: Dict[str, str],
    ) -> str:
        await conn.execute(f"""
            INSERT INTO {self.CP}.external_economics_events (
              source, kind, external_id,
              tenant, project, user_id,
              amount_cents, tokens, currency,
              status, stripe_event_id, metadata
            ) VALUES (
              'stripe', $1, $2,
              $3, $4, $5,
              $6, $7, $8,
              'pending', $9, $10::jsonb
            )
            ON CONFLICT (source, kind, external_id) DO NOTHING
        """, kind, external_id, tenant, project, user_id, amount_cents, tokens, currency, stripe_event_id, json.dumps(metadata))

        row = await conn.fetchrow(f"""
            SELECT status
            FROM {self.CP}.external_economics_events
            WHERE source='stripe' AND kind=$1 AND external_id=$2
            FOR UPDATE
        """, kind, external_id)

        if not row:
            raise RuntimeError("Failed to lock/create external_economics_events row")

        return str(row["status"])

    async def _mark_ext_event_applied(self, conn: asyncpg.Connection, *, kind: str, external_id: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source='stripe' AND kind=$1 AND external_id=$2
        """, kind, external_id)

    async def _mark_ext_event_failed(self, conn: asyncpg.Connection, *, kind: str, external_id: str, error: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='failed', error=$3, updated_at=NOW()
            WHERE source='stripe' AND kind=$1 AND external_id=$2
        """, kind, external_id, error[:2000])

    async def _fetch_internal_event_for_update(
        self,
        *,
        conn: asyncpg.Connection,
        kind: str,
        external_id: str,
    ) -> Optional[asyncpg.Record]:
        return await conn.fetchrow(f"""
            SELECT *
            FROM {self.CP}.external_economics_events
            WHERE source='internal' AND kind=$1 AND external_id=$2
            FOR UPDATE
        """, kind, external_id)

    async def _mark_internal_event_applied(self, conn: asyncpg.Connection, *, kind: str, external_id: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source='internal' AND kind=$1 AND external_id=$2
        """, kind, external_id)

    async def _mark_internal_event_failed(self, conn: asyncpg.Connection, *, kind: str, external_id: str, error: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='failed', error=$3, updated_at=NOW()
            WHERE source='internal' AND kind=$1 AND external_id=$2
        """, kind, external_id, (error or "")[:2000])

    # ---------------- event handlers ----------------

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

        usd_amount = amount_cents / 100.0
        tokens_added, _ = quote_tokens_for_usd(
            usd_amount=usd_amount,
            ref_provider=self.ref_provider,
            ref_model=self.ref_model,
        )
        notes = meta.get("notes") or meta.get("package") or "Stripe payment_intent.succeeded"

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                status = await self._lock_or_create_ext_event(
                    conn=conn,
                    kind="user_credits",
                    external_id=payment_intent_id,
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    amount_cents=amount_cents,
                    tokens=int(tokens_added),
                    currency=currency,
                    stripe_event_id=stripe_event_id,
                    metadata=meta,
                )
                if status == "applied":
                    return StripeHandleResult(
                        status="ok", action="duplicate", message="Already applied (idempotent)",
                        kind="user_credits", external_id=payment_intent_id, tenant=tenant, project=project, user_id=user_id,
                    )

                try:
                    # APPLY via TierBalanceManager inside SAME transaction
                    await self.user_credits_mgr.add_lifetime_tokens(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        tokens=int(tokens_added),
                        usd_amount=float(usd_amount),
                        purchase_id=payment_intent_id,
                        notes=notes,
                        conn=conn,
                    )
                    await self._mark_ext_event_applied(conn, kind="user_credits", external_id=payment_intent_id)
                except Exception as e:
                    await self._mark_ext_event_failed(conn, kind="user_credits", external_id=payment_intent_id, error=str(e))
                    raise

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

    async def _handle_invoice_paid(self, event: Dict[str, Any], invoice: Dict[str, Any]) -> StripeHandleResult:
        meta, tenant, project, user_id = self._resolve_invoice_context(invoice)
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
        tier = (meta.get("tier") or meta.get("plan") or "paid").lower()
        if tier not in ("free", "paid", "premium", "admin"):
            tier = "paid"

        stripe_customer_id = invoice.get("customer")
        stripe_subscription_id = invoice.get("subscription")

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
                lines = (invoice.get("lines") or {}).get("data") or []
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
            lines = (invoice.get("lines") or {}).get("data") or []
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
                status = await self._lock_or_create_ext_event(
                    conn=conn,
                    kind="subscription_topup",
                    external_id=topup_external_id,
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    amount_cents=amount_cents,
                    tokens=None,
                    currency=currency,
                    stripe_event_id=stripe_event_id,
                    metadata={
                        **meta,
                        "period_end": period_desc_new["period_end"].isoformat(),
                        "period_key": period_desc_new["period_key"],
                    },
                )
                if status == "applied":
                    return StripeHandleResult(
                        status="ok", action="duplicate", message="Already applied (idempotent)",
                        kind="subscription_topup", external_id=topup_external_id, tenant=tenant, project=project, user_id=user_id,
                    )

                try:
                    prev_sub = await self.subscription_mgr.get_subscription(
                        tenant=tenant, project=project, user_id=user_id, conn=conn
                    )
                    if prev_sub and prev_sub.next_charge_at:
                        prev_desc = build_subscription_period_descriptor(
                            tenant=tenant,
                            project=project,
                            user_id=user_id,
                            provider=prev_sub.provider,
                            stripe_subscription_id=prev_sub.stripe_subscription_id,
                            period_end=prev_sub.next_charge_at,
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

                    # APPLY via SubscriptionBudgetLimiter inside SAME transaction (+ledger)
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
                        tier=tier,
                        monthly_price_cents=int(amount_cents),
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        next_charge_at=next_charge_at,
                        charged_at=now,
                        conn=conn,
                    )

                    await self._mark_ext_event_applied(conn, kind="subscription_topup", external_id=topup_external_id)
                except Exception as e:
                    await self._mark_ext_event_failed(conn, kind="subscription_topup", external_id=topup_external_id, error=str(e))
                    raise

        return StripeHandleResult(
            status="ok",
            action="applied",
            message=f"Subscription invoice credited subscription balance: +${usd_amount:.2f}",
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

        # record stripe event idempotently
        stripe_event_id = str(event.get("id") or "")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                status = await self._lock_or_create_ext_event(
                    conn=conn,
                    kind="wallet_refund",
                    external_id=refund_id,
                    tenant=meta.get("tenant") or self.default_tenant,
                    project=meta.get("project") or self.default_project,
                    user_id=meta.get("user_id"),
                    amount_cents=amount_cents,
                    tokens=None,
                    currency=(refund.get("currency") or "usd"),
                    stripe_event_id=stripe_event_id,
                    metadata=meta,
                )
                if status == "applied":
                    return StripeHandleResult(status="ok", action="duplicate", message="Already applied (idempotent)")

                request_id = meta.get("refund_request_id")
                if not request_id and payment_intent_id and amount_cents > 0:
                    request_id = f"wallet_refund:{payment_intent_id}:{amount_cents}"

                if request_id:
                    internal = await self._fetch_internal_event_for_update(
                        conn=conn,
                        kind="wallet_refund",
                        external_id=request_id,
                    )
                else:
                    internal = None

                if internal and refund_status in ("succeeded",):
                    if str(internal["status"]) != "applied":
                        await self._mark_internal_event_applied(conn, kind="wallet_refund", external_id=request_id)
                elif internal and refund_status in ("failed", "canceled"):
                    if str(internal["status"]) == "pending":
                        tokens = int(internal["tokens"] or 0)
                        usd_amount = float(int(internal["amount_cents"] or 0)) / 100.0
                        if tokens > 0 and usd_amount > 0:
                            await self.user_credits_mgr.restore_lifetime_tokens(
                                tenant=str(internal["tenant"]),
                                project=str(internal["project"]),
                                user_id=str(internal["user_id"]),
                                tokens=tokens,
                                usd_amount=usd_amount,
                                conn=conn,
                            )
                        await self._mark_internal_event_failed(
                            conn,
                            kind="wallet_refund",
                            external_id=request_id,
                            error=f"stripe refund status={refund_status}",
                        )

                if refund_status in ("succeeded", "failed", "canceled"):
                    await self._mark_ext_event_applied(conn, kind="wallet_refund", external_id=refund_id)

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
            existing = await self.subscription_mgr.get_subscription_by_stripe_id(stripe_subscription_id=stripe_sub_id)
            if existing:
                tenant, project, user_id = existing.tenant, existing.project, existing.user_id

        stripe_status = str(sub.get("status") or "")
        cp_status = map_stripe_subscription_status_to_cp(stripe_status)
        cpe = sub.get("current_period_end")
        next_charge_at = datetime.fromtimestamp(int(cpe), tz=timezone.utc) if cpe else None

        stripe_event_id = str(event.get("id") or "")
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                status = await self._lock_or_create_ext_event(
                    conn=conn,
                    kind="subscription_status",
                    external_id=stripe_event_id or stripe_sub_id,
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    amount_cents=None,
                    tokens=None,
                    currency=None,
                    stripe_event_id=stripe_event_id,
                    metadata=meta,
                )
                if status == "applied":
                    return StripeHandleResult(status="ok", action="duplicate", message="Already applied (idempotent)")

                if cp_status == "canceled":
                    await self.subscription_mgr.update_status_by_stripe_id(
                        stripe_subscription_id=stripe_sub_id,
                        status="canceled",
                        next_charge_at=None,
                        conn=conn,
                    )
                    # mark cancel request (if any)
                    internal = await self._fetch_internal_event_for_update(
                        conn=conn,
                        kind="subscription_cancel",
                        external_id=stripe_sub_id,
                    )
                    if internal and str(internal["status"]) != "applied":
                        await self._mark_internal_event_applied(conn, kind="subscription_cancel", external_id=stripe_sub_id)
                else:
                    # keep status updated for suspended/active; preserve next_charge_at from Stripe if provided
                    await self.subscription_mgr.update_status_by_stripe_id(
                        stripe_subscription_id=stripe_sub_id,
                        status=cp_status,
                        next_charge_at=next_charge_at,
                        conn=conn,
                    )

                await self._mark_ext_event_applied(conn, kind="subscription_status", external_id=stripe_event_id or stripe_sub_id)

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
    CP = "kdcube_control_plane"

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
        self.user_credits_mgr = user_credits_mgr
        self.subscription_mgr = subscription_mgr
        self.stripe_api_key = stripe_api_key or os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
        self.ref_provider = ref_provider
        self.ref_model = ref_model

    def _stripe(self):
        import stripe
        if not self.stripe_api_key:
            raise RuntimeError("Stripe API key not configured (STRIPE_SECRET_KEY)")
        stripe.api_key = self.stripe_api_key
        return stripe

    async def _lock_or_create_internal_event(
        self,
        *,
        conn: asyncpg.Connection,
        kind: str,
        external_id: str,
        tenant: str,
        project: str,
        user_id: Optional[str],
        amount_cents: Optional[int],
        tokens: Optional[int],
        metadata: Dict[str, Any],
    ) -> str:
        await conn.execute(f"""
            INSERT INTO {self.CP}.external_economics_events (
              source, kind, external_id,
              tenant, project, user_id,
              amount_cents, tokens, currency,
              status, stripe_event_id, metadata
            ) VALUES (
              'internal', $1, $2,
              $3, $4, $5,
              $6, $7, 'usd',
              'pending', NULL, $8::jsonb
            )
            ON CONFLICT (source, kind, external_id) DO NOTHING
        """, kind, external_id, tenant, project, user_id, amount_cents, tokens, json.dumps(metadata))

        row = await conn.fetchrow(f"""
            SELECT status
            FROM {self.CP}.external_economics_events
            WHERE source='internal' AND kind=$1 AND external_id=$2
            FOR UPDATE
        """, kind, external_id)

        if not row:
            raise RuntimeError("Failed to lock/create external_economics_events row (internal)")

        return str(row["status"])

    async def _update_internal_event_metadata(
        self,
        *,
        conn: asyncpg.Connection,
        kind: str,
        external_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET metadata = COALESCE(metadata, '{{}}'::jsonb) || $3::jsonb,
                updated_at = NOW()
            WHERE source='internal' AND kind=$1 AND external_id=$2
        """, kind, external_id, json.dumps(metadata))

    async def _mark_internal_event_applied(self, conn: asyncpg.Connection, *, kind: str, external_id: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source='internal' AND kind=$1 AND external_id=$2
        """, kind, external_id)

    async def _mark_internal_event_failed(self, conn: asyncpg.Connection, *, kind: str, external_id: str, error: str) -> None:
        await conn.execute(f"""
            UPDATE {self.CP}.external_economics_events
            SET status='failed', error=$3, updated_at=NOW()
            WHERE source='internal' AND kind=$1 AND external_id=$2
        """, kind, external_id, (error or "")[:2000])

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
        stripe = self._stripe()
        pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["charges"])

        currency = (pi.get("currency") or "usd").lower()
        if currency != "usd":
            raise ValueError(f"Unsupported currency for refund: {currency}")

        amount_received = int(pi.get("amount_received") or pi.get("amount") or 0)
        charges = (pi.get("charges") or {}).get("data") or []
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
        tokens_refund, _ = quote_tokens_for_usd(
            usd_amount=refund_usd,
            ref_provider=self.ref_provider,
            ref_model=self.ref_model,
        )
        tokens_refund = int(tokens_refund)
        if tokens_refund <= 0:
            raise ValueError("Refund amount too small to convert to tokens")

        # If payment intent has metadata user_id, validate
        meta = (pi.get("metadata") or {})
        pi_user_id = meta.get("user_id")
        if pi_user_id and str(pi_user_id) != str(user_id):
            raise ValueError("payment_intent user_id does not match requested user_id")

        external_id = f"wallet_refund:{payment_intent_id}:{refund_cents}"

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                status = await self._lock_or_create_internal_event(
                    conn=conn,
                    kind="wallet_refund",
                    external_id=external_id,
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    amount_cents=refund_cents,
                    tokens=tokens_refund,
                    metadata={
                        "payment_intent_id": payment_intent_id,
                        "refund_cents": refund_cents,
                        "refund_usd": refund_usd,
                        "tokens": tokens_refund,
                        "notes": notes,
                        "requested_by": actor or "unknown",
                    },
                )

                if status == "applied":
                    return {
                        "status": "ok",
                        "action": "duplicate",
                        "message": "Refund already applied",
                        "external_id": external_id,
                    }
                if status == "pending":
                    return {
                        "status": "ok",
                        "action": "duplicate",
                        "message": "Refund already pending",
                        "external_id": external_id,
                    }
                if status == "failed":
                    raise ValueError("Previous refund attempt failed; use a new request or investigate")

                await self.user_credits_mgr.refund_lifetime_tokens(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    tokens=tokens_refund,
                    usd_amount=refund_usd,
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
                    await self.user_credits_mgr.restore_lifetime_tokens(
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        tokens=tokens_refund,
                        usd_amount=refund_usd,
                        conn=conn,
                    )
                    await self._mark_internal_event_failed(conn, kind="wallet_refund", external_id=external_id, error=str(e))
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
                await self._update_internal_event_metadata(
                    conn=conn,
                    kind="wallet_refund",
                    external_id=external_id,
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
        stripe = self._stripe()

        sub = None
        if stripe_subscription_id:
            sub = await self.subscription_mgr.get_subscription_by_stripe_id(
                stripe_subscription_id=stripe_subscription_id
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

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                status = await self._lock_or_create_internal_event(
                    conn=conn,
                    kind="subscription_cancel",
                    external_id=external_id,
                    tenant=sub.tenant,
                    project=sub.project,
                    user_id=sub.user_id,
                    amount_cents=None,
                    tokens=None,
                    metadata={
                        "stripe_subscription_id": stripe_sub_id,
                        "notes": notes,
                        "requested_by": actor or "unknown",
                    },
                )
                if status == "applied":
                    return {"status": "ok", "action": "duplicate", "message": "Cancellation already applied"}
                if status == "pending":
                    return {"status": "ok", "action": "duplicate", "message": "Cancellation already pending"}
                if status == "failed":
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
                    await self._mark_internal_event_failed(conn, kind="subscription_cancel", external_id=external_id, error=str(e))
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
                await self._update_internal_event_metadata(
                    conn=conn,
                    kind="subscription_cancel",
                    external_id=external_id,
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
        kind: str = "all",
        limit: int = 200,
    ) -> Dict[str, Any]:
        stripe = self._stripe()
        kind = (kind or "all").lower()
        if kind not in ("all", "wallet_refund", "subscription_cancel"):
            raise ValueError("kind must be all|wallet_refund|subscription_cancel")

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT kind, external_id, tenant, project, user_id, amount_cents, tokens, metadata
                FROM {self.CP}.external_economics_events
                WHERE source='internal' AND status='pending'
                  AND (CASE WHEN $1 = 'all' THEN TRUE ELSE kind = $1 END)
                ORDER BY created_at ASC
                LIMIT $2
            """, kind, int(limit))

        reconciled = 0
        applied = 0
        failed = 0

        for row in rows:
            k = str(row["kind"])
            external_id = str(row["external_id"])
            metadata = row["metadata"] or {}

            if k == "wallet_refund":
                refund_id = metadata.get("stripe_refund_id")
                if not refund_id:
                    continue
                refund = stripe.Refund.retrieve(refund_id)
                status = str(refund.get("status") or "")

                if status in ("succeeded",):
                    async with self.pg_pool.acquire() as conn:
                        async with conn.transaction():
                            await self._mark_internal_event_applied(conn, kind="wallet_refund", external_id=external_id)
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
                            await self.user_credits_mgr.restore_lifetime_tokens(
                                tenant=row["tenant"],
                                project=row["project"],
                                user_id=row["user_id"],
                                tokens=tokens,
                                usd_amount=usd_amount,
                                conn=conn,
                            )
                            await self._mark_internal_event_failed(
                                conn, kind="wallet_refund", external_id=external_id,
                                error=f"stripe refund status={status}"
                            )
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
                            await self._mark_internal_event_failed(
                                conn, kind="subscription_cancel", external_id=external_id,
                                error="stripe subscription not found"
                            )
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
                                stripe_subscription_id=stripe_sub_id,
                                status="canceled",
                                next_charge_at=None,
                                conn=conn,
                            )
                            await self._mark_internal_event_applied(conn, kind="subscription_cancel", external_id=external_id)
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

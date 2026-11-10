# SPDX-License-Identifier: MIT

"""Stripe webhook money effects against a real PostgreSQL.

Drives `StripeEconomicsWebhookHandler` with Stripe event payloads and asserts
what lands in the economics tables: the ledger row, the user's plan row, and
which budget was funded with how much. Stripe API calls are not made (no API
key is configured); the subscription period budget is a recording fake so the
assertion is on what the handler asked it to fund.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import kdcube_ai_app.apps.chat.sdk.infra.economics.stripe as stripe_mod
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager

USER = "user-1"


class _RecordingSubscriptionBudget:
    def __init__(self):
        self.topups = []

    async def topup_subscription_budget(self, **kw):
        self.topups.append(kw)


class _NoopProjectBudget:
    pass


def _handler(db, monkeypatch, *, user_credits_mgr=None):
    monkeypatch.setattr(stripe_mod, "_configured_stripe_secret", lambda key: "")
    budget = _RecordingSubscriptionBudget()
    handler = stripe_mod.StripeEconomicsWebhookHandler(
        pg_pool=db.pool,
        user_credits_mgr=user_credits_mgr,
        subscription_budget_factory=lambda *a: budget,
        project_budget_factory=lambda *a: _NoopProjectBudget(),
        subscription_mgr=SubscriptionManager(db.pool),
        default_tenant=db.tenant,
        default_project=db.project,
        stripe_webhook_secret="whsec_test",
    )

    async def _no_key():
        return None

    handler._ensure_stripe_api_key = _no_key
    return handler, budget


# ---------------------------------------------------------------------------
# invoice.paid: entitlement-only plans
# ---------------------------------------------------------------------------

def _invoice(db, *, plan_id: str, amount_paid: int, invoice_id: str = "in_1"):
    period_start = datetime.now(timezone.utc).replace(microsecond=0)
    period_end = period_start + timedelta(days=30)
    return {
        "id": invoice_id,
        "object": "invoice",
        "currency": "usd",
        "amount_paid": amount_paid,
        "customer": "cus_1",
        "billing_reason": "subscription_cycle",
        "metadata": {
            "tenant": db.tenant, "project": db.project, "user_id": USER,
            "plan_id": plan_id, "kdcube_invoice_kind": "subscription",
        },
        "parent": {"subscription_details": {"subscription": "sub_1"}},
        "lines": {"data": [{
            "metadata": {},
            "period": {"start": int(period_start.timestamp()), "end": int(period_end.timestamp())},
        }]},
    }


async def _seed_plan(db, plan_id: str, *, price_cents: int, metadata=None):
    await SubscriptionManager(db.pool).upsert_plan(
        tenant=db.tenant, project=db.project, plan_id=plan_id, provider="stripe",
        stripe_price_id=f"price_{plan_id}", monthly_price_cents=price_cents, metadata=metadata,
    )


async def _invoice_paid(handler, invoice):
    event = {"id": "evt_inv", "type": "invoice.paid", "data": {"object": invoice}}
    return await handler.process_event(event)


async def test_invoice_paid_funds_the_plan_budget_for_an_ordinary_plan(econ_pg, monkeypatch):
    db = econ_pg
    await _seed_plan(db, "pro", price_cents=2000)
    handler, budget = _handler(db, monkeypatch)
    res = await _invoice_paid(handler, _invoice(db, plan_id="pro", amount_paid=2000))
    assert res.action == "applied"
    assert [t["usd_amount"] for t in budget.topups] == [20.0]


async def test_invoice_paid_records_but_funds_nothing_for_an_entitlement_only_plan(econ_pg, monkeypatch):
    db = econ_pg
    await _seed_plan(db, "access", price_cents=199, metadata={"entitlement_only": True})
    handler, budget = _handler(db, monkeypatch)
    invoice = _invoice(db, plan_id="access", amount_paid=238)  # 199 + tax
    res = await _invoice_paid(handler, invoice)

    assert res.action == "applied"
    assert "entitlement-only" in res.message
    assert budget.topups == []
    rows = await db.pool.fetch(f"SELECT * FROM {db.events_table} WHERE kind='subscription_topup'")
    assert len(rows) == 1
    assert rows[0]["status"] == "applied" and rows[0]["amount_cents"] == 238
    sub = await SubscriptionManager(db.pool).get_subscription(
        tenant=db.tenant, project=db.project, user_id=USER)
    assert (sub.plan_id, sub.provider, sub.status) == ("access", "stripe", "active")

    # a redelivery stays idempotent
    again = await _invoice_paid(handler, invoice)
    assert again.action == "duplicate" and budget.topups == []

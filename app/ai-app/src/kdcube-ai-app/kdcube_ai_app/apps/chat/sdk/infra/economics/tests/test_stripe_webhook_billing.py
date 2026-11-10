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


# ---------------------------------------------------------------------------
# payment_intent.succeeded: wallet top-ups credit the pre-tax price
# ---------------------------------------------------------------------------

NET_CENTS = 500     # a 5 USD pack
GROSS_CENTS = 595   # collected with 19% tax on top


def _credits_manager(db):
    from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import UserCreditsManager

    return UserCreditsManager(pg_pool=db.pool)


def _payment_intent(db, *, received=GROSS_CENTS, net=str(NET_CENTS), pi_id="pi_topup_1"):
    meta = {"tenant": db.tenant, "project": db.project, "user_id": USER,
            "kdcube_invoice_kind": "wallet_topup"}
    if net is not None:
        meta[stripe_mod.TOPUP_NET_AMOUNT_METADATA_KEY] = net
    return {"id": pi_id, "object": "payment_intent", "currency": "usd",
            "amount": received, "amount_received": received, "metadata": meta}


async def _pi_succeeded(handler, pi):
    event = {"id": "evt_pi", "type": "payment_intent.succeeded", "data": {"object": pi}}
    return await handler.process_event(event)


async def _purchased_cents(db):
    return await db.pool.fetchval(
        f"SELECT purchased_cents FROM {db.schema}.user_lifetime_credits WHERE user_id=$1", USER)


async def test_taxed_topup_credits_the_pre_tax_price_and_books_the_tax_beside_it(econ_pg, monkeypatch):
    db = econ_pg
    handler, _ = _handler(db, monkeypatch, user_credits_mgr=_credits_manager(db))
    res = await _pi_succeeded(handler, _payment_intent(db))
    assert res.action == "applied"
    assert await _purchased_cents(db) == NET_CENTS
    row = await db.event_row("stripe", "user_credits", "pi_topup_1")
    assert row["amount_cents"] == NET_CENTS
    assert row["metadata"]["kdcube_gross_amount_cents"] == str(GROSS_CENTS)
    assert row["metadata"]["kdcube_tax_cents"] == str(GROSS_CENTS - NET_CENTS)


async def test_untaxed_topup_is_credited_as_received(econ_pg, monkeypatch):
    db = econ_pg
    handler, _ = _handler(db, monkeypatch, user_credits_mgr=_credits_manager(db))
    await _pi_succeeded(handler, _payment_intent(db, received=NET_CENTS))
    assert await _purchased_cents(db) == NET_CENTS
    row = await db.event_row("stripe", "user_credits", "pi_topup_1")
    assert "kdcube_tax_cents" not in row["metadata"]


async def test_topup_without_a_net_figure_credits_the_amount_received(econ_pg, monkeypatch):
    """A PaymentIntent created before checkout stamped the net figure."""
    db = econ_pg
    handler, _ = _handler(db, monkeypatch, user_credits_mgr=_credits_manager(db))
    await _pi_succeeded(handler, _payment_intent(db, net=None))
    assert await _purchased_cents(db) == GROSS_CENTS


async def test_net_larger_than_the_amount_received_credits_the_amount_received(econ_pg, monkeypatch):
    db = econ_pg
    handler, _ = _handler(db, monkeypatch, user_credits_mgr=_credits_manager(db))
    await _pi_succeeded(handler, _payment_intent(db, received=400, net="500"))
    assert await _purchased_cents(db) == 400


@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "-5", None])
def test_net_topup_cents_falls_back_to_the_amount_received(raw):
    meta = {} if raw is None else {stripe_mod.TOPUP_NET_AMOUNT_METADATA_KEY: raw}
    assert stripe_mod.net_topup_cents(meta, GROSS_CENTS) == GROSS_CENTS


def test_net_topup_cents_honours_a_usable_net():
    key = stripe_mod.TOPUP_NET_AMOUNT_METADATA_KEY
    assert stripe_mod.net_topup_cents({key: "500"}, 595) == 500
    assert stripe_mod.net_topup_cents({key: " 500 "}, 595) == 500
    assert stripe_mod.net_topup_cents({key: "595"}, 595) == 595

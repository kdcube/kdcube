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


# ---------------------------------------------------------------------------
# Stripe Customers carry an opaque reference, not the platform user id
# ---------------------------------------------------------------------------

SECRET = "test-platform-secret"


def _with_secret(monkeypatch, value):
    async def _get_secret(key, default=None):
        assert key == "platform.services.session_token.secret"
        return value

    monkeypatch.setattr(stripe_mod, "get_secret", _get_secret)


async def test_customer_fields_carry_no_user_id(monkeypatch):
    _with_secret(monkeypatch, SECRET)
    user_id = "internal:idp:alice@example.com"
    fields = await stripe_mod.stripe_customer_fields("t", "p", user_id)
    ref = fields["metadata"][stripe_mod.CUSTOMER_REF_METADATA_KEY]
    assert fields["metadata"] == {"tenant": "t", "project": "p", stripe_mod.CUSTOMER_REF_METADATA_KEY: ref}
    assert user_id not in fields["description"] and "alice" not in repr(fields)
    assert ref == stripe_mod.stripe_customer_ref(SECRET, user_id)
    assert ref == (await stripe_mod.stripe_customer_fields("t", "p", user_id))["metadata"][
        stripe_mod.CUSTOMER_REF_METADATA_KEY]
    assert ref != stripe_mod.stripe_customer_ref(SECRET, "someone-else")
    assert ref != stripe_mod.stripe_customer_ref("another-secret", user_id)


async def test_customer_ref_without_the_secret_is_random(monkeypatch):
    _with_secret(monkeypatch, None)
    a = await stripe_mod.stripe_customer_fields("t", "p", USER)
    b = await stripe_mod.stripe_customer_fields("t", "p", USER)
    ref_a = a["metadata"][stripe_mod.CUSTOMER_REF_METADATA_KEY]
    assert ref_a.startswith("cr_") and ref_a != b["metadata"][stripe_mod.CUSTOMER_REF_METADATA_KEY]
    assert USER not in repr(a)


async def test_checkout_customer_is_created_opaque_and_mapped_in_user_plans(econ_pg, monkeypatch):
    import types

    from kdcube_ai_app.apps.chat.ingress.economics import stripe_router

    db = econ_pg
    _with_secret(monkeypatch, SECRET)
    created = []

    class _Customer:
        @staticmethod
        def create(**kw):
            created.append(kw)
            return types.SimpleNamespace(id="cus_new")

    monkeypatch.setattr(stripe_router.router, "state", types.SimpleNamespace(pg_pool=db.pool), raising=False)
    mgr = types.SimpleNamespace(subscription_mgr=SubscriptionManager(db.pool))
    customer_id = await stripe_router._resolve_stripe_customer(
        mgr, types.SimpleNamespace(Customer=_Customer), db.tenant, db.project, USER)

    assert customer_id == "cus_new"
    assert "user_id" not in created[0]["metadata"] and USER not in created[0]["description"]
    mapped = await db.pool.fetchval(
        f"SELECT user_id FROM {db.schema}.user_plans WHERE stripe_customer_id='cus_new'")
    assert mapped == USER


def _invoice_without_user(db, *, plan_id="pro", invoice_id="in_opaque"):
    invoice = _invoice(db, plan_id=plan_id, amount_paid=2000, invoice_id=invoice_id)
    invoice["customer"] = "cus_opaque"
    del invoice["metadata"]["user_id"]
    return invoice


async def test_invoice_paid_resolves_an_opaque_customer_through_user_plans(econ_pg, monkeypatch):
    db = econ_pg
    await _seed_plan(db, "pro", price_cents=2000)
    await db.pool.execute(
        f"INSERT INTO {db.schema}.user_plans (tenant, project, user_id, status, provider, stripe_customer_id)"
        " VALUES ($1,$2,$3,'active','internal','cus_opaque')",
        db.tenant, db.project, USER)
    handler, budget = _handler(db, monkeypatch)
    res = await _invoice_paid(handler, _invoice_without_user(db))
    assert res.action == "applied" and res.user_id == USER
    assert [t["usd_amount"] for t in budget.topups] == [20.0]


async def test_invoice_paid_prefers_a_legacy_customers_metadata(econ_pg, monkeypatch):
    """A customer created before opaque references still names its user in its
    own metadata; that wins, and user_plans is not consulted."""
    db = econ_pg
    await _seed_plan(db, "pro", price_cents=2000)
    handler, budget = _handler(db, monkeypatch)
    handler._fetch_customer_meta = lambda customer_id: {"user_id": "legacy-user"}

    async def _no_lookup(*a, **kw):
        raise AssertionError("user_plans must not be consulted when metadata names the user")

    handler._user_for_stripe_customer = _no_lookup
    res = await _invoice_paid(handler, _invoice_without_user(db))
    assert res.action == "applied" and res.user_id == "legacy-user"


async def test_invoice_paid_for_an_unknown_customer_is_ignored(econ_pg, monkeypatch):
    db = econ_pg
    handler, budget = _handler(db, monkeypatch)
    res = await _invoice_paid(handler, _invoice_without_user(db))
    assert res.action == "ignored" and budget.topups == []

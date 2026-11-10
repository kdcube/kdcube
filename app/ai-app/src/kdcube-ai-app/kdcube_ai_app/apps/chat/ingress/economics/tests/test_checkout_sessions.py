# SPDX-License-Identifier: MIT

"""Stripe Checkout Session parameters built by the economics checkout routes.

The routes are called directly with a recording Stripe client; the assertion is
on the keyword arguments sent to `checkout.Session.create`.
"""

from __future__ import annotations

import types

import pytest

from kdcube_ai_app.apps.chat.ingress.economics import checkout
from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import TOPUP_NET_AMOUNT_METADATA_KEY

TAX_KEYS = ("automatic_tax", "billing_address_collection", "customer_update", "tax_id_collection")


class _RecordingStripe:
    def __init__(self):
        self.sessions = []
        recorder = self

        class _Session:
            @staticmethod
            def create(**kw):
                recorder.sessions.append(kw)
                return types.SimpleNamespace(id="cs_1", url="https://checkout.example/cs_1")

        self.checkout = types.SimpleNamespace(Session=_Session)


class _SubMgr:
    def __init__(self, plan):
        self.plan = plan

    async def get_plan(self, **kw):
        return self.plan

    async def get_subscription(self, **kw):
        return None

    def assert_plan_change_allowed(self, **kw):
        return None


@pytest.fixture
def stripe_client(monkeypatch):
    client = _RecordingStripe()
    plan = types.SimpleNamespace(plan_id="pro", stripe_price_id="price_pro")
    mgr = types.SimpleNamespace(subscription_mgr=_SubMgr(plan))

    async def _get_stripe():
        return client

    async def _resolve_customer(*a, **kw):
        return "cus_1"

    monkeypatch.setattr(checkout, "get_settings", lambda: types.SimpleNamespace(TENANT="t", PROJECT="p"))
    monkeypatch.setattr(checkout, "_get_control_plane_manager", lambda router: mgr)
    monkeypatch.setattr(checkout, "_get_stripe", _get_stripe)
    monkeypatch.setattr(checkout, "_resolve_stripe_customer", _resolve_customer)
    return client


@pytest.fixture
def economics_yaml(tmp_path, monkeypatch):
    path = tmp_path / "economics.yaml"
    monkeypatch.setenv("ECONOMICS_YAML_DESCRIPTOR_PATH", str(path))

    def _write(text: str):
        path.write_text(text, encoding="utf-8")

    _write("version: 1\n")
    return _write


_SESSION = types.SimpleNamespace(user_id="user-1")


async def _topup(amount_usd=5.0):
    payload = checkout.CreateCheckoutTopupRequest(
        amount_usd=amount_usd, success_url="https://app.example/ok", cancel_url="https://app.example/no")
    return await checkout.create_checkout_topup(payload, session=_SESSION)


async def _subscription():
    payload = checkout.CreateCheckoutSubscriptionRequest(
        plan_id="pro", success_url="https://app.example/ok", cancel_url="https://app.example/no")
    return await checkout.create_checkout_subscription(payload, session=_SESSION)


async def test_tax_is_off_by_default(stripe_client, economics_yaml):
    await _topup()
    await _subscription()
    for kw in stripe_client.sessions:
        assert not set(TAX_KEYS) & set(kw)
    assert "tax_behavior" not in stripe_client.sessions[0]["line_items"][0]["price_data"]


async def test_automatic_tax_on_both_sessions(stripe_client, economics_yaml):
    economics_yaml("stripe:\n  checkout:\n    automatic_tax: true\n")
    await _topup()
    await _subscription()
    for kw in stripe_client.sessions:
        assert kw["automatic_tax"] == {"enabled": True}
        assert kw["billing_address_collection"] == "required"
        assert kw["customer_update"] == {"address": "auto"}
        assert "tax_id_collection" not in kw
    topup, subscription = stripe_client.sessions
    assert topup["line_items"][0]["price_data"]["tax_behavior"] == "exclusive"
    assert subscription["line_items"] == [{"price": "price_pro", "quantity": 1}]


async def test_tax_id_collection(stripe_client, economics_yaml):
    economics_yaml("stripe:\n  checkout:\n    automatic_tax: true\n    tax_id_collection: true\n")
    await _topup()
    kw = stripe_client.sessions[0]
    assert kw["tax_id_collection"] == {"enabled": True}
    assert kw["customer_update"] == {"address": "auto", "name": "auto"}


async def test_topup_stamps_the_charged_pre_tax_price_onto_the_payment_intent(stripe_client, economics_yaml):
    economics_yaml("stripe:\n  checkout:\n    automatic_tax: true\n")
    await _topup(amount_usd=5.0)
    kw = stripe_client.sessions[0]
    unit_amount = kw["line_items"][0]["price_data"]["unit_amount"]
    assert unit_amount == 500
    assert kw["payment_intent_data"]["metadata"][TOPUP_NET_AMOUNT_METADATA_KEY] == str(unit_amount)
    assert kw["metadata"][TOPUP_NET_AMOUNT_METADATA_KEY] == str(unit_amount)


async def test_descriptor_write_back_preserves_the_stripe_section():
    from kdcube_ai_app.apps.chat.sdk.infra.economics.descriptor import build_economics_descriptor

    class _CP:
        class subscription_mgr:
            @staticmethod
            async def list_plans(**kw):
                return []

        async def list_plan_quota_policies(self, **kw):
            return []

        async def list_tenant_project_budget_policies(self, **kw):
            return []

    stripe_section = {"checkout": {"automatic_tax": True, "tax_id_collection": True}}
    payload = await build_economics_descriptor(
        _CP(), tenant="t", project="p", existing={"stripe": stripe_section})
    assert payload["stripe"] == stripe_section

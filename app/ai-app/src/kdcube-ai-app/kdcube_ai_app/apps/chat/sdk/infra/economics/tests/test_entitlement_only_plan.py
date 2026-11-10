# SPDX-License-Identifier: MIT

"""The `entitlement_only` plan property.

An active external subscription funds its own per-period plan budget from the
paid invoice. A plan declared `entitlement_only` sells access instead: the
subscription is active (the plan's quota applies) but it funds no plan budget,
so turns are funded like an internal plan's.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics import descriptor as descriptor_mod
from kdcube_ai_app.apps.chat.sdk.infra.economics.plan_resolution import (
    plan_is_entitlement_only,
    plan_metadata_with_entitlement_only,
    subscription_needs_plan_lookup,
    subscription_provides_plan_budget,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionPlan

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _sub(*, provider="stripe", active=True, plan_id="access"):
    return types.SimpleNamespace(
        status="active" if active else "canceled",
        monthly_price_cents=199,
        next_charge_at=None,
        provider=provider,
        plan_id=plan_id,
    )


def _plan(metadata):
    return types.SimpleNamespace(metadata=metadata)


def test_external_subscription_without_a_plan_funds_a_plan_budget():
    assert subscription_provides_plan_budget(_sub(), NOW) is True


def test_ordinary_plan_funds_a_plan_budget():
    assert subscription_provides_plan_budget(_sub(), NOW, plan=_plan({"tier": "pro"})) is True


def test_entitlement_only_plan_funds_no_plan_budget():
    assert subscription_provides_plan_budget(_sub(), NOW, plan=_plan({"entitlement_only": True})) is False


@pytest.mark.parametrize("metadata, expected", [
    ('{"entitlement_only": true}', True),     # jsonb delivered as text
    (b'{"entitlement_only": true}', True),
    ({"entitlement_only": False}, False),
    ("not json", False),
    (["entitlement_only"], False),
    (None, False),
])
def test_entitlement_only_metadata_shapes(metadata, expected):
    assert plan_is_entitlement_only(_plan(metadata)) is expected
    assert plan_is_entitlement_only({"metadata": metadata}) is expected


def test_internal_and_inactive_subscriptions_fund_nothing_whatever_the_plan():
    assert subscription_provides_plan_budget(_sub(provider="internal"), NOW, plan=_plan({})) is False
    assert subscription_provides_plan_budget(_sub(active=False), NOW, plan=_plan({})) is False


def test_plan_lookup_is_needed_only_for_active_external_subscriptions_with_a_plan():
    assert subscription_needs_plan_lookup(_sub(), NOW) is True
    assert subscription_needs_plan_lookup(_sub(provider="internal"), NOW) is False
    assert subscription_needs_plan_lookup(_sub(active=False), NOW) is False
    assert subscription_needs_plan_lookup(_sub(plan_id=None), NOW) is False
    assert subscription_needs_plan_lookup(None, NOW) is False


def test_metadata_folding():
    assert plan_metadata_with_entitlement_only({"a": 1}, None) == {"a": 1}
    assert plan_metadata_with_entitlement_only(None, None) is None
    assert plan_metadata_with_entitlement_only({"a": 1}, True) == {"a": 1, "entitlement_only": True}
    assert plan_metadata_with_entitlement_only({"a": 1, "entitlement_only": True}, False) == {"a": 1}
    assert plan_metadata_with_entitlement_only(None, False) == {}


def test_subscription_plan_exposes_the_property():
    now = datetime.now(timezone.utc)
    common = dict(tenant="t", project="p", plan_id="access", provider="stripe",
                  stripe_price_id="price_1", monthly_price_cents=199, active=True,
                  created_at=now, updated_at=now, created_by=None, notes=None)
    assert SubscriptionPlan(metadata={"entitlement_only": True}, **common).entitlement_only is True
    assert SubscriptionPlan(metadata=None, **common).entitlement_only is False


# ---------------------------------------------------------------------------
# economics.yaml: seeded and written back as a first-class plan property
# ---------------------------------------------------------------------------

class _RecordingMgr:
    def __init__(self):
        self.rows = []

    def execute_sql(self, sql, data=None, **kw):
        self.rows.append((sql, data))


def test_seed_folds_entitlement_only_into_plan_metadata(tmp_path):
    from kdcube_ai_app.ops.deployment.economics import economics_seed

    path = tmp_path / "economics.yaml"
    path.write_text(
        "plans:\n"
        "  access: { provider: stripe, stripe_price_id: price_a, monthly_price_cents: 199,"
        " entitlement_only: true, metadata: { tier: basic } }\n"
        "  pro: { provider: stripe, stripe_price_id: price_p, monthly_price_cents: 2000 }\n",
        encoding="utf-8",
    )
    mgr = _RecordingMgr()
    economics_seed.seed_economics("t", "p", mgr=mgr, path=str(path))

    plan_rows = {}
    for sql, data in mgr.rows:
        if ".plans (" not in sql:
            continue
        cols = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
        row = dict(zip(cols, data))
        plan_rows[row["plan_id"]] = row
    assert plan_rows["access"]["metadata"].adapted == {"tier": "basic", "entitlement_only": True}
    assert plan_rows["pro"]["metadata"] is None
    assert "entitlement_only" not in plan_rows["access"]


async def test_descriptor_write_back_keeps_entitlement_only_and_metadata():
    now = datetime.now(timezone.utc)

    def plan(plan_id, metadata):
        return SubscriptionPlan(tenant="t", project="p", plan_id=plan_id, provider="stripe",
                                stripe_price_id=f"price_{plan_id}", monthly_price_cents=199,
                                active=True, metadata=metadata, created_at=now, updated_at=now,
                                created_by=None, notes=None)

    class _SubMgr:
        async def list_plans(self, **kw):
            return [plan("access", {"entitlement_only": True, "tier": "basic"}), plan("pro", None)]

    class _CP:
        subscription_mgr = _SubMgr()

        async def list_plan_quota_policies(self, **kw):
            return []

        async def list_tenant_project_budget_policies(self, **kw):
            return []

    payload = await descriptor_mod.build_economics_descriptor(_CP(), tenant="t", project="p", existing={})
    assert payload["plans"]["access"]["entitlement_only"] is True
    assert payload["plans"]["access"]["metadata"] == {"tier": "basic"}
    assert "entitlement_only" not in payload["plans"]["pro"]
    assert "metadata" not in payload["plans"]["pro"]

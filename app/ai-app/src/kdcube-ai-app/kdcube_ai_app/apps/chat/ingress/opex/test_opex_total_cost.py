# SPDX-License-Identifier: MIT
"""`/api/opex/total` and `/api/opex/conversation` report the priced cost in
`total.cost_usd` and `rollup[].spent.cost_usd`.

Accounting events carry tokens, not dollars, so the aggregated `cost_usd`
fields held only provider-reported cost: $0.00 for a window that cost real
money, while the same response's `cost_estimate` had the right figure."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.ingress.opex import opex
from kdcube_ai_app.infra.accounting import pricing

TABLE = {
    "llm": [{"provider": "anthropic", "model": "m1", "input_tokens_1M": 1.0, "output_tokens_1M": 5.0}],
    "embedding": [{"provider": "openai", "model": "e1", "tokens_1M": 0.02}],
    "web_search": [],
}

USAGE = {
    "total": {"input_tokens": 2_000_000, "output_tokens": 1_000_000, "cost_usd": 0.0},
    "rollup": [
        {"service": "llm", "provider": "anthropic", "model": "m1",
         "spent": {"input": 1_000_000, "output": 1_000_000, "cost_usd": 0.0}},
        {"service": "embedding", "provider": "openai", "model": "e1",
         "spent": {"tokens": 1_000_000, "cost_usd": 0.0}},
    ],
    "user_count": 1,
    "event_count": 2,
    "turns": {},
}


class _FakeCalc:
    def __init__(self):
        self.result = copy.deepcopy(USAGE)

    async def usage_all_users(self, **_kw):
        return self.result

    async def usage_user_conversation(self, **_kw):
        return self.result


@pytest.fixture
def calc(monkeypatch):
    monkeypatch.setattr(pricing, "price_table", lambda: TABLE)
    monkeypatch.setattr(
        opex, "_compute_cost_estimate",
        lambda rollup: pricing.compute_cost_estimate(rollup, services_config={}),
    )
    return _FakeCalc()


def _request(calc):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(accounting_calculator=calc)))


def _assert_priced(resp, calc):
    assert resp["cost_estimate"]["total_cost_usd"] == pytest.approx(6.02)
    assert resp["total"]["cost_usd"] == pytest.approx(6.02)
    assert [r["spent"]["cost_usd"] for r in resp["rollup"]] == pytest.approx([6.0, 0.02])
    # the aggregate the calculator returned (possibly cached) is not rewritten
    assert calc.result == USAGE


async def test_total_reports_priced_cost(calc):
    resp = await opex.get_total_usage(
        _request(calc), tenant="t", project="p", date_from="2026-01-01", date_to="2026-01-01",
        app_bundle_id=None, service_types=None, hard_file_limit=None, session=None,
    )
    _assert_priced(resp, calc)


async def test_conversation_reports_priced_cost(calc):
    resp = await opex.get_conversation_usage(
        _request(calc), tenant="t", project="p", user_id="u", conversation_id="c",
        date_from=None, date_to=None, app_bundle_id=None, service_types=None,
        hard_file_limit=None, session=None,
    )
    _assert_priced(resp, calc)


def test_length_mismatch_leaves_lines_unpriced():
    total, rollup = pricing.apply_priced_cost(
        {"cost_usd": 0.0}, [{"spent": {"cost_usd": 0.1}}],
        {"total_cost_usd": 1.0, "breakdown": []},
    )
    assert total["cost_usd"] == 1.0
    assert rollup[0]["spent"]["cost_usd"] == 0.1


def test_no_estimate_keeps_response():
    total, rollup = pricing.apply_priced_cost({"cost_usd": 0.0}, [], None)
    assert total == {"cost_usd": 0.0} and rollup == []

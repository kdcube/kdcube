# SPDX-License-Identifier: MIT
"""One pricing operation for settlement and reporting.

Turn settlement (`RateCalculator.calculate_turn_costs`), per-agent settlement
(`_calculate_agent_costs`) and reporting (`compute_cost_estimate`) all price
through `pricing.price_rollup_line`. These tests price one synthetic rollup
through every surface and require the same answer, and pin the lookup rule
(case-insensitive, aliases honoured) and the missing-price signal."""

from __future__ import annotations

import logging

import pytest

from kdcube_ai_app.infra.accounting import calculator, pricing, usage
from kdcube_ai_app.infra.accounting.calculator import RateCalculator, _calculate_agent_costs

TABLE = {
    "llm": [
        {
            "provider": "anthropic", "model": "claude-x-20250101", "aliases": ["claude-x"],
            "input_tokens_1M": 1.0, "output_tokens_1M": 5.0,
            "cache_pricing": {"5m": {"write_tokens_1M": 1.25, "read_tokens_1M": 0.1},
                              "1h": {"write_tokens_1M": 2.0, "read_tokens_1M": 0.1}},
            "cache_write_tokens_1M": 1.25, "cache_read_tokens_1M": 0.1,
        },
        {
            "provider": "openai", "model": "gpt-y",
            "input_tokens_1M": 0.2, "output_tokens_1M": 1.0,
            "cache_write_tokens_1M": 0.0, "cache_read_tokens_1M": 0.02,
        },
        {
            "provider": "google", "model": "gem-think",
            "input_tokens_1M": 0.15, "output_tokens_1M": 0.6, "thinking_output_tokens_1M": 3.5,
        },
    ],
    "embedding": [{"provider": "openai", "model": "emb-1", "aliases": ["emb-one"], "tokens_1M": 0.02}],
    "web_search": [{"provider": "brave", "tier": "base", "cost_per_1k_requests": 3.0}],
}
SERVICES = {"web_search": {"brave": {"tier": "base"}}}

M = 1_000_000

ROLLUP = [
    # split 5m/1h cache writes, cache reads; model spelled by alias, mixed case
    {"service": "llm", "provider": "Anthropic", "model": "CLAUDE-X",
     "spent": {"input": M, "output": M, "cache_5m_write": M, "cache_1h_write": M, "cache_read": M}},
    # single-bucket cache write
    {"service": "llm", "provider": "openai", "model": "gpt-y",
     "spent": {"input": M, "output": M, "cache_creation": M, "cache_read": M}},
    # thinking tokens are part of output, billed at the thinking rate
    {"service": "llm", "provider": "google", "model": "gem-think",
     "spent": {"input": M, "output": 2 * M, "thinking": M}},
    {"service": "embedding", "provider": "OpenAI", "model": "emb-one", "spent": {"tokens": M}},
    {"service": "web_search", "provider": "brave", "spent": {"search_queries": 1000}},
    # no price row: provider-reported cost is the ground truth, every service
    {"service": "llm", "provider": "selfreport", "model": "r1", "spent": {"output": 10, "cost_usd": 0.5}},
    {"service": "embedding", "provider": "selfreport", "model": "e1", "spent": {"tokens": 10, "cost_usd": 0.25}},
    {"service": "web_search", "provider": "selfreport", "spent": {"search_queries": 1, "cost_usd": 0.125}},
]

EXPECTED_LINES = [
    1.0 + 5.0 + 1.25 + 2.0 + 0.1,
    0.2 + 1.0 + 0.0 + 0.02,
    0.15 + 0.6 + 3.5,
    0.02,
    3.0,
    0.5,
    0.25,
    0.125,
]
EXPECTED_TOTAL = sum(EXPECTED_LINES)


@pytest.fixture(autouse=True)
def _fixed_table(monkeypatch):
    monkeypatch.setattr(pricing, "price_table", lambda: TABLE)
    monkeypatch.setattr(usage, "price_table", lambda: TABLE)
    monkeypatch.setattr(calculator, "price_table", lambda: TABLE)
    pricing._MISSING_PRICE_WARNED.clear()


def test_reporting_prices_every_bucket():
    est = pricing.compute_cost_estimate(ROLLUP, services_config=SERVICES)
    assert [b["cost_usd"] for b in est["breakdown"]] == pytest.approx(EXPECTED_LINES)
    assert est["total_cost_usd"] == pytest.approx(EXPECTED_TOTAL)
    sources = [b["pricing_source"] for b in est["breakdown"]]
    assert sources == ["price_table"] * 5 + ["reported_usage_cost"] * 3


def test_per_agent_settlement_matches_reporting():
    costs = _calculate_agent_costs({"a": ROLLUP[:4], "b": ROLLUP[4:]},
                                   pricing_table=TABLE, accounting_services_config=SERVICES)
    assert costs["a"]["total_cost_usd"] + costs["b"]["total_cost_usd"] == pytest.approx(EXPECTED_TOTAL)
    assert costs["a"]["tokens"]["thinking"] == M


async def test_turn_settlement_matches_reporting():
    calc = RateCalculator.__new__(RateCalculator)

    async def _rollup(**_kw):
        return [dict(i, spent=dict(i["spent"])) for i in ROLLUP]

    async def _by_agent(**_kw):
        return {"agent": [dict(i, spent=dict(i["spent"])) for i in ROLLUP]}

    calc.turn_usage_rollup_compact = _rollup
    calc.turn_usage_by_agent = _by_agent
    res = await calc.calculate_turn_costs(
        tenant_id="t", project_id="p", conversation_id="c", turn_id="turn",
        accounting_services_config=SERVICES,
        ref_provider="anthropic", ref_model="claude-x-20250101",
    )
    est = pricing.compute_cost_estimate(ROLLUP, services_config=SERVICES)
    assert res["cost_total_usd"] == pytest.approx(est["total_cost_usd"])
    assert res["agent_costs"]["agent"]["total_cost_usd"] == pytest.approx(est["total_cost_usd"])
    assert [b["cost_usd"] for b in res["cost_breakdown"]] == pytest.approx(EXPECTED_LINES)
    # the settlement detail shape is kept
    first = res["cost_breakdown"][0]
    assert first["cache_5m_write_cost_usd"] == pytest.approx(1.25)
    assert first["cache_1h_write_cost_usd"] == pytest.approx(2.0)
    assert res["cost_breakdown"][2]["thinking_cost_usd"] == pytest.approx(3.5)
    assert res["cost_breakdown"][5]["pricing_source"] == "reported_usage_cost"


def test_lookup_is_case_insensitive_and_honours_aliases():
    assert usage._find_llm_price("ANTHROPIC", "Claude-X", TABLE)["model"] == "claude-x-20250101"
    assert usage._find_emb_price("openai", "EMB-ONE", TABLE)["model"] == "emb-1"
    assert usage._find_web_search_price("Brave", "BASE", TABLE)["tier"] == "base"


def test_alias_spelling_is_priced_in_reports():
    """A usage row stored under a model alias used to price at $0 in reporting
    while settlement priced it correctly."""
    line = {"service": "llm", "provider": "anthropic", "model": "claude-x", "spent": {"input": M}}
    assert pricing.compute_cost_estimate([line], services_config={})["total_cost_usd"] == pytest.approx(1.0)


def test_missing_price_is_flagged_and_logged_once(caplog):
    line = {"service": "llm", "provider": "openai", "model": "not-in-table", "spent": {"input": M, "output": M}}
    with caplog.at_level(logging.WARNING, logger=pricing.__name__):
        est = pricing.compute_cost_estimate([line, line], services_config={})
    assert est["total_cost_usd"] == 0.0
    assert [b["pricing_source"] for b in est["breakdown"]] == ["missing_price", "missing_price"]
    warnings = [r for r in caplog.records if "not-in-table" in r.getMessage()]
    assert len(warnings) == 1


def test_missing_price_marks_settlement_detail():
    line = {"service": "embedding", "provider": "openai", "model": "nope", "spent": {"tokens": 5}}
    priced = pricing.price_rollup_line(line, pricing_table=TABLE, services_config={})
    assert priced["cost_usd"] == 0.0
    assert priced["details"]["pricing_source"] == "missing_price"

# SPDX-License-Identifier: MIT
"""Price usage rollup lines against the platform price table.

Every surface that turns usage into dollars prices through `price_rollup_line`:
turn settlement (`RateCalculator.calculate_turn_costs`), per-agent settlement
(`_calculate_agent_costs`), the opex endpoints, the spend-rollup writer and the
user budget display (both through `compute_cost_estimate`). One function means
what a user is charged and what a report shows cannot drift apart.

Price rows are resolved with the finders in `usage.py` (case-insensitive,
honouring each row's `aliases`), against the effective `price_table()`.

Resolution order for one line:
  1. a price-table row wins (`pricing_source = "price_table"`);
  2. otherwise a provider-reported `spent.cost_usd` is the ground truth
     (`"reported_usage_cost"`) -- runtimes that self-report spend;
  3. otherwise the line is unpriced (`"missing_price"`): it costs $0 as before,
     and the gap is logged once per (service, provider, model) per process so
     usage of a model absent from the table is observable instead of silently
     free.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.accounting.usage import (
    _find_emb_price,
    _find_llm_price,
    _find_web_search_price,
    get_web_search_tier,
    price_table,
)

logger = logging.getLogger(__name__)

PRICED = "price_table"
REPORTED = "reported_usage_cost"
MISSING = "missing_price"

_PRICED_SERVICES = ("llm", "embedding", "web_search")
_MISSING_PRICE_WARNED: set = set()
_MISSING_PRICE_WARN_LIMIT = 1024


def load_services_config() -> Dict[str, Any]:
    """The parsed ACCOUNTING_SERVICES setting (web-search tiers); {} when unset."""
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        raw = get_settings().PLATFORM.ACCOUNTING.ACCOUNTING_SERVICES or "{}"
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _per_1m(tokens: Any, rate: Any) -> float:
    return (float(tokens or 0) / 1_000_000.0) * float(rate or 0.0)


def _warn_missing_price(service: Any, provider: Any, model: Any) -> None:
    key = (service, provider, model)
    if key in _MISSING_PRICE_WARNED:
        return
    if len(_MISSING_PRICE_WARNED) < _MISSING_PRICE_WARN_LIMIT:
        _MISSING_PRICE_WARNED.add(key)
    logger.warning(
        "[pricing] no price-table row for %s %s/%s and no provider-reported cost; "
        "this usage is booked at $0.00. Add a row to the price table "
        "(or the economics descriptor's price_tables section).",
        service, provider, model,
    )


def _price_llm(spent: Dict[str, Any], pr: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
    """Cost and settlement detail of one llm line priced by row `pr`.

    `spent.output` is the TOTAL output; `spent.thinking` is the part of it
    billed at `thinking_output_tokens_1M` (default: the output rate)."""
    input_tokens = spent.get("input", 0) or 0
    output_tokens = spent.get("output", 0) or 0
    thinking_tokens = spent.get("thinking", 0) or 0
    cache_read_tokens = spent.get("cache_read", 0) or 0

    out_price = float(pr.get("output_tokens_1M", 0.0) or 0.0)
    thinking_price = float(pr.get("thinking_output_tokens_1M", out_price) or out_price)
    non_thinking_out = max(float(output_tokens) - float(thinking_tokens), 0.0)

    input_cost = _per_1m(input_tokens, pr.get("input_tokens_1M"))
    output_cost = _per_1m(non_thinking_out, out_price)
    thinking_cost = _per_1m(thinking_tokens, thinking_price)
    cache_read_cost = _per_1m(cache_read_tokens, pr.get("cache_read_tokens_1M"))

    cache_pricing = pr.get("cache_pricing")
    if isinstance(cache_pricing, dict) and cache_pricing:
        w5m = spent.get("cache_5m_write", 0) or 0
        w1h = spent.get("cache_1h_write", 0) or 0
        cost_5m = _per_1m(w5m, (cache_pricing.get("5m") or {}).get("write_tokens_1M"))
        cost_1h = _per_1m(w1h, (cache_pricing.get("1h") or {}).get("write_tokens_1M"))
        cache_write_cost = cost_5m + cost_1h
        details = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_5m_write_tokens": w5m,
            "cache_1h_write_tokens": w1h,
            "cache_read_tokens": cache_read_tokens,
            "total_input_tokens": input_tokens + w5m + w1h + cache_read_tokens,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "cache_5m_write_cost_usd": cost_5m,
            "cache_1h_write_cost_usd": cost_1h,
            "cache_read_cost_usd": cache_read_cost,
        }
    else:
        cache_creation = spent.get("cache_creation", 0) or 0
        cache_write_cost = _per_1m(cache_creation, pr.get("cache_write_tokens_1M"))
        details = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation,
            "cache_read_tokens": cache_read_tokens,
            "total_input_tokens": input_tokens + cache_creation + cache_read_tokens,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "cache_write_cost_usd": cache_write_cost,
            "cache_read_cost_usd": cache_read_cost,
        }
    if thinking_tokens:
        details["thinking_tokens"] = thinking_tokens
        details["thinking_cost_usd"] = thinking_cost

    cost = input_cost + output_cost + thinking_cost + cache_write_cost + cache_read_cost
    return cost, details


def price_rollup_line(
        item: Dict[str, Any],
        *,
        pricing_table: Optional[Dict[str, Any]] = None,
        services_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Price one rollup line `{service, provider, model, spent}`.

    Returns `{cost_usd, pricing_source, price, tier, details}`: `price` is the
    matched price row (or None), `tier` the web-search tier (else None), and
    `details` the per-bucket settlement breakdown.

    `pricing_table` defaults to `price_table()`; `services_config` (the parsed
    ACCOUNTING_SERVICES setting) defaults to the configured value and only
    matters for web-search tier resolution.
    """
    table = pricing_table or price_table()
    service = item.get("service")
    provider = item.get("provider")
    model = item.get("model")
    spent = item.get("spent") or {}
    direct_cost_usd = float(spent.get("cost_usd", 0.0) or 0.0)

    pr: Optional[Dict[str, Any]] = None
    tier: Optional[str] = None
    cost_usd = 0.0
    details: Dict[str, Any] = {}

    if service == "llm":
        pr = _find_llm_price(provider, model, table)
        if pr:
            cost_usd, details = _price_llm(spent, pr)
    elif service == "embedding":
        pr = _find_emb_price(provider, model, table)
        if pr:
            cost_usd = _per_1m(spent.get("tokens", 0), pr.get("tokens_1M"))
            details = {"tokens": spent.get("tokens", 0)}
    elif service == "web_search":
        if services_config is None:
            services_config = load_services_config()
        tier = get_web_search_tier(provider, services_config)
        pr = _find_web_search_price(provider, tier, table)
        details = {
            "search_queries": spent.get("search_queries", 0),
            "search_results": spent.get("search_results", 0),
            "tier": tier,
        }
        if pr:
            cost_usd = (float(spent.get("search_queries", 0) or 0) / 1000.0) * float(
                pr.get("cost_per_1k_requests", 0.0) or 0.0
            )

    if pr:
        source = PRICED
    elif direct_cost_usd > 0:
        source = REPORTED
        cost_usd = direct_cost_usd
        details = {
            **(details if service == "web_search" else {}),
            "direct_cost_usd": direct_cost_usd,
            "pricing_source": REPORTED,
        }
    else:
        source = MISSING
        if service in _PRICED_SERVICES:
            _warn_missing_price(service, provider, model)
            details = {
                **(details if service == "web_search" else {}),
                "pricing_source": MISSING,
            }

    return {
        "cost_usd": cost_usd,
        "pricing_source": source,
        "price": pr,
        "tier": tier,
        "details": details,
    }


def compute_cost_estimate(rollup: List[dict], *, services_config: Optional[dict] = None) -> dict:
    """
    Price a usage rollup for reporting. Returns
    `{total_cost_usd, breakdown}` with one breakdown item per rollup line, in
    the same order. Each item carries `pricing_source`; a `missing_price` line
    is usage the price table cannot price.

    `services_config` is the parsed ACCOUNTING_SERVICES configuration; it only
    matters for web_search tier resolution and may be omitted.
    """
    table = price_table()
    if services_config is None:
        services_config = load_services_config()

    total_cost = 0.0
    breakdown = []
    for item in rollup:
        priced = price_rollup_line(item, pricing_table=table, services_config=services_config)
        service = item.get("service")
        spent = item.get("spent") or {}
        total_cost += priced["cost_usd"]

        breakdown_item = {
            "service": service,
            "provider": item.get("provider"),
            "model": item.get("model"),
            "cost_usd": priced["cost_usd"],
            "pricing_source": priced["pricing_source"],
        }
        if service == "web_search" and priced["tier"]:
            breakdown_item["tier"] = priced["tier"]
            breakdown_item["search_queries"] = spent.get("search_queries", 0)
            breakdown_item["search_results"] = spent.get("search_results", 0)
            if priced["price"]:
                breakdown_item["cost_per_1k_requests"] = priced["price"].get("cost_per_1k_requests", 0.0)
        breakdown.append(breakdown_item)

    return {
        "total_cost_usd": total_cost,
        "breakdown": breakdown,
    }


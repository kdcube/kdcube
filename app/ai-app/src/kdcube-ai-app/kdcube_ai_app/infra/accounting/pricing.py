# SPDX-License-Identifier: MIT
"""Price a usage rollup against the platform price table.

One pricing semantics for every spend surface: the opex endpoints, the
spend-rollup writer, and any report that turns rollup lines into dollars all
call `compute_cost_estimate`. The reported-cost rule matches the settlement
path in RateCalculator.calculate_turn_costs: a price-table entry wins;
otherwise the provider-reported cost accumulated in the rollup is the ground
truth (e.g. runtimes that self-report spend and have no price-table entry).
"""

from typing import List, Optional

from kdcube_ai_app.infra.accounting.usage import price_table


def compute_cost_estimate(rollup: List[dict], *, services_config: Optional[dict] = None) -> dict:
    """
    Compute cost estimates from rollup data using the price table.
    Returns cost breakdown (one item per rollup line, same order) and total.

    Supports: llm, embedding, web_search.

    `services_config` is the parsed ACCOUNTING_SERVICES configuration; it only
    matters for web_search tier resolution and may be omitted.
    """
    configuration = price_table()
    llm_pricelist = configuration.get("llm", [])
    emb_pricelist = configuration.get("embedding", [])
    web_search_pricelist = configuration.get("web_search", [])

    accounting_services_config = services_config or {}

    def _find_llm_price(provider: str, model: str):
        for p in llm_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    def _find_emb_price(provider: str, model: str):
        for p in emb_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    def _find_web_search_price(provider: str, tier: str):
        for p in web_search_pricelist:
            if p.get("provider") == provider and p.get("tier") == tier:
                return p
        return None

    def _get_web_search_tier(provider: str) -> str:
        web_search_config = accounting_services_config.get("web_search", {})
        provider_config = web_search_config.get(provider, {})
        defaults = {"brave": "base", "duckduckgo": "free"}
        return provider_config.get("tier", defaults.get(provider, "free"))

    total_cost = 0.0
    breakdown = []

    for item in rollup:
        service = item.get("service")
        provider = item.get("provider")
        model = item.get("model")
        spent = item.get("spent", {}) or {}

        cost_usd = 0.0
        tier = None  # for web_search
        pr = {}
        # Provider-REPORTED cost accumulated in the rollup: a price-table entry
        # wins; otherwise the reported cost is the ground truth.
        direct_cost_usd = float(spent.get("cost_usd", 0.0) or 0.0)

        if service == "llm":
            pr = _find_llm_price(provider, model)
            if pr:
                input_cost = (float(spent.get("input", 0)) / 1_000_000.0) * float(pr.get("input_tokens_1M", 0.0))
                output_cost = (float(spent.get("output", 0)) / 1_000_000.0) * float(pr.get("output_tokens_1M", 0.0))
                cache_read_cost = (float(spent.get("cache_read", 0)) / 1_000_000.0) * float(pr.get("cache_read_tokens_1M", 0.0))

                cache_write_cost = 0.0
                cache_pricing = pr.get("cache_pricing")

                if cache_pricing and isinstance(cache_pricing, dict):
                    cache_5m_tokens = float(spent.get("cache_5m_write", 0))
                    cache_1h_tokens = float(spent.get("cache_1h_write", 0))

                    if cache_5m_tokens > 0:
                        price_5m = float(cache_pricing.get("5m", {}).get("write_tokens_1M", 0.0))
                        cache_write_cost += (cache_5m_tokens / 1_000_000.0) * price_5m

                    if cache_1h_tokens > 0:
                        price_1h = float(cache_pricing.get("1h", {}).get("write_tokens_1M", 0.0))
                        cache_write_cost += (cache_1h_tokens / 1_000_000.0) * price_1h
                else:
                    cache_write_tokens = float(spent.get("cache_creation", 0))
                    cache_write_price = float(pr.get("cache_write_tokens_1M", 0.0))
                    cache_write_cost = (cache_write_tokens / 1_000_000.0) * cache_write_price

                cost_usd = input_cost + output_cost + cache_write_cost + cache_read_cost
            elif direct_cost_usd > 0:
                cost_usd = direct_cost_usd

        elif service == "embedding":
            pr = _find_emb_price(provider, model)
            if pr:
                cost_usd = (float(spent.get("tokens", 0)) / 1_000_000.0) * float(pr.get("tokens_1M", 0.0))
            elif direct_cost_usd > 0:
                cost_usd = direct_cost_usd

        elif service == "web_search":
            tier = _get_web_search_tier(provider)
            pr = _find_web_search_price(provider, tier)

            if pr:
                search_queries = float(spent.get("search_queries", 0))
                cost_per_1k = float(pr.get("cost_per_1k_requests", 0.0))
                cost_usd = (search_queries / 1000.0) * cost_per_1k

        total_cost += cost_usd

        breakdown_item = {
            "service": service,
            "provider": provider,
            "model": model,
            "cost_usd": cost_usd,
        }

        if service == "web_search" and tier:
            breakdown_item["tier"] = tier
            breakdown_item["search_queries"] = spent.get("search_queries", 0)
            breakdown_item["search_results"] = spent.get("search_results", 0)
            if pr:
                breakdown_item["cost_per_1k_requests"] = pr.get("cost_per_1k_requests", 0.0)

        breakdown.append(breakdown_item)

    return {
        "total_cost_usd": total_cost,
        "breakdown": breakdown
    }

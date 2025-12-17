#!/usr/bin/env python
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/sdk/examples/accounting/calc.py

# Compute total cost + per-agent breakdown from a folder of accounting events

import os
import json
from typing import Dict, Any, Tuple, List

from kdcube_ai_app.infra.accounting.calculator import (
    _spent_seed,
    _accumulate_compact,
    _extract_usage,
    _calculate_agent_costs,
)
from kdcube_ai_app.infra.accounting.usage import price_table


def iter_events(root_dir: str):
    """
    Yield parsed JSON accounting events from root_dir (recursively).
    """
    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    ev = json.load(f)
            except Exception:
                # silently skip broken files
                continue
            yield ev


def build_agent_usage(root_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Aggregate usage per agent into the same structure that
    _calculate_agent_costs(agent_usage, ...) expects:

        {
          "agent_name": [
            {
              "service": "llm" | "embedding",
              "provider": "...",
              "model": "...",
              "spent": {
                # for llm:
                "input": ...,
                "output": ...,
                "cache_5m_write": ...,
                "cache_1h_write": ...,
                "cache_read": ...,
                # for embedding:
                "tokens": ...
              },
            },
            ...
          ],
          ...
        }
    """
    # agent -> (service, provider, model) -> spent
    agent_rollup: Dict[str, Dict[Tuple[str, str, str], Dict[str, int]]] = {}

    for ev in iter_events(root_dir):
        service = str(ev.get("service_type") or "").strip()
        if service not in ("llm", "embedding"):
            continue

        # usage extraction
        usage = _extract_usage(ev)
        if not usage:
            continue

        metadata = ev.get("metadata") or {}
        context = ev.get("context") or {}

        agent = (
                metadata.get("agent")
                or context.get("agent")
                or ev.get("agent")
                or ev.get("agent_name")
                or "unknown"
        )

        provider = str(
            ev.get("provider") or context.get("provider") or ""
        ).strip()
        model = str(
            ev.get("model_or_service") or context.get("model_or_service") or ""
        ).strip()

        if agent not in agent_rollup:
            agent_rollup[agent] = {}

        key = (service, provider, model)
        spent = agent_rollup[agent].get(key)
        if not spent:
            spent = _spent_seed(service)
            agent_rollup[agent][key] = spent

        _accumulate_compact(spent, usage, service)

    # Convert to the structure expected by _calculate_agent_costs
    agent_usage: Dict[str, List[Dict[str, Any]]] = {}
    for agent, kv in agent_rollup.items():
        items: List[Dict[str, Any]] = []
        for (service, provider, model), spent in kv.items():
            items.append(
                {
                    "service": service,
                    "provider": provider or None,
                    "model": model or None,
                    "spent": {k: int(v) for k, v in spent.items()},
                }
            )
        agent_usage[agent] = items

    return agent_usage


def compute_costs_from_folder(root_dir: str):
    """
    Compute total cost and per-agent breakdown from folder.
    """
    agent_usage = build_agent_usage(root_dir)

    cfg = price_table()
    llm_pricelist = cfg.get("llm", []) or []
    emb_pricelist = cfg.get("embedding", []) or []

    agent_costs = _calculate_agent_costs(agent_usage, llm_pricelist, emb_pricelist)

    total_cost = sum(info["total_cost_usd"] for info in agent_costs.values())

    print(f"=== Total cost for folder '{root_dir}' ===")
    print(f"Total cost: ${total_cost:.6f}\n")

    print("=== Cost by agent ===")
    for agent, info in sorted(agent_costs.items(), key=lambda kv: kv[0]):
        print(f"- {agent}: ${info['total_cost_usd']:.6f}")

    print("\n=== Detailed breakdown per agent (service/provider/model) ===")
    for agent, info in sorted(agent_costs.items(), key=lambda kv: kv[0]):
        print(f"\nAgent: {agent}")
        for item in agent_usage.get(agent, []):
            svc = item["service"]
            prov = item["provider"]
            mdl = item["model"]
            spent = item["spent"]
            # Find cost entry for this (service, provider, model)
            matching = [
                b
                for b in info["breakdown"]
                if b["service"] == svc and b["provider"] == prov and b["model"] == mdl
            ]
            cost_usd = matching[0]["cost_usd"] if matching else 0.0
            print(
                f"  - {svc} | {prov} | {mdl}: "
                f"tokens/spent={spent}, cost=${cost_usd:.6f}"
            )


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description="Compute total cost and per-agent breakdown "
                    "from a folder of accounting JSON events."
    )
    parser.add_argument(
        "events_dir",
        help="Path to the folder containing JSON accounting events",
    )
    args = parser.parse_args()

    compute_costs_from_folder(args.events_dir)

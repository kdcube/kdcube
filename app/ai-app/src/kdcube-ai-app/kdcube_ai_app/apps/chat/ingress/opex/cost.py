# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/opex/cost.py
"""
Shared true-cost helpers.

Turns accounting usage *rollups* into real USD spend, priced per (service,
provider, model) with each model's own input/output (and cache) rates via the
canonical engine `infra.accounting.usage.compute_rollup_cost` -- the SAME
computation that powers /api/opex/*. So every "cost per user" surface agrees to
the cent.

Why this exists:
    The economics dashboards historically priced usage as
    `equivalent_tokens * reference_model_OUTPUT_rate` (see
    user_budget.UserBudgetBreakdownService): that charges input tokens at the
    output rate and prices every model at the reference model's rate. It is a
    quota-equivalent estimate, NOT real spend. These helpers expose the real,
    per-model spend so both the user and admin dashboards can show it.

Consumers:
    - GET /api/economics/me/cost-breakdown   (this user's true spend)
    - GET /api/economics/admin/cost-by-user  (per-user true spend, all users)
"""
from typing import Any, Dict, List

from kdcube_ai_app.infra.accounting.usage import compute_rollup_cost

__all__ = [
    "build_rate_calculator",
    "assemble_cost",
    "cost_for_user",
    "cost_by_user",
]


def build_rate_calculator():
    """Construct a RateCalculator over the configured accounting storage.

    Mirrors opex.opex._get_calculator (raw events under 'accounting', rolled-up
    aggregates under 'analytics') but without the app.state cache, so routers
    that only have get_settings() available can use it. Heavy deps are imported
    lazily so this module's pure helpers stay importable on their own.
    """
    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    from kdcube_ai_app.storage.storage import create_storage_backend
    from kdcube_ai_app.infra.accounting.calculator import RateCalculator

    settings = get_settings()
    kdcube_path = settings.STORAGE_PATH or "file:///tmp/kdcube_data"
    backend = create_storage_backend(kdcube_path)
    return RateCalculator(backend, base_path="accounting", agg_base="analytics")


def _tokens_from_rollup(rollup: List[dict]) -> Dict[str, int]:
    """Sum raw token counts across a rollup, split by kind (for display)."""
    agg = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "embedding_tokens": 0,
    }
    for item in rollup or []:
        spent = item.get("spent", {}) or {}
        agg["input_tokens"] += int(spent.get("input", 0) or 0)
        agg["output_tokens"] += int(spent.get("output", 0) or 0)
        agg["cache_read_tokens"] += int(spent.get("cache_read", 0) or 0)
        agg["cache_write_tokens"] += (
            int(spent.get("cache_5m_write", 0) or 0)
            + int(spent.get("cache_1h_write", 0) or 0)
            + int(spent.get("cache_creation", 0) or 0)
        )
        agg["embedding_tokens"] += int(spent.get("tokens", 0) or 0)
    return agg


def assemble_cost(rollup: List[dict]) -> Dict[str, Any]:
    """Turn a usage rollup into a compact true-cost payload.

    Returns {total_cost_usd, by_model[], tokens{}} where each by_model entry is
    {service, provider, model, cost_usd} priced with real per-model input/output
    (and cache) rates.
    """
    rollup = rollup or []
    est = compute_rollup_cost(rollup) or {}
    breakdown = est.get("breakdown", []) or []

    # compute_rollup_cost preserves input order (one breakdown entry per rollup
    # item), so zip to attach each line's token split for a per-model table.
    by_model = []
    for item, b in zip(rollup, breakdown):
        spent = item.get("spent", {}) or {}
        by_model.append({
            **b,
            "input_tokens": int(spent.get("input", 0) or 0),
            "output_tokens": int(spent.get("output", 0) or 0),
            "embedding_tokens": int(spent.get("tokens", 0) or 0),
        })

    return {
        "total_cost_usd": round(float(est.get("total_cost_usd", 0.0) or 0.0), 6),
        "by_model": by_model,
        "tokens": _tokens_from_rollup(rollup),
    }


def _event_count(user_data: dict) -> int:
    # The aggregate path exposes event_count at the top level of the user entry;
    # the raw-scan fallback nests it inside "total". Accept either.
    user_data = user_data or {}
    raw = user_data.get("event_count")
    if raw is None:
        raw = (user_data.get("total") or {}).get("event_count", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def cost_for_user(
    calc,
    *,
    tenant: str,
    project: str,
    user_id: str,
    date_from: str,
    date_to: str,
) -> Dict[str, Any]:
    """True spend for a single user over [date_from, date_to] (YYYY-MM-DD).

    Uses the calculator's single-user path so we never build or price every
    other user's usage just to report one user's spend.
    """
    user_data = await calc.usage_for_user(
        tenant_id=tenant,
        project_id=project,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
    ) or {}
    rollup = user_data.get("rollup") or []
    out = assemble_cost(rollup)
    out.update(
        {
            "user_id": user_id,
            "date_from": date_from,
            "date_to": date_to,
            "event_count": _event_count(user_data),
        }
    )
    return out


async def cost_by_user(
    calc,
    *,
    tenant: str,
    project: str,
    date_from: str,
    date_to: str,
) -> Dict[str, Any]:
    """True spend per user across the workspace, sorted by cost descending."""
    by_user = await calc.usage_by_user(
        tenant_id=tenant,
        project_id=project,
        date_from=date_from,
        date_to=date_to,
    )

    users: List[Dict[str, Any]] = []
    total = 0.0
    for uid, user_data in (by_user or {}).items():
        rollup = (user_data or {}).get("rollup") or []
        c = assemble_cost(rollup)
        total += c["total_cost_usd"]
        users.append({"user_id": uid, **c, "event_count": _event_count(user_data)})

    users.sort(key=lambda u: u["total_cost_usd"], reverse=True)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_cost_usd": round(total, 6),
        "total_users": len(users),
        "users": users,
    }

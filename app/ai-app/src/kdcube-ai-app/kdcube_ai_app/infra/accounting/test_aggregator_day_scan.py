# SPDX-License-Identifier: MIT
"""Day-scan behavior of AccountingAggregator: bounded-concurrency reads produce
the same daily aggregate as the previous sequential loop, and a recompute pass
overwrites a stale partial aggregate (the intra-day today-refresh contract)."""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from kdcube_ai_app.infra.accounting.aggregator import AccountingAggregator
from kdcube_ai_app.storage.storage import create_storage_backend


def _event(user, model, inp, out, ts):
    return {
        "event_id": f"ev-{user}-{model}-{ts}",
        "timestamp": ts,
        "service_type": "llm",
        "provider": "anthropic",
        "model_or_service": model,
        "user_id": user,
        "context": {"user_id": user},
        "usage": {"input_tokens": inp, "output_tokens": out, "requests": 1},
        "metadata": {"agent_name": "main"},
    }


def _write_raw(backend_root, tenant, project, day_label, events):
    day_dir = backend_root / "accounting" / tenant / project / day_label / "llm" / "grp"
    day_dir.mkdir(parents=True, exist_ok=True)
    for i, ev in enumerate(events):
        (day_dir / f"e{i}.json").write_text(json.dumps(ev))


@pytest.fixture()
def storage(tmp_path):
    return tmp_path, create_storage_backend(f"file://{tmp_path}")


def test_day_scan_aggregates_all_users_with_bounded_reads(storage):
    root, backend = storage
    events = (
        [_event("alice", "claude-sonnet-4-5", 100, 10, f"2026-07-21T0{h}:00:00Z") for h in range(5)]
        + [_event("bob", "claude-opus-4-8", 200, 20, "2026-07-21T09:00:00Z")]
    )
    _write_raw(root, "t", "p", "2026.07.21", events)

    agg = AccountingAggregator(backend, read_concurrency=4)
    result = asyncio.run(agg.aggregate_daily_for_project(tenant_id="t", project_id="p", day=date(2026, 7, 21)))

    assert result is not None
    users = json.loads((root / "analytics/t/p/accounting/daily/2026/07/21/users.json").read_text())
    per_user = {u["user_id"]: u for u in users["users"]}
    assert set(per_user) == {"alice", "bob"}
    assert per_user["alice"]["event_count"] == 5
    assert per_user["alice"]["total"]["input_tokens"] == 500
    assert per_user["bob"]["total"]["output_tokens"] == 20
    bob_models = {r["model"] for r in per_user["bob"]["rollup"]}
    assert bob_models == {"claude-opus-4-8"}


def test_recompute_overwrites_partial_day(storage):
    """The today-refresh contract: a second aggregation pass with more events
    must replace the earlier (partial) aggregate, not be skipped."""
    root, backend = storage
    _write_raw(root, "t", "p", "2026.07.21", [_event("alice", "claude-sonnet-4-5", 100, 10, "2026-07-21T08:00:00Z")])
    agg = AccountingAggregator(backend, read_concurrency=4)

    asyncio.run(agg.aggregate_daily_range_for_project(
        tenant_id="t", project_id="p", date_from="2026-07-21", date_to="2026-07-21", skip_existing=True))
    users1 = {u["user_id"]: u for u in json.loads((root / "analytics/t/p/accounting/daily/2026/07/21/users.json").read_text())["users"]}
    assert users1["alice"]["event_count"] == 1

    # more spend arrives during the day
    _write_raw(root, "t", "p", "2026.07.21", [
        _event("alice", "claude-sonnet-4-5", 100, 10, "2026-07-21T08:00:00Z"),
        _event("alice", "claude-sonnet-4-5", 300, 30, "2026-07-21T11:00:00Z"),
    ])

    # skip_existing=True would keep the stale aggregate...
    asyncio.run(agg.aggregate_daily_range_for_project(
        tenant_id="t", project_id="p", date_from="2026-07-21", date_to="2026-07-21", skip_existing=True))
    stale = {u["user_id"]: u for u in json.loads((root / "analytics/t/p/accounting/daily/2026/07/21/users.json").read_text())["users"]}
    assert stale["alice"]["event_count"] == 1

    # ...recompute (skip_existing=False) refreshes it
    asyncio.run(agg.aggregate_daily_range_for_project(
        tenant_id="t", project_id="p", date_from="2026-07-21", date_to="2026-07-21", skip_existing=False))
    fresh = {u["user_id"]: u for u in json.loads((root / "analytics/t/p/accounting/daily/2026/07/21/users.json").read_text())["users"]}
    assert fresh["alice"]["event_count"] == 2
    assert fresh["alice"]["total"]["input_tokens"] == 400


def test_usage_by_user_aggregates_only_never_raw_scans(storage):
    """Latency contract for user-facing spend views: with aggregates_only=True,
    a window with no aggregate coverage returns {} instead of falling back to
    the raw event scan (which reads every event file in the window)."""
    from kdcube_ai_app.infra.accounting.calculator import RateCalculator

    root, backend = storage
    # raw events exist, but NO aggregates were computed for this window
    _write_raw(root, "t", "p", "2026.07.21", [_event("alice", "claude-sonnet-4-5", 100, 10, "2026-07-21T08:00:00Z")])
    calc = RateCalculator(backend, base_path="accounting", agg_base="analytics")

    res = asyncio.run(calc.usage_by_user(
        tenant_id="t", project_id="p", date_from="2026-07-21", date_to="2026-07-21",
        aggregates_only=True))
    assert res == {}

    # once the day is aggregated, the same call serves data from aggregates
    agg = AccountingAggregator(backend)
    asyncio.run(agg.aggregate_daily_for_project(tenant_id="t", project_id="p", day=date(2026, 7, 21)))
    res2 = asyncio.run(calc.usage_by_user(
        tenant_id="t", project_id="p", date_from="2026-07-21", date_to="2026-07-21",
        aggregates_only=True))
    assert "alice" in res2 and res2["alice"]["rollup"]

# SPDX-License-Identifier: MIT
"""Server-side sort/filter/page over a priced dimension set.

Sorting by spend is derived, so the full set is computed once and the page is
cut from it; these tests pin the pure helpers so /users, /agents and /apps
page identically."""
from __future__ import annotations

from kdcube_ai_app.apps.chat.ingress.opex.paging import (
    dimension_rows as _dimension_rows,
    page_dimension_rows as _page_dimension_rows,
)


def _by_dim():
    return {
        "alice": {"total": {"input_tokens": 100, "output_tokens": 10, "requests": 3}, "event_count": 3, "rollup": [1]},
        "bob": {"total": {"input_tokens": 900, "output_tokens": 90}, "event_count": 9, "rollup": [1]},
        "bundle": {"total": {"input_tokens": 50, "output_tokens": 5}, "event_count": 1, "rollup": [1]},
    }


def _estimates():
    return {
        "alice": {"total_cost_usd": 0.10, "breakdown": [{"model": "m1", "cost_usd": 0.10}]},
        "bob": {"total_cost_usd": 0.90, "breakdown": [{"model": "m2", "cost_usd": 0.90}]},
        "bundle": {"total_cost_usd": 0.05, "breakdown": []},
    }


def test_rows_flatten_totals_costs_and_events():
    rows = {r["id"]: r for r in _dimension_rows(_by_dim(), _estimates())}
    assert rows["bob"]["cost_usd"] == 0.9
    assert rows["bob"]["input_tokens"] == 900
    assert rows["alice"]["events"] == 3
    assert rows["bob"]["by_model"][0]["model"] == "m2"


def test_page_sorts_by_cost_desc_and_slices():
    rows = _dimension_rows(_by_dim(), _estimates())
    page = _page_dimension_rows(rows, sort_by="cost", order="desc", limit=2, offset=0)
    assert [r["id"] for r in page["items"]] == ["bob", "alice"]
    assert page["total_count"] == 3
    assert abs(page["total_cost_usd"] - 1.05) < 1e-9
    page2 = _page_dimension_rows(rows, sort_by="cost", order="desc", limit=2, offset=2)
    assert [r["id"] for r in page2["items"]] == ["bundle"]


def test_filter_tokens_and_totals_follow_filter():
    rows = _dimension_rows(_by_dim(), _estimates())
    page = _page_dimension_rows(rows, q="ali, bun", limit=10)
    assert {r["id"] for r in page["items"]} == {"alice", "bundle"}
    assert page["total_count"] == 2
    assert abs(page["total_cost_usd"] - 0.15) < 1e-9


def test_sort_by_id_asc_and_unknown_key_falls_back_to_cost():
    rows = _dimension_rows(_by_dim(), _estimates())
    page = _page_dimension_rows(rows, sort_by="id", order="asc", limit=10)
    assert [r["id"] for r in page["items"]] == ["alice", "bob", "bundle"]
    fallback = _page_dimension_rows(rows, sort_by="nonsense", limit=10)
    assert fallback["items"][0]["id"] == "bob"

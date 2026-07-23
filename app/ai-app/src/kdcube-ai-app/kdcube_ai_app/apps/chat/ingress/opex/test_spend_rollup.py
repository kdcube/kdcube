# SPDX-License-Identifier: MIT
"""Pure parts of the derived spend-rollup index: row building must mirror the
file-aggregate paging exactly, so SQL pages and file pages agree."""
from decimal import Decimal

from kdcube_ai_app.apps.chat.ingress.opex.paging import dimension_rows
from kdcube_ai_app.apps.chat.ingress.opex.spend_rollup import (
    build_day_rows, like_patterns, rows_from_records, _line_tokens,
)


def _by_dim():
    return {
        "alice": {
            "total": {"input_tokens": 100, "output_tokens": 10},
            "event_count": 3,
            "rollup": [
                {"service": "llm", "provider": "anthropic", "model": "m1",
                 "spent": {"input": 80, "output": 8, "requests": 2}},
                {"service": "llm", "provider": "anthropic", "model": "m1",
                 "spent": {"input": 20, "output": 2, "requests": 1}},
                {"service": "embedding", "provider": "openai", "model": "e1",
                 "spent": {"tokens": 500, "requests": 4}},
            ],
        },
    }


def _estimates():
    return {
        "alice": {
            "total_cost_usd": 0.35,
            "breakdown": [
                {"service": "llm", "provider": "anthropic", "model": "m1", "cost_usd": 0.2},
                {"service": "llm", "provider": "anthropic", "model": "m1", "cost_usd": 0.1},
                {"service": "embedding", "provider": "openai", "model": "e1", "cost_usd": 0.05},
            ],
        },
    }


def test_totals_rows_mirror_dimension_rows():
    totals_rows, _ = build_day_rows("user", _by_dim(), _estimates())
    assert totals_rows == [("user", "alice", 100, 10, 0, 3, Decimal("0.35"))]
    # exactly what the file-aggregate path would page
    ref = dimension_rows(_by_dim(), _estimates())[0]
    assert (ref["input_tokens"], ref["events"], ref["cost_usd"]) == (100, 3, 0.35)


def test_line_rows_accumulate_per_service_provider_model():
    _, line_rows = build_day_rows("user", _by_dim(), _estimates())
    lines = {(r[2], r[3], r[4]): r for r in line_rows}
    llm = lines[("llm", "anthropic", "m1")]
    # two rollup lines for the same model fold into one PK row
    assert (llm[5], llm[6], llm[8]) == (100, 10, 3)   # input, output, requests
    assert llm[9] == Decimal("0.3")
    emb = lines[("embedding", "openai", "e1")]
    assert (emb[7], emb[8], emb[9]) == (500, 4, Decimal("0.05"))


def test_line_tokens_per_service():
    assert _line_tokens("llm", {"input": 5, "output": 7, "requests": 2}) == (5, 7, 0, 2)
    assert _line_tokens("embedding", {"tokens": 9, "requests": 1}) == (0, 0, 9, 1)
    assert _line_tokens("web_search", {"search_queries": 6}) == (0, 0, 0, 6)


def test_like_patterns_escape_wildcards():
    assert like_patterns("ali, x%y") == ["%ali%", "%x\\%y%"]
    assert like_patterns("") == []


def test_rows_from_records_shapes_page_items():
    recs = [{"dim_id": "alice", "cost_usd": 0.35, "input_tokens": 100,
             "output_tokens": 10, "embedding_tokens": 500, "events": 3}]
    items = rows_from_records(recs, {"alice": [{"model": "m1", "cost_usd": 0.3}]})
    assert items[0]["id"] == "alice"
    assert items[0]["by_model"][0]["model"] == "m1"

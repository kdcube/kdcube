# SPDX-License-Identifier: MIT
"""Server-side sort / filter / paging over a priced dimension set.

Sorting by spend is a derived criterion: a page of "top spenders" cannot be
produced without pricing the whole window first. So the full set is computed
once (and briefly cached per window+dimension); filter/sort/slice per request
are cheap. Shared by the opex endpoints (file-aggregate path) and the
spend-rollup reader (DB path) so both produce the same paged shape.
"""

import re
from typing import List

SORT_KEYS = {
    "cost": lambda r: r["cost_usd"],
    "input_tokens": lambda r: r["input_tokens"],
    "output_tokens": lambda r: r["output_tokens"],
    "events": lambda r: r["events"],
    "id": lambda r: r["id"],
}


def dimension_rows(by_dim: dict, estimates: dict) -> List[dict]:
    """Flatten a usage_by_* result + cost estimates into uniform rows."""
    rows: List[dict] = []
    for dim_id, data in (by_dim or {}).items():
        data = data or {}
        total = data.get("total") or {}
        est = (estimates or {}).get(dim_id) or {}
        events = data.get("event_count")
        if events is None:
            events = (total or {}).get("requests", 0)
        rows.append({
            "id": str(dim_id),
            "cost_usd": round(float(est.get("total_cost_usd", 0.0) or 0.0), 6),
            "input_tokens": int(total.get("input_tokens", 0) or 0),
            "output_tokens": int(total.get("output_tokens", 0) or 0),
            "embedding_tokens": int(total.get("embedding_tokens", 0) or 0),
            "events": int(events or 0),
            "by_model": est.get("breakdown") or [],
        })
    return rows


def filter_tokens(q: str) -> List[str]:
    """Comma/space-separated id-filter tokens, lowercased."""
    return [t.strip().lower() for t in re.split(r"[\s,]+", str(q or "")) if t.strip()]


def page_dimension_rows(
    rows: List[dict],
    *,
    sort_by: str = "cost",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    q: str = "",
) -> dict:
    """Filter by id tokens, sort, slice. Pure — testable without FastAPI.

    `q` is comma/space-separated tokens; a row matches when any token is a
    case-insensitive substring of its id. Returns the page plus totals over the
    FILTERED set so the header numbers match what the filter selected.
    """
    tokens = filter_tokens(q)
    if tokens:
        rows = [r for r in rows if any(t in r["id"].lower() for t in tokens)]

    key = SORT_KEYS.get(str(sort_by or "cost").lower(), SORT_KEYS["cost"])
    reverse = str(order or "desc").lower() != "asc"
    rows = sorted(rows, key=key, reverse=reverse)

    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    page = rows[offset:offset + limit]

    return {
        "total_count": len(rows),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "total_events": sum(r["events"] for r in rows),
        "offset": offset,
        "limit": limit,
        "sort_by": sort_by,
        "order": "asc" if not reverse else "desc",
        "items": page,
    }

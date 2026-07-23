# SPDX-License-Identifier: MIT
"""Derived spend-rollup index: aggregates → per-project DB tables → SQL paging.

The accounting analytics files remain the source of truth. This module keeps a
DAILY per-dimension rollup of them in Postgres (spend_rollup_totals /
spend_rollup_lines / spend_rollup_coverage, DDL in
ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql) so that "top spenders
this month, page 5, filtered to two apps" is one indexed ORDER BY … LIMIT
instead of a full compute.

Written ONLY by the aggregation routines (nightly cron, today-refresh, admin
rebuild) right after they write the JSON aggregates — never by the live turn
path. Rebuildable at any time; a price-table change is repriced by rebuild
because tokens stay in the rows. The reader serves a window from SQL only when
EVERY day of the window is covered (coverage markers distinguish "no usage"
from "never computed"); anything else returns None and the caller falls back
to the file-aggregate path.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema
from kdcube_ai_app.apps.chat.ingress.opex.paging import dimension_rows, filter_tokens

logger = logging.getLogger("OPEX.SpendRollup")

DIMENSIONS = ("user", "agent", "app")

_SORT_COLUMNS = {
    "cost": "cost_usd",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "events": "events",
    "id": "dim_id",
}


# =============================================================================
# Pure row building (testable without a database)
# =============================================================================

def _line_tokens(service: str, spent: dict) -> Tuple[int, int, int, int]:
    """(input, output, embedding, requests) for one rollup line."""
    spent = spent or {}
    requests = int(spent.get("requests", 0) or 0)
    if service == "llm":
        return int(spent.get("input", 0) or 0), int(spent.get("output", 0) or 0), 0, requests
    if service == "embedding":
        return 0, 0, int(spent.get("tokens", 0) or 0), requests
    if service == "web_search":
        return 0, 0, 0, requests or int(spent.get("search_queries", 0) or 0)
    return 0, 0, 0, requests


def build_day_rows(dimension: str, by_dim: dict, estimates: dict) -> Tuple[List[tuple], List[tuple]]:
    """Turn one day's usage_by_* result + estimates into DB row tuples.

    Returns (totals_rows, line_rows) without the day column — the writer
    prepends it. Totals rows mirror paging.dimension_rows exactly, so SQL pages
    and file-aggregate pages agree. Lines are accumulated per
    (service, provider, model) to satisfy the primary key.
    """
    totals_rows: List[tuple] = []
    line_rows: List[tuple] = []

    totals = dimension_rows(by_dim, estimates)
    for row in totals:
        totals_rows.append((
            dimension, row["id"],
            row["input_tokens"], row["output_tokens"], row["embedding_tokens"],
            row["events"], Decimal(str(row["cost_usd"])),
        ))

    for dim_id, data in (by_dim or {}).items():
        rollup = (data or {}).get("rollup") or []
        breakdown = ((estimates or {}).get(dim_id) or {}).get("breakdown") or []
        acc: Dict[tuple, list] = {}
        # compute_cost_estimate emits breakdown in rollup order — zip is exact.
        for item, priced in zip(rollup, breakdown):
            service = str(item.get("service") or "")
            provider = str(item.get("provider") or "")
            model = str(item.get("model") or "")
            i, o, e, r = _line_tokens(service, item.get("spent") or {})
            cost = float(priced.get("cost_usd", 0.0) or 0.0)
            key = (service, provider, model)
            slot = acc.setdefault(key, [0, 0, 0, 0, 0.0])
            slot[0] += i; slot[1] += o; slot[2] += e; slot[3] += r; slot[4] += cost
        for (service, provider, model), (i, o, e, r, cost) in acc.items():
            line_rows.append((
                dimension, str(dim_id), service, provider, model,
                i, o, e, r, Decimal(str(round(cost, 6))),
            ))

    return totals_rows, line_rows


def rows_from_records(page_records: list, lines_by_id: Dict[str, list]) -> List[dict]:
    """Shape SQL records into the paged `items` rows (same shape as paging.py)."""
    items: List[dict] = []
    for rec in page_records:
        dim_id = rec["dim_id"]
        items.append({
            "id": dim_id,
            "cost_usd": round(float(rec["cost_usd"] or 0.0), 6),
            "input_tokens": int(rec["input_tokens"] or 0),
            "output_tokens": int(rec["output_tokens"] or 0),
            "embedding_tokens": int(rec["embedding_tokens"] or 0),
            "events": int(rec["events"] or 0),
            "by_model": lines_by_id.get(dim_id, []),
        })
    return items


def like_patterns(q: str) -> List[str]:
    """ILIKE patterns for the id filter (escaped, substring semantics)."""
    pats = []
    for t in filter_tokens(q):
        t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pats.append(f"%{t}%")
    return pats


# =============================================================================
# Writer — called by the aggregation routines only
# =============================================================================

async def upsert_day(pool, *, tenant: str, project: str, day: str, calc, services_config: Optional[dict] = None) -> None:
    """Replace the rollup rows for one day from the freshly written aggregates.

    Delete-then-insert per (day, dimension) in one transaction, then mark
    coverage — so a partially failed write never counts as covered.
    """
    from kdcube_ai_app.infra.accounting.pricing import compute_cost_estimate

    schema = project_schema(tenant, project)
    day_d = date.fromisoformat(day)

    fetchers = {
        "user": lambda: calc.usage_by_user(
            tenant_id=tenant, project_id=project, date_from=day, date_to=day, aggregates_only=True),
        "agent": lambda: calc.usage_by_agent(
            tenant_id=tenant, project_id=project, date_from=day, date_to=day),
        "app": lambda: calc.usage_by_app(
            tenant_id=tenant, project_id=project, date_from=day, date_to=day, aggregates_only=True),
    }

    for dimension in DIMENSIONS:
        by_dim = await fetchers[dimension]() or {}
        estimates = {
            dim_id: compute_cost_estimate(data.get("rollup") or [], services_config=services_config)
            for dim_id, data in by_dim.items() if (data or {}).get("rollup")
        }
        totals_rows, line_rows = build_day_rows(dimension, by_dim, estimates)

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"DELETE FROM {schema}.spend_rollup_totals WHERE day = $1 AND dimension = $2",
                    day_d, dimension)
                await conn.execute(
                    f"DELETE FROM {schema}.spend_rollup_lines WHERE day = $1 AND dimension = $2",
                    day_d, dimension)
                if totals_rows:
                    await conn.executemany(
                        f"""INSERT INTO {schema}.spend_rollup_totals
                            (day, dimension, dim_id, input_tokens, output_tokens, embedding_tokens, events, cost_usd)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                        [(day_d, *r) for r in totals_rows])
                if line_rows:
                    await conn.executemany(
                        f"""INSERT INTO {schema}.spend_rollup_lines
                            (day, dimension, dim_id, service, provider, model,
                             input_tokens, output_tokens, embedding_tokens, requests, cost_usd)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
                        [(day_d, *r) for r in line_rows])
                await conn.execute(
                    f"""INSERT INTO {schema}.spend_rollup_coverage (day, dimension)
                        VALUES ($1, $2)
                        ON CONFLICT (day, dimension) DO UPDATE SET computed_at = NOW()""",
                    day_d, dimension)

    logger.info("[SpendRollup] Rolled up %s/%s for %s", tenant, project, day)


# =============================================================================
# Reader — used by the opex paged endpoints; None means "fall back to files"
# =============================================================================

async def query_page(
    pool, *, tenant: str, project: str, dimension: str,
    date_from: str, date_to: str,
    sort_by: str = "cost", order: str = "desc",
    limit: int = 50, offset: int = 0, q: str = "",
) -> Optional[dict]:
    schema = project_schema(tenant, project)
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    if d_to < d_from:
        return None

    today = datetime.now(timezone.utc).date()
    expected_days = max(0, (min(d_to, today) - d_from).days + 1) if d_from <= today else 0

    async with pool.acquire() as conn:
        if expected_days:
            covered = await conn.fetchval(
                f"""SELECT COUNT(*) FROM {schema}.spend_rollup_coverage
                    WHERE dimension = $1 AND day BETWEEN $2 AND $3""",
                dimension, d_from, min(d_to, today))
            if int(covered or 0) < expected_days:
                return None

        col = _SORT_COLUMNS.get(str(sort_by or "cost").lower(), "cost_usd")
        direction = "ASC" if str(order or "desc").lower() == "asc" else "DESC"
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))

        pats = like_patterns(q)
        where_q = "AND dim_id ILIKE ANY($4::text[])" if pats else ""
        agg_cte = f"""
            WITH agg AS (
                SELECT dim_id,
                       SUM(input_tokens)     AS input_tokens,
                       SUM(output_tokens)    AS output_tokens,
                       SUM(embedding_tokens) AS embedding_tokens,
                       SUM(events)           AS events,
                       SUM(cost_usd)::float8 AS cost_usd
                FROM {schema}.spend_rollup_totals
                WHERE dimension = $1 AND day BETWEEN $2 AND $3 {where_q}
                GROUP BY dim_id
            )
        """
        args = [dimension, d_from, d_to] + ([pats] if pats else [])

        totals = await conn.fetchrow(
            agg_cte + """SELECT COUNT(*) AS cnt,
                                COALESCE(SUM(cost_usd), 0)::float8 AS cost,
                                COALESCE(SUM(events), 0) AS events
                         FROM agg""",
            *args)
        page = await conn.fetch(
            agg_cte + f"""SELECT * FROM agg
                          ORDER BY {col} {direction}, dim_id ASC
                          LIMIT {limit} OFFSET {offset}""",
            *args)

        lines_by_id: Dict[str, list] = {}
        page_ids = [r["dim_id"] for r in page]
        if page_ids:
            line_recs = await conn.fetch(
                f"""SELECT dim_id, service, provider, model,
                           SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(embedding_tokens) AS embedding_tokens,
                           SUM(requests) AS requests,
                           SUM(cost_usd)::float8 AS cost_usd
                    FROM {schema}.spend_rollup_lines
                    WHERE dimension = $1 AND day BETWEEN $2 AND $3 AND dim_id = ANY($4::text[])
                    GROUP BY dim_id, service, provider, model
                    ORDER BY dim_id, cost_usd DESC""",
                dimension, d_from, d_to, page_ids)
            for rec in line_recs:
                lines_by_id.setdefault(rec["dim_id"], []).append({
                    "service": rec["service"],
                    "provider": rec["provider"] or None,
                    "model": rec["model"] or None,
                    "cost_usd": round(float(rec["cost_usd"] or 0.0), 6),
                    "input_tokens": int(rec["input_tokens"] or 0),
                    "output_tokens": int(rec["output_tokens"] or 0),
                    "embedding_tokens": int(rec["embedding_tokens"] or 0),
                    "requests": int(rec["requests"] or 0),
                })

    return {
        "total_count": int(totals["cnt"] or 0),
        "total_cost_usd": round(float(totals["cost"] or 0.0), 6),
        "total_events": int(totals["events"] or 0),
        "offset": offset,
        "limit": limit,
        "sort_by": sort_by,
        "order": direction.lower(),
        "items": rows_from_records(page, lines_by_id),
    }


async def fetch_page(**kwargs) -> Optional[dict]:
    """Endpoint-facing wrapper: acquire the ingress pool, query, and swallow
    every failure into None so callers always have the file-aggregate path.
    Missing tables (schema not yet redeployed) land here too."""
    try:
        from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool
        pool = await get_pg_pool()
        if pool is None:
            return None
        return await query_page(pool, **kwargs)
    except Exception as e:
        logger.debug("[SpendRollup] falling back to file aggregates: %s", e)
        return None

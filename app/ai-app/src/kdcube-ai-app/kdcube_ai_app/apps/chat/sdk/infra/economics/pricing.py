# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/sdk/infra/economics/pricing.py
"""
Effective-dated model pricing.

Cost is recorded from the provider's REPORTED cost when available (the billed
ground truth). When it is not reported, cost is computed from a price table --
and that table is time-versioned here so a price change applies from a chosen
date forward while historical events keep the price that was in effect.

  * seed_from_code()  -> one-time seed from the in-code price_table() at epoch,
    so every historical event has a defined fallback price.
  * update_price()    -> record a new price version effective from a given date
    ("start the change"); older events are unaffected.
  * table_as_of(when) -> a price_table()-shaped dict with the rate in effect at
    `when`, for compute_rollup_cost(). Returns None when unseeded (callers then
    use the in-code table), so this is fully backward compatible.

Pricing is logically global; it is stored per project schema (alongside the
usage ledger) so the write/backfill path -- which already holds the project pool
-- can resolve it without a cross-schema dependency.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema
from kdcube_ai_app.infra.accounting.usage import price_table

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ModelPricingStore:
    def __init__(self, pg_pool, *, tenant: str, project: str):
        self.pool = pg_pool
        self.tenant = tenant
        self.project = project
        self.schema = _project_schema(tenant, project)

    async def is_empty(self) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT 1 FROM {self.schema}.model_pricing LIMIT 1")
        return row is None

    async def seed_from_code(self, *, force: bool = False) -> int:
        """Seed the table from the in-code price_table() (effective at epoch).

        No-op if already seeded (unless force). Returns rows inserted.
        """
        if not force and not await self.is_empty():
            return 0
        pt = price_table() or {}
        rows: List[tuple] = []
        for svc in ("llm", "embedding", "web_search"):
            for item in (pt.get(svc) or []):
                provider = item.get("provider")
                model = item.get("model") or item.get("tier")
                if not provider or not model:
                    continue
                rows.append((svc, provider, model, json.dumps(item), _EPOCH, "seed:code"))
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                f"""INSERT INTO {self.schema}.model_pricing
                    (service_type, provider, model, rates, effective_from, note)
                    VALUES ($1,$2,$3,$4::jsonb,$5,$6)""",
                rows,
            )
        return len(rows)

    async def update_price(
        self,
        *,
        service_type: str,
        provider: str,
        model: str,
        rates: Dict[str, Any],
        effective_from: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> None:
        """Record a new price version, effective from `effective_from` (default now)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {self.schema}.model_pricing
                    (service_type, provider, model, rates, effective_from, note)
                    VALUES ($1,$2,$3,$4::jsonb, COALESCE($5, NOW()), $6)""",
                service_type, provider, model, json.dumps(rates), effective_from, note,
            )

    async def table_as_of(self, when: datetime) -> Optional[dict]:
        """price_table()-shaped dict with the rates effective at `when`.

        Returns None when no pricing rows exist (caller falls back to the in-code
        table), keeping behavior identical until the table is seeded.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT ON (service_type, provider, model)
                        service_type, rates
                    FROM {self.schema}.model_pricing
                    WHERE effective_from <= $1
                    ORDER BY service_type, provider, model, effective_from DESC""",
                when,
            )
        if not rows:
            return None
        out: Dict[str, List[dict]] = {"llm": [], "embedding": [], "web_search": []}
        for r in rows:
            svc = r["service_type"]
            rates = r["rates"]
            if isinstance(rates, str):
                rates = json.loads(rates)
            out.setdefault(svc, []).append(rates)
        return out

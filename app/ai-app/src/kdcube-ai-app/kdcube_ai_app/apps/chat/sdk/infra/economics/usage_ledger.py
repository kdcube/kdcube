# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/ingress/economics/usage_ledger.py  (SQL-backed usage accounting)
"""
SQL-backed per-user usage/cost ledger.

The authoritative source for "cost per user" is a transactional Postgres table,
`<schema>.llm_usage_events` (one row per accounting event, with the per-model
input/output token split and computed USD cost), aggregated by the plain view
`<schema>.llm_usage_by_user_model` (user / model / day / month). This replaces
scanning the brittle file-based accounting store, which is only written
opportunistically and was the cause of dashboards showing "$0 / no spend".

Two roles:
  * write side  -> UsageLedgerStore.record_event(): called from the accounting
    write path (see SQLUsageAccountingStorage); idempotent on event_id.
  * read side   -> UsageLedgerStore.cost_by_user() / cost_for_user(): the SQL
    that powers /api/economics/admin/cost-by-user and /me/cost-breakdown. The
    query lives in the function, as required.
"""
import logging
from typing import Any, Dict, List, Optional

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema
from kdcube_ai_app.infra.accounting.usage import compute_rollup_cost

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Self-healing schema bootstrap
# --------------------------------------------------------------------------- #
# The authoritative copy of this DDL lives in
# ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql (rendered with the
# project schema substituted for <SCHEMA>). The deploy-time SQL step is fragile
# and has been observed NOT to run on some targets, leaving the ledger tables
# absent -> every mirror insert raised asyncpg.UndefinedTableError and was
# dropped fail-safe (cost-per-user silently under-reported). To make the runtime
# correct wherever the patched code runs, we keep an identical, fully idempotent
# (IF NOT EXISTS / CREATE OR REPLACE) copy here and apply it once per process at
# sink-attach time. Keep it in sync with the SQL file.
_LEDGER_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {schema}.llm_usage_events (
    id                 BIGSERIAL PRIMARY KEY,
    tenant             VARCHAR(255) NOT NULL,
    project            VARCHAR(255) NOT NULL,
    user_id            VARCHAR(255),
    request_id         VARCHAR(255),
    conversation_id    VARCHAR(255),
    turn_id            VARCHAR(255),
    agent              VARCHAR(255),
    bundle_id          VARCHAR(255),
    service_type       VARCHAR(64) NOT NULL,
    provider           VARCHAR(128),
    model              VARCHAR(255),
    input_tokens       BIGINT NOT NULL DEFAULT 0,
    output_tokens      BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens  BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    embedding_tokens   BIGINT NOT NULL DEFAULT 0,
    search_queries     BIGINT NOT NULL DEFAULT 0,
    requests           BIGINT NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(18,6) NOT NULL DEFAULT 0,
    event_id           VARCHAR(128),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_usage_event_id
  ON {schema}.llm_usage_events (event_id) WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_project_time
  ON {schema}.llm_usage_events (tenant, project, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time
  ON {schema}.llm_usage_events (tenant, project, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model_time
  ON {schema}.llm_usage_events (tenant, project, model, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_request
  ON {schema}.llm_usage_events (tenant, project, request_id);

CREATE TABLE IF NOT EXISTS {schema}.model_pricing (
    id             BIGSERIAL PRIMARY KEY,
    service_type   VARCHAR(64) NOT NULL,
    provider       VARCHAR(128) NOT NULL,
    model          VARCHAR(255) NOT NULL,
    rates          JSONB NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
  ON {schema}.model_pricing (service_type, provider, model, effective_from DESC);

CREATE OR REPLACE VIEW {schema}.llm_usage_by_user_model AS
SELECT
    tenant,
    project,
    user_id,
    service_type,
    provider,
    model,
    date_trunc('day', created_at)   AS day,
    date_trunc('month', created_at) AS month,
    sum(input_tokens)       AS input_tokens,
    sum(output_tokens)      AS output_tokens,
    sum(cache_read_tokens)  AS cache_read_tokens,
    sum(cache_write_tokens) AS cache_write_tokens,
    sum(embedding_tokens)   AS embedding_tokens,
    sum(search_queries)     AS search_queries,
    sum(requests)           AS requests,
    sum(cost_usd)           AS cost_usd,
    count(*)                AS events
FROM {schema}.llm_usage_events
GROUP BY tenant, project, user_id, service_type, provider, model,
         date_trunc('day', created_at), date_trunc('month', created_at);
"""


async def ensure_ledger_schema(pg_pool, *, tenant: str, project: str) -> str:
    """Idempotently create the usage-ledger tables/indexes/view for a project schema.

    Mirrors the DDL appended to deploy-kdcube-proj-schema.sql, schema-qualified
    via project_schema(tenant, project). Safe to call repeatedly and concurrently
    (every statement is IF NOT EXISTS / CREATE OR REPLACE). Returns the schema name.

    Callers should treat failures as non-fatal (the mirror write path is already
    fail-safe); this raises so callers can decide whether to log/skip the lazy
    pricing seed.
    """
    schema = _project_schema(tenant, project)
    ddl = _LEDGER_DDL_TEMPLATE.format(schema=schema)
    async with pg_pool.acquire() as conn:
        # asyncpg executes a multi-statement string via the simple query protocol
        # when no args are passed, which is exactly what we want for DDL.
        await conn.execute(ddl)
    return schema


def _spent_from_usage(usage: dict) -> dict:
    """Map a ServiceUsage-shaped dict to the rollup 'spent' keys compute_rollup_cost reads."""
    usage = usage or {}
    cache_creation = usage.get("cache_creation")
    spent = {
        "input": int(usage.get("input_tokens", 0) or 0),
        "output": int(usage.get("output_tokens", 0) or 0),
        "cache_read": int(usage.get("cache_read_tokens", 0) or 0),
        "tokens": int(usage.get("embedding_tokens", 0) or 0),
        "search_queries": int(usage.get("search_queries", 0) or 0),
        "search_results": int(usage.get("search_results", 0) or 0),
    }
    if isinstance(cache_creation, dict):
        spent["cache_5m_write"] = int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0)
        spent["cache_1h_write"] = int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0)
    else:
        spent["cache_creation"] = int(usage.get("cache_creation_tokens", 0) or 0)
    return spent


def cost_usd_for_event(*, service_type: str, provider: str, model: str, usage: dict,
                       pricing_table: Optional[dict] = None) -> float:
    """Real USD cost for a single usage event.

    Prefers the provider-REPORTED cost (usage.cost_usd) when present and positive
    -- that is the ground truth the provider billed. Otherwise computes from the
    price table via the canonical engine. `pricing_table`, when given, is a
    price_table()-shaped dict resolved as-of the event's time (see
    ModelPricingStore); when omitted, the current code price table is used.
    """
    reported = (usage or {}).get("cost_usd")
    if reported is not None:
        try:
            v = float(reported)
            if v > 0:
                return round(v, 6)
        except (TypeError, ValueError):
            pass
    rollup = [{"service": service_type, "provider": provider, "model": model,
               "spent": _spent_from_usage(usage)}]
    est = compute_rollup_cost(rollup, pricing_table=pricing_table) or {}
    return round(float(est.get("total_cost_usd", 0.0) or 0.0), 6)


class UsageLedgerStore:
    """Read/write access to <schema>.llm_usage_events and its aggregation view."""

    def __init__(self, pg_pool, *, tenant: str, project: str):
        self.pool = pg_pool
        self.tenant = tenant
        self.project = project
        self.schema = _project_schema(tenant, project)

    # ----------------------------- write side -----------------------------

    async def record_event(
        self,
        *,
        event_id: Optional[str],
        user_id: Optional[str],
        request_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        agent: Optional[str] = None,
        bundle_id: Optional[str] = None,
        service_type: str,
        provider: Optional[str],
        model: Optional[str],
        usage: dict,
        created_at=None,
        pricing_table: Optional[dict] = None,
    ) -> None:
        """Insert one usage row. Idempotent on event_id (ON CONFLICT DO NOTHING)."""
        spent = _spent_from_usage(usage)
        cost = cost_usd_for_event(service_type=service_type, provider=provider or "", model=model or "",
                                  usage=usage, pricing_table=pricing_table)
        cache_write = int(spent.get("cache_5m_write", 0)) + int(spent.get("cache_1h_write", 0)) + int(spent.get("cache_creation", 0))

        sql = f"""
            INSERT INTO {self.schema}.llm_usage_events (
                tenant, project, user_id, request_id, conversation_id, turn_id, agent, bundle_id,
                service_type, provider, model,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                embedding_tokens, search_queries, requests, cost_usd, event_id, created_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,
                $9,$10,$11,
                $12,$13,$14,$15,
                $16,$17,$18,$19,$20, COALESCE($21, NOW())
            )
            ON CONFLICT (event_id) WHERE event_id IS NOT NULL DO NOTHING
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                sql,
                self.tenant, self.project, user_id, request_id, conversation_id, turn_id, agent, bundle_id,
                str(service_type), provider, model,
                int(spent.get("input", 0)), int(spent.get("output", 0)), int(spent.get("cache_read", 0)), int(cache_write),
                int(spent.get("tokens", 0)), int(spent.get("search_queries", 0)),
                int(usage.get("requests", 0) or 0), float(cost), event_id, created_at,
            )

    # ----------------------------- read side -----------------------------

    @staticmethod
    def _assemble(rows: List[dict]) -> Dict[str, Dict[str, Any]]:
        """Group flat (user, model) rows into {user_id: {total_cost_usd, by_model[], tokens{}, event_count}}."""
        users: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            uid = r["user_id"] or "(unknown)"
            u = users.get(uid)
            if u is None:
                u = {"user_id": uid, "total_cost_usd": 0.0, "by_model": [],
                     "tokens": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                                "cache_write_tokens": 0, "embedding_tokens": 0},
                     "event_count": 0}
                users[uid] = u
            cost = round(float(r["cost_usd"] or 0.0), 6)
            inp = int(r["input_tokens"] or 0)
            out = int(r["output_tokens"] or 0)
            u["total_cost_usd"] = round(u["total_cost_usd"] + cost, 6)
            u["tokens"]["input_tokens"] += inp
            u["tokens"]["output_tokens"] += out
            u["tokens"]["embedding_tokens"] += int(r["embedding_tokens"] or 0)
            u["event_count"] += int(r["requests"] or 0)
            u["by_model"].append({
                "service": r["service_type"], "provider": r["provider"], "model": r["model"],
                "cost_usd": cost, "input_tokens": inp, "output_tokens": out,
                "embedding_tokens": int(r["embedding_tokens"] or 0),
            })
        for u in users.values():
            u["by_model"].sort(key=lambda m: m["cost_usd"], reverse=True)
        return users

    async def cost_by_user(self, *, date_from: str, date_to: str) -> Dict[str, Any]:
        """Per-user true spend across the workspace, sorted by cost descending."""
        sql = f"""
            SELECT user_id, service_type, provider, model,
                   SUM(cost_usd)       AS cost_usd,
                   SUM(input_tokens)   AS input_tokens,
                   SUM(output_tokens)  AS output_tokens,
                   SUM(embedding_tokens) AS embedding_tokens,
                   SUM(requests)       AS requests
            FROM {self.schema}.llm_usage_by_user_model
            WHERE tenant=$1 AND project=$2
              AND day >= $3::date AND day < ($4::date + INTERVAL '1 day')
            GROUP BY user_id, service_type, provider, model
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, self.tenant, self.project, date_from, date_to)
        users = list(self._assemble([dict(r) for r in rows]).values())
        users.sort(key=lambda u: u["total_cost_usd"], reverse=True)
        return {
            "date_from": date_from,
            "date_to": date_to,
            "total_cost_usd": round(sum(u["total_cost_usd"] for u in users), 6),
            "total_users": len(users),
            "users": users,
        }

    async def cost_for_user(self, *, user_id: str, date_from: str, date_to: str) -> Dict[str, Any]:
        """True spend for a single user, broken down by model."""
        sql = f"""
            SELECT user_id, service_type, provider, model,
                   SUM(cost_usd)       AS cost_usd,
                   SUM(input_tokens)   AS input_tokens,
                   SUM(output_tokens)  AS output_tokens,
                   SUM(embedding_tokens) AS embedding_tokens,
                   SUM(requests)       AS requests
            FROM {self.schema}.llm_usage_by_user_model
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND day >= $4::date AND day < ($5::date + INTERVAL '1 day')
            GROUP BY user_id, service_type, provider, model
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, self.tenant, self.project, user_id, date_from, date_to)
        users = self._assemble([dict(r) for r in rows])
        u = users.get(user_id) or {
            "user_id": user_id, "total_cost_usd": 0.0, "by_model": [],
            "tokens": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                       "cache_write_tokens": 0, "embedding_tokens": 0},
            "event_count": 0,
        }
        u = dict(u)
        u.update({"date_from": date_from, "date_to": date_to})
        return u

    async def has_data(self, *, date_from: str, date_to: str) -> bool:
        """True if any usage rows exist for the window (used to decide SQL vs file fallback)."""
        sql = f"""
            SELECT 1 FROM {self.schema}.llm_usage_events
            WHERE tenant=$1 AND project=$2
              AND created_at >= $3::date AND created_at < ($4::date + INTERVAL '1 day')
            LIMIT 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, self.tenant, self.project, date_from, date_to)
        return row is not None


class SQLUsageAccountingStorage:
    """IAccountingStorage wrapper that mirrors each accounting event into the SQL
    usage ledger, in addition to the wrapped (file) storage.

    Fail-safe by design: the wrapped storage write happens first and its result is
    returned; a failure in the SQL mirror is swallowed (logged at debug) so usage
    accounting can never block or fail a turn. Tenant/project are taken from each
    event, so it is correct in a multi-tenant process.
    """

    def __init__(self, inner, pg_pool):
        self._inner = inner
        self._pool = pg_pool

    def __getattr__(self, name):
        # Delegate any non-overridden attribute/method (e.g. turn_cache, flush)
        # to the wrapped storage so this wrapper is a drop-in for FileAccountingStorage.
        return getattr(self._inner, name)

    async def store_event(self, event) -> bool:
        ok = True
        try:
            ok = await self._inner.store_event(event)
        finally:
            try:
                await self._mirror(event)
            except Exception:
                logger.warning("SQL usage sink: mirror insert failed (usage ledger row "
                               "not written; cost-per-user may under-report)", exc_info=True)
        return ok

    async def _mirror(self, event) -> None:
        d = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        service = str(d.get("service_type") or "").strip()
        if service not in ("llm", "embedding", "web_search"):
            return
        ctx = d.get("context") or {}
        tenant = d.get("tenant_id") or ctx.get("tenant_id")
        project = d.get("project_id") or ctx.get("project_id")
        if not tenant or not project:
            return
        store = UsageLedgerStore(self._pool, tenant=str(tenant), project=str(project))
        await store.record_event(
            event_id=d.get("event_id"),
            user_id=d.get("user_id") or ctx.get("user_id"),
            request_id=d.get("request_id") or ctx.get("request_id"),
            conversation_id=ctx.get("conversation_id") or d.get("conversation_id"),
            turn_id=ctx.get("turn_id") or d.get("turn_id"),
            agent=(d.get("metadata") or {}).get("agent_name") or ctx.get("component"),
            bundle_id=d.get("app_bundle_id") or ctx.get("app_bundle_id"),
            service_type=service,
            provider=d.get("provider"),
            model=d.get("model_or_service"),
            usage=d.get("usage") or {},
        )


async def backfill_usage_ledger(
    *,
    pg_pool,
    storage_backend,
    tenant: str,
    project: str,
    date_from: str,
    date_to: str,
    base_path: str = "accounting",
    agg_base: str = "analytics",
    pricing_table: Optional[dict] = None,
) -> Dict[str, int]:
    """Import existing file accounting events into <schema>.llm_usage_events.

    Idempotent (ON CONFLICT event_id DO NOTHING) -- safe to re-run. Each row keeps
    the original event timestamp so historical time-window queries stay correct.
    Returns {"scanned": <files seen>, "recorded": <usage rows written>}.
    """
    import json
    from datetime import datetime
    from kdcube_ai_app.infra.accounting.calculator import RateCalculator, AccountingQuery

    calc = RateCalculator(storage_backend, base_path=base_path, agg_base=agg_base)
    store = UsageLedgerStore(pg_pool, tenant=tenant, project=project)
    q = AccountingQuery(tenant_id=tenant, project_id=project, date_from=date_from, date_to=date_to)

    def _parse_ts(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    scanned = 0
    recorded = 0
    async for path in calc._iter_event_paths(q):
        scanned += 1
        try:
            raw = await calc.fs.read_text_a(path)
            ev = json.loads(raw)
        except Exception:
            logger.debug("backfill: unreadable event %s", path, exc_info=True)
            continue
        service = str(ev.get("service_type") or "").strip()
        if service not in ("llm", "embedding", "web_search"):
            continue
        ctx = ev.get("context") or {}
        try:
            await store.record_event(
                event_id=ev.get("event_id"),
                user_id=ev.get("user_id") or ctx.get("user_id"),
                request_id=ev.get("request_id") or ctx.get("request_id"),
                conversation_id=ctx.get("conversation_id") or ev.get("conversation_id"),
                turn_id=ctx.get("turn_id") or ev.get("turn_id"),
                agent=(ev.get("metadata") or {}).get("agent_name") or ctx.get("component"),
                bundle_id=ev.get("app_bundle_id") or ctx.get("app_bundle_id"),
                service_type=service,
                provider=ev.get("provider"),
                model=ev.get("model_or_service"),
                usage=ev.get("usage") or {},
                created_at=_parse_ts(ev.get("timestamp")),
                pricing_table=pricing_table,
            )
            recorded += 1
        except Exception:
            logger.debug("backfill: insert failed for %s", path, exc_info=True)
    logger.info("backfill_usage_ledger %s/%s [%s..%s]: scanned=%d recorded=%d",
                tenant, project, date_from, date_to, scanned, recorded)

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/user_budget.py

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from decimal import Decimal
import asyncpg
from redis.asyncio import Redis

from kdcube_ai_app.infra.namespaces import REDIS
from kdcube_ai_app.infra.redis.client import get_async_redis_client
from kdcube_ai_app.apps.chat.sdk.config import resolve_asyncpg_ssl
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema

logger = logging.getLogger(__name__)


def _usd_to_cents(usd: float) -> int:
    return int(round(float(usd) * 100))


def _cents_to_usd(cents: int) -> float:
    return float(cents) / 100.0


# -----------------------------------------------------------------------------
# Compatibility snapshot (flattened) used widely by RL / run() today.
# Backed by TWO tables:
#   - user_plan_overrides
#   - user_lifetime_credits
# -----------------------------------------------------------------------------
@dataclass
class UserPlanBalance:
    # Identification
    tenant: str
    project: str
    user_id: str

    # Plan Override Limits (NULL = use base plan)
    max_concurrent: Optional[int] = None
    requests_per_day: Optional[int] = None
    requests_per_month: Optional[int] = None
    total_requests: Optional[int] = None
    tokens_per_hour: Optional[int] = None
    tokens_per_day: Optional[int] = None
    tokens_per_month: Optional[int] = None

    # Plan override expiry
    expires_at: Optional[datetime] = None

    # Plan override grant tracking (NOT personal credits purchase)
    grant_id: Optional[str] = None
    grant_amount_usd: Optional[float] = None
    grant_notes: Optional[str] = None

    # Personal credits (USD-native, cents)
    purchased_cents: Optional[int] = None
    spent_cents: Optional[int] = None

    # Lifetime USD aggregate + last purchase snapshot (personal credits)
    lifetime_usd_purchased: Optional[float] = None
    last_purchase_id: Optional[str] = None
    last_purchase_amount_usd: Optional[float] = None
    last_purchase_notes: Optional[str] = None

    active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # ---------- plan override semantics ----------
    def is_plan_override_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def has_plan_override(self) -> bool:
        return any([
            self.max_concurrent is not None,
            self.requests_per_day is not None,
            self.requests_per_month is not None,
            self.total_requests is not None,
            self.tokens_per_hour is not None,
            self.tokens_per_day is not None,
            self.tokens_per_month is not None,
            ])

    def plan_override_is_active(self) -> bool:
        return self.active and self.has_plan_override() and (not self.is_plan_override_expired())

    # ---------- personal credits semantics ----------
    def has_lifetime_budget(self) -> bool:
        # "Budget info exists" (can be positive, zero, or negative)
        return (
                self.purchased_cents is not None
                or self.spent_cents is not None
                or self.lifetime_usd_purchased is not None
        )

    # Note: do NOT mix the two lifecycles anymore.
    # This is *not* "plan override is valid". It's "any user-budget info exists and active".
    def is_valid(self) -> bool:
        if not self.active:
            return False
        return self.plan_override_is_active() or self.has_lifetime_budget()


@dataclass
class CreditReservation:
    tenant: str
    project: str
    user_id: str
    reservation_id: str
    usd_reserved_cents: int
    status: str
    expires_at: datetime
    bundle_id: Optional[str] = None
    actual_spent_cents: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    notes: Optional[str] = None


class UserPlanBalanceSnapshotManager:
    """
    Read-only snapshot: single SQL that joins
      - user_plan_overrides
      - user_lifetime_credits
    and returns the flattened UserPlanBalance dataclass.
    """
    OVERRIDE_TABLE = "user_plan_overrides"
    CREDITS_TABLE = "user_lifetime_credits"

    @staticmethod
    def _schema(tenant: str, project: str) -> str:
        return _project_schema(tenant, project)

    def __init__(self, pg_pool: Optional[asyncpg.Pool] = None):
        self._pg_pool = pg_pool

    def set_pg_pool(self, pg_pool: asyncpg.Pool) -> None:
        self._pg_pool = pg_pool

    @staticmethod
    def _f(x):
        # asyncpg may return Decimal for NUMERIC
        if x is None:
            return None
        if isinstance(x, Decimal):
            return float(x)
        return float(x)

    async def get_user_plan_balance(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            include_expired: bool = False,
    ) -> Optional[UserPlanBalance]:
        if not self._pg_pool:
            return None

        sql = f"""
        WITH
          o AS (
            SELECT *
            FROM {self._schema(tenant, project)}.{self.OVERRIDE_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND active=TRUE
            LIMIT 1
          ),
          c AS (
            SELECT *
            FROM {self._schema(tenant, project)}.{self.CREDITS_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND active=TRUE
            LIMIT 1
          )
        SELECT
          COALESCE(o.tenant, c.tenant)   AS tenant,
          COALESCE(o.project, c.project) AS project,
          COALESCE(o.user_id, c.user_id) AS user_id,

          -- If include_expired=false, null out expired override fields (credits still show).
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.max_concurrent END     AS max_concurrent,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.requests_per_day END   AS requests_per_day,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.requests_per_month END AS requests_per_month,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.total_requests END     AS total_requests,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.tokens_per_hour END    AS tokens_per_hour,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.tokens_per_day END     AS tokens_per_day,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.tokens_per_month END   AS tokens_per_month,

          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.expires_at END         AS expires_at,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.grant_id END           AS grant_id,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.grant_amount_usd END   AS grant_amount_usd,
          CASE WHEN $4 OR o.expires_at IS NULL OR o.expires_at > NOW() THEN o.grant_notes END        AS grant_notes,

          c.purchased_cents           AS purchased_cents,
          c.spent_cents               AS spent_cents,
          c.lifetime_usd_purchased    AS lifetime_usd_purchased,
          c.last_purchase_id          AS last_purchase_id,
          c.last_purchase_amount_usd  AS last_purchase_amount_usd,
          c.last_purchase_notes       AS last_purchase_notes,

          TRUE AS active,

          CASE
            WHEN o.created_at IS NULL THEN c.created_at
            WHEN c.created_at IS NULL THEN o.created_at
            ELSE LEAST(o.created_at, c.created_at)
          END AS created_at,

          CASE
            WHEN o.updated_at IS NULL THEN c.updated_at
            WHEN c.updated_at IS NULL THEN o.updated_at
            ELSE GREATEST(o.updated_at, c.updated_at)
          END AS updated_at

        FROM o
        FULL OUTER JOIN c
          ON o.tenant=c.tenant AND o.project=c.project AND o.user_id=c.user_id
        LIMIT 1
        """

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, include_expired)

        if not row:
            return None

        d = dict(row)

        # Normalize numeric -> float
        d["grant_amount_usd"] = self._f(d.get("grant_amount_usd"))
        d["lifetime_usd_purchased"] = self._f(d.get("lifetime_usd_purchased"))
        d["last_purchase_amount_usd"] = self._f(d.get("last_purchase_amount_usd"))

        return UserPlanBalance(**d)

# -----------------------------------------------------------------------------
# PlanOverrideManager  (table: user_plan_overrides)
# -----------------------------------------------------------------------------
class PlanOverrideManager:
    TABLE = "user_plan_overrides"

    @staticmethod
    def _schema(tenant: str, project: str) -> str:
        return _project_schema(tenant, project)

    def __init__(
            self,
            pg_pool: Optional[asyncpg.Pool] = None,
            redis: Optional[Redis] = None,
            *,
            cache_ttl: int = 10,
            cache_namespace: str = REDIS.ECONOMICS.PLAN_BALANCE_CACHE,
    ):
        self._pg_pool = pg_pool
        self._redis = redis
        self.cache_ttl = cache_ttl
        self.cache_ns = cache_namespace + ":plan_override"

        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        self._settings = get_settings()

        self._owns_pool = pg_pool is None
        self._owns_redis = redis is None

    async def init(self, *, redis_url: Optional[str] = None):
        if not self._pg_pool:
            self._pg_pool = await asyncpg.create_pool(
                host=self._settings.PGHOST,
                port=self._settings.PGPORT,
                user=self._settings.PGUSER,
                password=self._settings.PGPASSWORD,
                database=self._settings.PGDATABASE,
                ssl=resolve_asyncpg_ssl(self._settings),
            )
            self._owns_pool = True

        if not self._redis and redis_url:
            self._redis = get_async_redis_client(redis_url)
            self._owns_redis = True

    async def close(self):
        if self._owns_pool and self._pg_pool:
            await self._pg_pool.close()
        if self._owns_redis and self._redis:
            if not getattr(self._redis, "_kdcube_shared", False):
                await self._redis.close()

    def _cache_key(self, tenant: str, project: str, user_id: str) -> str:
        return f"{self.cache_ns}:{tenant}:{project}:{user_id}"

    async def _invalidate(self, tenant: str, project: str, user_id: str) -> None:
        if self._redis:
            try:
                await self._redis.delete(self._cache_key(tenant, project, user_id))
            except Exception:
                pass

    async def get_user_plan_override(
            self, *, tenant: str, project: str, user_id: str, include_expired: bool = False
    ) -> Optional[dict]:
        """
        Returns raw dict row from user_plan_overrides or None.
        """
        # Redis
        if self._redis:
            try:
                cached = await self._redis.get(self._cache_key(tenant, project, user_id))
                if cached:
                    raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
                    data = json.loads(raw)
                    for k in ["expires_at", "created_at", "updated_at"]:
                        if data.get(k):
                            data[k] = datetime.fromisoformat(data[k])
                    if not include_expired and data.get("expires_at") and datetime.now(timezone.utc) >= data["expires_at"]:
                        return None
                    return data
            except Exception as e:
                logger.warning("Redis plan_override read error: %s", e)

        if not self._pg_pool:
            return None

        expired_filter = "" if include_expired else "AND (expires_at IS NULL OR expires_at > NOW())"

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self._schema(tenant, project)}.{self.TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND active=TRUE
                  {expired_filter}
                LIMIT 1
            """, tenant, project, user_id)

        if not row:
            return None

        data = dict(row)

        if self._redis:
            try:
                out = dict(data)
                for k in ["expires_at", "created_at", "updated_at"]:
                    if out.get(k):
                        out[k] = out[k].isoformat()
                await self._redis.setex(self._cache_key(tenant, project, user_id), self.cache_ttl, json.dumps(out))
            except Exception:
                pass

        return data

    async def update_user_plan_override(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            max_concurrent: Optional[int] = None,
            requests_per_day: Optional[int] = None,
            requests_per_month: Optional[int] = None,
            total_requests: Optional[int] = None,
            tokens_per_hour: Optional[int] = None,
            tokens_per_day: Optional[int] = None,
            tokens_per_month: Optional[int] = None,
            expires_at: Optional[datetime] = None,
            grant_id: Optional[str] = None,
            grant_amount_usd: Optional[float] = None,
            grant_notes: Optional[str] = None,
    ) -> dict:
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO {self._schema(tenant, project)}.{self.TABLE} (
                    tenant, project, user_id,
                    max_concurrent, requests_per_day, requests_per_month, total_requests,
                    tokens_per_hour, tokens_per_day, tokens_per_month,
                    expires_at,
                    grant_id, grant_amount_usd, grant_notes
                ) VALUES (
                    $1,$2,$3,
                    $4,$5,$6,$7,
                    $8,$9,$10,
                    $11,
                    $12,$13,$14
                )
                ON CONFLICT (tenant, project, user_id)
                DO UPDATE SET
                    max_concurrent     = COALESCE(EXCLUDED.max_concurrent, {self._schema(tenant, project)}.{self.TABLE}.max_concurrent),
                    requests_per_day   = COALESCE(EXCLUDED.requests_per_day, {self._schema(tenant, project)}.{self.TABLE}.requests_per_day),
                    requests_per_month = COALESCE(EXCLUDED.requests_per_month, {self._schema(tenant, project)}.{self.TABLE}.requests_per_month),
                    total_requests     = COALESCE(EXCLUDED.total_requests, {self._schema(tenant, project)}.{self.TABLE}.total_requests),
                    tokens_per_hour    = COALESCE(EXCLUDED.tokens_per_hour, {self._schema(tenant, project)}.{self.TABLE}.tokens_per_hour),
                    tokens_per_day     = COALESCE(EXCLUDED.tokens_per_day, {self._schema(tenant, project)}.{self.TABLE}.tokens_per_day),
                    tokens_per_month   = COALESCE(EXCLUDED.tokens_per_month, {self._schema(tenant, project)}.{self.TABLE}.tokens_per_month),
                    expires_at         = COALESCE(EXCLUDED.expires_at, {self._schema(tenant, project)}.{self.TABLE}.expires_at),
                    grant_id           = COALESCE(EXCLUDED.grant_id, {self._schema(tenant, project)}.{self.TABLE}.grant_id),
                    grant_amount_usd   = COALESCE(EXCLUDED.grant_amount_usd, {self._schema(tenant, project)}.{self.TABLE}.grant_amount_usd),
                    grant_notes        = COALESCE(EXCLUDED.grant_notes, {self._schema(tenant, project)}.{self.TABLE}.grant_notes),
                    active             = TRUE,
                    updated_at         = NOW()
                RETURNING *
            """,
                                      tenant, project, user_id,
                                      max_concurrent, requests_per_day, requests_per_month, total_requests,
                                      tokens_per_hour, tokens_per_day, tokens_per_month,
                                      expires_at,
                                      grant_id, grant_amount_usd, grant_notes)

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    async def deactivate_plan_override(self, *, tenant: str, project: str, user_id: str) -> None:
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        async with self._pg_pool.acquire() as conn:
            await conn.execute(f"""
                UPDATE {self._schema(tenant, project)}.{self.TABLE}
                SET active=FALSE, updated_at=NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3
            """, tenant, project, user_id)

        await self._invalidate(tenant, project, user_id)


# -----------------------------------------------------------------------------
# UserCreditsManager  (table: user_lifetime_credits + user_credit_reservations)
# -----------------------------------------------------------------------------
class UserCreditsManager:
    TABLE = "user_lifetime_credits"
    RESERVATIONS_TABLE = "user_credit_reservations"

    @staticmethod
    def _schema(tenant: str, project: str) -> str:
        return _project_schema(tenant, project)

    def __init__(
            self,
            pg_pool: Optional[asyncpg.Pool] = None,
            redis: Optional[Redis] = None,
            *,
            cache_ttl: int = 10,
            cache_namespace: str = REDIS.ECONOMICS.PLAN_BALANCE_CACHE,
    ):
        self._pg_pool = pg_pool
        self._redis = redis
        self.cache_ttl = cache_ttl
        self.cache_ns = cache_namespace + ":user_credits"

        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        self._settings = get_settings()

        self._owns_pool = pg_pool is None
        self._owns_redis = redis is None

    async def init(self, *, redis_url: Optional[str] = None):
        if not self._pg_pool:
            self._pg_pool = await asyncpg.create_pool(
                host=self._settings.PGHOST,
                port=self._settings.PGPORT,
                user=self._settings.PGUSER,
                password=self._settings.PGPASSWORD,
                database=self._settings.PGDATABASE,
                ssl=resolve_asyncpg_ssl(self._settings),
            )
            self._owns_pool = True

        if not self._redis and redis_url:
            self._redis = get_async_redis_client(redis_url)
            self._owns_redis = True

    async def close(self):
        if self._owns_pool and self._pg_pool:
            await self._pg_pool.close()
        if self._owns_redis and self._redis:
            if not getattr(self._redis, "_kdcube_shared", False):
                await self._redis.close()

    def _cache_key(self, tenant: str, project: str, user_id: str) -> str:
        return f"{self.cache_ns}:{tenant}:{project}:{user_id}"

    async def _invalidate(self, tenant: str, project: str, user_id: str) -> None:
        if self._redis:
            try:
                await self._redis.delete(self._cache_key(tenant, project, user_id))
            except Exception:
                pass

    async def get_user_credits(self, *, tenant: str, project: str, user_id: str) -> Optional[dict]:
        # Redis
        if self._redis:
            try:
                cached = await self._redis.get(self._cache_key(tenant, project, user_id))
                if cached:
                    raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
                    data = json.loads(raw)
                    for k in ["created_at", "updated_at"]:
                        if data.get(k):
                            data[k] = datetime.fromisoformat(data[k])
                    return data
            except Exception as e:
                logger.warning("Redis user_credits read error: %s", e)

        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self._schema(tenant, project)}.{self.TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND active=TRUE
                LIMIT 1
            """, tenant, project, user_id)

        if not row:
            return None

        data = dict(row)

        if self._redis:
            try:
                out = dict(data)
                for k in ["created_at", "updated_at"]:
                    if out.get(k):
                        out[k] = out[k].isoformat()
                await self._redis.setex(self._cache_key(tenant, project, user_id), self.cache_ttl, json.dumps(out))
            except Exception:
                pass

        return data

    # ---------------- credits mutations ----------------

    async def add_lifetime_credits(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            usd_amount: float,
            purchase_id: Optional[str] = None,
            notes: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        """Add `usd_amount` of credits. Increments purchased_cents (authoritative)
        and lifetime_usd_purchased (reporting)."""
        if usd_amount <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "purchased_cents": 0, "spent_cents": 0, "lifetime_usd_purchased": 0,
            }

        purchased_cents = _usd_to_cents(usd_amount)
        sql = f"""
          INSERT INTO {self._schema(tenant, project)}.{self.TABLE} (
            tenant, project, user_id,
            purchased_cents,
            lifetime_usd_purchased,
            last_purchase_id,
            last_purchase_amount_usd,
            last_purchase_notes
          ) VALUES ($1,$2,$3,$5,$4,$6,$4,$7)
          ON CONFLICT (tenant, project, user_id)
          DO UPDATE SET
            purchased_cents           = {self._schema(tenant, project)}.{self.TABLE}.purchased_cents + EXCLUDED.purchased_cents,
            lifetime_usd_purchased    = {self._schema(tenant, project)}.{self.TABLE}.lifetime_usd_purchased + EXCLUDED.lifetime_usd_purchased,
            last_purchase_id          = EXCLUDED.last_purchase_id,
            last_purchase_amount_usd  = EXCLUDED.last_purchase_amount_usd,
            last_purchase_notes       = EXCLUDED.last_purchase_notes,
            active                    = TRUE,
            updated_at                = NOW()
          RETURNING *
        """

        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, float(usd_amount), int(purchased_cents), purchase_id, notes)
        else:
            if not self._pg_pool:
                raise RuntimeError("PostgreSQL pool not initialized")
            async with self._pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id, float(usd_amount), int(purchased_cents), purchase_id, notes)

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    async def refund_lifetime_credits(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            usd_amount: float,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        """
        Refund (remove) `usd_amount` of credits. Requires available_cents
        (purchased - spent - reserved) >= the refund amount.
        """
        if usd_amount <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "purchased_cents": 0, "spent_cents": 0, "lifetime_usd_purchased": 0,
            }

        refund_cents = _usd_to_cents(usd_amount)

        async def _apply(c: asyncpg.Connection) -> asyncpg.Record:
            bal = await c.fetchrow(f"""
                SELECT purchased_cents, spent_cents
                FROM {self._schema(tenant, project)}.{self.TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND active=TRUE
                FOR UPDATE
            """, tenant, project, user_id)
            if not bal:
                raise ValueError("lifetime credits not found")

            purchased_cents = int(bal["purchased_cents"] or 0)
            spent_cents = int(bal["spent_cents"] or 0)
            reserved_cents = await self._reserved_cents_sum(conn=c, tenant=tenant, project=project, user_id=user_id)
            available_cents = purchased_cents - spent_cents - reserved_cents
            if available_cents < int(refund_cents):
                raise ValueError(f"insufficient refundable credits: available_cents={available_cents}, requested_cents={int(refund_cents)}")

            row = await c.fetchrow(f"""
                UPDATE {self._schema(tenant, project)}.{self.TABLE}
                SET purchased_cents = GREATEST(purchased_cents - $5, 0),
                    lifetime_usd_purchased = GREATEST(lifetime_usd_purchased - $4, 0),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                RETURNING *
            """, tenant, project, user_id, float(usd_amount), int(refund_cents))
            return row

        if conn:
            row = await _apply(conn)
        else:
            if not self._pg_pool:
                raise RuntimeError("PostgreSQL pool not initialized")
            async with self._pg_pool.acquire() as c:
                async with c.transaction():
                    row = await _apply(c)

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    async def restore_lifetime_credits(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            usd_amount: float,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        """
        Restore (add back) `usd_amount` of credits after a failed refund.
        Does not alter last_purchase_* fields.
        """
        if usd_amount <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "purchased_cents": 0, "spent_cents": 0, "lifetime_usd_purchased": 0,
            }

        restore_cents = _usd_to_cents(usd_amount)
        sql = f"""
            INSERT INTO {self._schema(tenant, project)}.{self.TABLE} (
                tenant, project, user_id,
                purchased_cents,
                lifetime_usd_purchased,
                active
            ) VALUES ($1,$2,$3,$5,$4,TRUE)
            ON CONFLICT (tenant, project, user_id)
            DO UPDATE SET
                purchased_cents           = {self._schema(tenant, project)}.{self.TABLE}.purchased_cents + EXCLUDED.purchased_cents,
                lifetime_usd_purchased    = {self._schema(tenant, project)}.{self.TABLE}.lifetime_usd_purchased + EXCLUDED.lifetime_usd_purchased,
                active                    = TRUE,
                updated_at                = NOW()
            RETURNING *
        """

        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, float(usd_amount), int(restore_cents))
        else:
            if not self._pg_pool:
                raise RuntimeError("PostgreSQL pool not initialized")
            async with self._pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id, float(usd_amount), int(restore_cents))

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    # ---------------- reservation-aware balance ----------------

    async def _reserved_cents_sum(
            self,
            *,
            conn: asyncpg.Connection,
            tenant: str,
            project: str,
            user_id: str,
            exclude_reservation_id: Optional[str] = None,
    ) -> int:
        exclude_sql = ""
        args = [tenant, project, user_id]
        if exclude_reservation_id:
            args.append(exclude_reservation_id)
            exclude_sql = f"AND reservation_id <> ${len(args)}"

        v = await conn.fetchval(f"""
            SELECT COALESCE(SUM(usd_reserved_cents), 0)
            FROM {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND status='reserved'
              AND expires_at > NOW()
              {exclude_sql}
        """, *args)
        return int(v or 0)

    async def get_available_cents(self, *, tenant: str, project: str, user_id: str) -> Optional[int]:
        """
        Remaining AVAILABLE USD balance in cents (authoritative):
          purchased_cents - spent_cents - active reservations (usd_reserved_cents)
        """
        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT
                    COALESCE(ulc.purchased_cents, 0) AS purchased,
                    COALESCE(ulc.spent_cents, 0) AS spent,
                    COALESCE(rsv.reserved, 0) AS reserved
                FROM {self._schema(tenant, project)}.{self.TABLE} ulc
                LEFT JOIN (
                    SELECT tenant, project, user_id, COALESCE(SUM(usd_reserved_cents), 0) AS reserved
                    FROM {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND status='reserved'
                      AND expires_at > NOW()
                    GROUP BY tenant, project, user_id
                ) rsv
                  ON rsv.tenant=ulc.tenant AND rsv.project=ulc.project AND rsv.user_id=ulc.user_id
                WHERE ulc.tenant=$1 AND ulc.project=$2 AND ulc.user_id=$3
                  AND ulc.active=TRUE
            """, tenant, project, user_id)

        if not row:
            return None

        return int(row["purchased"]) - int(row["spent"]) - int(row["reserved"])

    async def get_lifetime_balance(self, *, tenant: str, project: str, user_id: str) -> Optional[int]:
        """
        Available balance expressed in reference tokens at the LIVE rate — for the
        split-admission feeder only (transient, never stored). Derived from the
        USD-native balance via get_available_cents(). Money callers should use
        get_available_cents() directly.
        """
        cents = await self.get_available_cents(tenant=tenant, project=project, user_id=user_id)
        if cents is None:
            return None
        from kdcube_ai_app.infra.accounting.usage import usd_per_reference_token
        rate = usd_per_reference_token()
        if rate <= 0:
            return 0
        return int(_cents_to_usd(cents) / rate)

    async def reserve_lifetime_credits(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            reservation_id: str,
            usd_cents: int,
            ttl_sec: int = 900,
            bundle_id: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> bool:
        """
        Hold `usd_cents` against the wallet for an in-flight turn. Checks
        available_cents = purchased - spent - other active holds.
        """
        if usd_cents <= 0:
            return True
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        exp = datetime.now(timezone.utc) + timedelta(seconds=int(ttl_sec))

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchrow(f"""
                    SELECT purchased_cents, spent_cents
                    FROM {self._schema(tenant, project)}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    return False

                purchased_cents = int(bal["purchased_cents"] or 0)
                spent_cents = int(bal["spent_cents"] or 0)

                reserved_cents = await self._reserved_cents_sum(conn=conn, tenant=tenant, project=project, user_id=user_id)
                available_cents = purchased_cents - spent_cents - reserved_cents
                if available_cents < int(usd_cents):
                    return False

                await conn.execute(f"""
                    INSERT INTO {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE} (
                        tenant, project, user_id,
                        reservation_id, bundle_id,
                        usd_reserved_cents, status, expires_at, notes
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,'reserved',$7,$8)
                    ON CONFLICT (tenant, project, user_id, reservation_id)
                    DO UPDATE SET
                        usd_reserved_cents = GREATEST(EXCLUDED.usd_reserved_cents, {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}.usd_reserved_cents),
                        status='reserved',
                        expires_at=EXCLUDED.expires_at,
                        bundle_id=EXCLUDED.bundle_id,
                        notes=EXCLUDED.notes,
                        updated_at=NOW()
                """, tenant, project, user_id, reservation_id, bundle_id, int(usd_cents), exp, notes)

        return True

    async def release_lifetime_token_reservation(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            reservation_id: str,
            reason: Optional[str] = None,
    ) -> None:
        if not self._pg_pool:
            return

        async with self._pg_pool.acquire() as conn:
            await conn.execute(f"""
                UPDATE {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                SET status='released',
                    released_at=NOW(),
                    notes = COALESCE(notes, '') || CASE WHEN $5::text IS NULL THEN '' ELSE (' | ' || $5::text) END,
                    updated_at=NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                  AND status='reserved'
            """, tenant, project, user_id, reservation_id, reason)

    async def commit_reserved_lifetime_credits(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            reservation_id: str,
            usd_cents: int,
    ) -> int:
        """
        Commit actual USD spend (cents) against a reservation. Charges spent_cents
        (capped by the hold and available balance). Returns the uncovered cents the
        wallet could not cover.
        """
        if usd_cents <= 0:
            return 0
        if not self._pg_pool:
            return usd_cents

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchrow(f"""
                    SELECT purchased_cents, spent_cents
                    FROM {self._schema(tenant, project)}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    # release reservation best-effort
                    await conn.execute(f"""
                        UPDATE {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                        SET status='released', released_at=NOW(), updated_at=NOW(),
                            notes=COALESCE(notes,'') || ' | commit: no_balance_row'
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                          AND status='reserved'
                    """, tenant, project, user_id, reservation_id)
                    return usd_cents

                res = await conn.fetchrow(f"""
                        SELECT status, usd_reserved_cents
                        FROM {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                    """, tenant, project, user_id, reservation_id)

                if not res or res.get("status") != "reserved":
                    # Missing or already finalized reservation; do not consume
                    return usd_cents

                reserved_cents = int(res.get("usd_reserved_cents") or 0)

                purchased_cents = int(bal["purchased_cents"] or 0)
                spent_cents = int(bal["spent_cents"] or 0)

                other_reserved_cents = await self._reserved_cents_sum(
                    conn=conn, tenant=tenant, project=project, user_id=user_id,
                    exclude_reservation_id=reservation_id,
                )
                available_cents = max(purchased_cents - spent_cents - other_reserved_cents, 0)
                charge_cents = min(int(usd_cents), int(available_cents), int(reserved_cents))

                if charge_cents > 0:
                    await conn.execute(f"""
                        UPDATE {self._schema(tenant, project)}.{self.TABLE}
                        SET spent_cents = spent_cents + $4,
                            updated_at=NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                    """, tenant, project, user_id, int(charge_cents))

                await conn.execute(f"""
                    UPDATE {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                    SET status='committed',
                        actual_spent_cents=$5,
                        committed_at=NOW(),
                        expires_at=NOW(),
                        updated_at=NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                """, tenant, project, user_id, reservation_id, int(charge_cents))

        await self._invalidate(tenant, project, user_id)
        return max(int(usd_cents) - int(charge_cents), 0)

    async def consume_lifetime_credits(self, *, tenant: str, project: str, user_id: str, usd_cents: int) -> int:
        """
        Reservation-free USD consumption (cents) WITHOUT a reservation_id.
        Will NOT steal credits reserved by other in-flight requests. Returns
        uncovered cents.
        """
        if usd_cents <= 0:
            return 0
        if not self._pg_pool:
            return usd_cents

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchrow(f"""
                    SELECT purchased_cents, spent_cents
                    FROM {self._schema(tenant, project)}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    return usd_cents

                purchased_cents = int(bal["purchased_cents"] or 0)
                spent_cents = int(bal["spent_cents"] or 0)

                reserved_cents = await self._reserved_cents_sum(conn=conn, tenant=tenant, project=project, user_id=user_id)
                available_cents = max(purchased_cents - spent_cents - reserved_cents, 0)

                charge_cents = min(int(usd_cents), int(available_cents))
                if charge_cents > 0:
                    await conn.execute(f"""
                        UPDATE {self._schema(tenant, project)}.{self.TABLE}
                        SET spent_cents = spent_cents + $4,
                            updated_at=NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                    """, tenant, project, user_id, int(charge_cents))

        await self._invalidate(tenant, project, user_id)
        return max(int(usd_cents) - int(charge_cents), 0)

    async def get_active_reserved_cents(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
    ) -> int:
        """Sum of currently active reservation holds, in cents."""
        if not self._pg_pool:
            return 0

        async with self._pg_pool.acquire() as conn:
            v = await conn.fetchval(f"""
                SELECT COALESCE(SUM(usd_reserved_cents), 0)
                FROM {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND status='reserved'
                  AND expires_at > NOW()
            """, tenant, project, user_id)

        return int(v or 0)

    async def list_active_reservations(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            limit: int = 50,
    ) -> list[CreditReservation]:
        """
        List active 'reserved' reservations (not committed/released), newest first.
        """
        if limit <= 0:
            return []
        if not self._pg_pool:
            return []

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT
                    tenant, project, user_id,
                    reservation_id,
                    bundle_id,
                    usd_reserved_cents,
                    actual_spent_cents,
                    status,
                    expires_at,
                    created_at,
                    updated_at,
                    committed_at,
                    released_at,
                    notes
                FROM {self._schema(tenant, project)}.{self.RESERVATIONS_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND status='reserved'
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT $4
            """, tenant, project, user_id, int(limit))

        out: list[CreditReservation] = []
        for r in rows:
            out.append(CreditReservation(**dict(r)))
        return out

class UserBudgetBreakdownService:
    """
    Builds a full per-user budget snapshot for admin/debug UI:
      - base policy
      - plan override snapshot (+ expired status)
      - effective policy (merge)
      - RL usage counters (requests/tokens)
      - remaining headroom vs effective policy
      - lifetime credits (gross/reserved/available) + active reservations list

    Important: this service contains the orchestration logic so REST stays SQL-free.
    """

    def __init__(
            self,
            *,
            pg_pool: asyncpg.Pool,
            redis: Redis,
            plan_balance_snapshot_mgr: Optional[UserPlanBalanceSnapshotManager] = None,
            credits_mgr: Optional[UserCreditsManager] = None,
            subscription_mgr: Optional[SubscriptionManager] = None,
    ):
        self._pg_pool = pg_pool
        self._redis = redis
        self._plan_snapshot = plan_balance_snapshot_mgr or UserPlanBalanceSnapshotManager(pg_pool=pg_pool)
        self._credits_mgr = credits_mgr or UserCreditsManager(pg_pool=pg_pool, redis=redis)
        self._subscription_mgr = subscription_mgr or SubscriptionManager(pg_pool=pg_pool)

    @staticmethod
    def _policy_to_dict(p) -> dict:
        if not p:
            return {}
        # tolerate different policy shapes (dataclass/pydantic/record)
        return {
            "max_concurrent": getattr(p, "max_concurrent", None),
            "requests_per_day": getattr(p, "requests_per_day", None),
            "requests_per_month": getattr(p, "requests_per_month", None),
            "total_requests": getattr(p, "total_requests", None),
            "tokens_per_hour": getattr(p, "tokens_per_hour", None),
            "tokens_per_day": getattr(p, "tokens_per_day", None),
            "tokens_per_month": getattr(p, "tokens_per_month", None),
        }

    @staticmethod
    def _dt(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None

    @staticmethod
    def _calc_remaining(limit: Optional[int], used: int) -> Optional[int]:
        if limit is None:
            return None
        return int(limit) - int(used)

    async def get_user_budget_breakdown(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            role: Optional[str],
            plan_id: str,
            plan_source: Optional[str] = None,
            base_policy,
            include_expired_override: bool = True,
            reservations_limit: int = 50,
            bundle_ids: Optional[list[str]] = None,
            reference_provider: Optional[str] = None,
            reference_model: Optional[str] = None,
    ) -> dict:
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
            UserEconomicsRateLimiter,
            _merge_policy_with_plan_override,
            _k,
            subject_id_of,
        )
        from kdcube_ai_app.infra.accounting.usage import (
            llm_output_price_usd_per_token,
            quote_tokens_for_usd,
            llm_reference_service,
        )

        bundle_ids = bundle_ids or ["*"]
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

        if not reference_provider or not reference_model:
            reference_provider, reference_model = llm_reference_service()

        # -------- plan override snapshots (for display + for effective merge) --------
        plan_full = await self._plan_snapshot.get_user_plan_balance(
            tenant=tenant, project=project, user_id=user_id, include_expired=True
        )
        plan_effective = await self._plan_snapshot.get_user_plan_balance(
            tenant=tenant, project=project, user_id=user_id, include_expired=False
        )

        usd_per_token = float(llm_output_price_usd_per_token(reference_provider, reference_model))

        def _usd(tokens: Optional[int]) -> Optional[float]:
            if tokens is None:
                return None
            return round(float(tokens) * usd_per_token, 2)

        # -------- usage counters from RL (Redis) --------
        rl = UserEconomicsRateLimiter(self._redis)
        usage_breakdown = await rl.breakdown(
            tenant=tenant, project=project, user_id=user_id, bundle_ids=bundle_ids, now=now
        )

        totals = usage_breakdown.get("totals") or {}
        req_day = int(totals.get("requests_today") or 0)
        req_month = int(totals.get("requests_this_month") or 0)
        req_total = int(totals.get("requests_total") or 0)
        tok_day = int(totals.get("tokens_today") or 0)
        tok_month = int(totals.get("tokens_this_month") or 0)
        tok_hour = int(totals.get("tokens_this_hour") or 0)
        tok_reserved = int(totals.get("tokens_reserved") or 0)

        # -------- effective policy (override semantics) --------
        effective_policy = _merge_policy_with_plan_override(base_policy, plan_effective) if plan_effective else base_policy

        # -------- rolling window reset info (per bundle, optional) --------
        reset_windows = None
        if bundle_ids and len(bundle_ids) == 1 and bundle_ids[0] != "*":
            bundle_id = bundle_ids[0]
            subject_id = subject_id_of(tenant, project, user_id)

            hour_reset_at = None
            tokens_per_hour = getattr(effective_policy, "tokens_per_hour", None)
            if tokens_per_hour is not None:
                bucket_prefix = _k(rl.ns, bundle_id, subject_id, "toks:hour:bucket")
                _, reset_at = await rl._rolling_hour_stats(
                    bucket_prefix,
                    now,
                    limit=int(tokens_per_hour or 0),
                    reserved=0,
                )
                if reset_at:
                    hour_reset_at = datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat()

            day_period_start, day_period_end, day_period_key = await rl._rolling_day_period(
                bundle_id=bundle_id,
                subject_id=subject_id,
                now=now,
                create_if_missing=False,
            )

            month_reset_at = None
            has_month_limit = (
                getattr(effective_policy, "requests_per_month", None) is not None
                or getattr(effective_policy, "tokens_per_month", None) is not None
            )
            period_start, period_end, period_key = await rl._rolling_month_period(
                bundle_id=bundle_id,
                subject_id=subject_id,
                now=now,
                create_if_missing=False,
            )
            if has_month_limit and period_end:
                month_reset_at = period_end.isoformat()

            reset_windows = {
                "bundle_id": bundle_id,
                "hour_reset_at": hour_reset_at,
                "day_started_at": day_period_start.isoformat() if day_period_start else None,
                "day_reset_at": day_period_end.isoformat() if day_period_end else None,
                "month_started_at": period_start.isoformat() if period_start else None,
                "month_reset_at": month_reset_at,
            }

        # Remaining (NOTE: not clamped to >=0; admin wants to see negative headroom too)
        remaining_req_day = self._calc_remaining(getattr(effective_policy, "requests_per_day", None), req_day)
        remaining_req_month = self._calc_remaining(getattr(effective_policy, "requests_per_month", None), req_month)
        remaining_tok_hour = self._calc_remaining(getattr(effective_policy, "tokens_per_hour", None), tok_hour + tok_reserved)
        remaining_tok_day = self._calc_remaining(getattr(effective_policy, "tokens_per_day", None), tok_day + tok_reserved)
        remaining_tok_month = self._calc_remaining(getattr(effective_policy, "tokens_per_month", None), tok_month + tok_reserved)

        percentage_used = None
        if getattr(effective_policy, "requests_per_day", None):
            lim = int(getattr(effective_policy, "requests_per_day") or 0)
            if lim > 0:
                percentage_used = round((req_day / lim) * 100, 1)

        # -------- plan override payload (from plan_full) --------
        plan_override_payload = None
        if plan_full and plan_full.has_plan_override():
            expired = plan_full.is_plan_override_expired()
            active = plan_full.plan_override_is_active()

            if include_expired_override or active:
                plan_override_payload = {
                    "active": bool(active),
                    "expired": bool(expired),
                    "expires_at": self._dt(plan_full.expires_at),
                    "limits": {
                        "max_concurrent": plan_full.max_concurrent,
                        "requests_per_day": plan_full.requests_per_day,
                        "requests_per_month": plan_full.requests_per_month,
                        "total_requests": plan_full.total_requests,
                        "tokens_per_hour": plan_full.tokens_per_hour,
                        "tokens_per_day": plan_full.tokens_per_day,
                        "tokens_per_month": plan_full.tokens_per_month,
                        "usd_per_hour": _usd(plan_full.tokens_per_hour),
                        "usd_per_day": _usd(plan_full.tokens_per_day),
                        "usd_per_month": _usd(plan_full.tokens_per_month),
                    },
                    "grant": {
                        "id": plan_full.grant_id,
                        "amount_usd": plan_full.grant_amount_usd,
                        "notes": plan_full.grant_notes,
                    },
                }

        # -------- lifetime credits + reservations --------
        credits_payload = None
        reservations_payload: list[dict] = []

        if plan_full and plan_full.has_lifetime_budget():
            # Wallet is USD-native (cents). available = purchased - spent - reserved.
            purchased_cents = int(plan_full.purchased_cents or 0)
            spent_cents = int(plan_full.spent_cents or 0)
            reserved_cents = await self._credits_mgr.get_active_reserved_cents(
                tenant=tenant, project=project, user_id=user_id
            )
            available_cents = purchased_cents - spent_cents - reserved_cents

            # list reservations (limited)
            reservations = await self._credits_mgr.list_active_reservations(
                tenant=tenant, project=project, user_id=user_id, limit=int(reservations_limit)
            )
            for r in reservations:
                reservations_payload.append({
                    "reservation_id": r.reservation_id,
                    "bundle_id": r.bundle_id,
                    "reserved_usd": round(int(r.usd_reserved_cents or 0) / 100.0, 2),
                    "spent_usd": round(int(r.actual_spent_cents) / 100.0, 2)
                    if r.actual_spent_cents is not None else None,
                    "status": r.status,
                    "expires_at": self._dt(r.expires_at),
                    "created_at": self._dt(r.created_at),
                    "updated_at": self._dt(r.updated_at),
                    "notes": r.notes,
                })

            credits_payload = {
                "has_lifetime_credits": True,
                "purchased_usd": round(purchased_cents / 100.0, 2),
                "spent_usd": round(spent_cents / 100.0, 2),
                "reserved_usd": round(reserved_cents / 100.0, 2),
                "available_usd": round(available_cents / 100.0, 2),
                "lifetime_usd_purchased": float(plan_full.lifetime_usd_purchased)
                if plan_full.lifetime_usd_purchased is not None else None,
                "last_purchase": {
                    "id": plan_full.last_purchase_id,
                    "amount_usd": float(plan_full.last_purchase_amount_usd)
                    if plan_full.last_purchase_amount_usd is not None else None,
                    "notes": plan_full.last_purchase_notes,
                },
            }

        # -------- subscription balance (per-user) --------
        subscription_payload = None
        if self._subscription_mgr:
            sub = await self._subscription_mgr.get_subscription(
                tenant=tenant, project=project, user_id=user_id
            )
            if sub:
                from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import build_subscription_period_descriptor
                period_desc = build_subscription_period_descriptor(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    provider=getattr(sub, "provider", "internal") or "internal",
                    stripe_subscription_id=getattr(sub, "stripe_subscription_id", None),
                    period_end=getattr(sub, "next_charge_at", None),
                    period_start=getattr(sub, "last_charged_at", None),
                )
                limiter = SubscriptionBudgetLimiter(
                    pg_pool=self._pg_pool,
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    period_key=period_desc["period_key"],
                    period_start=period_desc["period_start"],
                    period_end=period_desc["period_end"],
                )
                sub_bal = await limiter.get_subscription_budget_balance()

                def _tokens_from_usd(usd_amount: Optional[float]) -> Optional[int]:
                    if usd_amount is None:
                        return None
                    tokens, _ = quote_tokens_for_usd(
                        usd_amount=float(usd_amount),
                        ref_provider=reference_provider,
                        ref_model=reference_model,
                    )
                    return int(tokens)

                subscription_payload = {
                    "has_subscription": True,
                    "active": bool(getattr(sub, "status", None) == "active"),
                    "plan_id": getattr(sub, "plan_id", None),
                    "status": getattr(sub, "status", None),
                    "provider": getattr(sub, "provider", None),
                    "monthly_price_cents": getattr(sub, "monthly_price_cents", None),
                    "period_key": sub_bal.get("period_key"),
                    "period_start": self._dt(sub_bal.get("period_start")),
                    "period_end": self._dt(sub_bal.get("period_end")),
                    "period_status": sub_bal.get("status"),
                    "balance_usd": float(sub_bal.get("balance_usd") or 0.0),
                    "reserved_usd": float(sub_bal.get("reserved_usd") or 0.0),
                    "available_usd": float(sub_bal.get("available_usd") or 0.0),
                    "balance_tokens": _tokens_from_usd(sub_bal.get("balance_usd")),
                    "reserved_tokens": _tokens_from_usd(sub_bal.get("reserved_usd")),
                    "available_tokens": _tokens_from_usd(sub_bal.get("available_usd")),
                    "topup_usd": float(sub_bal.get("topup_usd") or 0.0),
                    "rolled_over_usd": float(sub_bal.get("rolled_over_usd") or 0.0),
                    "spent_usd": float(sub_bal.get("spent_usd") or 0.0),
                    "lifetime_added_usd": float(sub_bal.get("lifetime_added_usd") or 0.0),
                    "lifetime_spent_usd": float(sub_bal.get("lifetime_spent_usd") or 0.0),
                    "reference_model": f"{reference_provider}/{reference_model}",
                }

        base_policy_payload = self._policy_to_dict(base_policy)
        effective_policy_payload = self._policy_to_dict(effective_policy)
        for payload in (base_policy_payload, effective_policy_payload):
            payload["usd_per_hour"] = _usd(payload.get("tokens_per_hour"))
            payload["usd_per_day"] = _usd(payload.get("tokens_per_day"))
            payload["usd_per_month"] = _usd(payload.get("tokens_per_month"))

        return {
            "status": "ok",
            "user_id": user_id,
            "role": role,
            "plan_id": plan_id,
            "plan_source": plan_source,
            "bundle_breakdown": usage_breakdown.get("bundles"),
            "base_policy": base_policy_payload,
            "plan_override": plan_override_payload,
            "effective_policy": effective_policy_payload,
            "current_usage": {
                "requests_today": req_day,
                "requests_this_month": req_month,
                "requests_total": req_total,
                "tokens_this_hour": tok_hour,
                "tokens_today": tok_day,
                "tokens_this_month": tok_month,
                "tokens_reserved": tok_reserved,
                "tokens_this_hour_usd": _usd(tok_hour),
                "tokens_today_usd": _usd(tok_day),
                "tokens_this_month_usd": _usd(tok_month),
                "tokens_reserved_usd": _usd(tok_reserved),
                "concurrent": 0,
            },
            "reset_windows": reset_windows,
            "remaining": {
                "requests_today": remaining_req_day,
                "requests_this_month": remaining_req_month,
                "tokens_this_hour": remaining_tok_hour,
                "tokens_today": remaining_tok_day,
                "tokens_this_month": remaining_tok_month,
                "tokens_this_hour_usd": _usd(remaining_tok_hour),
                "tokens_today_usd": _usd(remaining_tok_day),
                "tokens_this_month_usd": _usd(remaining_tok_month),
                "percentage_used": percentage_used,
            },
            "lifetime_credits": credits_payload,
            "subscription_balance": subscription_payload,
            "active_reservations": reservations_payload,
            "reference_model": f"{reference_provider}/{reference_model}",
        }

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
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter

logger = logging.getLogger(__name__)


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

    # Lifetime tokens (personal credits)
    lifetime_tokens_purchased: Optional[int] = None
    lifetime_tokens_consumed: Optional[int] = None

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
                self.lifetime_tokens_purchased is not None
                or self.lifetime_tokens_consumed is not None
                or self.lifetime_usd_purchased is not None
        )

    # Note: do NOT mix the two lifecycles anymore.
    # This is *not* "plan override is valid". It's "any user-budget info exists and active".
    def is_valid(self) -> bool:
        if not self.active:
            return False
        return self.plan_override_is_active() or self.has_lifetime_budget()


@dataclass
class TokenReservation:
    tenant: str
    project: str
    user_id: str
    reservation_id: str
    tokens_reserved: int
    status: str
    expires_at: datetime
    bundle_id: Optional[str] = None
    tokens_used: Optional[int] = None
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
    CP = "kdcube_control_plane"
    OVERRIDE_TABLE = "user_plan_overrides"
    CREDITS_TABLE = "user_lifetime_credits"

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
            FROM {self.CP}.{self.OVERRIDE_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND active=TRUE
            LIMIT 1
          ),
          c AS (
            SELECT *
            FROM {self.CP}.{self.CREDITS_TABLE}
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

          c.lifetime_tokens_purchased AS lifetime_tokens_purchased,
          c.lifetime_tokens_consumed  AS lifetime_tokens_consumed,
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
    CP = "kdcube_control_plane"
    TABLE = "user_plan_overrides"

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
                ssl=self._settings.PGSSL,
            )
            self._owns_pool = True

        if not self._redis and redis_url:
            self._redis = Redis.from_url(redis_url)
            self._owns_redis = True

    async def close(self):
        if self._owns_pool and self._pg_pool:
            await self._pg_pool.close()
        if self._owns_redis and self._redis:
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
                FROM {self.CP}.{self.TABLE}
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
                INSERT INTO {self.CP}.{self.TABLE} (
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
                    max_concurrent     = COALESCE(EXCLUDED.max_concurrent, {self.CP}.{self.TABLE}.max_concurrent),
                    requests_per_day   = COALESCE(EXCLUDED.requests_per_day, {self.CP}.{self.TABLE}.requests_per_day),
                    requests_per_month = COALESCE(EXCLUDED.requests_per_month, {self.CP}.{self.TABLE}.requests_per_month),
                    total_requests     = COALESCE(EXCLUDED.total_requests, {self.CP}.{self.TABLE}.total_requests),
                    tokens_per_hour    = COALESCE(EXCLUDED.tokens_per_hour, {self.CP}.{self.TABLE}.tokens_per_hour),
                    tokens_per_day     = COALESCE(EXCLUDED.tokens_per_day, {self.CP}.{self.TABLE}.tokens_per_day),
                    tokens_per_month   = COALESCE(EXCLUDED.tokens_per_month, {self.CP}.{self.TABLE}.tokens_per_month),
                    expires_at         = COALESCE(EXCLUDED.expires_at, {self.CP}.{self.TABLE}.expires_at),
                    grant_id           = COALESCE(EXCLUDED.grant_id, {self.CP}.{self.TABLE}.grant_id),
                    grant_amount_usd   = COALESCE(EXCLUDED.grant_amount_usd, {self.CP}.{self.TABLE}.grant_amount_usd),
                    grant_notes        = COALESCE(EXCLUDED.grant_notes, {self.CP}.{self.TABLE}.grant_notes),
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
                UPDATE {self.CP}.{self.TABLE}
                SET active=FALSE, updated_at=NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3
            """, tenant, project, user_id)

        await self._invalidate(tenant, project, user_id)


# -----------------------------------------------------------------------------
# UserCreditsManager  (table: user_lifetime_credits + user_token_reservations)
# -----------------------------------------------------------------------------
class UserCreditsManager:
    CP = "kdcube_control_plane"
    TABLE = "user_lifetime_credits"
    RESERVATIONS_TABLE = "user_token_reservations"

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
                ssl=self._settings.PGSSL,
            )
            self._owns_pool = True

        if not self._redis and redis_url:
            self._redis = Redis.from_url(redis_url)
            self._owns_redis = True

    async def close(self):
        if self._owns_pool and self._pg_pool:
            await self._pg_pool.close()
        if self._owns_redis and self._redis:
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
                FROM {self.CP}.{self.TABLE}
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

    async def add_lifetime_tokens(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            tokens: int,
            usd_amount: float,
            purchase_id: Optional[str] = None,
            notes: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        if tokens <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "lifetime_tokens_purchased": 0, "lifetime_tokens_consumed": 0,
                "lifetime_usd_purchased": 0,
            }

        sql = f"""
          INSERT INTO {self.CP}.{self.TABLE} (
            tenant, project, user_id,
            lifetime_tokens_purchased,
            lifetime_tokens_consumed,
            lifetime_usd_purchased,
            last_purchase_id,
            last_purchase_amount_usd,
            last_purchase_notes
          ) VALUES ($1,$2,$3,$4,0,$5,$6,$5,$7)
          ON CONFLICT (tenant, project, user_id)
          DO UPDATE SET
            lifetime_tokens_purchased = {self.CP}.{self.TABLE}.lifetime_tokens_purchased + EXCLUDED.lifetime_tokens_purchased,
            lifetime_usd_purchased    = {self.CP}.{self.TABLE}.lifetime_usd_purchased + EXCLUDED.lifetime_usd_purchased,
            last_purchase_id          = EXCLUDED.last_purchase_id,
            last_purchase_amount_usd  = EXCLUDED.last_purchase_amount_usd,
            last_purchase_notes       = EXCLUDED.last_purchase_notes,
            active                    = TRUE,
            updated_at                = NOW()
          RETURNING *
        """

        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, int(tokens), float(usd_amount), purchase_id, notes)
        else:
            if not self._pg_pool:
                raise RuntimeError("PostgreSQL pool not initialized")
            async with self._pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id, int(tokens), float(usd_amount), purchase_id, notes)

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    async def refund_lifetime_tokens(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            tokens: int,
            usd_amount: float,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        """
        Refund (remove) lifetime tokens/amount.
        Ensures refundable = purchased - consumed - reserved >= tokens.
        """
        if tokens <= 0 or usd_amount <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "lifetime_tokens_purchased": 0, "lifetime_tokens_consumed": 0,
                "lifetime_usd_purchased": 0,
            }

        async def _apply(c: asyncpg.Connection) -> asyncpg.Record:
            bal = await c.fetchrow(f"""
                SELECT lifetime_tokens_purchased AS purchased,
                       lifetime_tokens_consumed AS consumed,
                       lifetime_usd_purchased AS usd_purchased
                FROM {self.CP}.{self.TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND active=TRUE
                FOR UPDATE
            """, tenant, project, user_id)
            if not bal:
                raise ValueError("lifetime credits not found")

            purchased = int(bal["purchased"] or 0)
            consumed = int(bal["consumed"] or 0)
            reserved = await self._reserved_sum(conn=c, tenant=tenant, project=project, user_id=user_id)
            available = purchased - consumed - reserved
            if available < int(tokens):
                raise ValueError(f"insufficient refundable tokens: available={available}, requested={int(tokens)}")

            row = await c.fetchrow(f"""
                UPDATE {self.CP}.{self.TABLE}
                SET lifetime_tokens_purchased = lifetime_tokens_purchased - $4,
                    lifetime_usd_purchased = GREATEST(lifetime_usd_purchased - $5, 0),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                RETURNING *
            """, tenant, project, user_id, int(tokens), float(usd_amount))
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

    async def restore_lifetime_tokens(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            tokens: int,
            usd_amount: float,
            conn: Optional[asyncpg.Connection] = None,
    ) -> dict:
        """
        Restore (add back) lifetime tokens/amount after a failed refund.
        Does not alter last_purchase_* fields.
        """
        if tokens <= 0 or usd_amount <= 0:
            existing = await self.get_user_credits(tenant=tenant, project=project, user_id=user_id)
            return existing or {
                "tenant": tenant, "project": project, "user_id": user_id,
                "lifetime_tokens_purchased": 0, "lifetime_tokens_consumed": 0,
                "lifetime_usd_purchased": 0,
            }

        sql = f"""
            INSERT INTO {self.CP}.{self.TABLE} (
                tenant, project, user_id,
                lifetime_tokens_purchased,
                lifetime_tokens_consumed,
                lifetime_usd_purchased,
                active
            ) VALUES ($1,$2,$3,$4,0,$5,TRUE)
            ON CONFLICT (tenant, project, user_id)
            DO UPDATE SET
                lifetime_tokens_purchased = {self.CP}.{self.TABLE}.lifetime_tokens_purchased + EXCLUDED.lifetime_tokens_purchased,
                lifetime_usd_purchased    = {self.CP}.{self.TABLE}.lifetime_usd_purchased + EXCLUDED.lifetime_usd_purchased,
                active                    = TRUE,
                updated_at                = NOW()
            RETURNING *
        """

        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, int(tokens), float(usd_amount))
        else:
            if not self._pg_pool:
                raise RuntimeError("PostgreSQL pool not initialized")
            async with self._pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id, int(tokens), float(usd_amount))

        await self._invalidate(tenant, project, user_id)
        return dict(row)

    async def deduct_lifetime_tokens(self, *, tenant: str, project: str, user_id: str, tokens: int) -> int:
        """
        Simple deduction (not reservation-aware).
        Returns overflow tokens not covered by credits.
        """
        if tokens <= 0:
            return 0
        if not self._pg_pool:
            return tokens

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                UPDATE {self.CP}.{self.TABLE}
                SET lifetime_tokens_consumed = LEAST(lifetime_tokens_consumed + $4, lifetime_tokens_purchased),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND active=TRUE
                RETURNING lifetime_tokens_purchased AS purchased, lifetime_tokens_consumed AS consumed
            """, tenant, project, user_id, int(tokens))

        if not row:
            return tokens

        purchased = int(row["purchased"] or 0)
        consumed = int(row["consumed"] or 0)
        old_consumed = max(consumed - int(tokens), 0)
        actually = min(int(tokens), max(purchased - old_consumed, 0))
        overflow = int(tokens) - actually

        await self._invalidate(tenant, project, user_id)
        return max(overflow, 0)

    # ---------------- reservation-aware balance ----------------

    async def _reserved_sum(
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
            SELECT COALESCE(SUM(tokens_reserved), 0)
            FROM {self.CP}.{self.RESERVATIONS_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND status='reserved'
              AND expires_at > NOW()
              {exclude_sql}
        """, *args)
        return int(v or 0)

    async def get_lifetime_balance(self, *, tenant: str, project: str, user_id: str) -> Optional[int]:
        """
        Remaining AVAILABLE lifetime tokens:
          purchased - consumed - active_reservations
        """
        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT
                    COALESCE(ulc.lifetime_tokens_purchased, 0) AS purchased,
                    COALESCE(ulc.lifetime_tokens_consumed, 0) AS consumed,
                    COALESCE(rsv.reserved, 0) AS reserved
                FROM {self.CP}.{self.TABLE} ulc
                LEFT JOIN (
                    SELECT tenant, project, user_id, COALESCE(SUM(tokens_reserved), 0) AS reserved
                    FROM {self.CP}.{self.RESERVATIONS_TABLE}
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

        remaining = int(row["purchased"]) - int(row["consumed"]) - int(row["reserved"])
        return remaining

    async def reserve_lifetime_tokens(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            reservation_id: str,
            tokens: int,
            ttl_sec: int = 900,
            bundle_id: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> bool:
        if tokens <= 0:
            return True
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        exp = datetime.now(timezone.utc) + timedelta(seconds=int(ttl_sec))

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                res = await conn.fetchrow(f"""
                    SELECT tokens_reserved, status
                    FROM {self.CP}.{self.RESERVATIONS_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                    FOR UPDATE
                """, tenant, project, user_id, reservation_id)

                bal = await conn.fetchrow(f"""
                    SELECT lifetime_tokens_purchased AS purchased,
                           lifetime_tokens_consumed AS consumed
                    FROM {self.CP}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    return False

                purchased = int(bal["purchased"] or 0)
                consumed = int(bal["consumed"] or 0)
                # if purchased <= 0:
                #     return False

                reserved = await self._reserved_sum(conn=conn, tenant=tenant, project=project, user_id=user_id)
                available = purchased - consumed - reserved
                if available < int(tokens):
                    return False

                await conn.execute(f"""
                    INSERT INTO {self.CP}.{self.RESERVATIONS_TABLE} (
                        tenant, project, user_id,
                        reservation_id, bundle_id,
                        tokens_reserved, status, expires_at, notes
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,'reserved',$7,$8)
                    ON CONFLICT (tenant, project, user_id, reservation_id)
                    DO UPDATE SET
                        tokens_reserved = GREATEST(EXCLUDED.tokens_reserved, {self.CP}.{self.RESERVATIONS_TABLE}.tokens_reserved),
                        status='reserved',
                        expires_at=EXCLUDED.expires_at,
                        bundle_id=EXCLUDED.bundle_id,
                        notes=EXCLUDED.notes,
                        updated_at=NOW()
                """, tenant, project, user_id, reservation_id, bundle_id, int(tokens), exp, notes)

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
                UPDATE {self.CP}.{self.RESERVATIONS_TABLE}
                SET status='released',
                    released_at=NOW(),
                    notes = COALESCE(notes, '') || CASE WHEN $5 IS NULL THEN '' ELSE (' | ' || $5) END,
                    updated_at=NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                  AND status='reserved'
            """, tenant, project, user_id, reservation_id, reason)

    async def commit_reserved_lifetime_tokens(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            reservation_id: str,
            tokens: int,
    ) -> int:
        """
        Commit actual token spend against a reservation.
        Returns overflow tokens not covered by credits.
        """
        if tokens <= 0:
            return 0
        if not self._pg_pool:
            return tokens

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchrow(f"""
                    SELECT lifetime_tokens_purchased AS purchased,
                           lifetime_tokens_consumed AS consumed
                    FROM {self.CP}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    # release reservation best-effort
                    await conn.execute(f"""
                        UPDATE {self.CP}.{self.RESERVATIONS_TABLE}
                        SET status='released', released_at=NOW(), updated_at=NOW(),
                            notes=COALESCE(notes,'') || ' | commit: no_balance_row'
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                          AND status='reserved'
                    """, tenant, project, user_id, reservation_id)
                    return tokens

                if not res or res.get("status") != "reserved":
                    # Missing or already finalized reservation; do not consume
                    return tokens

                reserved = int(res.get("tokens_reserved") or 0)

                purchased = int(bal["purchased"] or 0)
                consumed = int(bal["consumed"] or 0)

                other_reserved = await self._reserved_sum(
                    conn=conn, tenant=tenant, project=project, user_id=user_id,
                    exclude_reservation_id=reservation_id,
                )
                available = max(purchased - consumed - other_reserved, 0)
                consume = min(int(tokens), int(available), int(reserved))

                if consume > 0:
                    await conn.execute(f"""
                        UPDATE {self.CP}.{self.TABLE}
                        SET lifetime_tokens_consumed = LEAST(lifetime_tokens_consumed + $4, lifetime_tokens_purchased),
                            updated_at=NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                    """, tenant, project, user_id, int(consume))

                await conn.execute(f"""
                    UPDATE {self.CP}.{self.RESERVATIONS_TABLE}
                    SET status='committed',
                        tokens_used=$5,
                        committed_at=NOW(),
                        expires_at=NOW(),
                        updated_at=NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND reservation_id=$4
                """, tenant, project, user_id, reservation_id, int(consume))

        await self._invalidate(tenant, project, user_id)
        return max(int(tokens) - int(consume), 0)

    async def consume_lifetime_tokens(self, *, tenant: str, project: str, user_id: str, tokens: int) -> int:
        """
        Reservation-aware consumption WITHOUT a reservation_id.
        Will NOT steal tokens reserved by other in-flight requests.
        Returns overflow tokens.
        """
        if tokens <= 0:
            return 0
        if not self._pg_pool:
            return tokens

        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                bal = await conn.fetchrow(f"""
                    SELECT lifetime_tokens_purchased AS purchased,
                           lifetime_tokens_consumed AS consumed
                    FROM {self.CP}.{self.TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3
                      AND active=TRUE
                    FOR UPDATE
                """, tenant, project, user_id)

                if not bal:
                    return tokens

                purchased = int(bal["purchased"] or 0)
                consumed = int(bal["consumed"] or 0)

                reserved = await self._reserved_sum(conn=conn, tenant=tenant, project=project, user_id=user_id)
                available = max(purchased - consumed - reserved, 0)

                consume = min(int(tokens), int(available))
                if consume > 0:
                    await conn.execute(f"""
                        UPDATE {self.CP}.{self.TABLE}
                        SET lifetime_tokens_consumed = LEAST(lifetime_tokens_consumed + $4, lifetime_tokens_purchased),
                            updated_at=NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                    """, tenant, project, user_id, int(consume))

        await self._invalidate(tenant, project, user_id)
        return max(int(tokens) - int(consume), 0)

    async def get_active_reserved_sum(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
    ) -> int:
        """
        Sum of currently active reservations.
        """
        if not self._pg_pool:
            return 0

        async with self._pg_pool.acquire() as conn:
            v = await conn.fetchval(f"""
                SELECT COALESCE(SUM(tokens_reserved), 0)
                FROM {self.CP}.{self.RESERVATIONS_TABLE}
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
    ) -> list[TokenReservation]:
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
                    tokens_reserved,
                    tokens_used,
                    status,
                    expires_at,
                    created_at,
                    updated_at,
                    committed_at,
                    released_at,
                    notes
                FROM {self.CP}.{self.RESERVATIONS_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3
                  AND status='reserved'
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT $4
            """, tenant, project, user_id, int(limit))

        out: list[TokenReservation] = []
        for r in rows:
            d = dict(r)
            out.append(TokenReservation(**d))
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
            reference_provider: str = "anthropic",
            reference_model: str = "claude-sonnet-4-5-20250929",
    ) -> dict:
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
            UserEconomicsRateLimiter,
            _merge_policy_with_plan_override,
            _k,
            subject_id_of,
        )
        from kdcube_ai_app.infra.accounting.usage import llm_output_price_usd_per_token, quote_tokens_for_usd

        bundle_ids = bundle_ids or ["*"]
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

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
                "month_reset_at": month_reset_at,
            }

        # Remaining (NOTE: not clamped to >=0; admin wants to see negative headroom too)
        remaining_req_day = self._calc_remaining(getattr(effective_policy, "requests_per_day", None), req_day)
        remaining_req_month = self._calc_remaining(getattr(effective_policy, "requests_per_month", None), req_month)
        remaining_tok_day = self._calc_remaining(getattr(effective_policy, "tokens_per_day", None), tok_day)
        remaining_tok_month = self._calc_remaining(getattr(effective_policy, "tokens_per_month", None), tok_month)

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
            purchased = int(plan_full.lifetime_tokens_purchased or 0)
            consumed = int(plan_full.lifetime_tokens_consumed or 0)

            # Do NOT clamp: if later you allow negative credits, UI/API stays truthful.
            gross_remaining = purchased - consumed

            reserved_sum = await self._credits_mgr.get_active_reserved_sum(
                tenant=tenant, project=project, user_id=user_id
            )

            available = gross_remaining - int(reserved_sum)

            available_usd = round(float(available) * usd_per_token, 2)

            # list reservations (limited)
            reservations = await self._credits_mgr.list_active_reservations(
                tenant=tenant, project=project, user_id=user_id, limit=int(reservations_limit)
            )
            for r in reservations:
                reservations_payload.append({
                    "reservation_id": r.reservation_id,
                    "bundle_id": r.bundle_id,
                    "tokens_reserved": int(r.tokens_reserved),
                    "tokens_used": int(r.tokens_used or 0),
                    "status": r.status,
                    "expires_at": self._dt(r.expires_at),
                    "created_at": self._dt(r.created_at),
                    "updated_at": self._dt(r.updated_at),
                    "notes": r.notes,
                })

            credits_payload = {
                "has_lifetime_credits": True,
                "tokens_purchased": purchased,
                "tokens_consumed": consumed,
                "tokens_gross_remaining": int(gross_remaining),
                "tokens_reserved": int(reserved_sum),
                "tokens_available": int(available),
                "available_usd": float(available_usd),
                "lifetime_usd_purchased": float(plan_full.lifetime_usd_purchased)
                if plan_full.lifetime_usd_purchased is not None else None,
                "last_purchase": {
                    "id": plan_full.last_purchase_id,
                    "amount_usd": float(plan_full.last_purchase_amount_usd)
                    if plan_full.last_purchase_amount_usd is not None else None,
                    "notes": plan_full.last_purchase_notes,
                },
                "reference_model": f"{reference_provider}/{reference_model}",
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
                "tokens_today": tok_day,
                "tokens_this_month": tok_month,
                "tokens_today_usd": _usd(tok_day),
                "tokens_this_month_usd": _usd(tok_month),
                "concurrent": 0,
            },
            "reset_windows": reset_windows,
            "remaining": {
                "requests_today": remaining_req_day,
                "requests_this_month": remaining_req_month,
                "tokens_today": remaining_tok_day,
                "tokens_this_month": remaining_tok_month,
                "tokens_today_usd": _usd(remaining_tok_day),
                "tokens_this_month_usd": _usd(remaining_tok_month),
                "percentage_used": percentage_used,
            },
            "lifetime_credits": credits_payload,
            "subscription_balance": subscription_payload,
            "active_reservations": reservations_payload,
            "reference_model": f"{reference_provider}/{reference_model}",
        }

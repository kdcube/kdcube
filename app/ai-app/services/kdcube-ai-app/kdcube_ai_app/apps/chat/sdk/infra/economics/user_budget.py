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

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Compatibility snapshot (flattened) used widely by RL / run() today.
# Backed by TWO tables:
#   - user_tier_overrides
#   - user_lifetime_credits
# -----------------------------------------------------------------------------
@dataclass
class UserTierBalance:
    # Identification
    tenant: str
    project: str
    user_id: str

    # Tier Override Limits (NULL = use base tier)
    max_concurrent: Optional[int] = None
    requests_per_day: Optional[int] = None
    requests_per_month: Optional[int] = None
    total_requests: Optional[int] = None
    tokens_per_hour: Optional[int] = None
    tokens_per_day: Optional[int] = None
    tokens_per_month: Optional[int] = None

    # Tier override expiry
    expires_at: Optional[datetime] = None

    # Tier override grant tracking (NOT personal credits purchase)
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

    # ---------- tier override semantics ----------
    def is_tier_override_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def has_tier_override(self) -> bool:
        return any([
            self.max_concurrent is not None,
            self.requests_per_day is not None,
            self.requests_per_month is not None,
            self.total_requests is not None,
            self.tokens_per_hour is not None,
            self.tokens_per_day is not None,
            self.tokens_per_month is not None,
            ])

    def tier_override_is_active(self) -> bool:
        return self.active and self.has_tier_override() and (not self.is_tier_override_expired())

    # ---------- personal credits semantics ----------
    def has_lifetime_budget(self) -> bool:
        # "Budget info exists" (can be positive, zero, or negative)
        return (
                self.lifetime_tokens_purchased is not None
                or self.lifetime_tokens_consumed is not None
                or self.lifetime_usd_purchased is not None
        )

    # Note: do NOT mix the two lifecycles anymore.
    # This is *not* "tier override is valid". It's "any user-budget info exists and active".
    def is_valid(self) -> bool:
        if not self.active:
            return False
        return self.tier_override_is_active() or self.has_lifetime_budget()


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


class UserTierBalanceSnapshotManager:
    """
    Read-only snapshot: single SQL that joins
      - user_tier_overrides
      - user_lifetime_credits
    and returns the flattened UserTierBalance dataclass.
    """
    CP = "kdcube_control_plane"
    OVERRIDE_TABLE = "user_tier_overrides"
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

    async def get_user_tier_balance(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            include_expired: bool = False,
    ) -> Optional[UserTierBalance]:
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

        return UserTierBalance(**d)

# -----------------------------------------------------------------------------
# TierOverrideManager  (table: user_tier_overrides)
# -----------------------------------------------------------------------------
class TierOverrideManager:
    CP = "kdcube_control_plane"
    TABLE = "user_tier_overrides"

    def __init__(
            self,
            pg_pool: Optional[asyncpg.Pool] = None,
            redis: Optional[Redis] = None,
            *,
            cache_ttl: int = 10,
            cache_namespace: str = REDIS.ECONOMICS.TIER_BALANCE_CACHE,  # reuse existing namespace
    ):
        self._pg_pool = pg_pool
        self._redis = redis
        self.cache_ttl = cache_ttl
        self.cache_ns = cache_namespace + ":tier_override"

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

    async def get_user_tier_override(
            self, *, tenant: str, project: str, user_id: str, include_expired: bool = False
    ) -> Optional[dict]:
        """
        Returns raw dict row from user_tier_overrides or None.
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
                logger.warning("Redis tier_override read error: %s", e)

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

    async def update_user_tier_budget(
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

    async def deactivate_tier_override(self, *, tenant: str, project: str, user_id: str) -> None:
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
            cache_namespace: str = REDIS.ECONOMICS.TIER_BALANCE_CACHE,  # reuse existing namespace
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

                purchased = int(bal["purchased"] or 0)
                consumed = int(bal["consumed"] or 0)

                other_reserved = await self._reserved_sum(
                    conn=conn, tenant=tenant, project=project, user_id=user_id,
                    exclude_reservation_id=reservation_id,
                )
                available = max(purchased - consumed - other_reserved, 0)
                consume = min(int(tokens), int(available))

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
      - tier override snapshot (+ expired status)
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
            tier_balance_snapshot_mgr: Optional[UserTierBalanceSnapshotManager] = None,
            credits_mgr: Optional[UserCreditsManager] = None,
    ):
        self._pg_pool = pg_pool
        self._redis = redis
        self._tier_snapshot = tier_balance_snapshot_mgr or UserTierBalanceSnapshotManager(pg_pool=pg_pool)
        self._credits_mgr = credits_mgr or UserCreditsManager(pg_pool=pg_pool, redis=redis)

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
            user_type: str,
            base_policy,
            include_expired_override: bool = True,
            reservations_limit: int = 50,
            bundle_ids: Optional[list[str]] = None,
            reference_provider: str = "anthropic",
            reference_model: str = "claude-sonnet-4-5-20250929",
    ) -> dict:
        from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
            UserEconomicsRateLimiter,
            _merge_policy_with_tier_balance,
        )
        from kdcube_ai_app.infra.accounting.usage import llm_output_price_usd_per_token

        bundle_ids = bundle_ids or ["*"]

        # -------- tier balance snapshots (for display + for effective merge) --------
        tier_full = await self._tier_snapshot.get_user_tier_balance(
            tenant=tenant, project=project, user_id=user_id, include_expired=True
        )
        tier_effective = await self._tier_snapshot.get_user_tier_balance(
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
            tenant=tenant, project=project, user_id=user_id, bundle_ids=bundle_ids
        )

        totals = usage_breakdown.get("totals") or {}
        req_day = int(totals.get("requests_today") or 0)
        req_month = int(totals.get("requests_this_month") or 0)
        req_total = int(totals.get("requests_total") or 0)
        tok_day = int(totals.get("tokens_today") or 0)
        tok_month = int(totals.get("tokens_this_month") or 0)

        # -------- effective policy (override semantics) --------
        effective_policy = _merge_policy_with_tier_balance(base_policy, tier_effective) if tier_effective else base_policy

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

        # -------- tier override payload (from tier_full) --------
        tier_override_payload = None
        if tier_full and tier_full.has_tier_override():
            expired = tier_full.is_tier_override_expired()
            active = tier_full.tier_override_is_active()

            if include_expired_override or active:
                tier_override_payload = {
                    "active": bool(active),
                    "expired": bool(expired),
                    "expires_at": self._dt(tier_full.expires_at),
                    "limits": {
                        "max_concurrent": tier_full.max_concurrent,
                        "requests_per_day": tier_full.requests_per_day,
                        "requests_per_month": tier_full.requests_per_month,
                        "total_requests": tier_full.total_requests,
                        "tokens_per_hour": tier_full.tokens_per_hour,
                        "tokens_per_day": tier_full.tokens_per_day,
                        "tokens_per_month": tier_full.tokens_per_month,
                        "usd_per_hour": _usd(tier_full.tokens_per_hour),
                        "usd_per_day": _usd(tier_full.tokens_per_day),
                        "usd_per_month": _usd(tier_full.tokens_per_month),
                    },
                    "grant": {
                        "id": tier_full.grant_id,
                        "amount_usd": tier_full.grant_amount_usd,
                        "notes": tier_full.grant_notes,
                    },
                }

        # -------- lifetime credits + reservations --------
        credits_payload = None
        reservations_payload: list[dict] = []

        if tier_full and tier_full.has_lifetime_budget():
            purchased = int(tier_full.lifetime_tokens_purchased or 0)
            consumed = int(tier_full.lifetime_tokens_consumed or 0)

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
                "lifetime_usd_purchased": float(tier_full.lifetime_usd_purchased)
                if tier_full.lifetime_usd_purchased is not None else None,
                "last_purchase": {
                    "id": tier_full.last_purchase_id,
                    "amount_usd": float(tier_full.last_purchase_amount_usd)
                    if tier_full.last_purchase_amount_usd is not None else None,
                    "notes": tier_full.last_purchase_notes,
                },
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
            "user_type": user_type,
            "bundle_breakdown": usage_breakdown.get("bundles"),
            "base_policy": base_policy_payload,
            "tier_override": tier_override_payload,
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
            "active_reservations": reservations_payload,
            "reference_model": f"{reference_provider}/{reference_model}",
        }

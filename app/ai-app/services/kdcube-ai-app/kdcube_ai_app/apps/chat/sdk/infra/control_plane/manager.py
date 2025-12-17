# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/rate_limit/control_plane_manager.py

"""
Control Plane Manager

Unified manager for:
1. User quota replenishments (via ReplenishmentManager)
2. User quota policies (base policies by user type)
3. Application budget policies (spending limits per provider)

All with PostgreSQL storage and Redis caching.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import asyncpg
from redis.asyncio import Redis

from kdcube_ai_app.apps.chat.sdk.infra.rate_limit.replenishment_manager import (
    ReplenishmentManager,
    QuotaReplenishment,
)
from kdcube_ai_app.apps.chat.sdk.infra.rate_limit.policy import QuotaPolicy, ProviderBudgetPolicy

logger = logging.getLogger(__name__)


@dataclass
class UserQuotaPolicy:
    """Base quota policy for a user type."""
    tenant: str
    project: str
    user_type: str
    bundle_id: str

    # Quota limits (None = unlimited)
    max_concurrent: Optional[int] = None
    requests_per_day: Optional[int] = None
    requests_per_month: Optional[int] = None
    total_requests: Optional[int] = None
    tokens_per_hour: Optional[int] = None
    tokens_per_day: Optional[int] = None
    tokens_per_month: Optional[int] = None

    # Metadata
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    notes: Optional[str] = None
    active: bool = True

    def to_quota_policy(self) -> QuotaPolicy:
        """Convert to QuotaPolicy dataclass."""
        return QuotaPolicy(
            max_concurrent=self.max_concurrent,
            requests_per_day=self.requests_per_day,
            requests_per_month=self.requests_per_month,
            total_requests=self.total_requests,
            tokens_per_hour=self.tokens_per_hour,
            tokens_per_day=self.tokens_per_day,
            tokens_per_month=self.tokens_per_month,
        )


@dataclass
class ApplicationBudgetPolicy:
    """Budget policy for a provider."""
    tenant: str
    project: str
    bundle_id: str
    provider: str

    # Budget limits in USD (None = unlimited)
    usd_per_hour: Optional[float] = None
    usd_per_day: Optional[float] = None
    usd_per_month: Optional[float] = None

    # Metadata
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    notes: Optional[str] = None
    active: bool = True

    def to_provider_budget_policy(self) -> ProviderBudgetPolicy:
        """Convert to ProviderBudgetPolicy dataclass."""
        return ProviderBudgetPolicy(
            provider=self.provider,
            usd_per_hour=self.usd_per_hour,
            usd_per_day=self.usd_per_day,
            usd_per_month=self.usd_per_month,
        )


class ControlPlaneManager:
    """
    Unified Control Plane Manager.

    Manages:
    1. User quota replenishments (delegates to ReplenishmentManager)
    2. User quota policies (base policies)
    3. Application budget policies

    All with PostgreSQL + Redis caching.
    """

    CONTROL_PLANE_SCHEMA = "kdcube_control_plane"

    def __init__(
            self,
            pg_pool: Optional[asyncpg.Pool] = None,
            redis: Optional[Redis] = None,
            *,
            cache_ttl: int = 60,  # 60 seconds for policies (longer than replenishments)
            replenishment_cache_ttl: int = 10,  # 10 seconds for replenishments
    ):
        """
        Initialize Control Plane Manager.

        Args:
            pg_pool: asyncpg connection pool
            redis: Redis client for caching
            cache_ttl: Cache TTL for policies in seconds (default: 60)
            replenishment_cache_ttl: Cache TTL for replenishments (default: 10)
        """
        self._pg_pool = pg_pool
        self._redis = redis
        self.cache_ttl = cache_ttl

        # Initialize ReplenishmentManager (delegates to it)
        self.replenishment_mgr = ReplenishmentManager(
            pg_pool=pg_pool,
            redis=redis,
            cache_ttl=replenishment_cache_ttl,
        )

        # Track if we own the pool/redis
        self._owns_pool = pg_pool is None
        self._owns_redis = redis is None

    async def init(
            self,
            *,
            pg_host: Optional[str] = None,
            pg_port: Optional[int] = None,
            pg_user: Optional[str] = None,
            pg_password: Optional[str] = None,
            pg_database: Optional[str] = None,
            pg_ssl: Optional[str] = None,
            redis_url: Optional[str] = None,
    ):
        """Initialize connections if not provided."""
        # Initialize via replenishment manager
        await self.replenishment_mgr.init(
            pg_host=pg_host,
            pg_port=pg_port,
            pg_user=pg_user,
            pg_password=pg_password,
            pg_database=pg_database,
            pg_ssl=pg_ssl,
            redis_url=redis_url,
        )

        # Share connections
        self._pg_pool = self.replenishment_mgr._pg_pool
        self._redis = self.replenishment_mgr._redis

    async def close(self):
        """Close connections."""
        await self.replenishment_mgr.close()

    # =========================================================================
    # Replenishment Operations (delegate to ReplenishmentManager)
    # =========================================================================

    async def get_replenishment(
            self, *, tenant: str, project: str, user_id: str, bundle_id: str
    ) -> Optional[QuotaReplenishment]:
        """Get user's quota replenishment (cached)."""
        return await self.replenishment_mgr.get_replenishment(
            tenant=tenant, project=project, user_id=user_id, bundle_id=bundle_id
        )

    async def create_replenishment(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            bundle_id: str,
            additional_requests_per_day: Optional[int] = None,
            additional_requests_per_month: Optional[int] = None,
            additional_total_requests: Optional[int] = None,
            additional_tokens_per_hour: Optional[int] = None,
            additional_tokens_per_day: Optional[int] = None,
            additional_tokens_per_month: Optional[int] = None,
            additional_max_concurrent: Optional[int] = None,
            expires_at: Optional[datetime] = None,
            purchase_id: Optional[str] = None,
            purchase_amount_usd: Optional[float] = None,
            purchase_notes: Optional[str] = None,
    ) -> QuotaReplenishment:
        """Create or update replenishment."""
        return await self.replenishment_mgr.create_replenishment(
            tenant=tenant,
            project=project,
            user_id=user_id,
            bundle_id=bundle_id,
            additional_requests_per_day=additional_requests_per_day,
            additional_requests_per_month=additional_requests_per_month,
            additional_total_requests=additional_total_requests,
            additional_tokens_per_hour=additional_tokens_per_hour,
            additional_tokens_per_day=additional_tokens_per_day,
            additional_tokens_per_month=additional_tokens_per_month,
            additional_max_concurrent=additional_max_concurrent,
            expires_at=expires_at,
            purchase_id=purchase_id,
            purchase_amount_usd=purchase_amount_usd,
            purchase_notes=purchase_notes,
        )

    async def list_user_replenishments(
            self, *, tenant: str, project: str, user_id: str, include_expired: bool = False
    ) -> List[QuotaReplenishment]:
        """List all replenishments for a user."""
        return await self.replenishment_mgr.list_user_replenishments(
            tenant=tenant, project=project, user_id=user_id, include_expired=include_expired
        )

    async def deactivate_replenishment(
            self, *, tenant: str, project: str, user_id: str, bundle_id: str
    ):
        """Deactivate a replenishment."""
        await self.replenishment_mgr.deactivate_replenishment(
            tenant=tenant, project=project, user_id=user_id, bundle_id=bundle_id
        )

    async def list_all_replenishments(
            self,
            *,
            tenant: Optional[str] = None,
            project: Optional[str] = None,
            user_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
            include_expired: bool = False,
            limit: int = 100,
    ) -> List[QuotaReplenishment]:
        """List replenishments with filters (for admin UI)."""
        if not self._pg_pool:
            return []

        where_clauses = ["active = TRUE"]
        args = []

        if tenant:
            args.append(tenant)
            where_clauses.append(f"tenant = ${len(args)}")
        if project:
            args.append(project)
            where_clauses.append(f"project = ${len(args)}")
        if user_id:
            args.append(user_id)
            where_clauses.append(f"user_id = ${len(args)}")
        if bundle_id:
            args.append(bundle_id)
            where_clauses.append(f"bundle_id = ${len(args)}")

        if not include_expired:
            where_clauses.append("(expires_at IS NULL OR expires_at > NOW())")

        where_sql = " AND ".join(where_clauses)

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.user_quota_replenishment
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT {int(limit)}
            """, *args)

        return [QuotaReplenishment(**dict(row)) for row in rows]

    async def list_users_with_credits(
            self,
            *,
            tenant: Optional[str] = None,
            project: Optional[str] = None,
            limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List users who have active credits."""
        if not self._pg_pool:
            return []

        where_clauses = ["active = TRUE", "(expires_at IS NULL OR expires_at > NOW())"]
        args = []

        if tenant:
            args.append(tenant)
            where_clauses.append(f"tenant = ${len(args)}")
        if project:
            args.append(project)
            where_clauses.append(f"project = ${len(args)}")

        where_sql = " AND ".join(where_clauses)

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT 
                    tenant, project, user_id,
                    COUNT(*) as credit_count,
                    SUM(additional_requests_per_day) as total_additional_requests_per_day,
                    SUM(additional_tokens_per_day) as total_additional_tokens_per_day,
                    MIN(expires_at) as earliest_expiry,
                    MAX(created_at) as latest_purchase
                FROM {self.CONTROL_PLANE_SCHEMA}.user_quota_replenishment
                WHERE {where_sql}
                GROUP BY tenant, project, user_id
                ORDER BY latest_purchase DESC
                LIMIT {int(limit)}
            """, *args)

        return [dict(row) for row in rows]

    # =========================================================================
    # User Quota Policy Operations
    # =========================================================================

    def _quota_policy_cache_key(
            self, tenant: str, project: str, user_type: str, bundle_id: str
    ) -> str:
        """Build Redis cache key for quota policy."""
        return f"kdcube:cp:quota_policy:{tenant}:{project}:{user_type}:{bundle_id}"

    async def get_user_quota_policy(
            self, *, tenant: str, project: str, user_type: str, bundle_id: str
    ) -> Optional[QuotaPolicy]:
        """
        Get quota policy for a user type.

        Lookup order:
        1. Redis cache
        2. PostgreSQL (bundle-specific)
        3. PostgreSQL (global bundle_id='*')

        Returns QuotaPolicy or None.
        """
        # Check Redis cache
        if self._redis:
            cache_key = self._quota_policy_cache_key(tenant, project, user_type, bundle_id)
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached.decode())
                    if data:
                        logger.debug(f"Quota policy cache HIT (Redis): {user_type}/{bundle_id}")
                        return QuotaPolicy(**data)
            except Exception as e:
                logger.warning(f"Redis cache read error for quota policy: {e}")

        # Query PostgreSQL
        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            # Try bundle-specific first
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.user_quota_policies
                WHERE tenant = $1 AND project = $2 AND user_type = $3 AND bundle_id = $4
                  AND active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, tenant, project, user_type, bundle_id)

            # Fall back to global
            if not row:
                row = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.user_quota_policies
                    WHERE tenant = $1 AND project = $2 AND user_type = $3 AND bundle_id = '*'
                      AND active = TRUE
                    ORDER BY created_at DESC
                    LIMIT 1
                """, tenant, project, user_type)

        if row:
            logger.debug(f"Quota policy cache MISS (DB hit): {user_type}/{bundle_id}")
            policy_obj = UserQuotaPolicy(**dict(row))
            quota_policy = policy_obj.to_quota_policy()

            # Cache in Redis
            if self._redis:
                cache_key = self._quota_policy_cache_key(tenant, project, user_type, bundle_id)
                try:
                    await self._redis.setex(
                        cache_key,
                        self.cache_ttl,
                        json.dumps(asdict(quota_policy))
                    )
                except Exception as e:
                    logger.warning(f"Redis cache write error for quota policy: {e}")

            return quota_policy

        return None

    async def set_user_quota_policy(
            self,
            *,
            tenant: str,
            project: str,
            user_type: str,
            bundle_id: str,
            max_concurrent: Optional[int] = None,
            requests_per_day: Optional[int] = None,
            requests_per_month: Optional[int] = None,
            total_requests: Optional[int] = None,
            tokens_per_hour: Optional[int] = None,
            tokens_per_day: Optional[int] = None,
            tokens_per_month: Optional[int] = None,
            created_by: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> UserQuotaPolicy:
        """Create or update quota policy."""
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO {self.CONTROL_PLANE_SCHEMA}.user_quota_policies (
                    tenant, project, user_type, bundle_id,
                    max_concurrent, requests_per_day, requests_per_month, total_requests,
                    tokens_per_hour, tokens_per_day, tokens_per_month,
                    created_by, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (tenant, project, user_type, bundle_id)
                DO UPDATE SET
                    max_concurrent = COALESCE(EXCLUDED.max_concurrent, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.max_concurrent),
                    requests_per_day = COALESCE(EXCLUDED.requests_per_day, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.requests_per_day),
                    requests_per_month = COALESCE(EXCLUDED.requests_per_month, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.requests_per_month),
                    total_requests = COALESCE(EXCLUDED.total_requests, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.total_requests),
                    tokens_per_hour = COALESCE(EXCLUDED.tokens_per_hour, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.tokens_per_hour),
                    tokens_per_day = COALESCE(EXCLUDED.tokens_per_day, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.tokens_per_day),
                    tokens_per_month = COALESCE(EXCLUDED.tokens_per_month, {self.CONTROL_PLANE_SCHEMA}.user_quota_policies.tokens_per_month),
                    created_by = EXCLUDED.created_by,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                RETURNING *
            """,
                                      tenant, project, user_type, bundle_id,
                                      max_concurrent, requests_per_day, requests_per_month, total_requests,
                                      tokens_per_hour, tokens_per_day, tokens_per_month,
                                      created_by, notes
                                      )

        # Invalidate cache
        if self._redis:
            cache_key = self._quota_policy_cache_key(tenant, project, user_type, bundle_id)
            try:
                await self._redis.delete(cache_key)
            except Exception:
                pass

        return UserQuotaPolicy(**dict(row))

    async def list_quota_policies(
            self, *, tenant: Optional[str] = None, project: Optional[str] = None, limit: int = 100
    ) -> List[UserQuotaPolicy]:
        """List all quota policies."""
        if not self._pg_pool:
            return []

        where_clauses = ["active = TRUE"]
        args = []

        if tenant:
            args.append(tenant)
            where_clauses.append(f"tenant = ${len(args)}")
        if project:
            args.append(project)
            where_clauses.append(f"project = ${len(args)}")

        where_sql = " AND ".join(where_clauses)

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.user_quota_policies
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT {int(limit)}
            """, *args)

        return [UserQuotaPolicy(**dict(row)) for row in rows]

    # =========================================================================
    # Application Budget Policy Operations
    # =========================================================================

    def _budget_policy_cache_key(
            self, tenant: str, project: str, bundle_id: str, provider: str
    ) -> str:
        """Build Redis cache key for budget policy."""
        return f"kdcube:cp:budget_policy:{tenant}:{project}:{bundle_id}:{provider}"

    async def get_budget_policy(
            self, *, tenant: str, project: str, bundle_id: str, provider: str
    ) -> Optional[ProviderBudgetPolicy]:
        """Get budget policy for a provider."""
        # Check Redis cache
        if self._redis:
            cache_key = self._budget_policy_cache_key(tenant, project, bundle_id, provider)
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached.decode())
                    if data:
                        logger.debug(f"Budget policy cache HIT (Redis): {bundle_id}/{provider}")
                        return ProviderBudgetPolicy(**data)
            except Exception as e:
                logger.warning(f"Redis cache read error for budget policy: {e}")

        # Query PostgreSQL
        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.application_budget_policies
                WHERE tenant = $1 AND project = $2 AND bundle_id = $3 AND provider = $4
                  AND active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
            """, tenant, project, bundle_id, provider)

        if row:
            logger.debug(f"Budget policy cache MISS (DB hit): {bundle_id}/{provider}")
            policy_obj = ApplicationBudgetPolicy(**dict(row))
            budget_policy = policy_obj.to_provider_budget_policy()

            # Cache in Redis
            if self._redis:
                cache_key = self._budget_policy_cache_key(tenant, project, bundle_id, provider)
                try:
                    await self._redis.setex(
                        cache_key,
                        self.cache_ttl,
                        json.dumps(asdict(budget_policy))
                    )
                except Exception as e:
                    logger.warning(f"Redis cache write error for budget policy: {e}")

            return budget_policy

        return None

    async def set_budget_policy(
            self,
            *,
            tenant: str,
            project: str,
            bundle_id: str,
            provider: str,
            usd_per_hour: Optional[float] = None,
            usd_per_day: Optional[float] = None,
            usd_per_month: Optional[float] = None,
            created_by: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> ApplicationBudgetPolicy:
        """Create or update budget policy."""
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO {self.CONTROL_PLANE_SCHEMA}.application_budget_policies (
                    tenant, project, bundle_id, provider,
                    usd_per_hour, usd_per_day, usd_per_month,
                    created_by, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (tenant, project, bundle_id, provider)
                DO UPDATE SET
                    usd_per_hour = COALESCE(EXCLUDED.usd_per_hour, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_hour),
                    usd_per_day = COALESCE(EXCLUDED.usd_per_day, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_day),
                    usd_per_month = COALESCE(EXCLUDED.usd_per_month, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_month),
                    created_by = EXCLUDED.created_by,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                RETURNING *
            """,
                                      tenant, project, bundle_id, provider,
                                      usd_per_hour, usd_per_day, usd_per_month,
                                      created_by, notes
                                      )

        # Invalidate cache
        if self._redis:
            cache_key = self._budget_policy_cache_key(tenant, project, bundle_id, provider)
            try:
                await self._redis.delete(cache_key)
            except Exception:
                pass

        return ApplicationBudgetPolicy(**dict(row))

    async def list_budget_policies(
            self, *, tenant: Optional[str] = None, project: Optional[str] = None, limit: int = 100
    ) -> List[ApplicationBudgetPolicy]:
        """List all budget policies."""
        if not self._pg_pool:
            return []

        where_clauses = ["active = TRUE"]
        args = []

        if tenant:
            args.append(tenant)
            where_clauses.append(f"tenant = ${len(args)}")
        if project:
            args.append(project)
            where_clauses.append(f"project = ${len(args)}")

        where_sql = " AND ".join(where_clauses)

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.application_budget_policies
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT {int(limit)}
            """, *args)

        return [ApplicationBudgetPolicy(**dict(row)) for row in rows]

    async def get_all_budget_policies_for_bundle(
            self, *, tenant: str, project: str, bundle_id: str
    ) -> Dict[str, ProviderBudgetPolicy]:
        """Get all budget policies for a bundle (returns dict by provider)."""
        if not self._pg_pool:
            return {}

        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.application_budget_policies
                WHERE tenant = $1 AND project = $2 AND bundle_id = $3 AND active = TRUE
                ORDER BY provider
            """, tenant, project, bundle_id)

        policies = {}
        for row in rows:
            policy_obj = ApplicationBudgetPolicy(**dict(row))
            policies[policy_obj.provider] = policy_obj.to_provider_budget_policy()

        return policies
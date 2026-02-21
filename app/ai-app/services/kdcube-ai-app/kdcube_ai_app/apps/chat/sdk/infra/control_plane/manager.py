# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/control_plane/manager.py

"""
Control Plane Manager

Unified manager for:
1. User plan override balance (via PlanOverrideManager)
2. Plan quota policies (base limits by plan id)
3. Application budget policies (spending limits per provider - NO bundle_id!)

All with PostgreSQL storage and Redis caching.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List

import asyncpg
from redis.asyncio import Redis

from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import (
    PlanOverrideManager,
    UserCreditsManager,
    UserPlanBalance,
    UserPlanBalanceSnapshotManager
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy, ProviderBudgetPolicy
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager
from kdcube_ai_app.infra.accounting.usage import quote_tokens_for_usd

logger = logging.getLogger(__name__)


@dataclass
class PlanQuotaPolicy:
    """Base quota policy for a plan (free, payasyougo, admin, etc.)."""
    tenant: str
    project: str
    plan_id: str
    # Policies are global per tenant/project

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
    """Budget policy for a provider (global per tenant/project)."""
    tenant: str
    project: str
    provider: str
    # Budget policies are global per tenant/project

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
    1. Plan Quota Policies (base limits by plan id)
    2. User Plan Balance (plan overrides + lifetime budget) - delegates to PlanOverrideManager
    3. Application Budget POLICIES (spending limits)
    # Stores SPENDING LIMITS per provider
    # Table: application_budget_policies
        - set_tenant_project_budget_policy()
        - list_tenant_project_budget_policies()
        - Table: application_budget_policies
    # Example: "Anthropic can spend max $200/day"
    # set_tenant_project_budget_policy(
    #     provider="anthropic",
    #     usd_per_day=200.0  # LIMIT
    # )

    All with PostgreSQL + Redis caching.
    """

    CONTROL_PLANE_SCHEMA = "kdcube_control_plane"

    def __init__(
            self,
            pg_pool: Optional[asyncpg.Pool] = None,
            redis: Optional[Redis] = None,
            *,
            cache_ttl: int = 60,  # 60 seconds for policies
            plan_balance_cache_ttl: int = 10,  # 10 seconds for plan override balance
    ):
        """
        Initialize Control Plane Manager.

        Args:
            pg_pool: asyncpg connection pool
            redis: Redis client for caching
            cache_ttl: Cache TTL for policies in seconds (default: 60)
            plan_balance_cache_ttl: Cache TTL for plan override balance (default: 10)
        """
        self._pg_pool = pg_pool
        self._redis = redis
        self.cache_ttl = cache_ttl

        # Initialize PlanOverrideManager (delegates to it)
        self.plan_override_mgr = PlanOverrideManager(
            pg_pool=pg_pool,
            redis=redis,
            cache_ttl=plan_balance_cache_ttl,
        )
        self.user_credits_mgr = UserCreditsManager(
            pg_pool=pg_pool,
            redis=redis,
            cache_ttl=plan_balance_cache_ttl,
        )
        self.plan_balance_snapshot_mgr = UserPlanBalanceSnapshotManager(pg_pool=pg_pool)
        self.subscription_mgr = SubscriptionManager(pg_pool=pg_pool)

        # Track if we own the pool/redis
        self._owns_pool = pg_pool is None
        self._owns_redis = redis is None

    async def init(self, *, redis_url: Optional[str] = None):
        """Initialize connections if not provided."""
        # Initialize via plan override balance manager
        await self.plan_override_mgr.init(redis_url=redis_url)
        await self.user_credits_mgr.init(redis_url=redis_url)

        # Share connections
        self._pg_pool = self.plan_override_mgr._pg_pool
        self._redis = self.plan_override_mgr._redis
        self.plan_balance_snapshot_mgr.set_pg_pool(self._pg_pool)

    async def close(self):
        """Close connections."""
        await self.plan_override_mgr.close()
        await self.user_credits_mgr.close()

    # =========================================================================
    # Plan Balance Operations
    # =========================================================================

    async def get_user_plan_balance(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            include_expired: bool = False,
    ) -> Optional[UserPlanBalance]:
        return await self.plan_balance_snapshot_mgr.get_user_plan_balance(
            tenant=tenant,
            project=project,
            user_id=user_id,
            include_expired=include_expired,
        )


    async def update_user_plan_override(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            # Plan override fields (PARTIAL UPDATES SUPPORTED!)
            max_concurrent: Optional[int] = None,
            requests_per_day: Optional[int] = None,
            requests_per_month: Optional[int] = None,
            total_requests: Optional[int] = None,
            tokens_per_hour: Optional[int] = None,
            tokens_per_day: Optional[int] = None,
            tokens_per_month: Optional[int] = None,
            # Metadata
            expires_at: Optional[datetime] = None,
            purchase_id: Optional[str] = None,
            purchase_amount_usd: Optional[float] = None,
            purchase_notes: Optional[str] = None,
    ) -> UserPlanBalance:
        """Update user's plan override (supports partial updates via COALESCE)."""
        await self.plan_override_mgr.update_user_plan_override(
            tenant=tenant, project=project, user_id=user_id,
            max_concurrent=max_concurrent,
            requests_per_day=requests_per_day,
            requests_per_month=requests_per_month,
            total_requests=total_requests,
            tokens_per_hour=tokens_per_hour,
            tokens_per_day=tokens_per_day,
            tokens_per_month=tokens_per_month,
            expires_at=expires_at,
            grant_id=purchase_id,
            grant_amount_usd=purchase_amount_usd,
            grant_notes=purchase_notes,
        )
        return await self.get_user_plan_balance(tenant=tenant, project=project, user_id=user_id)

    async def deactivate_plan_override(
            self, *, tenant: str, project: str, user_id: str
    ):
        """Deactivate plan override balance."""
        await self.plan_override_mgr.deactivate_plan_override(
            tenant=tenant, project=project, user_id=user_id
        )

    async def add_user_credits_usd(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            usd_amount: float,
            ref_provider: str = "anthropic",
            ref_model: str = "claude-sonnet-4-5-20250929",
            purchase_id: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> UserPlanBalance:
        """
        Add purchased credits in USD (converted to lifetime tokens).
        Uses plan override balance manager's add_lifetime_tokens method.
        """


        # from kdcube_ai_app.infra.accounting.usage import _find_llm_price
        #
        # # Convert USD to tokens
        # pr = _find_llm_price(ref_provider, ref_model)
        # p_ref_out = float(pr["output_tokens_1M"]) / 1_000_000
        # tokens = int(usd_amount / p_ref_out)

        tokens, usd_per_token = quote_tokens_for_usd(
            usd_amount=usd_amount,
            ref_provider=ref_provider,
            ref_model=ref_model,
        )

        # Delegate to plan override balance manager
        await self.user_credits_mgr.add_lifetime_tokens(
            tenant=tenant,
            project=project,
            user_id=user_id,
            tokens=tokens,
            usd_amount=usd_amount,
            purchase_id=purchase_id,
            notes=notes or f"USD purchase: ${usd_amount:.2f} → {tokens:,} tokens",
        )

        # Return the canonical flattened snapshot (single SQL)
        bal = await self.get_user_plan_balance(tenant=tenant, project=project, user_id=user_id, include_expired=True)
        if not bal:
            # extremely unlikely after add_lifetime_tokens succeeds
            raise RuntimeError("Failed to load plan override balance after adding credits")
        return bal

    # =========================================================================
    # Plan Quota Policy Operations
    # =========================================================================

    def _quota_policy_cache_key(
            self, tenant: str, project: str, plan_id: str
    ) -> str:
        """Build Redis cache key for quota policy."""
        return f"kdcube:cp:quota_policy:{tenant}:{project}:{plan_id}"

    async def get_plan_quota_policy(
            self, *, tenant: str, project: str, plan_id: str,
    ) -> Optional[QuotaPolicy]:
        """
        Get quota policy for a plan.

        Lookup order:
        1. Redis cache
        2. PostgreSQL

        Returns QuotaPolicy or None.
        """
        # Check Redis cache
        if self._redis:
            cache_key = self._quota_policy_cache_key(tenant, project, plan_id)
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached.decode())
                    if data:
                        logger.debug(f"Quota policy cache HIT (Redis): {plan_id}")
                        return QuotaPolicy(**data)
            except Exception as e:
                logger.warning(f"Redis cache read error for quota policy: {e}")

        # Query PostgreSQL
        if not self._pg_pool:
            return None

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies
                WHERE tenant = $1 AND project = $2 AND plan_id = $3
                  AND active = TRUE
                LIMIT 1
            """, tenant, project, plan_id)

        if row:
            logger.debug(f"Quota policy cache MISS (DB hit): {plan_id}")
            policy_obj = PlanQuotaPolicy(**dict(row))
            quota_policy = policy_obj.to_quota_policy()

            # Cache in Redis
            if self._redis:
                cache_key = self._quota_policy_cache_key(tenant, project, plan_id)
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

    async def set_tenant_project_plan_quota_policy(
            self,
            *,
            tenant: str,
            project: str,
            plan_id: str,
            max_concurrent: Optional[int] = None,
            requests_per_day: Optional[int] = None,
            requests_per_month: Optional[int] = None,
            total_requests: Optional[int] = None,
            tokens_per_hour: Optional[int] = None,
            tokens_per_day: Optional[int] = None,
            tokens_per_month: Optional[int] = None,
            created_by: Optional[str] = None,
            notes: Optional[str] = None,
    ) -> PlanQuotaPolicy:
        """Create or update quota policy (supports partial updates via COALESCE)."""
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL pool not initialized")

        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies (
                    tenant, project, plan_id,
                    max_concurrent, requests_per_day, requests_per_month, total_requests,
                    tokens_per_hour, tokens_per_day, tokens_per_month,
                    created_by, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (tenant, project, plan_id)
                DO UPDATE SET
                    max_concurrent = COALESCE(EXCLUDED.max_concurrent, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.max_concurrent),
                    requests_per_day = COALESCE(EXCLUDED.requests_per_day, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.requests_per_day),
                    requests_per_month = COALESCE(EXCLUDED.requests_per_month, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.requests_per_month),
                    total_requests = COALESCE(EXCLUDED.total_requests, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.total_requests),
                    tokens_per_hour = COALESCE(EXCLUDED.tokens_per_hour, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.tokens_per_hour),
                    tokens_per_day = COALESCE(EXCLUDED.tokens_per_day, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.tokens_per_day),
                    tokens_per_month = COALESCE(EXCLUDED.tokens_per_month, {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies.tokens_per_month),
                    created_by = EXCLUDED.created_by,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                RETURNING *
            """,
                                      tenant, project, plan_id,
                                      max_concurrent, requests_per_day, requests_per_month, total_requests,
                                      tokens_per_hour, tokens_per_day, tokens_per_month,
                                      created_by, notes
                                      )

        # Invalidate cache
        if self._redis:
            cache_key = self._quota_policy_cache_key(tenant, project, plan_id)
            try:
                await self._redis.delete(cache_key)
            except Exception:
                pass

        return PlanQuotaPolicy(**dict(row))

    async def list_plan_quota_policies(
            self, *, tenant: Optional[str] = None, project: Optional[str] = None, limit: int = 100
    ) -> List[PlanQuotaPolicy]:
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
                FROM {self.CONTROL_PLANE_SCHEMA}.plan_quota_policies
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT {int(limit)}
            """, *args)

        return [PlanQuotaPolicy(**dict(row)) for row in rows]

    async def tenant_project_plan_quota_policies_initialize_from_master_app(self,
                                                                            tenant: str,
                                                                            project: str,
                                                                            bundle_id: str,
                                                                            app_quota_policies,
                                                                            app_budget_policies):
        """
        Ensure policies are seeded from bundle configuration (one-time operation).

        Uses Redis distributed lock to prevent concurrent initialization.
        Idempotent - safe to call multiple times.

        Flow:
        1. Check if already initialized (instance flag)
        2. Acquire distributed lock (Redis SET NX)
        3. Check if policies exist in database
        4. If not, seed from bundle configuration
        5. Release lock
        """

        # Distributed lock key (global across all instances)
        lock_key = f"kdcube:cp:init_lock:{tenant}:{project}"
        lock_ttl = 30  # 30 seconds

        policies_initialized = False
        lock_acquired = False
        try:
            # Try to acquire distributed lock
            lock_acquired = await self._redis.set(
                lock_key, "1", ex=lock_ttl, nx=True
            )

            if not lock_acquired:
                # Another instance is initializing, wait a bit then assume done
                logger.info(
                    f"[tenant_project_plan_quota_policies_initialize_from_master_app] Lock held by another instance, waiting..."
                )
                import asyncio
                await asyncio.sleep(2)
                policies_initialized = True
                return policies_initialized

            # We have the lock - check if policies exist
            existing_policies = await self.list_plan_quota_policies(
                tenant=tenant,
                project=project,
                limit=1,
            )

            if existing_policies:
                logger.debug(
                    f"[ensure_policies_initialized] Policies already exist, skipping seed"
                )
                policies_initialized = True
                return policies_initialized

            # Seed policies from bundle configuration
            logger.info(
                f"[ensure_policies_initialized] Seeding policies from bundle config..."
            )

            # Get hardcoded policies from bundle
            bundle_quota_policies = app_quota_policies
            bundle_budget_policies = app_budget_policies

            # Seed quota policies
            for plan_id, policy in bundle_quota_policies.items():
                try:
                    await self.set_tenant_project_plan_quota_policy(
                        tenant=tenant,
                        project=project,
                        plan_id=plan_id,
                        max_concurrent=policy.max_concurrent,
                        requests_per_day=policy.requests_per_day,
                        requests_per_month=policy.requests_per_month,
                        total_requests=policy.total_requests,
                        tokens_per_hour=policy.tokens_per_hour,
                        tokens_per_day=policy.tokens_per_day,
                        tokens_per_month=policy.tokens_per_month,
                        created_by="bundle_seed",
                        notes=f"Seeded from {bundle_id} configuration",
                    )
                    logger.info(
                        f"[ensure_policies_initialized] Seeded quota policy for {plan_id}"
                    )
                except Exception as e:
                    logger.exception(
                        f"[ensure_policies_initialized] Failed to seed {plan_id}: {e}"
                    )

            # Seed budget policies
            for provider, policy in bundle_budget_policies.items():
                try:
                    await self.set_tenant_project_budget_policy(
                        tenant=tenant,
                        project=project,
                        provider=provider,
                        usd_per_hour=policy.usd_per_hour,
                        usd_per_day=policy.usd_per_day,
                        usd_per_month=policy.usd_per_month,
                        created_by="bundle_seed",
                        notes=f"Seeded from {bundle_id} configuration",
                    )
                    logger.info(f"[ensure_policies_initialized] Seeded budget policy for {provider}")
                except Exception as e:
                    logger.exception(f"[ensure_policies_initialized] Failed to seed {provider}: {e}")

            logger.info(f"[ensure_policies_initialized] Policy seeding complete")
            policies_initialized = True

        finally:
            # Release lock
            try:
                if lock_acquired:
                    await self._redis.delete(lock_key)
            except Exception:
                pass
        return policies_initialized

    # =========================================================================
    # Tenant / Project (Application) Budget Policy Operations
    # =========================================================================

    def _budget_policy_cache_key(
            self, tenant: str, project: str, provider: str
    ) -> str:
        """Build Redis cache key for budget policy."""
        return f"kdcube:cp:budget_policy:{tenant}:{project}:{provider}"

    async def set_tenant_project_budget_policy(
            self,
            *,
            tenant: str,
            project: str,
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
                    tenant, project, provider,
                    usd_per_hour, usd_per_day, usd_per_month,
                    created_by, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (tenant, project, provider)
                DO UPDATE SET
                    usd_per_hour = COALESCE(EXCLUDED.usd_per_hour, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_hour),
                    usd_per_day = COALESCE(EXCLUDED.usd_per_day, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_day),
                    usd_per_month = COALESCE(EXCLUDED.usd_per_month, {self.CONTROL_PLANE_SCHEMA}.application_budget_policies.usd_per_month),
                    created_by = EXCLUDED.created_by,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                RETURNING *
            """,
                                      tenant, project, provider,
                                      usd_per_hour, usd_per_day, usd_per_month,
                                      created_by, notes
                                      )

        # Invalidate cache
        if self._redis:
            cache_key = self._budget_policy_cache_key(tenant, project, provider)
            try:
                await self._redis.delete(cache_key)
            except Exception:
                pass

        return ApplicationBudgetPolicy(**dict(row))

    async def list_tenant_project_budget_policies(
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

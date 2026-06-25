# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/subscription.py
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import asyncpg
from calendar import monthrange

from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema

def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlanChangeNotAllowed(Exception):
    """Raised when a requested subscription plan change violates the resub rules."""
    def __init__(self, message: str, *, code: str = "plan_change_not_allowed"):
        super().__init__(message)
        self.message = message
        self.code = code


def _add_one_month(dt: datetime) -> datetime:
    # Preserve time + tz, clamp day (e.g. Jan 31 -> Feb 28/29)
    dt = dt.astimezone(timezone.utc)
    y = dt.year + (dt.month // 12)
    m = (dt.month % 12) + 1
    last_day = monthrange(y, m)[1]
    d = min(dt.day, last_day)
    return dt.replace(year=y, month=m, day=d)

def _sub_one_month(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    if dt.month == 1:
        y, m = dt.year - 1, 12
    else:
        y, m = dt.year, dt.month - 1
    last_day = monthrange(y, m)[1]
    d = min(dt.day, last_day)
    return dt.replace(year=y, month=m, day=d)

def _period_key_for_subscription(
    *,
    tenant: str,
    project: str,
    user_id: str,
    provider: str,
    stripe_subscription_id: Optional[str],
    period_end: datetime,
) -> str:
    if provider == "stripe" and stripe_subscription_id:
        return f"stripe:{stripe_subscription_id}:{period_end.isoformat()}"
    return f"internal:{tenant}:{project}:{user_id}:{period_end.isoformat()}"

def build_subscription_period_descriptor(
    *,
    tenant: str,
    project: str,
    user_id: str,
    provider: str,
    stripe_subscription_id: Optional[str],
    period_end: Optional[datetime],
    period_start: Optional[datetime],
    fallback_charged_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    if period_end is None:
        base = fallback_charged_at or _now()
        period_end = _add_one_month(base)
    if period_start is None:
        period_start = _sub_one_month(period_end)

    period_key = _period_key_for_subscription(
        tenant=tenant,
        project=project,
        user_id=user_id,
        provider=provider,
        stripe_subscription_id=stripe_subscription_id,
        period_end=period_end,
    )
    return {
        "period_key": period_key,
        "period_start": period_start,
        "period_end": period_end,
    }

@dataclass(frozen=True)
class Subscription:
    tenant: str
    project: str
    user_id: str
    plan_id: Optional[str]
    status: str
    monthly_price_cents: int
    started_at: datetime
    next_charge_at: Optional[datetime]
    last_charged_at: Optional[datetime]
    provider: str
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    rl_month_anchor_at: Optional[datetime] = None


@dataclass(frozen=True)
class SubscriptionPlan:
    tenant: str
    project: str
    plan_id: str
    provider: str
    stripe_price_id: Optional[str]
    monthly_price_cents: int
    active: bool
    metadata: Optional[dict]
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]
    notes: Optional[str]


class RLMonthAnchorStore:
    """
    Durable mirror of the RL monthly-window anchor.
    """

    TABLE = "user_subscriptions"

    def __init__(self, pg_pool: asyncpg.Pool):
        self.pg_pool = pg_pool

    @staticmethod
    def _split_subject(subject_id: str) -> Optional[Tuple[str, str, str]]:
        # subject_id_of(tenant, project, user_id) -> "{tenant}:{project}:{user_id}".
        # maxsplit=2 keeps any colons inside user_id intact.
        if not subject_id:
            return None
        parts = subject_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            return None
        return parts[0], parts[1], parts[2]

    async def load(self, subject_id: str) -> Optional[datetime]:
        ident = self._split_subject(subject_id)
        if not ident:
            return None
        tenant, project, user_id = ident
        schema = _project_schema(tenant, project)
        sql = (
            f"SELECT rl_month_anchor_at FROM {schema}.{self.TABLE} "
            f"WHERE tenant=$1 AND project=$2 AND user_id=$3"
        )
        try:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id)
        except Exception:
            return None
        if not row:
            return None
        return row["rl_month_anchor_at"]

    async def save_if_absent(self, subject_id: str, anchor_at: datetime) -> None:
        ident = self._split_subject(subject_id)
        if not ident:
            return
        tenant, project, user_id = ident
        if anchor_at.tzinfo is None:
            anchor_at = anchor_at.replace(tzinfo=timezone.utc)
        schema = _project_schema(tenant, project)
        sql = (
            f"UPDATE {schema}.{self.TABLE} "
            f"SET rl_month_anchor_at=$4 "
            f"WHERE tenant=$1 AND project=$2 AND user_id=$3 AND rl_month_anchor_at IS NULL"
        )
        try:
            async with self.pg_pool.acquire() as c:
                await c.execute(sql, tenant, project, user_id, anchor_at)
        except Exception:
            # Non-fatal: the Redis anchor is still authoritative this period; we
            # just lose the durable mirror until the next write.
            return

    async def save(self, subject_id: str, anchor_at: datetime) -> None:
        """Overwrite the durable monthly-window anchor unconditionally (re-anchor)."""
        ident = self._split_subject(subject_id)
        if not ident:
            return
        tenant, project, user_id = ident
        if anchor_at.tzinfo is None:
            anchor_at = anchor_at.replace(tzinfo=timezone.utc)
        schema = _project_schema(tenant, project)
        sql = (
            f"UPDATE {schema}.{self.TABLE} "
            f"SET rl_month_anchor_at=$4 "
            f"WHERE tenant=$1 AND project=$2 AND user_id=$3"
        )
        try:
            async with self.pg_pool.acquire() as c:
                await c.execute(sql, tenant, project, user_id, anchor_at)
        except Exception:
            return


class SubscriptionManager:
    TABLE = "user_subscriptions"
    PLAN_TABLE = "subscription_plans"
    EXT_EVENTS_TABLE = "external_economics_events"

    def __init__(self, pg_pool: asyncpg.Pool):
        self.pg_pool = pg_pool

    @staticmethod
    def _schema(tenant: str, project: str) -> str:
        return _project_schema(tenant, project)

    def _from_row(self, row: asyncpg.Record) -> Subscription:
        return Subscription(**dict(row))

    def _plan_from_row(self, row: asyncpg.Record) -> SubscriptionPlan:
        return SubscriptionPlan(**dict(row))

    async def get_plan(
        self,
        *,
        tenant: str,
        project: str,
        plan_id: str,
        include_inactive: bool = False,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[SubscriptionPlan]:
        if not plan_id:
            return None
        active_clause = "" if include_inactive else "AND active = TRUE"
        sql = f"""
            SELECT *
            FROM {self._schema(tenant, project)}.{self.PLAN_TABLE}
            WHERE tenant=$1 AND project=$2 AND plan_id=$3
              {active_clause}
            LIMIT 1
        """
        if conn:
            row = await conn.fetchrow(sql, tenant, project, plan_id)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, plan_id)
        return self._plan_from_row(row) if row else None

    async def get_plan_by_stripe_price_id(
        self,
        *,
        tenant: str,
        project: str,
        stripe_price_id: str,
        include_inactive: bool = False,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[SubscriptionPlan]:
        if not stripe_price_id:
            return None
        active_clause = "" if include_inactive else "AND active = TRUE"
        sql = f"""
            SELECT *
            FROM {self._schema(tenant, project)}.{self.PLAN_TABLE}
            WHERE tenant=$1 AND project=$2
              AND provider='stripe' AND stripe_price_id=$3
              {active_clause}
            LIMIT 1
        """
        if conn:
            row = await conn.fetchrow(sql, tenant, project, stripe_price_id)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, stripe_price_id)
        return self._plan_from_row(row) if row else None

    async def list_plans(
        self,
        *,
        tenant: str,
        project: str,
        provider: Optional[str] = None,
        active_only: bool = True,
        limit: int = 200,
        offset: int = 0,
        conn: Optional[asyncpg.Connection] = None,
    ) -> List[SubscriptionPlan]:
        clauses = ["tenant=$1", "project=$2"]
        args: list = [tenant, project]
        idx = 3
        if provider:
            clauses.append(f"provider=${idx}")
            args.append(provider)
            idx += 1
        if active_only:
            clauses.append("active = TRUE")

        sql = f"""
            SELECT *
            FROM {self._schema(tenant, project)}.{self.PLAN_TABLE}
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        args.extend([limit, offset])

        if conn:
            rows = await conn.fetch(sql, *args)
        else:
            async with self.pg_pool.acquire() as c:
                rows = await c.fetch(sql, *args)
        return [self._plan_from_row(r) for r in rows]

    async def upsert_plan(
        self,
        *,
        tenant: str,
        project: str,
        plan_id: str,
        provider: str,
        stripe_price_id: Optional[str] = None,
        monthly_price_cents: int = 0,
        active: bool = True,
        metadata: Optional[dict] = None,
        created_by: Optional[str] = None,
        notes: Optional[str] = None,
        conn: Optional[asyncpg.Connection] = None,
    ) -> SubscriptionPlan:
        tbl = f"{self._schema(tenant, project)}.{self.PLAN_TABLE}"
        sql = f"""
            INSERT INTO {tbl} (
              tenant, project, plan_id,
              provider, stripe_price_id, monthly_price_cents,
              active, metadata, created_by, notes
            ) VALUES (
              $1,$2,$3,
              $4,$5,$6,
              $7,$8,$9,$10
            )
            ON CONFLICT (tenant, project, plan_id)
            DO UPDATE SET
              provider = COALESCE(EXCLUDED.provider, {tbl}.provider),
              stripe_price_id = COALESCE(EXCLUDED.stripe_price_id, {tbl}.stripe_price_id),
              monthly_price_cents = COALESCE(EXCLUDED.monthly_price_cents, {tbl}.monthly_price_cents),
              active = COALESCE(EXCLUDED.active, {tbl}.active),
              metadata = COALESCE(EXCLUDED.metadata, {tbl}.metadata),
              created_by = COALESCE(EXCLUDED.created_by, {tbl}.created_by),
              notes = COALESCE(EXCLUDED.notes, {tbl}.notes),
              updated_at = NOW()
            RETURNING *
        """
        if conn:
            row = await conn.fetchrow(
                sql,
                tenant,
                project,
                plan_id,
                provider,
                stripe_price_id,
                int(monthly_price_cents),
                bool(active),
                json.dumps(metadata) if metadata is not None else None,
                created_by,
                notes,
            )
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(
                    sql,
                    tenant,
                    project,
                    plan_id,
                    provider,
                    stripe_price_id,
                    int(monthly_price_cents),
                    bool(active),
                    json.dumps(metadata) if metadata is not None else None,
                    created_by,
                    notes,
                )
        return self._plan_from_row(row)

    async def get_subscription(
        self,
        *,
        tenant: str,
        project: str,
        user_id: str,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        sql = f"SELECT * FROM {self._schema(tenant, project)}.{self.TABLE} WHERE tenant=$1 AND project=$2 AND user_id=$3"
        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id)
        return self._from_row(row) if row else None

    async def get_subscription_by_stripe_id(
        self,
        *,
        tenant: str,
        project: str,
        stripe_subscription_id: str,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        if not stripe_subscription_id:
            return None
        sql = f"SELECT * FROM {self._schema(tenant, project)}.{self.TABLE} WHERE provider='stripe' AND stripe_subscription_id=$1"
        if conn:
            row = await conn.fetchrow(sql, stripe_subscription_id)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, stripe_subscription_id)
        return self._from_row(row) if row else None

    async def ensure_subscription_for_user(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            plan_id: str,
            now: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        """
        Ensure a subscription row exists for a user (internal bootstrap).

        Rules:
          - If an existing row is an ACTIVE provider='stripe' subscription, DO
            NOT MODIFY it. A non-active stripe row (canceled/past_due/...) may be
            overridden onto an internal plan.
          - Otherwise, ensure/refresh an internal row.
          - Internal plans carry no charge schedule: next_charge_at is always NULL.
        """
        now = now or _now()
        plan = await self.get_plan(tenant=tenant, project=project, plan_id=plan_id, conn=conn)
        if not plan:
            raise ValueError(f"subscription plan not found: {plan_id}")
        if plan.provider != "internal":
            raise ValueError(f"plan provider must be internal: {plan_id}")

        price = int(plan.monthly_price_cents)

        tbl = f"{self._schema(tenant, project)}.{self.TABLE}"

        sql = f"""
        INSERT INTO {tbl} (
          tenant, project, user_id,
          plan_id, status, monthly_price_cents,
          started_at, next_charge_at, last_charged_at,
          provider, stripe_customer_id, stripe_subscription_id
        ) VALUES (
          $1,$2,$3,
          $4,'active',$5,
          $6::timestamptz,
          NULL,
          NULL,
          'internal', NULL, NULL
        )
        ON CONFLICT (tenant, project, user_id)
        DO UPDATE SET
          plan_id = EXCLUDED.plan_id,
          status = 'active',
          monthly_price_cents = EXCLUDED.monthly_price_cents,

          -- keep started_at stable
          started_at = {tbl}.started_at,

          next_charge_at = NULL,

          provider = 'internal',

          stripe_customer_id = NULL,
          stripe_subscription_id = NULL,

          updated_at = NOW()
        WHERE {tbl}.provider IS DISTINCT FROM 'stripe' OR {tbl}.status <> 'active'
        RETURNING *
        """

        async def _run(c: asyncpg.Connection) -> asyncpg.Record:
            row = await c.fetchrow(sql, tenant, project, user_id, plan.plan_id, price, now)
            if row:
                return row
            # If conflict row is an ACTIVE stripe sub => UPDATE skipped => RETURNING empty => fetch existing row
            existing = await c.fetchrow(
                f"SELECT * FROM {tbl} WHERE tenant=$1 AND project=$2 AND user_id=$3",
                tenant, project, user_id
            )
            if not existing:
                raise RuntimeError(f"failed to ensure subscription row for {tenant}/{project}/{user_id}")
            return existing

        if conn:
            row = await _run(conn)
        else:
            async with self.pg_pool.acquire() as c:
                row = await _run(c)

        return self._from_row(row)


    async def ensure_baseline_subscription(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            plan_id: str,
            now: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        """
        Ensure a free/admin BASELINE subscription row exists for a user.

        Differs from `ensure_subscription_for_user`:
          - The plan MUST be internal and zero-cost (monthly_price_cents == 0);
            baseline plans are never chargeable.
          - It carries no charge schedule: next_charge_at is NULL.
          - It NEVER clobbers an existing row. If the user already has any
            subscription (a paid catalog plan, a stripe row, or an existing
            baseline), it is left untouched and returned as-is.
        """
        now = now or _now()
        plan = await self.get_plan(tenant=tenant, project=project, plan_id=plan_id, conn=conn)
        if not plan:
            raise ValueError(f"subscription plan not found: {plan_id}")
        if plan.provider != "internal":
            raise ValueError(f"baseline plan provider must be internal: {plan_id}")
        if int(plan.monthly_price_cents) != 0:
            raise ValueError(f"baseline plan must be zero-cost: {plan_id}")

        tbl = f"{self._schema(tenant, project)}.{self.TABLE}"

        # Insert a zero-cost internal row with no charge schedule (next_charge_at NULL).
        # ON CONFLICT DO NOTHING: never touch an existing row (paid/stripe/baseline).
        sql = f"""
        INSERT INTO {tbl} (
          tenant, project, user_id,
          plan_id, status, monthly_price_cents,
          started_at, next_charge_at, last_charged_at,
          provider, stripe_customer_id, stripe_subscription_id
        ) VALUES (
          $1,$2,$3,
          $4,'active',0,
          $5::timestamptz,
          NULL,
          $5::timestamptz,
          'internal', NULL, NULL
        )
        ON CONFLICT (tenant, project, user_id) DO NOTHING
        RETURNING *
        """

        async def _run(c: asyncpg.Connection) -> asyncpg.Record:
            row = await c.fetchrow(sql, tenant, project, user_id, plan.plan_id, now)
            if row:
                return row
            # Row already existed (DO NOTHING) => fetch and return it untouched.
            existing = await c.fetchrow(
                f"SELECT * FROM {tbl} WHERE tenant=$1 AND project=$2 AND user_id=$3",
                tenant, project, user_id
            )
            if not existing:
                raise RuntimeError(
                    f"failed to ensure baseline subscription for {tenant}/{project}/{user_id}"
                )
            return existing

        if conn:
            row = await _run(conn)
        else:
            async with self.pg_pool.acquire() as c:
                row = await _run(c)

        return self._from_row(row)

    def assert_plan_change_allowed(
            self,
            *,
            current: Optional[Subscription],
            target_plan: SubscriptionPlan,
            operator: bool = False,
    ) -> None:
        """
        Central guard for re-subscription. Raises PlanChangeNotAllowed when a
        requested plan change violates the resub rules.

        Rules (self-serve, operator=False):
          - admin is locked: a user currently on `admin` cannot move to any
            other plan.
          - `wallet`/`anonymous` are quota-only policies, never subscribable
            plans — they can never be a target (and should not exist in
            subscription_plans, this is defense-in-depth).
          - free (or no subscription yet) may only move to a chargeable plan
            (monthly_price_cents > 0); moving free→free / free→zero-cost is a
            no-op that the baseline already covers.

        Paid→paid changes are allowed (e.g. plan upgrade/downgrade between
        chargeable catalog plans); the caller owns the money flow.

        operator=True is the admin/operator override: only the quota-only target
        sanity check applies (operators may grant admin/free, or move a user who
        is currently on admin). `current` is unused in this mode.
        """
        target_id = (target_plan.plan_id or "").strip().lower()
        if target_id in ("wallet", "anonymous"):
            raise PlanChangeNotAllowed(
                f"plan '{target_plan.plan_id}' is a quota-only policy and cannot be subscribed to",
                code="target_not_subscribable",
            )

        if operator:
            return

        cur_id = ((current.plan_id if current else None) or "").strip().lower()
        if cur_id == "admin":
            raise PlanChangeNotAllowed(
                "admin users cannot re-subscribe to another plan",
                code="admin_plan_locked",
            )

        target_price = int(target_plan.monthly_price_cents or 0)
        # free (or unsubscribed) may only re-subscribe to a chargeable plan.
        if cur_id in ("", "free") and target_price <= 0:
            raise PlanChangeNotAllowed(
                "free users may only re-subscribe to a paid plan",
                code="free_requires_paid_plan",
            )

    async def upsert_from_stripe_invoice_paid(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            monthly_price_cents: int,
            stripe_customer_id: Optional[str],
            stripe_subscription_id: Optional[str],
            next_charge_at: Optional[datetime],
            charged_at: Optional[datetime] = None,
            plan_id: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        charged_at = charged_at or _now()

        sql = f"""
        INSERT INTO {self._schema(tenant, project)}.{self.TABLE} (
          tenant, project, user_id,
          plan_id, status, monthly_price_cents,
          started_at, next_charge_at, last_charged_at,
          provider, stripe_customer_id, stripe_subscription_id
        ) VALUES ($1,$2,$3, $4,'active',$5, NOW(), $6,$7, 'stripe', $8,$9)
        ON CONFLICT (tenant, project, user_id)
        DO UPDATE SET
          plan_id=COALESCE(EXCLUDED.plan_id, {self._schema(tenant, project)}.{self.TABLE}.plan_id),
          status='active',
          monthly_price_cents=EXCLUDED.monthly_price_cents,
          -- keep started_at stable (don’t reset on renewals)
          started_at={self._schema(tenant, project)}.{self.TABLE}.started_at,
          last_charged_at=EXCLUDED.last_charged_at,
          next_charge_at=COALESCE(EXCLUDED.next_charge_at, {self._schema(tenant, project)}.{self.TABLE}.next_charge_at),
          provider='stripe',
          stripe_customer_id=COALESCE(EXCLUDED.stripe_customer_id, {self._schema(tenant, project)}.{self.TABLE}.stripe_customer_id),
          stripe_subscription_id=COALESCE(EXCLUDED.stripe_subscription_id, {self._schema(tenant, project)}.{self.TABLE}.stripe_subscription_id),
          updated_at=NOW()
        RETURNING *
        """

        if conn:
            row = await conn.fetchrow(
                sql, tenant, project, user_id, plan_id, int(monthly_price_cents),
                next_charge_at, charged_at, stripe_customer_id, stripe_subscription_id
            )
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(
                    sql, tenant, project, user_id, plan_id, int(monthly_price_cents),
                    next_charge_at, charged_at, stripe_customer_id, stripe_subscription_id
                )
        return self._from_row(row)

    async def update_status_by_stripe_id(
            self,
            *,
            tenant: str,
            project: str,
            stripe_subscription_id: str,
            status: str,
            next_charge_at: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        if not stripe_subscription_id:
            return None
        # We use COALESCE($3, next_charge_at) to ensure we don't accidentally clear the period end
        # during intermediate status updates (like soft cancellation).
        sql = f"""
            UPDATE {self._schema(tenant, project)}.{self.TABLE}
            SET status=$2,
                next_charge_at=COALESCE($3, next_charge_at),
                updated_at=NOW()
            WHERE provider='stripe' AND stripe_subscription_id=$1
            RETURNING *
        """
        if conn:
            row = await conn.fetchrow(sql, stripe_subscription_id, status, next_charge_at)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, stripe_subscription_id, status, next_charge_at)
        return self._from_row(row) if row else None

    async def list_due_internal_paid_subscriptions(
            self,
            *,
            tenant: str,
            project: str,
            now: Optional[datetime] = None,
            limit: int = 500,
            conn: Optional[asyncpg.Connection] = None,
    ) -> List[Subscription]:
        now = now or _now()
        sql = f"""
        SELECT *
        FROM {self._schema(tenant, project)}.{self.TABLE}
        WHERE tenant=$1 AND project=$2
          AND provider='internal'
          AND monthly_price_cents > 0
          AND status='active'
          AND next_charge_at IS NOT NULL
          AND next_charge_at <= $3
        ORDER BY next_charge_at ASC
        LIMIT {int(limit)}
        """
        if conn:
            rows = await conn.fetch(sql, tenant, project, now)
        else:
            async with self.pg_pool.acquire() as c:
                rows = await c.fetch(sql, tenant, project, now)
        return [self._from_row(r) for r in rows]

    async def list_subscriptions(
            self,
            *,
            tenant: str,
            project: str,
            provider: Optional[str] = None,
            user_id: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
            conn: Optional[asyncpg.Connection] = None,
    ) -> List[Subscription]:
        tbl = f"{self._schema(tenant, project)}.{self.TABLE}"
        where = ["tenant=$1", "project=$2"]
        args = [tenant, project]
        i = 3

        if provider:
            where.append(f"provider=${i}")
            args.append(provider)
            i += 1
        if user_id:
            where.append(f"user_id=${i}")
            args.append(user_id)
            i += 1

        sql = f"""
        SELECT *
        FROM {tbl}
        WHERE {" AND ".join(where)}
        ORDER BY updated_at DESC
        LIMIT {int(limit)} OFFSET {int(offset)}
        """

        if conn:
            rows = await conn.fetch(sql, *args)
        else:
            async with self.pg_pool.acquire() as c:
                rows = await c.fetch(sql, *args)

        return [self._from_row(r) for r in rows]

    # ---------------- internal idempotency (no SQL in routes) ----------------

    async def _lock_or_create_internal_event(
            self,
            *,
            conn: asyncpg.Connection,
            kind: str,
            external_id: str,
            tenant: str,
            project: str,
            user_id: str,
            amount_cents: int,
            metadata: Dict[str, Any],
    ) -> Tuple[str, bool]:
        """Returns (status, was_created)"""
        tbl = f"{self._schema(tenant, project)}.{self.EXT_EVENTS_TABLE}"
        res = await conn.fetchval(f"""
            INSERT INTO {tbl} (
              source, kind, external_id,
              tenant, project, user_id,
              amount_cents, tokens, currency,
              status, stripe_event_id, metadata
            ) VALUES (
              'internal', $2, $1,
              $3, $4, $5,
              $6, NULL, 'usd',
              'pending', NULL, $7
            )
            ON CONFLICT (source, kind, external_id) DO NOTHING
            RETURNING TRUE
        """, external_id, kind, tenant, project, user_id, int(amount_cents), metadata)

        was_created = bool(res)

        row = await conn.fetchrow(f"""
            SELECT status
            FROM {tbl}
            WHERE source='internal' AND kind=$2 AND external_id=$1
            FOR UPDATE
        """, external_id, kind)

        if not row:
            raise RuntimeError("Failed to lock/create external_economics_events row (internal)")

        return str(row["status"]), was_created

    async def _mark_internal_event_applied(self, conn: asyncpg.Connection, *, tenant: str, project: str, kind: str, external_id: str) -> None:
        tbl = f"{self._schema(tenant, project)}.{self.EXT_EVENTS_TABLE}"
        await conn.execute(f"""
            UPDATE {tbl}
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source='internal' AND kind=$2 AND external_id=$1
        """, external_id, kind)

    async def _mark_internal_event_failed(self, conn: asyncpg.Connection, *, tenant: str, project: str, kind: str, external_id: str, error: str) -> None:
        tbl = f"{self._schema(tenant, project)}.{self.EXT_EVENTS_TABLE}"
        await conn.execute(f"""
            UPDATE {tbl}
            SET status='failed', error=$2, updated_at=NOW()
            WHERE source='internal' AND kind=$3 AND external_id=$1
        """, external_id, (error or "")[:2000], kind)

    async def rollover_unused_balance_once(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            subscription_budget: SubscriptionBudgetLimiter,
            project_budget: ProjectBudgetLimiter,
            period_key: str,
            period_end: Optional[datetime] = None,
            actor: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Move unused subscription balance to project budget.

        Primary sweep is idempotent by period_key and closes the period.
        """
        note = f"subscription rollover user_id={user_id}"
        if period_end:
            note += f" period_end={period_end.isoformat()}"
        if actor:
            note += f" by={actor}"

        async def _run(c: asyncpg.Connection) -> Dict[str, Any]:
            status, was_created = await self._lock_or_create_internal_event(
                conn=c,
                kind="subscription_rollover",
                external_id=period_key,
                tenant=tenant,
                project=project,
                user_id=user_id,
                amount_cents=0,
                metadata={"period_end": period_end.isoformat() if period_end else None, "by": actor or "unknown"},
            )
            if status == "applied":
                return {"status": "ok", "action": "duplicate", "moved_usd": 0.0}
            
            if not was_created and status == "pending":
                return {"status": "ok", "action": "duplicate", "moved_usd": 0.0}

            try:
                await subscription_budget.reap_expired_reservations(conn=c, project_budget=project_budget)
                request_id = f"internal:rollover:{period_key}"
                moved_cents = await subscription_budget.close_period_and_rollover(
                    conn=c,
                    project_budget=project_budget,
                    note=note,
                    request_id=request_id,
                )

                await self._mark_internal_event_applied(c, tenant=tenant, project=project, kind="subscription_rollover", external_id=period_key)
                return {
                    "status": "ok",
                    "action": "applied",
                    "moved_usd": float(moved_cents) / 100.0,
                }
            except Exception as e:
                await self._mark_internal_event_failed(c, tenant=tenant, project=project, kind="subscription_rollover", external_id=period_key, error=str(e))
                raise

        if conn:
            return await _run(conn)

        async with self.pg_pool.acquire() as c:
            async with c.transaction():
                return await _run(c)

    async def sweep_due_subscription_rollovers(
            self,
            *,
            tenant: str,
            project: str,
            now: Optional[datetime] = None,
            limit: int = 500,
            project_budget_factory: Optional[callable] = None,
            actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sweep unused balances for EXTERNAL subscriptions whose next_charge_at <= now.

        Internal plans carry no plan budget and are never scheduled (next_charge_at is
        NULL), so they are excluded both by the schedule predicate and explicitly by
        provider.
        """
        now = now or _now()
        tbl = f"{self._schema(tenant, project)}.{self.TABLE}"

        async with self.pg_pool.acquire() as c:
            rows = await c.fetch(f"""
                SELECT *
                FROM {tbl}
                WHERE tenant=$1 AND project=$2
                  AND provider != 'internal'
                  AND status IN ('active', 'past_due', 'unpaid')
                  AND monthly_price_cents > 0
                  AND next_charge_at IS NOT NULL
                  AND next_charge_at <= $3
                ORDER BY next_charge_at ASC
                LIMIT {int(limit)}
            """, tenant, project, now)

        if not rows:
            return {"status": "ok", "count": 0, "moved_usd": 0.0}

        moved_total = 0.0
        processed = 0

        for r in rows:
            uid = str(r["user_id"])
            period_end = r["next_charge_at"]
            period_desc = build_subscription_period_descriptor(
                tenant=tenant,
                project=project,
                user_id=uid,
                provider=str(r["provider"] or "internal"),
                stripe_subscription_id=r["stripe_subscription_id"],
                period_end=period_end,
                period_start=r["last_charged_at"],
            )
            period_key = period_desc["period_key"]
            period_start = period_desc["period_start"]
            period_end = period_desc["period_end"]

            subscription_budget = SubscriptionBudgetLimiter(
                pg_pool=self.pg_pool,
                tenant=tenant,
                project=project,
                user_id=uid,
                period_key=period_key,
                period_start=period_start,
                period_end=period_end,
            )
            project_budget = project_budget_factory(tenant, project) if project_budget_factory else ProjectBudgetLimiter(
                redis=None, pg_pool=self.pg_pool, tenant=tenant, project=project
            )

            res = await self.rollover_unused_balance_once(
                tenant=tenant,
                project=project,
                user_id=uid,
                subscription_budget=subscription_budget,
                project_budget=project_budget,
                period_key=period_key,
                period_end=period_end,
                actor=actor,
            )
            moved_total += float(res.get("moved_usd") or 0.0)
            processed += 1

        return {"status": "ok", "count": processed, "moved_usd": moved_total}

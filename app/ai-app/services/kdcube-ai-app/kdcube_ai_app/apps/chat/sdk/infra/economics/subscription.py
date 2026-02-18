# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/subscription.py
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import asyncpg
from calendar import monthrange

from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tier_from_user_type(user_type: str) -> str:
    return {
        "registered": "free",
        "paid": "paid",
        "privileged": "premium",
        "admin": "admin",
    }.get(user_type, "unknown")


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
    tier: str
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


@dataclass(frozen=True)
class InternalRenewOnceResult:
    status: str            # "ok" | "error"
    action: str            # "applied" | "duplicate"
    message: str
    external_id: str
    user_id: str
    usd_amount: float
    charged_at: datetime


class SubscriptionManager:
    CP = "kdcube_control_plane"
    TABLE = "user_subscriptions"
    EXT_EVENTS_TABLE = "external_economics_events"

    def __init__(self, pg_pool: asyncpg.Pool):
        self.pg_pool = pg_pool

    def _from_row(self, row: asyncpg.Record) -> Subscription:
        return Subscription(**dict(row))

    async def get_subscription(
        self,
        *,
        tenant: str,
        project: str,
        user_id: str,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        sql = f"SELECT * FROM {self.CP}.{self.TABLE} WHERE tenant=$1 AND project=$2 AND user_id=$3"
        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id)
        return self._from_row(row) if row else None

    async def get_subscription_by_stripe_id(
        self,
        *,
        stripe_subscription_id: str,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        if not stripe_subscription_id:
            return None
        sql = f"SELECT * FROM {self.CP}.{self.TABLE} WHERE provider='stripe' AND stripe_subscription_id=$1"
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
            user_type: str,
            monthly_price_cents_free: int = 0,
            monthly_price_cents_paid: int = 2000,
            monthly_price_cents_admin: int = 0,
            now: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        """
        Ensure a subscription row exists for a user (internal bootstrap).

        Rules:
          - If an existing row is provider='stripe', DO NOT MODIFY it at all.
          - Otherwise, ensure/refresh an internal row.
          - Internal paid/premium ARE scheduled via next_charge_at.
        """
        now = now or _now()
        tier = _tier_from_user_type(user_type)

        if tier == "unknown" or user_type == "anonymous":
            raise ValueError(f"unsupported user_type={user_type}")

        if tier == "free":
            price = int(monthly_price_cents_free)
        elif tier == "admin":
            price = int(monthly_price_cents_admin)
        else:
            # paid OR premium default to paid price
            price = int(monthly_price_cents_paid)

        tbl = f"{self.CP}.{self.TABLE}"

        sql = f"""
        INSERT INTO {tbl} (
          tenant, project, user_id,
          tier, status, monthly_price_cents,
          started_at, next_charge_at, last_charged_at,
          provider, stripe_customer_id, stripe_subscription_id
        ) VALUES (
          $1,$2,$3,
          $4,'active',$5,
          $6,
          CASE
            WHEN $4 IN ('paid','premium') AND $5 > 0 THEN $6
            ELSE NULL
          END,
          NULL,
          'internal', NULL, NULL
        )
        ON CONFLICT (tenant, project, user_id)
        DO UPDATE SET
          tier = EXCLUDED.tier,
          status = 'active',
          monthly_price_cents = EXCLUDED.monthly_price_cents,

          -- keep started_at stable
          started_at = {tbl}.started_at,

          -- schedule only if chargeable
          next_charge_at = CASE
            WHEN EXCLUDED.tier IN ('paid','premium') AND EXCLUDED.monthly_price_cents > 0 THEN
              COALESCE(
                {tbl}.next_charge_at,
                ({tbl}.last_charged_at + interval '1 month'),
                $6
              )
            ELSE NULL
          END,

          provider = 'internal',

          stripe_customer_id = NULL,
          stripe_subscription_id = NULL,

          updated_at = NOW()
        WHERE {tbl}.provider IS DISTINCT FROM 'stripe'
        RETURNING *
        """

        async def _run(c: asyncpg.Connection) -> asyncpg.Record:
            row = await c.fetchrow(sql, tenant, project, user_id, tier, price, now)
            if row:
                return row
            # If conflict row exists and is stripe => UPDATE skipped => RETURNING empty => fetch existing row
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


    async def upsert_from_stripe_invoice_paid(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            tier: str,
            monthly_price_cents: int,
            stripe_customer_id: Optional[str],
            stripe_subscription_id: Optional[str],
            next_charge_at: Optional[datetime],
            charged_at: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        charged_at = charged_at or _now()

        sql = f"""
        INSERT INTO {self.CP}.{self.TABLE} (
          tenant, project, user_id,
          tier, status, monthly_price_cents,
          started_at, next_charge_at, last_charged_at,
          provider, stripe_customer_id, stripe_subscription_id
        ) VALUES ($1,$2,$3, $4,'active',$5, NOW(), $6,$7, 'stripe', $8,$9)
        ON CONFLICT (tenant, project, user_id)
        DO UPDATE SET
          tier=EXCLUDED.tier,
          status='active',
          monthly_price_cents=EXCLUDED.monthly_price_cents,
          -- keep started_at stable (don’t reset on renewals)
          started_at={self.CP}.{self.TABLE}.started_at,
          last_charged_at=EXCLUDED.last_charged_at,
          next_charge_at=COALESCE(EXCLUDED.next_charge_at, {self.CP}.{self.TABLE}.next_charge_at),
          provider='stripe',
          stripe_customer_id=COALESCE(EXCLUDED.stripe_customer_id, {self.CP}.{self.TABLE}.stripe_customer_id),
          stripe_subscription_id=COALESCE(EXCLUDED.stripe_subscription_id, {self.CP}.{self.TABLE}.stripe_subscription_id),
          updated_at=NOW()
        RETURNING *
        """

        if conn:
            row = await conn.fetchrow(
                sql, tenant, project, user_id, tier, int(monthly_price_cents),
                next_charge_at, charged_at, stripe_customer_id, stripe_subscription_id
            )
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(
                    sql, tenant, project, user_id, tier, int(monthly_price_cents),
                    next_charge_at, charged_at, stripe_customer_id, stripe_subscription_id
                )
        return self._from_row(row)

    async def update_status_by_stripe_id(
            self,
            *,
            stripe_subscription_id: str,
            status: str,
            next_charge_at: Optional[datetime] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Subscription]:
        if not stripe_subscription_id:
            return None
        sql = f"""
            UPDATE {self.CP}.{self.TABLE}
            SET status=$2,
                next_charge_at=$3,
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
        FROM {self.CP}.{self.TABLE}
        WHERE tenant=$1 AND project=$2
          AND provider='internal'
          AND tier IN ('paid','premium')
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
        tbl = f"{self.CP}.{self.TABLE}"
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

    async def mark_internal_charge(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            charged_at: Optional[datetime],
            next_charge_at: Optional[datetime],
            conn: Optional[asyncpg.Connection] = None,
    ) -> Subscription:
        charged_at = charged_at or _now()
        sql = f"""
        UPDATE {self.CP}.{self.TABLE}
        SET last_charged_at=$4, next_charge_at=$5, updated_at=NOW()
        WHERE tenant=$1 AND project=$2 AND user_id=$3
        RETURNING *
        """
        if conn:
            row = await conn.fetchrow(sql, tenant, project, user_id, charged_at, next_charge_at)
        else:
            async with self.pg_pool.acquire() as c:
                row = await c.fetchrow(sql, tenant, project, user_id, charged_at, next_charge_at)
        if not row:
            raise ValueError(f"subscription not found: {tenant}/{project}/{user_id}")
        return self._from_row(row)


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
    ) -> str:
        tbl = f"{self.CP}.{self.EXT_EVENTS_TABLE}"
        await conn.execute(f"""
            INSERT INTO {tbl} (
              source, kind, external_id,
              tenant, project, user_id,
              amount_cents, tokens, currency,
              status, stripe_event_id, metadata
            ) VALUES (
              'internal', $2, $1,
              $3, $4, $5,
              $6, NULL, 'usd',
              'pending', NULL, $7::jsonb
            )
            ON CONFLICT (source, kind, external_id) DO NOTHING
        """, external_id, kind, tenant, project, user_id, int(amount_cents), json.dumps(metadata))

        row = await conn.fetchrow(f"""
            SELECT status
            FROM {tbl}
            WHERE source='internal' AND kind=$2 AND external_id=$1
            FOR UPDATE
        """, external_id, kind)

        if not row:
            raise RuntimeError("Failed to lock/create external_economics_events row (internal)")

        return str(row["status"])

    async def _mark_internal_event_applied(self, conn: asyncpg.Connection, *, kind: str, external_id: str) -> None:
        tbl = f"{self.CP}.{self.EXT_EVENTS_TABLE}"
        await conn.execute(f"""
            UPDATE {tbl}
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source='internal' AND kind=$2 AND external_id=$1
        """, external_id, kind)

    async def _mark_internal_event_failed(self, conn: asyncpg.Connection, *, kind: str, external_id: str, error: str) -> None:
        tbl = f"{self.CP}.{self.EXT_EVENTS_TABLE}"
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
            status = await self._lock_or_create_internal_event(
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

            try:
                await subscription_budget.reap_expired_reservations(conn=c, project_budget=project_budget)
                request_id = f"internal:rollover:{period_key}"
                moved_cents = await subscription_budget.close_period_and_rollover(
                    conn=c,
                    project_budget=project_budget,
                    note=note,
                    request_id=request_id,
                )

                await self._mark_internal_event_applied(c, kind="subscription_rollover", external_id=period_key)
                return {
                    "status": "ok",
                    "action": "applied",
                    "moved_usd": float(moved_cents) / 100.0,
                }
            except Exception as e:
                await self._mark_internal_event_failed(c, kind="subscription_rollover", external_id=period_key, error=str(e))
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
        Sweep unused subscription balances for subscriptions whose next_charge_at <= now.
        """
        now = now or _now()
        tbl = f"{self.CP}.{self.TABLE}"

        async with self.pg_pool.acquire() as c:
            rows = await c.fetch(f"""
                SELECT *
                FROM {tbl}
                WHERE tenant=$1 AND project=$2
                  AND status='active'
                  AND tier IN ('paid','premium')
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

    async def renew_internal_subscription_once(
            self,
            *,
            tenant: str,
            project: str,
            user_id: str,
            subscription_budget: Optional[SubscriptionBudgetLimiter] = None,
            charged_at: Optional[datetime] = None,
            idempotency_key: Optional[str] = None,
            actor: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
    ) -> InternalRenewOnceResult:
        """
        One-shot manual renewal for INTERNAL subscriptions:
          - idempotent via external_economics_events (source='internal', kind='subscription_topup')
          - tops up user subscription balance
          - marks last_charged_at and advances next_charge_at by one month
        """
        charged_at = charged_at or _now()
        next_due = _add_one_month(charged_at)
        ym = charged_at.strftime("%Y-%m")

        async def _run(c: asyncpg.Connection) -> InternalRenewOnceResult:
            sub = await self.get_subscription(tenant=tenant, project=project, user_id=user_id, conn=c)
            if not sub:
                raise ValueError("subscription not found")
            if sub.provider != "internal":
                raise ValueError("not an internal subscription")
            if sub.status != "active":
                raise ValueError("subscription not active")
            if sub.tier not in ("paid", "premium"):
                raise ValueError(f"tier={sub.tier} is not chargeable")
            if int(sub.monthly_price_cents or 0) <= 0:
                raise ValueError("monthly_price_cents is 0; nothing to charge")

            prev_desc = build_subscription_period_descriptor(
                tenant=tenant,
                project=project,
                user_id=user_id,
                provider=sub.provider,
                stripe_subscription_id=sub.stripe_subscription_id,
                period_end=sub.next_charge_at,
                period_start=sub.last_charged_at,
                fallback_charged_at=charged_at,
            )
            period_key_prev = prev_desc["period_key"]
            period_end_prev = prev_desc["period_end"]

            new_desc = build_subscription_period_descriptor(
                tenant=tenant,
                project=project,
                user_id=user_id,
                provider=sub.provider,
                stripe_subscription_id=sub.stripe_subscription_id,
                period_end=next_due,
                period_start=charged_at,
            )
            period_key_new = new_desc["period_key"]
            period_start_new = new_desc["period_start"]
            period_end_new = new_desc["period_end"]

            external_id = idempotency_key or f"internal:renew:{tenant}:{project}:{user_id}:{period_key_new}"

            status = await self._lock_or_create_internal_event(
                conn=c,
                kind="subscription_topup",
                external_id=external_id,
                tenant=tenant,
                project=project,
                user_id=user_id,
                amount_cents=int(sub.monthly_price_cents),
                metadata={
                    "tier": sub.tier,
                    "by": actor or "unknown",
                    "period_end": period_end_new.isoformat(),
                },
            )
            if status == "applied":
                return InternalRenewOnceResult(
                    status="ok",
                    action="duplicate",
                    message="Already applied (idempotent)",
                    external_id=external_id,
                    user_id=user_id,
                    usd_amount=float(int(sub.monthly_price_cents) / 100.0),
                    charged_at=charged_at,
                )

            usd_amount = float(int(sub.monthly_price_cents) / 100.0)

            try:
                period_key = period_key_prev
                project_budget = ProjectBudgetLimiter(redis=None, pg_pool=self.pg_pool, tenant=tenant, project=project)

                await self.rollover_unused_balance_once(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    subscription_budget=SubscriptionBudgetLimiter(
                        pg_pool=self.pg_pool,
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        period_key=period_key_prev,
                        period_start=prev_desc["period_start"],
                        period_end=period_end_prev,
                    ),
                    project_budget=project_budget,
                    period_key=period_key,
                    period_end=period_end_prev,
                    actor=actor,
                    conn=c,
                )

                budget = subscription_budget
                if not budget or budget.period_key != period_key_new:
                    budget = SubscriptionBudgetLimiter(
                        pg_pool=self.pg_pool,
                        tenant=tenant,
                        project=project,
                        user_id=user_id,
                        period_key=period_key_new,
                        period_start=period_start_new,
                        period_end=period_end_new,
                    )

                await budget.topup_subscription_budget(
                    usd_amount=usd_amount,
                    notes=f"internal subscription renewal user_id={user_id} tier={sub.tier}",
                    request_id=f"internal:renew:{external_id}",
                    conn=c,
                )

                # no scheduling => next_charge_at stays NULL
                await self.mark_internal_charge(
                    tenant=tenant,
                    project=project,
                    user_id=user_id,
                    charged_at=charged_at,
                    next_charge_at=next_due,
                    conn=c,
                )

                await self._mark_internal_event_applied(c, kind="subscription_topup", external_id=external_id)
            except Exception as e:
                # NOTE: if transaction rolls back, this "failed" mark also rolls back (same pattern as Stripe handler).
                # That’s acceptable for now: next attempt will re-run since status remains pending/not-applied.
                await self._mark_internal_event_failed(c, kind="subscription_topup", external_id=external_id, error=str(e))
                raise

            return InternalRenewOnceResult(
                status="ok",
                action="applied",
                message=f"Renewed internal subscription and topped up subscription balance: +${usd_amount:.2f}",
                external_id=external_id,
                user_id=user_id,
                usd_amount=usd_amount,
                charged_at=charged_at,
            )

        if conn:
            return await _run(conn)

        async with self.pg_pool.acquire() as c:
            async with c.transaction():
                return await _run(c)

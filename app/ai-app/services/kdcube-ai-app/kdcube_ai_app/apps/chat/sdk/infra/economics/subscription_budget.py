# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/infra/economics/subscription_budget.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import UUID, uuid4

import asyncpg

from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import BudgetInsufficientFunds, ProjectBudgetLimiter

logger = logging.getLogger(__name__)


def _usd_to_cents(usd: float) -> int:
    return int(round(float(usd) * 100))


def _cents_to_usd(cents: int) -> float:
    return float(cents) / 100.0


@dataclass(frozen=True)
class SubscriptionBudgetSnapshot:
    period_key: str
    period_start: datetime
    period_end: datetime
    status: str

    balance_usd: float
    reserved_usd: float
    available_usd: float
    overdraft_limit_usd: Optional[float]
    overdraft_used_usd: float

    topup_usd: float
    rolled_over_usd: float
    spent_usd: float


@dataclass(frozen=True)
class SubscriptionReservationResult:
    reservation_id: UUID
    reserved_usd: float
    expires_at: datetime
    snapshot: SubscriptionBudgetSnapshot


class SubscriptionBudgetLimiter:
    """
    Per-user subscription budget limiter scoped to a billing period.

    Tracks actual money available for a user's subscription in PostgreSQL,
    with reservation support for in-flight requests.
    """

    CONTROL_PLANE_SCHEMA = "kdcube_control_plane"
    BUDGET_TABLE = "user_subscription_period_budget"
    RESERVATIONS_TABLE = "user_subscription_period_reservations"
    LEDGER_TABLE = "user_subscription_period_ledger"

    def __init__(
        self,
        *,
        pg_pool: asyncpg.Pool,
        tenant: str,
        project: str,
        user_id: str,
        period_key: str,
        period_start: datetime,
        period_end: datetime,
    ):
        self.pg_pool = pg_pool
        self.tenant = tenant
        self.project = project
        self.user_id = user_id
        self.period_key = period_key
        self.period_start = period_start
        self.period_end = period_end

    # ---------------- DB helpers ----------------
    async def _ensure_budget_row(self, conn: asyncpg.Connection) -> None:
        await conn.execute(f"""
            INSERT INTO {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE} (
                tenant, project, user_id, period_key,
                period_start, period_end
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant, project, user_id, period_key) DO NOTHING
        """, self.tenant, self.project, self.user_id, self.period_key, self.period_start, self.period_end)

    async def _get_overdraft_limit_cents(self, conn: asyncpg.Connection) -> Optional[int]:
        # Subscription overdraft is disabled.
        return 0

    async def _insert_ledger(
        self,
        conn: asyncpg.Connection,
        *,
        amount_cents: int,
        kind: str,
        note: Optional[str] = None,
        reservation_id: Optional[UUID] = None,
        bundle_id: Optional[str] = None,
        provider: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        await conn.execute(f"""
            INSERT INTO {self.CONTROL_PLANE_SCHEMA}.{self.LEDGER_TABLE} (
                tenant, project, user_id, period_key,
                amount_cents, kind, note,
                reservation_id, bundle_id, provider, request_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """,
        self.tenant, self.project, self.user_id, self.period_key,
        int(amount_cents), str(kind), note,
        reservation_id, bundle_id, provider, request_id)

    def _snapshot_from_row(self, row: asyncpg.Record, overdraft_limit_cents: Optional[int], reserved_override: Optional[int] = None) -> SubscriptionBudgetSnapshot:
        bal = int(row["balance_cents"] or 0)
        res = int(reserved_override if reserved_override is not None else (row["reserved_cents"] or 0))
        available = bal - res

        od_lim_c = overdraft_limit_cents
        overdraft_used = max(0, -available)

        topup_cents = int(row["topup_cents"] or 0)
        rolled_over_cents = int(row["rolled_over_cents"] or 0)
        spent_cents = topup_cents - rolled_over_cents - bal

        return SubscriptionBudgetSnapshot(
            period_key=str(row["period_key"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
            status=str(row["status"]),
            balance_usd=_cents_to_usd(bal),
            reserved_usd=_cents_to_usd(res),
            available_usd=_cents_to_usd(available),
            overdraft_limit_usd=None if od_lim_c is None else _cents_to_usd(od_lim_c),
            overdraft_used_usd=_cents_to_usd(overdraft_used),
            topup_usd=_cents_to_usd(topup_cents),
            rolled_over_usd=_cents_to_usd(rolled_over_cents),
            spent_usd=_cents_to_usd(spent_cents),
        )

    async def _active_reserved_cents(self, conn: asyncpg.Connection, now: datetime) -> int:
        row = await conn.fetchrow(f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS reserved
            FROM {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
              AND status='active' AND expires_at > $5
        """, self.tenant, self.project, self.user_id, self.period_key, now)
        return int(row["reserved"] or 0)

    def _check_overdraft(self, *, available_after_cents: int, overdraft_limit_cents: Optional[int]) -> None:
        if overdraft_limit_cents is None:
            return
        if available_after_cents < -int(overdraft_limit_cents):
            raise BudgetInsufficientFunds(
                f"available_after_cents={available_after_cents} would exceed overdraft_limit_cents={overdraft_limit_cents}"
            )

    # ---------------- Public API ----------------

    async def set_overdraft_limit(self, *, overdraft_limit_usd: Optional[float], notes: Optional[str] = None) -> SubscriptionBudgetSnapshot:
        raise ValueError("Subscription overdraft is disabled.")

    async def get_subscription_budget_balance(self) -> Dict[str, Any]:
        async with self.pg_pool.acquire() as conn:
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
            """, self.tenant, self.project, self.user_id, self.period_key)

            od_lim_c = await self._get_overdraft_limit_cents(conn)

        if not row:
            snap = SubscriptionBudgetSnapshot(
                period_key=self.period_key,
                period_start=self.period_start,
                period_end=self.period_end,
                status="open",
                balance_usd=0.0, reserved_usd=0.0, available_usd=0.0,
                overdraft_limit_usd=None if od_lim_c is None else _cents_to_usd(od_lim_c),
                overdraft_used_usd=0.0,
                topup_usd=0.0, rolled_over_usd=0.0, spent_usd=0.0,
            )
        else:
            async with self.pg_pool.acquire() as conn:
                now = datetime.utcnow().replace(tzinfo=timezone.utc)
                active_reserved = await self._active_reserved_cents(conn, now)
            snap = self._snapshot_from_row(row, od_lim_c, reserved_override=active_reserved)

        return {
            "period_key": snap.period_key,
            "period_start": snap.period_start,
            "period_end": snap.period_end,
            "status": snap.status,
            "balance_usd": snap.balance_usd,
            "reserved_usd": snap.reserved_usd,
            "available_usd": snap.available_usd,
            "overdraft_limit_usd": snap.overdraft_limit_usd,
            "overdraft_used_usd": snap.overdraft_used_usd,
            "topup_usd": snap.topup_usd,
            "rolled_over_usd": snap.rolled_over_usd,
            "spent_usd": snap.spent_usd,
            # legacy keys kept for backward compatibility
            "lifetime_added_usd": snap.topup_usd,
            "lifetime_spent_usd": snap.spent_usd,
        }

    async def topup_subscription_budget(
        self,
        *,
        usd_amount: float,
        notes: Optional[str] = None,
        request_id: Optional[str] = None,
        conn: Optional[asyncpg.Connection] = None,
        provider: Optional[str] = None,
        allow_multiple_topups: bool = False,
    ) -> Dict[str, float]:
        cents = _usd_to_cents(usd_amount)
        if cents <= 0:
            return {"new_balance_usd": 0.0, "topup_usd": 0.0}

        async def _apply(c: asyncpg.Connection) -> SubscriptionBudgetSnapshot:
            await self._ensure_budget_row(c)

            row = await c.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                FOR UPDATE
            """, self.tenant, self.project, self.user_id, self.period_key)

            if not row:
                raise RuntimeError("failed to lock subscription period budget row")

            if row["status"] != "open":
                raise ValueError("subscription period is closed; cannot top up")

            if not allow_multiple_topups and int(row["topup_cents"] or 0) > 0:
                raise ValueError("subscription period already topped up")

            row = await c.fetchrow(f"""
                UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                SET balance_cents = balance_cents + $5,
                    topup_cents = topup_cents + $5,
                    notes = COALESCE($6, notes),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                RETURNING *
            """, self.tenant, self.project, self.user_id, self.period_key, cents, notes)

            await self._insert_ledger(
                c,
                amount_cents=+cents,
                kind="topup",
                note=notes,
                request_id=request_id,
                provider=provider,
            )

            od_lim_c = await self._get_overdraft_limit_cents(c)
            return self._snapshot_from_row(row, od_lim_c)

        if conn:
            snap = await _apply(conn)
        else:
            async with self.pg_pool.acquire() as c:
                async with c.transaction():
                    snap = await _apply(c)

        return {"new_balance_usd": snap.balance_usd, "topup_usd": snap.topup_usd}

    async def reserve(
        self,
        *,
        bundle_id: Optional[str],
        amount_usd: float,
        provider: Optional[str] = None,
        request_id: Optional[str] = None,
        reservation_id: Optional[UUID] = None,
        ttl_sec: int = 300,
        now: Optional[datetime] = None,
        notes: Optional[str] = None,
    ) -> SubscriptionReservationResult:
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        amount_cents = _usd_to_cents(amount_usd)
        if amount_cents <= 0:
            raise ValueError("amount_usd must be > 0")

        rid = reservation_id or uuid4()
        expires_at = now + timedelta(seconds=int(ttl_sec))

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_budget_row(conn)
                od_lim_c = await self._get_overdraft_limit_cents(conn)

                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    FOR UPDATE
                """, self.tenant, self.project, self.user_id, self.period_key)

                if not b:
                    raise RuntimeError("failed to lock subscription period budget row")

                if b["status"] != "open":
                    raise ValueError("subscription period is closed; cannot reserve")

                bal = int(b["balance_cents"] or 0)
                res = await self._active_reserved_cents(conn, now)

                new_reserved = res + amount_cents
                available_after = bal - new_reserved
                self._check_overdraft(available_after_cents=available_after, overdraft_limit_cents=od_lim_c)

                inserted = await conn.execute(f"""
                    INSERT INTO {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE} (
                        reservation_id, tenant, project, user_id, period_key,
                        bundle_id, provider, request_id,
                        amount_cents, status, expires_at, notes
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'active',$10,$11)
                    ON CONFLICT (reservation_id) DO NOTHING
                """, rid, self.tenant, self.project, self.user_id, self.period_key, bundle_id, provider, request_id, amount_cents, expires_at, notes)

                if not inserted.endswith("1"):
                    raise ValueError(f"reservation_id already exists: {rid}")

                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    SET reserved_cents = $5,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    RETURNING *
                """, self.tenant, self.project, self.user_id, self.period_key, new_reserved)

                snap = self._snapshot_from_row(row, od_lim_c)
                return SubscriptionReservationResult(
                    reservation_id=rid,
                    reserved_usd=_cents_to_usd(amount_cents),
                    expires_at=expires_at,
                    snapshot=snap,
                )

    async def release_reservation(
        self,
        *,
        reservation_id: UUID,
        note: Optional[str] = None,
        now: Optional[datetime] = None,
        project_budget: Optional[ProjectBudgetLimiter] = None,
    ) -> SubscriptionBudgetSnapshot:
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                r = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3 AND user_id=$4 AND period_key=$5
                    FOR UPDATE
                """, reservation_id, self.tenant, self.project, self.user_id, self.period_key)

                if not r:
                    raise ValueError(f"reservation not found: {reservation_id}")

                od_lim_c = await self._get_overdraft_limit_cents(conn)

                if r["status"] != "active":
                    b = await conn.fetchrow(f"""
                        SELECT *
                        FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    """, self.tenant, self.project, self.user_id, self.period_key)
                    if not b:
                        return SubscriptionBudgetSnapshot(
                        period_key=self.period_key,
                        period_start=self.period_start,
                        period_end=self.period_end,
                        status="open",
                        balance_usd=0.0, reserved_usd=0.0, available_usd=0.0,
                        overdraft_limit_usd=None if od_lim_c is None else _cents_to_usd(od_lim_c),
                        overdraft_used_usd=0.0,
                        topup_usd=0.0, rolled_over_usd=0.0, spent_usd=0.0,
                        )
                    active_reserved = await self._active_reserved_cents(conn, now)
                    return self._snapshot_from_row(b, od_lim_c, reserved_override=active_reserved)

                amount_cents = int(r["amount_cents"])
                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                    SET status='released', released_at=$6
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3 AND user_id=$4 AND period_key=$5
                """, reservation_id, self.tenant, self.project, self.user_id, self.period_key, now)

                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    FOR UPDATE
                """, self.tenant, self.project, self.user_id, self.period_key)

                active_reserved = await self._active_reserved_cents(conn, now)
                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    SET reserved_cents = $5,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    RETURNING *
                """, self.tenant, self.project, self.user_id, self.period_key, active_reserved)

                if note:
                    await self._insert_ledger(
                        conn,
                        amount_cents=0,
                        kind="reserve_release",
                        note=note,
                        reservation_id=reservation_id,
                    )

                if b and b["status"] == "closed" and amount_cents > 0:
                    if project_budget is None:
                        logger.warning("Subscription period closed but project_budget not provided; late release not rolled over")
                    else:
                        await conn.execute(f"""
                            UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                            SET balance_cents = balance_cents - $5,
                                rolled_over_cents = rolled_over_cents + $5,
                                updated_at = NOW()
                            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                        """, self.tenant, self.project, self.user_id, self.period_key, amount_cents)
                        await project_budget.topup_app_budget(
                            usd_amount=_cents_to_usd(amount_cents),
                            notes="subscription late release rollover",
                            request_id=f"internal:rollover:late_release:{self.period_key}",
                            conn=conn,
                            user_id=self.user_id,
                            provider="subscription_rollover",
                        )
                        await self._insert_ledger(
                            conn,
                            amount_cents=-int(amount_cents),
                            kind="rollover_out",
                            note="late release rollover",
                            reservation_id=reservation_id,
                        )

                row = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                """, self.tenant, self.project, self.user_id, self.period_key)

                return self._snapshot_from_row(row, od_lim_c, reserved_override=active_reserved)

    async def commit_reserved_spend(
        self,
        *,
        reservation_id: UUID,
        spent_usd: float,
        now: Optional[datetime] = None,
        project_budget: Optional[ProjectBudgetLimiter] = None,
    ) -> SubscriptionBudgetSnapshot:
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        spent_cents = _usd_to_cents(spent_usd)
        if spent_cents < 0:
            raise ValueError("spent_usd must be >= 0")

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                r = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3 AND user_id=$4 AND period_key=$5
                    FOR UPDATE
                """, reservation_id, self.tenant, self.project, self.user_id, self.period_key)

                if not r:
                    raise ValueError(f"reservation not found: {reservation_id}")

                od_lim_c = await self._get_overdraft_limit_cents(conn)

                if r["status"] != "active":
                    b = await conn.fetchrow(f"""
                        SELECT *
                        FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    """, self.tenant, self.project, self.user_id, self.period_key)
                    if not b:
                        return SubscriptionBudgetSnapshot(
                        period_key=self.period_key,
                        period_start=self.period_start,
                        period_end=self.period_end,
                        status="open",
                        balance_usd=0.0, reserved_usd=0.0, available_usd=0.0,
                        overdraft_limit_usd=None if od_lim_c is None else _cents_to_usd(od_lim_c),
                        overdraft_used_usd=0.0,
                        topup_usd=0.0, rolled_over_usd=0.0, spent_usd=0.0,
                        )
                    active_reserved = await self._active_reserved_cents(conn, now)
                    return self._snapshot_from_row(b, od_lim_c, reserved_override=active_reserved)

                reserved_cents = int(r["amount_cents"])

                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    FOR UPDATE
                """, self.tenant, self.project, self.user_id, self.period_key)

                bal = int(b["balance_cents"] or 0)
                new_bal = bal - spent_cents
                overdraft_excess_cents: int | None = None

                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                    SET status='committed',
                        committed_at=$6,
                        actual_spent_cents=$7
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3 AND user_id=$4 AND period_key=$5
                """, reservation_id, self.tenant, self.project, self.user_id, self.period_key, now, spent_cents)

                active_reserved = await self._active_reserved_cents(conn, now)
                new_res_total = int(active_reserved)
                available_after = new_bal - new_res_total
                if od_lim_c is not None and available_after < -int(od_lim_c):
                    overdraft_excess_cents = (-int(available_after)) - int(od_lim_c)

                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    SET
                        reserved_cents = $5,
                        balance_cents = $6,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    RETURNING *
                """, self.tenant, self.project, self.user_id, self.period_key, new_res_total, new_bal)

                await self._insert_ledger(
                    conn,
                    amount_cents=-spent_cents,
                    kind="spend",
                    note=r["notes"],
                    reservation_id=reservation_id,
                    bundle_id=r["bundle_id"],
                    provider=r["provider"],
                    request_id=r["request_id"],
                )
                if overdraft_excess_cents and overdraft_excess_cents > 0:
                    await self._insert_ledger(
                        conn,
                        amount_cents=0,
                        kind="overdraft_exceeded",
                        note=f"overdraft_excess_cents={int(overdraft_excess_cents)}; commit_reserved_spend",
                        reservation_id=reservation_id,
                        bundle_id=r["bundle_id"],
                        provider=r["provider"],
                        request_id=r["request_id"],
                    )
                    logger.error(
                        "Subscription overdraft exceeded on commit_reserved_spend: tenant=%s project=%s user=%s reservation=%s excess_cents=%s",
                        self.tenant, self.project, self.user_id, str(reservation_id), int(overdraft_excess_cents)
                    )

                release_delta = max(0, reserved_cents - spent_cents)
                if row and row["status"] == "closed" and release_delta > 0:
                    if project_budget is None:
                        logger.warning("Subscription period closed but project_budget not provided; late release not rolled over")
                    else:
                        await conn.execute(f"""
                            UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                            SET balance_cents = balance_cents - $5,
                                rolled_over_cents = rolled_over_cents + $5,
                                updated_at = NOW()
                            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                        """, self.tenant, self.project, self.user_id, self.period_key, int(release_delta))
                        await project_budget.topup_app_budget(
                            usd_amount=_cents_to_usd(int(release_delta)),
                            notes="subscription late commit rollover",
                            request_id=f"internal:rollover:late_commit:{self.period_key}",
                            conn=conn,
                            user_id=self.user_id,
                            provider="subscription_rollover",
                        )
                        await self._insert_ledger(
                            conn,
                            amount_cents=-int(release_delta),
                            kind="rollover_out",
                            note="late commit rollover",
                            reservation_id=reservation_id,
                        )

                row = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                """, self.tenant, self.project, self.user_id, self.period_key)

        return self._snapshot_from_row(row, od_lim_c, reserved_override=new_res_total)

    async def reap_expired_reservations(
        self,
        *,
        limit: int = 500,
        now: Optional[datetime] = None,
        conn: Optional[asyncpg.Connection] = None,
        project_budget: Optional[ProjectBudgetLimiter] = None,
    ) -> int:
        """
        Expire active reservations whose expires_at has passed and release their holds.

        Returns number of reservations expired.
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)

        async def _run(c: asyncpg.Connection) -> int:
            rows = await c.fetch(f"""
                SELECT reservation_id, amount_cents
                FROM {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                  AND status='active' AND expires_at <= $5
                ORDER BY expires_at ASC
                LIMIT {int(limit)}
                FOR UPDATE
            """, self.tenant, self.project, self.user_id, self.period_key, now)

            if not rows:
                return 0

            total_release = sum(int(r["amount_cents"]) for r in rows)
            ids = [r["reservation_id"] for r in rows]

            await c.execute(f"""
                UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.RESERVATIONS_TABLE}
                SET status='expired', released_at=$5
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                  AND reservation_id = ANY($6::uuid[])
            """, self.tenant, self.project, self.user_id, self.period_key, now, ids)

            await self._ensure_budget_row(c)

            row = await c.fetchrow(f"""
                UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                SET reserved_cents = GREATEST(0, reserved_cents - $5),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                RETURNING *
            """, self.tenant, self.project, self.user_id, self.period_key, int(total_release))

            active_reserved = await self._active_reserved_cents(c, now)
            row = await c.fetchrow(f"""
                UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                SET reserved_cents = $5,
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                RETURNING *
            """, self.tenant, self.project, self.user_id, self.period_key, int(active_reserved))

            if row and row["status"] == "closed" and total_release > 0:
                if project_budget is None:
                    logger.warning("Subscription period closed but project_budget not provided; expired reservations not rolled over")
                else:
                    await c.execute(f"""
                        UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                        SET balance_cents = balance_cents - $5,
                            rolled_over_cents = rolled_over_cents + $5,
                            updated_at = NOW()
                        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                    """, self.tenant, self.project, self.user_id, self.period_key, int(total_release))
                    await project_budget.topup_app_budget(
                        usd_amount=_cents_to_usd(int(total_release)),
                        notes="subscription expired reservation rollover",
                        request_id=f"internal:rollover:expired:{self.period_key}",
                        conn=c,
                        user_id=self.user_id,
                        provider="subscription_rollover",
                    )
                    await self._insert_ledger(
                        c,
                        amount_cents=-int(total_release),
                        kind="rollover_out",
                        note="expired reservation rollover",
                        reservation_id=None,
                    )

            return len(rows)

        if conn:
            return await _run(conn)

        async with self.pg_pool.acquire() as c:
            async with c.transaction():
                return await _run(c)

    async def force_subscription_spend(
        self,
        *,
        spent_usd: float,
        bundle_id: Optional[str],
        provider: Optional[str],
        request_id: Optional[str],
        note: Optional[str],
    ) -> None:
        """
        Force-deduct from subscription budget WITHOUT overdraft checks.
        Used for post-fact settlement (user underfunded).
        """
        if spent_usd <= 0:
            return

        spent_cents = int(round(float(spent_usd) * 100))
        if spent_cents <= 0:
            return

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_budget_row(conn)

                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
                    SET
                        balance_cents = balance_cents - $5,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
                """, self.tenant, self.project, self.user_id, self.period_key, int(spent_cents))

                await self._insert_ledger(
                    conn,
                    amount_cents=-int(spent_cents),
                    kind="spend",
                    note=note,
                    reservation_id=None,
                    bundle_id=bundle_id,
                    provider=provider,
                    request_id=request_id,
                )

    async def close_period_and_rollover(
        self,
        *,
        conn: asyncpg.Connection,
        project_budget: ProjectBudgetLimiter,
        note: Optional[str],
        request_id: Optional[str],
        bundle_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> int:
        """
        Atomically closes the billing period and moves AVAILABLE (balance - reserved)
        funds to the project budget. Returns cents moved.
        """
        await self._ensure_budget_row(conn)

        row = await conn.fetchrow(f"""
            SELECT *
            FROM {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
            FOR UPDATE
        """, self.tenant, self.project, self.user_id, self.period_key)

        if not row:
            return 0

        if row["status"] == "closed":
            return 0

        bal = int(row["balance_cents"] or 0)
        res = int(row["reserved_cents"] or 0)
        available = max(bal - res, 0)

        await conn.execute(f"""
            UPDATE {self.CONTROL_PLANE_SCHEMA}.{self.BUDGET_TABLE}
            SET status='closed',
                closed_at=NOW(),
                balance_cents = balance_cents - $5,
                rolled_over_cents = rolled_over_cents + $5,
                updated_at = NOW()
            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND period_key=$4
        """, self.tenant, self.project, self.user_id, self.period_key, int(available))

        if available > 0:
            await project_budget.topup_app_budget(
                usd_amount=_cents_to_usd(int(available)),
                notes=note,
                request_id=request_id,
                conn=conn,
                user_id=self.user_id,
                provider="subscription_rollover",
            )

            await self._insert_ledger(
                conn,
                amount_cents=-int(available),
                kind="rollover_out",
                note=note,
                reservation_id=None,
                bundle_id=bundle_id,
                provider=provider,
                request_id=request_id,
            )

        return int(available)

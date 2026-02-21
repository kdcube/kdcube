# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# sdk/infra/economics/project_budget.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import uuid4, UUID

from redis.asyncio import Redis
import asyncpg

from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

def _ymd(dt: datetime) -> str:  return dt.strftime("%Y%m%d")
def _ym(dt: datetime) -> str:   return dt.strftime("%Y%m")
def _ymdh(dt: datetime) -> str: return dt.strftime("%Y%m%d%H")

def _eod(dt: datetime) -> int:
    end = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)
    return int(end.timestamp())

def _eom(dt: datetime) -> int:
    if dt.month == 12:
        nxt = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp())

def _eoh(dt: datetime) -> int:
    end = datetime(dt.year, dt.month, dt.day, dt.hour, tzinfo=timezone.utc) + timedelta(hours=1)
    return int(end.timestamp())

def _strs(*items) -> list[str]:
    return [str(x) for x in items]

def _usd_to_cents(usd: float) -> int:
    # consistent rounding
    return int(round(float(usd) * 100))

def _cents_to_usd(cents: int) -> float:
    return float(cents) / 100.0


# Lua script for atomic spend commit in Redis (unchanged)
_LUA_COMMIT_SPEND = r"""
local h_spend = KEYS[1]
local d_spend = KEYS[2]
local m_spend = KEYS[3]
local last_u  = KEYS[4]
local last_a  = KEYS[5]

local inc_usd  = tonumber(ARGV[1])
local exp_hour = tonumber(ARGV[2])
local exp_day  = tonumber(ARGV[3])
local exp_mon  = tonumber(ARGV[4])
local now_ts   = tonumber(ARGV[5])

if inc_usd > 0 then
  local inc_cents = math.floor(inc_usd * 100 + 0.5)
  redis.call('INCRBY', h_spend, inc_cents); redis.call('EXPIREAT', h_spend, exp_hour)
  redis.call('INCRBY', d_spend, inc_cents); redis.call('EXPIREAT', d_spend, exp_day)
  redis.call('INCRBY', m_spend, inc_cents); redis.call('EXPIREAT', m_spend, exp_mon)
  redis.call('SET', last_u, tostring(inc_cents))
  redis.call('SET', last_a, tostring(now_ts))
end
return 1
"""


class BudgetInsufficientFunds(Exception):
    """Raised when reserve/spend would exceed overdraft limit."""


@dataclass(frozen=True)
class BudgetSnapshot:
    balance_usd: float
    reserved_usd: float
    available_usd: float
    overdraft_limit_usd: Optional[float]
    overdraft_used_usd: float

    lifetime_added_usd: float
    lifetime_spent_usd: float


@dataclass(frozen=True)
class ReservationResult:
    reservation_id: UUID
    reserved_usd: float
    expires_at: datetime
    snapshot: BudgetSnapshot


class ProjectBudgetLimiter:
    """
    Project funds (project budget) limiter.

    Two-level system:
    1. Redis: Per-bundle spending tracking (hour/day/month)
    2. PostgreSQL: Global app budget balance (deducted on commit)

     1. Application Budget BALANCE (money tracking)
│      - get_app_budget_balance()
│      - topup_app_budget()
│      - commit() → deducts from balance
│      - Table: tenant_project_budget
    Tracks actual MONEY available
    Tables:
          tenant_project_budget (balance + reserved + overdraft_limit)
          tenant_project_budget_reservations (per-request holds)
          tenant_project_budget_ledger (signed ledger)

    Example: "We have $5,432.19 remaining"
    topup_app_budget(
        usd_amount=1000.0  # ADD MONEY
    )
    commit(
        spent_usd=12.50  # DEDUCT MONEY
    )

    │ 2. Spending per-bundle/provider Analytics (hour/day/month) (Redis)
    │  - get_spending_by_bundle()
    """

    CONTROL_PLANE_SCHEMA = "kdcube_control_plane"

    def __init__(self, redis: Redis, pg_pool: asyncpg.Pool, *, tenant: str, project: str, namespace: str = REDIS.ECONOMICS.PROJ_BUDGET):
        self.r = redis
        self.pg_pool = pg_pool
        self.tenant = tenant
        self.project = project
        self.ns = namespace

    # ---------------- Redis key helper ----------------
    def _k(self, bundle: str, provider: str, *parts: str) -> str:
        """Redis key: {tenant}:{project}:kdcube:budget:{bundle}:{provider}:{parts}"""
        base = f"{self.ns}:{bundle}:{provider}"
        if parts:
            base = f"{base}:{':'.join(parts)}"
        return f"{self.tenant}:{self.project}:{base}"

    # ---------------- DB helpers ----------------
    async def _ensure_budget_row(self, conn: asyncpg.Connection) -> None:
        await conn.execute(f"""
            INSERT INTO {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget (tenant, project)
            VALUES ($1, $2)
            ON CONFLICT (tenant, project) DO NOTHING
        """, self.tenant, self.project)

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
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        await conn.execute(f"""
            INSERT INTO {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_ledger (
                tenant, project, amount_cents, kind, note,
                reservation_id, bundle_id, provider, user_id, request_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        """,
        self.tenant, self.project, int(amount_cents), str(kind), note,
        reservation_id, bundle_id, provider, user_id, request_id)

    def _snapshot_from_row(self, row: asyncpg.Record, reserved_override: Optional[int] = None) -> BudgetSnapshot:
        bal = int(row["balance_cents"] or 0)
        res = int(reserved_override if reserved_override is not None else (row["reserved_cents"] or 0))
        available = bal - res

        od_lim = row["overdraft_limit_cents"]
        od_lim_c = None if od_lim is None else int(od_lim)

        overdraft_used = max(0, -available)

        return BudgetSnapshot(
            balance_usd=_cents_to_usd(bal),
            reserved_usd=_cents_to_usd(res),
            available_usd=_cents_to_usd(available),
            overdraft_limit_usd=None if od_lim_c is None else _cents_to_usd(od_lim_c),
            overdraft_used_usd=_cents_to_usd(overdraft_used),
            lifetime_added_usd=_cents_to_usd(int(row["lifetime_added_cents"] or 0)),
            lifetime_spent_usd=_cents_to_usd(int(row["lifetime_spent_cents"] or 0)),
        )

    async def _active_reserved_cents(self, conn: asyncpg.Connection, now: datetime) -> int:
        row = await conn.fetchrow(f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS reserved
            FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
            WHERE tenant=$1 AND project=$2
              AND status='active' AND expires_at > $3
        """, self.tenant, self.project, now)
        return int(row["reserved"] or 0)

    def _check_overdraft(self, *, available_after_cents: int, overdraft_limit_cents: Optional[int]) -> None:
        # overdraft_limit_cents == None => unlimited negative
        if overdraft_limit_cents is None:
            return
        if available_after_cents < -int(overdraft_limit_cents):
            raise BudgetInsufficientFunds(
                f"available_after_cents={available_after_cents} would exceed overdraft_limit_cents={overdraft_limit_cents}"
            )

    # ---------------- Public API ----------------

    async def set_overdraft_limit(self, *, overdraft_limit_usd: Optional[float], notes: Optional[str] = None) -> BudgetSnapshot:
        """
        Set overdraft limit.
          - overdraft_limit_usd = None => unlimited negative
          - 0 => no overdraft allowed
        """
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_budget_row(conn)
                lim_c = None if overdraft_limit_usd is None else _usd_to_cents(overdraft_limit_usd)

                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET overdraft_limit_cents = $3,
                        notes = COALESCE($4, notes),
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                    RETURNING *
                """, self.tenant, self.project, lim_c, notes)

                return self._snapshot_from_row(row)

    async def get_app_budget_balance(self) -> Dict[str, Any]:
        """
        Returns full snapshot including reserved/available/overdraft_used.
        """
        async with self.pg_pool.acquire() as conn:
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            row = await conn.fetchrow(f"""
                SELECT *
                FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                WHERE tenant=$1 AND project=$2
            """, self.tenant, self.project)

        if not row:
            snap = BudgetSnapshot(
                balance_usd=0.0, reserved_usd=0.0, available_usd=0.0,
                overdraft_limit_usd=0.0, overdraft_used_usd=0.0,
                lifetime_added_usd=0.0, lifetime_spent_usd=0.0,
            )
        else:
            async with self.pg_pool.acquire() as conn:
                now = datetime.utcnow().replace(tzinfo=timezone.utc)
                active_reserved = await self._active_reserved_cents(conn, now)
            snap = self._snapshot_from_row(row, reserved_override=active_reserved)

        return {
            "balance_usd": snap.balance_usd,
            "reserved_usd": snap.reserved_usd,
            "available_usd": snap.available_usd,
            "overdraft_limit_usd": snap.overdraft_limit_usd,
            "overdraft_used_usd": snap.overdraft_used_usd,
            "lifetime_added_usd": snap.lifetime_added_usd,
            "lifetime_spent_usd": snap.lifetime_spent_usd,
        }

    async def topup_app_budget(
            self,
            *,
            usd_amount: float,
            notes: Optional[str] = None,
            request_id: Optional[str] = None,
            conn: Optional[asyncpg.Connection] = None,
            user_id: Optional[str] = None,
            provider: Optional[str] = None,   # keep for future targeted topups
    ) -> Dict[str, float]:
        cents = _usd_to_cents(usd_amount)
        if cents <= 0:
            return {"new_balance_usd": 0.0, "lifetime_added_usd": 0.0}

        async def _apply(c: asyncpg.Connection) -> BudgetSnapshot:
            await self._ensure_budget_row(c)

            row = await c.fetchrow(f"""
                UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                SET balance_cents = balance_cents + $3,
                    lifetime_added_cents = lifetime_added_cents + $3,
                    notes = COALESCE($4, notes),
                    updated_at = NOW()
                WHERE tenant=$1 AND project=$2
                RETURNING *
            """, self.tenant, self.project, cents, notes)

            await self._insert_ledger(
                c,
                amount_cents=+cents,
                kind="topup",
                note=notes,
                request_id=request_id,
                user_id=user_id,
                provider=provider,   # should be None for topups today
            )
            return self._snapshot_from_row(row)

        if conn:
            snap = await _apply(conn)
        else:
            async with self.pg_pool.acquire() as c:
                async with c.transaction():
                    snap = await _apply(c)

        return {"new_balance_usd": snap.balance_usd, "lifetime_added_usd": snap.lifetime_added_usd}

    async def reserve(
        self,
        *,
        bundle_id: Optional[str],
        amount_usd: float,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        request_id: Optional[str] = None,
        reservation_id: Optional[UUID] = None,
        ttl_sec: int = 300,
        now: Optional[datetime] = None,
        notes: Optional[str] = None,
    ) -> ReservationResult:
        """
        Reserve project funds for an in-flight request (estimate).

        Atomic rules:
          available_after = balance_cents - (reserved_cents + amount_cents)
          allow if overdraft_limit is NULL OR available_after >= -overdraft_limit
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        amount_cents = _usd_to_cents(amount_usd)
        if amount_cents <= 0:
            raise ValueError("amount_usd must be > 0")

        rid = reservation_id or uuid4()
        expires_at = now + timedelta(seconds=int(ttl_sec))

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_budget_row(conn)

                # Lock budget row
                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    WHERE tenant=$1 AND project=$2
                    FOR UPDATE
                """, self.tenant, self.project)

                bal = int(b["balance_cents"] or 0)
                res = await self._active_reserved_cents(conn, now)
                od_lim = b["overdraft_limit_cents"]
                od_lim_c = None if od_lim is None else int(od_lim)

                new_reserved = res + amount_cents
                available_after = bal - new_reserved

                self._check_overdraft(available_after_cents=available_after, overdraft_limit_cents=od_lim_c)

                # Insert reservation (idempotent: if exists, fail fast)
                inserted = await conn.execute(f"""
                    INSERT INTO {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations (
                        reservation_id, tenant, project,
                        bundle_id, provider, user_id, request_id,
                        amount_cents, status, expires_at, notes
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'active',$9, $10)
                    ON CONFLICT (reservation_id) DO NOTHING
                """, rid, self.tenant, self.project, bundle_id, provider, user_id, request_id, amount_cents, expires_at, notes)

                if not inserted.endswith("1"):
                    raise ValueError(f"reservation_id already exists: {rid}")

                # Apply to budget
                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET reserved_cents = $3,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                    RETURNING *
                """, self.tenant, self.project, new_reserved)

                snap = self._snapshot_from_row(row)
                return ReservationResult(
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
    ) -> BudgetSnapshot:
        """
        Release a reservation without spending.
        Safe to call multiple times; only affects active reservations.
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # Lock reservation
                r = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3
                    FOR UPDATE
                """, reservation_id, self.tenant, self.project)

                if not r:
                    raise ValueError(f"reservation not found: {reservation_id}")

                if r["status"] != "active":
                    # no-op
                    b = await conn.fetchrow(f"""
                        SELECT * FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                        WHERE tenant=$1 AND project=$2
                    """, self.tenant, self.project)
                    if not b:
                        return BudgetSnapshot(0,0,0,0,0,0,0)
                    active_reserved = await self._active_reserved_cents(conn, now)
                    return self._snapshot_from_row(b, reserved_override=active_reserved)

                amount_cents = int(r["amount_cents"])
                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    SET status='released', released_at=$4
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3
                """, reservation_id, self.tenant, self.project, now)

                active_reserved = await self._active_reserved_cents(conn, now)
                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET reserved_cents = $3,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                    RETURNING *
                """, self.tenant, self.project, active_reserved)

                # Ledger is optional for release; keep it quiet by default
                if note:
                    await self._insert_ledger(
                        conn,
                        amount_cents=0,
                        kind="reserve_release",
                        note=note,
                        reservation_id=reservation_id,
                        user_id=r["user_id"]
                    )

                return self._snapshot_from_row(row, reserved_override=active_reserved)

    async def commit_reserved_spend(
        self,
        *,
        reservation_id: UUID,
        spent_usd: float,
        now: Optional[datetime] = None,
    ) -> BudgetSnapshot:
        """
        Finalize spend for an existing reservation:
          - releases reservation hold
          - deducts actual spend from balance
          - updates lifetime_spent
          - writes signed ledger entry (negative amount)

        If actual spend > reserved estimate, the delta also hits balance and is overdraft-checked.
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        spent_cents = _usd_to_cents(spent_usd)
        if spent_cents < 0:
            raise ValueError("spent_usd must be >= 0")

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # Lock reservation
                r = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3
                    FOR UPDATE
                """, reservation_id, self.tenant, self.project)

                if not r:
                    raise ValueError(f"reservation not found: {reservation_id}")

                if r["status"] != "active":
                    # already finalized; return snapshot
                    b = await conn.fetchrow(f"""
                        SELECT * FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                        WHERE tenant=$1 AND project=$2
                    """, self.tenant, self.project)
                    if not b:
                        return BudgetSnapshot(0,0,0,0,0,0,0)
                    active_reserved = await self._active_reserved_cents(conn, now)
                    return self._snapshot_from_row(b, reserved_override=active_reserved)

                # Lock budget row
                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    WHERE tenant=$1 AND project=$2
                    FOR UPDATE
                """, self.tenant, self.project)

                bal = int(b["balance_cents"] or 0)
                od_lim = b["overdraft_limit_cents"]
                od_lim_c = None if od_lim is None else int(od_lim)

                # self._check_overdraft(available_after_cents=available_after, overdraft_limit_cents=od_lim_c)
                # Soft overdraft detection: committing a FACT should not be blocked.
                # Reserve() is the planning gate; commit must record reality and then alert.
                overdraft_excess_cents: int | None = None

                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    SET status='committed',
                        committed_at=$4,
                        actual_spent_cents=$5
                    WHERE reservation_id=$1 AND tenant=$2 AND project=$3
                """, reservation_id, self.tenant, self.project, now, spent_cents)

                active_reserved = await self._active_reserved_cents(conn, now)
                new_res_total = int(active_reserved)
                new_bal = bal - spent_cents
                available_after = new_bal - new_res_total
                if od_lim_c is not None and available_after < -int(od_lim_c):
                    overdraft_excess_cents = (-int(available_after)) - int(od_lim_c)

                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET
                        reserved_cents = $3,
                        balance_cents = $4,
                        lifetime_spent_cents = lifetime_spent_cents + $5,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                    RETURNING *
                """, self.tenant, self.project, new_res_total, new_bal, spent_cents)

                # Signed ledger: spend is negative
                await self._insert_ledger(
                    conn,
                    amount_cents=-spent_cents,
                    kind="spend",
                    note=r["notes"],
                    reservation_id=reservation_id,
                    bundle_id=r["bundle_id"],
                    provider=r["provider"],
                    user_id=r["user_id"],
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
                        user_id=r["user_id"],
                        request_id=r["request_id"],
                    )
                    logger.error(
                        "Budget overdraft exceeded on commit_reserved_spend: tenant=%s project=%s reservation=%s excess_cents=%s",
                        self.tenant, self.project, str(reservation_id), int(overdraft_excess_cents)
                    )

        return self._snapshot_from_row(row, reserved_override=new_res_total)

    async def reap_expired_reservations(self, *, limit: int = 500, now: Optional[datetime] = None) -> int:
        """
        Expire active reservations whose expires_at has passed and release their holds.

        Returns number of reservations expired.
        """
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # Grab a batch to expire and lock them
                rows = await conn.fetch(f"""
                    SELECT reservation_id, amount_cents
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    WHERE tenant=$1 AND project=$2 AND status='active' AND expires_at <= $3
                    ORDER BY expires_at ASC
                    LIMIT {int(limit)}
                    FOR UPDATE
                """, self.tenant, self.project, now)

                if not rows:
                    return 0

                total_release = sum(int(r["amount_cents"]) for r in rows)
                ids = [r["reservation_id"] for r in rows]

                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget_reservations
                    SET status='expired', released_at=$3
                    WHERE tenant=$1 AND project=$2 AND reservation_id = ANY($4::uuid[])
                """, self.tenant, self.project, now, ids)

                await self._ensure_budget_row(conn)

                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET reserved_cents = GREATEST(0, reserved_cents - $3),
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                """, self.tenant, self.project, int(total_release))

                active_reserved = await self._active_reserved_cents(conn, now)
                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET reserved_cents = $3,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                """, self.tenant, self.project, int(active_reserved))

                return len(rows)

    # ---------------- Existing spend analytics (Redis) + direct commit ----------------

    async def commit(self, *, bundle_id: str, provider: str, spent_usd: float, now: Optional[datetime] = None, request_id: Optional[str] = None, user_id: str = None) -> None:
        """
        Direct spend commit WITHOUT reservation (kept for backward compatibility).

        Recommended path is:
          reserve(...) -> commit_reserved_spend(...)
        """
        if spent_usd <= 0:
            return

        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        # 1) Redis analytics
        k_spend_h = self._k(bundle_id, provider, "spend:hour", ymdh)
        k_spend_d = self._k(bundle_id, provider, "spend:day", ymd)
        k_spend_m = self._k(bundle_id, provider, "spend:month", ym)
        k_last_u  = self._k(bundle_id, provider, "last_spend_usd")
        k_last_a  = self._k(bundle_id, provider, "last_spend_at")

        await self.r.eval(
            _LUA_COMMIT_SPEND, 5,
            *_strs(k_spend_h, k_spend_d, k_spend_m, k_last_u, k_last_a),
            *_strs(float(spent_usd), _eoh(now), _eod(now), _eom(now), int(now.timestamp())),
        )

        spent_cents = _usd_to_cents(spent_usd)

        # 2) PostgreSQL: overdraft check applies to available (balance - reserved)
        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_budget_row(conn)

                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    WHERE tenant=$1 AND project=$2
                    FOR UPDATE
                """, self.tenant, self.project)

                bal = int(b["balance_cents"] or 0)
                res = int(b["reserved_cents"] or 0)
                od_lim = b["overdraft_limit_cents"]
                od_lim_c = None if od_lim is None else int(od_lim)

                # available_after = (bal - spent) - res
                available_after = (bal - spent_cents) - res
                self._check_overdraft(available_after_cents=available_after, overdraft_limit_cents=od_lim_c)

                row = await conn.fetchrow(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET
                        balance_cents = balance_cents - $3,
                        lifetime_spent_cents = lifetime_spent_cents + $3,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                    RETURNING *
                """, self.tenant, self.project, spent_cents)

                await self._insert_ledger(
                    conn,
                    amount_cents=-spent_cents,
                    kind="spend",
                    note=None,
                    bundle_id=bundle_id,
                    provider=provider,
                    request_id=request_id,
                    user_id=user_id
                )

        logger.debug(f"Budget committed (direct): {self.tenant}/{self.project}/{bundle_id}/{provider}: ${spent_usd:.6f}")

    async def get_spending_by_bundle(self, *, bundle_ids: Optional[list[str]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
        # unchanged from your version (kept as-is)
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        if not bundle_ids:
            # Scan for all bundles
            pattern = f"{self.tenant}:{self.project}:{self.ns}:*:spend:month:{ym}"
            cursor = 0
            found_keys = []
            while True:
                cursor, keys = await self.r.scan(cursor, match=pattern, count=100)
                found_keys.extend(keys)
                if cursor == 0:
                    break

            # Extract unique bundle:provider pairs
            bundle_providers = set()
            prefix = f"{self.tenant}:{self.project}:{self.ns}:"

            for key in found_keys:
                key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                if not key_str.startswith(prefix):
                    continue
                rest = key_str[len(prefix):]               # "{bundle}:{provider}:spend:month:{ym}"
                rest_parts = rest.split(":")
                if len(rest_parts) >= 2:
                    bundle_id = rest_parts[0]
                    provider = rest_parts[1]
                    bundle_providers.add((bundle_id, provider))
                # parts = key.decode().split(":")
                # # Format: {tenant}:{project}:kdcube:budget:{bundle}:{provider}:spend:month:{ym}
                # if len(parts) >= 7:
                #     bundle_id = parts[4]
                #     provider = parts[5]
                #     bundle_providers.add((bundle_id, provider))

            bundle_ids = list(set(bp[0] for bp in bundle_providers))

        bundles: dict[str, dict[str, float]] = {}
        total_hour = 0.0
        total_day = 0.0
        total_month = 0.0

        for bundle_id in bundle_ids:
            # Scan providers for this bundle
            pattern = f"{self.tenant}:{self.project}:{self.ns}:{bundle_id}:*:spend:month:{ym}"
            cursor = 0
            providers = set()
            prefix = f"{self.tenant}:{self.project}:{self.ns}:{bundle_id}:"

            while True:
                cursor, keys = await self.r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                    if not key_str.startswith(prefix):
                        continue
                    rest = key_str[len(prefix):]               # "{provider}:spend:month:{ym}"
                    provider = rest.split(":", 1)[0]
                    if provider:
                        providers.add(provider)
                if cursor == 0:
                    break

            bundle_total_h = 0.0
            bundle_total_d = 0.0
            bundle_total_m = 0.0

            for provider in providers:
                k_h = self._k(bundle_id, provider, "spend:hour", ymdh)
                k_d = self._k(bundle_id, provider, "spend:day", ymd)
                k_m = self._k(bundle_id, provider, "spend:month", ym)

                vals = await self.r.mget(k_h, k_d, k_m)
                h = int(vals[0] or 0) / 100.0
                d = int(vals[1] or 0) / 100.0
                m = int(vals[2] or 0) / 100.0

                bundle_total_h += h
                bundle_total_d += d
                bundle_total_m += m

            bundles[bundle_id] = {
                "hour": bundle_total_h,
                "day": bundle_total_d,
                "month": bundle_total_m,
            }

            total_hour += bundle_total_h
            total_day += bundle_total_d
            total_month += bundle_total_m

        return {
            "bundles": bundles,
            "totals": {
                "hour": total_hour,
                "day": total_day,
                "month": total_month,
            }
        }

    async def get_current_spend(self, *, bundle_id: str, provider: str, now: Optional[datetime] = None) -> dict[str, float]:
        """Get spending for specific bundle+provider."""
        now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        k_spend_h = self._k(bundle_id, provider, "spend:hour", ymdh)
        k_spend_d = self._k(bundle_id, provider, "spend:day", ymd)
        k_spend_m = self._k(bundle_id, provider, "spend:month", ym)

        vals = await self.r.mget(k_spend_h, k_spend_d, k_spend_m)

        return {
            "hour": int(vals[0] or 0) / 100.0,
            "day": int(vals[1] or 0) / 100.0,
            "month": int(vals[2] or 0) / 100.0,
        }

    async def record_budget_analytics_only(self, *, bundle_id: str, provider: str, spent_usd: float, now: datetime):
        """
        Updates Redis hour/day/month spend counters WITHOUT touching PG balance.
        This matches ProjectBudgetLimiter.commit() Redis part but avoids double-deducting PG.
        """
        if spent_usd <= 0:
            return
        now = now.astimezone(timezone.utc)
        ymd, ym, ymdh = _ymd(now), _ym(now), _ymdh(now)

        k_spend_h = self._k(bundle_id, provider, "spend:hour", ymdh)
        k_spend_d = self._k(bundle_id, provider, "spend:day", ymd)
        k_spend_m = self._k(bundle_id, provider, "spend:month", ym)
        k_last_u  = self._k(bundle_id, provider, "last_spend_usd")
        k_last_a  = self._k(bundle_id, provider, "last_spend_at")

        await self.r.eval(
            _LUA_COMMIT_SPEND, 5,
            *_strs(k_spend_h, k_spend_d, k_spend_m, k_last_u, k_last_a),
            *_strs(float(spent_usd), _eoh(now), _eod(now), _eom(now), int(now.timestamp())),
        )

    async def force_project_spend(
            self,
            *,
            spent_usd: float,
            bundle_id: Optional[str],
            provider: Optional[str],
            request_id: Optional[str],
            user_id: Optional[str],
            note: Optional[str],
    ) -> None:
        """
        Force-deduct from PG budget WITHOUT any overdraft checks.
        This is explicitly used for post-fact reality settlement (fact risk),
        including "user underfunded" deltas.

        Uses ProjectBudgetLimiter internals (private helpers) intentionally.
        """
        if spent_usd <= 0:
            return

        spent_cents = int(round(float(spent_usd) * 100))
        if spent_cents <= 0:
            return

        async with self.pg_pool.acquire() as conn:
            async with conn.transaction():
                # Ensure row exists
                await self._ensure_budget_row(conn)

                # Lock budget row
                b = await conn.fetchrow(f"""
                    SELECT *
                    FROM {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    WHERE tenant=$1 AND project=$2
                    FOR UPDATE
                """, self.tenant, self.project)

                # Apply delta (no overdraft guard)
                await conn.execute(f"""
                    UPDATE {self.CONTROL_PLANE_SCHEMA}.tenant_project_budget
                    SET
                        balance_cents = balance_cents - $3,
                        lifetime_spent_cents = lifetime_spent_cents + $3,
                        updated_at = NOW()
                    WHERE tenant=$1 AND project=$2
                """, self.tenant, self.project, int(spent_cents))

                # Ledger
                await self._insert_ledger(
                    conn,
                    amount_cents=-int(spent_cents),
                    kind="spend",
                    note=note,
                    reservation_id=None,
                    bundle_id=bundle_id,
                    provider=provider,
                    user_id=user_id,
                    request_id=request_id,
                )

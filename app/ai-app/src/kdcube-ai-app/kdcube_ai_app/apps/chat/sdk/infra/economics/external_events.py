# SPDX-License-Identifier: MIT
# apps/chat/sdk/infra/economics/external_events.py
"""The one module that writes `external_economics_events`.

`external_economics_events` is the payment idempotency ledger: one row per
external effect, keyed by `(source, kind, external_id)`, with status
`pending|applied|failed|ignored`. `StripeEconomicsWebhookHandler`,
`StripeEconomicsAdminService` (stripe.py) and `SubscriptionManager`
(subscription.py) all claim and mark rows through this module, so `source` is a
parameter rather than a SQL literal and a new payment rail is a new
`LedgerKey.source` rather than another copy of the protocol.

This module is unrelated to `apps/chat/external_events.py`, the chat external
event subsystem.

Two invariants this module exists to hold:

* **Identity is `(source, kind, external_id)`** -- the table's real primary key.
  `tenant`/`project` choose the SCHEMA (`project_schema`), never the row, which is
  why no statement here filters on them.
* **`metadata` binds as `($n::text)::jsonb`.** The app's pool registers a jsonb
  codec (`apps/chat/ingress/resolvers.py`); other economics pools do not.
  A declared-`jsonb` parameter would be json.dumps-ed a second time on the former
  and the column would hold a JSONB *string*, which `metadata.get(...)` cannot
  read -- silently breaking refund reconciliation. Declaring the parameter `text`
  and casting in SQL is correct on both.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

import asyncpg

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema

TABLE = "external_economics_events"


@dataclass(frozen=True)
class LedgerKey:
    """Exactly the table's primary key, in its column order."""
    source: str
    kind: str
    external_id: str

    def __iter__(self):
        """Test/PK-order convenience: `tuple(key)` is the primary key tuple."""
        yield self.source
        yield self.kind
        yield self.external_id


@dataclass(frozen=True)
class Claim:
    key: LedgerKey
    status: str
    was_created: bool

    @property
    def already_applied(self) -> bool:
        """This event has been applied before; the caller must not apply it again."""
        return self.status == "applied"

    @property
    def concurrent_duplicate(self) -> bool:
        """Someone else opened this row and has not finished. `request_wallet_refund`
        treats this as a duplicate rather than a retry."""
        return (not self.was_created) and self.status == "pending"


class ExternalEconomicsLedger:
    def __init__(self, *, pg_pool: Optional[asyncpg.Pool] = None):
        self.pg_pool = pg_pool

    @staticmethod
    def _table(tenant: str, project: str) -> str:
        return f"{_project_schema(tenant, project)}.{TABLE}"

    async def claim(
        self,
        *,
        conn: asyncpg.Connection,
        key: LedgerKey,
        tenant: str,
        project: str,
        user_id: Optional[str] = None,
        amount_cents: Optional[int] = None,
        tokens: Optional[int] = None,
        currency: Optional[str] = None,
        provider_event_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Claim:
        """Insert-or-lock the row and return its status.

        `RETURNING TRUE` after `ON CONFLICT DO NOTHING` yields `was_created`;
        `FOR UPDATE` then serializes concurrent deliveries of the same event.

        `provider_event_id` writes the `stripe_event_id` column. The column is
        misnamed for a generic ledger; renaming it is a separate migration.
        """
        tbl = self._table(tenant, project)
        created = await conn.fetchval(
            f"""
            INSERT INTO {tbl} (
              source, kind, external_id,
              tenant, project, user_id,
              amount_cents, tokens, currency,
              status, stripe_event_id, metadata
            ) VALUES (
              $1, $2, $3,
              $4, $5, $6,
              $7, $8, $9,
              'pending', $10, ($11::text)::jsonb
            )
            ON CONFLICT (source, kind, external_id) DO NOTHING
            RETURNING TRUE
            """,
            key.source, key.kind, key.external_id,
            tenant, project, user_id,
            amount_cents, tokens, currency,
            provider_event_id, json.dumps(metadata if metadata is not None else {}),
        )

        row = await conn.fetchrow(
            f"""
            SELECT status FROM {tbl}
            WHERE source=$1 AND kind=$2 AND external_id=$3
            FOR UPDATE
            """,
            key.source, key.kind, key.external_id,
        )
        if not row:
            raise RuntimeError(
                f"Failed to lock/create {TABLE} row (source={key.source!r}, "
                f"kind={key.kind!r}, external_id={key.external_id!r})"
            )
        return Claim(key=key, status=str(row["status"]), was_created=bool(created))

    async def mark_applied(self, *, conn: asyncpg.Connection, key: LedgerKey,
                           tenant: str, project: str) -> None:
        await conn.execute(
            f"""
            UPDATE {self._table(tenant, project)}
            SET status='applied', applied_at=NOW(), error=NULL, updated_at=NOW()
            WHERE source=$1 AND kind=$2 AND external_id=$3
            """,
            key.source, key.kind, key.external_id,
        )

    async def mark_failed(self, *, conn: asyncpg.Connection, key: LedgerKey,
                          tenant: str, project: str, error: Optional[str]) -> None:
        """`applied_at` is deliberately left alone: a failed retry of an event
        keeps the timestamp of any earlier successful application."""
        await conn.execute(
            f"""
            UPDATE {self._table(tenant, project)}
            SET status='failed', error=$4, updated_at=NOW()
            WHERE source=$1 AND kind=$2 AND external_id=$3
            """,
            key.source, key.kind, key.external_id, (error or "")[:2000],
        )

    async def fetch_for_update(self, *, conn: asyncpg.Connection, key: LedgerKey,
                               tenant: str, project: str) -> Optional[asyncpg.Record]:
        """Lock a row this caller did not necessarily create.

        The Stripe webhook path resolves rows the admin service wrote: a
        cross-source read, not an oversight.
        """
        return await conn.fetchrow(
            f"""
            SELECT * FROM {self._table(tenant, project)}
            WHERE source=$1 AND kind=$2 AND external_id=$3
            FOR UPDATE
            """,
            key.source, key.kind, key.external_id,
        )

    async def merge_metadata(self, *, conn: asyncpg.Connection, key: LedgerKey,
                             tenant: str, project: str,
                             metadata: Optional[Dict[str, Any]]) -> None:
        """Merge-patch, never replace: the refund path appends the Stripe refund id
        to a row whose `payment_intent_id` the reconcile sweep still needs."""
        await conn.execute(
            f"""
            UPDATE {self._table(tenant, project)}
            SET metadata = COALESCE(metadata, '{{}}'::jsonb) || ($4::text)::jsonb,
                updated_at = NOW()
            WHERE source=$1 AND kind=$2 AND external_id=$3
            """,
            key.source, key.kind, key.external_id,
            json.dumps(metadata if metadata is not None else {}),
        )

    async def list_by_status(
        self,
        *,
        tenant: str,
        project: str,
        source: str,
        status: str = "pending",
        kinds: Optional[Iterable[str]] = None,
        limit: int = 200,
        conn: Optional[asyncpg.Connection] = None,
    ) -> List[asyncpg.Record]:
        """Oldest first, capped. `kinds=None` means every kind.

        Replaces the inline SELECT in `reconcile_pending_requests`, whose
        `CASE WHEN $1='all'` sentinel becomes `None`. `conn` is for callers
        already inside a transaction; without it the ledger's own pool is used,
        which is what the reconcile sweep does.
        """
        tbl = self._table(tenant, project)
        kind_list = list(kinds) if kinds is not None else None
        sql = f"""
            SELECT kind, external_id, tenant, project, user_id,
                   amount_cents, tokens, metadata
            FROM {tbl}
            WHERE source=$1 AND status=$2
              AND ($3::text[] IS NULL OR kind = ANY($3::text[]))
            ORDER BY created_at ASC
            LIMIT $4
        """
        if conn is not None:
            return list(await conn.fetch(sql, source, status, kind_list, int(limit)))
        async with self.pg_pool.acquire() as c:
            return list(await c.fetch(sql, source, status, kind_list, int(limit)))

    @asynccontextmanager
    async def apply_once(self, *, conn: asyncpg.Connection, key: LedgerKey,
                         tenant: str, project: str, **claim_kwargs) -> AsyncIterator[Claim]:
        """Claim, run the caller's effect, then mark -- all on the caller's
        connection, inside the caller's transaction.

        That co-location is the property worth encoding: money added and the
        ledger mark either both commit or neither does, and every rail gets that
        property by construction instead of re-implementing it.

        Two behaviours are deliberate and preserved from the previous inline
        implementations:

        * A caller that leaves the block because `claim.already_applied` is True
          gets NO second mark, so `applied_at` keeps its original value.
        * `mark_failed` runs on the caller's connection, so if the caller lets the
          exception propagate out of its transaction the mark is rolled back with
          everything else and the row ends up ABSENT, not 'failed'. That is what
          the previous inline helpers did. Recording the failure durably needs a
          separate connection or a savepoint and is a separate behaviour change.
        """
        claim = await self.claim(conn=conn, key=key, tenant=tenant, project=project,
                                 **claim_kwargs)
        try:
            yield claim
        except Exception as exc:  # noqa: BLE001 -- re-raised immediately
            if not claim.already_applied:
                await self.mark_failed(conn=conn, key=key, tenant=tenant,
                                       project=project, error=str(exc))
            raise
        if not claim.already_applied:
            await self.mark_applied(conn=conn, key=key, tenant=tenant, project=project)

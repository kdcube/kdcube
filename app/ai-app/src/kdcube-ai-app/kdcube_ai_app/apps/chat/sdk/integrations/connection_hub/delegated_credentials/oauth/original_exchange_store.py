# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable validated-code lookup using the host's bound PostgreSQL pool.

This ledger maps a consumed-code proof to the original Hub plan. It stores no
bearers, raw codes, verifiers or consumed payloads, creates no decision/session,
and holds one connection only within a short transaction. Host composition must
bind the original plan before any mint, then recover the original decision and
credential reservations. An unfinished mapping returns pending data, never a
replacement authorization code. Rows remain no-rebegin tombstones after expiry.
"""
from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg

from connection_hub.delegated_credentials.oauth.grants import ACCESS_TOKEN_TTL_SECONDS
from kdcube_ai_app.auth.bundle.session_schema import bundle_session_schema
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    CodeExchangeProof, OriginalExchangeRefused, ValidatedCodeExchange, canonical, plan_snapshot, text,
)

TABLE_EXCHANGES = "kdcube_oauth_original_code_exchanges"
UNPLANNED_RECOVERY_SECONDS = 600
_WAIT_SECONDS = 5


@dataclass(frozen=True)
class OriginalExchange:
    identity: str
    decision_request_id: str
    payload_digest: str
    original_input_digest: str
    pending_until: int
    delivery_deadline: int | None
    plan: dict[str, Any] | None
    created: bool = False
    access_expires_at: int | None = None
    access_ttl_seconds: int | None = None


class PostgresOriginalExchangeStore:
    def __init__(self, *, pg_pool: Any, tenant: str, project: str):
        text(tenant)
        text(project)
        if pg_pool is None:
            raise OriginalExchangeRefused("original_exchange_store_not_bound")
        self._pool, self.tenant, self.project = pg_pool, tenant, project
        self.schema = bundle_session_schema(tenant=tenant, project=project)

    @asynccontextmanager
    async def _connection(self):
        try:
            async with self._pool.acquire(timeout=_WAIT_SECONDS) as connection:
                yield connection
        except (TimeoutError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
            raise OriginalExchangeRefused("original_exchange_unavailable") from None

    async def ensure_schema(self) -> None:
        async with self._connection() as connection:
            await connection.execute(f"""
                CREATE SCHEMA IF NOT EXISTS {self.schema};
                CREATE TABLE IF NOT EXISTS {self.schema}.{TABLE_EXCHANGES} (
                    identity CHAR(64) PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    project TEXT NOT NULL,
                    code_sha256 CHAR(64) NOT NULL,
                    proof_digest CHAR(64) NOT NULL,
                    binding_digest CHAR(64) NOT NULL,
                    payload_digest CHAR(64) NOT NULL,
                    original_input_digest CHAR(64) NOT NULL,
                    decision_request_id CHAR(64) NOT NULL UNIQUE,
                    pending_until TIMESTAMPTZ NOT NULL,
                    delivery_deadline TIMESTAMPTZ,
                    access_expires_at TIMESTAMPTZ,
                    access_ttl_seconds INTEGER,
                    plan JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                    CHECK ((plan IS NULL) = (delivery_deadline IS NULL))
                );
                ALTER TABLE {self.schema}.{TABLE_EXCHANGES}
                    ADD COLUMN IF NOT EXISTS access_expires_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS access_ttl_seconds INTEGER;
            """, timeout=_WAIT_SECONDS)

    def _proof(self, proof: CodeExchangeProof) -> None:
        if type(proof) is not CodeExchangeProof:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        proof.validated()
        if proof.tenant != self.tenant or proof.project != self.project:
            raise OriginalExchangeRefused("original_exchange_namespace_mismatch")

    def _binding(self, binding: ValidatedCodeExchange) -> None:
        if type(binding) is not ValidatedCodeExchange:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        binding.validated()
        self._proof(binding.proof)

    async def _row(self, connection: Any, identity: str, *, lock: bool = False) -> Any:
        return await connection.fetchrow(f"""
            SELECT *, floor(extract(epoch FROM pending_until))::bigint AS pending_epoch,
                   floor(extract(epoch FROM delivery_deadline))::bigint AS delivery_epoch,
                   floor(extract(epoch FROM access_expires_at))::bigint AS access_epoch
            FROM {self.schema}.{TABLE_EXCHANGES} WHERE identity = $1
            {'FOR UPDATE' if lock else ''}
        """, identity, timeout=_WAIT_SECONDS)

    @staticmethod
    def _match(row: Any, proof: CodeExchangeProof, binding: ValidatedCodeExchange | None = None) -> None:
        if (row["tenant"] != proof.tenant or row["project"] != proof.project
                or not hmac.compare_digest(row["proof_digest"], proof.fingerprint)):
            raise OriginalExchangeRefused("original_exchange_proof_mismatch")
        if binding is not None and not hmac.compare_digest(row["binding_digest"], binding.fingerprint):
            raise OriginalExchangeRefused("original_exchange_identity_conflict")

    @staticmethod
    async def _live(connection: Any, row: Any) -> None:
        # Evaluated after any conflicting INSERT/row-lock wait. A transaction's
        # now() would freeze the clock before that wait and reopen expiry.
        live = await connection.fetchval(
            "SELECT clock_timestamp() < $1::timestamptz",
            row["delivery_deadline"] or row["pending_until"], timeout=_WAIT_SECONDS,
        )
        if not live:
            raise OriginalExchangeRefused("original_exchange_delivery_expired")
        expiry, ttl = row["access_expires_at"], row["access_ttl_seconds"]
        if ((expiry is None) != (ttl is None)
                or (ttl is not None and (type(ttl) is not int or not 1 <= ttl <= ACCESS_TOKEN_TTL_SECONDS))):
            raise OriginalExchangeRefused("original_exchange_access_expiry_invalid")
        if expiry is not None:
            access_live = await connection.fetchval(
                "SELECT clock_timestamp() < $1::timestamptz", expiry, timeout=_WAIT_SECONDS,
            )
            if not access_live:
                raise OriginalExchangeRefused("original_exchange_access_expired")

    @staticmethod
    def _decode(row: Any, *, created: bool = False) -> OriginalExchange:
        raw = row["plan"]
        return OriginalExchange(
            identity=row["identity"], decision_request_id=row["decision_request_id"],
            payload_digest=row["payload_digest"], original_input_digest=row["original_input_digest"],
            pending_until=row["pending_epoch"], delivery_deadline=row["delivery_epoch"],
            plan=json.loads(raw) if isinstance(raw, str) else raw, created=created,
            access_expires_at=row["access_epoch"], access_ttl_seconds=row["access_ttl_seconds"],
        )

    async def begin(self, binding: ValidatedCodeExchange) -> OriginalExchange:
        """Pin the first live validation before Hub begin or any credential mint."""
        self._binding(binding)
        async with self._connection() as connection:
            async with connection.transaction():
                created = await connection.fetchval(f"""
                    INSERT INTO {self.schema}.{TABLE_EXCHANGES} (
                        identity, tenant, project, code_sha256, proof_digest, binding_digest,
                        payload_digest, original_input_digest, decision_request_id, pending_until
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,
                              to_timestamp(floor(extract(epoch FROM clock_timestamp())) + $10))
                    ON CONFLICT (identity) DO NOTHING RETURNING true
                """, binding.proof.identity, self.tenant, self.project, binding.proof.code_sha256,
                    binding.proof.fingerprint, binding.fingerprint, binding.payload_digest,
                    binding.original_input_digest, binding.decision_request_id, UNPLANNED_RECOVERY_SECONDS,
                    timeout=_WAIT_SECONDS)
                row = await self._row(connection, binding.proof.identity, lock=True)
                self._match(row, binding.proof, binding)
                await self._live(connection, row)
                return self._decode(row, created=bool(created))

    async def read(self, proof: CodeExchangeProof) -> OriginalExchange | None:
        """Recover by the same client, redirect and PKCE proof; never consume or mint."""
        self._proof(proof)
        async with self._connection() as connection:
            row = await self._row(connection, proof.identity)
            if row is None:
                return None
            self._match(row, proof)
            await self._live(connection, row)
            return self._decode(row)

    async def capture_access_expiry(self, proof: CodeExchangeProof, *, ttl_seconds: int) -> OriginalExchange:
        """Capture the first access deadline before any preparation or mint.

        The trusted host calls this after pinning its authenticated original
        plan. A retry obtains the stored instant, never a renewed TTL. The
        original Card cap bounds it; the existing delivery window is unchanged.
        This stores no credential and is not an authorization or mint operation.
        Legacy NULL fields make no claim about a previously minted access token.
        """
        self._proof(proof)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= ACCESS_TOKEN_TTL_SECONDS:
            raise OriginalExchangeRefused("original_exchange_access_expiry_invalid")
        async with self._connection() as connection:
            async with connection.transaction():
                row = await self._row(connection, proof.identity, lock=True)
                if row is None:
                    raise OriginalExchangeRefused("original_exchange_not_validated")
                self._match(row, proof)
                await self._live(connection, row)
                original = self._decode(row)
                if original.plan is None:
                    raise OriginalExchangeRefused("original_exchange_not_planned")
                if original.access_expires_at is not None:
                    if original.access_ttl_seconds != ttl_seconds:
                        raise OriginalExchangeRefused("original_exchange_identity_conflict")
                    return original
                cap = original.plan.get("expires_at")
                if type(cap) is not int or cap < 1:
                    raise OriginalExchangeRefused("original_exchange_plan_invalid")
                now = await connection.fetchval(
                    "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint", timeout=_WAIT_SECONDS,
                )
                expiry = min(cap, now + ttl_seconds)
                if expiry <= now:
                    raise OriginalExchangeRefused("original_exchange_access_expired")
                await connection.execute(f"""
                    UPDATE {self.schema}.{TABLE_EXCHANGES}
                    SET access_expires_at=to_timestamp($2), access_ttl_seconds=$3 WHERE identity=$1
                """, proof.identity, expiry, ttl_seconds, timeout=_WAIT_SECONDS)
                return self._decode(await self._row(connection, proof.identity))

    async def pin_plan(self, binding: ValidatedCodeExchange, plan: object, *,
                       access_ttl_seconds: int | None = None) -> OriginalExchange:
        """Pin the host-validated original Hub plan once, before preparation/mint.

        The plan's own delivery deadline is kept exactly. pending_until bounds
        an unfinished mapping, not the Hub's separately captured original
        delivery window. A retry cannot substitute any part of a pinned plan.
        """
        self._binding(binding)
        if (access_ttl_seconds is not None
                and (type(access_ttl_seconds) is not int
                     or not 1 <= access_ttl_seconds <= ACCESS_TOKEN_TTL_SECONDS)):
            raise OriginalExchangeRefused("original_exchange_access_expiry_invalid")
        snapshot = plan_snapshot(binding, plan)
        async with self._connection() as connection:
            async with connection.transaction():
                row = await self._row(connection, binding.proof.identity, lock=True)
                if row is None:
                    raise OriginalExchangeRefused("original_exchange_not_validated")
                self._match(row, binding.proof, binding)
                await self._live(connection, row)
                if row["plan"] is not None:
                    if self._decode(row).plan != snapshot:
                        raise OriginalExchangeRefused("original_exchange_identity_conflict")
                    if access_ttl_seconds is not None:
                        # Unknown legacy expiry does not authorize a first or
                        # replacement mint. New HTTP composition pins plan and
                        # expiry in one transaction, including after restart.
                        if row["access_expires_at"] is None:
                            raise OriginalExchangeRefused("original_exchange_access_expiry_unknown")
                        if row["access_ttl_seconds"] != access_ttl_seconds:
                            raise OriginalExchangeRefused("original_exchange_identity_conflict")
                    return self._decode(row)
                if row["access_expires_at"] is not None or row["access_ttl_seconds"] is not None:
                    raise OriginalExchangeRefused("original_exchange_access_expiry_unknown")
                valid = await connection.fetchval(
                    "SELECT clock_timestamp() < to_timestamp($1) AND clock_timestamp() < to_timestamp($2)",
                    snapshot["delivery_deadline"], snapshot["reserved_until"], timeout=_WAIT_SECONDS,
                )
                if not valid:
                    raise OriginalExchangeRefused("original_exchange_delivery_expired")
                access_expiry = None
                if access_ttl_seconds is not None:
                    now = await connection.fetchval(
                        "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint", timeout=_WAIT_SECONDS,
                    )
                    access_expiry = min(snapshot["expires_at"], now + access_ttl_seconds)
                    if access_expiry <= now:
                        raise OriginalExchangeRefused("original_exchange_access_expired")
                await connection.execute(f"""
                    UPDATE {self.schema}.{TABLE_EXCHANGES}
                    SET plan=$2::jsonb, delivery_deadline=to_timestamp($3),
                        access_expires_at=to_timestamp($4::bigint), access_ttl_seconds=$5::integer
                    WHERE identity=$1
                """, binding.proof.identity, canonical(snapshot), snapshot["delivery_deadline"],
                    access_expiry, access_ttl_seconds, timeout=_WAIT_SECONDS)
                return self._decode(await self._row(connection, binding.proof.identity))

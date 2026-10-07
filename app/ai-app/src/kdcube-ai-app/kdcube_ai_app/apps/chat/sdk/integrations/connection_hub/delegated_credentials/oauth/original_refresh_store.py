# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Original refresh signing inputs and custody coordinates, never bearers.

This issuer metadata is separate from Bundle access sessions and Hub's sole
grant decision. Hub activates the refresh family; this table grants nothing.
Short row locks retain one original input and no-mint retirement tombstone.
"""
from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan
from connection_hub.delegated_credentials.oauth.issuance_store import MAX_ISSUANCE_TTL_SECONDS
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, TerminalIssuanceContext,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    OriginalExchangeRefused, canonical, digest, hex_digest, text,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import (
    PostgresOriginalExchangeStore,
)

TABLE_REFRESH = "kdcube_oauth_original_refresh_issuances"
REFRESH_SCHEMA = "kdcube.oauth.original_refresh.v1"
CLAIM_FIELDS = frozenset({"schema", "tenant", "project", "transaction_id", "slot", "sid", "sub", "iat", "exp"})


@dataclass(frozen=True)
class OriginalRefreshReservation:
    context: PlannedIssuanceContext
    inputs_digest: str
    claims: dict[str, Any]
    secret_ref: str
    bearer_sha256: str | None
    state: str

    def receipt(self) -> SessionIssuanceReceipt:
        if self.bearer_sha256 is None:
            raise OriginalExchangeRefused("original_refresh_unsealed")
        return SessionIssuanceReceipt(self.claims["sid"], self.secret_ref,
                                      self.bearer_sha256, "recovered").validated()


class PostgresOriginalRefreshStore:
    def __init__(self, *, pg_pool: Any, tenant: str, project: str):
        # Reuse the owning OAuth metadata connection boundary, not its rows.
        self._db = PostgresOriginalExchangeStore(pg_pool=pg_pool, tenant=tenant, project=project)
        self.tenant, self.project, self.schema = tenant, project, self._db.schema

    async def ensure_schema(self) -> None:
        async with self._db._connection() as connection:
            await connection.execute(f"""
                CREATE SCHEMA IF NOT EXISTS {self.schema};
                CREATE TABLE IF NOT EXISTS {self.schema}.{TABLE_REFRESH} (
                    identity CHAR(64) PRIMARY KEY, inputs_digest CHAR(64) NOT NULL,
                    binding JSONB NOT NULL, claims JSONB, secret_ref CHAR(32), bearer_sha256 CHAR(64),
                    original_digest CHAR(64),
                    state TEXT NOT NULL CHECK (state IN ('reserved','ready','applied','retired')),
                    applied_receipt CHAR(64), terminal_digest CHAR(64),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                );
                ALTER TABLE {self.schema}.{TABLE_REFRESH} ADD COLUMN IF NOT EXISTS original_digest CHAR(64);
            """, timeout=5)

    def _binding(self, plan: OAuthIssuancePlan, card_kind: str, ttl_seconds: int):
        if type(plan) is not OAuthIssuancePlan:
            raise OriginalExchangeRefused("original_refresh_plan_invalid")
        bound = PlannedIssuanceContext.from_oauth_plan(plan, slot="refresh", expires_at=plan.expires_at)
        if (bound.tenant, bound.project) != (self.tenant, self.project):
            raise OriginalExchangeRefused("original_refresh_namespace_mismatch")
        text(card_kind)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_ISSUANCE_TTL_SECONDS:
            raise OriginalExchangeRefused("original_refresh_ttl_invalid")
        binding = {"plan": plan.to_dict(), "card_kind": card_kind, "ttl_seconds": ttl_seconds}
        return bound, binding, digest(canonical(binding))

    async def _row(self, connection, identity, *, lock=False):
        return await connection.fetchrow(f"SELECT * FROM {self.schema}.{TABLE_REFRESH} WHERE identity=$1"
                                         + (" FOR UPDATE" if lock else ""), identity, timeout=5)

    @staticmethod
    def _match(row, binding, fingerprint):
        stored = json.loads(row["binding"]) if isinstance(row["binding"], str) else row["binding"]
        if not hmac.compare_digest(row["inputs_digest"], fingerprint) or canonical(stored) != canonical(binding):
            raise OriginalExchangeRefused("original_refresh_identity_conflict")
        if row["state"] == "retired":
            raise OriginalExchangeRefused("original_refresh_terminal")

    @staticmethod
    async def _live(connection, bound, *, preparing=False):
        now = await connection.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint", timeout=5)
        if bound.expires_at <= now or bound.delivery_deadline <= now:
            raise OriginalExchangeRefused("original_refresh_expired")
        if preparing and bound.reserved_until <= now:
            raise OriginalExchangeRefused("original_refresh_reservation_expired")
        return now

    @staticmethod
    def _original_digest(claims, secret_ref):
        return digest(canonical({"claims": claims, "secret_ref": secret_ref}))

    @staticmethod
    def _decode(row, bound):
        claims = json.loads(row["claims"]) if isinstance(row["claims"], str) else row["claims"]
        if (not isinstance(claims, dict) or set(claims) != CLAIM_FIELDS or claims.get("schema") != REFRESH_SCHEMA
                or claims.get("tenant") != bound.tenant or claims.get("project") != bound.project
                or claims.get("transaction_id") != bound.transaction_id or claims.get("slot") != "refresh"
                or claims.get("sub") != bound.credential_subject or claims.get("exp") != bound.expires_at
                or type(claims.get("iat")) is not int or not 0 < claims["iat"] < bound.expires_at
                or type(claims.get("exp")) is not int
                or type(claims.get("sid")) is not str or len(claims["sid"]) != 37
                or not claims["sid"].startswith("oref_")
                or any(char not in "0123456789abcdef" for char in claims["sid"][5:])
                or not row["original_digest"]
                or not hmac.compare_digest(row["original_digest"],
                    PostgresOriginalRefreshStore._original_digest(claims, row["secret_ref"]))):
            raise OriginalExchangeRefused("original_refresh_record_invalid")
        # Use the public receipt validator for opaque reference/id/hash shape.
        SessionIssuanceReceipt(claims.get("sid"), row["secret_ref"], row["bearer_sha256"] or "0" * 64).validated()
        return OriginalRefreshReservation(bound, row["inputs_digest"], claims,
                                          row["secret_ref"], row["bearer_sha256"], row["state"])

    async def reserve(self, *, plan, card_kind: str, ttl_seconds: int) -> OriginalRefreshReservation:
        bound, binding, fingerprint = self._binding(plan, card_kind, ttl_seconds)
        async with self._db._connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                                         self.schema + ":refresh:" + bound.identity, timeout=5)
                row = await self._row(connection, bound.identity, lock=True)
                if row is not None:
                    self._match(row, binding, fingerprint)
                now = await self._live(connection, bound, preparing=True)
                if row is None:
                    claims = {"schema": REFRESH_SCHEMA, "tenant": bound.tenant, "project": bound.project,
                              "transaction_id": bound.transaction_id, "slot": "refresh",
                              "sid": "oref_" + uuid.uuid4().hex, "sub": bound.credential_subject,
                              "iat": now, "exp": bound.expires_at}
                    secret_ref = uuid.uuid4().hex
                    await connection.execute(f"INSERT INTO {self.schema}.{TABLE_REFRESH} "
                        "(identity,inputs_digest,binding,claims,secret_ref,original_digest,state) "
                        "VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6,'reserved')",
                        bound.identity, fingerprint, canonical(binding), canonical(claims), secret_ref,
                        self._original_digest(claims, secret_ref), timeout=5)
                    row = await self._row(connection, bound.identity)
                return self._decode(row, bound)

    async def read(self, *, plan, card_kind: str, ttl_seconds: int) -> OriginalRefreshReservation:
        bound, binding, fingerprint = self._binding(plan, card_kind, ttl_seconds)
        async with self._db._connection() as connection:
            row = await self._row(connection, bound.identity)
            if row is None:
                raise OriginalExchangeRefused("original_refresh_missing")
            self._match(row, binding, fingerprint)
            await self._live(connection, bound)
        result = self._decode(row, bound)
        if result.state not in {"ready", "applied"} or result.bearer_sha256 is None:
            raise OriginalExchangeRefused("original_refresh_unsealed")
        return result

    async def seal(self, original: OriginalRefreshReservation, token_sha256: str, *, ready=False):
        if type(original) is not OriginalRefreshReservation or type(ready) is not bool:
            raise OriginalExchangeRefused("original_refresh_record_invalid")
        hex_digest(token_sha256)
        async with self._db._connection() as connection:
            async with connection.transaction():
                row = await self._row(connection, original.context.identity, lock=True)
                if row is None or row["inputs_digest"] != original.inputs_digest:
                    raise OriginalExchangeRefused("original_refresh_identity_conflict")
                if row["state"] == "retired":
                    raise OriginalExchangeRefused("original_refresh_terminal")
                binding = json.loads(row["binding"]) if isinstance(row["binding"], str) else row["binding"]
                stored_plan = OAuthIssuancePlan.from_mapping(binding["plan"])
                if original.context != PlannedIssuanceContext.from_oauth_plan(
                        stored_plan, slot="refresh", expires_at=stored_plan.expires_at):
                    raise OriginalExchangeRefused("original_refresh_identity_conflict")
                stored = self._decode(row, original.context)
                if (canonical(original.claims) != canonical(stored.claims)
                        or original.secret_ref != stored.secret_ref):
                    raise OriginalExchangeRefused("original_refresh_identity_conflict")
                await self._live(connection, original.context)
                if row["bearer_sha256"] is not None and not hmac.compare_digest(row["bearer_sha256"], token_sha256):
                    raise OriginalExchangeRefused("original_refresh_commitment_mismatch")
                state = "ready" if ready and row["state"] != "applied" else row["state"]
                await connection.execute(f"UPDATE {self.schema}.{TABLE_REFRESH} SET bearer_sha256=$2,state=$3 WHERE identity=$1",
                                         original.context.identity, token_sha256, state, timeout=5)
                return self._decode(await self._row(connection, original.context.identity), original.context)

    async def protect_applied(self, *, plan, card_kind: str, ttl_seconds: int, result):
        """Retain an applied original; this is not recipient-delivery evidence."""
        bound, binding, fingerprint = self._binding(plan, card_kind, ttl_seconds)
        applied = AppliedIssuanceContext.from_oauth_result(bound, result)
        async with self._db._connection() as connection:
            async with connection.transaction():
                row = await self._row(connection, bound.identity, lock=True)
                if row is None:
                    raise OriginalExchangeRefused("original_refresh_missing")
                self._match(row, binding, fingerprint)
                self._decode(row, bound)
                await self._live(connection, bound)
                if (row["state"] not in {"ready", "applied"} or row["bearer_sha256"] != applied.token_sha256
                        or row["applied_receipt"] not in {None, applied.digest}):
                    raise OriginalExchangeRefused("original_refresh_commitment_mismatch")
                await connection.execute(f"UPDATE {self.schema}.{TABLE_REFRESH} SET state='applied',applied_receipt=$2 WHERE identity=$1",
                                         bound.identity, applied.digest, timeout=5)

    async def retire(self, *, plan, card_kind: str, ttl_seconds: int, terminal) -> str | None:
        bound, binding, fingerprint = self._binding(plan, card_kind, ttl_seconds)
        terminal = TerminalIssuanceContext.from_context(terminal)
        if terminal.plan != bound:
            raise OriginalExchangeRefused("original_refresh_identity_conflict")
        async with self._db._connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                                         self.schema + ":refresh:" + bound.identity, timeout=5)
                row = await self._row(connection, bound.identity, lock=True)
                if terminal.state == "expired" and await connection.fetchval(
                        "SELECT clock_timestamp() < to_timestamp($1)", bound.delivery_deadline, timeout=5):
                    raise OriginalExchangeRefused("original_refresh_not_expired")
                if row is None:
                    await connection.execute(f"INSERT INTO {self.schema}.{TABLE_REFRESH} "
                        "(identity,inputs_digest,binding,state,terminal_digest) VALUES ($1,$2,$3::jsonb,'retired',$4)",
                        bound.identity, fingerprint, canonical(binding), terminal.digest, timeout=5)
                    return None
                stored = json.loads(row["binding"]) if isinstance(row["binding"], str) else row["binding"]
                if row["inputs_digest"] != fingerprint or canonical(stored) != canonical(binding):
                    raise OriginalExchangeRefused("original_refresh_identity_conflict")
                if row["claims"] is not None:
                    self._decode(row, bound)
                elif row["secret_ref"] is not None:
                    raise OriginalExchangeRefused("original_refresh_record_invalid")
                if row["state"] == "applied":
                    raise OriginalExchangeRefused("original_refresh_already_applied")
                if row["terminal_digest"] not in {None, terminal.digest}:
                    raise OriginalExchangeRefused("original_refresh_identity_conflict")
                if terminal.token_sha256 and row["bearer_sha256"] not in {None, terminal.token_sha256}:
                    raise OriginalExchangeRefused("original_refresh_commitment_mismatch")
                await connection.execute(f"UPDATE {self.schema}.{TABLE_REFRESH} SET state='retired',terminal_digest=$2 WHERE identity=$1",
                                         bound.identity, terminal.digest, timeout=5)
                return row["secret_ref"]

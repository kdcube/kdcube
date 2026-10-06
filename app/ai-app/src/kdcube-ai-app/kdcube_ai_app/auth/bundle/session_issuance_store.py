# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable reservations for one recoverable session issuance identity.

Reservations contain only public coordinates and a hash-only session record.
The issuer must save the original bearer in create-only secret custody before
activation. Both steps use this reservation's fixed coordinates. Completed or
expired reservations remain identity tombstones; removing them would permit a
previous transaction to mint again.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_SESSIONS, TABLE_USERS

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_SECRET_FIELDS = frozenset({
    "token", "access_token", "refresh_token", "id_token", "bearer", "secret",
    "password", "client_secret", "private_key", "authorization", "cookie", "api_key",
})


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _no_secret(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise SessionIssuanceRefused("issuance_record_invalid")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise SessionIssuanceRefused("issuance_record_invalid")
            normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")
            if normalized in _SECRET_FIELDS:
                raise SessionIssuanceRefused("issuance_record_invalid")
            _no_secret(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _no_secret(child, depth + 1)


@dataclass(frozen=True)
class SessionIssuanceReservation:
    identity: str
    inputs_digest: str
    session_id: str
    secret_ref: str
    expires_at: int
    expected_version: int
    record: dict[str, Any]
    state: str
    created: bool = False
    user_record: dict[str, Any] | None = None
    expected_user_revision: int | None = None


class PostgresSessionIssuanceStore:
    """An identity ledger within the existing bundle-session authority schema."""

    def __init__(self, *, pg_pool: Any, schema: str, tenant: str, project: str) -> None:
        self._pool = pg_pool
        self.schema = schema  # produced by the existing project_schema boundary
        self.tenant = tenant
        self.project = project

    @staticmethod
    def _reservation(row: Any, *, created: bool = False) -> SessionIssuanceReservation:
        value = dict(row)
        return SessionIssuanceReservation(
            identity=value["identity"], inputs_digest=value["inputs_digest"],
            session_id=value["session_id"], secret_ref=value["secret_ref"],
            expires_at=int(value["expires_epoch"]),
            expected_version=int(value["expected_version"]),
            record=_record(value["session_record"]), state=value["state"], created=created,
            user_record=_record(value.get("user_record")) or None,
            expected_user_revision=(int(value["expected_user_revision"])
                                    if value.get("expected_user_revision") is not None else None),
        )

    async def read_issuance(self, identity: str) -> SessionIssuanceReservation | None:
        if type(identity) is not str or not _HEX64.fullmatch(identity):
            raise SessionIssuanceRefused("issuance_identity_invalid")
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT *, floor(extract(epoch FROM expires_at))::bigint AS expires_epoch "
                f"FROM {self.schema}.{TABLE_ISSUANCES} WHERE identity = $1", identity,
            )
        return self._reservation(row) if row is not None else None

    async def reserve_issuance(
        self, identity: str, inputs_digest: str, session_id: str, secret_ref: str,
        expires_at: int, *, session_record: Mapping[str, Any], expected_version: int,
        user_record: Mapping[str, Any] | None = None,
        expected_user_revision: int | None = None,
    ) -> SessionIssuanceReservation:
        if not isinstance(session_record, Mapping):
            raise SessionIssuanceRefused("issuance_record_invalid")
        record = dict(session_record)
        if (
            type(identity) is not str or not _HEX64.fullmatch(identity)
            or type(inputs_digest) is not str or not _HEX64.fullmatch(inputs_digest)
            or type(secret_ref) is not str or not _HEX32.fullmatch(secret_ref)
            or type(session_id) is not str or not session_id or len(session_id) > 256
            or type(expires_at) is not int or expires_at <= 0
            or type(expected_version) is not int or expected_version < 1
            or (expected_user_revision is not None
                and (type(expected_user_revision) is not int or expected_user_revision < 0))
            or record.get("session_id") != session_id
            or not isinstance(record.get("sub"), str) or not record["sub"]
            or type(record.get("token_sha256")) is not str
            or not _HEX64.fullmatch(record["token_sha256"])
            or type(record.get("version")) is not int or record["version"] != expected_version
            or type(record.get("iat")) is not int or record["iat"] <= 0
            or type(record.get("exp")) is not int
            or not record["iat"] < record["exp"] <= expires_at
            or record.get("max_exp") != expires_at
            or record.get("last_seen") != record["iat"]
        ):
            raise SessionIssuanceRefused("issuance_record_invalid")
        _no_secret(record)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 65536:
            raise SessionIssuanceRefused("issuance_record_invalid")
        profile = dict(user_record) if user_record is not None else None
        if profile is not None:
            if profile.get("sub") != record["sub"]:
                raise SessionIssuanceRefused("issuance_record_invalid")
            _no_secret(profile)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                # Serialize this identity before provisioning any user. A
                # conflict cannot mutate the user, even on the first attempt.
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    self.schema + ":session-issuance:" + identity,
                )
                prior = await connection.fetchrow(
                    f"SELECT *, floor(extract(epoch FROM expires_at))::bigint AS expires_epoch "
                    f"FROM {self.schema}.{TABLE_ISSUANCES} WHERE identity = $1", identity,
                )
                if prior is not None:
                    original = self._reservation(prior)
                    if (original.inputs_digest != inputs_digest
                            or original.expires_at != expires_at
                            or original.record.get("sub") != record["sub"]):
                        raise SessionIssuanceRefused("issuance_identity_conflict")
                    return original
                provisioned_subject = None
                if profile is not None:
                    provisioned = {**profile, "roles": [], "permissions": []}
                    provisioned_subject = await connection.fetchval(
                        f"""
                        INSERT INTO {self.schema}.{TABLE_USERS} (
                            subject, tenant, project, record, session_version,
                            state, disabled, created_at, updated_at
                        ) VALUES ($1, $2, $3, ($4::text)::jsonb, 1,
                            'active', FALSE, to_timestamp($5), to_timestamp($5))
                        ON CONFLICT (subject) DO NOTHING RETURNING subject
                        """, record["sub"], self.tenant, self.project,
                        json.dumps(provisioned, sort_keys=True, separators=(",", ":")), record["iat"],
                    )
                # All authority writes lock the user before a session/issuance.
                # Following that order avoids a revoke-versus-activate deadlock.
                user = await connection.fetchrow(
                    f"SELECT subject, session_version, revision, record, state, disabled FROM {self.schema}.{TABLE_USERS} "
                    "WHERE subject = $1 FOR UPDATE", record["sub"],
                )
                if user is None:
                    raise SessionIssuanceRefused("issuance_user_missing")
                if (user["state"] != "active" or user["disabled"]
                        or int(user["session_version"]) != expected_version):
                    raise SessionIssuanceRefused("issuance_authority_moved")
                captured_revision = int(user["revision"])
                if (expected_user_revision is not None
                        and ((expected_user_revision == 0 and provisioned_subject is None)
                             or (expected_user_revision > 0
                                 and captured_revision != expected_user_revision))):
                    raise SessionIssuanceRefused("issuance_authority_moved")
                inserted = await connection.fetchrow(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_ISSUANCES} (
                        identity, inputs_digest, session_id, secret_ref, subject,
                        expected_version, session_record, expires_at, user_record, expected_user_revision
                    ) VALUES ($1, $2, $3, $4, $5, $6, ($7::text)::jsonb, to_timestamp($8), ($9::text)::jsonb, $10)
                    ON CONFLICT (identity) DO NOTHING RETURNING identity
                    """, identity, inputs_digest, session_id, secret_ref, record["sub"],
                    expected_version, encoded, expires_at,
                    json.dumps(profile, sort_keys=True, separators=(",", ":")) if profile is not None else None,
                    captured_revision,
                )
                row = await connection.fetchrow(
                    f"SELECT *, floor(extract(epoch FROM expires_at))::bigint AS expires_epoch "
                    f"FROM {self.schema}.{TABLE_ISSUANCES} WHERE identity = $1 FOR UPDATE", identity,
                )
                reservation = self._reservation(row, created=inserted is not None)
                if (
                    reservation.inputs_digest != inputs_digest
                    or reservation.expires_at != expires_at
                    or reservation.record.get("sub") != record["sub"]
                ):
                    raise SessionIssuanceRefused("issuance_identity_conflict")
        return reservation

    async def activate_reserved(self, identity: str) -> SessionIssuanceReservation:
        initial = await self.read_issuance(identity)
        if initial is None:
            raise SessionIssuanceRefused("issuance_reservation_missing")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                user = await connection.fetchrow(
                    f"SELECT session_version, revision, record, state, disabled FROM {self.schema}.{TABLE_USERS} "
                    "WHERE subject = $1 FOR UPDATE", initial.record["sub"],
                )
                row = await connection.fetchrow(
                    f"SELECT *, floor(extract(epoch FROM expires_at))::bigint AS expires_epoch, "
                    f"expires_at > clock_timestamp() AND "
                    "(session_record->>'exp')::bigint > extract(epoch FROM clock_timestamp()) "
                    f"AS live FROM {self.schema}.{TABLE_ISSUANCES} "
                    "WHERE identity = $1 FOR UPDATE", identity,
                )
                current = self._reservation(row)
                if not row["live"]:
                    raise SessionIssuanceRefused("issuance_expired")
                if (user is None or user["state"] != "active" or user["disabled"]
                        or int(user["session_version"]) != current.expected_version):
                    raise SessionIssuanceRefused("issuance_authority_moved")
                if (current.state != "active"
                        and (current.expected_user_revision is None
                             or int(user["revision"]) != current.expected_user_revision)):
                    raise SessionIssuanceRefused("issuance_authority_moved")
                record = current.record
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_SESSIONS} (
                        session_id, subject, token_sha256, record, state,
                        issued_at, idle_expires_at, hard_expires_at, last_seen_at
                    ) VALUES ($1, $2, $3, ($4::text)::jsonb, 'active',
                        to_timestamp($5), to_timestamp($6), to_timestamp($7), to_timestamp($8))
                    ON CONFLICT (session_id) DO NOTHING
                    """, current.session_id, record["sub"], record["token_sha256"],
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                    record["iat"], record["exp"], record["max_exp"], record["last_seen"],
                )
                session = await connection.fetchrow(
                    f"SELECT record, state FROM {self.schema}.{TABLE_SESSIONS} "
                    "WHERE session_id = $1 FOR UPDATE", current.session_id,
                )
                if session["state"] != "active" or _record(session["record"]) != record:
                    raise SessionIssuanceRefused("issuance_session_conflict")
                if current.state != "active":
                    if current.user_record is not None:
                        old_profile = _record(user["record"])
                        # Install the grant only at first activation. Recovery
                        # of an already-active issuance never rolls back the
                        # user's authority after a later grant or revocation.
                        updated = {**old_profile, "roles": list(current.user_record.get("roles") or []),
                                   "permissions": list(current.user_record.get("permissions") or [])}
                        if updated != old_profile:
                            updated["updated_at"] = record["iat"]
                            await connection.execute(
                                f"UPDATE {self.schema}.{TABLE_USERS} SET record = ($2::text)::jsonb, "
                                "revision = revision + 1, updated_at = clock_timestamp() WHERE subject = $1",
                                record["sub"], json.dumps(updated, sort_keys=True, separators=(",", ":")),
                            )
                    await connection.execute(
                        f"UPDATE {self.schema}.{TABLE_ISSUANCES} "
                        "SET state = 'active', activated_at = now() WHERE identity = $1", identity,
                    )
        return SessionIssuanceReservation(**{**current.__dict__, "state": "active"})

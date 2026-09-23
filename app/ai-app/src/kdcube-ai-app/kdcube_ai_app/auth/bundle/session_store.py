# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from kdcube_ai_app.auth.bundle.session_schema import (
    TABLE_SESSIONS,
    TABLE_USERS,
    bundle_session_schema,
    bundle_session_schema_sql,
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), separators=(",", ":"), sort_keys=True)


def _affected_rows(status: str) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class BundleSessionLoginState:
    user: dict[str, Any]
    version: int
    user_revision: int = 0


@dataclass(frozen=True)
class BundleSessionValidationState:
    session: dict[str, Any]
    session_state: str
    idle_expires_at: int
    hard_expires_at: int
    user: dict[str, Any]
    user_state: str
    user_disabled: bool
    version: int
    session_revision: int = 0
    user_revision: int = 0


class BundleSessionStore(Protocol):
    async def register_user(
        self,
        *,
        sub: str,
        updates: Mapping[str, Any],
        now: int,
    ) -> dict[str, Any]: ...

    async def get_user(self, sub: str) -> dict[str, Any] | None: ...

    async def get_login_state(self, sub: str) -> BundleSessionLoginState | None: ...

    async def issue_session(
        self,
        record: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> bool: ...

    async def get_validation_state(
        self,
        session_id: str,
    ) -> BundleSessionValidationState | None: ...

    async def touch_session(
        self,
        session_id: str,
        *,
        expires_at: int,
        now: int,
    ) -> dict[str, Any] | None: ...

    async def revoke_session(self, session_id: str) -> bool: ...

    async def invalidate_user(self, sub: str) -> int: ...

    async def delete_user(self, sub: str) -> bool: ...


class PostgresBundleSessionStore:
    """PostgreSQL authority for bundle users, epochs, and live sessions."""

    def __init__(self, *, pg_pool: Any, tenant: str, project: str) -> None:
        if pg_pool is None:
            raise RuntimeError("PostgresBundleSessionStore requires pg_pool")
        self._pool = pg_pool
        self.tenant = str(tenant or "").strip() or "default"
        self.project = str(project or "").strip() or "default"
        self.schema = bundle_session_schema(
            tenant=self.tenant,
            project=self.project,
        )

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(bundle_session_schema_sql(self.schema))

    async def import_user_authority(
        self,
        *,
        subject: str,
        record: Mapping[str, Any] | None,
        session_version: int,
    ) -> bool:
        """Insert one Redis user/version authority or prove an exact rerun."""

        sub = str(subject or "").strip()
        version = int(session_version)
        if not sub or version < 1:
            raise ValueError("bundle user migration identity is invalid")
        source_record = dict(record) if record is not None else None
        state = "active" if source_record is not None else "deleted"
        stored_record = source_record or {"sub": sub, "disabled": True}
        if str(stored_record.get("sub") or "") != sub:
            raise ValueError("bundle user migration subject mismatch")
        disabled = bool(stored_record.get("disabled") or state == "deleted")
        created_at = int(stored_record.get("created_at") or 0)
        updated_at = int(stored_record.get("updated_at") or created_at)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                status = await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_USERS} (
                        subject, tenant, project, record, session_version,
                        state, disabled, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, ($4::text)::jsonb, $5,
                        $6, $7, to_timestamp($8), to_timestamp($9)
                    )
                    ON CONFLICT (subject) DO NOTHING
                    """,
                    sub,
                    self.tenant,
                    self.project,
                    _json_text(stored_record),
                    version,
                    state,
                    disabled,
                    created_at,
                    updated_at,
                )
                row = await connection.fetchrow(
                    f"""
                    SELECT tenant, project, record, session_version,
                           state, disabled
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1
                    FOR UPDATE
                    """,
                    sub,
                )
                value = dict(row) if row is not None else {}
                if (
                    str(value.get("tenant") or "") != self.tenant
                    or str(value.get("project") or "") != self.project
                    or _json_object(value.get("record")) != stored_record
                    or int(value.get("session_version") or 0) != version
                    or str(value.get("state") or "") != state
                    or bool(value.get("disabled")) != disabled
                ):
                    raise RuntimeError("bundle_user_migration_target_conflict")
        return str(status or "").strip().endswith(" 1")

    async def import_session_authority(self, record: Mapping[str, Any]) -> bool:
        """Insert one live Redis bundle session without renewing its expiry."""

        payload = dict(record or {})
        subject = str(payload.get("sub") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        token_sha256 = str(payload.get("token_sha256") or "").strip().lower()
        issued_at = int(payload.get("iat") or 0)
        idle_expires_at = int(payload.get("exp") or 0)
        hard_expires_at = int(payload.get("max_exp") or idle_expires_at)
        last_seen = int(payload.get("last_seen") or issued_at)
        if (
            not subject
            or not session_id
            or len(token_sha256) != 64
            or issued_at <= 0
            or idle_expires_at <= issued_at
            or hard_expires_at < idle_expires_at
        ):
            raise ValueError("bundle session migration record is invalid")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                user = await connection.fetchrow(
                    f"""
                    SELECT session_version, state, disabled
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1
                    FOR UPDATE
                    """,
                    subject,
                )
                user_value = dict(user) if user is not None else {}
                if (
                    str(user_value.get("state") or "") != "active"
                    or bool(user_value.get("disabled"))
                    or int(user_value.get("session_version") or 0)
                    != int(payload.get("version") or 0)
                ):
                    raise RuntimeError("bundle_session_migration_user_conflict")
                status = await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_SESSIONS} (
                        session_id, subject, token_sha256, record, state,
                        issued_at, idle_expires_at, hard_expires_at,
                        last_seen_at
                    ) VALUES (
                        $1, $2, $3, ($4::text)::jsonb, 'active',
                        to_timestamp($5), to_timestamp($6), to_timestamp($7),
                        to_timestamp($8)
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    session_id,
                    subject,
                    token_sha256,
                    _json_text(payload),
                    issued_at,
                    idle_expires_at,
                    hard_expires_at,
                    last_seen,
                )
                row = await connection.fetchrow(
                    f"""
                    SELECT subject, token_sha256, record, state,
                           floor(extract(epoch FROM issued_at))::bigint AS issued_at,
                           floor(extract(epoch FROM idle_expires_at))::bigint
                               AS idle_expires_at,
                           floor(extract(epoch FROM hard_expires_at))::bigint
                               AS hard_expires_at,
                           floor(extract(epoch FROM last_seen_at))::bigint AS last_seen
                    FROM {self.schema}.{TABLE_SESSIONS}
                    WHERE session_id = $1
                    FOR UPDATE
                    """,
                    session_id,
                )
                value = dict(row) if row is not None else {}
                if (
                    str(value.get("subject") or "") != subject
                    or str(value.get("token_sha256") or "") != token_sha256
                    or _json_object(value.get("record")) != payload
                    or str(value.get("state") or "") != "active"
                    or int(value.get("issued_at") or 0) != issued_at
                    or int(value.get("idle_expires_at") or 0) != idle_expires_at
                    or int(value.get("hard_expires_at") or 0) != hard_expires_at
                    or int(value.get("last_seen") or 0) != last_seen
                ):
                    raise RuntimeError("bundle_session_migration_target_conflict")
        return str(status or "").strip().endswith(" 1")

    async def migration_rows(
        self,
        *,
        captured_at_ms: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with self._pool.acquire() as connection:
            users = await connection.fetch(
                f"""
                SELECT subject, record, session_version, state, disabled
                FROM {self.schema}.{TABLE_USERS}
                ORDER BY subject
                """
            )
            sessions = await connection.fetch(
                f"""
                SELECT session_id, record,
                       floor(extract(epoch FROM idle_expires_at) * 1000)::bigint
                           AS expires_at_ms
                FROM {self.schema}.{TABLE_SESSIONS}
                WHERE state = 'active'
                  AND idle_expires_at > to_timestamp($1::double precision / 1000.0)
                ORDER BY session_id
                """,
                int(captured_at_ms),
            )
        user_rows = []
        for row in users:
            value = dict(row)
            value["record"] = _json_object(value.get("record"))
            user_rows.append(value)
        session_rows = []
        for row in sessions:
            value = dict(row)
            value["record"] = _json_object(value.get("record"))
            session_rows.append(value)
        return user_rows, session_rows

    async def register_user(
        self,
        *,
        sub: str,
        updates: Mapping[str, Any],
        now: int,
    ) -> dict[str, Any]:
        subject = str(sub or "").strip()
        if not subject:
            raise ValueError("bundle session user sub is required")
        timestamp = int(now)
        initial = {
            "sub": subject,
            "roles": [],
            "permissions": [],
            "metadata": {},
            "disabled": False,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        created = dict(initial)
        created.update(dict(updates))
        created["sub"] = subject
        created["created_at"] = timestamp
        created["updated_at"] = timestamp
        created["disabled"] = bool(created.get("disabled") or False)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_USERS} (
                        subject, tenant, project, record, session_version,
                        state, disabled, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, ($4::text)::jsonb, 1,
                        'active', $5, to_timestamp($6), to_timestamp($6)
                    )
                    ON CONFLICT (subject) DO NOTHING
                    """,
                    subject,
                    self.tenant,
                    self.project,
                    _json_text(created),
                    bool(created["disabled"]),
                    timestamp,
                )
                row = await connection.fetchrow(
                    f"""
                    SELECT record, state, disabled, revision
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1
                    FOR UPDATE
                    """,
                    subject,
                )
                row_value = dict(row) if row else {}
                existing_record = _json_object(row_value.get("record"))
                existing = (
                    initial
                    if str(row_value.get("state") or "") == "deleted"
                    else existing_record or initial
                )
                merged = dict(existing)
                merged.update(dict(updates))
                merged["sub"] = subject
                merged["created_at"] = int(existing.get("created_at") or timestamp)
                merged["disabled"] = bool(merged.get("disabled") or False)
                comparable_existing = dict(existing_record)
                comparable_existing.pop("updated_at", None)
                comparable_merged = dict(merged)
                comparable_merged.pop("updated_at", None)
                changed = (
                    str(row_value.get("state") or "") != "active"
                    or bool(row_value.get("disabled")) != bool(merged["disabled"])
                    or comparable_existing != comparable_merged
                )
                if changed:
                    merged["updated_at"] = timestamp
                    await connection.execute(
                        f"""
                        UPDATE {self.schema}.{TABLE_USERS}
                        SET record = ($2::text)::jsonb,
                            disabled = $3,
                            state = 'active',
                            revision = revision + 1,
                            updated_at = to_timestamp($4)
                        WHERE subject = $1
                        """,
                        subject,
                        _json_text(merged),
                        bool(merged["disabled"]),
                        timestamp,
                    )
                else:
                    merged = existing_record
        return merged

    async def get_user(self, sub: str) -> dict[str, Any] | None:
        subject = str(sub or "").strip()
        if not subject:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT record
                FROM {self.schema}.{TABLE_USERS}
                WHERE subject = $1 AND state = 'active'
                """,
                subject,
            )
        return _json_object(dict(row).get("record")) if row is not None else None

    async def get_login_state(self, sub: str) -> BundleSessionLoginState | None:
        subject = str(sub or "").strip()
        if not subject:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT record, session_version, revision
                FROM {self.schema}.{TABLE_USERS}
                WHERE subject = $1 AND state = 'active'
                """,
                subject,
            )
        if row is None:
            return None
        value = dict(row)
        return BundleSessionLoginState(
            user=_json_object(value.get("record")),
            version=int(value.get("session_version") or 0),
            user_revision=int(value.get("revision") or 0),
        )

    async def issue_session(
        self,
        record: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> bool:
        payload = dict(record)
        subject = str(payload.get("sub") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        token_sha256 = str(payload.get("token_sha256") or "").strip()
        if not subject or not session_id or len(token_sha256) != 64:
            raise ValueError("bundle session record identity is invalid")
        issued_at = int(payload.get("iat") or 0)
        idle_expires_at = int(payload.get("exp") or 0)
        hard_expires_at = int(payload.get("max_exp") or idle_expires_at)
        last_seen = int(payload.get("last_seen") or issued_at)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                user = await connection.fetchrow(
                    f"""
                    SELECT session_version, state, disabled
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1
                    FOR UPDATE
                    """,
                    subject,
                )
                if user is None:
                    return False
                current = dict(user)
                if (
                    str(current.get("state") or "") != "active"
                    or bool(current.get("disabled"))
                    or int(current.get("session_version") or 0)
                    != int(expected_version)
                ):
                    return False
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_SESSIONS} (
                        session_id, subject, token_sha256, record, state,
                        issued_at, idle_expires_at, hard_expires_at,
                        last_seen_at
                    ) VALUES (
                        $1, $2, $3, ($4::text)::jsonb, 'active',
                        to_timestamp($5), to_timestamp($6), to_timestamp($7),
                        to_timestamp($8)
                    )
                    """,
                    session_id,
                    subject,
                    token_sha256,
                    _json_text(payload),
                    issued_at,
                    idle_expires_at,
                    hard_expires_at,
                    last_seen,
                )
        return True

    async def get_validation_state(
        self,
        session_id: str,
    ) -> BundleSessionValidationState | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT bundle_session.record AS session_record,
                       bundle_session.state AS session_state,
                       bundle_session.revision AS session_revision,
                       floor(extract(epoch FROM bundle_session.idle_expires_at))::bigint
                           AS idle_expires_at,
                       floor(extract(epoch FROM bundle_session.hard_expires_at))::bigint
                           AS hard_expires_at,
                       bundle_user.record AS user_record,
                       bundle_user.state AS user_state,
                       bundle_user.disabled AS user_disabled,
                       bundle_user.session_version,
                       bundle_user.revision AS user_revision
                FROM {self.schema}.{TABLE_SESSIONS} AS bundle_session
                JOIN {self.schema}.{TABLE_USERS} AS bundle_user
                  ON bundle_user.subject = bundle_session.subject
                WHERE bundle_session.session_id = $1
                """,
                sid,
            )
        if row is None:
            return None
        value = dict(row)
        return BundleSessionValidationState(
            session=_json_object(value.get("session_record")),
            session_state=str(value.get("session_state") or ""),
            idle_expires_at=int(value.get("idle_expires_at") or 0),
            hard_expires_at=int(value.get("hard_expires_at") or 0),
            user=_json_object(value.get("user_record")),
            user_state=str(value.get("user_state") or ""),
            user_disabled=bool(value.get("user_disabled")),
            version=int(value.get("session_version") or 0),
            session_revision=int(value.get("session_revision") or 0),
            user_revision=int(value.get("user_revision") or 0),
        )

    async def touch_session(
        self,
        session_id: str,
        *,
        expires_at: int,
        now: int,
    ) -> dict[str, Any] | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""
                    WITH candidate AS (
                        SELECT session_id,
                               record,
                               idle_expires_at,
                               GREATEST(
                                   idle_expires_at,
                                   LEAST(to_timestamp($2), hard_expires_at)
                               ) AS next_expiry
                        FROM {self.schema}.{TABLE_SESSIONS}
                        WHERE session_id = $1
                          AND state = 'active'
                          AND idle_expires_at >= to_timestamp($3)
                          AND hard_expires_at >= to_timestamp($3)
                        FOR UPDATE
                    ), updated AS (
                        UPDATE {self.schema}.{TABLE_SESSIONS} AS bundle_session
                        SET idle_expires_at = candidate.next_expiry,
                            record = bundle_session.record || jsonb_build_object(
                                'exp', floor(extract(epoch FROM candidate.next_expiry))::bigint
                            ),
                            revision = bundle_session.revision + 1,
                            updated_at = now()
                        FROM candidate
                        WHERE bundle_session.session_id = candidate.session_id
                          AND candidate.idle_expires_at < to_timestamp($3) + (
                              (candidate.next_expiry - to_timestamp($3)) / 2
                          )
                        RETURNING bundle_session.record
                    )
                    SELECT record FROM updated
                    UNION ALL
                    SELECT candidate.record
                    FROM candidate
                    WHERE NOT EXISTS (SELECT 1 FROM updated)
                    LIMIT 1
                    """,
                    sid,
                    int(expires_at),
                    int(now),
                )
        return _json_object(dict(row).get("record")) if row is not None else None

    async def revoke_session(self, session_id: str) -> bool:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                status = await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_SESSIONS}
                    SET state = 'revoked',
                        revision = revision + 1,
                        revoked_at = now(),
                        updated_at = now()
                    WHERE session_id = $1 AND state = 'active'
                    """,
                    sid,
                )
        return _affected_rows(status) > 0

    async def invalidate_user(self, sub: str) -> int:
        subject = str(sub or "").strip()
        if not subject:
            return 0
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                user = await connection.fetchrow(
                    f"""
                    SELECT subject
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1 AND state = 'active'
                    FOR UPDATE
                    """,
                    subject,
                )
                if user is None:
                    return 0
                await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_USERS}
                    SET session_version = session_version + 1,
                        revision = revision + 1,
                        updated_at = now()
                    WHERE subject = $1 AND state = 'active'
                    """,
                    subject,
                )
                status = await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_SESSIONS}
                    SET state = 'revoked',
                        revision = revision + 1,
                        revoked_at = now(),
                        updated_at = now()
                    WHERE subject = $1 AND state = 'active'
                    """,
                    subject,
                )
        return _affected_rows(status)

    async def delete_user(self, sub: str) -> bool:
        subject = str(sub or "").strip()
        if not subject:
            return False
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                user = await connection.fetchrow(
                    f"""
                    SELECT subject
                    FROM {self.schema}.{TABLE_USERS}
                    WHERE subject = $1 AND state = 'active'
                    FOR UPDATE
                    """,
                    subject,
                )
                if user is None:
                    return False
                await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_USERS}
                    SET state = 'deleted',
                        disabled = TRUE,
                        session_version = session_version + 1,
                        revision = revision + 1,
                        record = record || jsonb_build_object(
                            'disabled', TRUE,
                            'updated_at', floor(extract(epoch FROM now()))::bigint
                        ),
                        updated_at = now()
                    WHERE subject = $1 AND state = 'active'
                    """,
                    subject,
                )
                await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_SESSIONS}
                    SET state = 'revoked',
                        revision = revision + 1,
                        revoked_at = now(),
                        updated_at = now()
                    WHERE subject = $1 AND state = 'active'
                    """,
                    subject,
                )
        return True

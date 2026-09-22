# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from kdcube_ai_app.auth.platform_session_schema import (
    TABLE_PLATFORM_SESSIONS,
    platform_session_schema,
    platform_session_schema_sql,
)

_USER_UPDATE_FIELDS = (
    "roles",
    "permissions",
    "user_id",
    "username",
    "email",
    "identity_authority",
    "rate_limit_subject",
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


def _merge_record(
    existing: Mapping[str, Any],
    *,
    user_data: Mapping[str, Any],
    request_context: Mapping[str, Any],
    user_type: str,
) -> dict[str, Any]:
    merged = dict(existing)
    merged["user_type"] = str(user_type)
    for field in _USER_UPDATE_FIELDS:
        if field in user_data:
            merged[field] = user_data[field]
    merged["request_context"] = dict(request_context)
    timezone = request_context.get("user_timezone")
    if timezone not in (None, ""):
        merged["timezone"] = timezone
    for field in ("roles", "permissions"):
        value = merged.get(field)
        merged[field] = list(value) if isinstance(value, (list, tuple, set)) else []
    return merged


@dataclass(frozen=True)
class PlatformSessionStoreResult:
    record: dict[str, Any]
    created: bool
    revision: int = 0


class PlatformSessionStore(Protocol):
    async def get_or_create(
        self,
        *,
        authority_key: str,
        candidate: Mapping[str, Any],
        user_data: Mapping[str, Any],
        request_context: Mapping[str, Any],
        user_type: str,
        ttl_seconds: int,
        now: float,
    ) -> PlatformSessionStoreResult: ...

    async def update_session(
        self,
        record: Mapping[str, Any],
        *,
        ttl_seconds: int,
        now: float,
    ) -> bool: ...

    async def get_session_by_id(self, session_id: str) -> dict[str, Any] | None: ...

    async def get_session_by_user_id(self, user_id: str) -> dict[str, Any] | None: ...


class PostgresPlatformSessionStore:
    """Transactional authority for active platform sessions."""

    def __init__(self, *, pg_pool: Any, tenant: str, project: str) -> None:
        if pg_pool is None:
            raise RuntimeError("PostgresPlatformSessionStore requires pg_pool")
        self._pool = pg_pool
        self.tenant = str(tenant or "").strip() or "default"
        self.project = str(project or "").strip() or "default"
        self.schema = platform_session_schema(
            tenant=self.tenant,
            project=self.project,
        )

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(platform_session_schema_sql(self.schema))

    async def get_or_create(
        self,
        *,
        authority_key: str,
        candidate: Mapping[str, Any],
        user_data: Mapping[str, Any],
        request_context: Mapping[str, Any],
        user_type: str,
        ttl_seconds: int,
        now: float,
    ) -> PlatformSessionStoreResult:
        key = str(authority_key or "").strip()
        if not key:
            raise ValueError("platform session authority key is required")
        timestamp = float(now)
        ttl = max(1, int(ttl_seconds))
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"{self.schema}:{key}",
                )
                row = await connection.fetchrow(
                    f"""
                    SELECT session_id, record, revision,
                           extract(epoch FROM expires_at) AS expires_at
                    FROM {self.schema}.{TABLE_PLATFORM_SESSIONS}
                    WHERE authority_key = $1 AND state = 'active'
                    FOR UPDATE
                    """,
                    key,
                )
                if row is not None:
                    current = dict(row)
                    current_revision = int(current.get("revision") or 0)
                    if float(current.get("expires_at") or 0) > timestamp:
                        merged = _merge_record(
                            _json_object(current.get("record")),
                            user_data=user_data,
                            request_context=request_context,
                            user_type=user_type,
                        )
                        previous = _json_object(current.get("record"))
                        should_extend = float(current.get("expires_at") or 0) < (
                            timestamp + (ttl / 2)
                        )
                        if merged != previous or should_extend:
                            merged["last_seen"] = timestamp
                            await connection.execute(
                                f"""
                                UPDATE {self.schema}.{TABLE_PLATFORM_SESSIONS}
                                SET user_type = $2,
                                    user_id = $3,
                                    fingerprint = $4,
                                    record = ($5::text)::jsonb,
                                    revision = revision + 1,
                                    last_seen_at = to_timestamp($6),
                                    expires_at = CASE
                                        WHEN expires_at < to_timestamp($6) + (
                                            ($7 * interval '1 second') / 2
                                        )
                                        THEN to_timestamp($6) + ($7 * interval '1 second')
                                        ELSE expires_at
                                    END,
                                    updated_at = now()
                                WHERE session_id = $1 AND state = 'active'
                                """,
                                str(current.get("session_id") or ""),
                                str(user_type),
                                str(merged.get("user_id") or "").strip() or None,
                                str(merged.get("fingerprint") or "").strip() or None,
                                _json_text(merged),
                                timestamp,
                                ttl,
                            )
                            current_revision += 1
                        return PlatformSessionStoreResult(
                            merged,
                            False,
                            current_revision,
                        )
                    await connection.execute(
                        f"""
                        UPDATE {self.schema}.{TABLE_PLATFORM_SESSIONS}
                        SET state = 'expired',
                            revision = revision + 1,
                            updated_at = now()
                        WHERE session_id = $1 AND state = 'active'
                        """,
                        str(current.get("session_id") or ""),
                    )

                created = _merge_record(
                    candidate,
                    user_data=user_data,
                    request_context=request_context,
                    user_type=user_type,
                )
                created["created_at"] = timestamp
                created["last_seen"] = timestamp
                session_id = str(created.get("session_id") or "").strip()
                if not session_id:
                    raise ValueError("platform session id is required")
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema}.{TABLE_PLATFORM_SESSIONS} (
                        session_id, authority_key, tenant, project,
                        user_type, user_id, fingerprint, record, state,
                        created_at, last_seen_at, expires_at
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, ($8::text)::jsonb, 'active',
                        to_timestamp($9), to_timestamp($9),
                        to_timestamp($9) + ($10 * interval '1 second')
                    )
                    """,
                    session_id,
                    key,
                    self.tenant,
                    self.project,
                    str(user_type),
                    str(created.get("user_id") or "").strip() or None,
                    str(created.get("fingerprint") or "").strip() or None,
                    _json_text(created),
                    timestamp,
                    ttl,
                )
        return PlatformSessionStoreResult(created, True, 1)

    async def update_session(
        self,
        record: Mapping[str, Any],
        *,
        ttl_seconds: int,
        now: float,
    ) -> bool:
        """Persist an explicit mutation, including exact last-seen and TTL renewal.

        Per-request resolution uses ``get_or_create``, whose unchanged path is
        write-free and renews only after the half-TTL threshold.
        """
        payload = dict(record)
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return False
        timestamp = float(now)
        payload["last_seen"] = timestamp
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                status = await connection.execute(
                    f"""
                    UPDATE {self.schema}.{TABLE_PLATFORM_SESSIONS}
                    SET user_type = $2,
                        user_id = $3,
                        fingerprint = $4,
                        record = ($5::text)::jsonb,
                        revision = revision + 1,
                        last_seen_at = to_timestamp($6),
                        expires_at = to_timestamp($6) + ($7 * interval '1 second'),
                        updated_at = now()
                    WHERE session_id = $1
                      AND state = 'active'
                      AND expires_at > to_timestamp($6)
                    """,
                    session_id,
                    str(payload.get("user_type") or ""),
                    str(payload.get("user_id") or "").strip() or None,
                    str(payload.get("fingerprint") or "").strip() or None,
                    _json_text(payload),
                    timestamp,
                    max(1, int(ttl_seconds)),
                )
        return str(status) != "UPDATE 0"

    async def get_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT record
                FROM {self.schema}.{TABLE_PLATFORM_SESSIONS}
                WHERE session_id = $1
                  AND state = 'active'
                  AND expires_at > now()
                """,
                sid,
            )
        return _json_object(dict(row).get("record")) if row is not None else None

    async def get_session_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        subject = str(user_id or "").strip()
        if not subject:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT record
                FROM {self.schema}.{TABLE_PLATFORM_SESSIONS}
                WHERE user_id = $1
                  AND state = 'active'
                  AND expires_at > now()
                ORDER BY last_seen_at DESC
                LIMIT 1
                """,
                subject,
            )
        return _json_object(dict(row).get("record")) if row is not None else None

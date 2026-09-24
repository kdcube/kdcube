# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube PostgreSQL targets for the shared authority migration protocol."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from connection_hub.delegated_credentials.authority_cutover import (
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_USERS,
    FAMILY_PLATFORM_SESSIONS,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationRecord,
    AuthorityMigrationSnapshot,
)
from kdcube_ai_app.auth.bundle.session_schema import TABLE_SESSIONS, TABLE_USERS
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.migration.redis_source import (
    KDCUBE_SESSION_MIGRATION_FAMILIES,
)
from kdcube_ai_app.auth.platform_session_store import PostgresPlatformSessionStore
from kdcube_ai_app.auth.platform_session_schema import TABLE_PLATFORM_SESSIONS


class KdcubePostgresSessionMigrationTarget:
    """Import and inventory KDCube-owned session authority families."""

    def __init__(
        self,
        *,
        bundle_sessions: PostgresBundleSessionStore,
        platform_sessions: PostgresPlatformSessionStore,
    ) -> None:
        self.bundle_sessions = bundle_sessions
        self.platform_sessions = platform_sessions
        self.tenant = bundle_sessions.tenant
        self.project = bundle_sessions.project
        if (platform_sessions.tenant, platform_sessions.project) != (
            self.tenant,
            self.project,
        ):
            raise ValueError("KDCube migration targets must share one scope")

    async def synchronize(self, source: AuthorityMigrationSnapshot) -> None:
        """Remove stale or changed inactive-target rows before exact import."""

        snapshot = source.validated()
        if (snapshot.tenant, snapshot.project) != (self.tenant, self.project):
            raise ValueError("KDCube migration target scope mismatch")
        source_by_key = {
            (record.record_type, record.identity): record
            for record in snapshot.records
            if record.record_type in {
                "bundle_user_authority",
                "bundle_session",
                "platform_session",
            }
        }
        current = await self.snapshot(captured_at_ms=snapshot.captured_at_ms)
        changed_users: set[str] = set()
        for target in current.records:
            if target.record_type != "bundle_user_authority":
                continue
            expected = source_by_key.get((target.record_type, target.identity))
            if expected is None or expected.evidence() != target.evidence():
                changed_users.add(str(target.payload.get("subject") or ""))

        async with (
            self.bundle_sessions._pool.acquire() as connection,
            connection.transaction(),
        ):
            for target in current.records:
                expected = source_by_key.get((target.record_type, target.identity))
                changed = expected is None or expected.evidence() != target.evidence()
                if target.record_type == "bundle_session":
                    subject = str(
                        dict(target.payload.get("record") or {}).get("sub") or ""
                    )
                    if changed or subject in changed_users:
                        await connection.execute(
                            f"DELETE FROM {self.bundle_sessions.schema}.{TABLE_SESSIONS} "
                            "WHERE session_id = $1",
                            target.identity,
                        )
            for target in current.records:
                if target.record_type != "bundle_user_authority":
                    continue
                subject = str(target.payload.get("subject") or "")
                if subject in changed_users:
                    await connection.execute(
                        f"DELETE FROM {self.bundle_sessions.schema}.{TABLE_USERS} "
                        "WHERE subject = $1",
                        subject,
                    )

        async with (
            self.platform_sessions._pool.acquire() as connection,
            connection.transaction(),
        ):
            for target in current.records:
                if target.record_type != "platform_session":
                    continue
                expected = source_by_key.get((target.record_type, target.identity))
                if expected is None or expected.evidence() != target.evidence():
                    await connection.execute(
                        f"DELETE FROM {self.platform_sessions.schema}."
                        f"{TABLE_PLATFORM_SESSIONS} WHERE session_id = $1",
                        target.identity,
                    )

    async def import_record(self, record: AuthorityMigrationRecord) -> bool:
        source = record.validated()
        if source.record_type == "bundle_user_authority":
            payload = dict(source.payload)
            return await self.bundle_sessions.import_user_authority(
                subject=str(payload.get("subject") or ""),
                record=(
                    dict(payload["record"])
                    if isinstance(payload.get("record"), dict)
                    else None
                ),
                session_version=int(payload.get("session_version") or 0),
            )
        if source.record_type == "bundle_session":
            return await self.bundle_sessions.import_session_authority(
                dict(source.payload.get("record") or {})
            )
        if source.record_type == "platform_session":
            if source.expires_at_ms is None:
                raise ValueError("platform session migration record requires expiry")
            return await self.platform_sessions.import_session_authority(
                authority_key=str(source.payload.get("authority_key") or ""),
                record=dict(source.payload.get("record") or {}),
                expires_at_ms=source.expires_at_ms,
            )
        raise ValueError(f"unsupported KDCube migration record: {source.record_type}")

    async def snapshot(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationSnapshot:
        captured = int(
            captured_at_ms
            if captured_at_ms is not None
            else time.time_ns() // 1_000_000
        )
        users, sessions = await self.bundle_sessions.migration_rows(
            captured_at_ms=captured
        )
        platform = await self.platform_sessions.migration_rows(
            captured_at_ms=captured
        )
        records: list[AuthorityMigrationRecord] = []
        for value in users:
            subject = str(value.get("subject") or "")
            state = str(value.get("state") or "")
            if state not in {"active", "deleted"}:
                raise RuntimeError("bundle_user_migration_state_invalid")
            record = value.get("record") if state == "active" else None
            if state == "active" and not isinstance(record, dict):
                record = dict(record or {})
            families = [FAMILY_BUNDLE_AUTHORITY_VERSION]
            if state == "active":
                families.append(FAMILY_BUNDLE_USERS)
            records.append(
                AuthorityMigrationRecord(
                    record_type="bundle_user_authority",
                    identity=hashlib.sha256(subject.encode("utf-8")).hexdigest(),
                    families=families,
                    payload={
                        "subject": subject,
                        "record": record,
                        "session_version": int(value.get("session_version") or 0),
                    },
                )
            )
        for value in sessions:
            session_id = str(value.get("session_id") or "")
            records.append(
                AuthorityMigrationRecord(
                    record_type="bundle_session",
                    identity=session_id,
                    families=(FAMILY_BUNDLE_SESSIONS,),
                    payload={"record": dict(value.get("record") or {})},
                    expires_at_ms=int(value.get("expires_at_ms") or 0),
                )
            )
        for value in platform:
            session_id = str(value.get("session_id") or "")
            records.append(
                AuthorityMigrationRecord(
                    record_type="platform_session",
                    identity=session_id,
                    families=(FAMILY_PLATFORM_SESSIONS,),
                    payload={
                        "authority_key": str(value.get("authority_key") or ""),
                        "record": dict(value.get("record") or {}),
                    },
                    expires_at_ms=int(value.get("expires_at_ms") or 0),
                )
            )
        return AuthorityMigrationSnapshot(
            tenant=self.tenant,
            project=self.project,
            records=records,
            declared_families=KDCUBE_SESSION_MIGRATION_FAMILIES,
            captured_at_ms=captured,
        ).validated()


__all__ = ["KdcubePostgresSessionMigrationTarget"]

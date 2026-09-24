# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Storage-neutral composition of runtime authority migration targets."""

from __future__ import annotations

import time
from typing import Protocol

from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationRecord,
    AuthorityMigrationSnapshot,
    combine_migration_snapshots,
)

KDCUBE_MIGRATION_RECORD_TYPES = frozenset(
    {
        "bundle_user_authority",
        "bundle_session",
        "platform_session",
    }
)


class ScopedAuthorityMigrationTarget(Protocol):
    """Asynchronous target port for one authority-family subset."""

    tenant: str
    project: str

    async def synchronize(self, source: AuthorityMigrationSnapshot) -> None: ...

    async def import_record(self, record: AuthorityMigrationRecord) -> bool: ...

    async def snapshot(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationSnapshot: ...


class RuntimeAuthorityMigrationTarget:
    """One storage-neutral target spanning Connection Hub and KDCube."""

    def __init__(
        self,
        *,
        connection_hub: ScopedAuthorityMigrationTarget,
        kdcube_sessions: ScopedAuthorityMigrationTarget,
    ) -> None:
        self.connection_hub = connection_hub
        self.kdcube_sessions = kdcube_sessions
        if (connection_hub.tenant, connection_hub.project) != (
            kdcube_sessions.tenant,
            kdcube_sessions.project,
        ):
            raise ValueError("runtime migration targets must share one scope")

    async def synchronize(self, source: AuthorityMigrationSnapshot) -> None:
        snapshot = source.validated()
        await self.connection_hub.synchronize(snapshot)
        await self.kdcube_sessions.synchronize(snapshot)

    async def import_record(self, record: AuthorityMigrationRecord) -> bool:
        if record.record_type in KDCUBE_MIGRATION_RECORD_TYPES:
            return await self.kdcube_sessions.import_record(record)
        return await self.connection_hub.import_record(record)

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
        return combine_migration_snapshots(
            (
                await self.connection_hub.snapshot(captured_at_ms=captured),
                await self.kdcube_sessions.snapshot(captured_at_ms=captured),
            ),
            captured_at_ms=captured,
        )


__all__ = [
    "KDCUBE_MIGRATION_RECORD_TYPES",
    "RuntimeAuthorityMigrationTarget",
    "ScopedAuthorityMigrationTarget",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Storage-neutral composition of runtime authority migration sources."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationInspection,
    combine_migration_inspections,
)


class ScopedAuthorityMigrationSource(Protocol):
    """Asynchronous inspection port for one authority-family subset."""

    async def inspect(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationInspection: ...


class RuntimeAuthorityMigrationSource:
    """One source inspection spanning Connection Hub and KDCube."""

    def __init__(
        self,
        *,
        connection_hub: ScopedAuthorityMigrationSource,
        kdcube_sessions: ScopedAuthorityMigrationSource,
    ) -> None:
        self.connection_hub = connection_hub
        self.kdcube_sessions = kdcube_sessions

    async def inspect(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationInspection:
        captured = int(
            captured_at_ms
            if captured_at_ms is not None
            else time.time_ns() // 1_000_000
        )
        connection_hub, kdcube_sessions = await asyncio.gather(
            self.connection_hub.inspect(captured_at_ms=captured),
            self.kdcube_sessions.inspect(captured_at_ms=captured),
        )
        return combine_migration_inspections(
            (connection_hub, kdcube_sessions),
            captured_at_ms=captured,
        )


__all__ = [
    "RuntimeAuthorityMigrationSource",
    "ScopedAuthorityMigrationSource",
]

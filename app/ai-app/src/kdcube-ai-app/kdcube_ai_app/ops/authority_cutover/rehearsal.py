# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Rollback-only rehearsal for one reviewed authority migration."""

from __future__ import annotations

from typing import Awaitable, Callable

from connection_hub.delegated_credentials.cards.resident_secrets.model import (
    ResidentSecretStore,
)
from connection_hub.delegated_credentials.migration.apply import (
    AuthorityMigrationSource,
    AuthorityMigrationTarget,
    rehearse_reviewed_migration,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationPreview,
    AuthorityMigrationSnapshot,
)
from kdcube_ai_app.ops.authority_cutover.transaction import TransactionBoundPool


class RehearsalResidentSecretStore:
    """Read real custody and stage rehearsal creates only in memory."""

    def __init__(self, delegate: ResidentSecretStore) -> None:
        self._delegate = delegate
        self._staged: dict[str, tuple[str, int]] = {}

    async def create(
        self,
        *,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        if secret_ref in self._staged:
            return False
        if await self._delegate.get(secret_ref=secret_ref) is not None:
            return False
        self._staged[secret_ref] = (value, int(expires_at))
        return True

    async def get(self, *, secret_ref: str) -> str | None:
        staged = self._staged.get(secret_ref)
        if staged is not None:
            return staged[0]
        return await self._delegate.get(secret_ref=secret_ref)

    async def delete(self, *, secret_ref: str) -> None:
        self._staged.pop(secret_ref, None)

    async def purge_expired(self, *, now: int, limit: int) -> int:
        expired = [
            secret_ref
            for secret_ref, (_value, expires_at) in self._staged.items()
            if expires_at <= int(now)
        ][: max(0, int(limit))]
        for secret_ref in expired:
            self._staged.pop(secret_ref, None)
        return len(expired)


RehearsalTargetFactory = Callable[
    [TransactionBoundPool, ResidentSecretStore],
    Awaitable[AuthorityMigrationTarget],
]


async def rehearse_migration_in_rollback(
    *,
    pg_pool: Any,
    resident_secret_store: ResidentSecretStore,
    target_factory: RehearsalTargetFactory,
    preview: AuthorityMigrationPreview,
    source: AuthorityMigrationSource,
    confirmed_preview_sha256: str,
) -> AuthorityMigrationSnapshot:
    """Run the real import path while retaining no target mutation."""

    async with pg_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            target = await target_factory(
                TransactionBoundPool(connection),
                RehearsalResidentSecretStore(resident_secret_store),
            )
            return await rehearse_reviewed_migration(
                preview=preview,
                source=source,
                target=target,
                confirmed_preview_sha256=confirmed_preview_sha256,
            )
        finally:
            await transaction.rollback()


__all__ = [
    "RehearsalResidentSecretStore",
    "RehearsalTargetFactory",
    "rehearse_migration_in_rollback",
]

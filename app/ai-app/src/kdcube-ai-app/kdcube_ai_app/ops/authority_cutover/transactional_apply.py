# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Atomic PostgreSQL apply with compensating resident-secret cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from connection_hub.delegated_credentials.authority_cutover import (
    AuthorityCutoverReceipt,
)
from connection_hub.delegated_credentials.cards.resident_secrets.model import (
    ResidentSecretStore,
)
from connection_hub.delegated_credentials.migration.apply import (
    AuthorityCutoverReceiptTarget,
    AuthorityMigrationSource,
    AuthorityMigrationTarget,
    apply_reviewed_migration,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationPreview,
)
from kdcube_ai_app.ops.authority_cutover.transaction import TransactionBoundPool


class AuthorityMigrationTransactionOutcomeUnknown(RuntimeError):
    """The database transaction outcome cannot be established safely."""


class AuthorityMigrationSecretCompensationFailed(RuntimeError):
    """A rolled-back migration left one or more inert secrets for cleanup."""


class CompensatingResidentSecretStore:
    """Track migration-created secrets and delete them after SQL rollback."""

    def __init__(self, delegate: ResidentSecretStore) -> None:
        self._delegate = delegate
        self._created: list[str] = []
        self._created_set: set[str] = set()

    def _track(self, secret_ref: str) -> None:
        if secret_ref not in self._created_set:
            self._created.append(secret_ref)
            self._created_set.add(secret_ref)

    async def create(
        self,
        *,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        try:
            created = await self._delegate.create(
                secret_ref=secret_ref,
                value=value,
                expires_at=expires_at,
            )
        except Exception:
            # A create exception has an unknown outcome. A successful read of
            # the exact candidate makes compensating deletion safe.
            try:
                stored = await self._delegate.get(secret_ref=secret_ref)
            except Exception:
                stored = None
            if stored == value:
                self._track(secret_ref)
            raise
        if created is True:
            self._track(secret_ref)
        return created

    async def get(self, *, secret_ref: str) -> str | None:
        return await self._delegate.get(secret_ref=secret_ref)

    async def delete(self, *, secret_ref: str) -> None:
        await self._delegate.delete(secret_ref=secret_ref)
        self._created_set.discard(secret_ref)

    async def purge_expired(self, *, now: int, limit: int) -> int:
        return await self._delegate.purge_expired(now=now, limit=limit)

    async def compensate(self) -> None:
        failures = 0
        for secret_ref in reversed(self._created):
            if secret_ref not in self._created_set:
                continue
            try:
                await self._delegate.delete(secret_ref=secret_ref)
            except Exception:
                failures += 1
            else:
                self._created_set.discard(secret_ref)
        if failures:
            raise AuthorityMigrationSecretCompensationFailed(
                f"authority_migration_secret_compensation_failed:{failures}"
            )
        self.commit()

    def commit(self) -> None:
        self._created.clear()
        self._created_set.clear()


@dataclass(frozen=True)
class TransactionalApplyTarget:
    target: AuthorityMigrationTarget
    receipts: AuthorityCutoverReceiptTarget


TransactionalApplyTargetFactory = Callable[
    [TransactionBoundPool, ResidentSecretStore],
    Awaitable[TransactionalApplyTarget],
]


async def apply_migration_in_transaction(
    *,
    pg_pool: Any,
    resident_secret_store: ResidentSecretStore,
    target_factory: TransactionalApplyTargetFactory,
    preview: AuthorityMigrationPreview,
    source: AuthorityMigrationSource,
    confirmed_preview_sha256: str,
    source_is_quiesced: bool,
) -> AuthorityCutoverReceipt:
    """Commit imports, reconciliation, and receipt in one SQL transaction."""

    custody = CompensatingResidentSecretStore(resident_secret_store)
    async with pg_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            components = await target_factory(
                TransactionBoundPool(connection),
                custody,
            )
            receipt = await apply_reviewed_migration(
                preview=preview,
                source=source,
                target=components.target,
                receipts=components.receipts,
                confirmed_preview_sha256=confirmed_preview_sha256,
                source_is_quiesced=source_is_quiesced,
            )
        except Exception:
            try:
                await transaction.rollback()
            except Exception as rollback_error:
                raise AuthorityMigrationTransactionOutcomeUnknown(
                    "authority_migration_transaction_rollback_outcome_unknown"
                ) from rollback_error
            await custody.compensate()
            raise

        try:
            await transaction.commit()
        except Exception as commit_error:
            # The receipt and metadata may have committed. Deleting custody in
            # this state could invalidate a successful cutover.
            raise AuthorityMigrationTransactionOutcomeUnknown(
                "authority_migration_transaction_commit_outcome_unknown"
            ) from commit_error
        custody.commit()
        return receipt


__all__ = [
    "AuthorityMigrationSecretCompensationFailed",
    "AuthorityMigrationTransactionOutcomeUnknown",
    "CompensatingResidentSecretStore",
    "TransactionalApplyTarget",
    "TransactionalApplyTargetFactory",
    "apply_migration_in_transaction",
]

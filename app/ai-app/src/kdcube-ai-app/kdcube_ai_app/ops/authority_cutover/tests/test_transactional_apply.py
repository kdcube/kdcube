from __future__ import annotations

import pytest

from connection_hub.delegated_credentials.migration.apply import (
    AuthorityMigrationImportFailed,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationRecord,
    AuthorityMigrationSnapshot,
    build_migration_preview,
    inspection_from_snapshot,
)
from kdcube_ai_app.ops.authority_cutover.transactional_apply import (
    AuthorityMigrationTransactionOutcomeUnknown,
    TransactionalApplyTarget,
    apply_migration_in_transaction,
)


class _Transaction:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.started = 0
        self.committed = 0
        self.rolled_back = 0

    async def start(self) -> None:
        self.started += 1

    async def commit(self) -> None:
        self.committed += 1
        if self.fail_commit:
            raise RuntimeError("commit-outcome-unknown")

    async def rollback(self) -> None:
        self.rolled_back += 1
        if self.fail_rollback:
            raise RuntimeError("rollback-outcome-unknown")


class _Connection:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.transaction_value = _Transaction(
            fail_commit=fail_commit,
            fail_rollback=fail_rollback,
        )

    def transaction(self) -> _Transaction:
        return self.transaction_value


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


class _ResidentSecrets:
    def __init__(self, *, fail_create_response: bool = False) -> None:
        self.fail_create_response = fail_create_response
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        if secret_ref in self.values:
            return False
        self.values[secret_ref] = value
        if self.fail_create_response:
            raise RuntimeError("create-response-lost")
        return True

    async def get(self, *, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)

    async def delete(self, *, secret_ref: str) -> None:
        self.deleted.append(secret_ref)
        self.values.pop(secret_ref, None)

    async def purge_expired(self, *, now: int, limit: int) -> int:
        return 0


class _Source:
    def __init__(self, snapshot: AuthorityMigrationSnapshot) -> None:
        self.snapshot = snapshot

    async def inspect(self, *, captured_at_ms=None):
        return inspection_from_snapshot(self.snapshot)


class _Target:
    def __init__(self, snapshot, secret_store, *, fail: bool) -> None:
        self.snapshot_value = snapshot
        self.secret_store = secret_store
        self.fail = fail

    async def import_record(self, record: AuthorityMigrationRecord) -> bool:
        assert await self.secret_store.create(
            secret_ref="migration-secret",
            value="value",
            expires_at=2_000_000_000,
        ) is True
        if self.fail:
            raise RuntimeError("import-interrupted")
        return True

    async def snapshot(self, *, captured_at_ms=None) -> AuthorityMigrationSnapshot:
        return self.snapshot_value


class _Receipts:
    def __init__(self) -> None:
        self.receipt = None

    async def read(self, generation_id: str):
        return self.receipt

    async def activate(self, receipt):
        self.receipt = receipt
        return receipt


def _snapshot() -> AuthorityMigrationSnapshot:
    return AuthorityMigrationSnapshot(
        tenant="demo-tenant",
        project="demo-project",
        records=(
            AuthorityMigrationRecord(
                record_type="oauth_client",
                identity="client-1",
                families=("oauth_clients",),
                payload={"record": {"client_id": "client-1"}},
            ),
        ),
        declared_families=("oauth_clients",),
        captured_at_ms=1_900_000_000_000,
    ).validated()


async def _run(
    *,
    fail_import: bool,
    fail_commit: bool = False,
    fail_rollback: bool = False,
    fail_create_response: bool = False,
):
    snapshot = _snapshot()
    preview = build_migration_preview(
        snapshot,
        generation_id="authority-transactional-apply",
        prerequisites={"reviewed": True},
    )
    connection = _Connection(
        fail_commit=fail_commit,
        fail_rollback=fail_rollback,
    )
    secrets = _ResidentSecrets(fail_create_response=fail_create_response)
    receipts = _Receipts()

    async def target_factory(pool, secret_store):
        async with pool.acquire() as borrowed:
            assert borrowed is connection
        return TransactionalApplyTarget(
            target=_Target(snapshot, secret_store, fail=fail_import),
            receipts=receipts,
        )

    operation = apply_migration_in_transaction(
        pg_pool=_Pool(connection),
        resident_secret_store=secrets,
        target_factory=target_factory,
        preview=preview,
        source=_Source(snapshot),
        confirmed_preview_sha256=preview.preview_sha256,
        source_is_quiesced=True,
    )
    return operation, connection, secrets, receipts


@pytest.mark.asyncio
async def test_apply_commits_import_reconciliation_and_receipt_together() -> None:
    operation, connection, secrets, receipts = await _run(fail_import=False)

    receipt = await operation

    assert receipt is receipts.receipt
    assert connection.transaction_value.started == 1
    assert connection.transaction_value.committed == 1
    assert connection.transaction_value.rolled_back == 0
    assert secrets.values == {"migration-secret": "value"}
    assert secrets.deleted == []


@pytest.mark.asyncio
async def test_apply_rolls_back_sql_and_compensates_secret_on_import_failure() -> None:
    operation, connection, secrets, receipts = await _run(fail_import=True)

    with pytest.raises(AuthorityMigrationImportFailed, match="import-interrupted"):
        await operation

    assert receipts.receipt is None
    assert connection.transaction_value.started == 1
    assert connection.transaction_value.committed == 0
    assert connection.transaction_value.rolled_back == 1
    assert secrets.values == {}
    assert secrets.deleted == ["migration-secret"]


@pytest.mark.asyncio
async def test_commit_outcome_unknown_keeps_secret_for_possible_committed_metadata(
) -> None:
    operation, connection, secrets, _receipts = await _run(
        fail_import=False,
        fail_commit=True,
    )

    with pytest.raises(
        AuthorityMigrationTransactionOutcomeUnknown,
        match="commit_outcome_unknown",
    ):
        await operation

    assert connection.transaction_value.rolled_back == 0
    assert secrets.values == {"migration-secret": "value"}
    assert secrets.deleted == []


@pytest.mark.asyncio
async def test_create_response_loss_is_probed_and_compensated_after_rollback() -> None:
    operation, connection, secrets, _receipts = await _run(
        fail_import=False,
        fail_create_response=True,
    )

    with pytest.raises(
        AuthorityMigrationImportFailed,
        match="create-response-lost",
    ):
        await operation

    assert connection.transaction_value.rolled_back == 1
    assert secrets.values == {}
    assert secrets.deleted == ["migration-secret"]


@pytest.mark.asyncio
async def test_rollback_outcome_unknown_keeps_secret_for_possible_metadata() -> None:
    operation, connection, secrets, _receipts = await _run(
        fail_import=True,
        fail_rollback=True,
    )

    with pytest.raises(
        AuthorityMigrationTransactionOutcomeUnknown,
        match="rollback_outcome_unknown",
    ):
        await operation

    assert connection.transaction_value.rolled_back == 1
    assert secrets.values == {"migration-secret": "value"}
    assert secrets.deleted == []

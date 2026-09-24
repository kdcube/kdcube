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
from kdcube_ai_app.ops.authority_cutover.rehearsal import (
    rehearse_migration_in_rollback,
)


class _Transaction:
    def __init__(self) -> None:
        self.started = 0
        self.rolled_back = 0

    async def start(self) -> None:
        self.started += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


class _Connection:
    def __init__(self) -> None:
        self.transactions: list[_Transaction] = []

    def transaction(self) -> _Transaction:
        transaction = _Transaction()
        self.transactions.append(transaction)
        return transaction


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
    def __init__(self) -> None:
        self.values = {"existing": "resident"}
        self.created: list[str] = []
        self.deleted: list[str] = []

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        self.created.append(secret_ref)
        self.values[secret_ref] = value
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

    async def synchronize(self, source: AuthorityMigrationSnapshot) -> None:
        self.snapshot_value = source

    async def import_record(self, record: AuthorityMigrationRecord) -> bool:
        assert await self.secret_store.create(
            secret_ref="rehearsed",
            value="value",
            expires_at=2_000_000_000,
        ) is True
        assert await self.secret_store.get(secret_ref="rehearsed") == "value"
        if self.fail:
            raise RuntimeError("rehearsal-conflict")
        return True

    async def snapshot(self, *, captured_at_ms=None) -> AuthorityMigrationSnapshot:
        return self.snapshot_value


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


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_rehearsal_always_rolls_back_and_never_writes_real_secrets(
    fail: bool,
) -> None:
    snapshot = _snapshot()
    preview = build_migration_preview(
        snapshot,
        generation_id="authority-rehearsal",
        prerequisites={"reviewed": True},
    )
    connection = _Connection()
    resident_secrets = _ResidentSecrets()

    async def target_factory(pool, secret_store):
        async with pool.acquire() as borrowed:
            assert borrowed is connection
        assert await secret_store.get(secret_ref="existing") == "resident"
        return _Target(snapshot, secret_store, fail=fail)

    operation = rehearse_migration_in_rollback(
        pg_pool=_Pool(connection),
        resident_secret_store=resident_secrets,
        target_factory=target_factory,
        preview=preview,
        source=_Source(snapshot),
        confirmed_preview_sha256=preview.preview_sha256,
    )
    if fail:
        with pytest.raises(AuthorityMigrationImportFailed):
            await operation
    else:
        assert await operation == snapshot

    assert len(connection.transactions) == 1
    assert connection.transactions[0].started == 1
    assert connection.transactions[0].rolled_back == 1
    assert resident_secrets.created == []
    assert resident_secrets.deleted == []
    assert resident_secrets.values == {"existing": "resident"}

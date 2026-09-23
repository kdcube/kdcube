from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.ops.authority_cutover.schema_contracts import (
    AUTHORITY_TARGET_TABLE_CONTRACTS,
)
from kdcube_ai_app.ops.authority_cutover.schema_preflight import (
    AuthorityTargetSchemaMismatch,
    require_authority_target_schema,
)


class _Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Pool:
    def __init__(self, rows) -> None:
        self.connection = SimpleNamespace(fetch=self._fetch)
        self.rows = rows

    async def _fetch(self, query, schema, table_names):
        assert "information_schema.columns" in query
        assert schema == "kdcube_test"
        assert set(table_names) == {
            contract.table_name
            for contract in AUTHORITY_TARGET_TABLE_CONTRACTS
        }
        return self.rows

    def acquire(self):
        return _Acquire(self.connection)


def _contract_rows():
    return [
        {"table_name": contract.table_name, "column_name": column_name}
        for contract in AUTHORITY_TARGET_TABLE_CONTRACTS
        for column_name in contract.required_columns
    ]


@pytest.mark.asyncio
async def test_preflight_accepts_all_migration_target_columns() -> None:
    report = await require_authority_target_schema(
        pool=_Pool(_contract_rows()),
        schema="kdcube_test",
    )

    assert len(report.verified_tables) == 12
    assert report.verified_tables == tuple(sorted(report.verified_tables))


@pytest.mark.asyncio
async def test_preflight_names_legacy_receipt_column_before_writer_stop() -> None:
    rows = [
        row
        for row in _contract_rows()
        if not (
            row["table_name"] == "connection_hub_authority_cutovers"
            and row["column_name"] == "generation_id"
        )
    ]
    rows.append(
        {
            "table_name": "connection_hub_authority_cutovers",
            "column_name": "migration_id",
        }
    )

    with pytest.raises(
        AuthorityTargetSchemaMismatch,
        match=(
            "connection_hub_authority_cutovers:"
            "missing_columns=generation_id"
        ),
    ):
        await require_authority_target_schema(
            pool=_Pool(rows),
            schema="kdcube_test",
        )

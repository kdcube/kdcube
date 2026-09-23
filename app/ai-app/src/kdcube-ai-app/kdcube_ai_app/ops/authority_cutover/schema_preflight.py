# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Live schema verification for an authority cutover target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from kdcube_ai_app.ops.authority_cutover.schema_contracts import (
    AUTHORITY_TARGET_TABLE_CONTRACTS,
)


class AuthorityTargetSchemaMismatch(RuntimeError):
    """The live target cannot satisfy the migration code's column contract."""


@dataclass(frozen=True)
class AuthorityTargetSchemaReport:
    schema: str
    verified_tables: tuple[str, ...]


def _schema_mismatches(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    actual: dict[str, set[str]] = {}
    for row in rows:
        table_name = str(row.get("table_name") or "")
        column_name = str(row.get("column_name") or "")
        if table_name and column_name:
            actual.setdefault(table_name, set()).add(column_name)

    mismatches: list[str] = []
    for contract in AUTHORITY_TARGET_TABLE_CONTRACTS:
        columns = actual.get(contract.table_name)
        if columns is None:
            mismatches.append(f"{contract.table_name}:missing_table")
            continue
        missing = sorted(contract.required_columns.difference(columns))
        if missing:
            mismatches.append(
                f"{contract.table_name}:missing_columns={','.join(missing)}"
            )
    return tuple(mismatches)


async def require_authority_target_schema(
    *,
    pool: Any,
    schema: str,
) -> AuthorityTargetSchemaReport:
    """Refuse a cutover target whose live columns cannot serve the migration."""

    table_names = [
        contract.table_name for contract in AUTHORITY_TARGET_TABLE_CONTRACTS
    ]
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = ANY($2::text[])
            ORDER BY table_name, ordinal_position
            """,
            schema,
            table_names,
        )
    mismatches = _schema_mismatches(dict(row) for row in rows)
    if mismatches:
        raise AuthorityTargetSchemaMismatch(
            "authority_target_schema_mismatch: " + "; ".join(mismatches)
        )
    return AuthorityTargetSchemaReport(
        schema=schema,
        verified_tables=tuple(sorted(table_names)),
    )


__all__ = [
    "AuthorityTargetSchemaMismatch",
    "AuthorityTargetSchemaReport",
    "require_authority_target_schema",
]

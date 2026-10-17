# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Insert imported user props without overwriting any (W890); the runtime is the only database writer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kdcube_ai_app.apps.chat.proc.rest.management.user_props_contracts import UserPropRow


class ManagementUserPropsUnavailable(RuntimeError):
    pass


# One statement: the data-modifying CTE inserts what is new; the outer query reads the table as it was before
# the statement (PostgreSQL's snapshot rule for data-modifying CTEs), so a row that was not inserted is compared
# with the value already stored. Nothing existing is changed and no value is returned.
_IMPORT_SQL = """
WITH input AS (
    SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[])
        AS t(user_id, bundle_id, key, subsystem, value_text)
),
inserted AS (
    INSERT INTO {schema}.user_bundle_props (user_id, bundle_id, key, subsystem, value_json)
    SELECT user_id, bundle_id, key, subsystem, value_text::jsonb FROM input
    ON CONFLICT (user_id, bundle_id, key) DO NOTHING
    RETURNING user_id, bundle_id, key
)
SELECT
    count(*) FILTER (WHERE n.key IS NOT NULL) AS inserted,
    count(*) FILTER (WHERE n.key IS NULL AND p.key IS NOT NULL
                       AND p.value_json = i.value_text::jsonb AND p.subsystem = i.subsystem) AS equal,
    count(*) FILTER (WHERE n.key IS NULL AND NOT (p.key IS NOT NULL
                       AND p.value_json = i.value_text::jsonb AND p.subsystem = i.subsystem)) AS different
FROM input i
LEFT JOIN inserted n USING (user_id, bundle_id, key)
LEFT JOIN {schema}.user_bundle_props p
    ON p.user_id = i.user_id AND p.bundle_id = i.bundle_id AND p.key = i.key
"""


class KDCubeUserPropsRuntime:
    def __init__(self, *, manager: Any | None = None) -> None:
        self._manager_override = manager

    def _manager(self) -> Any:
        if self._manager_override is not None:
            return self._manager_override
        from kdcube_ai_app.infra.props.manager import get_props_manager

        return get_props_manager()

    async def import_rows(self, rows: Sequence[UserPropRow]) -> Mapping[str, Any]:
        """Counts only: inserted, equal (already stored with the same value) and different (already stored
        with another value, left unchanged)."""
        manager = self._manager()
        columns = [[getattr(row, name) for row in rows]
                   for name in ("user_id", "bundle_id", "key", "subsystem", "value_json")]
        try:
            pool = await manager._pool()
            async with pool.acquire() as con:
                record = await con.fetchrow(_IMPORT_SQL.format(schema=manager._schema), *columns)
        except Exception as exc:
            raise ManagementUserPropsUnavailable("User props could not be imported") from exc
        counts = {name: int(record[name]) for name in ("inserted", "equal", "different")}
        if sum(counts.values()) != len(rows):
            raise ManagementUserPropsUnavailable("User props import counts do not add up")
        return {**counts, "received": len(rows), "state": "imported"}


__all__ = ["KDCubeUserPropsRuntime", "ManagementUserPropsUnavailable"]

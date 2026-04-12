# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from functools import lru_cache
from typing import Any

from psycopg2.extras import Json

from kdcube_ai_app.infra.relational.psql.psql_base import PostgreSqlDbMgr
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema


class UserPropsManager:
    """
    Persistent non-secret per-user bundle props.

    Scope:
    - tenant/project is implicit in the selected project schema
    - bundle_id is required
    - key is bundle-owned logical dotted path
    - value_json stores arbitrary JSON, except Python None which should use delete
    """

    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        dbmgr: PostgreSqlDbMgr | None = None,
    ) -> None:
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()
        self._schema = _project_schema(self._tenant, self._project)
        self._dbmgr = dbmgr or PostgreSqlDbMgr()

    def get_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
    ) -> Any | None:
        rows = self._dbmgr.execute_sql(
            f"""
            SELECT value_json
            FROM {self._schema}.user_bundle_props
            WHERE user_id = %s
              AND bundle_id = %s
              AND key = %s
            LIMIT 1
            """,
            data=(str(user_id), str(bundle_id), str(key)),
        ) or []
        if not rows:
            return None
        return rows[0].get("value_json")

    def list_user_props(
        self,
        *,
        user_id: str,
        bundle_id: str,
    ) -> dict[str, Any]:
        rows = self._dbmgr.execute_sql(
            f"""
            SELECT key, value_json
            FROM {self._schema}.user_bundle_props
            WHERE user_id = %s
              AND bundle_id = %s
            ORDER BY key ASC
            """,
            data=(str(user_id), str(bundle_id)),
        ) or []
        return {
            str(row.get("key")): row.get("value_json")
            for row in rows
            if row.get("key") is not None
        }

    def set_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
        value: Any,
    ) -> None:
        if value is None:
            raise ValueError("User prop value cannot be None. Use delete_user_prop(...) to clear it.")
        self._dbmgr.execute_sql(
            f"""
            INSERT INTO {self._schema}.user_bundle_props (
                user_id,
                bundle_id,
                key,
                value_json
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, bundle_id, key) DO UPDATE
            SET value_json = EXCLUDED.value_json,
                updated_at = now()
            """,
            data=(str(user_id), str(bundle_id), str(key), Json(value)),
        )

    def delete_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
    ) -> None:
        self._dbmgr.execute_sql(
            f"""
            DELETE FROM {self._schema}.user_bundle_props
            WHERE user_id = %s
              AND bundle_id = %s
              AND key = %s
            """,
            data=(str(user_id), str(bundle_id), str(key)),
        )


@lru_cache()
def get_props_manager() -> UserPropsManager:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    settings = get_settings()
    return UserPropsManager(
        tenant=settings.TENANT,
        project=settings.PROJECT,
    )

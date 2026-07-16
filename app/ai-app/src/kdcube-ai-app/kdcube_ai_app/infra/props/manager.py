# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema


PoolFactory = Callable[[], Any | Awaitable[Any]]


async def _default_pool() -> Any:
    # The processor and ingress share this asyncpg pool resolver. Import it
    # lazily so the infrastructure module does not create an SDK import cycle.
    from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool

    return await get_pg_pool()


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


class UserPropsManager:
    """
    Async persistent non-secret per-user bundle props over the shared pool.

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
        pg_pool: Any | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()
        self._schema = _project_schema(self._tenant, self._project)
        self._pg_pool = pg_pool
        self._pool_factory = pool_factory or _default_pool

    async def _pool(self) -> Any:
        if self._pg_pool is None:
            candidate = self._pool_factory()
            self._pg_pool = await candidate if inspect.isawaitable(candidate) else candidate
        if self._pg_pool is None:
            raise RuntimeError("UserPropsManager requires an asyncpg pool")
        return self._pg_pool

    async def get_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
    ) -> Any | None:
        pool = await self._pool()
        async with pool.acquire() as con:
            value = await con.fetchval(
                f"""
                SELECT value_json::text
                FROM {self._schema}.user_bundle_props
                WHERE user_id = $1
                  AND bundle_id = $2
                  AND key = $3
                LIMIT 1
                """,
                str(user_id),
                str(bundle_id),
                str(key),
            )
        return _decode_json_value(value)

    async def list_user_props(
        self,
        *,
        user_id: str,
        bundle_id: str,
    ) -> dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                f"""
                SELECT key, value_json::text AS value_json
                FROM {self._schema}.user_bundle_props
                WHERE user_id = $1
                  AND bundle_id = $2
                ORDER BY key ASC
                """,
                str(user_id),
                str(bundle_id),
            )
        return {
            str(row.get("key")): _decode_json_value(row.get("value_json"))
            for row in rows
            if row.get("key") is not None
        }

    async def set_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
        value: Any,
    ) -> None:
        if value is None:
            raise ValueError("User prop value cannot be None. Use delete_user_prop(...) to clear it.")
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                f"""
                INSERT INTO {self._schema}.user_bundle_props (
                    user_id,
                    bundle_id,
                    key,
                    value_json
                )
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (user_id, bundle_id, key) DO UPDATE
                SET value_json = EXCLUDED.value_json,
                    updated_at = now()
                """,
                str(user_id),
                str(bundle_id),
                str(key),
                json.dumps(value, ensure_ascii=False, sort_keys=True),
            )

    async def delete_user_prop(
        self,
        *,
        user_id: str,
        bundle_id: str,
        key: str,
    ) -> None:
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                f"""
                DELETE FROM {self._schema}.user_bundle_props
                WHERE user_id = $1
                  AND bundle_id = $2
                  AND key = $3
                """,
                str(user_id),
                str(bundle_id),
                str(key),
            )


@lru_cache()
def get_props_manager() -> UserPropsManager:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    settings = get_settings()
    return UserPropsManager(
        tenant=settings.TENANT,
        project=settings.PROJECT,
    )

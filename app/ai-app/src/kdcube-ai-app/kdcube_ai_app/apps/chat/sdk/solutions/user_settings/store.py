# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The generic user-settings core over ``user_bundle_props``.

One table carries every durable per-user choice, in the tenant/project schema:
PK ``(user_id, bundle_id, key)`` plus a ``subsystem`` column naming the owning
store. ``UserSettingsStore`` is the typed base any settings store builds on:
idempotent schema bootstrap, record get/put by the full address, and a shallow
merge-write helper. Concrete stores (e.g. the agent selection record in
``agent_selection.py``) own their record shape, key convention, defaults, and
richer merge/clamp semantics on top.

Conventions:

- ``bundle_id`` is the REAL app id for app-scoped settings;
  ``PLATFORM_WIDE_BUNDLE_ID`` (``'*'``) marks a record that governs the user
  across every app (the memory-preferences convention).
- ``subsystem`` is one stable name per store (``memory``, ``agents``, …).
- ``key`` is constant for a singleton record (``preferences``) or
  parameterized for a per-entity family (``agent_selection:<agent_id>``).
- Records are versioned via ``schema_version`` inside ``value_json``; defaults
  flow from config, never from storage (absent row = configured default).

Direction: the memory-preferences store predates this core and still carries
its own table access; it is the next store intended to move onto this base
(same table, same conventions — a drop-in reparenting).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.context.memory.store import (
    USER_BUNDLE_PROPS_TABLE,
    _safe_identifier,
    _schema_from_scope,
)

USER_SETTINGS_TABLE = USER_BUNDLE_PROPS_TABLE

# The bundle_id marker for a record that governs the user across every app.
PLATFORM_WIDE_BUNDLE_ID = "*"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_value(value: Any) -> Any:
    """value_json as a dict, whether the driver returned text or a mapping."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value


class UserSettingsStore:
    """Typed access to ``user_bundle_props`` — the base for settings stores.

    Expects an asyncpg-like pool. Request auth stays with the caller: the
    entrypoint passes the already authenticated user id; writes are always
    single-actor.
    """

    def __init__(
        self,
        *,
        pg_pool: Any | None = None,
        schema: str | None = None,
        tenant: str = "default",
        project: str = "default",
    ):
        self._pool = pg_pool
        self.schema = _safe_identifier(schema) if schema else _schema_from_scope(tenant, project)

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError(f"{type(self).__name__} requires pg_pool")
        return self._pool

    async def ensure_schema(self) -> None:
        """Idempotent bootstrap: any one store creates the shared table."""
        pool = self._require_pool()
        statements = [
            f"CREATE SCHEMA IF NOT EXISTS {self.schema}",
            f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.{USER_SETTINGS_TABLE} (
                user_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                subsystem TEXT NOT NULL DEFAULT 'bundle',
                PRIMARY KEY (user_id, bundle_id, key)
            )
            """,
            f"ALTER TABLE {self.schema}.{USER_SETTINGS_TABLE} ADD COLUMN IF NOT EXISTS subsystem TEXT NOT NULL DEFAULT 'bundle'",
            f"CREATE INDEX IF NOT EXISTS idx_user_bundle_props_subsystem ON {self.schema}.{USER_SETTINGS_TABLE} (user_id, subsystem, bundle_id, key, updated_at DESC)",
        ]
        async with pool.acquire() as con:
            for statement in statements:
                await con.execute(statement)

    async def get_record(
        self,
        *,
        user_id: str,
        bundle_id: str,
        subsystem: str,
        key: str,
    ) -> Optional[dict[str, Any]]:
        """``{value, created_at, updated_at}`` or None when no row exists."""
        pool = self._require_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                SELECT value_json, created_at, updated_at
                FROM {self.schema}.{USER_SETTINGS_TABLE}
                WHERE user_id=$1
                  AND bundle_id=$2
                  AND key=$3
                  AND COALESCE(subsystem, 'bundle')=$4
                LIMIT 1
                """,
                str(user_id or "").strip() or "anonymous",
                str(bundle_id or "").strip(),
                str(key or "").strip(),
                str(subsystem or "bundle").strip(),
            )
        if not row:
            return None
        data = dict(row)
        value = json_value(data.get("value_json"))
        return {
            "value": dict(value) if isinstance(value, Mapping) else {},
            "created_at": str(data.get("created_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
        }

    async def put_record(
        self,
        *,
        user_id: str,
        bundle_id: str,
        subsystem: str,
        key: str,
        value: Mapping[str, Any],
    ) -> None:
        """Upsert the whole record (concrete stores merge/clamp BEFORE this)."""
        pool = self._require_pool()
        async with pool.acquire() as con:
            await con.execute(
                f"""
                INSERT INTO {self.schema}.{USER_SETTINGS_TABLE}
                    (user_id, bundle_id, key, value_json, created_at, updated_at, subsystem)
                VALUES ($1, $2, $3, $4::jsonb, now(), now(), $5)
                ON CONFLICT (user_id, bundle_id, key)
                DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    subsystem = EXCLUDED.subsystem,
                    updated_at = now()
                """,
                str(user_id or "").strip() or "anonymous",
                str(bundle_id or "").strip(),
                str(key or "").strip(),
                json.dumps(dict(value), ensure_ascii=False, sort_keys=True),
                str(subsystem or "bundle").strip(),
            )

    async def merge_record(
        self,
        *,
        user_id: str,
        bundle_id: str,
        subsystem: str,
        key: str,
        patch: Mapping[str, Any],
        defaults: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Shallow merge-write: read → overlay the patch's top-level fields →
        upsert. Omitted fields keep their stored value (or the default), so
        partial writes never clobber sibling fields — this is also the
        concurrency model. Stores with structured fields implement their own
        deep merge and call ``put_record`` directly."""
        stored = await self.get_record(user_id=user_id, bundle_id=bundle_id, subsystem=subsystem, key=key)
        value: dict[str, Any] = dict(defaults or {})
        if stored:
            value.update(stored["value"])
        value.update({str(k): v for k, v in dict(patch or {}).items()})
        await self.put_record(user_id=user_id, bundle_id=bundle_id, subsystem=subsystem, key=key, value=value)
        return value


__all__ = [
    "PLATFORM_WIDE_BUNDLE_ID",
    "USER_SETTINGS_TABLE",
    "UserSettingsStore",
    "json_value",
    "utc_now_iso",
]

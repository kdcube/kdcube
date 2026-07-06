# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Per-user agent selection store over ``user_bundle_props``.

One row per (user, REAL bundle_id, agent): ``subsystem='agents'``,
``key='agent_selection:<agent_id>'``. The value is a deny-list record:

    {
      "schema_version": 1,
      "disabled": {
        "tools": {"<alias>": true | ["<tool_name>", ...]},
        "mcp": {"<server_id>": true},
        "named_services": {"<namespace>": true},
        "skills": ["<namespace>.<skill_id>", ...]
      },
      "updated_at": "<iso>"
    }

Absent row = full configured set (nothing disabled). Writes are merge-writes
of partial toggles, clamped against the live inventory catalog when one is
provided, so the selection can only ever narrow the configured set.
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
from kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory import clamp_selection

AGENT_SELECTION_SUBSYSTEM = "agents"
AGENT_SELECTION_KEY_PREFIX = "agent_selection:"

_DICT_CATEGORIES = ("tools", "mcp", "named_services")


def agent_selection_key(agent_id: str) -> str:
    return f"{AGENT_SELECTION_KEY_PREFIX}{str(agent_id or '').strip() or 'main'}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value


def merge_selection_patch(
    current: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge a partial toggle patch over the current ``disabled`` record.

    Dict categories (tools / mcp / named_services): per-key toggles —
    ``true`` or a non-empty name list sets/replaces the denial, ``false`` /
    ``null`` / empty list removes it; keys absent from the patch keep their
    current state. Skills accept either a list (replaces the whole denied set)
    or a ``{skill_id: bool}`` mapping (per-skill toggles).
    """
    out: dict[str, Any] = {}
    current = current or {}
    patch = patch or {}

    for category in _DICT_CATEGORIES:
        merged: dict[str, Any] = {}
        existing = current.get(category)
        if isinstance(existing, Mapping):
            for name, value in existing.items():
                name = str(name or "").strip()
                if name and value:
                    merged[name] = True if value is True else [str(v) for v in value]
        raw = patch.get(category)
        if isinstance(raw, Mapping):
            for name, value in raw.items():
                name = str(name or "").strip()
                if not name:
                    continue
                if value is True:
                    merged[name] = True
                elif isinstance(value, (list, tuple, set)):
                    names = [str(v or "").strip() for v in value if str(v or "").strip()]
                    if names:
                        merged[name] = names
                    else:
                        merged.pop(name, None)
                else:
                    # false / None / anything else: re-enable.
                    merged.pop(name, None)
        if merged:
            out[category] = merged

    skills: list[str] = []
    existing_skills = current.get("skills")
    if isinstance(existing_skills, (list, tuple)):
        skills = [str(s or "").strip() for s in existing_skills if str(s or "").strip()]
    raw_skills = patch.get("skills")
    if isinstance(raw_skills, Mapping):
        for skill_id, value in raw_skills.items():
            skill_id = str(skill_id or "").strip()
            if not skill_id:
                continue
            if value:
                if skill_id not in skills:
                    skills.append(skill_id)
            elif skill_id in skills:
                skills.remove(skill_id)
    elif isinstance(raw_skills, (list, tuple, set)):
        skills = [str(s or "").strip() for s in raw_skills if str(s or "").strip()]
    if skills:
        out["skills"] = skills
    return out


class UserAgentSelectionStore:
    """Postgres-backed per-user agent selection (deny-list) store.

    Rides the ``user_bundle_props`` table shared with other per-user bundle
    subsystems. Expects an asyncpg-like pool; request auth stays with the
    caller — the entrypoint passes the already authenticated user id.
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
            raise RuntimeError("UserAgentSelectionStore requires pg_pool")
        return self._pool

    async def ensure_schema(self) -> None:
        pool = self._require_pool()
        statements = [
            f"CREATE SCHEMA IF NOT EXISTS {self.schema}",
            f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.{USER_BUNDLE_PROPS_TABLE} (
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
            f"ALTER TABLE {self.schema}.{USER_BUNDLE_PROPS_TABLE} ADD COLUMN IF NOT EXISTS subsystem TEXT NOT NULL DEFAULT 'bundle'",
            f"CREATE INDEX IF NOT EXISTS idx_user_bundle_props_subsystem ON {self.schema}.{USER_BUNDLE_PROPS_TABLE} (user_id, subsystem, bundle_id, key, updated_at DESC)",
        ]
        async with pool.acquire() as con:
            for statement in statements:
                await con.execute(statement)

    async def get_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """The stored selection record; ``{}`` disabled when no row exists."""
        pool = self._require_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                SELECT value_json, created_at, updated_at
                FROM {self.schema}.{USER_BUNDLE_PROPS_TABLE}
                WHERE user_id=$1
                  AND bundle_id=$2
                  AND key=$3
                  AND COALESCE(subsystem, 'bundle')=$4
                LIMIT 1
                """,
                str(user_id or "").strip() or "anonymous",
                str(bundle_id or "").strip(),
                agent_selection_key(agent_id),
                AGENT_SELECTION_SUBSYSTEM,
            )
        if not row:
            now = _utc_now_iso()
            return {"schema_version": 1, "disabled": {}, "created_at": now, "updated_at": now}
        data = dict(row)
        value = _json(data.get("value_json"))
        disabled = value.get("disabled") if isinstance(value, Mapping) else {}
        return {
            "schema_version": 1,
            "disabled": dict(disabled) if isinstance(disabled, Mapping) else {},
            "created_at": str(data.get("created_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
        }

    async def set_selection(
        self,
        *,
        user_id: str,
        bundle_id: str,
        agent_id: str,
        patch: Mapping[str, Any] | None,
        catalog: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Merge-write a partial toggle patch (or replace the whole record).

        When ``catalog`` (the live inventory) is provided the merged result is
        clamped against it: anything outside the inventory is stripped, and
        system tool aliases are always stripped (locked on).
        """
        current: Mapping[str, Any] = {}
        if not replace:
            current = (await self.get_selection(
                user_id=user_id,
                bundle_id=bundle_id,
                agent_id=agent_id,
            )).get("disabled") or {}
        merged = merge_selection_patch(current, patch)
        if catalog is not None:
            merged = clamp_selection(merged, catalog)

        now = _utc_now_iso()
        value = {"schema_version": 1, "disabled": merged, "updated_at": now}
        pool = self._require_pool()
        async with pool.acquire() as con:
            await con.execute(
                f"""
                INSERT INTO {self.schema}.{USER_BUNDLE_PROPS_TABLE}
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
                agent_selection_key(agent_id),
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                AGENT_SELECTION_SUBSYSTEM,
            )
        return {"schema_version": 1, "disabled": merged, "updated_at": now}


__all__ = [
    "AGENT_SELECTION_KEY_PREFIX",
    "AGENT_SELECTION_SUBSYSTEM",
    "UserAgentSelectionStore",
    "agent_selection_key",
    "merge_selection_patch",
]

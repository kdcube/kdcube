from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Optional

LOGGER = logging.getLogger("kdcube.connection_hub.authenticators")

TABLE_AUTHENTICATORS = "connection_hub_request_authenticators"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _safe_identifier(value: str, *, fallback: str = "default") -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    if not raw:
        raw = fallback
    if raw[0].isdigit():
        raw = f"_{raw}"
    return raw


def schema_for_scope(*, tenant: str, project: str) -> str:
    tenant_part = _safe_identifier(tenant, fallback="default")
    project_part = _safe_identifier(project, fallback="default")
    schema = f"{tenant_part}_{project_part}"
    if not schema.startswith("kdcube_"):
        schema = f"kdcube_{schema}"
    return schema


def _schema_sql(schema: str) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.{TABLE_AUTHENTICATORS} (
    authenticator_id   TEXT PRIMARY KEY,
    tenant             TEXT NOT NULL,
    project            TEXT NOT NULL,
    bundle_id          TEXT NOT NULL DEFAULT 'connection-hub@1-0',
    provider           TEXT NOT NULL,
    authority_id       TEXT NOT NULL DEFAULT '',
    connection_id      TEXT NOT NULL DEFAULT '',
    label              TEXT NOT NULL DEFAULT '',
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    role_providing     BOOLEAN NOT NULL DEFAULT FALSE,
    subject_namespace  TEXT NOT NULL DEFAULT '',
    secret_ref         TEXT NOT NULL DEFAULT '',
    selector           JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    verifier           JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    properties         JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ
);

ALTER TABLE {schema}.{TABLE_AUTHENTICATORS}
    ADD COLUMN IF NOT EXISTS connection_id TEXT NOT NULL DEFAULT '';

ALTER TABLE {schema}.{TABLE_AUTHENTICATORS}
    ADD COLUMN IF NOT EXISTS authority_id TEXT NOT NULL DEFAULT '';

ALTER TABLE {schema}.{TABLE_AUTHENTICATORS}
    ADD COLUMN IF NOT EXISTS role_providing BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS connection_hub_authenticators_provider_idx
    ON {schema}.{TABLE_AUTHENTICATORS} (provider)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS connection_hub_authenticators_authority_idx
    ON {schema}.{TABLE_AUTHENTICATORS} (authority_id)
    WHERE deleted_at IS NULL AND authority_id <> '';

CREATE INDEX IF NOT EXISTS connection_hub_authenticators_connection_idx
    ON {schema}.{TABLE_AUTHENTICATORS} (provider, connection_id)
    WHERE deleted_at IS NULL AND connection_id <> '';

CREATE INDEX IF NOT EXISTS connection_hub_authenticators_enabled_idx
    ON {schema}.{TABLE_AUTHENTICATORS} (enabled)
    WHERE deleted_at IS NULL;
"""


class AuthenticatorStore:
    """Postgres-backed request-authenticator registration store.

    This is Connection Hub domain metadata: provider, row id, selector/verifier
    hints, and `secret_ref`. Secret values never live here. They stay in the
    platform/bundle secrets provider and are resolved by `get_secret("b:...")`.
    """

    def __init__(
        self,
        *,
        pg_pool: Any,
        tenant: str,
        project: str,
        bundle_id: str = "connection-hub@1-0",
    ) -> None:
        if pg_pool is None:
            raise RuntimeError("AuthenticatorStore requires pg_pool")
        self._pool = pg_pool
        self.tenant = _clean(tenant) or "default"
        self.project = _clean(project) or "default"
        self.bundle_id = _clean(bundle_id) or "connection-hub@1-0"
        self.schema = schema_for_scope(tenant=self.tenant, project=self.project)

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as con:
            await con.execute(_schema_sql(self.schema))
        LOGGER.info(
            "[connection-hub.authenticators] schema ensured tenant=%s project=%s schema=%s",
            self.tenant,
            self.project,
            self.schema,
        )

    def _row(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "authenticator_id": _clean(raw.get("authenticator_id")),
            "provider": _clean(raw.get("provider")).lower(),
            "authority_id": _clean(raw.get("authority_id")),
            "connection_id": _clean(raw.get("connection_id")),
            "label": _clean(raw.get("label")),
            "enabled": raw.get("enabled") is not False,
            "role_providing": bool(raw.get("role_providing")),
            "subject_namespace": _clean(raw.get("subject_namespace")),
            "secret_ref": _clean(raw.get("secret_ref")),
            "selector": _safe_mapping(raw.get("selector")),
            "verifier": _safe_mapping(raw.get("verifier")),
            "properties": _safe_mapping(raw.get("properties")),
            "source": "postgres",
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
        }

    async def list_rows(self, *, provider: str = "") -> list[dict[str, Any]]:
        where = ["deleted_at IS NULL"]
        args: list[Any] = []
        provider_filter = _clean(provider).lower()
        if provider_filter:
            args.append(provider_filter)
            where.append(f"provider = ${len(args)}")
        sql = f"""
            SELECT *
            FROM {self.schema}.{TABLE_AUTHENTICATORS}
            WHERE {" AND ".join(where)}
            ORDER BY provider ASC, authenticator_id ASC
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(sql, *args)
        return [self._row(dict(row)) for row in rows]

    async def upsert_row(
        self,
        *,
        authenticator_id: str,
        provider: str,
        authority_id: str = "",
        label: str = "",
        enabled: bool = True,
        connection_id: str = "",
        role_providing: bool = False,
        subject_namespace: str = "",
        secret_ref: str = "",
        selector: Optional[Mapping[str, Any]] = None,
        verifier: Optional[Mapping[str, Any]] = None,
        properties: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        auth_id = _clean(authenticator_id)
        provider_value = _clean(provider).lower()
        if not auth_id:
            raise ValueError("authenticator_id is required")
        if not provider_value:
            raise ValueError("provider is required")
        subject_ns = _clean(subject_namespace) or provider_value
        row = await self._upsert_row(
            authenticator_id=auth_id,
            provider=provider_value,
            authority_id=_clean(authority_id),
            connection_id=_clean(connection_id) or auth_id,
            label=_clean(label) or auth_id,
            enabled=bool(enabled),
            role_providing=bool(role_providing),
            subject_namespace=subject_ns,
            secret_ref=_clean(secret_ref),
            selector=_safe_mapping(selector),
            verifier=_safe_mapping(verifier),
            properties=_safe_mapping(properties),
        )
        return self._row(dict(row))

    async def _upsert_row(self, **values: Any) -> Mapping[str, Any]:
        async with self._pool.acquire() as con:
            return await con.fetchrow(
                f"""
                INSERT INTO {self.schema}.{TABLE_AUTHENTICATORS} (
                    authenticator_id, tenant, project, bundle_id, provider,
                    authority_id, connection_id, label, enabled, role_providing, subject_namespace, secret_ref,
                    selector, verifier, properties, created_at, updated_at, deleted_at
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11, $12,
                    $13::jsonb, $14::jsonb, $15::jsonb, now(), now(), NULL
                )
                ON CONFLICT (authenticator_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    authority_id = EXCLUDED.authority_id,
                    connection_id = EXCLUDED.connection_id,
                    label = EXCLUDED.label,
                    enabled = EXCLUDED.enabled,
                    role_providing = EXCLUDED.role_providing,
                    subject_namespace = EXCLUDED.subject_namespace,
                    secret_ref = EXCLUDED.secret_ref,
                    selector = EXCLUDED.selector,
                    verifier = EXCLUDED.verifier,
                    properties = EXCLUDED.properties,
                    updated_at = now(),
                    deleted_at = NULL
                RETURNING *
                """,
                values["authenticator_id"],
                self.tenant,
                self.project,
                self.bundle_id,
                values["provider"],
                values["authority_id"],
                values["connection_id"],
                values["label"],
                values["enabled"],
                values["role_providing"],
                values["subject_namespace"],
                values["secret_ref"],
                json.dumps(values["selector"], sort_keys=True),
                json.dumps(values["verifier"], sort_keys=True),
                json.dumps(values["properties"], sort_keys=True),
            )

    async def remove_row(self, *, authenticator_id: str) -> dict[str, Any]:
        auth_id = _clean(authenticator_id)
        if not auth_id:
            return {"ok": False, "error": "authenticator_id_required"}
        async with self._pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                UPDATE {self.schema}.{TABLE_AUTHENTICATORS}
                SET deleted_at = now(), updated_at = now()
                WHERE authenticator_id = $1 AND deleted_at IS NULL
                RETURNING *
                """,
                auth_id,
            )
        if not row:
            return {"ok": True, "removed": False}
        return {"ok": True, "removed": True, "authenticator": self._row(dict(row))}

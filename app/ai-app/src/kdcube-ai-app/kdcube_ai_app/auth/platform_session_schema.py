# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema

TABLE_PLATFORM_SESSIONS = "kdcube_platform_sessions"

def platform_session_schema(*, tenant: str, project: str) -> str:
    return project_schema(tenant or "default", project or "default-project")


def platform_session_schema_sql(schema: str) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.{TABLE_PLATFORM_SESSIONS} (
    session_id          TEXT PRIMARY KEY,
    authority_key       TEXT NOT NULL,
    tenant              TEXT NOT NULL,
    project             TEXT NOT NULL,
    user_type           TEXT NOT NULL,
    user_id             TEXT,
    fingerprint         TEXT,
    record              JSONB NOT NULL,
    revision            BIGINT NOT NULL DEFAULT 1
                        CHECK (revision >= 1),
    state               TEXT NOT NULL DEFAULT 'active'
                        CHECK (state IN ('active', 'expired', 'revoked')),
    created_at          TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS kdcube_platform_sessions_one_active_idx
    ON {schema}.{TABLE_PLATFORM_SESSIONS} (authority_key)
    WHERE state = 'active';

CREATE INDEX IF NOT EXISTS kdcube_platform_sessions_user_idx
    ON {schema}.{TABLE_PLATFORM_SESSIONS} (user_id, state, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS kdcube_platform_sessions_expiry_idx
    ON {schema}.{TABLE_PLATFORM_SESSIONS} (expires_at)
    WHERE state = 'active';
"""

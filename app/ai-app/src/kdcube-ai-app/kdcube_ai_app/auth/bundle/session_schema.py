# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema

TABLE_USERS = "kdcube_bundle_session_users"
TABLE_SESSIONS = "kdcube_bundle_sessions"

def bundle_session_schema(*, tenant: str, project: str) -> str:
    return project_schema(tenant or "default", project or "default-project")


def bundle_session_schema_sql(schema: str) -> str:
    """DDL for durable bundle users, revocation epochs, and sessions."""

    return f"""
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.{TABLE_USERS} (
    subject             TEXT PRIMARY KEY,
    tenant              TEXT NOT NULL,
    project             TEXT NOT NULL,
    record              JSONB NOT NULL,
    session_version     BIGINT NOT NULL DEFAULT 1
                        CHECK (session_version >= 1),
    revision            BIGINT NOT NULL DEFAULT 1
                        CHECK (revision >= 1),
    state               TEXT NOT NULL DEFAULT 'active'
                        CHECK (state IN ('active', 'deleted')),
    disabled            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kdcube_bundle_session_users_provider_idx
    ON {schema}.{TABLE_USERS} ((record->>'provider'), (record->>'provider_subject'))
    WHERE state = 'active';

CREATE TABLE IF NOT EXISTS {schema}.{TABLE_SESSIONS} (
    session_id          TEXT PRIMARY KEY,
    subject             TEXT NOT NULL
                        REFERENCES {schema}.{TABLE_USERS}(subject),
    token_sha256        CHAR(64) NOT NULL UNIQUE,
    record              JSONB NOT NULL,
    revision            BIGINT NOT NULL DEFAULT 1
                        CHECK (revision >= 1),
    state               TEXT NOT NULL DEFAULT 'active'
                        CHECK (state IN ('active', 'revoked', 'expired')),
    issued_at           TIMESTAMPTZ NOT NULL,
    idle_expires_at     TIMESTAMPTZ NOT NULL,
    hard_expires_at     TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kdcube_bundle_sessions_subject_idx
    ON {schema}.{TABLE_SESSIONS} (subject, state, idle_expires_at);

CREATE INDEX IF NOT EXISTS kdcube_bundle_sessions_live_idx
    ON {schema}.{TABLE_SESSIONS} (idle_expires_at)
    WHERE state = 'active';
"""

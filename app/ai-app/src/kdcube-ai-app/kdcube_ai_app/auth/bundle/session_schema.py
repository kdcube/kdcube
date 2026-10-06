# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema

TABLE_USERS = "kdcube_bundle_session_users"
TABLE_SESSIONS = "kdcube_bundle_sessions"
TABLE_ISSUANCES = "kdcube_bundle_session_issuances"

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

CREATE TABLE IF NOT EXISTS {schema}.{TABLE_ISSUANCES} (
    identity            CHAR(64) PRIMARY KEY,
    inputs_digest       CHAR(64) NOT NULL,
    session_id          TEXT NOT NULL UNIQUE,
    secret_ref          CHAR(32) NOT NULL UNIQUE,
    subject             TEXT NOT NULL
                        REFERENCES {schema}.{TABLE_USERS}(subject),
    expected_version    BIGINT NOT NULL CHECK (expected_version >= 1),
    expected_user_revision BIGINT CHECK (expected_user_revision >= 1),
    session_record      JSONB NOT NULL,
    user_record         JSONB,
    expires_at          TIMESTAMPTZ NOT NULL,
    state               TEXT NOT NULL DEFAULT 'reserved'
                        CHECK (state IN ('reserved', 'active')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at        TIMESTAMPTZ
);

ALTER TABLE {schema}.{TABLE_ISSUANCES}
    ADD COLUMN IF NOT EXISTS user_record JSONB;

-- An older pending reservation has no trustworthy historical revision.
-- Leave it NULL; first activation refuses rather than inventing a fence.
ALTER TABLE {schema}.{TABLE_ISSUANCES}
    ADD COLUMN IF NOT EXISTS expected_user_revision BIGINT;
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Explicit migration for service-owned, value-free custody metadata.

The deployment migrator supplies its schema and pool. Normal service
operations need USAGE plus SELECT/INSERT/UPDATE, not DDL or DELETE. This
migration is never run automatically by an HTTP request or SDK client.
"""
from __future__ import annotations

import re


class RuntimeMetadataError(RuntimeError):
    """Finite refusal; database and cloud exception text never crosses it."""


def metadata_tables(schema: str) -> tuple[str, str]:
    if type(schema) is not str or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None:
        raise RuntimeMetadataError("runtime_secret_metadata_configuration_invalid")
    return f'"{schema}".runtime_secret_records', f'"{schema}".runtime_secret_cleanup'


async def create_runtime_metadata_schema(pool, *, schema: str) -> None:
    records, cleanup = metadata_tables(schema)
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                await connection.execute(f"""
                    CREATE TABLE IF NOT EXISTS {records} (
                        namespace text NOT NULL,
                        secret_ref text NOT NULL CHECK (secret_ref ~ '^[0-9a-f]{{32}}$'),
                        incarnation text NOT NULL CHECK (incarnation ~ '^[0-9a-f]{{32}}$'),
                        request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{{64}}$'),
                        expires_at bigint NOT NULL CHECK (expires_at > 0),
                        creation_token text NOT NULL CHECK (creation_token ~ '^[0-9a-f]{{32}}$'),
                        secret_name text NOT NULL,
                        state text NOT NULL CHECK (state IN ('reserved', 'active', 'terminal')),
                        arn text,
                        version_id text,
                        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                        PRIMARY KEY (namespace, secret_ref),
                        UNIQUE (namespace, secret_ref, incarnation),
                        CHECK ((arn IS NULL) = (version_id IS NULL)),
                        CHECK (state != 'active' OR arn IS NOT NULL),
                        CHECK (version_id IS NULL OR version_id = creation_token)
                    )
                """)
                await connection.execute(f"""
                    CREATE OR REPLACE FUNCTION "{schema}".runtime_terminal_guard()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        IF OLD.state = 'terminal' AND NEW.state != 'terminal' THEN
                            RAISE EXCEPTION 'runtime_secret_terminal_immutable'
                                USING ERRCODE = '23514';
                        END IF;
                        RETURN NEW;
                    END;
                    $$
                """)
                await connection.execute(f"DROP TRIGGER IF EXISTS runtime_terminal_guard ON {records}")
                await connection.execute(
                    f"CREATE TRIGGER runtime_terminal_guard BEFORE UPDATE ON {records} "
                    f'FOR EACH ROW EXECUTE FUNCTION "{schema}".runtime_terminal_guard()',
                )
                await connection.execute(f"""
                    CREATE TABLE IF NOT EXISTS {cleanup} (
                        namespace text NOT NULL,
                        job_id text NOT NULL CHECK (job_id ~ '^[0-9a-f]{{64}}$'),
                        secret_ref text NOT NULL,
                        incarnation text NOT NULL,
                        arn text,
                        version_id text,
                        state text NOT NULL DEFAULT 'pending'
                            CHECK (state IN ('pending', 'claimed', 'delete_accepted', 'deleted')),
                        phase text NOT NULL CHECK (phase IN ('reconcile', 'delete', 'confirm')),
                        claim_token text,
                        claim_until timestamptz,
                        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                        PRIMARY KEY (namespace, job_id),
                        FOREIGN KEY (namespace, secret_ref, incarnation)
                            REFERENCES {records} (namespace, secret_ref, incarnation),
                        CHECK ((arn IS NULL) = (version_id IS NULL)),
                        CHECK ((phase = 'reconcile') = (arn IS NULL)),
                        CHECK (state != 'deleted' OR phase = 'confirm'),
                        CHECK ((state = 'claimed') =
                            (claim_token IS NOT NULL AND claim_until IS NOT NULL))
                    )
                """)
                await connection.execute(
                    f"CREATE INDEX IF NOT EXISTS runtime_secret_expiry ON {records} "
                    "(namespace, expires_at, secret_ref) WHERE state != 'terminal'",
                )
                await connection.execute(
                    f"CREATE INDEX IF NOT EXISTS runtime_secret_cleanup_pending ON {cleanup} "
                    "(namespace, updated_at, job_id) WHERE state != 'deleted'",
                )
    except RuntimeMetadataError:
        raise
    except Exception:
        raise RuntimeMetadataError("runtime_secret_metadata_unavailable") from None

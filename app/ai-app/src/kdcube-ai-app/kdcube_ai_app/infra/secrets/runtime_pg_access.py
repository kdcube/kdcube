# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Read-only access checks for the dedicated custody service pool.

These checks inspect the actual connected principal and migrated relations.
They do not create roles, migrate schemas, or qualify a deployment's complete
database/IAM boundary. The service runs them on every pool acquisition.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError, metadata_tables

_LOG = logging.getLogger(__name__)


async def _warn_outside_relation(connection, *, schema: str) -> None:
    """Best-effort, bounded catalog diagnostic; never log driver text or rows."""
    try:
        row = await asyncio.wait_for(connection.fetchrow("""
            SELECT n.nspname AS schema_name, c.relname AS relation_name
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname != $1 AND n.nspname != 'information_schema'
                AND n.nspname NOT LIKE 'pg_%' AND c.relkind IN ('r','p','v','m','f')
                AND has_table_privilege(current_user, c.oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            ORDER BY n.nspname, c.relname LIMIT 1
        """, schema, timeout=0.25), timeout=0.25)
        if row is None:
            return
        names = (row["schema_name"], row["relation_name"])
        if any(type(name) is not str for name in names):
            return
        # PostgreSQL identifiers are at most 63 bytes; also bound unexpected
        # input and escape control characters to keep one value-free log line.
        schema_name, table_name = (json.dumps(name[:63], ensure_ascii=True) for name in names)
        _LOG.warning("runtime_secret_metadata_outside_relation schema=%s table=%s", schema_name, table_name)
    except Exception:
        # A failed diagnostic (including logging) cannot weaken access refusal
        # or replace its fixed public error. Cancellation still propagates.
        pass


async def check_runtime_metadata_access(connection, *, schema: str, service_role: str) -> None:
    records, cleanup = metadata_tables(schema)
    if (schema == "public" or schema.startswith("pg_")
            or type(service_role) is not str
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", service_role) is None):
        raise RuntimeMetadataError("runtime_secret_metadata_configuration_invalid")
    try:
        valid = await connection.fetchval("""
            WITH principal AS (
                SELECT * FROM pg_catalog.pg_roles WHERE rolname = current_user
            ), relations AS (
                SELECT c.* FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1 AND c.relname IN
                    ('runtime_secret_records', 'runtime_secret_cleanup')
            )
            SELECT current_user = $2 AND session_user = $2
                AND EXISTS (SELECT 1 FROM principal WHERE rolcanlogin
                    AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
                AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                    JOIN principal p ON p.oid = m.member)
                AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
                AND has_schema_privilege(current_user, $1, 'USAGE')
                AND NOT has_schema_privilege(current_user, $1, 'CREATE')
                AND EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname = $1
                    AND NOT pg_has_role(current_user, n.nspowner, 'MEMBER'))
                AND (SELECT count(*) FROM relations) = 2
                AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.relkind != 'r'
                    OR r.relpersistence != 'p'
                    OR pg_has_role(current_user, r.relowner, 'MEMBER')
                    OR NOT has_table_privilege(current_user, r.oid, 'SELECT')
                    OR NOT has_table_privilege(current_user, r.oid, 'INSERT')
                    OR NOT has_table_privilege(current_user, r.oid, 'UPDATE')
                    OR has_table_privilege(current_user, r.oid, 'DELETE,TRUNCATE,REFERENCES,TRIGGER'))
                AND EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t
                    JOIN relations r ON r.oid = t.tgrelid
                    JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
                    WHERE r.relname = 'runtime_secret_records'
                        AND t.tgname = 'runtime_terminal_guard' AND t.tgenabled = 'O'
                        AND NOT t.tgisinternal
                        AND NOT pg_has_role(current_user, p.proowner, 'MEMBER'))
                AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname != $1 AND n.nspname != 'information_schema'
                        AND n.nspname NOT LIKE 'pg_%' AND c.relkind IN ('r','p','v','m','f')
                        AND has_table_privilege(current_user, c.oid,
                            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
        """, schema, service_role)
        if valid is not True:
            await _warn_outside_relation(connection, schema=schema)
            raise RuntimeMetadataError("runtime_secret_metadata_unavailable")
        # Preparing explicit columns detects stale/pre-ledger schema input
        # without reading a record or running DDL. This is not a hash-based
        # attestation of every constraint or the trigger function's body.
        await connection.execute(f"""
            SELECT namespace, secret_ref, incarnation, request_digest, expires_at,
                creation_token, secret_name, state, arn, version_id, attempt_state,
                created_at, updated_at FROM {records} LIMIT 0
        """)
        await connection.execute(f"""
            SELECT namespace, job_id, secret_ref, incarnation, arn, version_id,
                state, phase, claim_token, claim_until, retry_count, next_attempt_at,
                updated_at FROM {cleanup} LIMIT 0
        """)
    except RuntimeMetadataError:
        raise
    except Exception:
        raise RuntimeMetadataError("runtime_secret_metadata_unavailable") from None

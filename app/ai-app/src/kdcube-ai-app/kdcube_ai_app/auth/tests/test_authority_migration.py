from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
import uuid

import pytest

from connection_hub.delegated_credentials.authority_cutover import (
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_USERS,
    FAMILY_PLATFORM_SESSIONS,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationRecord,
    AuthorityMigrationSnapshot,
)
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.migration.postgres_target import (
    KdcubePostgresSessionMigrationTarget,
)
from kdcube_ai_app.auth.migration.redis_source import (
    KDCUBE_SESSION_MIGRATION_FAMILIES,
    KdcubeRedisSessionMigrationSource,
    KdcubeRedisSessionResetSource,
)
from kdcube_ai_app.auth.platform_session_store import PostgresPlatformSessionStore


TENANT = "demo-tenant"
PROJECT = "demo-project"


class _Redis:
    def __init__(self, records):
        self.records = dict(records)

    async def scan(self, *, cursor, match, count):
        return 0, [
            key.encode()
            for key in sorted(self.records)
            if fnmatch.fnmatch(key, match)
        ]

    async def eval(self, script, key_count, key):
        value, expiry = self.records[key]
        return [value.encode(), expiry if expiry is not None else -1]


def _source_records(*, now: int) -> dict[str, tuple[str, int | None]]:
    prefix = f"{TENANT}:{PROJECT}"
    bundle_expiry = now + 3600
    platform_expiry_ms = (now + 1800) * 1000
    bundle_record = {
        "schema": "kdcube.bundle-session.v1",
        "session_id": "bsn_existing",
        "sub": "user-1",
        "provider": "oidc",
        "provider_subject": "subject-1",
        "token_sha256": "a" * 64,
        "version": 4,
        "active": True,
        "metadata": {},
        "iat": now - 10,
        "exp": bundle_expiry,
        "max_exp": now + 7200,
        "last_seen": now - 5,
    }
    platform_record = {
        "session_id": "platform-session-1",
        "user_type": "registered",
        "user_id": "user-1",
        "fingerprint": "fingerprint-1",
        "roles": ["user"],
        "permissions": [],
        "request_context": {},
        "created_at": now - 20,
        "last_seen": now - 10,
    }
    reconstructable_anonymous = {
        "session_id": "anonymous-reconstructable",
        "user_type": "anonymous",
        "fingerprint": "anonymous-fingerprint-1",
        "roles": [],
        "permissions": [],
        "request_context": {"ip": "127.0.0.1"},
        "created_at": now - 20,
        "last_seen": now - 10,
    }
    stateful_anonymous = {
        **reconstructable_anonymous,
        "session_id": "anonymous-preserved",
        "fingerprint": "anonymous-fingerprint-2",
        "rate_limit_subject": "stable-rate-limit-subject",
    }
    return {
        f"{prefix}:kdcube:auth:bundle-session:user:user-1": (
            json.dumps(
                {
                    "sub": "user-1",
                    "roles": ["user"],
                    "permissions": [],
                    "metadata": {},
                    "disabled": False,
                    "created_at": now - 100,
                    "updated_at": now - 50,
                }
            ),
            None,
        ),
        f"{prefix}:kdcube:auth:bundle-session:user-version:user-1": ("4", None),
        f"{prefix}:kdcube:auth:bundle-session:user-version:deleted-user": ("7", None),
        f"{prefix}:kdcube:auth:bundle-session:session:bsn_existing": (
            json.dumps(bundle_record),
            bundle_expiry * 1000,
        ),
        f"{prefix}:kdcube:session:registered:user-1": (
            json.dumps(platform_record),
            platform_expiry_ms,
        ),
        f"{prefix}:kdcube:session:index:platform-session-1": (
            f"{prefix}:kdcube:session:registered:user-1",
            platform_expiry_ms,
        ),
        f"{prefix}:kdcube:session:anonymous:anonymous-fingerprint-1": (
            json.dumps(reconstructable_anonymous),
            platform_expiry_ms,
        ),
        f"{prefix}:kdcube:session:anonymous:anonymous-fingerprint-2": (
            json.dumps(stateful_anonymous),
            platform_expiry_ms,
        ),
    }


@pytest.mark.asyncio
async def test_redis_source_preserves_user_tombstones_and_ignores_derived_indexes() -> None:
    now = int(time.time())
    inspection = await KdcubeRedisSessionMigrationSource(
        _Redis(_source_records(now=now)),
        tenant=TENANT,
        project=PROJECT,
    ).inspect(captured_at_ms=now * 1000)
    snapshot = inspection.snapshot

    assert snapshot.counts == {
        FAMILY_BUNDLE_AUTHORITY_VERSION: 2,
        FAMILY_BUNDLE_SESSIONS: 1,
        FAMILY_BUNDLE_USERS: 1,
        FAMILY_PLATFORM_SESSIONS: 2,
    }
    assert [record.record_type for record in snapshot.records] == [
        "bundle_user_authority",
        "bundle_user_authority",
        "bundle_session",
        "platform_session",
        "platform_session",
    ]
    deleted = next(
        record
        for record in snapshot.records
        if record.payload.get("subject") == "deleted-user"
    )
    assert deleted.payload["record"] is None
    assert deleted.payload["session_version"] == 7
    assert inspection.source_summary["bundle_user_authority"] == {
        "active": 1,
        "revocation_tombstone": 1,
    }
    assert inspection.source_summary["platform_sessions"] == {
        "preserved_anonymous": 1,
        "preserved_authenticated": 1,
        "skipped_reconstructable_anonymous": 1,
    }


@pytest.mark.asyncio
async def test_reset_source_reports_sessions_without_preserving_them() -> None:
    now = int(time.time())
    inspection = await KdcubeRedisSessionResetSource(
        _Redis(_source_records(now=now)),
        tenant=TENANT,
        project=PROJECT,
    ).inspect(captured_at_ms=now * 1000)

    assert inspection.snapshot.records == ()
    assert inspection.snapshot.counts == {
        FAMILY_BUNDLE_AUTHORITY_VERSION: 0,
        FAMILY_BUNDLE_SESSIONS: 0,
        FAMILY_BUNDLE_USERS: 0,
        FAMILY_PLATFORM_SESSIONS: 0,
    }
    assert inspection.source_summary == {
        "reset": {
            "bundle_authority_versions": 2,
            "bundle_sessions": 1,
            "bundle_users": 1,
            "platform_sessions": 3,
        }
    }


def _postgres_snapshot(*, now: int) -> AuthorityMigrationSnapshot:
    user = {
        "sub": "user-1",
        "roles": ["user"],
        "permissions": [],
        "metadata": {},
        "disabled": False,
        "created_at": now - 100,
        "updated_at": now - 50,
    }
    bundle_session = {
        "schema": "kdcube.bundle-session.v1",
        "session_id": "bsn_existing",
        "sub": "user-1",
        "provider": "oidc",
        "provider_subject": "subject-1",
        "token_sha256": "a" * 64,
        "version": 4,
        "active": True,
        "metadata": {},
        "iat": now - 10,
        "exp": now + 3600,
        "max_exp": now + 7200,
        "last_seen": now - 5,
    }
    platform_session = {
        "session_id": "platform-session-1",
        "user_type": "registered",
        "user_id": "user-1",
        "fingerprint": "fingerprint-1",
        "roles": ["user"],
        "permissions": [],
        "request_context": {},
        "created_at": now - 20,
        "last_seen": now - 10,
    }
    return AuthorityMigrationSnapshot(
        tenant=TENANT,
        project=PROJECT,
        records=(
            AuthorityMigrationRecord(
                record_type="bundle_user_authority",
                identity=hashlib.sha256(b"user-1").hexdigest(),
                families=(FAMILY_BUNDLE_USERS, FAMILY_BUNDLE_AUTHORITY_VERSION),
                payload={
                    "subject": "user-1",
                    "record": user,
                    "session_version": 4,
                },
            ),
            AuthorityMigrationRecord(
                record_type="bundle_user_authority",
                identity=hashlib.sha256(b"deleted-user").hexdigest(),
                families=(FAMILY_BUNDLE_AUTHORITY_VERSION,),
                payload={
                    "subject": "deleted-user",
                    "record": None,
                    "session_version": 7,
                },
            ),
            AuthorityMigrationRecord(
                record_type="bundle_session",
                identity="bsn_existing",
                families=(FAMILY_BUNDLE_SESSIONS,),
                payload={"record": bundle_session},
                expires_at_ms=(now + 3600) * 1000,
            ),
            AuthorityMigrationRecord(
                record_type="platform_session",
                identity="platform-session-1",
                families=(FAMILY_PLATFORM_SESSIONS,),
                payload={
                    "authority_key": "registered:user-1",
                    "record": platform_session,
                },
                expires_at_ms=(now + 1800) * 1000,
            ),
        ),
        declared_families=KDCUBE_SESSION_MIGRATION_FAMILIES,
        captured_at_ms=now * 1000,
    ).validated()


@pytest.mark.asyncio
async def test_postgres_target_round_trips_exact_generation_and_is_idempotent() -> None:
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")

    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    tenant = f"authority-test-{uuid.uuid4().hex}"
    bundle = PostgresBundleSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=PROJECT,
    )
    platform = PostgresPlatformSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=PROJECT,
    )
    target = KdcubePostgresSessionMigrationTarget(
        bundle_sessions=bundle,
        platform_sessions=platform,
    )
    now = int(time.time())
    source = _postgres_snapshot(now=now)
    source = AuthorityMigrationSnapshot(
        tenant=tenant,
        project=PROJECT,
        records=source.records,
        declared_families=source.declared_families,
        captured_at_ms=source.captured_at_ms,
    ).validated()
    try:
        await bundle.ensure_schema()
        await platform.ensure_schema()
        first = [await target.import_record(record) for record in source.records]
        second = [await target.import_record(record) for record in source.records]
        destination = await target.snapshot(captured_at_ms=source.captured_at_ms)

        assert first == [True, True, True, True]
        assert second == [False, False, False, False]
        assert destination.counts == source.counts
        assert destination.generation == source.generation
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f"DROP SCHEMA IF EXISTS {bundle.schema} CASCADE")
        await pool.close()

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from connection_hub.delegated_credentials.admission_replay import (
    PostgresAdmissionReplayClaimStore,
)
from connection_hub.delegated_credentials.authority_config import (
    CONNECTION_HUB_AUTHORITY_FAMILIES,
)
from connection_hub.delegated_credentials.authority_cutover import (
    PostgresAuthorityCutoverStore,
)
from connection_hub.delegated_credentials.cards.credential_handles import (
    PostgresCardCredentialHandleStore,
)
from connection_hub.delegated_credentials.cards.handle_authority import (
    PostgresCardHandleMetadataStore,
)
from connection_hub.delegated_credentials.cards.identity import CARD_KIND_AGENT
from connection_hub.delegated_credentials.cards.model import CardAuthority
from connection_hub.delegated_credentials.cards.resident_secrets import (
    ResidentCardSecretService,
)
from connection_hub.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
    subject_hash_for,
)
from connection_hub.delegated_credentials.migration.apply import (
    apply_reviewed_migration,
)
from connection_hub.delegated_credentials.migration.card_source import (
    BundleStorageCardAuthorityLoader,
)
from connection_hub.delegated_credentials.migration.reset_source import (
    ConnectionHubRedisResetSource,
)
from connection_hub.delegated_credentials.migration.service import (
    create_migration_preview,
)
from connection_hub.delegated_credentials.migration.target import (
    ConnectionHubPostgresMigrationTarget,
)
from connection_hub.delegated_credentials.oauth.authority_store import (
    PostgresOAuthAuthorityStore,
)
from connection_hub.delegated_credentials.oauth.migration import (
    PostgresOAuthMigrationTarget,
)
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.bundle.sessions import (
    BundleSessionAuthority,
    BundleSessionInvalid,
)
from kdcube_ai_app.auth.migration.postgres_target import (
    KdcubePostgresSessionMigrationTarget,
)
from kdcube_ai_app.auth.migration.redis_source import (
    KDCUBE_SESSION_MIGRATION_FAMILIES,
    KdcubeRedisSessionResetSource,
)
from kdcube_ai_app.auth.migration.source import RuntimeAuthorityMigrationSource
from kdcube_ai_app.auth.migration.target import RuntimeAuthorityMigrationTarget
from kdcube_ai_app.auth.platform_session_store import PostgresPlatformSessionStore
from kdcube_ai_app.infra.secrets.ephemeral import KDCubeEphemeralSecretStore
from kdcube_ai_app.infra.secrets.manager import InMemorySecretsManager
from kdcube_ai_app.ops.authority_cutover.evidence import (
    reviewed_reset_prerequisites,
)
from kdcube_ai_app.ops.authority_cutover.rehearsal import (
    rehearse_migration_in_rollback,
)
from kdcube_ai_app.ops.authority_cutover.schema_preflight import (
    require_authority_target_schema,
)


class _UnavailableSource:
    async def inspect(self, *, captured_at_ms=None):
        raise AssertionError("an activated rerun must not read Redis")


@pytest.mark.asyncio
async def test_reset_preserves_resident_card_and_discards_reconstructable_state(
    tmp_path,
) -> None:
    postgres_dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("KDCUBE_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("real PostgreSQL and Redis test endpoints are required")

    import asyncpg
    from redis.asyncio import Redis

    tenant = f"ar-{uuid.uuid4().hex[:16]}"
    project = "integration"
    generation_id = f"generation-{uuid.uuid4().hex}"
    now = int(time.time())
    expires_at = now + 3600
    authority = CardAuthority(
        access_id="agent-card-integration",
        client_id="kdcube-agent:test:worker",
        grantor_subject="operator",
        delegate_subject="worker",
        source="agent",
        card_kind=CARD_KIND_AGENT,
        card_revision=1,
        created_at=now,
        expires_at=expires_at,
    )
    card_store = BundleStorageDelegatedCardStore(tmp_path)
    subject_hash = subject_hash_for(authority.grantor_subject)
    pointer = await card_store.write_revision(
        subject_hash=subject_hash,
        authority=authority,
        updated_at=datetime.now(timezone.utc),
    )
    await card_store.advance_current(
        subject_hash=subject_hash,
        pointer=pointer,
    )
    card_loader = BundleStorageCardAuthorityLoader(card_store)

    redis = Redis.from_url(redis_url, decode_responses=False)
    pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=2)
    prefix = f"{tenant}:{project}"
    card_key = (
        f"{prefix}:kdcube:delegated-access:card-handles:"
        f"{authority.access_id}"
    )
    redis_records = {
        card_key: json.dumps(
            {
                "access_id": authority.access_id,
                "access_token": "resident-worker-bearer",
                "session_id": "resident-session",
            }
        ),
        f"{prefix}:kdcube:oauth:client:dcr-test-client": json.dumps(
            {
                "client_id": "dcr-test-client",
                "redirect_uris": ["https://client.example/callback"],
                "token_endpoint_auth_method": "none",
                "application_type": "native",
                "metadata": {},
            }
        ),
        f"{prefix}:kdcube:oauth:refresh:resident-worker-refresh": json.dumps(
            {
                "client_id": "dcr-test-client",
                "sub": "operator",
                "registry_access_id": authority.access_id,
                "card_kind": CARD_KIND_AGENT,
            }
        ),
        (
            f"{prefix}:kdcube:oauth:agrant:"
            f"{hashlib.sha256(b'resident-worker-bearer').hexdigest()}"
        ): json.dumps(
            {
                "operations": ["problem-board"],
                "registry_access_id": authority.access_id,
            }
        ),
        f"{prefix}:kdcube:session:registered:user-1": "{}",
    }

    oauth = PostgresOAuthAuthorityStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    metadata = PostgresCardHandleMetadataStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    resident_secret_store = KDCubeEphemeralSecretStore(
        InMemorySecretsManager(),
        namespace="resident-card-credentials",
    )
    secrets = ResidentCardSecretService(
        metadata_store=metadata,
        secret_store=resident_secret_store,
    )
    handles = PostgresCardCredentialHandleStore(
        metadata_store=metadata,
        resident_secrets=secrets,
    )
    admission = PostgresAdmissionReplayClaimStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    bundle = PostgresBundleSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    platform = PostgresPlatformSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    receipts = PostgresAuthorityCutoverStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    source = RuntimeAuthorityMigrationSource(
        connection_hub=ConnectionHubRedisResetSource(
            redis,
            tenant=tenant,
            project=project,
            card_authorities=card_loader,
        ),
        kdcube_sessions=KdcubeRedisSessionResetSource(
            redis,
            tenant=tenant,
            project=project,
        ),
    )
    target = RuntimeAuthorityMigrationTarget(
        connection_hub=ConnectionHubPostgresMigrationTarget(
            oauth=PostgresOAuthMigrationTarget(oauth),
            card_handles=handles,
            card_authorities=card_loader,
            admission_replay=admission,
        ),
        kdcube_sessions=KdcubePostgresSessionMigrationTarget(
            bundle_sessions=bundle,
            platform_sessions=platform,
        ),
    )

    try:
        for key, value in redis_records.items():
            await redis.set(key, value, pxat=expires_at * 1000)
        legacy_sessions = BundleSessionAuthority(
            tenant=tenant,
            project=project,
            redis=redis,
            secret="authority-reset-test-secret",
        )
        await legacy_sessions.register_user(
            sub="user-1",
            roles=("kdcube:role:registered",),
            permissions=("chat",),
        )
        legacy_grant = await legacy_sessions.login(sub="user-1")

        source_keys = [
            key
            async for key in redis.scan_iter(match=f"{prefix}:*")
        ]
        redis_snapshot = {
            key: (await redis.dump(key), await redis.pttl(key))
            for key in source_keys
        }
        for store in (oauth, handles, admission, bundle, platform, receipts):
            await store.ensure_schema()
        schema_report = await require_authority_target_schema(
            pool=pool,
            schema=receipts.schema,
        )
        assert len(schema_report.verified_tables) == 12

        preview = await create_migration_preview(
            source=source,
            generation_id=generation_id,
            prerequisites=reviewed_reset_prerequisites(),
        )
        assert preview.source_counts == {
            "admission_replay_claims": 0,
            "bundle_authority_version": 0,
            "bundle_sessions": 0,
            "bundle_users": 0,
            "card_handle_metadata": 1,
            "oauth_access_bindings": 1,
            "oauth_clients": 1,
            "oauth_refresh_families": 1,
            "platform_sessions": 0,
            "resident_card_secrets": 1,
        }

        async def rehearsal_target_factory(
            rehearsal_pool,
            rehearsal_secret_store,
        ):
            rehearsal_oauth = PostgresOAuthAuthorityStore(
                pg_pool=rehearsal_pool,
                tenant=tenant,
                project=project,
            )
            rehearsal_metadata = PostgresCardHandleMetadataStore(
                pg_pool=rehearsal_pool,
                tenant=tenant,
                project=project,
            )
            rehearsal_handles = PostgresCardCredentialHandleStore(
                metadata_store=rehearsal_metadata,
                resident_secrets=ResidentCardSecretService(
                    metadata_store=rehearsal_metadata,
                    secret_store=rehearsal_secret_store,
                ),
            )
            return RuntimeAuthorityMigrationTarget(
                connection_hub=ConnectionHubPostgresMigrationTarget(
                    oauth=PostgresOAuthMigrationTarget(rehearsal_oauth),
                    card_handles=rehearsal_handles,
                    card_authorities=card_loader,
                    admission_replay=PostgresAdmissionReplayClaimStore(
                        pg_pool=rehearsal_pool,
                        tenant=tenant,
                        project=project,
                    ),
                ),
                kdcube_sessions=KdcubePostgresSessionMigrationTarget(
                    bundle_sessions=PostgresBundleSessionStore(
                        pg_pool=rehearsal_pool,
                        tenant=tenant,
                        project=project,
                    ),
                    platform_sessions=PostgresPlatformSessionStore(
                        pg_pool=rehearsal_pool,
                        tenant=tenant,
                        project=project,
                    ),
                ),
            )

        rehearsed = await rehearse_migration_in_rollback(
            pg_pool=pool,
            resident_secret_store=resident_secret_store,
            target_factory=rehearsal_target_factory,
            preview=preview,
            source=source,
            confirmed_preview_sha256=preview.preview_sha256,
        )
        assert rehearsed.generation == preview.source_generation
        assert rehearsed.counts == preview.source_counts
        empty_target = await target.snapshot(
            captured_at_ms=preview.created_at_ms
        )
        assert all(count == 0 for count in empty_target.counts.values())
        assert await receipts.read(generation_id) is None

        receipt = await apply_reviewed_migration(
            preview=preview,
            source=source,
            target=target,
            receipts=receipts,
            confirmed_preview_sha256=preview.preview_sha256,
            source_is_quiesced=True,
        )
        assert receipt.generation_id == generation_id
        assert (await handles.read(authority)).access_token == (
            "resident-worker-bearer"
        )
        assert (
            await oauth.get_client_record("dcr-test-client", ttl_seconds=1)
            is not None
        )
        assert (
            await oauth.get_refresh_token_state("resident-worker-refresh")
            is not None
        )
        assert (
            await oauth.get_access_grant_record("resident-worker-bearer")
            is not None
        )
        await receipts.require_activated(
            generation_id,
            required_families=(
                *CONNECTION_HUB_AUTHORITY_FAMILIES,
                *KDCUBE_SESSION_MIGRATION_FAMILIES,
            ),
        )

        await redis.delete(*source_keys)
        assert (await handles.read(authority)).access_token == (
            "resident-worker-bearer"
        )
        assert (
            await oauth.get_access_grant_record("resident-worker-bearer")
            is not None
        )
        for key, (value, ttl_ms) in redis_snapshot.items():
            assert value is not None
            await redis.restore(
                key,
                max(0, int(ttl_ms)),
                value,
                replace=True,
            )
        destination = await target.snapshot(
            captured_at_ms=preview.created_at_ms
        )
        assert destination.generation == receipt.target_generation

        durable_sessions = BundleSessionAuthority(
            tenant=tenant,
            project=project,
            secret="authority-reset-test-secret",
            authority_store=bundle,
        )
        with pytest.raises(
            BundleSessionInvalid,
            match="bundle session is not active",
        ):
            await durable_sessions.validate_token(legacy_grant.token)
        await durable_sessions.register_user(
            sub="user-1",
            roles=("kdcube:role:registered",),
            permissions=("chat",),
        )
        durable_grant = await durable_sessions.login(sub="user-1")
        assert (
            await durable_sessions.validate_token(durable_grant.token)
        ).session_id == durable_grant.session_id

        replayed = await apply_reviewed_migration(
            preview=preview,
            source=_UnavailableSource(),
            target=target,
            receipts=receipts,
            confirmed_preview_sha256=preview.preview_sha256,
            source_is_quiesced=True,
        )
        assert replayed == receipt
    finally:
        cleanup_keys = [
            key
            async for key in redis.scan_iter(match=f"{prefix}:*")
        ]
        if cleanup_keys:
            await redis.delete(*cleanup_keys)
        await redis.aclose()
        async with pool.acquire() as connection:
            await connection.execute(
                f"DROP SCHEMA IF EXISTS {bundle.schema} CASCADE"
            )
        await pool.close()

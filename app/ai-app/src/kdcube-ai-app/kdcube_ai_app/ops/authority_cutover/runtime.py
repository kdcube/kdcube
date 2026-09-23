# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-owned composition for the one-time authority cutover."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from typing import Any

import asyncpg

from connection_hub.delegated_credentials.admission_replay import (
    PostgresAdmissionReplayClaimStore,
)
from connection_hub.delegated_credentials.authority_cutover import (
    PostgresAuthorityCutoverStore,
)
from connection_hub.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)
from connection_hub.delegated_credentials.migration.card_source import (
    BundleStorageCardAuthorityLoader,
)
from connection_hub.delegated_credentials.migration.reset_source import (
    ConnectionHubRedisResetSource,
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
from kdcube_ai_app.apps.chat.sdk.config import resolve_asyncpg_ssl
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.credential_handles import (
    postgres_card_credential_handle_store,
    resident_card_secret_store,
)
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.migration.postgres_target import (
    KdcubePostgresSessionMigrationTarget,
)
from kdcube_ai_app.auth.migration.redis_source import (
    KdcubeRedisSessionResetSource,
)
from kdcube_ai_app.auth.migration.source import RuntimeAuthorityMigrationSource
from kdcube_ai_app.auth.migration.target import RuntimeAuthorityMigrationTarget
from kdcube_ai_app.auth.platform_session_store import PostgresPlatformSessionStore
from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir
from kdcube_ai_app.infra.redis.client import create_async_redis_client
from kdcube_ai_app.ops.authority_cutover.schema_preflight import (
    AuthorityTargetSchemaReport,
    require_authority_target_schema,
)
from kdcube_ai_app.ops.authority_cutover.rehearsal import (
    rehearse_migration_in_rollback,
)
from kdcube_ai_app.ops.authority_cutover.transaction import TransactionBoundPool
from kdcube_ai_app.ops.authority_cutover.transactional_apply import (
    TransactionalApplyTarget,
    apply_migration_in_transaction,
)


CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"


@dataclass
class ResetSourceRuntime:
    source: RuntimeAuthorityMigrationSource
    redis: Any

    async def close(self) -> None:
        await self.redis.aclose()


@dataclass
class ResetTargetRuntime:
    target: RuntimeAuthorityMigrationTarget
    receipts: PostgresAuthorityCutoverStore
    schema_report: AuthorityTargetSchemaReport
    resident_secret_store: Any
    pool: Any

    async def close(self) -> None:
        await self.pool.close()


def _scope(settings: Any) -> tuple[str, str]:
    tenant = str(getattr(settings, "TENANT", "") or "").strip() or "default"
    project = (
        str(getattr(settings, "PROJECT", "") or "").strip()
        or "default-project"
    )
    return tenant, project


async def _card_authorities(
    *,
    tenant: str,
    project: str,
) -> BundleStorageCardAuthorityLoader:
    root = await asyncio.to_thread(
        partial(
            bundle_storage_dir,
            bundle_id=CONNECTION_HUB_BUNDLE_ID,
            tenant=tenant,
            project=project,
            ensure=False,
        )
    )
    return BundleStorageCardAuthorityLoader(
        BundleStorageDelegatedCardStore(root)
    )


async def open_reset_source(settings: Any) -> ResetSourceRuntime:
    """Open only legacy read dependencies; PostgreSQL is not contacted."""

    tenant, project = _scope(settings)
    redis = create_async_redis_client(
        settings.REDIS_URL,
        decode_responses=False,
        client_name_kind="authority_cutover_source",
    )
    try:
        cards = await _card_authorities(
            tenant=tenant,
            project=project,
        )
        source = RuntimeAuthorityMigrationSource(
            connection_hub=ConnectionHubRedisResetSource(
                redis,
                tenant=tenant,
                project=project,
                card_authorities=cards,
            ),
            kdcube_sessions=KdcubeRedisSessionResetSource(
                redis,
                tenant=tenant,
                project=project,
            ),
        )
        return ResetSourceRuntime(source=source, redis=redis)
    except Exception:
        await redis.aclose()
        raise


async def _open_postgres_pool(settings: Any) -> Any:
    async def _init_connection(connection: Any) -> None:
        await connection.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await connection.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    return await asyncpg.create_pool(
        host=settings.PGHOST,
        port=settings.PGPORT,
        user=settings.PGUSER,
        password=settings.PGPASSWORD,
        database=settings.PGDATABASE,
        ssl=resolve_asyncpg_ssl(settings),
        init=_init_connection,
        min_size=0,
        max_size=2,
    )


@dataclass(frozen=True)
class _ResetTargetComponents:
    target: RuntimeAuthorityMigrationTarget
    receipts: PostgresAuthorityCutoverStore
    stores: tuple[Any, ...]


async def _compose_reset_target(
    settings: Any,
    *,
    pool: Any,
    resident_secret_store: Any,
) -> _ResetTargetComponents:
    tenant, project = _scope(settings)
    cards = await _card_authorities(
        tenant=tenant,
        project=project,
    )
    oauth_store = PostgresOAuthAuthorityStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    card_handles = postgres_card_credential_handle_store(
        pg_pool=pool,
        tenant=tenant,
        project=project,
        settings=settings,
        secret_store=resident_secret_store,
    )
    admission = PostgresAdmissionReplayClaimStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    bundle_sessions = PostgresBundleSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    platform_sessions = PostgresPlatformSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    receipts = PostgresAuthorityCutoverStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    return _ResetTargetComponents(
        target=RuntimeAuthorityMigrationTarget(
            connection_hub=ConnectionHubPostgresMigrationTarget(
                oauth=PostgresOAuthMigrationTarget(oauth_store),
                card_handles=card_handles,
                card_authorities=cards,
                admission_replay=admission,
            ),
            kdcube_sessions=KdcubePostgresSessionMigrationTarget(
                bundle_sessions=bundle_sessions,
                platform_sessions=platform_sessions,
            ),
        ),
        receipts=receipts,
        stores=(
            oauth_store,
            card_handles,
            admission,
            bundle_sessions,
            platform_sessions,
            receipts,
        ),
    )


async def open_reset_target(settings: Any) -> ResetTargetRuntime:
    pool = await _open_postgres_pool(settings)
    try:
        card_secret_store = resident_card_secret_store(settings)
        await card_secret_store.probe_writable()
        components = await _compose_reset_target(
            settings,
            pool=pool,
            resident_secret_store=card_secret_store,
        )
        for store in components.stores:
            await store.ensure_schema()
        schema_report = await require_authority_target_schema(
            pool=pool,
            schema=components.receipts.schema,
        )
        return ResetTargetRuntime(
            target=components.target,
            receipts=components.receipts,
            schema_report=schema_report,
            resident_secret_store=card_secret_store,
            pool=pool,
        )
    except Exception:
        await pool.close()
        raise


async def rehearse_reset_target(
    settings: Any,
    *,
    source: Any,
    target_runtime: ResetTargetRuntime,
    preview: Any,
    confirmed_preview_sha256: str,
) -> Any:
    async def _target_factory(
        pg_pool: TransactionBoundPool,
        resident_secret_store: Any,
    ) -> RuntimeAuthorityMigrationTarget:
        components = await _compose_reset_target(
            settings,
            pool=pg_pool,
            resident_secret_store=resident_secret_store,
        )
        return components.target

    return await rehearse_migration_in_rollback(
        pg_pool=target_runtime.pool,
        resident_secret_store=target_runtime.resident_secret_store,
        target_factory=_target_factory,
        preview=preview,
        source=source,
        confirmed_preview_sha256=confirmed_preview_sha256,
    )


async def apply_reset_target(
    settings: Any,
    *,
    source: Any,
    target_runtime: ResetTargetRuntime,
    preview: Any,
    confirmed_preview_sha256: str,
    source_is_quiesced: bool,
) -> Any:
    """Apply one reviewed reset with all PostgreSQL writes in one transaction."""

    async def _target_factory(
        pg_pool: TransactionBoundPool,
        resident_secret_store: Any,
    ) -> TransactionalApplyTarget:
        components = await _compose_reset_target(
            settings,
            pool=pg_pool,
            resident_secret_store=resident_secret_store,
        )
        return TransactionalApplyTarget(
            target=components.target,
            receipts=components.receipts,
        )

    return await apply_migration_in_transaction(
        pg_pool=target_runtime.pool,
        resident_secret_store=target_runtime.resident_secret_store,
        target_factory=_target_factory,
        preview=preview,
        source=source,
        confirmed_preview_sha256=confirmed_preview_sha256,
        source_is_quiesced=source_is_quiesced,
    )


__all__ = [
    "CONNECTION_HUB_BUNDLE_ID",
    "ResetSourceRuntime",
    "ResetTargetRuntime",
    "apply_reset_target",
    "open_reset_source",
    "open_reset_target",
    "rehearse_reset_target",
]

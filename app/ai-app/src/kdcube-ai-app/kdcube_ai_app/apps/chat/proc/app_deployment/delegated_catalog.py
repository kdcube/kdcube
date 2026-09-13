# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Reconcile the deployment-wide delegated catalog from descriptor authority."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Any, Mapping

from connection_hub.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from connection_hub.delegated_credentials.catalog.models import CatalogDocument
from connection_hub.delegated_credentials.catalog.resolver import (
    CatalogUnavailable,
    DelegatedCatalogResolver,
)
from connection_hub.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from connection_hub.delegated_credentials.catalog.source import connections_from_props
from connection_hub.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
)

from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import (
    apply_effective_props,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.storage import (
    resolve_app_storage_root,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.catalog.assembly import (
    CATALOG_DECLARATION_KEY,
    CONNECTION_HUB_BUNDLE_ID,
    CatalogAssemblyError,
    DelegatedCatalogAssembly,
    assemble_delegated_catalog,
    catalog_connection_differences,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.catalog.publisher import (
    CatalogPublicationResult,
    ensure_delegated_catalog,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    load_bundle_for_deprovision_async,
)
from kdcube_ai_app.infra.plugin.bundle_registry import (
    resolve_git_bundle_entry_async,
)
from kdcube_ai_app.infra.plugin.bundle_store import (
    BundleEntry,
    BundlesRegistry,
    bundle_entry_to_spec,
    get_bundle_props_from_authority,
)


@dataclass(frozen=True)
class AuthoritativeCatalogAssembly:
    assembly: DelegatedCatalogAssembly
    connection_hub_entry: BundleEntry
    storage_root: Any


async def _bundle_props(
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
) -> dict[str, dict[str, Any]]:
    bundle_ids = sorted(str(value).strip() for value in registry.bundles if str(value).strip())
    values = await asyncio.gather(
        *(
            get_bundle_props_from_authority(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
            )
            for bundle_id in bundle_ids
        )
    )
    return {
        bundle_id: dict(value or {})
        for bundle_id, value in zip(bundle_ids, values, strict=True)
    }


def declaring_bundle_ids(app_props: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        str(bundle_id)
        for bundle_id, props in app_props.items()
        if isinstance(props, Mapping) and CATALOG_DECLARATION_KEY in props
    }


async def authoritative_catalog_participant_ids(
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
) -> set[str]:
    app_props = await _bundle_props(
        registry=registry,
        tenant=tenant,
        project=project,
    )
    participants = declaring_bundle_ids(app_props)
    if CONNECTION_HUB_BUNDLE_ID in registry.bundles:
        participants.add(CONNECTION_HUB_BUNDLE_ID)
    return participants


async def _resolved_connection_hub_entry(
    registry: BundlesRegistry,
) -> BundleEntry | None:
    entry = registry.bundles.get(CONNECTION_HUB_BUNDLE_ID)
    if entry is None:
        return None
    resolved = await resolve_git_bundle_entry_async(
        entry.id,
        entry.model_dump(mode="python", exclude_none=True),
        source="delegated-catalog.assembly",
    )
    return BundleEntry.model_validate(resolved)


async def _connection_hub_effective_props(
    *,
    entry: BundleEntry,
    descriptor_props: Mapping[str, Any],
    tenant: str,
    project: str,
    pg_pool: Any,
    redis: Any,
) -> dict[str, Any]:
    bundle_spec = bundle_entry_to_spec(entry)
    agentic_spec = BundleSpec(
        id=entry.id,
        path=entry.path,
        module=entry.module,
        singleton=bool(entry.singleton),
    )
    workflow, _module = await load_bundle_for_deprovision_async(
        agentic_spec,
        bundle_spec,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
    )
    return apply_effective_props(workflow, descriptor_props)


async def assemble_authoritative_delegated_catalog(
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
    pg_pool: Any,
    redis: Any,
    connection_hub_effective_props: Mapping[str, Any] | None = None,
) -> AuthoritativeCatalogAssembly | None:
    app_props = await _bundle_props(
        registry=registry,
        tenant=tenant,
        project=project,
    )
    entry = await _resolved_connection_hub_entry(registry)
    if entry is None:
        owners = tuple(sorted(declaring_bundle_ids(app_props)))
        if owners:
            raise CatalogAssemblyError(
                "connection_hub_not_configured",
                "App-owned delegated catalog declarations require connection-hub@1-0",
                path="bundles",
                owners=owners,
            )
        return None

    effective_props = (
        copy.deepcopy(dict(connection_hub_effective_props))
        if isinstance(connection_hub_effective_props, Mapping)
        else await _connection_hub_effective_props(
            entry=entry,
            descriptor_props=app_props.get(CONNECTION_HUB_BUNDLE_ID, {}),
            tenant=tenant,
            project=project,
            pg_pool=pg_pool,
            redis=redis,
        )
    )
    assembly = assemble_delegated_catalog(
        base_connections=connections_from_props(effective_props),
        app_props=app_props,
    )
    storage_root = await resolve_app_storage_root(
        spec=bundle_entry_to_spec(entry),
        tenant=tenant,
        project=project,
        ensure=True,
    )
    if storage_root is None:
        raise CatalogAssemblyError(
            "connection_hub_storage_unavailable",
            "Connection Hub storage is unavailable for delegated catalog publication",
            owners=(CONNECTION_HUB_BUNDLE_ID,),
        )
    return AuthoritativeCatalogAssembly(
        assembly=assembly,
        connection_hub_entry=entry,
        storage_root=storage_root,
    )


async def connection_hub_props_with_authoritative_catalog(
    effective_props: Mapping[str, Any],
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
    pg_pool: Any,
    redis: Any,
) -> dict[str, Any]:
    context = await assemble_authoritative_delegated_catalog(
        registry=registry,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
        connection_hub_effective_props=effective_props,
    )
    if context is None:
        return copy.deepcopy(dict(effective_props))
    result = copy.deepcopy(dict(effective_props))
    result["connections"] = copy.deepcopy(context.assembly.connections)
    return result


async def publish_authoritative_delegated_catalog(
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
    pg_pool: Any,
    redis: Any,
    reason: str,
    logger: Any = None,
) -> CatalogPublicationResult | None:
    context = await assemble_authoritative_delegated_catalog(
        registry=registry,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
    )
    if context is None:
        return None

    store = BundleStorageDelegatedCatalogStore(context.storage_root)
    cache = DelegatedCatalogRuntimeCache(redis, tenant=tenant, project=project)

    async def reread() -> Mapping[str, Any]:
        fresh = await assemble_authoritative_delegated_catalog(
            registry=registry,
            tenant=tenant,
            project=project,
            pg_pool=pg_pool,
            redis=redis,
        )
        if fresh is None:
            raise CatalogAssemblyError(
                "connection_hub_not_configured",
                "Connection Hub was removed during delegated catalog publication",
                owners=(CONNECTION_HUB_BUNDLE_ID,),
            )
        return fresh.assembly.connections

    return await ensure_delegated_catalog(
        connections=context.assembly.connections,
        store=store,
        cache=cache,
        reread=reread,
        settings=DelegatedCacheSettings.from_connections(context.assembly.connections),
        reason=reason,
        logger=logger,
    )


async def check_authoritative_delegated_catalog(
    *,
    registry: BundlesRegistry,
    tenant: str,
    project: str,
    pg_pool: Any,
    redis: Any,
) -> dict[str, Any]:
    context = await assemble_authoritative_delegated_catalog(
        registry=registry,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
    )
    if context is None:
        return {
            "schema": "kdcube.delegated-catalog-check.v1",
            "status": "not_configured",
            "in_sync": True,
            "contributors": [],
            "differences": [],
        }

    expected = CatalogDocument.build(context.assembly.connections)
    resolver = DelegatedCatalogResolver(
        cache=DelegatedCatalogRuntimeCache(redis, tenant=tenant, project=project),
        store=BundleStorageDelegatedCatalogStore(context.storage_root),
        settings=DelegatedCacheSettings.from_connections(context.assembly.connections),
    )
    try:
        active = await resolver.resolve_active()
    except CatalogUnavailable as exc:
        return {
            "schema": "kdcube.delegated-catalog-check.v1",
            "status": "unavailable",
            "in_sync": False,
            "reason": exc.reason,
            "expected_content_hash": expected.content_hash,
            "contributors": list(context.assembly.contributors),
            "differences": [],
        }

    differences = catalog_connection_differences(
        expected=context.assembly.connections,
        actual=active.connections,
    )
    counts: dict[str, int] = {}
    for difference in differences:
        kind = str(difference.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "schema": "kdcube.delegated-catalog-check.v1",
        "status": "in_sync" if not differences else "drift",
        "in_sync": not differences,
        "expected_content_hash": expected.content_hash,
        "active_content_hash": active.content_hash,
        "active_catalog_version": active.version,
        "contributors": list(context.assembly.contributors),
        "difference_counts": counts,
        "differences": differences,
    }


__all__ = [
    "AuthoritativeCatalogAssembly",
    "assemble_authoritative_delegated_catalog",
    "authoritative_catalog_participant_ids",
    "check_authoritative_delegated_catalog",
    "connection_hub_props_with_authoritative_catalog",
    "declaring_bundle_ids",
    "publish_authoritative_delegated_catalog",
]

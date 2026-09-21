# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Project installed bundle manifests into authority discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from connection_hub.authority_discovery import RedisAuthorityDiscovery
from connection_hub.authority_registry import (
    AuthorityProviderSpec,
    authority_provider_spec_from_declaration,
)

from kdcube_ai_app.infra.plugin.bundle_loader import load_bundle_manifest
from kdcube_ai_app.infra.plugin.bundle_registry import (
    ADMIN_BUNDLE_ID,
    resolve_git_bundle_entry_async,
)
from kdcube_ai_app.infra.plugin.bundle_store import (
    BundleEntry,
    BundlesRegistry,
    bundle_entry_to_spec,
    load_registry_from_authority_readonly,
)


@dataclass(frozen=True)
class AuthorityDiscoverySweep:
    """Outcome of one manifest-owned discovery reconciliation."""

    reconciled: bool
    run_id: str = ""
    bundle_count: int = 0
    provider_count: int = 0
    changed_bundle_count: int = 0
    source_digest: str = ""
    providers: tuple[AuthorityProviderSpec, ...] = ()


async def load_authority_provider_source(
    registry: BundlesRegistry,
) -> dict[str, tuple[AuthorityProviderSpec, ...]]:
    """Read authority declarations from the registry's authoritative sources."""

    source: dict[str, tuple[AuthorityProviderSpec, ...]] = {}
    for application_id, entry in sorted((registry.bundles or {}).items()):
        if application_id == ADMIN_BUNDLE_ID:
            continue
        resolved = await resolve_git_bundle_entry_async(
            application_id,
            entry.model_dump(mode="python", exclude_none=True),
            source="authority.discovery.reconcile",
        )
        resolved_entry = BundleEntry.model_validate(resolved)
        spec = bundle_entry_to_spec(resolved_entry)
        manifest = load_bundle_manifest(spec, bundle_id=resolved_entry.id)
        bundle_id = manifest.bundle_id or resolved_entry.id or spec.path
        declarations = tuple(
            authority_provider_spec_from_declaration(
                declaration,
                bundle_id=bundle_id,
            )
            for declaration in manifest.authority_providers
        )
        if declarations:
            source[bundle_id] = declarations
    return source


async def reconcile_authority_discovery(
    *,
    registry: BundlesRegistry | None,
    tenant: str,
    project: str,
    redis: Any,
    force: bool = False,
    source_revision: int = 0,
    invalidate_on_failure: bool = False,
    discovery: RedisAuthorityDiscovery | None = None,
    logger: logging.Logger | None = None,
) -> AuthorityDiscoverySweep:
    """Publish one complete manifest generation without bundle instantiation."""

    log = logger or logging.getLogger(__name__)
    projection = discovery or RedisAuthorityDiscovery(
        redis,
        tenant=tenant,
        project=project,
    )
    try:
        async def _load_source() -> dict[str, tuple[AuthorityProviderSpec, ...]]:
            durable_registry = await load_registry_from_authority_readonly(
                tenant,
                project,
            )
            source_registry = durable_registry or registry or BundlesRegistry()
            return await load_authority_provider_source(source_registry)

        result = await projection.reconcile_from_source(
            _load_source,
            source_revision=source_revision,
            force=force,
        )
    except Exception:
        if invalidate_on_failure:
            await projection.invalidate_epoch()
        raise
    if result.changed:
        log.info(
            "Authority discovery reconciled: run=%s bundles=%s providers=%s changed_bundles=%s revision=%s",
            result.run_id,
            result.bundle_count,
            len(result.providers),
            result.changed_bundle_count,
            source_revision,
        )
    return AuthorityDiscoverySweep(
        reconciled=result.changed,
        run_id=result.run_id,
        bundle_count=result.bundle_count,
        provider_count=len(result.providers),
        changed_bundle_count=result.changed_bundle_count,
        source_digest=result.source_digest,
        providers=result.providers,
    )


async def list_authority_providers(
    *,
    tenant: str,
    project: str,
    redis: Any,
    logger: logging.Logger | None = None,
) -> list[AuthorityProviderSpec]:
    """Read the projection, recovering once from descriptor authority on miss."""

    discovery = RedisAuthorityDiscovery(redis, tenant=tenant, project=project)

    async def _read_through() -> tuple[AuthorityProviderSpec, ...]:
        sweep = await reconcile_authority_discovery(
            registry=None,
            tenant=tenant,
            project=project,
            redis=redis,
            force=True,
            discovery=discovery,
            logger=logger,
        )
        return sweep.providers

    return await discovery.list_providers(read_through=_read_through)


__all__ = [
    "AuthorityDiscoverySweep",
    "list_authority_providers",
    "load_authority_provider_source",
    "reconcile_authority_discovery",
]

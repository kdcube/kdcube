# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Idempotent publication of the delegated-service catalog.

``ensure_delegated_catalog`` is the only path that writes catalog history. It
runs under the shared bundle-storage critical section and commits one immutable
version plus the complete active document. Request paths may restore an expired
Redis projection from committed durable state, but never publish.

The app generation is ready only after both the durable and the serving
publication succeed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.hashing import (
    connections_content_hash,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
)
from kdcube_ai_app.infra.plugin.bundle_once import run_once_for_shared_bundle_storage

_LOGGER = logging.getLogger("connection_hub.delegated_catalog.publisher")

CATALOG_OPERATION = "delegated-catalog-publish"
SIGNATURE_FILENAME = "publication.signature"


class CatalogPublicationError(RuntimeError):
    """The catalog could not be published for this app generation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CatalogPublicationResult:
    version: str
    content_hash: str
    created: bool


async def ensure_delegated_catalog(
    *,
    connections: Mapping[str, Any],
    store: BundleStorageDelegatedCatalogStore,
    cache: DelegatedCatalogRuntimeCache,
    reason: str = "",
    settings: DelegatedCacheSettings | None = None,
    logger: Any = None,
) -> CatalogPublicationResult:
    """Register ``connections`` as the active catalog, creating a version if it changed.

    ``reason`` is audit metadata only; it does not affect hashing, comparison,
    or control flow. The critical section serializes concurrent publishers onto
    one immutable version.
    """
    residency = (settings or DelegatedCacheSettings()).catalog
    try:
        expected_hash = connections_content_hash(connections)
    except (TypeError, ValueError) as exc:
        raise CatalogPublicationError("connections_not_hashable") from exc

    outcome: dict[str, Any] = {}

    async def _publish() -> None:
        document = CatalogDocument.build(connections)
        active = await store.read_active()
        created = True
        if active is not None and active.content_hash == document.content_hash:
            # Unchanged content: keep the registered version identity and only
            # verify that its immutable document still exists.
            document = active
            created = False
            if not await store.version_exists(document.version):
                await store.write_version(document)
        else:
            await store.write_version(document)
            await store.publish_active(document)

        await cache.cache_version(document, ttl_seconds=residency.version_cache_seconds)
        if not await cache.publish_active(
            document, ttl_seconds=residency.active_cache_seconds
        ):
            # A newer generation already owns the serving entry; that catalog is
            # the one requests must use, so this generation is still ready.
            _LOGGER.info(
                "[connection-hub.delegated-catalog] serving entry holds a newer catalog "
                "than version=%s reason=%s",
                document.version,
                reason,
            )
        outcome["result"] = CatalogPublicationResult(
            version=document.version,
            content_hash=document.content_hash,
            created=created,
        )

    async def _ready() -> bool:
        active = await store.read_active()
        if active is None or active.content_hash != expected_hash:
            return False
        if not await store.version_exists(active.version):
            return False
        cached = await cache.read_active()
        return cached is not None and cached.version >= active.version

    await run_once_for_shared_bundle_storage(
        storage_root=store.root,
        operation=CATALOG_OPERATION,
        signature_path=store.root / SIGNATURE_FILENAME,
        signature=expected_hash,
        ready=_ready,
        action=_publish,
        logger=logger,
        owner_metadata={"kind": "delegated-catalog", "reason": reason},
        log_prefix="[connection-hub.delegated-catalog]",
    )

    result = outcome.get("result")
    if result is None:
        # Another worker published this generation; adopt the registered state.
        result = await _adopt_registered(store=store, cache=cache, residency=residency)
    _LOGGER.info(
        "[connection-hub.delegated-catalog] ensured version=%s created=%s reason=%s",
        result.version,
        result.created,
        reason,
    )
    return result


async def _adopt_registered(
    *,
    store: BundleStorageDelegatedCatalogStore,
    cache: DelegatedCatalogRuntimeCache,
    residency: Any,
) -> CatalogPublicationResult:
    active = await store.read_active()
    if active is None:
        raise CatalogPublicationError("active_catalog_not_registered")
    await cache.cache_version(active, ttl_seconds=residency.version_cache_seconds)
    await cache.publish_active(active, ttl_seconds=residency.active_cache_seconds)
    return CatalogPublicationResult(
        version=active.version, content_hash=active.content_hash, created=False
    )


__all__ = [
    "CATALOG_OPERATION",
    "CatalogPublicationError",
    "CatalogPublicationResult",
    "ensure_delegated_catalog",
]

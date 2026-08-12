# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis-first catalog resolution with durable read-through.

The active document is the ceiling every governed call intersects against. A
historical version is loaded only when list/open must explain drift from a
card's saved baseline.

Three outcomes are kept distinct:

    document        usable current or historical catalog
    None            confirmed absent historical version -> baseline_missing
    unavailable     cannot be validated -> structured 503 / drift unavailable

Read-through may repair the serving projection. It never publishes catalog
history and never derives a catalog from effective props.
"""

from __future__ import annotations

import logging
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    CatalogStorageError,
    DelegatedCatalogStore,
    validated_version_name,
)

_LOGGER = logging.getLogger("connection_hub.delegated_catalog.resolver")


class CatalogUnavailable(RuntimeError):
    """Current or historical catalog authority could not be established."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DelegatedCatalogResolver:
    """Resolve registered catalog documents for request-serving code."""

    def __init__(
        self,
        *,
        cache: DelegatedCatalogRuntimeCache,
        store: DelegatedCatalogStore,
        settings: DelegatedCacheSettings | None = None,
    ) -> None:
        self._cache = cache
        self._store = store
        self._settings = settings or DelegatedCacheSettings()

    async def resolve_active(self) -> CatalogDocument:
        """The complete active catalog. Raises when it cannot be validated."""
        try:
            cached = await self._cache.read_active()
        except Exception as exc:
            raise CatalogUnavailable("cache_unavailable") from exc
        if cached is not None:
            return cached

        try:
            document = await self._store.read_active()
        except Exception as exc:
            raise CatalogUnavailable("durable_active_unreadable") from exc
        if document is None:
            raise CatalogUnavailable("active_catalog_not_registered")

        try:
            await self._cache.restore_active(
                document, ttl_seconds=self._settings.catalog.active_cache_seconds
            )
        except Exception:
            # The document is already validated; failing to repair the
            # projection must not deny a request that can proceed.
            _LOGGER.warning(
                "[connection-hub.delegated-catalog] active restore failed version=%s",
                document.version,
                exc_info=True,
            )
        return document

    async def resolve_version(self, version: str) -> CatalogDocument | None:
        """A card's saved baseline. ``None`` means confirmed durable absence."""
        try:
            validated_version_name(version)
        except CatalogStorageError:
            # A card whose baseline cannot even be named has no comparable
            # history; that is baseline_missing, not an availability failure.
            return None

        try:
            cached = await self._cache.read_version(version)
        except Exception as exc:
            raise CatalogUnavailable("cache_unavailable") from exc
        if cached is not None:
            return cached

        try:
            document = await self._store.read_version(version)
        except Exception as exc:
            raise CatalogUnavailable("durable_version_unreadable") from exc
        if document is None:
            return None

        try:
            await self._cache.cache_version(
                document, ttl_seconds=self._settings.catalog.version_cache_seconds
            )
        except Exception:
            _LOGGER.warning(
                "[connection-hub.delegated-catalog] version restore failed version=%s",
                version,
                exc_info=True,
            )
        return document


__all__ = ["CatalogUnavailable", "DelegatedCatalogResolver"]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis serving projection for the delegated catalog.

Two independent lifetimes live here. ``catalog:active`` is the ceiling every
governed call intersects against; ``catalog:version:<version>`` holds immutable
historical documents used only to explain drift. Neither carries authority
beyond what the document itself says, and neither is extended by a read.

Installation of ``catalog:active`` is a compare-and-set on the sortable version
so a delayed restoration cannot replace a newer published catalog.
"""

from __future__ import annotations

import logging
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_io import (
    decode_cache_value,
    encode_cache_value,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
    CatalogDocumentError,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    validated_version_name,
)

_LOGGER = logging.getLogger("connection_hub.delegated_catalog.cache")

# Install the active document unless the cached one is already at least as new.
# ARGV[3] == "1" lets a publication reinstall an equal version with a fresh
# residency TTL; a restoration passes "0" so it never slides a live entry.
_INSTALL_ACTIVE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, decoded = pcall(cjson.decode, existing)
  if ok and type(decoded) == 'table' and decoded['version'] then
    if decoded['version'] > ARGV[2] then
      return 0
    end
    if decoded['version'] == ARGV[2] and ARGV[3] ~= '1' then
      return 0
    end
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[4])
return 1
"""


class DelegatedCatalogRuntimeCache:
    """Async Redis projection of the registered catalog documents."""

    def __init__(self, redis: Any, *, tenant: str, project: str) -> None:
        self._redis = redis
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()

    def _key(self, suffix: str) -> str:
        return f"{self._tenant}:{self._project}:kdcube:delegated-catalog:{suffix}"

    def active_key(self) -> str:
        return self._key("active")

    def version_key(self, version: str) -> str:
        return self._key(f"version:{validated_version_name(version)}")

    async def read_active(self) -> CatalogDocument | None:
        return await self._read(self.active_key(), label="active")

    async def read_version(self, version: str) -> CatalogDocument | None:
        return await self._read(self.version_key(version), label=f"version:{version}")

    async def publish_active(self, document: CatalogDocument, *, ttl_seconds: int) -> bool:
        """Install a newly registered active document, refreshing residency."""
        return await self._install_active(document, ttl_seconds=ttl_seconds, allow_equal=True)

    async def restore_active(self, document: CatalogDocument, *, ttl_seconds: int) -> bool:
        """Repopulate after a miss. Never displaces a newer or equal live entry."""
        return await self._install_active(document, ttl_seconds=ttl_seconds, allow_equal=False)

    async def cache_version(self, document: CatalogDocument, *, ttl_seconds: int) -> None:
        """Immutable historical documents need no compare: identity is the key."""
        document.verify()
        await self._redis.set(
            self.version_key(document.version),
            encode_cache_value(document.to_dict()),
            ex=max(1, int(ttl_seconds)),
        )

    async def _install_active(
        self, document: CatalogDocument, *, ttl_seconds: int, allow_equal: bool
    ) -> bool:
        document.verify()
        result = await self._redis.eval(
            _INSTALL_ACTIVE_LUA,
            1,
            self.active_key(),
            encode_cache_value(document.to_dict()),
            document.version,
            "1" if allow_equal else "0",
            str(max(1, int(ttl_seconds))),
        )
        installed = bool(int(result or 0))
        if not installed:
            _LOGGER.info(
                "[connection-hub.delegated-catalog] refused active install version=%s allow_equal=%s",
                document.version,
                allow_equal,
            )
        return installed

    async def _read(self, key: str, *, label: str) -> CatalogDocument | None:
        payload = decode_cache_value(await self._redis.get(key))
        if payload is None:
            return None
        try:
            return CatalogDocument.from_mapping(payload)
        except CatalogDocumentError as exc:
            # Unusable cache data, not authority: the caller read-throughs.
            _LOGGER.warning(
                "[connection-hub.delegated-catalog] discarding cached %s: %s", label, exc.reason
            )
            return None


__all__ = ["DelegatedCatalogRuntimeCache"]

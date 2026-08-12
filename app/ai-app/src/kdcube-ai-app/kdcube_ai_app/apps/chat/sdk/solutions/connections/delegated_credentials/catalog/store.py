# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable storage for immutable catalog versions and the active catalog.

``active.json`` is self-contained: one read yields the active version, its hash,
and the exact ``connections`` mapping. The immutable file under ``versions/``
preserves the same document for card baselines and provenance.

Absence and unreadability are different outcomes. A missing object returns
``None``; an unreadable or malformed one raises, so a caller can answer
``baseline_missing`` and ``unavailable`` distinctly.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import Protocol

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.durable_io import (
    DurableStorageError,
    path_is_file,
    read_json_or_none,
    write_json_atomic,
)

CATALOG_DIRNAME = "delegated-catalog"
CATALOG_LAYOUT_VERSION = "v1"
ACTIVE_FILENAME = "active.json"
VERSIONS_DIRNAME = "versions"

_VERSION_PATTERN = re.compile(
    r"^delegated_catalog_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}_[0-9a-f]{12}$"
)


class CatalogStorageError(DurableStorageError):
    """Durable catalog storage could not be read or written."""


def validated_version_name(version: str) -> str:
    """Reject anything that is not a well-formed version identity.

    The value reaches storage from a stored card field, so it is checked before
    it is interpolated into a path.
    """
    value = str(version or "").strip()
    if not _VERSION_PATTERN.match(value):
        raise CatalogStorageError("version_name_invalid")
    return value


class DelegatedCatalogStore(Protocol):
    async def read_active(self) -> CatalogDocument | None: ...

    async def read_version(self, version: str) -> CatalogDocument | None: ...

    async def write_version(self, document: CatalogDocument) -> None: ...

    async def publish_active(self, document: CatalogDocument) -> None: ...

    async def version_exists(self, version: str) -> bool: ...


class BundleStorageDelegatedCatalogStore:
    """``DelegatedCatalogStore`` over a shared bundle-storage root.

    Local filesystems and shared mounts such as EFS are both implementations of
    that root; the catalog model names neither.
    """

    def __init__(self, storage_root: str | os.PathLike[str]) -> None:
        self._root = pathlib.Path(storage_root) / CATALOG_DIRNAME / CATALOG_LAYOUT_VERSION

    @property
    def root(self) -> pathlib.Path:
        return self._root

    def active_path(self) -> pathlib.Path:
        return self._root / ACTIVE_FILENAME

    def version_path(self, version: str) -> pathlib.Path:
        return self._root / VERSIONS_DIRNAME / f"{validated_version_name(version)}.json"

    async def read_active(self) -> CatalogDocument | None:
        return await self._read_document(self.active_path())

    async def read_version(self, version: str) -> CatalogDocument | None:
        return await self._read_document(self.version_path(version))

    async def write_version(self, document: CatalogDocument) -> None:
        document.verify()
        await write_json_atomic(self.version_path(document.version), document.to_dict())

    async def publish_active(self, document: CatalogDocument) -> None:
        document.verify()
        await write_json_atomic(self.active_path(), document.to_dict())

    async def version_exists(self, version: str) -> bool:
        return await path_is_file(self.version_path(version))

    @staticmethod
    async def _read_document(path: pathlib.Path) -> CatalogDocument | None:
        payload = await read_json_or_none(path)
        if payload is None:
            return None
        return CatalogDocument.from_mapping(payload)


__all__ = [
    "ACTIVE_FILENAME",
    "BundleStorageDelegatedCatalogStore",
    "CATALOG_DIRNAME",
    "CATALOG_LAYOUT_VERSION",
    "CatalogStorageError",
    "DelegatedCatalogStore",
    "VERSIONS_DIRNAME",
    "validated_version_name",
]

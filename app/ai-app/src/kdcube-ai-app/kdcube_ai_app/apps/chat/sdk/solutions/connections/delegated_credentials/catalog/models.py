# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Immutable delegated-catalog version documents.

A catalog document is version metadata plus one exact copy of a Connection Hub
``connections`` mapping. Publication never renames, flattens, reorders, or
enriches that mapping.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.hashing import (
    connections_content_hash,
)

CATALOG_DOCUMENT_SCHEMA = "connection_hub.delegated_catalog.v1"
CATALOG_VERSION_PREFIX = "delegated_catalog"
_VERSION_HASH_CHARS = 12


class CatalogDocumentError(ValueError):
    """A catalog document could not be trusted as a registered catalog."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def utc_stamp(moment: datetime) -> str:
    """Sortable ``YYYY-MM-DD-HH-MM-SS-mmm`` UTC stamp used in resource names."""
    value = moment.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d-%H-%M-%S-") + f"{value.microsecond // 1000:03d}"


def utc_timestamp(moment: datetime) -> str:
    """ISO-8601 UTC timestamp with milliseconds."""
    value = moment.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def catalog_version_name(content_hash: str, *, created_at: datetime) -> str:
    return f"{CATALOG_VERSION_PREFIX}_{utc_stamp(created_at)}_{content_hash[:_VERSION_HASH_CHARS]}"


@dataclass(frozen=True)
class CatalogDocument:
    version: str
    content_hash: str
    created_at: str
    connections: Mapping[str, Any]

    @classmethod
    def build(
        cls,
        connections: Mapping[str, Any],
        *,
        created_at: datetime | None = None,
    ) -> "CatalogDocument":
        """Create a document for a mapping, deep-copied at capture time."""
        moment = created_at or datetime.now(timezone.utc)
        body = copy.deepcopy(dict(connections or {}))
        content_hash = connections_content_hash(body)
        return cls(
            version=catalog_version_name(content_hash, created_at=moment),
            content_hash=content_hash,
            created_at=utc_timestamp(moment),
            connections=body,
        )

    @classmethod
    def from_mapping(cls, value: Any) -> "CatalogDocument":
        """Parse a stored document. Raises ``CatalogDocumentError`` when the
        payload is not a usable registered catalog."""
        if not isinstance(value, Mapping):
            raise CatalogDocumentError("document_not_object")
        if str(value.get("schema") or "").strip() != CATALOG_DOCUMENT_SCHEMA:
            raise CatalogDocumentError("schema_mismatch")
        version = str(value.get("version") or "").strip()
        if not version:
            raise CatalogDocumentError("version_missing")
        content_hash = str(value.get("content_hash") or "").strip().lower()
        if not content_hash:
            raise CatalogDocumentError("content_hash_missing")
        connections = value.get("connections")
        if not isinstance(connections, Mapping):
            raise CatalogDocumentError("connections_invalid")
        document = cls(
            version=version,
            content_hash=content_hash,
            created_at=str(value.get("created_at") or "").strip(),
            connections=copy.deepcopy(dict(connections)),
        )
        document.verify()
        return document

    def verify(self) -> None:
        """Re-derive the hash of the embedded body and compare it."""
        try:
            actual = connections_content_hash(self.connections)
        except (TypeError, ValueError) as exc:
            raise CatalogDocumentError("connections_not_hashable") from exc
        if actual != self.content_hash:
            raise CatalogDocumentError("content_hash_mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_DOCUMENT_SCHEMA,
            "version": self.version,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "connections": copy.deepcopy(dict(self.connections)),
        }


__all__ = [
    "CATALOG_DOCUMENT_SCHEMA",
    "CATALOG_VERSION_PREFIX",
    "CatalogDocument",
    "CatalogDocumentError",
    "catalog_version_name",
    "utc_stamp",
    "utc_timestamp",
]

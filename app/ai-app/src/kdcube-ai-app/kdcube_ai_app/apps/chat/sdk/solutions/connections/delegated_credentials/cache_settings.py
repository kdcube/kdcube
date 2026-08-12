# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-owned cache-residency settings for delegated catalogs and cards.

These are residency lifetimes only. They decide how long a Redis projection or
a temporary marker survives before a read-through or a retry; they never grant,
extend, or revoke authority. Card authorization lifetime remains the card's own
``expires_at``, and durable retention is a separate administrative policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_ACTIVE_CACHE_SECONDS = 300
DEFAULT_VERSION_CACHE_SECONDS = 3600
DEFAULT_UPDATING_MARKER_SECONDS = 15
DEFAULT_REVOKED_TOMBSTONE_SECONDS = 60

_MIN_SECONDS = 1
_MAX_SECONDS = 24 * 3600


def _bounded_seconds(value: Any, default: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return default
    if seconds <= 0:
        return default
    return max(_MIN_SECONDS, min(seconds, _MAX_SECONDS))


def _node(parent: Mapping[str, Any] | Any, key: str) -> Mapping[str, Any]:
    if not isinstance(parent, Mapping):
        return {}
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class DelegatedCatalogCacheSettings:
    """Residency of the serving catalog projections.

    ``active`` is the ceiling every governed call intersects against; its expiry
    forces a validated read-through, which is what repairs a corrupted entry.
    ``version`` holds immutable historical documents used only to explain drift.
    Neither is extended by an ordinary cache hit.
    """

    active_cache_seconds: int = DEFAULT_ACTIVE_CACHE_SECONDS
    version_cache_seconds: int = DEFAULT_VERSION_CACHE_SECONDS

    @classmethod
    def from_mapping(cls, value: Any) -> "DelegatedCatalogCacheSettings":
        return cls(
            active_cache_seconds=_bounded_seconds(
                _as_mapping(value).get("active_cache_seconds"), DEFAULT_ACTIVE_CACHE_SECONDS
            ),
            version_cache_seconds=_bounded_seconds(
                _as_mapping(value).get("version_cache_seconds"), DEFAULT_VERSION_CACHE_SECONDS
            ),
        )


@dataclass(frozen=True)
class DelegatedCardCacheSettings:
    """Lifetimes of the two short fail-closed card markers.

    The updating marker must outlive a worst-case durable commit, or a delayed
    read-through can restore the superseded revision mid-commit; it must stay
    short, because a crashed mutation blocks that card until it expires.
    """

    updating_marker_seconds: int = DEFAULT_UPDATING_MARKER_SECONDS
    revoked_tombstone_seconds: int = DEFAULT_REVOKED_TOMBSTONE_SECONDS

    @classmethod
    def from_mapping(cls, value: Any) -> "DelegatedCardCacheSettings":
        return cls(
            updating_marker_seconds=_bounded_seconds(
                _as_mapping(value).get("updating_marker_seconds"),
                DEFAULT_UPDATING_MARKER_SECONDS,
            ),
            revoked_tombstone_seconds=_bounded_seconds(
                _as_mapping(value).get("revoked_tombstone_seconds"),
                DEFAULT_REVOKED_TOMBSTONE_SECONDS,
            ),
        )


@dataclass(frozen=True)
class DelegatedCacheSettings:
    catalog: DelegatedCatalogCacheSettings = DelegatedCatalogCacheSettings()
    cards: DelegatedCardCacheSettings = DelegatedCardCacheSettings()

    @classmethod
    def from_connections(cls, connections: Any) -> "DelegatedCacheSettings":
        """Read ``connections.delegated_credentials.{catalog,cards}``."""
        delegated = _node(connections, "delegated_credentials")
        return cls(
            catalog=DelegatedCatalogCacheSettings.from_mapping(_node(delegated, "catalog")),
            cards=DelegatedCardCacheSettings.from_mapping(_node(delegated, "cards")),
        )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "DEFAULT_ACTIVE_CACHE_SECONDS",
    "DEFAULT_REVOKED_TOMBSTONE_SECONDS",
    "DEFAULT_UPDATING_MARKER_SECONDS",
    "DEFAULT_VERSION_CACHE_SECONDS",
    "DelegatedCacheSettings",
    "DelegatedCardCacheSettings",
    "DelegatedCatalogCacheSettings",
]

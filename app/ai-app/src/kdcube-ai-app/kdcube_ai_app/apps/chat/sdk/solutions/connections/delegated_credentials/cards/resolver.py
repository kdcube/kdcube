# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis-first card resolution with durable read-through.

Outcomes are kept distinct:

    authority       usable committed card authority
    None            absent, revoked, or expired -> deny
    unavailable     cannot be established -> structured 503

Read-through restores only active, unexpired authority, and only the non-secret
projection. Credential handles live in their own bounded stores and are never
reconstructed from durable history.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    CardCacheEntry,
    CardCacheUnusable,
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CardAuthority,
    authority_is_usable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
    CardStorageError,
)

_LOGGER = logging.getLogger("connection_hub.delegated_cards.resolver")


class CardUnavailable(RuntimeError):
    """Card authority could not be established from cache or durable state."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DelegatedCardResolver:
    def __init__(
        self,
        *,
        cache: DelegatedCardRuntimeCache,
        store: BundleStorageDelegatedCardStore,
        settings: DelegatedCacheSettings | None = None,
    ) -> None:
        self._cache = cache
        self._store = store
        self._settings = settings or DelegatedCacheSettings()

    async def resolve(
        self, *, subject_hash: str, access_id: str, now: int | None = None
    ) -> CardAuthority | None:
        moment = int(now if now is not None else time.time())

        entry: CardCacheEntry | None = None
        try:
            entry = await self._cache.read(access_id)
        except CardCacheUnusable:
            # Unusable cache data is not authority: fall through to the durable
            # revision, which also repairs the projection.
            _LOGGER.warning(
                "[connection-hub.delegated-cards] unusable projection card=%s; reading durable",
                access_id,
            )
        except CardStorageError:
            raise
        except Exception as exc:
            raise CardUnavailable("cache_unavailable") from exc

        if entry is not None:
            if entry.is_updating:
                # One mutation owns this transition; the prior revision must not
                # be served after a newer one may already have committed.
                raise CardUnavailable("card_updating")
            if entry.is_revoked:
                return None
            authority = entry.authority
            if authority is not None:
                return authority if authority_is_usable(authority, moment) else None

        return await self._restore(subject_hash=subject_hash, access_id=access_id, moment=moment)

    async def _restore(
        self, *, subject_hash: str, access_id: str, moment: int
    ) -> CardAuthority | None:
        try:
            current = await self._store.read_current_authority(
                subject_hash=subject_hash, access_id=access_id
            )
        except Exception as exc:
            raise CardUnavailable("durable_card_unreadable") from exc
        if current is None:
            return None

        _, authority = current
        if not authority_is_usable(authority, moment):
            # Expired or revoked durable state denies and is never re-cached.
            return None

        try:
            await self._cache.restore_projection(
                authority, ttl_seconds=max(0, authority.expires_at - moment)
            )
        except Exception:
            _LOGGER.warning(
                "[connection-hub.delegated-cards] projection restore failed card=%s",
                access_id,
                exc_info=True,
            )
        return authority

    async def list_active(
        self, *, subject_hash: str, now: int | None = None
    ) -> list[CardAuthority]:
        """Active, unexpired cards for one grantor.

        The index only accelerates discovery: every member is resolved through
        the card cache/store, and a member that no longer resolves is pruned.
        Losing the index cannot hide a live durable card.
        """
        moment = int(now if now is not None else time.time())
        try:
            members = await self._cache.index_members(subject_hash=subject_hash, now=moment)
        except Exception as exc:
            raise CardUnavailable("cache_unavailable") from exc

        if not members:
            members = await self._rebuild_index(subject_hash=subject_hash, moment=moment)

        found: list[CardAuthority] = []
        for access_id in members:
            try:
                authority = await self.resolve(
                    subject_hash=subject_hash, access_id=access_id, now=moment
                )
            except CardUnavailable:
                raise
            if authority is None:
                await self._forget(subject_hash=subject_hash, access_id=access_id)
                continue
            found.append(authority)
        found.sort(key=lambda item: item.created_at, reverse=True)
        return found

    async def _rebuild_index(self, *, subject_hash: str, moment: int) -> list[str]:
        try:
            card_ids = await self._store.list_card_ids(subject_hash=subject_hash)
        except Exception as exc:
            raise CardUnavailable("durable_card_unreadable") from exc

        restored: list[str] = []
        for access_id in card_ids:
            try:
                current = await self._store.read_current_authority(
                    subject_hash=subject_hash, access_id=access_id
                )
            except Exception:
                _LOGGER.warning(
                    "[connection-hub.delegated-cards] index rebuild skipped card=%s",
                    access_id,
                    exc_info=True,
                )
                continue
            if current is None:
                continue
            _, authority = current
            if not authority_is_usable(authority, moment):
                continue
            await self._cache.index_add(
                subject_hash=subject_hash,
                access_id=access_id,
                expires_at=authority.expires_at,
            )
            restored.append(access_id)
        return restored

    async def _forget(self, *, subject_hash: str, access_id: str) -> None:
        try:
            await self._cache.index_remove(subject_hash=subject_hash, access_id=access_id)
        except Exception:
            _LOGGER.warning(
                "[connection-hub.delegated-cards] index prune failed card=%s",
                access_id,
                exc_info=True,
            )



__all__ = ["CardUnavailable", "DelegatedCardResolver"]

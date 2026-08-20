# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The card persistence port.

Composing durable card storage belongs to whoever owns the storage root, so
policy code receives this contract instead of building the pieces itself.

    load          committed authority and its live handles, or None when the
                  card is absent, expired, or revoked
    current_revision
                  the committed revision whatever its state, 0 with no durable
                  history — the expected_revision a write must pass
    persist       commits the next revision; a mismatched expected_revision
                  raises CardConflict
    forget        revokes the card and drops its handles
    list_active   active, unexpired cards for one grantor, newest first
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.handles import (
    DelegatedCardHandleStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CardAuthority,
    CardCredentialHandles,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.resolver import (
    DelegatedCardResolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.service import (
    CardConflict,
    DelegatedCardService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    subject_hash_for,
)

LoadedCard = tuple[CardAuthority, CardCredentialHandles]


class CardPersistence(Protocol):
    async def load(self, access_id: str, *, subject_hash: str) -> LoadedCard | None: ...

    async def current_revision(self, access_id: str, *, subject_hash: str) -> int: ...

    async def persist(
        self,
        authority: CardAuthority,
        handles: CardCredentialHandles,
        *,
        subject_hash: str,
        expected_revision: int,
    ) -> None: ...

    async def forget(self, authority: CardAuthority, *, subject_hash: str) -> None: ...

    async def list_active(
        self, *, subject_hash: str, now: int | None = None
    ) -> list[CardAuthority]: ...


class DurableCardPersistence:
    """Immutable revisions in bundle storage, projected into Redis."""

    def __init__(
        self,
        *,
        redis: Any,
        tenant: str,
        project: str,
        card_store: Any,
        settings: DelegatedCacheSettings | None = None,
    ) -> None:
        resolved = settings or DelegatedCacheSettings()
        cache = DelegatedCardRuntimeCache(redis, tenant=tenant, project=project)
        self._cards = DelegatedCardService(store=card_store, cache=cache, settings=resolved)
        self._resolver = DelegatedCardResolver(cache=cache, store=card_store, settings=resolved)
        self._handles = DelegatedCardHandleStore(redis, tenant=tenant, project=project)

    async def load(self, access_id: str, *, subject_hash: str) -> LoadedCard | None:
        authority = await self._resolver.resolve(
            subject_hash=subject_hash, access_id=access_id
        )
        if authority is None:
            return None
        # The projection is keyed by access_id alone, so ownership is confirmed
        # against the card itself rather than the path it was read from.
        if subject_hash_for(authority.grantor_subject) != str(subject_hash):
            return None
        return authority, await self._handles.read(access_id)

    async def current_revision(self, access_id: str, *, subject_hash: str) -> int:
        return await self._cards.current_revision(
            subject_hash=subject_hash, access_id=access_id
        )

    async def persist(
        self,
        authority: CardAuthority,
        handles: CardCredentialHandles,
        *,
        subject_hash: str,
        expected_revision: int,
    ) -> None:
        now = int(time.time())
        await self._cards.commit(
            authority,
            subject_hash=subject_hash,
            expected_revision=expected_revision,
            now=now,
        )
        await self._handles.write(handles, ttl_seconds=max(0, authority.expires_at - now))

    async def forget(self, authority: CardAuthority, *, subject_hash: str) -> None:
        await self._cards.revoke(
            subject_hash=subject_hash,
            access_id=authority.access_id,
            expected_revision=authority.card_revision,
        )
        await self._handles.remove(authority.access_id)

    async def list_active(
        self, *, subject_hash: str, now: int | None = None
    ) -> list[CardAuthority]:
        return await self._resolver.list_active(subject_hash=subject_hash, now=now)


__all__ = [
    "CardPersistence",
    "DurableCardPersistence",
    "LoadedCard",
]

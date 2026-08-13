# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Ordered card mutations over durable revisions and the Redis projection.

Every mutation runs inside a per-card shared critical section. Two mechanisms
serve two different populations:

    critical section   mutual exclusion between writers, held for as long as
                       the owning worker lives
    updating marker    fails readers closed while the transition is in flight,
                       so no request continues under superseded authority

Inside the section the protocol is: read and validate the current durable
revision, install the marker, write the immutable next revision, advance
current.json, then replace the marker with the committed projection.
"""

from __future__ import annotations

import logging
import pathlib
import time
import uuid
from datetime import datetime, timezone

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    CARD_STATE_REVOKED,
    CardAuthority,
    CardCurrentPointer,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)
from kdcube_ai_app.storage.observed_file_locks import (
    ObservedFileLockTimeout,
    observed_file_lock_async,
)

_LOGGER = logging.getLogger("connection_hub.delegated_cards.service")

CARD_LOCK_FILENAME = ".mutation.lock"
# Bounds how long a caller waits for another mutation on the same card. The
# lock itself is held for the owner's lifetime, so this only caps the wait.
CARD_LOCK_WAIT_SECONDS = 30.0


class CardConflict(RuntimeError):
    """Another mutation owns this card, or its live revision moved."""

    def __init__(self, reason: str, *, current_revision: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_revision = current_revision


class CardCommitFailed(RuntimeError):
    """The durable revision could not be committed; prior authority stands."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DelegatedCardService:
    def __init__(
        self,
        *,
        store: BundleStorageDelegatedCardStore,
        cache: DelegatedCardRuntimeCache,
        settings: DelegatedCacheSettings | None = None,
    ) -> None:
        self._store = store
        self._cache = cache
        self._settings = (settings or DelegatedCacheSettings()).cards

    def _lock_path(self, *, subject_hash: str, access_id: str) -> pathlib.Path:
        return (
            self._store.card_path(subject_hash=subject_hash, access_id=access_id)
            / CARD_LOCK_FILENAME
        )

    def _critical_section(self, *, subject_hash: str, access_id: str):
        return observed_file_lock_async(
            lock_path=self._lock_path(subject_hash=subject_hash, access_id=access_id),
            resource_id=f"delegated-card:{access_id}",
            operation="delegated-card-mutation",
            wait_seconds=CARD_LOCK_WAIT_SECONDS,
        )

    async def commit(
        self,
        authority: CardAuthority,
        *,
        subject_hash: str,
        expected_revision: int,
        now: int | None = None,
    ) -> CardCurrentPointer:
        """Commit the next revision and expose it as live authority."""
        moment = int(now if now is not None else time.time())
        mutation_id = uuid.uuid4().hex
        try:
            async with self._critical_section(
                subject_hash=subject_hash, access_id=authority.access_id
            ):
                await self._assert_expected(
                    subject_hash=subject_hash,
                    access_id=authority.access_id,
                    expected_revision=expected_revision,
                )
                await self._mark_updating(
                    access_id=authority.access_id,
                    mutation_id=mutation_id,
                    expected_revision=expected_revision,
                )
                pointer = await self._commit_durable(
                    authority=authority, subject_hash=subject_hash, mutation_id=mutation_id
                )
                await self._cache.commit_projection(
                    authority,
                    mutation_id=mutation_id,
                    ttl_seconds=max(0, authority.expires_at - moment),
                )
                await self._index(
                    authority=authority, subject_hash=subject_hash, moment=moment
                )
                return pointer
        except ObservedFileLockTimeout as exc:
            raise CardConflict("card_mutation_lock_timeout") from exc

    async def revoke(
        self,
        *,
        subject_hash: str,
        access_id: str,
        expected_revision: int,
    ) -> CardCurrentPointer | None:
        """Commit a revoked revision before any credential cleanup.

        Returns ``None`` when the card has no durable history. After the
        revoked revision commits, a failure to clean a credential handle cannot
        restore authority: live resolution observes the revoked state.
        """
        mutation_id = uuid.uuid4().hex
        try:
            async with self._critical_section(
                subject_hash=subject_hash, access_id=access_id
            ):
                current = await self._store.read_current_authority(
                    subject_hash=subject_hash, access_id=access_id
                )
                if current is None:
                    await self._cache.finalize_removal(access_id, mutation_id=mutation_id)
                    await self._cache.index_remove(
                        subject_hash=subject_hash, access_id=access_id
                    )
                    return None
                _, authority = current
                if authority.card_revision != int(expected_revision):
                    raise CardConflict(
                        "card_revision_moved", current_revision=authority.card_revision
                    )

                await self._mark_updating(
                    access_id=access_id,
                    mutation_id=mutation_id,
                    expected_revision=expected_revision,
                )
                revoked = replace_state(authority, CARD_STATE_REVOKED)
                pointer = await self._commit_durable(
                    authority=revoked, subject_hash=subject_hash, mutation_id=mutation_id
                )
                await self._cache.commit_tombstone(
                    access_id,
                    card_revision=revoked.card_revision,
                    mutation_id=mutation_id,
                    ttl_seconds=self._settings.revoked_tombstone_seconds,
                )
                await self._cache.index_remove(
                    subject_hash=subject_hash, access_id=access_id
                )
                return pointer
        except ObservedFileLockTimeout as exc:
            raise CardConflict("card_mutation_lock_timeout") from exc

    async def _assert_expected(
        self, *, subject_hash: str, access_id: str, expected_revision: int
    ) -> None:
        """The lost-update check reads durable state, not the cache."""
        current = await self._store.read_current_authority(
            subject_hash=subject_hash, access_id=access_id
        )
        held = current[1].card_revision if current is not None else 0
        if held != int(expected_revision):
            raise CardConflict("card_revision_moved", current_revision=held)

    async def _mark_updating(
        self, *, access_id: str, mutation_id: str, expected_revision: int
    ) -> None:
        """Close readers for the transition. The section already excludes other
        writers, so a refusal here means the live value is a marker or tombstone
        this mutation must not displace."""
        claimed = await self._cache.claim_transition(
            access_id,
            mutation_id=mutation_id,
            expected_revision=expected_revision,
            ttl_seconds=self._settings.updating_marker_seconds,
        )
        if claimed:
            return
        entry = await self._cache.read(access_id)
        raise CardConflict(
            "card_transition_not_claimed",
            current_revision=entry.card_revision if entry is not None else 0,
        )

    async def _commit_durable(
        self, *, authority: CardAuthority, subject_hash: str, mutation_id: str
    ) -> CardCurrentPointer:
        moment = datetime.now(timezone.utc)
        try:
            pointer = await self._store.write_revision(
                subject_hash=subject_hash, authority=authority, updated_at=moment
            )
            await self._store.advance_current(subject_hash=subject_hash, pointer=pointer)
        except Exception as exc:
            # Nothing committed, or a revision written without becoming current.
            # Drop the marker so the previously committed revision serves again.
            await self._release(authority.access_id, mutation_id=mutation_id)
            raise CardCommitFailed("durable_commit_failed") from exc
        return pointer

    async def _release(self, access_id: str, *, mutation_id: str) -> None:
        try:
            await self._cache.finalize_removal(access_id, mutation_id=mutation_id)
        except Exception:
            _LOGGER.warning(
                "[connection-hub.delegated-cards] marker release failed card=%s",
                access_id,
                exc_info=True,
            )

    async def _index(
        self, *, authority: CardAuthority, subject_hash: str, moment: int
    ) -> None:
        if authority.state != CARD_STATE_ACTIVE or authority.expires_at <= moment:
            await self._cache.index_remove(
                subject_hash=subject_hash, access_id=authority.access_id
            )
            return
        await self._cache.index_add(
            subject_hash=subject_hash,
            access_id=authority.access_id,
            expires_at=authority.expires_at,
        )


def replace_state(authority: CardAuthority, state: str) -> CardAuthority:
    """The same authority at the next revision, in a new lifecycle state."""
    return CardAuthority(
        access_id=authority.access_id,
        client_id=authority.client_id,
        grantor_subject=authority.grantor_subject,
        delegate_subject=authority.delegate_subject,
        source=authority.source,
        label=authority.label,
        card_revision=authority.card_revision + 1,
        catalog_version=authority.catalog_version,
        state=state,
        operations=authority.operations,
        resource_grants=authority.resource_grants,
        named_service_operations=authority.named_service_operations,
        named_services=authority.named_services,
        account_scope=authority.account_scope,
        identity_scope=authority.identity_scope,
        created_at=authority.created_at,
        expires_at=authority.expires_at,
        last_issued_at=authority.last_issued_at,
        last_four=authority.last_four,
    )


__all__ = [
    "CARD_LOCK_WAIT_SECONDS",
    "CardCommitFailed",
    "CardConflict",
    "DelegatedCardService",
    "replace_state",
]

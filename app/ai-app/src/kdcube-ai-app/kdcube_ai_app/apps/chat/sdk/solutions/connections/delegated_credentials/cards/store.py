# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable storage for immutable card revisions and the current-revision pointer.

Layout under a shared bundle-storage root:

    delegated-cards/v1/grantors/<subject_hash>/cards/<access_id>/
        revisions/card_revision_<stamp>_<revision>_<hash12>.json
        current.json

This module owns paths and object IO only. The mutation protocol — critical
section, updating marker, ordered commit — belongs to the card service.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
from datetime import datetime
from typing import Protocol

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CardAuthority,
    CardCurrentPointer,
    CardRecordError,
    card_revision_name,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.durable_io import (
    DurableStorageError,
    list_child_names,
    read_json_or_none,
    write_json_atomic,
)

CARDS_DIRNAME = "delegated-cards"
CARDS_LAYOUT_VERSION = "v1"
GRANTORS_DIRNAME = "grantors"
CARDS_SUBDIRNAME = "cards"
REVISIONS_DIRNAME = "revisions"
CURRENT_FILENAME = "current.json"

_SUBJECT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ACCESS_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REVISION_NAME_PATTERN = re.compile(
    r"^card_revision_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}"
    r"_[0-9]{8}_[0-9a-f]{12}\.json$"
)


class CardStorageError(DurableStorageError):
    """Durable card storage could not be read or written."""


def subject_hash_for(grantor_subject: str) -> str:
    """The storage scope a grantor's cards live under."""
    return hashlib.sha256(str(grantor_subject or "").encode("utf-8")).hexdigest()


def validated_subject_hash(subject_hash: str) -> str:
    value = str(subject_hash or "").strip().lower()
    if not _SUBJECT_HASH_PATTERN.match(value):
        raise CardStorageError("subject_hash_invalid")
    return value


def validated_access_id(access_id: str) -> str:
    value = str(access_id or "").strip()
    if not _ACCESS_ID_PATTERN.match(value):
        raise CardStorageError("access_id_invalid")
    return value


def validated_revision_name(revision_name: str) -> str:
    value = str(revision_name or "").strip()
    if not _REVISION_NAME_PATTERN.match(value):
        raise CardStorageError("revision_name_invalid")
    return value


class DelegatedCardStore(Protocol):
    async def read_current(
        self, *, subject_hash: str, access_id: str
    ) -> CardCurrentPointer | None: ...

    async def read_revision(
        self, *, subject_hash: str, access_id: str, revision_name: str
    ) -> CardAuthority | None: ...

    async def write_revision(
        self, *, subject_hash: str, authority: CardAuthority, updated_at: datetime
    ) -> CardCurrentPointer: ...

    async def advance_current(
        self, *, subject_hash: str, pointer: CardCurrentPointer
    ) -> None: ...

    async def list_card_ids(self, *, subject_hash: str) -> list[str]: ...


class BundleStorageDelegatedCardStore:
    """``DelegatedCardStore`` over a shared bundle-storage root."""

    def __init__(self, storage_root: str | os.PathLike[str]) -> None:
        self._root = pathlib.Path(storage_root) / CARDS_DIRNAME / CARDS_LAYOUT_VERSION

    @property
    def root(self) -> pathlib.Path:
        return self._root

    def grantor_path(self, subject_hash: str) -> pathlib.Path:
        return self._root / GRANTORS_DIRNAME / validated_subject_hash(subject_hash) / CARDS_SUBDIRNAME

    def card_path(self, *, subject_hash: str, access_id: str) -> pathlib.Path:
        return self.grantor_path(subject_hash) / validated_access_id(access_id)

    def current_path(self, *, subject_hash: str, access_id: str) -> pathlib.Path:
        return self.card_path(subject_hash=subject_hash, access_id=access_id) / CURRENT_FILENAME

    def revision_path(
        self, *, subject_hash: str, access_id: str, revision_name: str
    ) -> pathlib.Path:
        return (
            self.card_path(subject_hash=subject_hash, access_id=access_id)
            / REVISIONS_DIRNAME
            / validated_revision_name(revision_name)
        )

    async def read_current(
        self, *, subject_hash: str, access_id: str
    ) -> CardCurrentPointer | None:
        payload = await read_json_or_none(
            self.current_path(subject_hash=subject_hash, access_id=access_id)
        )
        if payload is None:
            return None
        return CardCurrentPointer.from_mapping(payload)

    async def read_revision(
        self, *, subject_hash: str, access_id: str, revision_name: str
    ) -> CardAuthority | None:
        payload = await read_json_or_none(
            self.revision_path(
                subject_hash=subject_hash, access_id=access_id, revision_name=revision_name
            )
        )
        if payload is None:
            return None
        return CardAuthority.from_mapping(payload)

    async def read_current_authority(
        self, *, subject_hash: str, access_id: str
    ) -> tuple[CardCurrentPointer, CardAuthority] | None:
        """The latest committed revision, with its pointer's hash verified.

        ``None`` means the card is confirmed absent. A pointer that names a
        missing or altered revision raises: that is corruption, not absence.
        """
        pointer = await self.read_current(subject_hash=subject_hash, access_id=access_id)
        if pointer is None:
            return None
        authority = await self.read_revision(
            subject_hash=subject_hash,
            access_id=access_id,
            revision_name=pointer.revision_name,
        )
        if authority is None:
            raise CardStorageError("current_revision_missing")
        if authority.content_hash() != pointer.content_hash:
            raise CardRecordError("revision_content_hash_mismatch")
        if authority.card_revision != pointer.card_revision:
            raise CardRecordError("revision_number_mismatch")
        return pointer, authority

    async def write_revision(
        self, *, subject_hash: str, authority: CardAuthority, updated_at: datetime
    ) -> CardCurrentPointer:
        """Write the immutable revision and return the pointer that commits it.

        Advancing ``current.json`` is a separate step so the caller can validate
        the written object first.
        """
        content_hash = authority.content_hash()
        revision_name = card_revision_name(
            card_revision=authority.card_revision,
            content_hash=content_hash,
            updated_at=updated_at,
        )
        await write_json_atomic(
            self.revision_path(
                subject_hash=subject_hash,
                access_id=authority.access_id,
                revision_name=revision_name,
            ),
            authority.to_dict(),
        )
        return CardCurrentPointer.for_revision(
            authority,
            revision_name=revision_name,
            content_hash=content_hash,
            updated_at=updated_at,
        )

    async def advance_current(self, *, subject_hash: str, pointer: CardCurrentPointer) -> None:
        await write_json_atomic(
            self.current_path(subject_hash=subject_hash, access_id=pointer.access_id),
            pointer.to_dict(),
        )

    async def list_card_ids(self, *, subject_hash: str) -> list[str]:
        names = await list_child_names(self.grantor_path(subject_hash))
        return [name for name in names if _ACCESS_ID_PATTERN.match(name)]

    async def list_revision_names(self, *, subject_hash: str, access_id: str) -> list[str]:
        path = self.card_path(subject_hash=subject_hash, access_id=access_id) / REVISIONS_DIRNAME
        names = await list_child_names(path)
        return [name for name in names if _REVISION_NAME_PATTERN.match(name)]


__all__ = [
    "CARDS_DIRNAME",
    "CARDS_LAYOUT_VERSION",
    "CURRENT_FILENAME",
    "BundleStorageDelegatedCardStore",
    "CardStorageError",
    "DelegatedCardStore",
    "REVISIONS_DIRNAME",
    "validated_access_id",
    "validated_revision_name",
    "subject_hash_for",
    "validated_subject_hash",
]

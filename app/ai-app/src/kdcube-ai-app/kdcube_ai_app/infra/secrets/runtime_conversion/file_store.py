# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Explicit local cloned-vault port, with the existing encryption/CAS store."""
from __future__ import annotations

from pathlib import Path

from kdcube_ai_app.infra.secrets.host_vault.keys import FileRootKeyProvider, SealedValue
from kdcube_ai_app.infra.secrets.host_vault.protocol import SecretNamespace, SecretReference
from kdcube_ai_app.infra.secrets.host_vault.storage import FileDurableSecretStore
from kdcube_ai_app.infra.secrets.runtime_conversion.model import (
    ConversionError, Observation, SourceRecord,
)


class CopiedFileVault:
    """Construct through the admitted factory, never before target admission.

    This privileged offline adapter also observes tombstone generations,
    which the normal read API intentionally projects to absence. It does not
    introduce a service endpoint, selector override, root-key rotation or
    enrollment. Actual cloned-provider qualification remains independent.
    """
    def __init__(self, root: Path, *, scope: tuple[str, str, str]):
        key_directory = root / "root-keys"
        store_directory = root / "store"
        if (not key_directory.is_dir() or key_directory.is_symlink()
                or not store_directory.is_dir() or store_directory.is_symlink()):
            raise ConversionError("runtime_conversion_copy_incomplete")
        keys = FileRootKeyProvider(key_directory)
        keys.qualify_custody()  # Reads existing material only, never rotate().
        self._store = FileDurableSecretStore(store_directory, keys)
        self._store.qualify_custody()
        self._scope = SecretNamespace(tenant=scope[0], project=scope[1], application=scope[2])

    def _reference(self, source: SourceRecord):
        return SecretReference.derive(namespace=self._scope, internal_key=source.key)

    def read(self, source: SourceRecord) -> Observation:
        reference = self._reference(source)
        # Retain the existing store's validated codec/OS lock. No plaintext
        # is read for absence/tombstone and no private metadata is published.
        with self._store._locked():
            payload = self._store._read_raw(reference)
            if payload is None:
                return Observation(0, "absent")
            if payload["deleted"]:
                return Observation(payload["generation"], "tombstone")
            # Decode under this one OS lock. Calling get() here would acquire
            # another flock descriptor and deadlock against our own lock.
            metadata = self._store._record(payload)
            value = self._store._envelope.open(
                SealedValue.from_dict(payload.get("sealed") or {}),
                record_id=self._store._record_id(reference, metadata.generation))
            return Observation(metadata.generation, "present", value)

    def replace(self, source: SourceRecord, value: bytes) -> int:
        return self._store.put(self._reference(source), value,
                               expected_generation=source.generation).generation

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable encrypted secret store.

``DurableSecretStore`` is the service-owned storage interface; ``FileDurableSecretStore``
is the reference implementation: one file per record under a service-owned
directory, values sealed by ``keys.Envelope``.

Durability rules the implementation keeps:

- a mutation is acknowledged only after its record file is written to a
  candidate path, fsynced, atomically renamed over the committed path, and
  the directory entry is fsynced. A crash before the rename leaves the
  previous committed record untouched and a stray candidate that the next
  load ignores and removes;
- every record carries a generation; a replace or delete may name the
  generation it expects and fails with ``conflict`` when the committed one
  moved (concurrent replacement), so two brokers cannot silently overwrite;
- a delete commits a tombstone (generation advances, no value) rather than
  removing the file, so a deletion is as durable as a write and a later
  create continues the generation sequence;
- integrity: the record carries a digest over its metadata and sealed value;
  a mismatch, an unreadable file, an unknown key version, or an AEAD failure
  fails closed as ``corrupt_record``; nothing is guessed;
- bounds: value bytes and record size are capped at the protocol bound.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import stat
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:
    import fcntl
except ImportError:  # Native host-vault custody requires POSIX file locks.
    fcntl = None

from kdcube_ai_app.infra.secrets.host_vault.keys import (
    Envelope,
    RootKeyProvider,
    SealedValue,
)
from kdcube_ai_app.infra.secrets.host_vault.protocol import (
    MAX_VALUE_BYTES,
    ErrorCode,
    SecretNamespace,
    SecretReference,
    VaultError,
)
from kdcube_ai_app.infra.secrets.runtime_contract import persistent_filesystem

RECORD_FORMAT = "kdcube-host-vault-record/1"
MAX_RECORD_BYTES = MAX_VALUE_BYTES * 2 + 4096
MAX_LIST_SCAN_RECORDS = 100_000
LOGGER = logging.getLogger("kdcube.host_vault.storage")


@dataclass(frozen=True)
class StoredRecord:
    """What the store returns: never the plaintext by default."""

    reference_digest: str
    generation: int
    deleted: bool
    committed_at: float
    root_key_id: str


class DurableSecretStore(Protocol):
    def qualify_custody(self) -> None: ...

    def get(self, reference: SecretReference) -> tuple[StoredRecord, bytes] | None: ...

    def put(self, reference: SecretReference, value: bytes, *, expected_generation: int | None) -> StoredRecord: ...

    def delete(self, reference: SecretReference, *, expected_generation: int | None) -> StoredRecord: ...

    def list_names(
        self,
        namespace: SecretNamespace,
        *,
        prefix: str,
        limit: int,
    ) -> list[str]: ...

    def rewrap_all(self) -> int: ...


def _fsync_dir(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _record_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate record field")
        result[key] = value
    return result


def _reject_record_constant(_value: str) -> None:
    raise ValueError("nonstandard record number")


class FileDurableSecretStore:
    """Reference durable store. ``root`` must be a service-owned directory
    outside the KDCube runtime workdir."""

    CANDIDATE_SUFFIX = ".candidate"
    LOCK_NAME = ".store.lock"

    def __init__(self, root: Path, keys: RootKeyProvider) -> None:
        self._root = Path(root)
        self._keys = keys
        self._envelope = Envelope(keys)
        self._lock = threading.RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._root, 0o700)
        except OSError:
            pass
        self.recover()

    def qualify_custody(self) -> None:
        """Check private persistent storage and root keys under the OS lock.

        This does not attest a deployment mount's retention policy or host
        isolation. Those are independent installation/qualification legs.
        """
        fd = None
        try:
            if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            metadata = os.fstat(fd)
            entry = self._root.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            with self._locked() as lock_fd:
                if (persistent_filesystem(self._root) is not True
                        or self._keys.qualify_custody() is not None):
                    raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
                current = os.fstat(fd)
                entry = self._root.lstat()
                if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid()
                        or stat.S_IMODE(current.st_mode) != 0o700
                        or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
                        or (current.st_dev, current.st_ino) != (entry.st_dev, entry.st_ino)):
                    raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
                self._validate_lock(lock_fd)
        except Exception:
            # Root providers and OS probes may contain private paths/material.
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None
        finally:
            if fd is not None:
                os.close(fd)

    def _validate_lock(self, fd: int) -> None:
        metadata = os.fstat(fd)
        entry = (self._root / self.LOCK_NAME).lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
        ):
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)

    @contextmanager
    def _locked(self):
        """Serialize generation checks, commits and recovery across processes.

        Each acquisition opens its own descriptor, so independent store objects
        in one process also contend. The private lock inode must remain in place
        for the lifetime of the service root; cleanup must never unlink it.
        """
        with self._lock:
            if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE)
            fd = None
            lock_ready = False
            try:
                lock_path = self._root / self.LOCK_NAME
                fd = os.open(
                    lock_path,
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._validate_lock(fd)
                lock_ready = True
            except OSError:
                raise VaultError(ErrorCode.BACKEND_UNAVAILABLE) from None
            finally:
                # Acquisition can be interrupted before reaching the yield.
                # Release in that case too, including KeyboardInterrupt.
                if fd is not None and not lock_ready:
                    os.close(fd)
            try:
                yield fd
            finally:
                # Closing also releases the advisory lock on exceptional exits.
                os.close(fd)

    # ── layout ────────────────────────────────────────────────────────────

    def _path(self, reference: SecretReference) -> Path:
        # Two-level fan-out on the digest keeps directories bounded; the
        # digest, not the name, is the file name, so names never touch the fs.
        digest = reference.digest
        return self._root / digest[:2] / f"{digest}.json"

    @staticmethod
    def _record_id(reference: SecretReference, generation: int) -> str:
        return f"{reference.digest}#{generation}"

    @staticmethod
    def _name_record_id(reference_digest: str, generation: int) -> str:
        return f"{reference_digest}#{generation}:name"

    def recover(self) -> int:
        """Crash recovery: drop candidates that never reached commit. Called
        on start; safe to call any time. Returns the number removed."""
        with self._locked():
            removed = 0
            for candidate in self._root.rglob(f"*{self.CANDIDATE_SUFFIX}"):
                try:
                    candidate.unlink()
                    removed += 1
                except OSError:
                    pass
            return removed

    # ── record codec ──────────────────────────────────────────────────────

    @staticmethod
    def _digest(payload: dict) -> str:
        material = json.dumps({k: v for k, v in payload.items() if k != "integrity"}, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _read_path(self, path: Path) -> dict:
        if not path.is_file():
            raise VaultError(ErrorCode.NOT_FOUND)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE, detail=type(exc).__name__) from exc
        if len(raw) > MAX_RECORD_BYTES:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="record too large")
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_record_without_duplicate_keys,
                parse_constant=_reject_record_constant,
            )
        except Exception as exc:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="record json") from exc
        allowed = {
            "format",
            "reference_digest",
            "generation",
            "deleted",
            "committed_at",
            "sealed",
            "sealed_name",
            "integrity",
        }
        generation = payload.get("generation") if isinstance(payload, dict) else None
        committed_at = payload.get("committed_at") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) - allowed
            or payload.get("format") != RECORD_FORMAT
            or not isinstance(payload.get("reference_digest"), str)
            or not re.fullmatch(r"[a-f0-9]{24}", payload["reference_digest"])
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not isinstance(payload.get("deleted"), bool)
            or isinstance(committed_at, bool)
            or not isinstance(committed_at, (int, float))
            or not math.isfinite(float(committed_at))
            or float(committed_at) <= 0
            or not isinstance(payload.get("sealed"), dict)
            or (
                "sealed_name" in payload
                and not isinstance(payload.get("sealed_name"), dict)
            )
            or not isinstance(payload.get("integrity"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", payload["integrity"])
        ):
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="record format")
        if self._digest(payload) != payload.get("integrity"):
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="record integrity")
        return payload

    def _read_raw(self, reference: SecretReference) -> dict | None:
        path = self._path(reference)
        if not path.is_file():
            return None
        payload = self._read_path(path)
        if payload.get("reference_digest") != reference.digest:
            raise VaultError(ErrorCode.CORRUPT_RECORD, detail="record identity")
        return payload

    def _write_committed(self, reference: SecretReference, payload: dict) -> None:
        payload = dict(payload)
        payload["integrity"] = self._digest(payload)
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        if len(data) > MAX_RECORD_BYTES:
            raise VaultError(ErrorCode.TOO_LARGE)
        path = self._path(reference)
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = path.with_name(path.name + self.CANDIDATE_SUFFIX)
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                if os.name == "posix":
                    os.fchmod(fd, 0o600)
                remaining = memoryview(data)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("short record write")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            self._commit_hook()
            os.replace(candidate, path)
            _fsync_dir(path.parent)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError(ErrorCode.BACKEND_UNAVAILABLE, detail=type(exc).__name__) from exc

    def _commit_hook(self) -> None:
        """Test seam: raising here simulates a crash between the candidate
        write and the atomic commit."""

    @staticmethod
    def _record(payload: dict) -> StoredRecord:
        return StoredRecord(
            reference_digest=str(payload["reference_digest"]),
            generation=int(payload["generation"]),
            deleted=bool(payload.get("deleted")),
            committed_at=float(payload.get("committed_at") or 0.0),
            root_key_id=str((payload.get("sealed") or {}).get("root_key_id") or ""),
        )

    # ── operations ────────────────────────────────────────────────────────

    def get(self, reference: SecretReference) -> tuple[StoredRecord, bytes] | None:
        with self._locked():
            payload = self._read_raw(reference)
            if payload is None or payload.get("deleted"):
                return None
            record = self._record(payload)
            sealed = SealedValue.from_dict(payload.get("sealed") or {})
            value = self._envelope.open(sealed, record_id=self._record_id(reference, record.generation))
            return record, value

    def _check_generation(self, payload: dict | None, expected: int | None) -> int:
        current = int(payload["generation"]) if payload else 0
        if expected is not None and expected != current:
            raise VaultError(ErrorCode.CONFLICT)
        return current

    def put(self, reference: SecretReference, value: bytes, *, expected_generation: int | None) -> StoredRecord:
        if len(value) > MAX_VALUE_BYTES:
            raise VaultError(ErrorCode.TOO_LARGE)
        with self._locked():
            payload = self._read_raw(reference)
            generation = self._check_generation(payload, expected_generation) + 1
            sealed = self._envelope.seal(value, record_id=self._record_id(reference, generation))
            sealed_name = self._envelope.seal(
                reference.name.encode("utf-8"),
                record_id=self._name_record_id(reference.digest, generation),
            )
            new_payload = {
                "format": RECORD_FORMAT,
                "reference_digest": reference.digest,
                "generation": generation,
                "deleted": False,
                "committed_at": time.time(),
                "sealed": sealed.to_dict(),
                "sealed_name": sealed_name.to_dict(),
            }
            self._write_committed(reference, new_payload)
            return self._record(new_payload)

    def delete(self, reference: SecretReference, *, expected_generation: int | None) -> StoredRecord:
        with self._locked():
            payload = self._read_raw(reference)
            if payload is None or payload.get("deleted"):
                if expected_generation is not None:
                    self._check_generation(payload, expected_generation)
                raise VaultError(ErrorCode.NOT_FOUND)
            generation = self._check_generation(payload, expected_generation) + 1
            tombstone = {
                "format": RECORD_FORMAT,
                "reference_digest": reference.digest,
                "generation": generation,
                "deleted": True,
                "committed_at": time.time(),
                "sealed": {},
            }
            self._write_committed(reference, tombstone)
            return self._record(tombstone)

    def list_names(
        self,
        namespace: SecretNamespace,
        *,
        prefix: str,
        limit: int,
    ) -> list[str]:
        """List live names for one authorized namespace and bounded prefix.

        Names are encrypted in each new record. Legacy records without that
        field remain readable and are recovered by the broker's verified
        legacy-inventory fallback.
        """

        if limit <= 0:
            raise VaultError(ErrorCode.INVALID_REQUEST)
        found: set[str] = set()
        scanned = 0
        with self._locked():
            for path in self._root.rglob("*.json"):
                scanned += 1
                if scanned > MAX_LIST_SCAN_RECORDS:
                    raise VaultError(ErrorCode.TOO_LARGE)
                payload = self._read_path(path)
                if payload.get("deleted") or not isinstance(
                    payload.get("sealed_name"), dict
                ):
                    continue
                digest = str(payload.get("reference_digest") or "")
                generation = int(payload.get("generation") or 0)
                try:
                    name = self._envelope.open(
                        SealedValue.from_dict(payload["sealed_name"]),
                        record_id=self._name_record_id(digest, generation),
                    ).decode("utf-8")
                    reference = SecretReference(namespace=namespace, name=name)
                except (UnicodeDecodeError, ValueError, VaultError) as exc:
                    raise VaultError(
                        ErrorCode.CORRUPT_RECORD,
                        detail="record name",
                    ) from exc
                if reference.digest != digest:
                    continue
                if not name.startswith(prefix):
                    continue
                found.add(name)
                if len(found) > limit:
                    raise VaultError(ErrorCode.TOO_LARGE)
        return sorted(found)

    def _all_paths(self) -> Iterable[Path]:
        return sorted(self._root.rglob("*.json"))

    def rewrap_all(self) -> int:
        """Root-key rotation: rewrap every live record's data key under the
        current root key. Values are never decrypted. Returns rewrapped count.
        A record that fails to unwrap is left as is and counted nowhere; it
        surfaces as corrupt on its next read, which is the honest state."""
        count = 0
        with self._locked():
            for path in self._all_paths():
                try:
                    payload = self._read_path(path)
                except VaultError:
                    LOGGER.warning(
                        "Skipping unreadable host-vault record during root-key rotation: %s",
                        path.name,
                    )
                    continue
                if payload.get("deleted"):
                    continue
                digest = str(payload.get("reference_digest") or "")
                if path.name != f"{digest}.json":
                    LOGGER.warning(
                        "Skipping misplaced host-vault record during root-key rotation: %s",
                        path.name,
                    )
                    continue
                generation = int(payload.get("generation") or 0)
                try:
                    sealed = SealedValue.from_dict(payload.get("sealed") or {})
                    rewrapped = self._envelope.rewrap(sealed, record_id=f"{digest}#{generation}")
                    sealed_name_raw = payload.get("sealed_name")
                    sealed_name = (
                        SealedValue.from_dict(sealed_name_raw)
                        if isinstance(sealed_name_raw, dict)
                        else None
                    )
                    rewrapped_name = (
                        self._envelope.rewrap(
                            sealed_name,
                            record_id=self._name_record_id(digest, generation),
                        )
                        if sealed_name is not None
                        else None
                    )
                except VaultError:
                    continue
                if rewrapped is sealed and rewrapped_name is sealed_name:
                    continue
                payload["sealed"] = rewrapped.to_dict()
                if rewrapped_name is not None:
                    payload["sealed_name"] = rewrapped_name.to_dict()
                payload = {k: v for k, v in payload.items() if k != "integrity"}
                pseudo = _DigestOnlyReference(digest)
                self._write_committed(pseudo, payload)  # type: ignore[arg-type]
                count += 1
        return count


class _DigestOnlyReference:
    """Path selector for records visited by digest during rotation (the store
    never learns names from disk, and rewrap needs none)."""

    def __init__(self, digest: str) -> None:
        self.digest = digest


__all__ = [
    "MAX_LIST_SCAN_RECORDS",
    "RECORD_FORMAT",
    "DurableSecretStore",
    "FileDurableSecretStore",
    "StoredRecord",
]

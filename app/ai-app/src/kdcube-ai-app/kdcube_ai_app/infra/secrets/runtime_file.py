# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Private, namespace-bound runtime records, separate from descriptor YAML.

All readers and mutations take the same OS file lock. The host supplies the
authorized namespace set and a dedicated persistent root; namespace spelling
is never authorization. Same-UID/co-located code remains one trust boundary.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_REF = re.compile(r"[0-9a-f]{32}")
_MAX_VALUE_BYTES = 65536
_MAX_STORE_BYTES = 16 * 1024 * 1024


class RuntimeFileError(RuntimeError):
    """Finite failure without secret material or filesystem paths."""


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class RuntimeFileStore:
    """One authorized namespace, immutable create, expiry reads, locked purge.

    Durability through host/container replacement still depends on mounting
    this root persistently. This primitive proves local fsync/restart semantics,
    not the deployment's mount lifecycle or isolation from the same OS user.
    """

    def __init__(self, *, root: str | Path, namespace: str, authorized_namespaces: Iterable[str]):
        if type(namespace) is not str or _NAMESPACE.fullmatch(namespace) is None:
            raise RuntimeFileError("runtime_secret_scope_invalid")
        if (isinstance(authorized_namespaces, (str, bytes))
                or namespace not in frozenset(authorized_namespaces)):
            raise RuntimeFileError("runtime_secret_scope_forbidden")
        self._root = Path(root)
        if not self._root.is_absolute() or self._root == Path("/"):
            raise RuntimeFileError("runtime_secret_storage_unavailable")
        self._namespace = namespace
        self._path = self._root / f"{namespace}.json"
        self._lock_path = self._root / f"{namespace}.lock"

    @staticmethod
    def _private_file(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise RuntimeFileError("runtime_secret_storage_unavailable")

    def _prepare_root(self) -> None:
        # A configured broad/public directory is refused, never chmod-ed.
        self._root.mkdir(mode=0o700, exist_ok=True)
        info = self._root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or self._root.resolve() != self._root):
            raise RuntimeFileError("runtime_secret_storage_unavailable")

    @contextmanager
    def _locked(self):
        descriptor = None
        try:
            self._prepare_root()
            descriptor = os.open(
                self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
            )
            self._private_file(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            # A replaced lock entry must not split cooperating writers into
            # separate lock domains while one waited on the original inode.
            entry, opened = self._lock_path.lstat(), os.fstat(descriptor)
            if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
                raise RuntimeFileError("runtime_secret_storage_unavailable")
            self._private_file(descriptor)
            yield
        except (OSError, UnicodeError, ValueError, TypeError):
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def qualify(self) -> None:
        """Check actual private storage/locking and read integrity, without a value."""
        with self._locked():
            from kdcube_ai_app.infra.secrets.runtime_contract import persistent_filesystem

            if not persistent_filesystem(self._root):
                raise RuntimeFileError("runtime_secret_storage_not_persistent")
            self._load()

    def _load(self) -> dict:
        try:
            descriptor = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return {}
        try:
            self._private_file(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_STORE_BYTES + 1)
            if len(raw) > _MAX_STORE_BYTES:
                raise RuntimeFileError("runtime_secret_storage_unavailable")
            records = json.loads(raw, object_pairs_hook=_unique_fields)
            if type(records) is not dict:
                raise ValueError
            for ref, record in records.items():
                self._validate_ref(ref)
                if (type(record) is not dict or set(record) != {"value", "expires_at"}
                        or (record["value"] is not None and (
                            type(record["value"]) is not str
                            or len(record["value"].encode("utf-8")) > _MAX_VALUE_BYTES))
                        or type(record["expires_at"]) is not int or record["expires_at"] <= 0):
                    raise ValueError
            return records
        finally:
            os.close(descriptor)

    def _save(self, records: dict) -> None:
        encoded = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(encoded) > _MAX_STORE_BYTES:
            raise RuntimeFileError("runtime_secret_storage_unavailable")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._namespace}-", dir=self._root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            directory = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _validate_ref(secret_ref: str) -> None:
        if type(secret_ref) is not str or _REF.fullmatch(secret_ref) is None:
            raise RuntimeFileError("runtime_secret_reference_invalid")

    def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        self._validate_ref(secret_ref)
        try:
            if (type(value) is not str or len(value.encode("utf-8")) > _MAX_VALUE_BYTES
                    or type(expires_at) is not int or expires_at <= 0):
                raise ValueError
        except (ValueError, UnicodeError):
            raise RuntimeFileError("runtime_secret_value_invalid") from None
        with self._locked():
            # The clock is read after the writer lock, not before waiting.
            if expires_at <= int(time.time()):
                raise RuntimeFileError("runtime_secret_expired")
            records = self._load()
            if secret_ref in records:
                return False
            records[secret_ref] = {"value": value, "expires_at": expires_at}
            self._save(records)
            return True

    def get(self, *, secret_ref: str) -> str | None:
        self._validate_ref(secret_ref)
        with self._locked():
            record = self._load().get(secret_ref)
            if record is None or record["expires_at"] <= int(time.time()):
                return None
            return record["value"]

    def delete(self, *, secret_ref: str) -> None:
        self._validate_ref(secret_ref)
        with self._locked():
            records = self._load()
            record = records.get(secret_ref)
            if record is not None and record["value"] is not None:
                # Retain the used reference, with no bearer material, so a
                # delayed identical create cannot become a new incarnation.
                record["value"] = None
                self._save(records)

    def purge_expired(self, *, now: int, limit: int) -> int:
        if type(now) is not int or now <= 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RuntimeFileError("runtime_secret_purge_invalid")
        with self._locked():
            if now > int(time.time()):
                raise RuntimeFileError("runtime_secret_purge_invalid")
            records = self._load()
            expired = [ref for ref, record in records.items()
                       if record["value"] is not None and record["expires_at"] <= now][:limit]
            for ref in expired:
                records[ref]["value"] = None
            if expired:
                self._save(records)
            return len(expired)

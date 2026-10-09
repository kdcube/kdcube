# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Private runtime records of an owner bundle, one file per secret, separate from descriptor YAML.

Layout (operator, 2026-10-09: a folder per owner bundle with a subfolder per purpose; "NO!" to one file
per purpose; "nolock"):

    <root>/<owner bundle id or "platform">/<namespace>/<ref>.json

Create writes a private temporary file, fsyncs it, and publishes it with ``os.link`` to the final name,
which fails when the name exists: create-only across processes with no lock of any kind, and a reader never
sees a partial record. Delete and purge
replace a record with a value-free tombstone (write-temp, fsync, ``os.replace``); the tombstone stays, so a
used reference can never be created again. The host supplies the authorized namespace set and the root;
namespace spelling is never authorization. Same-UID/co-located code remains one trust boundary.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from time import sleep as _sleep
from typing import Iterable

_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
# One path segment: no separator, no dot (so no traversal and no ambiguity in dotted provider keys).
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_@-]{0,127}")
_REF = re.compile(r"[0-9a-f]{32}")
_RECORD_FILE = re.compile(r"[0-9a-f]{32}\.json")
PLATFORM_OWNER = "platform"
_MAX_VALUE_BYTES = 65536
# A record is one JSON object; ASCII escaping may grow a value up to six times.
_MAX_RECORD_BYTES = 6 * _MAX_VALUE_BYTES + 256
_LIVE_MODE, _TOMBSTONE_MODE = 0o600, 0o400
# A reader may meet a record between its publication link and the removal of its temporary name.
_INCOMPLETE_READ_ATTEMPTS, _INCOMPLETE_READ_PAUSE = 20, 0.025


class RuntimeFileError(RuntimeError):
    """Finite failure without secret material or filesystem paths."""


class _Incomplete(ValueError):
    pass


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def runtime_owner(bundle_id: str | None) -> str:
    """The owner folder: the bundle id, or ``platform`` for a bundle-less caller."""
    if bundle_id is None:
        return PLATFORM_OWNER
    if (type(bundle_id) is not str or _OWNER.fullmatch(bundle_id) is None
            or bundle_id == PLATFORM_OWNER):
        raise RuntimeFileError("runtime_secret_owner_invalid")
    return bundle_id


class RuntimeFileStore:
    """One owner and one authorized namespace; immutable create, expiry reads, tombstoning purge.

    Durability through host/container replacement still depends on mounting
    this root persistently. This primitive proves local fsync/restart semantics,
    not the deployment's mount lifecycle or isolation from the same OS user.
    """

    def __init__(self, *, root: str | Path, namespace: str, authorized_namespaces: Iterable[str],
                 owner: str | None = None):
        # "users" is reserved: <root>/<bundle>/users/ holds per-user secrets (user_secret_files).
        if type(namespace) is not str or _NAMESPACE.fullmatch(namespace) is None or namespace == "users":
            raise RuntimeFileError("runtime_secret_scope_invalid")
        if (isinstance(authorized_namespaces, (str, bytes))
                or namespace not in frozenset(authorized_namespaces)):
            raise RuntimeFileError("runtime_secret_scope_forbidden")
        self._root = Path(root)
        if not self._root.is_absolute() or self._root == Path("/"):
            raise RuntimeFileError("runtime_secret_storage_unavailable")
        self._namespace = namespace
        self._owner = runtime_owner(owner)
        self._folder = self._root / self._owner / namespace

    @staticmethod
    def _private_file(descriptor: int) -> int:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or mode not in (_LIVE_MODE, _TOMBSTONE_MODE) or info.st_nlink != 1):
            raise RuntimeFileError("runtime_secret_storage_unavailable")
        return mode

    def _prepare(self) -> None:
        # Each level is created 0700 when absent (a concurrent creator is fine) and then verified;
        # a configured broad/public directory or a symlink is refused, never chmod-ed or followed.
        try:
            for folder in (self._root, self._root / self._owner, self._folder):
                try:
                    folder.mkdir(mode=0o700)
                    created = True
                except FileExistsError:
                    created = False
                if created:  # a new entry is durable in its parent before anything is published inside it
                    parent = os.open(folder.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                info = folder.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o700):
                    raise RuntimeFileError("runtime_secret_storage_unavailable")
            if self._folder.resolve() != self._folder:
                raise RuntimeFileError("runtime_secret_storage_unavailable")
        except (OSError, ValueError):
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None

    def _path(self, secret_ref: str) -> Path:
        return self._folder / f"{secret_ref}.json"

    def _fsync_folder(self) -> None:
        directory = os.open(self._folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def qualify(self) -> None:
        """Check the actual private folder chain and its storage, without a value."""
        self._prepare()
        from kdcube_ai_app.infra.secrets.runtime_contract import persistent_filesystem

        if not persistent_filesystem(self._folder):
            raise RuntimeFileError("runtime_secret_storage_not_persistent")
        # Publication needs hard links on the actual mount; prove it without a value.
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".probe-", dir=self._folder)
            os.close(descriptor)
            published = f"{temporary}-published"
            try:
                os.link(temporary, published)
                os.unlink(published)
            finally:
                os.unlink(temporary)
        except OSError:
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None

    def _read_once(self, path: Path) -> dict | None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 2:
                raise _Incomplete  # being published: still linked under its temporary name
            mode = self._private_file(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_RECORD_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_RECORD_BYTES:
            raise ValueError
        record = json.loads(raw, object_pairs_hook=_unique_fields)
        if (type(record) is not dict or set(record) != {"value", "expires_at"}
                or type(record["expires_at"]) is not int or record["expires_at"] <= 0
                or (record["value"] is None) != (mode == _TOMBSTONE_MODE)
                or (record["value"] is not None and (
                    type(record["value"]) is not str
                    or len(record["value"].encode("utf-8")) > _MAX_VALUE_BYTES))):
            raise ValueError
        return record

    def _read(self, secret_ref: str) -> dict | None:
        path = self._path(secret_ref)
        try:
            for _ in range(_INCOMPLETE_READ_ATTEMPTS):
                try:
                    return self._read_once(path)
                except _Incomplete:
                    _sleep(_INCOMPLETE_READ_PAUSE)
            raise ValueError
        except (OSError, UnicodeError, ValueError, TypeError):
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None

    def _tombstone(self, secret_ref: str, expires_at: int) -> None:
        encoded = json.dumps({"value": None, "expires_at": expires_at}, sort_keys=True,
                             separators=(",", ":")).encode("ascii")
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".tombstone-", dir=self._folder)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fchmod(stream.fileno(), _TOMBSTONE_MODE)
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path(secret_ref))
                self._fsync_folder()
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        except OSError:
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None

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
        self._prepare()
        if expires_at <= int(time.time()):
            raise RuntimeFileError("runtime_secret_expired")
        encoded = json.dumps({"value": value, "expires_at": expires_at}, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True).encode("ascii")
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=self._folder)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    self._private_file(stream.fileno())
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # The complete record appears under its final name at once, and never replaces one.
                    os.link(temporary, self._path(secret_ref))
                except FileExistsError:
                    return False
            finally:
                os.unlink(temporary)
            self._fsync_folder()
        except OSError:
            # A crash before the link leaves only a temporary name: the reference is still free. After the
            # link, the reference is taken and its complete record stays the original.
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None
        return True

    def get(self, *, secret_ref: str) -> str | None:
        self._validate_ref(secret_ref)
        self._prepare()
        record = self._read(secret_ref)
        if record is None or record["value"] is None or record["expires_at"] <= int(time.time()):
            return None
        return record["value"]

    def delete(self, *, secret_ref: str) -> None:
        self._validate_ref(secret_ref)
        self._prepare()
        record = self._read(secret_ref)
        if record is not None and record["value"] is not None:
            # Retain the used reference, with no bearer material, so a
            # delayed identical create cannot become a new incarnation.
            self._tombstone(secret_ref, record["expires_at"])

    def purge_expired(self, *, now: int, limit: int) -> int:
        if type(now) is not int or now <= 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RuntimeFileError("runtime_secret_purge_invalid")
        if now > int(time.time()):
            raise RuntimeFileError("runtime_secret_purge_invalid")
        self._prepare()
        removed = 0
        try:
            with os.scandir(self._folder) as entries:
                for entry in entries:
                    if removed >= limit:
                        break
                    if _RECORD_FILE.fullmatch(entry.name) is None:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == _TOMBSTONE_MODE:
                        continue  # already value-free; skipped without opening it
                    secret_ref = entry.name[:-len(".json")]
                    record = self._read(secret_ref)
                    if record is not None and record["value"] is not None and record["expires_at"] <= now:
                        self._tombstone(secret_ref, record["expires_at"])
                        removed += 1
        except OSError:
            raise RuntimeFileError("runtime_secret_storage_unavailable") from None
        return removed

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Staged inbound files for integration actions.

The mirror of the signed download URL: an external agent asks the namespace
for an upload slot (``request_upload``), PUTs the bytes to the returned signed
URL over plain HTTP — never through the model's context — and then references
the returned ``staged:`` ref in ``send``/``upload_file`` payloads. The adapter
resolves the staged bytes into the tool workspace at action time and deletes
them after use (single-use refs with a short TTL sweep as backstop).

The authenticated upload request creates an owner-bound slot before minting a
capability. Consumers pass their trusted context explicitly as ``owner`` for
loads and cleanup; provider-fetch reads explicitly pass ``None`` after checking
their signed capability. Public uploads use ``owner=None`` only to write an
existing slot. Trusted actions stage new provider copies with ``owner=ctx``.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import pathlib
import re
import shutil
import stat
import tempfile
import time
import uuid

STAGED_REF_PREFIX = "staged:"
STAGED_TTL_SECONDS = 3600
MAX_STAGED_FILE_BYTES = 25 * 1024 * 1024
_STAGING_DIRNAME = "kdcube-integration-staging"
_STAGED_ID = re.compile(r"[0-9a-f]{32}")
_OWNER_FILENAME = ".owner.json"
_NOT_FOUND = "staged file not found (expired, already used, or never uploaded)"


def _safe_filename(raw: str) -> str:
    name = pathlib.PurePosixPath(str(raw or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or "\x00" in name or name.casefold() == _OWNER_FILENAME:
        raise ValueError("A staged file needs a plain filename (no directories).")
    return name


def staging_root(storage_path: str = "") -> pathlib.Path:
    """Directory staged files live in until an action consumes them.

    Prefers a local ``STORAGE_PATH`` (shared by all proc workers on the host);
    falls back to the system temp dir. Upload route and action resolution must
    agree on this, so both derive it from the same entrypoint settings.
    """
    base = str(storage_path or "").strip()
    if base and "://" not in base:
        root = pathlib.Path(base) / _STAGING_DIRNAME
    else:
        root = pathlib.Path(tempfile.gettempdir()) / _STAGING_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_staged_ref(filename: str) -> str:
    return f"{STAGED_REF_PREFIX}{uuid.uuid4().hex}:{_safe_filename(filename)}"


def parse_staged_ref(ref: str) -> tuple[str, str]:
    """``staged:<id>:<filename>`` -> (id, filename); raises ValueError otherwise."""
    text = str(ref or "").strip()
    if not text.startswith(STAGED_REF_PREFIX):
        raise ValueError(f"not a staged ref: {text!r}")
    rest = text[len(STAGED_REF_PREFIX):]
    staged_id, _, filename = rest.partition(":")
    if not _STAGED_ID.fullmatch(staged_id) or not filename or _safe_filename(filename) != filename:
        raise ValueError("Malformed staged file reference. Request a new upload slot.")
    return staged_id, filename


def _staged_dir(root: pathlib.Path, staged_id: str) -> pathlib.Path:
    if not _STAGED_ID.fullmatch(staged_id):
        raise ValueError("Invalid staged file identity.")
    base = pathlib.Path(root).resolve()
    slot = base / staged_id
    if slot.is_symlink() or slot.resolve().parent != base:
        raise ValueError("Invalid staged file location.")
    return slot


def _staged_path(root: pathlib.Path, staged_id: str, filename: str) -> pathlib.Path:
    slot = _staged_dir(root, staged_id)
    if _safe_filename(filename) != filename:
        raise ValueError("Invalid staged filename.")
    target = slot / filename
    if target.is_symlink() or target.resolve().parent != slot:
        raise ValueError("Invalid staged file location.")
    return target


def _owner_identity(owner) -> dict[str, str]:
    def field(name, alternate=""):
        if isinstance(owner, dict):
            return str(owner.get(name) or owner.get(alternate) or "").strip()
        return str(getattr(owner, name, "") or getattr(owner, alternate, "") or "").strip()
    identity = {"user": field("user_id"), "tenant": field("tenant", "tenant_id"), "project": field("project", "project_id")}
    if not all(identity.values()):
        raise FileNotFoundError(_NOT_FOUND)
    return identity


@contextlib.contextmanager
def _open_slot(root: pathlib.Path, staged_id: str):
    # Pin the checked directory while checking ownership and accessing bytes.
    # openat + NOFOLLOW prevents a concurrent slot/file symlink substitution.
    slot = _staged_dir(root, staged_id)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(slot.parent, flags)
    try:
        slot_fd = os.open(staged_id, flags, dir_fd=root_fd)
        try:
            yield slot, slot_fd
        finally:
            os.close(slot_fd)
    finally:
        os.close(root_fd)


def _read_file(slot_fd: int, filename: str, limit: int) -> bytes:
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=slot_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise FileNotFoundError(_NOT_FOUND)
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise FileNotFoundError(_NOT_FOUND)
        return data


def _slot_owner(slot_fd: int, owner) -> dict:
    try:
        record = json.loads(_read_file(slot_fd, _OWNER_FILENAME, 4096))
        if not isinstance(record, dict) or set(record) != {"user", "tenant", "project", "created"}:
            raise ValueError
        if not all(isinstance(record[key], str) and record[key].strip() for key in ("user", "tenant", "project")):
            raise ValueError
        created = record["created"]
        if type(created) not in (int, float) or not math.isfinite(created) or created > time.time() or created + STAGED_TTL_SECONDS < time.time():
            raise ValueError
        if owner is not None and any(record[key] != value for key, value in _owner_identity(owner).items()):
            raise ValueError
        return record
    except (OSError, ValueError, TypeError) as exc:
        raise FileNotFoundError(_NOT_FOUND) from exc


def _write_file(slot_fd: int, filename: str, data: bytes) -> None:
    temporary = ".upload-" + uuid.uuid4().hex
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=slot_fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, filename, src_dir_fd=slot_fd, dst_dir_fd=slot_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=slot_fd)
        except FileNotFoundError:
            pass


def create_slot(root: pathlib.Path, staged_ref: str, owner) -> pathlib.Path:
    """Create a new slot bound to the authenticated request's user and scope."""
    staged_id, _ = parse_staged_ref(staged_ref)
    record = {**_owner_identity(owner), "created": time.time()}
    slot = _staged_dir(root, staged_id)
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.mkdir(mode=0o700)
    with _open_slot(root, staged_id) as (_, slot_fd):
        _write_file(slot_fd, _OWNER_FILENAME, json.dumps(record).encode())
    sweep_expired(root)
    return slot


def save_staged(root: pathlib.Path, staged_ref: str, data: bytes, *, owner) -> pathlib.Path:
    staged_id, filename = parse_staged_ref(staged_ref)
    if len(data) > MAX_STAGED_FILE_BYTES:
        raise ValueError(f"staged file exceeds the {MAX_STAGED_FILE_BYTES}-byte limit")
    if owner is not None:
        try:
            create_slot(root, staged_ref, owner)
        except FileExistsError:
            pass
    target = _staged_path(root, staged_id, filename)
    try:
        with _open_slot(root, staged_id) as (_, slot_fd):
            _slot_owner(slot_fd, owner)
            _write_file(slot_fd, filename, data)
    except OSError as exc:
        raise FileNotFoundError(_NOT_FOUND) from exc
    sweep_expired(root)
    return target


def load_staged(root: pathlib.Path, staged_ref: str, *, owner) -> tuple[str, bytes]:
    """Read an owned slot. Only a verified provider-fetch capability uses None."""
    staged_id, filename = parse_staged_ref(staged_ref)
    try:
        _staged_path(root, staged_id, filename)
        with _open_slot(root, staged_id) as (_, slot_fd):
            _slot_owner(slot_fd, owner)
            return filename, _read_file(slot_fd, filename, MAX_STAGED_FILE_BYTES)
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(_NOT_FOUND) from exc


def delete_staged(root: pathlib.Path, staged_ref: str, *, owner) -> None:
    try:
        staged_id, _filename = parse_staged_ref(staged_ref)
        with _open_slot(root, staged_id) as (slot, slot_fd):
            _slot_owner(slot_fd, owner)
            # rmtree's directory-fd implementation refuses substituted links.
            root_fd = os.open(slot.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if os.stat(staged_id, dir_fd=root_fd, follow_symlinks=False).st_ino == os.fstat(slot_fd).st_ino:
                    shutil.rmtree(staged_id, dir_fd=root_fd)
            finally:
                os.close(root_fd)
    except (OSError, ValueError):
        return


def sweep_expired(root: pathlib.Path, *, ttl_seconds: int = STAGED_TTL_SECONDS) -> None:
    """Best-effort removal of staged dirs older than the TTL."""
    cutoff = time.time() - max(60, int(ttl_seconds))
    try:
        for entry in pathlib.Path(root).iterdir():
            try:
                slot = _staged_dir(root, entry.name)
                if slot.is_dir() and slot.stat().st_mtime < cutoff:
                    base_fd = os.open(slot.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        shutil.rmtree(slot.name, dir_fd=base_fd)
                    finally:
                        os.close(base_fd)
            except (OSError, ValueError):
                continue
    except OSError:
        pass


__all__ = [
    "MAX_STAGED_FILE_BYTES",
    "STAGED_REF_PREFIX",
    "STAGED_TTL_SECONDS",
    "create_slot",
    "delete_staged",
    "load_staged",
    "new_staged_ref",
    "parse_staged_ref",
    "save_staged",
    "staging_root",
    "sweep_expired",
]

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""W677: at container start, hand root-owned private entries of the secrets folder to the one owner uid.

Run as root before a service drops privileges (chat-proc's entrypoint) or serves (kdcube-secrets):

    python -m kdcube_ai_app.infra.secrets.ownership_repair <secrets root> <owner uid>

Entries are adopted only when ALL of these hold, otherwise they are left untouched for the stores to refuse:

- the target is an explicit secrets directory: absolute, canonical (no symlink or ".." on the path), not
  the filesystem root, at least two levels deep;
- every directory on the way down is a trusted private folder: a 0700 directory owned by root or by the
  owner, on the same filesystem as the target (other filesystems are pruned before descent);
- the entry itself is root-owned and is either a 0700 directory or a single-link regular file at exactly
  0600 (record) or 0400 (tombstone);
- the walk never follows a symlink: each entry is opened relative to its parent's descriptor with
  O_NOFOLLOW, its inode and attributes are re-checked on the open descriptor, and ownership changes with
  fchown on that descriptor.

Modes are never changed and nothing is removed, with one exception. A file-sharing layer may refuse to re-own
a read-only file: Docker Desktop on macOS keeps container ownership as an extended attribute of the host file.
The exception applies only to a runtime-record tombstone refused that way. That is a 0400
``<root>/<owner>/<namespace>/<32 hex>.json``, outside ``users``, whose bytes are exactly the store's
tombstone (``{"expires_at":<int>,"value":null}``, so a duplicate, escaped or reordered key never matches).
Its original is never modified. A replacement with the same bytes is written beside it: created 0600 with
O_EXCL, re-owned while still writable, made 0400 and fsynced. It then replaces the original. Any failure
before that leaves the root-owned original exactly as it was, so the entry is deferred again on every pass
and every start.

The replace is not a compare-and-swap. The name's inode is re-checked first, but a writer acting between
that check and the replace would be overwritten. No supported writer can act there: the runtime store takes
no lock (operator decision), create publishes with os.link and never reuses an existing tombstone's name,
and delete and purge write tombstones only over live records. So a tombstoned record name is never
rewritten, and a concurrent repair writes the same bytes. Every deferral is reported by stage and errno,
never by path. Stdlib only (the secrets image carries no SDK).
"""
from __future__ import annotations

import errno
import json
import os
import re
import stat
import sys

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_FILE_MODES = (0o600, 0o400)
_MAX_TOMBSTONE_BYTES = 256
_RECORD_NAME = re.compile(r"[0-9a-f]{32}\.json")  # runtime_file's record names


def safe_repair_root(root: object) -> bool:
    """An explicit secrets directory: absolute, canonical, not "/", at least two levels deep, existing."""
    if not isinstance(root, str) or not root.startswith("/"):
        return False
    normal = os.path.normpath(root)
    if normal == "/" or normal != root.rstrip("/") or len([part for part in normal.split("/") if part]) < 2:
        return False
    return os.path.realpath(normal) == normal and os.path.isdir(normal) and not os.path.islink(normal)


def _owner_uid(owner: object) -> int | None:
    text = str(owner if owner is not None else "").strip()
    return int(text) if text.isdigit() and int(text) != 0 else None


def _trusted_dir(info: os.stat_result, owner: int, device: int) -> bool:
    return (stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid in (0, owner) and info.st_dev == device)


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_ino, left.st_dev) == (right.st_ino, right.st_dev)


class _Tally:
    def __init__(self) -> None:
        self.adopted = 0
        self.deferred = 0  # eligible-looking entries this pass could not safely finish (I/O error or a change)
        self.reasons: dict[str, int] = {}  # "<stage>:<errno name>" -> count, for the log line (no paths)

    def defer(self, stage: str, exc: OSError | str | None = None) -> None:
        self.deferred += 1
        if isinstance(exc, OSError):
            cause = errno.errorcode.get(exc.errno or 0, "OSError")
        else:
            cause = exc or "changed"
        reason = f"{stage}:{cause}"
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


class _Deferred(Exception):
    def __init__(self, stage: str, cause: OSError | str | None = None) -> None:
        super().__init__(stage)
        self.stage, self.cause = stage, cause


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _is_tombstone(raw: bytes) -> bool:
    """Exactly the bytes runtime_file writes for a tombstone; anything else (a duplicate or escaped key, other
    spacing or order, a value) is refused before anything is created."""
    try:
        record = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_fields)
    except (UnicodeError, ValueError):
        return False
    if not (type(record) is dict and set(record) == {"value", "expires_at"} and record["value"] is None
            and type(record["expires_at"]) is int and record["expires_at"] > 0):
        return False
    canonical = json.dumps({"value": None, "expires_at": record["expires_at"]}, sort_keys=True,
                           separators=(",", ":")).encode("ascii")
    return raw == canonical


def _read_small(handle: int) -> bytes:
    chunks, size = [], 0
    while size <= _MAX_TOMBSTONE_BYTES:
        chunk = os.read(handle, _MAX_TOMBSTONE_BYTES + 1 - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _replace_tombstone(fd: int, name: str, handle: int, info: os.stat_result, owner: int) -> None:
    """Swap a read-only tombstone the filesystem would not re-own for an owner-owned 0400 copy (see module doc)."""
    try:
        raw = _read_small(handle)
    except OSError as exc:
        raise _Deferred("tombstone-read", exc) from None
    if len(raw) > _MAX_TOMBSTONE_BYTES or not _is_tombstone(raw):
        raise _Deferred("tombstone", "not-value-free")
    temporary = f".tombstone-repair-{os.urandom(8).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        out = os.open(temporary, flags, 0o600, dir_fd=fd)
    except OSError as exc:
        raise _Deferred("tombstone-replace", exc) from None
    published = False  # until then the temporary (created here, O_EXCL) is removed on any failure
    try:
        try:
            os.fchown(out, owner, -1)
            view = memoryview(raw)
            while view:
                view = view[os.write(out, view):]
            os.fchmod(out, 0o400)
            os.fsync(out)
            if not _same(os.stat(name, dir_fd=fd, follow_symlinks=False), info):
                raise _Deferred("tombstone-replace")  # the name now points elsewhere: look again next pass
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            published = True
        except OSError as exc:
            raise _Deferred("tombstone-replace", exc) from None
        finally:
            os.close(out)
        try:
            os.fsync(fd)
        except OSError:
            pass  # the copy is complete and correct; a crash before the folder syncs brings back the original
    finally:
        if not published:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass


def _visit(fd: int, owner: int, device: int, tally: _Tally, chain: tuple[str, ...] = ()) -> None:
    """fd is an open, already verified trusted directory (``chain``: its folder names below the root); adopt
    its private children and descend."""
    try:
        names = os.listdir(fd)
    except OSError as exc:
        tally.defer("list", exc)
        return
    for name in names:
        try:
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue  # removed meanwhile
        except OSError as exc:
            tally.defer("stat", exc)
            continue
        if entry.st_dev != device:
            continue  # another filesystem: pruned before descent
        if stat.S_ISDIR(entry.st_mode):
            if not _trusted_dir(entry, owner, device):
                continue  # a foreign-owned or broad folder: neither adopted nor descended into
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                tally.defer("dir-open", exc)
                continue
            try:
                opened = os.fstat(child)
                if not _same(entry, opened):
                    tally.defer("dir-open")  # the name now points elsewhere: look again next pass
                    continue
                if not _trusted_dir(opened, owner, device):
                    continue
                if opened.st_uid == 0:
                    os.fchown(child, owner, -1)
                    tally.adopted += 1
                _visit(child, owner, device, tally, chain + (name,))
            except OSError as exc:
                tally.defer("dir-chown", exc)
            finally:
                os.close(child)
        elif stat.S_ISREG(entry.st_mode):
            if not (entry.st_uid == 0 and entry.st_nlink == 1 and stat.S_IMODE(entry.st_mode) in _FILE_MODES):
                continue
            try:
                handle = os.open(name, _FILE_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                tally.defer("file-open", exc)
                continue
            try:
                opened = os.fstat(handle)
                if (_same(entry, opened) and stat.S_ISREG(opened.st_mode) and opened.st_uid == 0
                        and opened.st_nlink == 1 and stat.S_IMODE(opened.st_mode) in _FILE_MODES):
                    try:
                        os.fchown(handle, owner, -1)
                    except PermissionError:
                        record_folder = len(chain) == 2 and chain[1] != "users"
                        if (stat.S_IMODE(opened.st_mode) != 0o400 or not record_folder
                                or _RECORD_NAME.fullmatch(name) is None):
                            raise
                        _replace_tombstone(fd, name, handle, opened, owner)
                    tally.adopted += 1
                elif not _same(entry, opened):
                    tally.defer("file-open")  # the name now points elsewhere: look again next pass
            except _Deferred as deferral:
                tally.defer(deferral.stage, deferral.cause)
            except OSError as exc:
                tally.defer("file-chown", exc)
            finally:
                os.close(handle)
        # symlinks, sockets, devices and anything else are never touched


_MAX_PASSES = 3


def _one_pass(root: str, owner_uid: int) -> _Tally:
    tally = _Tally()
    fd = os.open(root, _DIR_FLAGS)
    try:
        top = os.fstat(fd)
        if not _trusted_dir(top, owner_uid, top.st_dev):
            return tally
        if top.st_uid == 0:
            os.fchown(fd, owner_uid, -1)
            tally.adopted += 1
        _visit(fd, owner_uid, top.st_dev, tally)
        return tally
    finally:
        os.close(fd)


def _repair(root: object, owner: object) -> tuple[int, _Tally]:
    """(adopted over all passes, the last pass's tally). Passes repeat while entries were deferred and the
    last pass made progress, so a transient I/O error or a concurrent change does not leave an eligible entry
    root-owned until next start."""
    last = _Tally()
    owner_uid = _owner_uid(owner)
    if owner_uid is None or os.geteuid() != 0 or not safe_repair_root(root):
        return 0, last
    root = os.path.normpath(str(root))
    adopted = 0
    for attempt in range(_MAX_PASSES):
        try:
            tally = _one_pass(root, owner_uid)
        except OSError as exc:
            last.defer("root", exc)
            return adopted, last
        adopted += tally.adopted
        last = tally
        if not tally.deferred or (attempt and not tally.adopted):
            break
    return adopted, last


def repair_secrets_ownership_report(root: object, owner: object) -> tuple[int, int]:
    """(adopted, deferred)."""
    adopted, last = _repair(root, owner)
    return adopted, last.deferred


def repair_secrets_ownership(root: object, owner: object) -> int:
    """Adopt root-owned private entries under ``root`` to ``owner``; the number of entries changed."""
    return repair_secrets_ownership_report(root, owner)[0]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    adopted, last = _repair(args[0], args[1])
    reasons = ",".join(f"{reason}*{count}" for reason, count in sorted(last.reasons.items()))
    print(f"secrets ownership repair: adopted={adopted} deferred={last.deferred}"
          + (f" reasons={reasons}" if reasons else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

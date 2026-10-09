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

Modes are never changed and nothing is created or removed. Stdlib only (the secrets image carries no SDK).
"""
from __future__ import annotations

import os
import stat
import sys

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_FILE_MODES = (0o600, 0o400)


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


def _visit(fd: int, owner: int, device: int) -> int:
    """fd is an open, already verified trusted directory; adopt its private children and descend."""
    changed = 0
    for name in os.listdir(fd):
        try:
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            continue
        if entry.st_dev != device:
            continue  # another filesystem: pruned before descent
        if stat.S_ISDIR(entry.st_mode):
            if not _trusted_dir(entry, owner, device):
                continue  # a foreign-owned or broad folder: neither adopted nor descended into
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError:
                continue
            try:
                opened = os.fstat(child)
                if not _same(entry, opened) or not _trusted_dir(opened, owner, device):
                    continue
                if opened.st_uid == 0:
                    os.fchown(child, owner, -1)
                    changed += 1
                changed += _visit(child, owner, device)
            finally:
                os.close(child)
        elif stat.S_ISREG(entry.st_mode):
            if not (entry.st_uid == 0 and entry.st_nlink == 1 and stat.S_IMODE(entry.st_mode) in _FILE_MODES):
                continue
            try:
                handle = os.open(name, _FILE_FLAGS, dir_fd=fd)
            except OSError:
                continue
            try:
                opened = os.fstat(handle)
                if (_same(entry, opened) and stat.S_ISREG(opened.st_mode) and opened.st_uid == 0
                        and opened.st_nlink == 1 and stat.S_IMODE(opened.st_mode) in _FILE_MODES):
                    os.fchown(handle, owner, -1)
                    changed += 1
            finally:
                os.close(handle)
        # symlinks, sockets, devices and anything else are never touched
    return changed


def repair_secrets_ownership(root: object, owner: object) -> int:
    """Adopt root-owned private entries under ``root`` to ``owner``; the number of entries changed."""
    owner_uid = _owner_uid(owner)
    if owner_uid is None or os.geteuid() != 0 or not safe_repair_root(root):
        return 0
    root = os.path.normpath(str(root))
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError:
        return 0
    try:
        top = os.fstat(fd)
        if not _trusted_dir(top, owner_uid, top.st_dev):
            return 0
        changed = 0
        if top.st_uid == 0:
            os.fchown(fd, owner_uid, -1)
            changed += 1
        return changed + _visit(fd, owner_uid, top.st_dev)
    except OSError:
        return 0
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    print(f"secrets ownership repair: adopted={repair_secrets_ownership(args[0], args[1])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

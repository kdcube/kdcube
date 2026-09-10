# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Cross-process locking for local descriptor mutations."""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

try:  # POSIX services and local macOS development.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows.
    fcntl = None  # type: ignore

try:  # Native Windows development.
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - exercised on POSIX.
    msvcrt = None  # type: ignore


_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


def _lock_file(handle: BinaryIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:  # pragma: no cover - exercised on Windows.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    raise RuntimeError("This platform provides no supported descriptor file lock")


def _unlock_file(handle: BinaryIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - exercised on Windows.
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def descriptor_edit_lock(path: Path) -> Iterator[None]:
    """Serialize read-modify-write cycles for one descriptor file."""
    target = Path(path)
    lock_path = target.with_name(f".{target.name}.lock")
    with _process_lock(lock_path):
        with lock_path.open("a+b") as handle:
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)


__all__ = ["descriptor_edit_lock"]

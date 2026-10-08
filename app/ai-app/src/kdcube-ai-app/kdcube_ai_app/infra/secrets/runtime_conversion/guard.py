# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Admission and private durable receipts for one explicitly bound clone."""
from __future__ import annotations

import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fcntl

from kdcube_ai_app.infra.secrets.runtime_conversion.model import (
    ConversionError, canonical, strict_json,
)

MARKER = ".runtime-conversion-clone.json"
JOURNAL = ".runtime-conversion-progress.json"
MARKER_SCHEMA = "kdcube.runtime_conversion_clone.v1"


@dataclass(frozen=True)
class CloneReceipt:
    target: Path
    clone_id: str
    inventory_sha256: str
    attempt: int
    sealed_roots: tuple[Path, ...]
    live_roots: tuple[Path, ...]
    live_open_paths: tuple[Path, ...]


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def read_private(root: Path, name: str, *, bound: int = 4 * 1024 * 1024):
    fd = None
    try:
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > bound):
            raise ValueError
        parts = bytearray()
        while len(parts) <= bound:
            part = os.read(fd, min(65536, bound + 1 - len(parts)))
            if not part:
                break
            parts.extend(part)
        current = (root / name).lstat()
        if len(parts) > bound or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError
        return strict_json(bytes(parts))
    except FileNotFoundError:
        return None
    except Exception:
        raise ConversionError("runtime_conversion_private_receipt_invalid") from None
    finally:
        if fd is not None:
            os.close(fd)


def write_private(root: Path, name: str, data) -> None:
    """Commit inside the admitted clone only; no value-bearing journal fields."""
    temporary = root / (".conversion-" + uuid.uuid4().hex + ".tmp")
    fd = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        pending = memoryview(canonical(data))
        while pending:
            count = os.write(fd, pending)
            if count <= 0:
                raise OSError
            pending = pending[count:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, root / name)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        raise ConversionError("runtime_conversion_receipt_write_failed") from None
    finally:
        if fd is not None:
            os.close(fd)
        # Only this exact newly-created candidate is disposable.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class Admission:
    def __init__(self, receipt: CloneReceipt):
        self.receipt = receipt
        self.root = receipt.target.resolve(strict=True)
        self._identity = None

    def check(self) -> dict:
        try:
            r = self.receipt
            if (not r.target.is_absolute() or r.target.is_symlink() or self.root == Path("/")
                    or re.fullmatch(r"[0-9a-f]{32}", r.clone_id) is None
                    or re.fullmatch(r"[0-9a-f]{64}", r.inventory_sha256) is None
                    or type(r.attempt) is not int or r.attempt < 1
                    or not r.sealed_roots or not r.live_roots or not r.live_open_paths):
                raise ValueError
            for path in (*r.sealed_roots, *r.live_roots, *r.live_open_paths):
                if not path.is_absolute() or _overlaps(self.root, path.resolve(strict=False)):
                    raise ValueError
            metadata = self.root.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or r.target.resolve(strict=True) != self.root):
                raise ValueError
            identity = (metadata.st_dev, metadata.st_ino)
            if self._identity is not None and identity != self._identity:
                raise ValueError
            self._identity = identity
            marker = read_private(self.root, MARKER, bound=4096)
            if (type(marker) is not dict
                    or set(marker) != {"schema", "clone_id", "inventory_sha256", "attempt", "state"}
                    or marker["schema"] != MARKER_SCHEMA or marker["clone_id"] != r.clone_id
                    or marker["inventory_sha256"] != r.inventory_sha256
                    or marker["attempt"] != r.attempt or type(marker["attempt"]) is not int
                    or marker["state"] not in {"ready", "running", "complete"}):
                raise ValueError
            return marker
        except Exception:
            raise ConversionError("runtime_conversion_target_refused") from None

    def state(self, state: str):
        marker = self.check()
        write_private(self.root, MARKER, {**marker, "state": state})

    @contextmanager
    def locked(self):
        self.check()  # Before store/key-provider construction or any key read.
        fd = None
        try:
            # Structural metadata only. A link or uncommitted store candidate
            # cannot be accepted as part of a sealed, quiescent source cut.
            for index, path in enumerate(self.root.rglob("*")):
                metadata = path.lstat()
                regular = stat.S_ISREG(metadata.st_mode)
                directory = stat.S_ISDIR(metadata.st_mode)
                mode = stat.S_IMODE(metadata.st_mode)
                if (index >= 100000 or not (regular or directory)
                        or metadata.st_uid != os.geteuid()
                        or regular and (metadata.st_nlink != 1 or mode not in {0o400, 0o600})
                        or directory and mode != 0o700
                        or path.name.endswith(".candidate")
                        or path.name.startswith(".conversion-") and path.name.endswith(".tmp")):
                    raise ConversionError("runtime_conversion_target_refused")
            fd = os.open(self.root / ".runtime-conversion.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            metadata = os.fstat(fd)
            entry = (self.root / ".runtime-conversion.lock").lstat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
                    or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)):
                raise ConversionError("runtime_conversion_target_refused")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check()
            yield self
        except BlockingIOError:
            raise ConversionError("runtime_conversion_already_running") from None
        except OSError:
            raise ConversionError("runtime_conversion_target_refused") from None
        finally:
            if fd is not None:
                os.close(fd)

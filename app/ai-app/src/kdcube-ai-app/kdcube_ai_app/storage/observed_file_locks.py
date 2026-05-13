# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pathlib
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, Iterator, Optional


LockMetadata = Dict[str, Any]
LockWaitCallback = Callable[[pathlib.Path, Optional[LockMetadata], Optional[float]], None]

_PROCESS_LOCKS: Dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class ObservedFileLockTimeout(TimeoutError):
    pass


def make_lock_metadata(
    *,
    resource_id: str,
    operation: str,
    owner_token: str,
    instance_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> LockMetadata:
    now = time.time()
    metadata: LockMetadata = {
        "owner_token": owner_token,
        "resource_id": resource_id,
        "operation": operation,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "instance_id": instance_id or os.environ.get("INSTANCE_ID") or os.environ.get("HOSTNAME") or "unknown",
        "created_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "created_ts": now,
    }
    if extra:
        metadata.update(extra)
    return metadata


def lock_metadata_path(lock_path: pathlib.Path) -> pathlib.Path:
    return lock_path


def read_lock_metadata(lock_path: pathlib.Path) -> Optional[LockMetadata]:
    try:
        if not lock_path.exists():
            return None
        content = lock_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def lock_metadata_age_seconds(metadata: Optional[LockMetadata]) -> Optional[float]:
    if not metadata:
        return None
    try:
        return max(0.0, time.time() - float(metadata.get("created_ts") or 0))
    except Exception:
        return None


def lock_owner_summary(metadata: Optional[LockMetadata]) -> str:
    if not metadata:
        return ""
    return (
        f" owner_instance={metadata.get('instance_id', '-')}"
        f" owner_host={metadata.get('hostname', '-')}"
        f" owner_pid={metadata.get('pid', '-')}"
        f" operation={metadata.get('operation', '-')}"
    )


def _write_lock_metadata(fh: Any, metadata: LockMetadata) -> None:
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(metadata, indent=2, sort_keys=True))
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())


def _delete_lock_metadata(fh: Any, owner_token: str) -> None:
    try:
        fh.seek(0)
        content = fh.read().strip()
        metadata = json.loads(content) if content else None
        if metadata and metadata.get("owner_token") not in {None, owner_token}:
            return
        fh.seek(0)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    except Exception:
        pass


def _process_lock(lock_key: str) -> threading.Lock:
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[lock_key] = lock
        return lock


def _notify_wait(
    *,
    lock_path: pathlib.Path,
    on_wait: Optional[LockWaitCallback],
) -> None:
    if on_wait is None:
        return
    metadata = read_lock_metadata(lock_path)
    on_wait(lock_path, metadata, lock_metadata_age_seconds(metadata))


def _deadline(wait_seconds: Optional[float]) -> Optional[float]:
    if wait_seconds is None:
        return None
    return time.time() + max(0.0, float(wait_seconds))


def _remaining_seconds(deadline: Optional[float]) -> Optional[float]:
    if deadline is None:
        return None
    return max(0.0, deadline - time.time())


def _timeout_if_expired(deadline: Optional[float], *, lock_path: pathlib.Path, operation: str) -> None:
    if deadline is not None and time.time() >= deadline:
        metadata = read_lock_metadata(lock_path)
        raise ObservedFileLockTimeout(
            f"timed out waiting for observed file lock operation={operation} lock={lock_path}"
            f"{lock_owner_summary(metadata)}"
        )


def _sleep_interval(deadline: Optional[float], poll_interval_seconds: float) -> float:
    poll = max(0.01, float(poll_interval_seconds))
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        return poll
    return max(0.01, min(poll, remaining))


@contextmanager
def observed_file_lock(
    *,
    lock_path: pathlib.Path,
    resource_id: str,
    operation: str,
    instance_id: Optional[str] = None,
    on_wait: Optional[LockWaitCallback] = None,
    wait_seconds: Optional[float] = None,
    poll_interval_seconds: float = 0.25,
) -> Iterator[LockMetadata]:
    """
    Acquire an observable local/EFS filesystem lock.

    The in-process lock serializes concurrent threads/coroutines in the current
    worker. The fcntl advisory lock coordinates sibling worker processes and
    EFS-sharing containers. The lock file itself contains diagnostic metadata.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = _deadline(wait_seconds)
    process_lock = _process_lock(str(lock_path.resolve()))
    while not process_lock.acquire(blocking=False):
        _notify_wait(lock_path=lock_path, on_wait=on_wait)
        _timeout_if_expired(deadline, lock_path=lock_path, operation=operation)
        time.sleep(_sleep_interval(deadline, poll_interval_seconds))
    owner_token = os.urandom(16).hex()
    metadata = make_lock_metadata(
        resource_id=resource_id,
        operation=operation,
        owner_token=owner_token,
        instance_id=instance_id,
    )
    try:
        with open(lock_path, "a+", encoding="utf-8") as fh:
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _notify_wait(lock_path=lock_path, on_wait=on_wait)
                    _timeout_if_expired(deadline, lock_path=lock_path, operation=operation)
                    time.sleep(_sleep_interval(deadline, poll_interval_seconds))
            _write_lock_metadata(fh, metadata)
            try:
                yield metadata
            finally:
                _delete_lock_metadata(fh, owner_token)
                fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        process_lock.release()


@asynccontextmanager
async def observed_file_lock_async(
    *,
    lock_path: pathlib.Path,
    resource_id: str,
    operation: str,
    instance_id: Optional[str] = None,
    on_wait: Optional[LockWaitCallback] = None,
    wait_seconds: Optional[float] = None,
    poll_interval_seconds: float = 0.25,
) -> AsyncIterator[LockMetadata]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = _deadline(wait_seconds)
    process_lock = _process_lock(str(lock_path.resolve()))
    while not process_lock.acquire(blocking=False):
        _notify_wait(lock_path=lock_path, on_wait=on_wait)
        _timeout_if_expired(deadline, lock_path=lock_path, operation=operation)
        await asyncio.sleep(_sleep_interval(deadline, poll_interval_seconds))
    owner_token = os.urandom(16).hex()
    metadata = make_lock_metadata(
        resource_id=resource_id,
        operation=operation,
        owner_token=owner_token,
        instance_id=instance_id,
    )
    try:
        with open(lock_path, "a+", encoding="utf-8") as fh:
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _notify_wait(lock_path=lock_path, on_wait=on_wait)
                    _timeout_if_expired(deadline, lock_path=lock_path, operation=operation)
                    await asyncio.sleep(_sleep_interval(deadline, poll_interval_seconds))
            _write_lock_metadata(fh, metadata)
            try:
                yield metadata
            finally:
                _delete_lock_metadata(fh, owner_token)
                await asyncio.to_thread(fcntl.flock, fh, fcntl.LOCK_UN)
    finally:
        process_lock.release()

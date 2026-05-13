# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Callable, Iterator, Optional

from kdcube_ai_app.storage.observed_file_locks import (
    LockMetadata,
    lock_metadata_age_seconds,
)


RedisWaitCallback = Callable[[str, Optional[LockMetadata], Optional[float]], None]


def _decode_metadata(value: Any) -> Optional[LockMetadata]:
    try:
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        data = json.loads(value)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@contextmanager
def observed_redis_lock(
    *,
    client: Any,
    key: str,
    metadata: LockMetadata,
    ttl_seconds: int,
    wait_seconds: int,
    poll_seconds: float = 0.5,
    on_wait: Optional[RedisWaitCallback] = None,
) -> Iterator[str]:
    value = json.dumps(metadata, sort_keys=True)
    acquired = False
    start = time.time()
    logged_wait = False
    while time.time() - start < wait_seconds:
        try:
            acquired = bool(client.set(key, value, nx=True, ex=ttl_seconds))
        except Exception:
            acquired = False
        if acquired:
            break
        if on_wait is not None and not logged_wait:
            try:
                current_metadata = _decode_metadata(client.get(key))
            except Exception:
                current_metadata = None
            on_wait(key, current_metadata, lock_metadata_age_seconds(current_metadata))
            logged_wait = True
        time.sleep(poll_seconds)
    if not acquired:
        raise TimeoutError(f"Timed out waiting for redis lock: {key}")
    try:
        yield value
    finally:
        try:
            client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                value,
            )
        except Exception:
            pass


@asynccontextmanager
async def observed_redis_lock_async(
    *,
    client: Any,
    key: str,
    metadata: LockMetadata,
    ttl_seconds: int,
    wait_seconds: int,
    poll_seconds: float = 0.5,
    on_wait: Optional[RedisWaitCallback] = None,
) -> AsyncIterator[str]:
    value = json.dumps(metadata, sort_keys=True)
    acquired = False
    start = time.time()
    logged_wait = False
    while time.time() - start < wait_seconds:
        try:
            acquired = bool(await client.set(key, value, nx=True, ex=ttl_seconds))
        except Exception:
            acquired = False
        if acquired:
            break
        if on_wait is not None and not logged_wait:
            try:
                current_metadata = _decode_metadata(await client.get(key))
            except Exception:
                current_metadata = None
            on_wait(key, current_metadata, lock_metadata_age_seconds(current_metadata))
            logged_wait = True
        await asyncio.sleep(poll_seconds)
    if not acquired:
        raise TimeoutError(f"Timed out waiting for redis lock: {key}")
    try:
        yield value
    finally:
        try:
            await client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                value,
            )
        except Exception:
            pass

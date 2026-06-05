# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kdcube.data_bus.locks")


def partition_lock_key(partition_key: str) -> str:
    digest = hashlib.sha256(str(partition_key or "").encode("utf-8")).hexdigest()
    return f"kdcube:data-bus:lock:{digest}"


@dataclass(frozen=True)
class DataBusPartitionLock:
    key: str
    token: str
    ttl_seconds: int


class RedisDataBusPartitionLocker:
    def __init__(self, redis: Any, *, ttl_seconds: int = 60) -> None:
        self.redis = redis
        self.ttl_seconds = max(1, int(ttl_seconds or 60))

    async def acquire(self, partition_key: str) -> DataBusPartitionLock | None:
        key = partition_lock_key(partition_key)
        token = uuid.uuid4().hex
        acquired = await self.redis.set(key, token, nx=True, ex=self.ttl_seconds)
        if not acquired:
            return None
        return DataBusPartitionLock(key=key, token=token, ttl_seconds=self.ttl_seconds)

    async def renew(self, lock: DataBusPartitionLock) -> bool:
        current = await self.redis.get(lock.key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if current != lock.token:
            return False
        await self.redis.expire(lock.key, lock.ttl_seconds)
        return True

    async def release(self, lock: DataBusPartitionLock) -> bool:
        current = await self.redis.get(lock.key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if current != lock.token:
            return False
        await self.redis.delete(lock.key)
        return True


async def renew_lock_until_cancelled(
    locker: RedisDataBusPartitionLocker,
    lock: DataBusPartitionLock,
    *,
    interval_seconds: float,
) -> None:
    interval = max(0.1, float(interval_seconds or 1.0))
    try:
        while True:
            await asyncio.sleep(interval)
            ok = await locker.renew(lock)
            if not ok:
                logger.warning("[data_bus.lock] Partition lock lost: key=%s", lock.key)
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[data_bus.lock] Partition lock renewal failed: key=%s", lock.key, exc_info=True)

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Shared Redis client helpers (sync + async) with optional connection caps.

Pool sizing is driven by gateway config: pools.redis_max_connections.
"""

from __future__ import annotations

import asyncio
import logging
import time
import contextlib
from typing import Dict, Optional, Callable, Any, List

import redis
from redis.asyncio import Redis as AsyncRedis

from kdcube_ai_app.infra.redis.factory import (
    RedisClientFactory,
    RedisClientRuntime,
    build_redis_client_name,
    redis_client_name_prefix,
    safe_redis_url,
)

logger = logging.getLogger(__name__)

_MONITORS: Dict[str, "RedisConnectionMonitor"] = {}
_CLIENT_FACTORY: RedisClientFactory | None = None


def _build_client_name(kind: str) -> str:
    return build_redis_client_name(kind)


def get_redis_client_name_prefix() -> str:
    """Prefix used for Redis client_name (without pool kind)."""
    return redis_client_name_prefix()


def _safe_redis_url(url: str) -> str:
    return safe_redis_url(url)


def _resolve_max_connections(default: Optional[int] = None) -> Optional[int]:
    if default is not None:
        return default
    try:
        from kdcube_ai_app.infra.gateway.config import get_gateway_config
        cfg = get_gateway_config()
        pools_cfg = getattr(cfg, "pools", None)
        if pools_cfg and pools_cfg.redis_max_connections is not None:
            value = int(pools_cfg.redis_max_connections)
            return value if value > 0 else None
    except Exception:
        return None
    return None


def _get_client_factory() -> RedisClientFactory:
    global _CLIENT_FACTORY
    if _CLIENT_FACTORY is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings

            topology_value = getattr(get_settings(), "REDIS_TOPOLOGY", None)
            topology = str(topology_value).strip() if topology_value is not None else ""
        except Exception:
            topology = ""
        if not topology:
            topology = "standalone"
        _CLIENT_FACTORY = RedisClientFactory(
            default_topology=topology,
            max_connections_resolver=_resolve_max_connections,
        )
    return _CLIENT_FACTORY


def create_async_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
    client_name_kind: Optional[str] = None,
    **kwargs,
) -> AsyncRedis:
    """
    Create a non-shared async Redis client.

    Use this for long-lived blocking or pub/sub flows that should be recreated
    independently from the process-wide shared pool.
    """
    factory = _get_client_factory()
    request = factory.request(
        redis_url,
        runtime=RedisClientRuntime.ASYNC,
        decode_responses=decode_responses,
        max_connections=max_connections,
        shared=False,
        client_name_kind=client_name_kind,
        **kwargs,
    )
    return factory.client(request)


def get_async_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
) -> AsyncRedis:
    factory = _get_client_factory()
    request = factory.request(
        redis_url,
        runtime=RedisClientRuntime.ASYNC,
        decode_responses=decode_responses,
        max_connections=max_connections,
        shared=True,
    )
    return factory.client(request)


def get_sync_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
) -> redis.Redis:
    factory = _get_client_factory()
    request = factory.request(
        redis_url,
        runtime=RedisClientRuntime.SYNC,
        decode_responses=decode_responses,
        max_connections=max_connections,
        shared=True,
    )
    return factory.client(request)


async def close_async_redis_clients() -> None:
    if _CLIENT_FACTORY is not None:
        await _CLIENT_FACTORY.close_async_clients()


def close_sync_redis_clients() -> None:
    if _CLIENT_FACTORY is not None:
        _CLIENT_FACTORY.close_sync_clients()


async def close_all_redis_clients() -> None:
    global _CLIENT_FACTORY
    await close_async_redis_clients()
    close_sync_redis_clients()
    _CLIENT_FACTORY = None


class RedisConnectionMonitor:
    """
    Lightweight Redis health monitor.
    Emits callbacks on state changes (down -> up, up -> down).
    """
    def __init__(
        self,
        redis_url: str,
        *,
        interval_sec: float = 5.0,
        timeout_sec: float = 2.0,
    ):
        self.redis_url = redis_url
        self.interval_sec = float(interval_sec)
        self.timeout_sec = float(timeout_sec)
        self._client = get_async_redis_client(redis_url)
        self._listeners: List[Callable[[str, Optional[Exception]], Any]] = []
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._healthy: Optional[bool] = None
        self._last_change_ts: float = 0.0

    def add_listener(self, cb: Callable[[str, Optional[Exception]], Any]) -> None:
        if cb and cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[str, Optional[Exception]], Any]) -> None:
        self._listeners = [c for c in self._listeners if c is not cb]

    def is_healthy(self) -> Optional[bool]:
        return self._healthy

    def last_change_ts(self) -> float:
        return self._last_change_ts

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="redis-conn-monitor")

    async def stop(self) -> None:
        if self._task:
            self._stop_event.set()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            err: Optional[Exception] = None
            ok = False
            try:
                await asyncio.wait_for(self._client.ping(), timeout=self.timeout_sec)
                ok = True
            except Exception as e:
                err = e
                ok = False

            if self._healthy is None:
                self._healthy = ok
                self._last_change_ts = time.time()
            elif self._healthy != ok:
                self._healthy = ok
                self._last_change_ts = time.time()
                state = "up" if ok else "down"
                if ok:
                    logger.info("[RedisMonitor] Redis connection recovered")
                else:
                    logger.warning("[RedisMonitor] Redis connection lost: %s", err)
                for cb in list(self._listeners):
                    try:
                        res = cb(state, err)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        logger.exception("[RedisMonitor] listener error")

            await asyncio.sleep(self.interval_sec)


def get_redis_monitor(redis_url: str) -> RedisConnectionMonitor:
    monitor = _MONITORS.get(redis_url)
    if monitor is None:
        monitor = RedisConnectionMonitor(redis_url)
        _MONITORS[redis_url] = monitor
    return monitor

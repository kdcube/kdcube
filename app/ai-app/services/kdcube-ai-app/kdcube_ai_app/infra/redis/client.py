# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Shared Redis client helpers (sync + async) with optional connection caps.

Pool sizing is driven by gateway config: pools.redis_max_connections.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import contextlib
from typing import Dict, Optional, Tuple, Callable, Any, List

import redis
import redis.asyncio as aioredis
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

_ASYNC_CLIENTS: Dict[Tuple[str, bool, Optional[int]], AsyncRedis] = {}
_SYNC_CLIENTS: Dict[Tuple[str, bool, Optional[int]], redis.Redis] = {}
_MONITORS: Dict[str, "RedisConnectionMonitor"] = {}

_DEFAULT_REDIS_CONNECT_TIMEOUT_SEC = 5.0
_DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC = 30
_DEFAULT_REDIS_SOCKET_KEEPALIVE = True


def _client_name_base() -> str:
    return (
        os.getenv("REDIS_CLIENT_NAME")
        or os.getenv("SERVICE_NAME")
        or os.getenv("APP_NAME")
        or os.getenv("COMPONENT_NAME")
        or "kdcube"
    )


def _client_instance_hint() -> str:
    return os.getenv("INSTANCE_ID") or os.getenv("HOSTNAME") or "local"


def _sanitize_client_name(raw: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in {"-", "_", ":", "."}) else "_" for ch in raw)
    return safe[:128]


def _build_client_name(kind: str) -> str:
    base = _client_name_base()
    instance = _client_instance_hint()
    pid = os.getpid()
    return _sanitize_client_name(f"{base}:{instance}:{pid}:{kind}")


def get_redis_client_name_prefix() -> str:
    """Prefix used for Redis client_name (without pool kind)."""
    base = _client_name_base()
    instance = _client_instance_hint()
    pid = os.getpid()
    return _sanitize_client_name(f"{base}:{instance}:{pid}")


def _safe_redis_url(url: str) -> str:
    if not url:
        return url
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, host = rest.split("@", 1)
        if ":" in creds:
            return f"{scheme}://***:***@{host}"
        return f"{scheme}://***@{host}"
    except Exception:
        return url


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


def _build_async_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
    shared: bool,
    client_name_kind: Optional[str] = None,
    **kwargs,
) -> AsyncRedis:
    max_connections = _resolve_max_connections(max_connections)
    options = {"decode_responses": decode_responses, **kwargs}
    if max_connections is not None:
        options["max_connections"] = max_connections
    options.setdefault("socket_connect_timeout", _DEFAULT_REDIS_CONNECT_TIMEOUT_SEC)
    options.setdefault("health_check_interval", _DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC)
    options.setdefault("socket_keepalive", _DEFAULT_REDIS_SOCKET_KEEPALIVE)
    options.setdefault("retry_on_timeout", True)
    options["client_name"] = options.get("client_name") or _build_client_name(
        client_name_kind or ("async_decode" if decode_responses else "async")
    )
    client = aioredis.from_url(redis_url, **options)
    try:
        setattr(client, "_kdcube_shared", shared)
    except Exception:
        pass
    logger.info(
        "Created %s async Redis client url=%s decode_responses=%s max_connections=%s client_name=%s",
        "shared" if shared else "dedicated",
        _safe_redis_url(redis_url),
        decode_responses,
        max_connections,
        options.get("client_name"),
    )
    return client


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
    return _build_async_client(
        redis_url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        shared=False,
        client_name_kind=client_name_kind,
        **kwargs,
    )


def get_async_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
) -> AsyncRedis:
    max_connections = _resolve_max_connections(max_connections)
    key = (redis_url, decode_responses, max_connections)
    client = _ASYNC_CLIENTS.get(key)
    if client is not None:
        return client
    client = _build_async_client(
        redis_url,
        decode_responses=decode_responses,
        max_connections=max_connections,
        shared=True,
    )
    _ASYNC_CLIENTS[key] = client
    return client


def get_sync_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    max_connections: Optional[int] = None,
) -> redis.Redis:
    max_connections = _resolve_max_connections(max_connections)
    key = (redis_url, decode_responses, max_connections)
    client = _SYNC_CLIENTS.get(key)
    if client is not None:
        return client

    kwargs = {"decode_responses": decode_responses}
    if max_connections is not None:
        kwargs["max_connections"] = max_connections
    kwargs.setdefault("socket_connect_timeout", _DEFAULT_REDIS_CONNECT_TIMEOUT_SEC)
    kwargs.setdefault("health_check_interval", _DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC)
    kwargs.setdefault("socket_keepalive", _DEFAULT_REDIS_SOCKET_KEEPALIVE)
    kwargs.setdefault("retry_on_timeout", True)
    kwargs["client_name"] = _build_client_name("sync_decode" if decode_responses else "sync")
    client = redis.Redis.from_url(redis_url, **kwargs)
    try:
        setattr(client, "_kdcube_shared", True)
    except Exception:
        pass
    _SYNC_CLIENTS[key] = client
    logger.info(
        "Created sync Redis client pool url=%s decode_responses=%s max_connections=%s client_name=%s",
        _safe_redis_url(redis_url),
        decode_responses,
        max_connections,
        kwargs.get("client_name"),
    )
    return client


async def close_async_redis_clients() -> None:
    for client in list(_ASYNC_CLIENTS.values()):
        try:
            await client.close()
        except Exception:
            logger.debug("Failed to close async Redis client", exc_info=True)
    _ASYNC_CLIENTS.clear()


def close_sync_redis_clients() -> None:
    for client in list(_SYNC_CLIENTS.values()):
        try:
            client.close()
        except Exception:
            logger.debug("Failed to close sync Redis client", exc_info=True)
    _SYNC_CLIENTS.clear()


async def close_all_redis_clients() -> None:
    await close_async_redis_clients()
    close_sync_redis_clients()


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
        interval = float(os.getenv("REDIS_HEALTHCHECK_INTERVAL_SEC", "5"))
        timeout = float(os.getenv("REDIS_HEALTHCHECK_TIMEOUT_SEC", "2"))
        monitor = RedisConnectionMonitor(redis_url, interval_sec=interval, timeout_sec=timeout)
        _MONITORS[redis_url] = monitor
    return monitor

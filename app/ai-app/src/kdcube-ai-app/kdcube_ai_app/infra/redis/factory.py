# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis client factory.

This module is intentionally additive for now. Runtime code can keep using the
existing helpers in ``infra.redis.client`` while we make Redis topology an
explicit factory concern before integrating cluster-aware behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


DEFAULT_REDIS_CONNECT_TIMEOUT_SEC = 5.0
DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC = 30
DEFAULT_REDIS_SOCKET_KEEPALIVE = True


class RedisTopology(str, Enum):
    """Redis deployment topology understood by the factory."""

    STANDALONE = "standalone"
    CLUSTER = "cluster"


class RedisClientRuntime(str, Enum):
    """Python client runtime shape."""

    ASYNC = "async"
    SYNC = "sync"


class RedisFactoryError(RuntimeError):
    """Base factory error."""


class RedisTopologyNotImplementedError(RedisFactoryError):
    """Raised when a declared topology is not wired in this build yet."""


def _sanitize_client_name(raw: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in {"-", "_", ":", "."}) else "_" for ch in raw)
    return safe[:128]


def build_redis_client_name(kind: str) -> str:
    pid = os.getpid()
    return _sanitize_client_name(f"kdcube:local:{pid}:{kind}")


def redis_client_name_prefix() -> str:
    pid = os.getpid()
    return _sanitize_client_name(f"kdcube:local:{pid}")


def safe_redis_url(url: str) -> str:
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


def normalize_redis_topology(value: RedisTopology | str | None) -> RedisTopology:
    raw = str(value.value if isinstance(value, RedisTopology) else (value or "")).strip().lower()
    if raw == RedisTopology.STANDALONE.value:
        return RedisTopology.STANDALONE
    if raw == RedisTopology.CLUSTER.value:
        return RedisTopology.CLUSTER
    raise RedisFactoryError(f"Unsupported Redis topology: {value!r}")


def normalize_redis_runtime(value: RedisClientRuntime | str) -> RedisClientRuntime:
    raw = str(value.value if isinstance(value, RedisClientRuntime) else value).strip().lower()
    return RedisClientRuntime(raw)


def _normalize_extra_options(extra_options: Iterable[Tuple[str, Any]] | None) -> Tuple[Tuple[str, Any], ...]:
    if not extra_options:
        return ()
    return tuple(sorted((str(k), v) for k, v in extra_options))


@dataclass(frozen=True)
class RedisClientRequest:
    """Immutable Redis client construction request.

    ``shared=True`` clients are cached by the factory. ``shared=False`` clients
    are always dedicated and suitable for blocking, pub/sub, or worker-local
    flows.
    """

    url: str
    runtime: RedisClientRuntime | str = RedisClientRuntime.ASYNC
    topology: RedisTopology | str = RedisTopology.STANDALONE
    decode_responses: bool = False
    max_connections: Optional[int] = None
    shared: bool = True
    client_name_kind: Optional[str] = None
    client_name: Optional[str] = None
    extra_options: Tuple[Tuple[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime", normalize_redis_runtime(self.runtime))
        object.__setattr__(self, "topology", normalize_redis_topology(self.topology))
        object.__setattr__(self, "extra_options", _normalize_extra_options(self.extra_options))


def _request_cache_key(request: RedisClientRequest) -> Tuple[Any, ...]:
    return (
        request.runtime.value,
        request.topology.value,
        request.url,
        request.decode_responses,
        request.max_connections,
        request.client_name_kind,
        request.client_name,
        tuple((k, repr(v)) for k, v in request.extra_options),
    )


def _set_shared_marker(client: Any, shared: bool) -> None:
    with contextlib.suppress(Exception):
        setattr(client, "_kdcube_shared", shared)


class RedisClientFactory:
    """Constructs Redis clients for the declared topology.

    The current implementation supports standalone Redis. Cluster mode is
    intentionally explicit and rejected until cross-slot key design is wired
    through the runtime.
    """

    def __init__(
        self,
        *,
        default_topology: RedisTopology | str | None = None,
        max_connections_resolver: Optional[Callable[[Optional[int]], Optional[int]]] = None,
    ) -> None:
        if default_topology is None:
            default_topology = RedisTopology.STANDALONE
        self.default_topology = normalize_redis_topology(default_topology)
        self._max_connections_resolver = max_connections_resolver or (lambda value: value)
        self._shared_async: Dict[Tuple[Any, ...], Any] = {}
        self._shared_sync: Dict[Tuple[Any, ...], Any] = {}

    def request(
        self,
        url: str,
        *,
        runtime: RedisClientRuntime | str = RedisClientRuntime.ASYNC,
        topology: RedisTopology | str | None = None,
        decode_responses: bool = False,
        max_connections: Optional[int] = None,
        shared: bool = True,
        client_name_kind: Optional[str] = None,
        client_name: Optional[str] = None,
        **extra_options: Any,
    ) -> RedisClientRequest:
        resolved_max_connections = self._max_connections_resolver(max_connections)
        return RedisClientRequest(
            url=url,
            runtime=runtime,
            topology=topology or self.default_topology,
            decode_responses=decode_responses,
            max_connections=resolved_max_connections,
            shared=shared,
            client_name_kind=client_name_kind,
            client_name=client_name,
            extra_options=tuple(extra_options.items()),
        )

    def client(self, request: RedisClientRequest) -> Any:
        if request.topology != RedisTopology.STANDALONE:
            raise RedisTopologyNotImplementedError(
                "Redis cluster topology is declared but the runtime is still using the standalone implementation"
            )

        if not request.shared:
            return self._open_standalone(request)

        cache = self._shared_async if request.runtime == RedisClientRuntime.ASYNC else self._shared_sync
        key = _request_cache_key(request)
        existing = cache.get(key)
        if existing is not None:
            return existing
        client = self._open_standalone(request)
        cache[key] = client
        return client

    def _open_standalone(self, request: RedisClientRequest) -> Any:
        options = self._client_options(request)
        if request.runtime == RedisClientRuntime.ASYNC:
            client = aioredis.from_url(request.url, **options)
        else:
            client = redis.Redis.from_url(request.url, **options)
        _set_shared_marker(client, request.shared)
        logger.info(
            "Created %s %s Redis client topology=%s url=%s decode_responses=%s max_connections=%s client_name=%s",
            "shared" if request.shared else "dedicated",
            request.runtime.value,
            request.topology.value,
            safe_redis_url(request.url),
            request.decode_responses,
            request.max_connections,
            options.get("client_name"),
        )
        return client

    def _client_options(self, request: RedisClientRequest) -> dict[str, Any]:
        options = dict(request.extra_options)
        options["decode_responses"] = request.decode_responses
        if request.max_connections is not None:
            options["max_connections"] = request.max_connections
        options.setdefault("socket_connect_timeout", DEFAULT_REDIS_CONNECT_TIMEOUT_SEC)
        options.setdefault("health_check_interval", DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC)
        options.setdefault("socket_keepalive", DEFAULT_REDIS_SOCKET_KEEPALIVE)
        options.setdefault("retry_on_timeout", True)
        options["client_name"] = request.client_name or options.get("client_name") or build_redis_client_name(
            request.client_name_kind
            or (
                f"{request.runtime.value}_decode"
                if request.decode_responses
                else request.runtime.value
            )
        )
        return options

    async def close_async_clients(self) -> None:
        for client in list(self._shared_async.values()):
            try:
                await client.close()
            except Exception:
                logger.debug("Failed to close shared async Redis client", exc_info=True)
        self._shared_async.clear()

    def close_sync_clients(self) -> None:
        for client in list(self._shared_sync.values()):
            try:
                client.close()
            except Exception:
                logger.debug("Failed to close shared sync Redis client", exc_info=True)
        self._shared_sync.clear()

    async def close(self) -> None:
        await self.close_async_clients()
        self.close_sync_clients()


_DEFAULT_FACTORY: RedisClientFactory | None = None


def get_redis_client_factory() -> RedisClientFactory:
    global _DEFAULT_FACTORY
    if _DEFAULT_FACTORY is None:
        _DEFAULT_FACTORY = RedisClientFactory()
    return _DEFAULT_FACTORY


async def close_redis_client_factory() -> None:
    global _DEFAULT_FACTORY
    if _DEFAULT_FACTORY is not None:
        await _DEFAULT_FACTORY.close()
        _DEFAULT_FACTORY = None

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/cache.py

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from redis import asyncio as aioredis
from redis.asyncio import Redis

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.namespaces import ns_key, REDIS

logger = logging.getLogger(__name__)

DEFAULT_FAVICON_TTL_SECONDS = int(os.getenv("FAVICON_CACHE_TTL_SECONDS", "86400"))
DEFAULT_KV_TTL_SECONDS = int(os.getenv("KV_CACHE_TTL_SECONDS", "3600"))


@dataclass(frozen=True)
class KVCacheConfig:
    redis_url: str
    default_ttl_seconds: int = DEFAULT_KV_TTL_SECONDS
    decode_responses: bool = True

    def as_dict(self) -> dict:
        return {
            "redis_url": self.redis_url,
            "default_ttl_seconds": int(self.default_ttl_seconds),
            "decode_responses": bool(self.decode_responses),
        }


class KVCache:
    def __init__(
        self,
        redis: Redis,
        *,
        default_ttl_seconds: int = DEFAULT_KV_TTL_SECONDS,
        config: Optional[KVCacheConfig] = None,
    ) -> None:
        self.redis = redis
        self.default_ttl_seconds = int(default_ttl_seconds)
        self._config = config

    def to_config(self) -> KVCacheConfig:
        if self._config:
            return self._config
        return KVCacheConfig(
            redis_url="",
            default_ttl_seconds=self.default_ttl_seconds,
            decode_responses=True,
        )

    def _key(self, key: str) -> str:
        return key

    async def get(self, key: str) -> Optional[str]:
        try:
            return await self.redis.get(self._key(key))
        except Exception:
            return None

    async def set(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> bool:
        if value is None:
            return False
        ttl = self.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        try:
            if ttl > 0:
                return bool(await self.redis.set(self._key(key), value, ex=ttl))
            return bool(await self.redis.set(self._key(key), value))
        except Exception:
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> bool:
        try:
            raw = json.dumps(value, ensure_ascii=True)
        except Exception:
            return False
        return await self.set(key, raw, ttl_seconds=ttl_seconds)

    async def mget(self, keys: Iterable[str]) -> list[Optional[str]]:
        keys_list = [self._key(k) for k in keys if k]
        if not keys_list:
            return []
        try:
            return await self.redis.mget(keys_list)
        except Exception:
            return [None for _ in keys_list]

    async def mget_json(self, keys: Iterable[str]) -> list[Optional[Any]]:
        items = await self.mget(keys)
        out: list[Optional[Any]] = []
        for raw in items:
            if raw is None:
                out.append(None)
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                out.append(None)
        return out

    async def set_many_json(self, mapping: dict[str, Any], *, ttl_seconds: Optional[int] = None) -> int:
        if not mapping:
            return 0
        ttl = self.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        pipe = self.redis.pipeline()
        for key, value in mapping.items():
            try:
                raw = json.dumps(value, ensure_ascii=True)
            except Exception:
                continue
            if ttl > 0:
                pipe.set(self._key(key), raw, ex=ttl)
            else:
                pipe.set(self._key(key), raw)
        try:
            res = await pipe.execute()
            return sum(1 for r in res if r)
        except Exception:
            return 0


@dataclass(frozen=True)
class NamespacedKVCacheConfig:
    redis_url: str
    namespace: str
    tenant: Optional[str] = None
    project: Optional[str] = None
    default_ttl_seconds: int = DEFAULT_FAVICON_TTL_SECONDS
    decode_responses: bool = True
    use_tp_prefix: bool = True

    def as_dict(self) -> dict:
        return {
            "redis_url": self.redis_url,
            "namespace": self.namespace,
            "tenant": self.tenant,
            "project": self.project,
            "default_ttl_seconds": int(self.default_ttl_seconds),
            "decode_responses": bool(self.decode_responses),
            "use_tp_prefix": bool(self.use_tp_prefix),
        }


class NamespacedKVCache(KVCache):
    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        default_ttl_seconds: int = DEFAULT_FAVICON_TTL_SECONDS,
        config: Optional[NamespacedKVCacheConfig] = None,
        use_tp_prefix: bool = True,
    ) -> None:
        super().__init__(redis, default_ttl_seconds=default_ttl_seconds, config=None)
        self.namespace = namespace
        self.tenant = tenant
        self.project = project
        self.use_tp_prefix = bool(use_tp_prefix)
        self._ns_config = config

    def to_config(self) -> NamespacedKVCacheConfig:
        if self._ns_config:
            return self._ns_config
        return NamespacedKVCacheConfig(
            redis_url="",
            namespace=self.namespace,
            tenant=self.tenant,
            project=self.project,
            default_ttl_seconds=self.default_ttl_seconds,
            decode_responses=True,
            use_tp_prefix=self.use_tp_prefix,
        )

    def _prefix(self) -> str:
        if not self.use_tp_prefix:
            return self.namespace
        return ns_key(self.namespace, tenant=self.tenant, project=self.project)

    def _key(self, key: str) -> str:
        return f"{self._prefix()}:{key}"


def build_default_favicon_cache_config() -> NamespacedKVCacheConfig:
    settings = get_settings()
    return NamespacedKVCacheConfig(
        redis_url=settings.REDIS_URL,
        namespace=REDIS.CACHE.FAVICON,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        default_ttl_seconds=DEFAULT_FAVICON_TTL_SECONDS,
        decode_responses=True,
        use_tp_prefix=True,
    )


def create_namespaced_kv_cache_from_config(config: NamespacedKVCacheConfig | dict | None) -> Optional[NamespacedKVCache]:
    if not config:
        return None
    if isinstance(config, dict):
        cfg = NamespacedKVCacheConfig(
            redis_url=str(config.get("redis_url") or ""),
            namespace=str(config.get("namespace") or REDIS.CACHE.FAVICON),
            tenant=config.get("tenant"),
            project=config.get("project"),
            default_ttl_seconds=int(config.get("default_ttl_seconds") or DEFAULT_FAVICON_TTL_SECONDS),
            decode_responses=bool(config.get("decode_responses", True)),
            use_tp_prefix=bool(config.get("use_tp_prefix", True)),
        )
    else:
        cfg = config
    if not cfg.redis_url:
        return None
    redis = aioredis.from_url(cfg.redis_url, decode_responses=cfg.decode_responses)
    return NamespacedKVCache(
        redis,
        namespace=cfg.namespace,
        tenant=cfg.tenant,
        project=cfg.project,
        default_ttl_seconds=cfg.default_ttl_seconds,
        config=cfg,
        use_tp_prefix=cfg.use_tp_prefix,
    )


def create_kv_cache_from_config(config: KVCacheConfig | dict | None) -> Optional[KVCache]:
    if not config:
        return None
    if isinstance(config, dict):
        cfg = KVCacheConfig(
            redis_url=str(config.get("redis_url") or ""),
            default_ttl_seconds=int(config.get("default_ttl_seconds") or DEFAULT_KV_TTL_SECONDS),
            decode_responses=bool(config.get("decode_responses", True)),
        )
    else:
        cfg = config
    if not cfg.redis_url:
        return None
    redis = aioredis.from_url(cfg.redis_url, decode_responses=cfg.decode_responses)
    return KVCache(
        redis,
        default_ttl_seconds=cfg.default_ttl_seconds,
        config=cfg,
    )


def create_kv_cache() -> Optional[KVCache]:
    settings = get_settings()
    if not settings.REDIS_URL:
        return None
    cfg = KVCacheConfig(
        redis_url=settings.REDIS_URL,
        default_ttl_seconds=DEFAULT_KV_TTL_SECONDS,
        decode_responses=True,
    )
    return create_kv_cache_from_config(cfg)


def create_namespaced_kv_cache(
        *,
        namespace: str,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        default_ttl_seconds: Optional[int] = None,
        use_tp_prefix: bool = True,
) -> Optional[NamespacedKVCache]:
    base = create_kv_cache()
    if base is None:
        return None
    return as_namespaced_cache(
        base,
        namespace=namespace,
        tenant=tenant,
        project=project,
        default_ttl_seconds=default_ttl_seconds,
        use_tp_prefix=use_tp_prefix,
    )


def create_kv_cache_from_env(*, ttl_env_var: str = "KV_CACHE_TTL_SECONDS") -> Optional[KVCache]:
    """
    Build a raw KV cache from environment variables.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    ttl = int(os.environ.get(ttl_env_var, DEFAULT_KV_TTL_SECONDS))
    cfg = KVCacheConfig(
        redis_url=redis_url,
        default_ttl_seconds=ttl,
        decode_responses=True,
    )
    return create_kv_cache_from_config(cfg)


def as_namespaced_cache(
        cache: Any,
        *,
        namespace: str,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        default_ttl_seconds: Optional[int] = None,
        use_tp_prefix: bool = True,
) -> Optional[NamespacedKVCache]:
    if cache is None:
        return None
    if isinstance(cache, NamespacedKVCache):
        return cache
    redis = None
    if isinstance(cache, KVCache):
        redis = cache.redis
        if default_ttl_seconds is None:
            default_ttl_seconds = cache.default_ttl_seconds
    elif isinstance(cache, Redis):
        redis = cache
    if redis is None:
        return None
    ttl = default_ttl_seconds if default_ttl_seconds is not None else DEFAULT_KV_TTL_SECONDS
    cfg = NamespacedKVCacheConfig(
        redis_url="",
        namespace=namespace,
        tenant=tenant,
        project=project,
        default_ttl_seconds=ttl,
        decode_responses=True,
        use_tp_prefix=use_tp_prefix,
    )
    return NamespacedKVCache(
        redis,
        namespace=namespace,
        tenant=tenant,
        project=project,
        default_ttl_seconds=ttl,
        config=cfg,
        use_tp_prefix=use_tp_prefix,
    )


def ensure_namespaced_cache(
        cache: Any,
        *,
        namespace: str,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        default_ttl_seconds: Optional[int] = None,
        use_tp_prefix: bool = True,
) -> Optional[NamespacedKVCache]:
    return as_namespaced_cache(
        cache,
        namespace=namespace,
        tenant=tenant,
        project=project,
        default_ttl_seconds=default_ttl_seconds,
        use_tp_prefix=use_tp_prefix,
    )

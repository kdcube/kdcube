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


@dataclass(frozen=True)
class NamespacedKVCacheConfig:
    redis_url: str
    namespace: str
    tenant: Optional[str] = None
    project: Optional[str] = None
    default_ttl_seconds: int = DEFAULT_FAVICON_TTL_SECONDS
    decode_responses: bool = True

    def as_dict(self) -> dict:
        return {
            "redis_url": self.redis_url,
            "namespace": self.namespace,
            "tenant": self.tenant,
            "project": self.project,
            "default_ttl_seconds": int(self.default_ttl_seconds),
            "decode_responses": bool(self.decode_responses),
        }


class NamespacedKVCache:
    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        default_ttl_seconds: int = DEFAULT_FAVICON_TTL_SECONDS,
        config: Optional[NamespacedKVCacheConfig] = None,
    ) -> None:
        self.redis = redis
        self.namespace = namespace
        self.tenant = tenant
        self.project = project
        self.default_ttl_seconds = int(default_ttl_seconds)
        self._config = config

    def to_config(self) -> NamespacedKVCacheConfig:
        if self._config:
            return self._config
        return NamespacedKVCacheConfig(
            redis_url="",
            namespace=self.namespace,
            tenant=self.tenant,
            project=self.project,
            default_ttl_seconds=self.default_ttl_seconds,
            decode_responses=True,
        )

    def _prefix(self) -> str:
        return ns_key(self.namespace, tenant=self.tenant, project=self.project)

    def _key(self, key: str) -> str:
        return f"{self._prefix()}:{key}"

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
        raw_list = await self.mget(keys)
        out: list[Optional[Any]] = []
        for raw in raw_list:
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


def build_default_favicon_cache_config() -> NamespacedKVCacheConfig:
    settings = get_settings()
    return NamespacedKVCacheConfig(
        redis_url=settings.REDIS_URL,
        namespace=REDIS.CACHE.FAVICON,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        default_ttl_seconds=DEFAULT_FAVICON_TTL_SECONDS,
        decode_responses=True,
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
    )


def create_namespaced_kv_cache() -> Optional[NamespacedKVCache]:
    try:
        cfg = build_default_favicon_cache_config()
    except Exception:
        logger.exception("Failed to build default favicon cache config")
        return None
    return create_namespaced_kv_cache_from_config(cfg)

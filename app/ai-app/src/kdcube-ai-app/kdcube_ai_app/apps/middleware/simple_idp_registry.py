# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.config_scopes import SIMPLE_IDP_STORE_PATH
from kdcube_ai_app.infra.redis.client import get_async_redis_client
from kdcube_ai_app.storage.observed_redis_locks import observed_redis_lock_async

logger = logging.getLogger(__name__)


DEFAULT_SIMPLE_IDP_USERS: Dict[str, Dict[str, Any]] = {
    "test-admin-token-123": {
        "sub": "admin-user-1",
        "username": "admin",
        "email": "admin@test.com",
        "name": "Administrator",
        "roles": ["kdcube:role:super-admin"],
        "permissions": [
            "kdcube:*:knowledge_base:*;read;write;delete",
            "kdcube:*:chat:*;read;write;delete",
            "kdcube:*:monitoring:*;read",
        ],
    },
    "test-chat-token-456": {
        "sub": "chat-user-1",
        "username": "chatuser",
        "email": "chat@test.com",
        "name": "Chat User",
        "roles": ["kdcube:role:registered"],
        "permissions": [
            "kdcube:*:chat:*;read",
        ],
    },
}


def _default_idp_path() -> str:
    return get_settings().AUTH.IDP.local.IDP_DB_PATH or SIMPLE_IDP_STORE_PATH


def _copy_users(users: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(token): dict(user) for token, user in users.items()}


class SimpleIDPRegistry:
    """
    Cached, cluster-safe registry for SimpleIDP token records.

    SimpleIDP is intentionally file-backed for local and embedded deployments.
    Reads are cached in-process to avoid repeated slow filesystem reads. Writes
    go through a Redis lock when available, with an advisory file lock fallback.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        default_users: Optional[Mapping[str, Mapping[str, Any]]] = None,
        redis_url: Optional[str] = None,
        cache_ttl_seconds: float = 5.0,
        version_check_seconds: float = 1.0,
        lock_ttl_seconds: int = 15,
        lock_wait_seconds: int = 5,
    ):
        self.path = Path(path).expanduser()
        self.default_users = _copy_users(DEFAULT_SIMPLE_IDP_USERS if default_users is None else default_users)
        self.redis_url = redis_url if redis_url is not None else (get_settings().REDIS_URL or "")
        self.cache_ttl_seconds = max(0.1, float(cache_ttl_seconds))
        self.version_check_seconds = max(0.1, float(version_check_seconds))
        self.lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self.lock_wait_seconds = max(1, int(lock_wait_seconds))

        digest = hashlib.sha256(str(self.path.absolute()).encode("utf-8")).hexdigest()[:24]
        self._redis_prefix = f"kdcube:simple_idp:{digest}"
        self._cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._cache_loaded_at = 0.0
        self._last_version_check_at = 0.0
        self._observed_version: Optional[str] = None
        self._redis_unavailable_until = 0.0
        self._process_lock = asyncio.Lock()

    @property
    def version_key(self) -> str:
        return f"{self._redis_prefix}:version"

    @property
    def write_lock_key(self) -> str:
        return f"{self._redis_prefix}:write"

    async def get_user(self, token: str) -> Optional[Dict[str, Any]]:
        users = await self.load_users()
        user = users.get(token)
        return copy.deepcopy(user) if user is not None else None

    async def load_users(self, *, force: bool = False) -> Dict[str, Dict[str, Any]]:
        async with self._process_lock:
            if not force and self._cache is not None and not await self._needs_reload_locked():
                return copy.deepcopy(self._cache)

            users = await asyncio.to_thread(self._read_or_initialize)
            self._cache = users
            self._cache_loaded_at = time.monotonic()
            self._last_version_check_at = self._cache_loaded_at
            self._observed_version = await self._get_remote_version()
            logger.debug(
                "SimpleIDP registry loaded path=%s users=%s version=%s",
                self.path,
                len(users),
                self._observed_version,
            )
            return copy.deepcopy(users)

    async def invalidate(self, *, publish: bool = False) -> None:
        async with self._process_lock:
            self._cache = None
            self._cache_loaded_at = 0.0
            self._last_version_check_at = 0.0
            if publish:
                version = await self._bump_remote_version()
                if version is not None:
                    self._observed_version = version

    async def upsert_user(self, token: str, user_data: Mapping[str, Any]) -> Dict[str, Any]:
        if not token:
            raise ValueError("SimpleIDP token must not be empty")
        clean_user = dict(user_data)
        if "username" not in clean_user or not clean_user.get("username"):
            clean_user["username"] = clean_user.get("sub") or token

        users: Dict[str, Dict[str, Any]]
        redis = await self._redis_or_none(verify=True)
        if redis is not None:
            try:
                async with observed_redis_lock_async(
                    client=redis,
                    key=self.write_lock_key,
                    metadata={
                        "op": "simple_idp.register",
                        "pid": os.getpid(),
                        "path": str(self.path),
                        "token_hint": token[:16],
                        "ts": time.time(),
                    },
                    ttl_seconds=self.lock_ttl_seconds,
                    wait_seconds=self.lock_wait_seconds,
                    poll_seconds=0.1,
                ):
                    users = await asyncio.to_thread(self._upsert_with_file_lock, token, clean_user)
            except Exception:
                logger.warning(
                    "SimpleIDP Redis write lock unavailable; falling back to file lock path=%s",
                    self.path,
                    exc_info=True,
                )
                users = await asyncio.to_thread(self._upsert_with_file_lock, token, clean_user)
        else:
            users = await asyncio.to_thread(self._upsert_with_file_lock, token, clean_user)

        version = await self._bump_remote_version()
        async with self._process_lock:
            self._cache = users
            self._cache_loaded_at = time.monotonic()
            self._last_version_check_at = self._cache_loaded_at
            if version is not None:
                self._observed_version = version
        logger.info(
            "SimpleIDP user registered path=%s token_hint=%s user=%s roles=%s",
            self.path,
            token[:16],
            clean_user.get("username") or clean_user.get("sub"),
            len(clean_user.get("roles") or []),
        )
        return copy.deepcopy(clean_user)

    async def remove_user(self, token: str) -> bool:
        if not token:
            return False
        redis = await self._redis_or_none(verify=True)
        if redis is not None:
            try:
                async with observed_redis_lock_async(
                    client=redis,
                    key=self.write_lock_key,
                    metadata={
                        "op": "simple_idp.remove",
                        "pid": os.getpid(),
                        "path": str(self.path),
                        "token_hint": token[:16],
                        "ts": time.time(),
                    },
                    ttl_seconds=self.lock_ttl_seconds,
                    wait_seconds=self.lock_wait_seconds,
                    poll_seconds=0.1,
                ):
                    users, removed = await asyncio.to_thread(self._remove_with_file_lock, token)
            except Exception:
                logger.warning(
                    "SimpleIDP Redis write lock unavailable for remove; falling back to file lock path=%s",
                    self.path,
                    exc_info=True,
                )
                users, removed = await asyncio.to_thread(self._remove_with_file_lock, token)
        else:
            users, removed = await asyncio.to_thread(self._remove_with_file_lock, token)
        version = await self._bump_remote_version()
        async with self._process_lock:
            self._cache = users
            self._cache_loaded_at = time.monotonic()
            self._last_version_check_at = self._cache_loaded_at
            if version is not None:
                self._observed_version = version
        return removed

    def list_users_sync(self) -> Dict[str, Dict[str, Any]]:
        if self._cache is not None:
            return copy.deepcopy(self._cache)
        users = self._read_or_initialize()
        self._cache = users
        self._cache_loaded_at = time.monotonic()
        return copy.deepcopy(users)

    def upsert_user_sync(self, token: str, user_data: Mapping[str, Any]) -> Dict[str, Any]:
        users = self._upsert_with_file_lock(token, dict(user_data))
        self._cache = users
        self._cache_loaded_at = time.monotonic()
        return copy.deepcopy(users[token])

    def remove_user_sync(self, token: str) -> bool:
        users, removed = self._remove_with_file_lock(token)
        self._cache = users
        self._cache_loaded_at = time.monotonic()
        return removed

    async def _needs_reload_locked(self) -> bool:
        now = time.monotonic()
        if now - self._cache_loaded_at >= self.cache_ttl_seconds:
            return True
        if now - self._last_version_check_at < self.version_check_seconds:
            return False
        self._last_version_check_at = now
        version = await self._get_remote_version()
        if version is None:
            return False
        if self._observed_version is None:
            self._observed_version = version
            return False
        if version != self._observed_version:
            logger.info(
                "SimpleIDP registry version changed; reloading path=%s old=%s new=%s",
                self.path,
                self._observed_version,
                version,
            )
            self._observed_version = version
            return True
        return False

    async def _redis_or_none(self, *, verify: bool = False) -> Any | None:
        if not self.redis_url or time.monotonic() < self._redis_unavailable_until:
            return None
        try:
            client = get_async_redis_client(self.redis_url, decode_responses=True)
            if verify:
                await asyncio.wait_for(client.ping(), timeout=0.5)
            return client
        except Exception:
            self._mark_redis_unavailable()
            return None

    async def _get_remote_version(self) -> Optional[str]:
        redis = await self._redis_or_none()
        if redis is None:
            return None
        try:
            value = await asyncio.wait_for(redis.get(self.version_key), timeout=0.5)
            return str(value) if value is not None else None
        except Exception:
            self._mark_redis_unavailable()
            logger.debug("SimpleIDP Redis version read failed", exc_info=True)
            return None

    async def _bump_remote_version(self) -> Optional[str]:
        redis = await self._redis_or_none()
        if redis is None:
            return None
        try:
            version = await asyncio.wait_for(redis.incr(self.version_key), timeout=0.5)
            return str(version)
        except Exception:
            self._mark_redis_unavailable()
            logger.debug("SimpleIDP Redis version bump failed", exc_info=True)
            return None

    def _mark_redis_unavailable(self) -> None:
        self._redis_unavailable_until = time.monotonic() + 30.0

    def _read_or_initialize(self) -> Dict[str, Dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return _copy_users(data)
                logger.warning("SimpleIDP registry is not a JSON object: %s", self.path)
            except Exception:
                logger.exception("Failed to read SimpleIDP registry: %s", self.path)

        users = copy.deepcopy(self.default_users)
        self._write_atomic(users)
        return users

    def _upsert_with_file_lock(self, token: str, user_data: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        def mutate(users: Dict[str, Dict[str, Any]]) -> None:
            users[token] = dict(user_data)

        return self._with_file_lock(mutate)

    def _remove_with_file_lock(self, token: str) -> tuple[Dict[str, Dict[str, Any]], bool]:
        removed = False

        def mutate(users: Dict[str, Dict[str, Any]]) -> None:
            nonlocal removed
            removed = token in users
            users.pop(token, None)

        users = self._with_file_lock(mutate)
        return users, removed

    def _with_file_lock(
        self,
        mutate: Callable[[Dict[str, Dict[str, Any]]], None],
    ) -> Dict[str, Dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                users = self._read_or_initialize()
                mutate(users)
                self._write_atomic(users)
                return users
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _write_atomic(self, users: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(_copy_users(users), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, self.path)


_REGISTRIES: Dict[str, SimpleIDPRegistry] = {}


def get_simple_idp_registry(
    path: str | os.PathLike[str] | None = None,
    *,
    default_users: Optional[Mapping[str, Mapping[str, Any]]] = None,
    redis_url: Optional[str] = None,
) -> SimpleIDPRegistry:
    resolved_path = str(Path(path or _default_idp_path()).expanduser())
    registry = _REGISTRIES.get(resolved_path)
    if registry is None:
        registry = SimpleIDPRegistry(
            resolved_path,
            default_users=DEFAULT_SIMPLE_IDP_USERS if default_users is None else default_users,
            redis_url=redis_url,
        )
        _REGISTRIES[resolved_path] = registry
    return registry


async def register_simple_idp_user(
    token: str,
    user_data: Mapping[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    registry = get_simple_idp_registry(path)
    return await registry.upsert_user(token, user_data)


async def invalidate_simple_idp_registry(*, path: str | os.PathLike[str] | None = None) -> None:
    registry = get_simple_idp_registry(path)
    await registry.invalidate()

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Iterable

_SECRET_CACHE_TTL_SECONDS = 120.0
# Entry: (expires_at, value, fingerprint). A fingerprinted entry (file-backed secret, W670) is valid exactly
# while its source file's fingerprint is unchanged, independent of the TTL.
_SECRET_VALUE_CACHE: dict[tuple[str, ...], tuple[float, str | None, tuple | None]] = {}
_PLAIN_VALUE_CACHE: dict[tuple[str, int, int, str], Any] = {}


def clear_secret_cache(
    *,
    tenant: str | None = None,
    project: str | None = None,
    bundle_id: str | None = None,
    user_id: str | None = None,
    key: str | None = None,
    keys: Iterable[str] | None = None,
) -> int:
    """Clear central process-local secret lookup cache entries."""
    tenant_filter = str(tenant or "").strip()
    project_filter = str(project or "").strip()
    bundle_filter = str(bundle_id or "").strip()
    user_filter = str(user_id or "").strip()
    key_filter = {str(candidate).strip() for candidate in (keys or []) if str(candidate).strip()}
    if key:
        key_filter.add(str(key).strip())

    if not tenant_filter and not project_filter and not bundle_filter and not user_filter and not key_filter:
        cleared = len(_SECRET_VALUE_CACHE)
        _SECRET_VALUE_CACHE.clear()
        return cleared

    def _provider_matches(cache_key: tuple[str, ...]) -> bool:
        _scope, cache_tenant, cache_project, secret_key = cache_key
        if tenant_filter and cache_tenant != tenant_filter:
            return False
        if project_filter and cache_project != project_filter:
            return False
        if bundle_filter and not secret_key.startswith(f"bundles.{bundle_filter}.secrets."):
            return False
        if user_filter:
            return False
        if key_filter and not bundle_filter and secret_key not in key_filter:
            return False
        return True

    def _user_matches(cache_key: tuple[str, ...]) -> bool:
        _scope, cache_tenant, cache_project, cache_user, cache_bundle, secret_tail = cache_key
        full_key = (
            f"users.{cache_user}.bundles.{cache_bundle}.secrets.{secret_tail}"
            if cache_bundle
            else f"users.{cache_user}.secrets.{secret_tail}"
        )
        if tenant_filter and cache_tenant != tenant_filter:
            return False
        if project_filter and cache_project != project_filter:
            return False
        if bundle_filter and cache_bundle != bundle_filter:
            return False
        if user_filter and cache_user != user_filter:
            return False
        if key_filter and not bundle_filter and not user_filter and secret_tail not in key_filter and full_key not in key_filter:
            return False
        return True

    to_delete: list[tuple[str, ...]] = []
    for cache_key in _SECRET_VALUE_CACHE:
        if not cache_key:
            continue
        if cache_key[0] == "provider" and _provider_matches(cache_key):
            to_delete.append(cache_key)
        elif cache_key[0] == "user" and _user_matches(cache_key):
            to_delete.append(cache_key)
    for cache_key in to_delete:
        _SECRET_VALUE_CACHE.pop(cache_key, None)
    return len(to_delete)


def get_secret_cache(cache_key: tuple[str, ...], *, fingerprint: tuple | None = None) -> tuple[bool, str | None]:
    cached = _SECRET_VALUE_CACHE.get(cache_key)
    if cached is None:
        return False, None
    expires_at, value, cached_fingerprint = cached
    if fingerprint is not None:
        if cached_fingerprint == fingerprint:
            return True, value
    elif cached_fingerprint is None and expires_at > time.monotonic():
        return True, value
    _SECRET_VALUE_CACHE.pop(cache_key, None)
    return False, None


def set_secret_cache(
    cache_key: tuple[str, ...], value: str | None, *, fingerprint: tuple | None = None,
) -> str | None:
    resolved = value or None
    if fingerprint is not None and resolved is None:
        # A file-backed miss is never cached (W670). Other backends may cache a miss: the
        # bundles.secrets.update invalidation clears it, with the TTL as the safety net (operator: "i think if
        # we have cache invalidation then not found can be cached").
        _SECRET_VALUE_CACHE.pop(cache_key, None)
        return None
    _SECRET_VALUE_CACHE[cache_key] = (time.monotonic() + _SECRET_CACHE_TTL_SECONDS, resolved, fingerprint)
    return resolved


def drop_secret_cache(cache_key: tuple[str, ...]) -> None:
    _SECRET_VALUE_CACHE.pop(cache_key, None)


def file_fingerprint(path: Any) -> tuple | None:
    """(inode, size, mtime_ns, ctime_ns) of the file itself (not a symlink target), plus (inode, mode, owner) of
    every folder above it; None when absent.

    A direct read refuses a record whose private folder chain became broad, changed owner or was replaced
    (W670 review R/U-CACHE-1: a warm entry still answered after a secrets folder became 0755). Any such
    change to a folder changes this fingerprint, so the cached value is dropped and the next get re-reads
    through the store's own checks. A folder's mtime is left out: writing a sibling secret changes it.
    """
    import os

    try:
        info = os.lstat(path)
        folders = []
        folder = os.path.dirname(os.path.abspath(os.fspath(path)))
        while True:
            state = os.lstat(folder)
            folders.append((state.st_ino, state.st_mode, state.st_uid))
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    except (OSError, TypeError, ValueError):
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, tuple(folders))


def clear_plain_cache() -> int:
    cleared = len(_PLAIN_VALUE_CACHE)
    _PLAIN_VALUE_CACHE.clear()
    return cleared


def get_plain_cache(
    *,
    path: str,
    mtime_ns: int,
    size: int,
    dotted_path: str,
    loader: Callable[[], Any],
) -> Any:
    cache_key = (str(path), int(mtime_ns), int(size), str(dotted_path or ""))
    if cache_key in _PLAIN_VALUE_CACHE:
        return _PLAIN_VALUE_CACHE[cache_key]
    value = loader()
    _PLAIN_VALUE_CACHE[cache_key] = value
    return value


def clear_config_cache() -> int:
    return clear_secret_cache() + clear_plain_cache()

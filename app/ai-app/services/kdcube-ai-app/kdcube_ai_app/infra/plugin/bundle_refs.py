# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_refs.py

from __future__ import annotations
import os
import time
from typing import Optional, Iterable, Set, Dict, Any

import kdcube_ai_app.infra.namespaces as namespaces

_LOCAL_ACTIVE: Dict[str, float] = {}


def _tp_from_env() -> tuple[str, str]:
    tenant = os.getenv("DEFAULT_TENANT") or os.getenv("TENANT_ID") or "default-tenant"
    project = os.getenv("DEFAULT_PROJECT_NAME") or os.getenv("CHAT_WEB_APP_PROJECT") or "default-project"
    return tenant, project


def refs_key(tenant: Optional[str] = None, project: Optional[str] = None) -> str:
    t, p = tenant, project
    if not t or not p:
        t2, p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    return namespaces.CONFIG.BUNDLES.ACTIVE_REFS_KEY_FMT.format(tenant=t, project=p)


def _ttl_seconds() -> int:
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        return int(get_settings().BUNDLE_REF_TTL_SECONDS)
    except Exception:
        return int(os.environ.get("BUNDLE_REF_TTL_SECONDS", "3600") or "3600")


def _touch_local(path: str) -> None:
    if not path:
        return
    _LOCAL_ACTIVE[path] = time.time()


def get_local_active_paths(*, ttl_seconds: Optional[int] = None) -> Set[str]:
    ttl = _ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    now = time.time()
    if ttl > 0:
        for p, ts in list(_LOCAL_ACTIVE.items()):
            if (now - ts) > ttl:
                _LOCAL_ACTIVE.pop(p, None)
    return set(_LOCAL_ACTIVE.keys())


async def touch_bundle_ref(
    redis: Any,
    *,
    path: str,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> None:
    if not path:
        return
    _touch_local(path)
    if redis is None:
        return
    ttl = _ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    key = refs_key(tenant, project)
    ts = time.time()
    try:
        await redis.zadd(key, {path: ts})
        if ttl > 0:
            await redis.expire(key, ttl * 2)
    except Exception:
        # best-effort tracking only
        return


async def get_active_paths(
    redis: Any,
    *,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Set[str]:
    ttl = _ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    now = time.time()
    active = set(get_local_active_paths(ttl_seconds=ttl))
    if redis is None:
        return active
    key = refs_key(tenant, project)
    try:
        if ttl > 0:
            cutoff = now - ttl
            items = await redis.zrangebyscore(key, cutoff, "+inf")
        else:
            items = await redis.zrange(key, 0, -1)
        if items:
            if isinstance(items[0], bytes):
                items = [i.decode("utf-8") for i in items]
            active.update([i for i in items if isinstance(i, str) and i])
    except Exception:
        pass
    return active

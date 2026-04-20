# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from kdcube_ai_app.apps.chat.ingress.resolvers import auth_without_pressure


router = APIRouter()


def _get_redis():
    redis = getattr(router.state.middleware, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis client not initialized")
    return redis


def _decode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(_decode_value(k)): _decode_value(v) for k, v in value.items()}
    if isinstance(value, (list, set, tuple)):
        return [_decode_value(v) for v in value]
    return value


async def _key_type(redis, key: str) -> str:
    raw = await redis.type(key)
    return _decode_value(raw) or "unknown"


@router.get("/keys")
async def list_keys(
    prefix: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    cursor: int = Query(default=0, ge=0),
    session=Depends(auth_without_pressure()),
):
    redis = _get_redis()
    match = f"{prefix}*" if prefix else None
    next_cursor = cursor
    keys: List[str] = []

    while True:
        next_cursor, batch = await redis.scan(cursor=next_cursor, match=match, count=limit)
        keys.extend([_decode_value(k) for k in batch])
        if len(keys) >= limit or next_cursor == 0:
            break

    keys = keys[:limit]
    items = []
    for key in keys:
        ktype = await _key_type(redis, key)
        ttl = await redis.ttl(key)
        items.append({"key": key, "type": ktype, "ttl": ttl})

    return {"items": items, "next_cursor": next_cursor}


@router.get("/key")
async def get_key(
    key: str = Query(...),
    max_items: int = Query(default=200, ge=1, le=5000),
    session=Depends(auth_without_pressure()),
):
    redis = _get_redis()
    if not key:
        raise HTTPException(status_code=400, detail="Missing key")

    ktype = await _key_type(redis, key)
    ttl = await redis.ttl(key)
    length = None
    value: Any = None

    if ktype == "string":
        value = _decode_value(await redis.get(key))
        length = len(value) if isinstance(value, str) else None
    elif ktype == "hash":
        value = _decode_value(await redis.hgetall(key))
        length = await redis.hlen(key)
    elif ktype == "list":
        value = _decode_value(await redis.lrange(key, 0, max_items - 1))
        length = await redis.llen(key)
    elif ktype == "set":
        cursor = 0
        items: List[Any] = []
        while True:
            cursor, batch = await redis.sscan(key, cursor=cursor, count=max_items)
            items.extend(_decode_value(batch))
            if len(items) >= max_items or cursor == 0:
                break
        value = items[:max_items]
        length = await redis.scard(key)
    elif ktype == "zset":
        value = _decode_value(await redis.zrange(key, 0, max_items - 1, withscores=True))
        length = await redis.zcard(key)
    elif ktype == "stream":
        value = _decode_value(await redis.xrange(key, count=max_items))
        length = await redis.xlen(key)
    else:
        value = None

    return {
        "key": key,
        "type": ktype,
        "ttl": ttl,
        "length": length,
        "value": value,
    }


@router.delete("/key")
async def delete_key(
    key: str = Query(...),
    session=Depends(auth_without_pressure()),
):
    redis = _get_redis()
    if not key:
        raise HTTPException(status_code=400, detail="Missing key")

    try:
        deleted = await redis.delete(key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete key: {exc}") from exc

    return {
        "status": "ok",
        "key": key,
        "deleted": int(deleted or 0),
    }

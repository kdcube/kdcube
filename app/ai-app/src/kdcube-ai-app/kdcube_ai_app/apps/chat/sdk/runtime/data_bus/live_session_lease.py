# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Bounded transport leases for live Data Bus routing metadata.

A lease proves recent transport liveness, not delegated authority. Publishers
still resolve the current Card and every parent Control before delivery.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

DEFAULT_TRANSPORT_LEASE_SECONDS = 75


def transport_lease_seconds(*, ping_interval: float, ping_timeout: float) -> int:
    """Allow three transport cycles, including the timeout/grace period."""
    values = (float(ping_interval), float(ping_timeout))
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Socket heartbeat intervals must be finite and positive.")
    return max(1, math.ceil(3 * max(values)))


def lease_deadline(*, now: int, credential_expires_at: int, lease_seconds: int) -> int:
    """Cap recent transport evidence at the already-admitted credential expiry."""
    if lease_seconds <= 0:
        raise ValueError("A live transport lease must have a positive duration.")
    if credential_expires_at <= now:
        raise ValueError("An expired credential cannot acquire a transport lease.")
    return min(credential_expires_at, now + lease_seconds)


def routing_member(*, session_id: str, socket_id: str) -> str:
    return json.dumps(
        {"session_id": str(session_id), "socket_id": str(socket_id)},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )


# Writers supply keys derived by the scoped registry, never client-selected keys.
# Redis runs each script atomically. A registration generation prevents a late
# old connection's renewal or disconnect from changing a newer registration.
REGISTER_LEASE = """
-- data_bus.register_transport_lease.v1
local socket_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if socket_type ~= 'none' and socket_type ~= 'string' then return 0 end
if index_type ~= 'none' and index_type ~= 'zset' then return 0 end
redis.call('SETEX', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[5])
return 1
"""

RENEW_LEASE = """
-- data_bus.renew_transport_lease.v1
local socket_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if socket_type ~= 'string' then return 0 end
if index_type ~= 'none' and index_type ~= 'zset' then return 0 end
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, record = pcall(cjson.decode, raw)
if not ok or type(record) ~= 'table' then return 0 end
if record.registration_id ~= ARGV[1] then return 0 end
local credential_expiry = tonumber(record.expires_at)
if not credential_expiry or credential_expiry <= tonumber(ARGV[2]) then return 0 end
if record.lease_index_key ~= KEYS[2] or record.lease_member ~= ARGV[3] then return 0 end
local expiry = math.min(credential_expiry, tonumber(ARGV[4]))
local ttl = expiry - tonumber(ARGV[2])
if ttl <= 0 then return 0 end
record.lease_expires_at = expiry
redis.call('SETEX', KEYS[1], ttl, cjson.encode(record))
redis.call('ZADD', KEYS[2], expiry, ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[5])
return 1
"""

RELEASE_LEASE = """
-- data_bus.release_transport_lease.v1
local socket_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if socket_type ~= 'string' then return 0 end
if index_type ~= 'none' and index_type ~= 'zset' then return 0 end
local raw = redis.call('GET', KEYS[1])
if not raw or raw ~= ARGV[1] then return 0 end
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('DEL', KEYS[1])
return 1
"""

PRUNE_MISSING_MEMBER = """
-- data_bus.prune_missing_transport_member.v1
if redis.call('EXISTS', KEYS[1]) ~= 0 then return 0 end
if redis.call('TYPE', KEYS[2]).ok ~= 'zset' then return 0 end
return redis.call('ZREM', KEYS[2], ARGV[1])
"""


async def register_lease(
    redis: Any, *, socket_key: str, index_key: str, member: str,
    record: Mapping[str, Any], now: int, lease_seconds: int, index_ttl: int,
) -> int:
    expiry = lease_deadline(
        now=now, credential_expires_at=int(record["expires_at"]),
        lease_seconds=lease_seconds,
    )
    value = dict(record)
    value.update(lease_expires_at=expiry, lease_index_key=index_key, lease_member=member)
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    applied = await redis.eval(
        REGISTER_LEASE, 2, socket_key, index_key, expiry - now, raw, expiry,
        member, max(index_ttl, lease_seconds),
    )
    if not applied:
        raise ValueError("Live transport routing keys have incompatible types.")
    return expiry


async def renew_lease(
    redis: Any, *, socket_key: str, index_key: str, member: str,
    registration_id: str, now: int, lease_seconds: int, index_ttl: int,
) -> bool:
    if lease_seconds <= 0:
        raise ValueError("A live transport lease must have a positive duration.")
    return bool(await redis.eval(
        RENEW_LEASE, 2, socket_key, index_key, registration_id, now, member,
        now + lease_seconds, max(index_ttl, lease_seconds),
    ))


async def release_lease(
    redis: Any, *, socket_key: str, index_key: str, member: str, raw_record: str | bytes,
) -> bool:
    return bool(await redis.eval(
        RELEASE_LEASE, 2, socket_key, index_key, raw_record, member,
    ))


async def prune_missing_member(
    redis: Any, *, socket_key: str, index_key: str, member: str,
) -> bool:
    return bool(await redis.eval(
        PRUNE_MISSING_MEMBER, 2, socket_key, index_key, member,
    ))

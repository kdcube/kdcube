"""Routing-script model for unit doubles; real Redis tests cover Lua itself."""
import json

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_session_lease import (
    REGISTER_LEASE, RENEW_LEASE, RELEASE_LEASE,
    PRUNE_MISSING_MEMBER, PRUNE_MISMATCHED_MEMBER,
)

LEASE_SCRIPTS = {REGISTER_LEASE, RENEW_LEASE, RELEASE_LEASE,
                 PRUNE_MISSING_MEMBER, PRUNE_MISMATCHED_MEMBER}


async def eval_transport_lease(redis, script, key_count, *args):
    assert key_count == 2
    socket, index = args[:2]
    values = args[2:]
    if script == REGISTER_LEASE:
        ttl, raw, expiry, member, index_ttl = values
        await redis.setex(socket, ttl, raw)
        await redis.zadd(index, {member: expiry})
        await redis.expire(index, index_ttl)
        return 1
    raw = await redis.get(socket)
    if script == RENEW_LEASE:
        generation, now, member, deadline, index_ttl = values
        if not raw:
            return 0
        record = json.loads(raw)
        if (record.get("registration_id") != generation
                or record["expires_at"] <= now
                or record.get("lease_index_key") != index
                or record.get("lease_member") != member):
            return 0
        expiry = min(record["expires_at"], deadline)
        record["lease_expires_at"] = expiry
        await redis.setex(socket, expiry - now, json.dumps(record))
        await redis.zadd(index, {member: expiry})
        await redis.expire(index, index_ttl)
        return 1
    if script == PRUNE_MISSING_MEMBER:
        if raw is not None:
            return 0
        await redis.zrem(index, values[0])
        return 1
    if raw is None or raw != values[0]:
        return 0
    await redis.zrem(index, values[1])
    if script == RELEASE_LEASE:
        await redis.delete(socket)
    return 1

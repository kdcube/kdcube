from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_session_lease import (
    REGISTER_LEASE,
    lease_deadline,
    register_lease,
    renew_lease,
    routing_member,
    transport_lease_seconds,
)


@pytest.mark.parametrize("credential_expiry,expected", [(2000000000, 1075), (1020, 1020)])
def test_long_credential_lifetime_does_not_become_transport_lifetime(credential_expiry, expected):
    assert lease_deadline(now=1000, credential_expires_at=credential_expiry, lease_seconds=75) == expected


@pytest.mark.parametrize("expiry", [999, 1000])
def test_expired_credentials_never_gain_one_extra_second(expiry):
    with pytest.raises(ValueError):
        lease_deadline(now=1000, credential_expires_at=expiry, lease_seconds=75)


@pytest.mark.parametrize("duration", [0, -1])
def test_nonpositive_duration_is_refused(duration):
    with pytest.raises(ValueError):
        lease_deadline(now=1000, credential_expires_at=2000, lease_seconds=duration)


@pytest.mark.parametrize("interval,timeout,expected", [(25, 20, 75), (10, 30, 90), (1.2, 1, 4)])
def test_transport_budget_covers_ping_and_timeout(interval, timeout, expected):
    assert transport_lease_seconds(ping_interval=interval, ping_timeout=timeout) == expected


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_transport_budget_is_refused(value):
    with pytest.raises(ValueError):
        transport_lease_seconds(ping_interval=value, ping_timeout=20)


@pytest.mark.asyncio
async def test_registration_uses_one_atomic_short_lease_and_score():
    redis = AsyncMock()
    member = routing_member(session_id="session-1", socket_id="sid-1")
    result = await register_lease(
        redis, socket_key="socket:sid-1", index_key="scope:principal:1", member=member,
        record={"expires_at": 2000000000, "registration_id": "generation-1"},
        now=1000, lease_seconds=75, index_ttl=3600,
    )
    assert result == 1075
    args = redis.eval.await_args.args
    assert args[:5] == (REGISTER_LEASE, 2, "socket:sid-1", "scope:principal:1", 75)
    record = json.loads(args[5])
    assert record["expires_at"] == 2000000000
    assert record["lease_expires_at"] == 1075
    assert record["lease_member"] == member
    assert args[6:] == (1075, member, 3600)
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_registration_performs_no_redis_write():
    redis = AsyncMock()
    with pytest.raises(ValueError):
        await register_lease(
            redis, socket_key="socket:sid", index_key="scope:principal:1", member="member",
            record={"expires_at": 1000}, now=1000, lease_seconds=75, index_ttl=3600,
        )
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_renewal_carries_exact_generation_and_binding():
    redis = AsyncMock()
    redis.eval.return_value = 0
    assert not await renew_lease(
        redis, socket_key="socket:sid", index_key="scope:principal:1", member="member",
        registration_id="old-generation", now=1000, lease_seconds=75, index_ttl=3600,
    )
    assert redis.eval.await_args.args[4:] == ("old-generation", 1000, "member", 1075, 3600)

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Live card resolution on the guard path, which has no durable source."""

from __future__ import annotations

import json
import time

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_io import (
    encode_cache_value,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_REVOKED,
    CardAuthority,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.live_grant import (
    LiveGrantCardError,
    live_grants_for_resource,
    resolve_live_grant_card,
)

TENANT = "tenant-a"
PROJECT = "project-a"
ACCESS_ID = "oauth-access-1"
RESOURCE = "https://runtime.example.test/mcp/productivity"
GRANTOR = "user-1"
CLIENT = "claude"
DELEGATE = "integration:claude:user-1"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_get = False

    async def get(self, key: str):
        if self.fail_get:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)


def _authority(
    *,
    operations=("sheets_read",),
    resource_grants=None,
    expires_at=None,
    state="active",
) -> CardAuthority:
    return CardAuthority(
        access_id=ACCESS_ID,
        label="Claude productivity",
        client_id=CLIENT,
        grantor_subject=GRANTOR,
        delegate_subject=DELEGATE,
        source="oauth",
        state=state,
        card_revision=1,
        operations=tuple(operations),
        resource_grants=(
            resource_grants
            if resource_grants is not None
            else {RESOURCE: ("sheets:read",)}
        ),
        named_service_operations=NamedServiceSelection.none(),
        expires_at=int(expires_at if expires_at is not None else time.time() + 3600),
    )


def _key() -> str:
    return DelegatedCardRuntimeCache(None, tenant=TENANT, project=PROJECT).card_key(ACCESS_ID)


def _projection(authority: CardAuthority) -> str:
    return encode_cache_value(
        {
            "kind": "card",
            "card_revision": authority.card_revision,
            "authority": authority.to_dict(),
        }
    )


async def _resolve(redis: _Redis):
    return await resolve_live_grant_card(
        redis,
        tenant=TENANT,
        project=PROJECT,
        access_id=ACCESS_ID,
        expected_client_id=CLIENT,
        expected_grantor_subject=GRANTOR,
        expected_delegate_subject=DELEGATE,
    )


@pytest.mark.asyncio
async def test_live_grant_resolves_current_valid_card():
    redis = _Redis()
    redis.values[_key()] = _projection(_authority())

    resolved = await _resolve(redis)

    assert resolved is not None
    assert resolved.operations == ("sheets_read",)
    assert resolved.resource_grants == {RESOURCE: ("sheets:read",)}


@pytest.mark.asyncio
async def test_live_grant_absent_expired_or_revoked_denies():
    redis = _Redis()
    assert await _resolve(redis) is None

    redis.values[_key()] = _projection(_authority(expires_at=int(time.time()) - 1))
    assert await _resolve(redis) is None

    redis.values[_key()] = _projection(_authority(state=CARD_STATE_REVOKED))
    assert await _resolve(redis) is None


@pytest.mark.asyncio
async def test_live_grant_revoked_tombstone_denies():
    redis = _Redis()
    redis.values[_key()] = encode_cache_value({"kind": "revoked", "card_revision": 2})

    assert await _resolve(redis) is None


@pytest.mark.asyncio
async def test_live_grant_updating_marker_fails_closed():
    redis = _Redis()
    redis.values[_key()] = encode_cache_value(
        {"kind": "updating", "mutation_id": "m1", "card_revision": 1}
    )

    with pytest.raises(LiveGrantCardError) as exc_info:
        await _resolve(redis)

    assert exc_info.value.reason == "card_updating"


@pytest.mark.asyncio
async def test_live_grant_lookup_failure_is_not_a_snapshot_fallback():
    redis = _Redis()
    redis.fail_get = True

    with pytest.raises(LiveGrantCardError, match="lookup_unavailable") as exc_info:
        await _resolve(redis)

    assert exc_info.value.reason == "lookup_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("{", "cached_card_not_decodable"),
        (json.dumps([]), "cached_card_not_decodable"),
        (json.dumps({"schema": "wrong"}), "cached_card_kind_unknown"),
        (
            json.dumps(
                {
                    "kind": "card",
                    "card_revision": 1,
                    "authority": {**_authority().to_dict(), "operations": "sheets_read"},
                }
            ),
            "cached_card_invalid",
        ),
    ],
)
async def test_live_grant_unusable_projection_is_unavailable_not_denial(payload, reason):
    """Damaged cache data must not read as a revoked card.

    The guard has no durable source, so it reports unavailability and the
    caller retries instead of being told to seek consent again.
    """
    redis = _Redis()
    redis.values[_key()] = payload

    with pytest.raises(LiveGrantCardError) as exc_info:
        await _resolve(redis)

    assert exc_info.value.reason == reason


@pytest.mark.asyncio
async def test_live_grant_binding_mismatch_fails_closed():
    redis = _Redis()
    authority = _authority()
    payload = json.loads(_projection(authority))
    payload["authority"]["client_id"] = "different-client"
    redis.values[_key()] = json.dumps(payload)

    with pytest.raises(LiveGrantCardError) as exc_info:
        await _resolve(redis)

    assert exc_info.value.reason == "client_id_mismatch"


def test_live_resource_grants_preserve_explicit_empty_narrowing():
    authority = _authority(resource_grants={RESOURCE: ()})

    assert live_grants_for_resource(authority, RESOURCE) == ()
    assert live_grants_for_resource(authority, "https://runtime.example.test/mcp/other") is None

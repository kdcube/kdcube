"""W342: a grantor profile demotion must narrow the next delegated call.

This disposable witness uses the real bundle-session signer, authenticator and
managed MCP guard with in-memory stores. It is not an external IdP, OAuth
issuance/refresh or live-host end-to-end test. The consent record and live Card
stay unchanged; only the grantor's current authoritative profile changes.

Run with ``--runxfail`` to expose the unresolved next-call contract as RED.
The separate revocation control proves the guard is not using the helper's
stub authenticator. No production authority or credentials are used.
"""
from __future__ import annotations

import asyncio
import secrets

import pytest

from kdcube_ai_app.auth import bundle
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.automation_access import (
    card_authority_from_record,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import surface_guard
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_surface_guard import (
    _projections_swept,  # noqa: F401: import the Card projection precondition
    _Redis,
    _authority,
    _client,
    _live_card,
    _rpc_tool_call,
    _store_card_authority,
)

PAID = "kdcube:role:paid"
REGISTERED = "kdcube:role:registered"
GRANTOR = "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"


@pytest.fixture
def signed_delegation(monkeypatch):
    """A valid delegated session, separate from its consenting grantor."""
    authority = bundle.BundleSessionAuthority(
        tenant="home", project="demo", redis=FakeRedis(),
        secret=secrets.token_bytes(32),
    )
    # Keep this fixture independent of any configured durable/live authority.
    monkeypatch.setattr(authority, "_active_authority_store", lambda: None)
    credential = _authority(grantor_subject=GRANTOR)
    delegated_subject = credential["subject"]

    async def issue():
        await authority.register_user(sub=GRANTOR, roles=[PAID])
        await authority.register_user(
            sub=delegated_subject,
            roles=["kdcube:role:delegated-client"],
            permissions=["kdcube:*:records:*;read"],
            provider="integration",
            metadata={"credential": credential},
        )
        return await authority.login(sub=delegated_subject)

    grant = asyncio.run(issue())
    card_redis = _Redis()
    _store_card_authority(card_redis, card_authority_from_record(_live_card()))
    authenticate = surface_guard._authenticate_delegated_client_access_token
    client = _client(
        monkeypatch,
        redis=card_redis,
        return_projection=True,
        grant_record={
            "registry_access_id": "oauth-access-1",
            "operations": ["records_export"],
            "credential": credential,
            "grantor_authority": {
                "grantor_roles": [PAID],
                "grantor_permissions": ["records:read"],
            },
        },
    )
    # _client normally stubs bearer validation; restore the production path.
    monkeypatch.setattr(
        surface_guard, "_authenticate_delegated_client_access_token", authenticate,
    )
    monkeypatch.setattr(bundle, "get_bundle_session_authority", lambda **_: authority)
    monkeypatch.setattr(surface_guard, "oauth_tenant_project", lambda *_a, **_k: ("home", "demo"))

    def next_call():
        # Keep the disposable bearer local; never print it or include it in evidence.
        return client.post(
            "/guard", json=_rpc_tool_call(),
            headers={"Authorization": "Bearer " + grant.token},
        )

    with client:
        yield authority, delegated_subject, next_call


def test_real_authenticator_refuses_the_revoked_delegated_subject(signed_delegation):
    authority, delegated_subject, next_call = signed_delegation
    first = next_call()
    assert first.status_code == 200
    assert first.json()["projection"]["roles"] == [PAID]

    asyncio.run(authority.invalidate_user(delegated_subject))
    assert next_call().status_code == 401


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="W342: the consent-time paid role survives current grantor profile demotion",
)
def test_next_call_no_longer_projects_the_demoted_grantors_paid_role(signed_delegation):
    authority, delegated_subject, next_call = signed_delegation
    assert delegated_subject != GRANTOR
    before = next_call()
    assert before.status_code == 200
    assert before.json()["projection"]["roles"] == [PAID]

    asyncio.run(authority.register_user(sub=GRANTOR, roles=[REGISTERED]))
    current_grantor = asyncio.run(authority.get_user(GRANTOR))
    assert current_grantor.roles == [REGISTERED]

    after = next_call()
    if after.status_code in (401, 403):
        return
    assert after.status_code == 200
    assert PAID not in after.json()["projection"]["roles"], (
        "W342 RED: the next managed call still projects the consent-time paid "
        "role after the grantor's authoritative profile became registered"
    )

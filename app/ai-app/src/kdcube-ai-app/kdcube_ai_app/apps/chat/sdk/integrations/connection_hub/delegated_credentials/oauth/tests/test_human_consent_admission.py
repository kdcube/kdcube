# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Human consent admission over verified sessions and bound/unbound grant rows.

The session authority and HTTP handlers are real; Redis is the existing unit
fixture. These tests make no PostgreSQL durability or live deployment claim.
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.grants import (
    mint_delegated_client_access_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import deps
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_authorize import (
    _params,
    client,
)


ROUTES = (
    ("GET", "/oauth/authorize"),
    ("GET", "/oauth/device"),
    ("GET", "/oauth/authorize/consent/draft"),
    ("POST", "/oauth/authorize/consent/decision"),
    ("POST", "/oauth/authorize/consent"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_lane", ["default", "override", "both-override"])
@pytest.mark.parametrize("bound", [False, True], ids=["unbound", "card-bound"])
@pytest.mark.parametrize("method,path", ROUTES)
async def test_integration_session_cannot_enter_human_consent(
    client, monkeypatch, auth_lane, bound, method, path,
):
    authority = BundleSessionAuthority(
        tenant="consent-test", project="consent-test", redis=FakeRedis(),
        secret="unit-session-signing-secret",
    )
    minted = await mint_delegated_client_access_token(
        "google:admin@example.test", ["records:read"],
        authority=authority, client_id="claude",
    )
    manager = BundleSessionAuthManager(authority=authority)

    async def authenticate(token):
        return (await manager.authenticate(token)).model_dump()

    app = client.app
    if auth_lane == "override":
        app.state.oauth_authenticate = authenticate
    else:
        del app.state.oauth_authenticate
        if auth_lane == "both-override":
            async def authenticate_with_both(token, id_token):
                return await authenticate(token)

            app.state.oauth_authenticate_with_both = authenticate_with_both
        else:
            import kdcube_ai_app.apps.chat.ingress.resolvers as resolvers
            import kdcube_ai_app.auth.bundle as bundle

            monkeypatch.setattr(bundle, "get_bundle_session_authority", lambda **_: authority)
            # The bundle manager must authenticate this real stateful token
            # first, without needing the interactive gateway's verifier.
            monkeypatch.setattr(resolvers, "create_auth_manager", lambda: object())
    store = app.state.oauth_grant_store
    if bound:
        await store.bind_access_grant(
            minted["access_token"], ["records_export"], 3600,
            registry_access_id="con_unit_card",
        )
    assert bool(await store.get_access_grant_record(minted["access_token"])) is bound
    before = dict(store.redis.values)
    mint_calls = []

    async def unexpected_mint(*args, **kwargs):
        mint_calls.append(True)
        raise AssertionError("human consent must refuse before minting")

    app.state.oauth_mint_access_token = unexpected_mint
    kwargs = {"headers": {"Authorization": "Bearer " + minted["access_token"]}}
    if method == "GET":
        kwargs["params"] = (
            _params() if path == "/oauth/authorize"
            else {"user_code": "BCDF-GHJK", "draft_id": "unit-draft"}
        )
    elif path.endswith("/decision"):
        kwargs["json"] = {"draft_id": "unit-draft", "decision": "approve"}
    else:
        kwargs["data"] = {**_params(), "decision": "approve"}
    response = client.request(method, path, **kwargs)
    assert response.status_code == 403
    assert response.json() == {"error": "oauth_human_consent_required"}
    assert store.redis.values == before
    assert mint_calls == []

    # The same bearer remains valid for delegated service authentication. This
    # guard is about who may consent, not global session validity or revocation.
    user = await manager.authenticate(minted["access_token"])
    assert user.sub.startswith("integration:")
    assert user.roles == ["kdcube:role:delegated-client"]
    assert user.permissions == ["records:read"]


def test_regular_human_still_reaches_consent(client):
    response = client.get(
        "/oauth/authorize", params=_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert response.status_code == 200
    assert "records_export" in response.text


@pytest.mark.parametrize("field", ["sub", "user_id", "id"])
def test_guard_checks_verified_identity_fields_not_display_name(field):
    assert deps.is_integration_consent_identity({field: "integration:client:grantor"})


def test_human_display_name_and_request_claims_are_not_identity_evidence():
    assert not deps.is_integration_consent_identity({
        "sub": "google:human", "username": "integration:display-name",
        "roles": ["kdcube:role:registered"],
    })


@pytest.mark.asyncio
async def test_trusted_authenticator_override_is_also_subject_to_consent_guard(client):
    async def authenticate(token):
        return {"sub": "integration:client:grantor", "roles": ["kdcube:role:super-admin"]}

    client.app.state.oauth_authenticate = authenticate
    response = client.get(
        "/oauth/authorize", params=_params(),
        headers={"Authorization": "Bearer synthetic-override-proof"},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "oauth_human_consent_required"}

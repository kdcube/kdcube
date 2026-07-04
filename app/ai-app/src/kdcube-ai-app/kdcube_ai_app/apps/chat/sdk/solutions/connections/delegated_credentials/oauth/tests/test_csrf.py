# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
CSRF protection for /oauth/authorize/consent.

The consent POST issues an authorization code and can be authenticated by the
admin's session cookie, so it must carry a synchronizer CSRF token bound to the
authenticated admin and minted at GET /oauth/authorize. Without it, a forged
cross-site POST (cookie auto-attached) could approve a consent for an attacker's
DCR-registered client + redirect_uri.
"""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import mount_test_oauth_adapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import enable_delegated_client

ISSUER = "https://connector.example.test"
CHALLENGE = make_s256_challenge("verifier-" + "x" * 50)
ADMIN = {"Authorization": "Bearer admin-tok"}


async def _authenticate(token):
    table = {
        "admin-tok": {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]},
        "admin2-tok": {"sub": "google:other-admin@example.test", "roles": ["kdcube:role:super-admin"]},
    }
    return table.get(token)


def _params(**over):
    p = {
        "client_id": "claude",
        "redirect_uri": "http://127.0.0.1:9876/callback",
        "response_type": "code",
        "scope": "records:read",
        "state": "st-1",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    p.update(over)
    return p


def _consent_form(csrf=None, decision="approve", **over):
    f = dict(_params(**over))
    f["decision"] = decision
    f["platform_grants"] = ["records:read"]
    f["tools"] = ["records_export"]
    if csrf is not None:
        f["csrf_token"] = csrf
    return f


@pytest.fixture
def client():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def _csrf_from_consent_page(client) -> str:
    r = client.get("/oauth/authorize", params=_params(), headers=ADMIN)
    assert r.status_code == 200
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    assert m, "consent page must embed a csrf_token hidden field"
    return m.group(1)


def test_consent_without_csrf_is_rejected(client):
    r = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf=None),
        headers=ADMIN, follow_redirects=False,
    )
    assert r.status_code == 403


def test_consent_with_forged_csrf_is_rejected(client):
    r = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf="not-a-real-token"),
        headers=ADMIN, follow_redirects=False,
    )
    assert r.status_code == 403


def test_consent_with_valid_csrf_succeeds_and_is_single_use(client):
    csrf = _csrf_from_consent_page(client)
    r = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf=csrf),
        headers=ADMIN, follow_redirects=False,
    )
    assert r.status_code == 302  # approved
    # Replay of the same token must fail (single-use).
    r2 = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf=csrf),
        headers=ADMIN, follow_redirects=False,
    )
    assert r2.status_code == 403


def test_csrf_token_is_bound_to_the_authenticated_admin(client):
    # A token minted for admin-tok must not be usable by a different admin.
    csrf = _csrf_from_consent_page(client)  # minted for google:admin@example.test
    r = client.post(
        "/oauth/authorize/consent", data=_consent_form(csrf=csrf),
        headers={"Authorization": "Bearer admin2-tok"}, follow_redirects=False,
    )
    assert r.status_code == 403

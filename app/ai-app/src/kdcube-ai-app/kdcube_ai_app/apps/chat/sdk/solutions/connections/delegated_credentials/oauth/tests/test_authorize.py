# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for /oauth/authorize: request validation, the granular consent screen,
and the authorization-code issuance on approval.

Pure validation/consent logic is tested directly; the routes are tested with a
fake session authenticator + fake-Redis-backed GrantStore injected on app.state.
"""
from __future__ import annotations

import urllib.parse as up

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import mount_test_oauth_adapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.flow import (
    AuthorizeError,
    build_redirect,
    parse_authorize_request,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.consent import render_consent_html
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import enable_delegated_client

ISSUER = "https://yey.boats"
CHALLENGE = make_s256_challenge("verifier-" + "x" * 50)


def _params(**over):
    p = {
        "client_id": "claude",
        "redirect_uri": "http://127.0.0.1:9876/callback",
        "response_type": "code",
        "scope": "conversations:read",
        "state": "st-123",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    p.update(over)
    return p


# ------------------------------ request validation ------------------------------

def test_parse_valid_request():
    req = parse_authorize_request(_params())
    assert req.client_id == "claude"
    assert req.redirect_uri == "http://127.0.0.1:9876/callback"
    assert req.scopes == ["conversations:read"]
    assert req.state == "st-123"
    assert req.code_challenge == CHALLENGE


def test_unknown_client_not_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(client_id="bogus"))
    assert ei.value.error == "invalid_client"
    assert ei.value.redirectable is False


def test_bad_redirect_not_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(redirect_uri="https://evil.example/cb"))
    assert ei.value.redirectable is False


def test_bad_response_type_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(response_type="token"))
    assert ei.value.error == "unsupported_response_type"
    assert ei.value.redirectable is True


def test_bad_scope_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(scope="conversations:write"))
    assert ei.value.error == "invalid_scope"
    assert ei.value.redirectable is True


def test_parse_resource_specific_scope():
    req = parse_authorize_request(
        _params(scope="memories:read", resource="https://runtime.example.test/public/mcp/memories"),
        supported_scopes=["memories:read"],
    )
    assert req.scopes == ["memories:read"]
    assert req.resource == "https://runtime.example.test/public/mcp/memories"


def test_missing_pkce_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(code_challenge=""))
    assert ei.value.error == "invalid_request"
    assert ei.value.redirectable is True


def test_non_s256_pkce_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        parse_authorize_request(_params(code_challenge_method="plain"))
    assert ei.value.error == "invalid_request"
    assert ei.value.redirectable is True


def test_build_redirect_appends_code_state_iss():
    url = build_redirect(
        "http://127.0.0.1:9876/callback",
        {"code": "abc", "state": "st-123", "iss": ISSUER},
    )
    q = dict(up.parse_qsl(up.urlsplit(url).query))
    assert q["code"] == "abc"
    assert q["state"] == "st-123"
    assert q["iss"] == ISSUER


# --------------------------------- consent UI ---------------------------------

def test_consent_html_lists_requested_tools_and_carries_state():
    req = parse_authorize_request(_params())
    html = render_consent_html(req, issuer=ISSUER)
    assert "conversations_export" in html       # selectable tool
    assert "conversations:read" in html         # requested scope
    assert 'value="st-123"' in html             # state carried in a hidden field
    assert "/oauth/authorize/consent" in html   # form posts to the consent endpoint
    assert "KDCube" in html                      # attribution


def test_consent_html_uses_configured_brand():
    req = parse_authorize_request(_params())

    branded = render_consent_html(req, issuer=ISSUER, brand="Acme AI")
    assert "Authorize an MCP connection to Acme AI" in branded   # <h1>
    assert "Authorize MCP connection · Acme AI" in branded       # <title>
    assert "Powered by" in branded and "KDCube" in branded       # attribution stays

    default = render_consent_html(req, issuer=ISSUER)
    assert "Authorize an MCP connection to KDCube" in default     # default brand


def test_consent_html_shows_platform_account_and_logout():
    req = parse_authorize_request(_params())
    html = render_consent_html(
        req,
        issuer=ISSUER,
        grantor_subject="02e53484-0081-70ce-11c1-e96706b1a182",
        grantor_label="elena@example.test",
        signout_action="/api/integrations/bundles/demo/demo/connection-hub@1-0/public/oauth/logout",
        return_to="/api/integrations/bundles/demo/demo/connection-hub@1-0/public/oauth/authorize?client_id=claude",
    )

    assert "KDCube account" in html
    assert "elena@example.test" in html
    assert "02e53484-0081-70ce-11c1-e96706b1a182" in html
    assert "Sign out of KDCube" in html
    assert "/public/oauth/logout" in html


# ----------------------------------- routes -----------------------------------

async def _fake_authenticate(token):
    table = {
        "admin-tok": {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]},
        "user-tok": {
            "sub": "google:user@example.test",
            "user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
            "roles": ["kdcube:role:chat-user"],
        },
    }
    return table.get(token)


@pytest.fixture
def client():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _fake_authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def test_authorize_unauthenticated_redirects_to_signin(client):
    # A browser hitting /oauth/authorize without a session must be sent to the
    # platform login (with a return-to) rather than getting a dead-end JSON 401.
    r = client.get("/oauth/authorize", params=_params(), follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/signin?next=")
    nxt = dict(up.parse_qsl(up.urlsplit(loc).query))["next"]
    # the full authorize request (path + multi-param query) is preserved url-encoded
    assert nxt.startswith("/oauth/authorize")
    assert "client_id=claude" in up.unquote(nxt)
    assert "code_challenge=" in up.unquote(nxt)


def test_authorize_rejects_user_without_delegable_grant(client):
    r = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer user-tok"})
    assert r.status_code == 403


def test_authorize_renders_consent_for_regular_user_when_grant_is_delegable(client):
    client.app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "memories:read",
                "label": "Read memories",
                "delegable_roles": ["kdcube:role:chat-user"],
            },
        ],
        "resources": [
            {
                "resource": "https://runtime.example.test/*/public/mcp/memories",
                "tools": {
                    "memory_search": {
                        "label": "Search memories",
                        "grants": ["memories:read"],
                    },
                },
            },
        ],
    }
    r = client.get(
        "/oauth/authorize",
        params=_params(
            scope="memories:read",
            resource="https://runtime.example.test/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
        ),
        headers={"Authorization": "Bearer user-tok"},
    )

    assert r.status_code == 200
    assert "memory_search" in r.text
    assert "memories:read" in r.text


def test_authorize_renders_consent_for_admin(client):
    r = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer admin-tok"})
    assert r.status_code == 200
    assert "conversations_export" in r.text
    assert "Sign out of KDCube" in r.text


def test_authorize_uses_id_token_when_browser_session_needs_it():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    mount_test_oauth_adapter(app)

    async def _auth_with_both(access_token, id_token):
        if access_token == "access-only" and id_token == "id-with-roles":
            return {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]}
        if access_token == "access-only":
            return {"sub": "google:admin@example.test", "roles": []}
        return None

    app.state.oauth_authenticate_with_both = _auth_with_both
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    c = TestClient(app)

    without_id = c.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer access-only"})
    assert without_id.status_code == 403

    with_id = c.get(
        "/oauth/authorize",
        params=_params(),
        headers={"Authorization": "Bearer access-only", "X-ID-Token": "id-with-roles"},
    )
    assert with_id.status_code == 200
    assert "conversations_export" in with_id.text


def test_authorize_unknown_client_is_400_not_redirect(client):
    r = client.get(
        "/oauth/authorize",
        params=_params(client_id="bogus"),
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert r.status_code == 400  # must NOT redirect to an unvalidated client


def _csrf_token(client, *, token: str = "admin-tok", params: dict | None = None) -> str:
    import re
    g = client.get("/oauth/authorize", params=params or _params(), headers={"Authorization": f"Bearer {token}"})
    assert g.status_code == 200
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', g.text).group(1)


def test_consent_approve_issues_code_bound_to_selection(client):
    store = client.app.state.oauth_grant_store
    form = _params()
    form["decision"] = "approve"
    form["platform_grants"] = ["conversations:read"]
    form["tools"] = ["conversations_export"]
    form["csrf_token"] = _csrf_token(client)
    r = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    q = dict(up.parse_qsl(up.urlsplit(loc).query))
    assert q["state"] == "st-123"
    assert q["iss"] == ISSUER
    code = q["code"]

    # Inspect the fake Redis directly (sync) to confirm the code is bound to the
    # consenting user + the selected tool, without re-entering the event loop.
    import json
    raw = store._r.values[store._key("code", code)]
    payload = json.loads(raw)
    assert payload["sub"] == "google:admin@example.test"
    assert payload["tools"] == ["conversations_export"]
    assert payload["scopes"] == ["conversations:read"]
    assert payload["delegation_edges"][0]["authority_id"] == "platform"
    assert payload["delegation_edges"][0]["grants"] == ["conversations:read"]


def test_consent_uses_platform_user_id_as_grantor_when_available(client):
    client.app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "memories:read",
                "label": "Read memories",
                "delegable_roles": ["kdcube:role:chat-user"],
            },
        ],
        "resources": [
            {
                "resource": "https://runtime.example.test/*/public/mcp/memories",
                "tools": {
                    "memory_search": {
                        "label": "Search memories",
                        "grants": ["memories:read"],
                    },
                },
            },
        ],
    }
    store = client.app.state.oauth_grant_store
    form = _params(
        scope="memories:read",
        resource="https://runtime.example.test/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
    )
    form["decision"] = "approve"
    form["platform_grants"] = ["memories:read"]
    form["tools"] = ["memory_search"]
    form["csrf_token"] = _csrf_token(client, token="user-tok", params=form)

    r = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer user-tok"},
        follow_redirects=False,
    )

    assert r.status_code == 302
    code = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))["code"]
    import json
    raw = store._r.values[store._key("code", code)]
    payload = json.loads(raw)
    assert payload["sub"] == "02e53484-0081-70ce-11c1-e96706b1a182"
    assert payload["tools"] == ["memory_search"]
    assert payload["scopes"] == ["memories:read"]
    assert payload["delegation_edges"][0]["identity_ref"] == "platform:02e53484-0081-70ce-11c1-e96706b1a182"


def test_consent_deny_redirects_with_error(client):
    form = _params()
    form["decision"] = "deny"
    form["csrf_token"] = _csrf_token(client)
    r = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    q = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))
    assert q["error"] == "access_denied"
    assert q["state"] == "st-123"

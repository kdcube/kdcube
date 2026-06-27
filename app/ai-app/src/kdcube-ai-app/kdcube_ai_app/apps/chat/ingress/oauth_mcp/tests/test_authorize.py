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

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.flow import (
    AuthorizeError,
    build_redirect,
    parse_authorize_request,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.consent import render_consent_html
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.store import GrantStore
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

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


# ----------------------------------- routes -----------------------------------

async def _fake_authenticate(token):
    table = {
        "admin-tok": {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]},
        "user-tok": {"sub": "google:user@example.test", "roles": ["kdcube:role:chat-user"]},
    }
    return table.get(token)


@pytest.fixture
def client():
    app = FastAPI()
    enable_oauth_mcp(app, issuer=ISSUER)
    mount_oauth_mcp(app)
    app.state.oauth_authenticate = _fake_authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def test_authorize_requires_authentication(client):
    r = client.get("/oauth/authorize", params=_params())
    assert r.status_code == 401


def test_authorize_rejects_non_admin(client):
    r = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer user-tok"})
    assert r.status_code == 403


def test_authorize_renders_consent_for_admin(client):
    r = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer admin-tok"})
    assert r.status_code == 200
    assert "conversations_export" in r.text


def test_authorize_unknown_client_is_400_not_redirect(client):
    r = client.get(
        "/oauth/authorize",
        params=_params(client_id="bogus"),
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert r.status_code == 400  # must NOT redirect to an unvalidated client


def _csrf_token(client) -> str:
    import re
    g = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer admin-tok"})
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', g.text).group(1)


def test_consent_approve_issues_code_bound_to_selection(client):
    store = client.app.state.oauth_grant_store
    form = _params()
    form["decision"] = "approve"
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
    # consenting admin + the selected tool, without re-entering the event loop.
    import json
    raw = store._r.values[store._key("code", code)]
    payload = json.loads(raw)
    assert payload["sub"] == "google:admin@example.test"
    assert payload["tools"] == ["conversations_export"]
    assert payload["scopes"] == ["conversations:read"]


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

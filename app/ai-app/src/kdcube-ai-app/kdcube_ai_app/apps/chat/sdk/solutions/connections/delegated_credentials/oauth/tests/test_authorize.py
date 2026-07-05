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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import enable_delegated_client
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http import routes as oauth_routes

ISSUER = "https://connector.example.test"
CHALLENGE = make_s256_challenge("verifier-" + "x" * 50)
SUPPORTED_SCOPES = ["records:read"]


def _params(**over):
    p = {
        "client_id": "claude",
        "redirect_uri": "http://127.0.0.1:9876/callback",
        "response_type": "code",
        "scope": "records:read",
        "state": "st-123",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    p.update(over)
    return p


def _parse(params):
    return parse_authorize_request(params, supported_scopes=SUPPORTED_SCOPES)


# ------------------------------ request validation ------------------------------

def test_parse_valid_request():
    req = _parse(_params())
    assert req.client_id == "claude"
    assert req.redirect_uri == "http://127.0.0.1:9876/callback"
    assert req.scopes == ["records:read"]
    assert req.state == "st-123"
    assert req.code_challenge == CHALLENGE


def test_unknown_client_not_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        _parse(_params(client_id="bogus"))
    assert ei.value.error == "invalid_client"
    assert ei.value.redirectable is False


def test_bad_redirect_not_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        _parse(_params(redirect_uri="https://evil.example/cb"))
    assert ei.value.redirectable is False


def test_bad_response_type_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        _parse(_params(response_type="token"))
    assert ei.value.error == "unsupported_response_type"
    assert ei.value.redirectable is True


def test_bad_scope_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        _parse(_params(scope="records:write"))
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
        _parse(_params(code_challenge=""))
    assert ei.value.error == "invalid_request"
    assert ei.value.redirectable is True


def test_non_s256_pkce_is_redirectable():
    with pytest.raises(AuthorizeError) as ei:
        _parse(_params(code_challenge_method="plain"))
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
    req = _parse(_params())
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    html = render_consent_html(req, issuer=ISSUER, config=oauth_delegated_config(app))
    assert "records_export" in html       # selectable tool
    assert "records:read" in html         # requested scope
    assert 'value="st-123"' in html             # state carried in a hidden field
    assert "/oauth/authorize/consent" in html   # form posts to the consent endpoint
    assert "KDCube" in html                      # attribution


def test_consent_html_uses_configured_brand():
    req = _parse(_params())

    branded = render_consent_html(req, issuer=ISSUER, brand="Acme AI")
    assert "Authorize an MCP connection to Acme AI" in branded   # <h1>
    assert "Authorize MCP connection · Acme AI" in branded       # <title>
    assert "Powered by" in branded and "KDCube" in branded       # attribution stays

    default = render_consent_html(req, issuer=ISSUER)
    assert "Authorize an MCP connection to KDCube" in default     # default brand


def test_consent_html_shows_platform_account_and_logout():
    req = _parse(_params())
    html = render_consent_html(
        req,
        issuer=ISSUER,
        grantor_subject="a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        grantor_label="elena@example.test",
        signout_action="/api/integrations/bundles/demo/demo/connection-hub@1-0/public/oauth/logout",
        return_to="/api/integrations/bundles/demo/demo/connection-hub@1-0/public/oauth/authorize?client_id=claude",
    )

    assert "KDCube account" in html
    assert "elena@example.test" in html
    assert "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d" in html
    assert "Sign out of KDCube" in html
    assert "/public/oauth/logout" in html


def test_consent_html_shows_named_service_namespace_operation_labels():
    app = FastAPI()
    resource = "https://runtime.example.test/public/mcp/named_services"
    app.state.oauth_delegated_config = {
        "enabled": True,
        "capabilities": [
            {"grant": "named_services:use", "label": "Use named services"},
            {"grant": "memories:read", "label": "Read memories"},
            {"grant": "memories:write", "label": "Write memories"},
        ],
        "resources": [
            {
                "resource": resource,
                "tools": {
                    "named_services_search": {"grants": ["named_services:use"]},
                    "named_services_upsert": {"grants": ["named_services:use"]},
                },
                "named_services": {
                    "namespaces": {
                        "mem": {
                            "label": "User memories",
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "label": "Search memories",
                                    "description": "Search memory notes.",
                                    "grants": ["memories:read"],
                                },
                                "upsert": {
                                    "operation": "object.upsert",
                                    "label": "Write memory",
                                    "description": "Create or update a memory note.",
                                    "grants": ["memories:write"],
                                },
                            },
                        },
                    },
                },
            },
        ],
    }
    req = parse_authorize_request(
        _params(scope="named_services:use memories:read memories:write", resource=resource),
        supported_scopes=oauth_delegated_config(app).supported_scopes(resource),
    )

    html = render_consent_html(req, issuer=ISSUER, config=oauth_delegated_config(app))

    assert "Named-service namespace boundaries" in html
    assert "User memories" in html
    assert "Search memories" in html
    assert "Write memory" in html
    assert "Create or update a memory note." in html


# ----------------------------------- routes -----------------------------------

async def _fake_authenticate(token):
    table = {
        "admin-tok": {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]},
        "user-tok": {
            "sub": "google:user@example.test",
            "user_id": "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "roles": ["kdcube:role:registered"],
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
    assert loc.startswith("/signin/?next=")
    nxt = dict(up.parse_qsl(up.urlsplit(loc).query))["next"]
    # the full authorize request (path + multi-param query) is preserved url-encoded
    assert nxt.startswith("/oauth/authorize")
    assert "client_id=claude" in up.unquote(nxt)
    assert "code_challenge=" in up.unquote(nxt)


def test_authorize_rejects_user_without_delegable_grant(client):
    r = client.get("/oauth/authorize", params=_params(), headers={"Authorization": "Bearer user-tok"})
    assert r.status_code == 403


def test_authorize_can_render_bundle_hosted_consent(client, monkeypatch):
    captured = {}
    client.app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "records:read",
                "label": "Read records",
                "delegable_roles": ["kdcube:role:super-admin"],
                "tools": [
                    {
                        "name": "records_export",
                        "label": "Export records",
                        "grants": ["records:read"],
                    },
                ],
            },
        ],
        "resources": [
            {
                "resource": "*",
                "grants": ["records:read"],
                "tools": {
                    "records_export": {
                        "label": "Export records",
                        "grants": ["records:read"],
                    },
                },
            },
        ],
        "consent_ui": {
            "host": {
                "bundle_id": "product@1-0",
                "route": "public",
                "operation": "delegated_consent",
            },
        },
    }

    async def fake_call_bundle_operation(**kwargs):
        captured.update(kwargs)
        assert kwargs["bundle_id"] == "product@1-0"
        assert kwargs["operation"] == "delegated_consent"
        assert kwargs["route"] == "public"
        data = kwargs["data"]
        assert data["csrf_token"]
        assert data["form_action"] == "/oauth/authorize/consent"
        assert data["request"]["client_id"] == "claude"
        assert data["oauth_request"]["client_id"] == "claude"
        assert data["platform_grants"][0]["grant"] == "records:read"
        assert data["tools"][0]["name"] == "records_export"
        return {"delegated_consent": {"html": "<html><body>Custom consent</body></html>"}}

    monkeypatch.setattr(oauth_routes, "call_bundle_operation", fake_call_bundle_operation)

    response = client.get(
        "/oauth/authorize",
        params=_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert response.status_code == 200
    assert "Custom consent" in response.text
    assert captured["http_method"] == "POST"


def test_authorize_renders_consent_for_regular_user_when_grant_is_delegable(client):
    client.app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "memories:read",
                "label": "Read memories",
                "delegable_roles": ["kdcube:role:registered"],
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
    assert "records_export" in r.text
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
    assert "records_export" in with_id.text


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
    form["platform_grants"] = ["records:read"]
    form["tools"] = ["records_export"]
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
    # consenting user + the selected operation, without re-entering the event loop.
    import json
    raw = store._r.values[store._key("code", code)]
    payload = json.loads(raw)
    assert payload["sub"] == "google:admin@example.test"
    assert payload["operations"] == ["records_export"]
    assert payload["scopes"] == ["records:read"]
    assert payload["delegation_edges"][0]["authority_id"] == "platform"
    assert payload["delegation_edges"][0]["grants"] == ["records:read"]


def test_consent_recovers_missing_authorize_fields_from_same_origin_referrer(client):
    form = _params()
    csrf = _csrf_token(client, params=form)
    referer = "http://testserver/oauth/authorize?" + up.urlencode(form)
    form.pop("client_id")
    form["decision"] = "approve"
    form["platform_grants"] = ["records:read"]
    form["tools"] = ["records_export"]
    form["csrf_token"] = csrf

    r = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer admin-tok", "Referer": referer},
        follow_redirects=False,
    )

    assert r.status_code == 302
    q = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))
    assert q["state"] == "st-123"
    assert q["iss"] == ISSUER
    assert q["code"]


def test_consent_recovers_blank_authorize_fields_from_same_origin_referrer(client):
    form = _params()
    csrf = _csrf_token(client, params=form)
    referer = "http://testserver/oauth/authorize?" + up.urlencode(form)
    form["client_id"] = ""
    form["decision"] = "approve"
    form["platform_grants"] = ["records:read"]
    form["tools"] = ["records_export"]
    form["csrf_token"] = csrf

    r = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer admin-tok", "Referer": referer},
        follow_redirects=False,
    )

    assert r.status_code == 302
    q = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))
    assert q["state"] == "st-123"
    assert q["iss"] == ISSUER
    assert q["code"]


def test_consent_uses_platform_user_id_as_grantor_when_available(client):
    client.app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "memories:read",
                "label": "Read memories",
                "delegable_roles": ["kdcube:role:registered"],
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
    assert payload["sub"] == "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert payload["operations"] == ["memory_search"]
    assert payload["scopes"] == ["memories:read"]
    assert payload["delegation_edges"][0]["identity_ref"] == "platform:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"


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

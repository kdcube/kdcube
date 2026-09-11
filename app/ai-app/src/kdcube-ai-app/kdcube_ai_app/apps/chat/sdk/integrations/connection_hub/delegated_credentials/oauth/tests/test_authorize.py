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

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    mount_test_oauth_adapter,
    publish_delegated_config,
)
from connection_hub.delegated_credentials.oauth.flow import (
    AuthorizeError,
    build_redirect,
    parse_authorize_request,
)
from connection_hub.delegated_credentials.oauth.clients import PublicClient
from connection_hub.delegated_credentials.oauth.consent import (
    CONSENT_CONTRACT_VERSION,
    named_service_selection_rows,
    render_consent_html,
    resource_selection_rows,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import oauth_delegated_config
from connection_hub.delegated_credentials.oauth.store import GrantStore
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import enable_delegated_client
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes as oauth_routes

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


def test_consent_html_lists_exact_owner_resource_operations():
    proxy = "https://connector.example.test/public/mcp/remote_mcp_proxy"
    connector = "urn:connection-hub:remote-mcp:mcp_0123456789abcdef01234567"
    app = FastAPI()
    publish_delegated_config(app, {
        "enabled": True,
        "capabilities": [
            {"grant": "external_mcp:use", "label": "Use external MCP"},
        ],
        "resources": [
            {
                "resource": proxy,
                "grants": ["external_mcp:use"],
                "resource_selection": True,
            },
            {
                "resource": connector,
                "label": "External MCP: Customer records",
                "grants": ["external_mcp:use"],
                "tools": {
                    "records.search": {
                        "label": "Search records",
                        "grants": ["external_mcp:use"],
                    },
                    "records.delete": {
                        "label": "Delete records",
                        "grants": ["external_mcp:use"],
                    },
                },
            },
        ],
    })
    cfg = oauth_delegated_config(app)
    req = parse_authorize_request(
        _params(scope="external_mcp:use", resource=proxy),
        supported_scopes=cfg.supported_scopes(proxy),
    )

    rows = resource_selection_rows(
        req.scopes,
        config=cfg,
        resource=proxy,
        seeded_operations={connector: ["records.search"]},
    )
    html = render_consent_html(
        req,
        issuer=ISSUER,
        config=cfg,
        seeded_resource_operations={connector: ["records.search"]},
    )

    assert rows[0]["resource"] == connector
    assert {
        item["name"]: item["held"] for item in rows[0]["operations"]
    } == {"records.search": True, "records.delete": False}
    assert "External MCP: Customer records" in html
    assert "Search records" in html
    assert 'name="resource_operations"' in html


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
    publish_delegated_config(app, {
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
    })
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




def _seeding_config(resource: str) -> dict:
    return {
        "enabled": True,
        "capabilities": [
            {"grant": "named_services:use", "label": "Use named services"},
            {"grant": "memories:read", "label": "Read memories"},
            {"grant": "memories:write", "label": "Write memories"},
        ],
        "resources": [
            {
                "resource": resource,
                "tools": {"named_services_call": {"grants": ["named_services:use"]}},
                "named_services": {
                    "namespaces": {
                        "mem": {
                            "label": "User memories",
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "label": "Search memories",
                                    "grants": ["memories:read"],
                                },
                                "upsert": {
                                    "operation": "object.upsert",
                                    "label": "Write memory",
                                    "grants": ["memories:write"],
                                },
                            },
                        },
                    },
                },
            },
        ],
    }


def _seeding_request(app, resource: str):
    return parse_authorize_request(
        _params(scope="named_services:use memories:read memories:write", resource=resource),
        supported_scopes=oauth_delegated_config(app).supported_scopes(resource),
    )


def _checkbox(html: str, operation: str) -> str:
    """The one input tag for this operation, so `checked` can be asserted on it."""
    marker = f'value="mem:{operation}"'
    start = html.index(marker)
    return html[html.rindex("<input", 0, start) : html.index(">", start) + 1]


def test_a_first_consent_seeds_nothing():
    app = FastAPI()
    resource = "https://runtime.example.test/public/mcp/named_services"
    publish_delegated_config(app, _seeding_config(resource))

    html = render_consent_html(
        _seeding_request(app, resource),
        issuer=ISSUER,
        config=oauth_delegated_config(app),
    )

    assert "Nothing is selected by default" in html
    assert "checked" not in _checkbox(html, "object.search")
    assert "checked" not in _checkbox(html, "object.upsert")


def test_a_re_consent_seeds_what_the_card_holds_and_separates_what_is_new():
    """A submission REPLACES, so an unseeded picker would drop the grant.

    The operation the card covers comes back checked; the one the catalog
    added since is offered apart and unchecked, because widening is a decision
    and not a default.
    """
    app = FastAPI()
    resource = "https://runtime.example.test/public/mcp/named_services"
    publish_delegated_config(app, _seeding_config(resource))

    html = render_consent_html(
        _seeding_request(app, resource),
        issuer=ISSUER,
        config=oauth_delegated_config(app),
        seeded_named_service_operations={"mem": ["object.search"]},
    )

    assert "checked" in _checkbox(html, "object.search")
    assert "checked" not in _checkbox(html, "object.upsert")
    assert "Named-service operations this client has now" in html
    assert "REPLACES" in html
    assert "Added since this client was last approved" in html
    assert "Nothing is selected by default" not in html


def test_the_view_model_carries_what_the_card_holds():
    """A custom renderer must be able to tell held from newly offered."""
    app = FastAPI()
    resource = "https://runtime.example.test/public/mcp/named_services"
    publish_delegated_config(app, _seeding_config(resource))

    rows = named_service_selection_rows(
        ["named_services:use", "memories:read", "memories:write"],
        config=oauth_delegated_config(app),
        resource=resource,
        seeded={"mem": ["object.search"]},
    )

    held = {row["operation"]: row["held"] for row in rows}
    assert held == {"object.search": True, "object.upsert": False}

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
    proxy = "https://connector.example.test/public/mcp/remote_mcp_proxy"
    connector = "urn:connection-hub:remote-mcp:mcp_0123456789abcdef01234567"
    static_config = {
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
                "resource": proxy,
                "grants": ["records:read"],
                "resource_selection": True,
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
    publish_delegated_config(client.app, static_config)

    owner_config_app = FastAPI()
    owner_config_app.state.oauth_delegated_config = {
        **static_config,
        "resources": [
            *static_config["resources"],
            {
                "resource": connector,
                "label": "External MCP: Customer records",
                "grants": ["records:read"],
                "tools": {
                    "records.search": {
                        "label": "Search records",
                        "grants": ["records:read"],
                    },
                },
            },
        ],
    }
    owner_config = oauth_delegated_config(owner_config_app)

    class OwnerAccess:
        async def oauth_consent_config(self, *, grantor_subject):
            assert grantor_subject == "google:admin@example.test"
            return owner_config

        async def oauth_seed_account_scope(self, **_kwargs):
            return {}

        async def oauth_seed_named_service_operations(self, **_kwargs):
            return {}

        async def oauth_seed_resource_operations(self, **_kwargs):
            return {}

    client.app.state.automation_access_factory = OwnerAccess

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
        # The renderer sees the whole view model, including the dimensions the
        # built-in page used to receive on its own.
        assert data["consent_contract"]["version"] == CONSENT_CONTRACT_VERSION
        assert data["resources"][0]["resource"] == connector
        assert data["resources"][0]["operations"][0]["name"] == "records.search"
        assert "named_service_operations" in data
        assert "connected_accounts" in data
        assert "seeded_account_scope" in data
        assert "seeded_resource_operations" in data
        return {
            "consent_contract_version": CONSENT_CONTRACT_VERSION,
            "delegated_consent": {"html": "<html><body>Custom consent</body></html>"},
        }

    monkeypatch.setattr(oauth_routes, "call_bundle_operation", fake_call_bundle_operation)

    response = client.get(
        "/oauth/authorize",
        params=_params(resource=proxy),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert response.status_code == 200
    assert "Custom consent" in response.text
    assert captured["http_method"] == "POST"


def test_a_renderer_that_does_not_state_the_contract_is_not_shown(client, monkeypatch):
    """A page that never rendered a dimension cannot report a choice about it.

    The custom renderer is presentation only, so it declares which view model it
    implements; an absent or older version is refused before the user can
    approve, instead of producing a card whose selection was never asked for.
    """
    publish_delegated_config(client.app, {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "records:read",
                "label": "Read records",
                "delegable_roles": ["kdcube:role:super-admin"],
            },
        ],
        "resources": [{"resource": "*", "grants": ["records:read"]}],
        "consent_ui": {
            "host": {
                "bundle_id": "product@1-0",
                "route": "public",
                "operation": "delegated_consent",
            },
        },
    })

    async def stale_renderer(**kwargs):
        return {"delegated_consent": {"html": "<html><body>Stale consent</body></html>"}}

    monkeypatch.setattr(oauth_routes, "call_bundle_operation", stale_renderer)

    response = client.get(
        "/oauth/authorize",
        params=_params(),
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert response.status_code == 500
    assert response.json()["error"] == "consent_ui_contract_mismatch"
    assert "Stale consent" not in response.text


def test_authorize_renders_consent_for_regular_user_when_grant_is_delegable(client):
    publish_delegated_config(client.app, {
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
    })
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


def _open_card_editor(client, monkeypatch, *, multi_resource: bool):
    resource = "https://runtime.example.test/public/mcp/worker_stream"
    config = oauth_delegated_config(client.app)

    class CardEditorAccess:
        async def oauth_consent_config(self, *, grantor_subject):
            assert grantor_subject == "google:admin@example.test"
            return config

        async def oauth_consent_card_seed(
            self, *, grantor_subject, client_id, resource, client_metadata
        ):
            assert grantor_subject == "google:admin@example.test"
            assert client_id == "claude"
            asserted = client_metadata.get("client_metadata") or {}
            full = asserted.get("kdcube_credential_use") == "multi_resource"
            return {
                "ok": True,
                "access_id": "oauth-card-1",
                "card_revision": 0,
                "catalog_scope": {
                    "mode": "full" if full else "entry",
                    "resources": [] if full else [resource],
                },
                "catalog_row_by_resource": {resource: "*"},
            }

        async def resolve_oauth_consent_authority(self, _user, **selection):
            return {
                "ok": True,
                "access_id": "oauth-card-1",
                "catalog_version": selection["expected_catalog_version"],
                "card_revision": selection["expected_card_revision"],
                "resource_grants": dict(selection["resource_grants"]),
                "resource_operations": dict(selection["resource_operations"]),
                "operations": sorted({
                    operation
                    for operations in selection["resource_operations"].values()
                    for operation in operations
                }),
                "named_service_operations": selection["named_service_operations"],
                "named_services": {},
                "account_scope": dict(selection["account_scope"]),
                "identity_scope": "grantor",
            }

    client.app.state.automation_access_factory = lambda: CardEditorAccess()
    monkeypatch.setattr(
        oauth_routes,
        "_connection_hub_widget_base",
        lambda _request: "https://runtime.example.test/widgets/connections_settings",
    )
    if multi_resource:
        public_client = PublicClient(
            client_id="claude",
            redirect_uris=("http://127.0.0.1/callback",),
            client_name="Connection Hub CLI",
            client_metadata={
                "kdcube_credential_use": "multi_resource",
                "kdcube_agent_id": "codex:session-1",
            },
        )
        monkeypatch.setattr(
            oauth_routes,
            "get_client",
            lambda client_id, _request=None: public_client if client_id == "claude" else None,
        )
    response = client.get(
        "/oauth/authorize",
        params=_params(resource=resource),
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = up.urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://runtime.example.test/widgets/connections_settings"
    )
    query = dict(up.parse_qsl(location.query))
    assert query["tab"] == "delegatedAccess"
    draft_id = query["oauth_consent"]
    assert len(draft_id) >= 32
    return resource, draft_id


def test_connection_hub_consent_opens_entry_bound_card_editor(client, monkeypatch):
    resource, draft_id = _open_card_editor(client, monkeypatch, multi_resource=False)

    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert draft.status_code == 200, draft.text
    payload = draft.json()
    assert payload["entry_door"]["resource"] == resource
    assert payload["catalog_scope"] == {"mode": "entry", "resources": [resource]}
    assert payload["selection"]["resource_grants"] == {resource: ["records:read"]}

    other_user = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer user-tok"},
    )
    assert other_user.status_code == 403
    assert other_user.json()["reason"] == "subject_mismatch"


def test_multi_resource_oauth_delivery_opens_full_card_editor(client, monkeypatch):
    resource, draft_id = _open_card_editor(client, monkeypatch, multi_resource=True)

    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert draft.status_code == 200, draft.text
    payload = draft.json()
    assert payload["entry_door"]["resource"] == resource
    assert payload["catalog_scope"] == {"mode": "full", "resources": []}
    assert payload["client"]["client_metadata"] == {
        "kdcube_credential_use": "multi_resource",
        "kdcube_agent_id": "codex:session-1",
    }
    assert payload["selection"]["label"].endswith("codex:session-1")


def test_multi_resource_card_decision_carries_full_selection_once(client, monkeypatch):
    import json

    entry, draft_id = _open_card_editor(client, monkeypatch, multi_resource=True)
    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    ).json()
    second = "https://runtime.example.test/public/mcp/another_service"
    decision = {
        "draft_id": draft_id,
        "decision": "approve",
        "label": "codex-main on dev-main",
        "resource_grants": {
            entry: ["records:read"],
            second: ["records:read"],
        },
        "resource_operations": {
            entry: ["records_export"],
            second: [],
        },
        "invocation_policies": {
            entry: {"records_export": "always"},
            second: {},
        },
        "named_service_operations": {},
        "account_scope": {},
        "expected_card_revision": draft["card_revision"],
        "expected_catalog_version": draft["catalog_version"],
    }

    approved = client.post(
        "/oauth/authorize/consent/decision",
        json=decision,
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert approved.status_code == 200, approved.text
    code = dict(up.parse_qsl(up.urlsplit(approved.json()["redirect_url"]).query))["code"]
    store = client.app.state.oauth_grant_store
    code_payload = json.loads(store._r.values[store._key("code", code)])
    assert code_payload["resource_grants"] == decision["resource_grants"]
    assert code_payload["resource_operations"] == decision["resource_operations"]
    assert code_payload["card_label"] == "codex-main on dev-main"
    assert code_payload["expected_card_revision"] == draft["card_revision"]
    assert code_payload["scopes"] == ["records:read"]

    replay = client.post(
        "/oauth/authorize/consent/decision",
        json=decision,
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert replay.status_code == 410


def _csrf_token(client, *, token: str = "admin-tok", params: dict | None = None) -> str:
    import re
    g = client.get("/oauth/authorize", params=params or _params(), headers={"Authorization": f"Bearer {token}"})
    assert g.status_code == 200
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', g.text).group(1)


def test_consent_approve_issues_code_bound_to_selection(client):
    store = client.app.state.oauth_grant_store
    form = _params()
    form["decision"] = "approve"
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
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


def test_oauth_consent_grants_an_authenticated_owners_exact_connector_tool(client):
    import json

    proxy = "https://connector.example.test/public/mcp/remote_mcp_proxy"
    connector = "urn:connection-hub:remote-mcp:mcp_0123456789abcdef01234567"
    static_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "external_mcp:use",
                "label": "Use external MCP",
                "delegable_roles": ["kdcube:role:registered"],
            },
        ],
        "resources": [
            {
                "resource": proxy,
                "label": "Connected external MCP tools",
                "grants": ["external_mcp:use"],
                "resource_selection": True,
            },
        ],
    }
    publish_delegated_config(client.app, static_config)
    owner_config_app = FastAPI()
    owner_config_app.state.oauth_delegated_config = {
        **static_config,
        "resources": [
            *static_config["resources"],
            {
                "resource": connector,
                "label": "External MCP: Customer records",
                "grants": ["external_mcp:use"],
                "tools": {
                    "records.search": {
                        "label": "Search records",
                        "grants": ["external_mcp:use"],
                    },
                    "records.delete": {
                        "label": "Delete records",
                        "grants": ["external_mcp:use"],
                    },
                },
            },
        ],
    }
    owner_config = oauth_delegated_config(owner_config_app)
    seen_subjects: list[str] = []

    class OwnerAccess:
        async def oauth_consent_config(self, *, grantor_subject):
            seen_subjects.append(grantor_subject)
            return owner_config

        async def oauth_seed_account_scope(self, **_kwargs):
            return {}

        async def oauth_seed_named_service_operations(self, **_kwargs):
            return {}

        async def oauth_seed_resource_operations(self, **_kwargs):
            return {}

    client.app.state.automation_access_factory = OwnerAccess
    params = _params(scope="external_mcp:use", resource=proxy)

    get_response = client.get(
        "/oauth/authorize",
        params=params,
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert get_response.status_code == 200
    assert "External MCP: Customer records" in get_response.text
    assert "Search records" in get_response.text

    form = dict(params)
    form.update({
        "decision": "approve",
        "consent_contract_version": CONSENT_CONTRACT_VERSION,
        "platform_grants": ["external_mcp:use"],
        "resource_operations": [json.dumps({
            "resource": connector,
            "operation": "records.search",
        })],
        "csrf_token": _csrf_token(client, params=params),
    })
    response = client.post(
        "/oauth/authorize/consent",
        data=form,
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    code = dict(up.parse_qsl(up.urlsplit(response.headers["location"]).query))["code"]
    store = client.app.state.oauth_grant_store
    payload = json.loads(store._r.values[store._key("code", code)])
    assert payload["resource_grants"] == {
        proxy: ["external_mcp:use"],
        connector: ["external_mcp:use"],
    }
    assert payload["resource_operations"] == {
        proxy: [],
        connector: ["records.search"],
    }
    assert payload["operations"] == ["records.search"]
    assert set(seen_subjects) == {"google:admin@example.test"}


def test_consent_recovers_missing_authorize_fields_from_same_origin_referrer(client):
    form = _params()
    csrf = _csrf_token(client, params=form)
    referer = "http://testserver/oauth/authorize?" + up.urlencode(form)
    form.pop("client_id")
    form["decision"] = "approve"
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
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
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
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
    publish_delegated_config(client.app, {
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
    })
    store = client.app.state.oauth_grant_store
    form = _params(
        scope="memories:read",
        resource="https://runtime.example.test/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
    )
    form["decision"] = "approve"
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
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


def test_a_submission_without_the_contract_version_issues_no_code(client):
    """An absent version means the page never rendered the current view model.

    Its silence about the operation selection is not a user's choice, so no
    authorization code is issued and no card is born.
    """
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

    assert r.status_code == 400
    assert r.json()["error"] == "consent_ui_contract_mismatch"


def test_the_consent_carries_the_operation_choice_into_the_code(client):
    """Claims and operations are independent dimensions, so the code carries
    both: the operator's operation selection travels to token exchange, where
    the card is born with it."""
    import json

    store = client.app.state.oauth_grant_store
    form = _params()
    form["decision"] = "approve"
    form["consent_contract_version"] = CONSENT_CONTRACT_VERSION
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
    code = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))["code"]
    payload = json.loads(store._r.values[store._key("code", code)])

    # Nothing offered, nothing selected: an explicit empty selection, not an
    # absent one.
    assert payload["named_service_operations"] == {}
    assert "catalog_version" in payload

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""HTTP contract tests for OAuth 2.0 Device Authorization Grant."""

from __future__ import annotations

import json
import urllib.parse as up
from typing import Any, Mapping

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from connection_hub.delegated_credentials.cards.identity import CARD_KIND_CONNECTOR
from connection_hub.delegated_credentials.oauth.device import (
    DEVICE_GRANT_TYPE,
    DEVICE_POLL_APPROVED,
    DeviceAuthorizationIssue,
    DevicePollResult,
    DeviceUserCodeResult,
)
from connection_hub.delegated_credentials.oauth.store import (
    GrantStore,
    GrantStoreUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import (
    routes as oauth_routes,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.device import (
    verification_uri,
    verification_uri_complete,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    enable_delegated_client,
    mount_test_oauth_adapter,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import (
    FakeRedis,
)


ISSUER = "https://connector.example.test"
RESOURCE = "https://connector.example.test/public/mcp/records"
ACCESS_ID = "con_device_card_1"
CARD_PROOF = "claude-card-last-refresh-token"


class CardContinuity:
    """Route seam for the Card's credential families (the Postgres lookup is
    covered by the Connection Hub package): which (last refresh token, client,
    Card) triples a machine can prove it held."""

    def __init__(self) -> None:
        self.held: set[tuple[str, str, str]] = set()
        self.asked: list[tuple[str, str, str]] = []

    def hold(self, refresh_token: str, client_id: str, access_id: str) -> None:
        self.held.add((refresh_token, client_id, access_id))

    async def proven(self, *, refresh_token: str, client_id: str, access_id: str) -> bool:
        self.asked.append((refresh_token, client_id, access_id))
        return (refresh_token, client_id, access_id) in self.held


def test_device_verification_uri_accepts_origin_or_mounted_oauth_issuer():
    mounted_issuer = f"{ISSUER}/public/oauth"

    assert verification_uri(ISSUER) == f"{ISSUER}/oauth/device"
    assert verification_uri(mounted_issuer) == f"{mounted_issuer}/device"
    assert verification_uri_complete(mounted_issuer, "BCDF-GHJK") == (
        f"{mounted_issuer}/device?user_code=BCDF-GHJK"
    )


async def _authenticate(token: str):
    if token == "admin-tok":
        return {
            "sub": "google:admin@example.test",
            "roles": ["kdcube:role:super-admin"],
        }
    return None


class MemoryDeviceStore:
    """Route seam; atomic Redis behavior is covered by the package tests."""

    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None
        self.authorization: dict[str, Any] | None = None
        self.terminal_error = ""
        self.consumed = False

    async def create(self, **request: Any) -> DeviceAuthorizationIssue:
        self.request = {
            "client_id": request["client_id"],
            "scopes": list(request["scopes"]),
            "resource": request["resource"],
            "client_metadata": dict(request["client_metadata"]),
            "requested_access_id": request["requested_access_id"],
            "context": dict(request["context"]),
        }
        if request.get("expected_card_revision") is not None:
            self.request["expected_card_revision"] = int(
                request["expected_card_revision"]
            )
        return DeviceAuthorizationIssue(
            device_code="device-secret-value",
            user_code="BCDF-GHJK",
            expires_in=600,
            interval=5,
        )

    async def read_user_code(
        self, user_code: str, *, attempt_key: str
    ) -> DeviceUserCodeResult:
        if user_code != "BCDF-GHJK" or self.request is None:
            return DeviceUserCodeResult(status="not_found")
        assert attempt_key == "google:admin@example.test"
        return DeviceUserCodeResult(
            status="found",
            device_digest="d" * 64,
            user_digest="u" * 64,
            request=dict(self.request),
        )

    async def approve(
        self,
        *,
        device_digest: str,
        user_digest: str,
        approving_subject: str,
        authorization: Mapping[str, Any],
    ) -> str:
        assert device_digest == "d" * 64
        assert user_digest == "u" * 64
        assert approving_subject == "google:admin@example.test"
        self.authorization = dict(authorization)
        return "approved"

    async def deny(
        self,
        *,
        device_digest: str,
        user_digest: str,
        approving_subject: str,
        error: str = "access_denied",
    ) -> str:
        assert device_digest == "d" * 64
        assert user_digest == "u" * 64
        assert approving_subject == "google:admin@example.test"
        self.terminal_error = error
        return "denied"

    async def poll(self, *, device_code: str, client_id: str) -> DevicePollResult:
        assert device_code == "device-secret-value"
        if client_id != "claude":
            return DevicePollResult(status="device_client_mismatch", interval=5)
        if self.consumed:
            return DevicePollResult(status="device_code_replayed", interval=5)
        if self.terminal_error:
            self.consumed = True
            return DevicePollResult(status=self.terminal_error, interval=5)
        if self.authorization is None:
            return DevicePollResult(status="authorization_pending", interval=5)
        self.consumed = True
        return DevicePollResult(
            status=DEVICE_POLL_APPROVED,
            interval=5,
            authorization=dict(self.authorization),
        )


class DeviceConsentAccess:
    def __init__(self, app: FastAPI, *, revision: int = 3) -> None:
        self._app = app
        self._revision = revision

    async def oauth_consent_config(self, *, grantor_subject: str):
        assert grantor_subject == "google:admin@example.test"
        return oauth_delegated_config(self._app)

    async def oauth_consent_card_seed(self, **_selection: Any):
        return {
            "ok": True,
            "access_id": ACCESS_ID,
            "card_kind": CARD_KIND_CONNECTOR,
            "card_revision": self._revision,
            "catalog_scope": {"mode": "entry", "resources": ["*"]},
            "catalog_row_by_resource": {RESOURCE: "*"},
        }

    async def resolve_oauth_consent_authority(
        self, _user: Mapping[str, Any], **selection: Any
    ):
        if int(selection["expected_card_revision"]) != self._revision:
            return {
                "ok": False,
                "error": "delegated_card_save_conflict",
                "status": 409,
            }
        return {
            "ok": True,
            "access_id": ACCESS_ID,
            "card_kind": CARD_KIND_CONNECTOR,
            "catalog_version": selection["expected_catalog_version"],
            "card_revision": self._revision,
            "resource_grants": dict(selection["resource_grants"]),
            "resource_operations": dict(selection["resource_operations"]),
            "operations": ["records_export"],
            "named_service_operations": {},
            "named_services": {},
            "account_scope": dict(selection["account_scope"]),
            "identity_scope": "grantor",
            "properties": dict(selection.get("properties") or {}),
        }


@pytest.fixture
def device_client(monkeypatch):
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    app.state.oauth_delegated_config["public_clients"] = [
        {
            "client_id": "claude",
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
        }
    ]
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(
        FakeRedis(), tenant="home", project="demo"
    )
    continuity = CardContinuity()
    continuity.hold(CARD_PROOF, "claude", ACCESS_ID)
    app.state.card_continuity = continuity
    app.state.oauth_grant_store.card_continuity_proven = continuity.proven
    device_store = MemoryDeviceStore()
    app.state.oauth_device_grant_store = device_store
    app.state.automation_access_factory = lambda: DeviceConsentAccess(app)
    monkeypatch.setattr(
        oauth_routes,
        "_connection_hub_widget_base",
        lambda _request: "https://connector.example.test/sites/connections",
    )
    return TestClient(app), device_store


def _start(
    client: TestClient,
    *,
    access_id: str = ACCESS_ID,
    expected_revision: int = 3,
):
    response = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": "claude",
            "scope": "records:read",
            "resource": RESOURCE,
            "access_id": access_id,
            "expected_card_revision": str(expected_revision),
            "continuity_refresh_token": CARD_PROOF,
        },
    )
    assert response.status_code == 200, response.text
    return response


def _open_draft(client: TestClient, prompt: Mapping[str, Any]) -> str:
    unauthenticated = client.get(
        prompt["verification_uri_complete"], follow_redirects=False
    )
    assert unauthenticated.status_code == 302
    assert unauthenticated.headers["location"].startswith("/signin/?next=")
    opened = client.get(
        prompt["verification_uri_complete"],
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )
    assert opened.status_code == 302, opened.text
    return dict(up.parse_qsl(up.urlsplit(opened.headers["location"]).query))[
        "oauth_consent"
    ]


def test_device_flow_uses_existing_card_editor_and_token_issuer(
    device_client, monkeypatch
):
    client, device_store = device_client
    prompt = _start(client).json()
    assert prompt == {
        "device_code": "device-secret-value",
        "user_code": "BCDF-GHJK",
        "verification_uri": f"{ISSUER}/oauth/device",
        "verification_uri_complete": f"{ISSUER}/oauth/device?user_code=BCDF-GHJK",
        "expires_in": 600,
        "interval": 5,
    }
    serialized_request = json.dumps(device_store.request, sort_keys=True)
    assert "device-secret-value" not in serialized_request
    assert "BCDF-GHJK" not in serialized_request
    assert '"access_token":' not in serialized_request
    assert '"refresh_token":' not in serialized_request

    draft_id = _open_draft(client, prompt)
    draft_response = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    assert draft["device_authorization"] == {"user_code": "BCDF-GHJK"}
    decision = {
        "draft_id": draft_id,
        "decision": "approve",
        "label": "Headless worker",
        **draft["selection"],
        "expected_card_revision": draft["card_revision"],
        "expected_catalog_version": draft["catalog_version"],
    }
    approved = client.post(
        "/oauth/authorize/consent/decision",
        json=decision,
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["redirect_url"] == (
        f"{ISSUER}/oauth/device/complete?result=approved"
    )
    assert device_store.authorization["registry_access_id"] == ACCESS_ID
    assert device_store.authorization["expected_card_revision"] == 3

    captured: dict[str, Any] = {}

    async def _issue(_request, _store, **authority):
        captured.update(authority)
        return JSONResponse(
            {
                "access_token": "issued-after-consumption",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _issue)
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": prompt["device_code"],
            "client_id": "claude",
        },
    )
    assert token.status_code == 200, token.text
    assert captured["registry_access_id"] == ACCESS_ID
    assert captured["replace_authority"] is True
    assert captured["card_conflict_error"] == "device_card_revision_conflict"

    replay = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": prompt["device_code"],
            "client_id": "claude",
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "device_code_replayed"


def test_discovery_and_dynamic_registration_enable_device_requests(device_client):
    client, device_store = device_client
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["device_authorization_endpoint"] == (
        f"{ISSUER}/oauth/device_authorization"
    )
    assert DEVICE_GRANT_TYPE in metadata["grant_types_supported"]

    # W414: a public native client asking only for the browser grants is
    # registered with the device grant too, and the response reports what was
    # stored, so its device login needs no second registration.
    browser_first = client.post(
        "/oauth/register",
        json={
            "client_name": "Browser client",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "application_type": "native",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert browser_first.status_code == 201, browser_first.text
    assert browser_first.json()["grant_types"] == [
        "authorization_code", "refresh_token", DEVICE_GRANT_TYPE,
    ]
    prompt = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": browser_first.json()["client_id"],
            "scope": "records:read",
            "resource": RESOURCE,
        },
    )
    assert prompt.status_code == 200, prompt.text
    assert device_store.request["client_id"] == browser_first.json()["client_id"]
    device_store.request = None

    # The grant gate itself is unchanged: a client that does not hold the
    # device grant is refused before any device request exists.
    client.app.state.oauth_delegated_config["public_clients"].append(
        {
            "client_id": "browser-only-provisioned",
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
        }
    )
    refused = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": "browser-only-provisioned",
            "scope": "records:read",
            "resource": RESOURCE,
            "access_id": ACCESS_ID,
        },
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "unauthorized_client"
    assert refused.json()["error_description"] == (
        "client_id is not registered for device authorization"
    )
    assert device_store.request is None

    unknown = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": "unrelated-client",
            "scope": "records:read",
            "resource": RESOURCE,
            "access_id": ACCESS_ID,
        },
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "invalid_client"
    assert device_store.request is None

    registration = client.post(
        "/oauth/register",
        json={
            "client_name": "Headless worker",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "application_type": "native",
            "token_endpoint_auth_method": "none",
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert registration.status_code == 201, registration.text
    registered = registration.json()
    assert registered["grant_types"] == [DEVICE_GRANT_TYPE, "refresh_token"]


def _browser_first_dcr_client(client: TestClient, *, application_type: str = "native") -> str:
    """A dynamic client stored before W414: browser grants only (the Infra shape)."""

    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "Browser client",
            "redirect_uris": (
                ["http://127.0.0.1/callback"]
                if application_type == "native"
                else ["https://claude.ai/api/mcp/auth_callback"]
            ),
            "application_type": application_type,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    store = client.app.state.oauth_grant_store
    stored_read = store.get_client_record

    async def pre_w414_record(requested: str):
        record = await stored_read(requested)
        if record is not None and requested == client_id:
            record = {**record, "grant_types": ["authorization_code", "refresh_token"]}
        return record

    store.get_client_record = pre_w414_record
    return client_id


def test_the_infra_shape_re_authorizes_its_card_before_any_stored_migration(device_client):
    """W414: an existing browser-first public native client (stored grants
    unchanged) re-authorizes its Card by device login with its continuity
    proof, with the same client_id; the checked handler derives the grant."""

    client, device_store = device_client
    client_id = _browser_first_dcr_client(client)
    client.app.state.card_continuity.hold("infra-last-refresh-token", client_id, ACCESS_ID)
    form = {
        "client_id": client_id,
        "scope": "records:read",
        "resource": RESOURCE,
        "access_id": ACCESS_ID,
        "expected_card_revision": "3",
    }

    refused = client.post("/oauth/device_authorization", data=form)
    assert refused.status_code == 400
    assert refused.json()["error"] == "card_continuity_required"
    assert device_store.request is None

    prompt = client.post(
        "/oauth/device_authorization",
        data={**form, "continuity_refresh_token": "infra-last-refresh-token"},
    )
    assert prompt.status_code == 200, prompt.text
    assert device_store.request["client_id"] == client_id
    assert device_store.request["requested_access_id"] == ACCESS_ID
    assert device_store.request["context"]["continuity_proven_access_id"] == ACCESS_ID


def _start_without_card(client: TestClient):
    response = client.post(
        "/oauth/device_authorization",
        data={"client_id": "claude", "scope": "records:read", "resource": RESOURCE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _poll(client: TestClient, prompt: Mapping[str, Any]):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": prompt["device_code"],
            "client_id": "claude",
        },
    )


# Review return 2026-09-30: leaving out access_id skipped the continuity check,
# and consent then resolved the approver's EXISTING Card for that client, so a
# phished approval of an attacker-started code re-authorized the owner's Card.
# A request without proven continuity may create a new Card, never reach one.
def test_a_request_without_proof_never_reaches_an_existing_card(device_client, monkeypatch):
    client, device_store = device_client
    issued = []

    async def _issue(*_args, **_kwargs):
        issued.append(True)
        return JSONResponse({"access_token": "must-not-issue"})

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _issue)
    prompt = _start_without_card(client)
    assert device_store.request["context"]["continuity_proven_access_id"] == ""

    draft_id = _open_draft(client, prompt)
    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert draft.status_code == 409
    assert draft.json()["error"] == "card_continuity_required"
    assert device_store.terminal_error == "card_continuity_required"

    token = _poll(client, prompt)
    assert token.status_code == 400
    assert token.json()["error"] == "card_continuity_required"
    assert issued == []


def test_the_owner_s_decision_cannot_reach_an_existing_card_without_proof(device_client):
    client, device_store = device_client

    async def must_not_resolve(*_args, **_kwargs):
        raise AssertionError("nothing may be resolved or saved for an unproven Card")

    access = DeviceConsentAccess(client.app)
    access.resolve_oauth_consent_authority = must_not_resolve
    client.app.state.automation_access_factory = lambda: access
    prompt = _start_without_card(client)
    draft_id = _open_draft(client, prompt)

    decided = client.post(
        "/oauth/authorize/consent/decision",
        json={
            "draft_id": draft_id,
            "decision": "approve",
            "label": "Headless worker",
            "resource_grants": {},
            "resource_operations": {},
            "invocation_policies": {},
            "account_scope": {},
            "expected_card_revision": 3,
            "expected_catalog_version": "v1",
        },
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert decided.status_code == 409, decided.text
    assert decided.json()["error"] == "card_continuity_required"
    assert device_store.terminal_error == "card_continuity_required"
    assert device_store.authorization is None


def test_a_genuinely_new_card_still_enrolls_by_device_login(device_client, monkeypatch):
    client, device_store = device_client
    client.app.state.automation_access_factory = lambda: DeviceConsentAccess(client.app, revision=0)
    captured: dict[str, Any] = {}

    async def _issue(_request, _store, **authority):
        captured.update(authority)
        return JSONResponse({"access_token": "new-card-token", "token_type": "Bearer", "expires_in": 3600})

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _issue)
    prompt = _start_without_card(client)
    draft_id = _open_draft(client, prompt)
    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert draft.status_code == 200, draft.text
    body = draft.json()
    approved = client.post(
        "/oauth/authorize/consent/decision",
        json={
            "draft_id": draft_id,
            "decision": "approve",
            "label": "New worker",
            **body["selection"],
            "expected_card_revision": body["card_revision"],
            "expected_catalog_version": body["catalog_version"],
        },
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert approved.status_code == 200, approved.text
    token = _poll(client, prompt)
    assert token.status_code == 200, token.text
    assert captured["registry_access_id"] == ACCESS_ID


def test_a_web_dynamic_client_gets_no_built_in_device_grant(device_client):
    client, device_store = device_client
    client_id = _browser_first_dcr_client(client, application_type="web")
    refused = client.post(
        "/oauth/device_authorization",
        data={"client_id": client_id, "scope": "records:read", "resource": RESOURCE},
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "unauthorized_client"
    assert device_store.request is None


# W414, operator ruling 2026-09-30: device login re-authorizes an existing Card
# only with continuity proof from the requesting machine, the Card's last
# refresh token, checked against that Card's credential families for the same
# client. A link and code alone never re-authorize a Card, so an approval an
# owner is tricked into gives the requester nothing.
@pytest.mark.parametrize(
    ("proof", "client_id", "why"),
    [
        (None, "claude", "no proof"),
        ("", "claude", "empty proof"),
        ("a-guessed-refresh-token", "claude", "wrong proof"),
        ("another-card-last-refresh-token", "claude", "another Card's family"),
        (CARD_PROOF, "stranger", "a stranger's client presenting the Card's proof"),
    ],
)
def test_an_existing_card_needs_its_continuity_proof(device_client, proof, client_id, why):
    client, device_store = device_client
    client.app.state.oauth_delegated_config["public_clients"].append(
        {
            "client_id": "stranger",
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
        }
    )
    client.app.state.card_continuity.hold(
        "another-card-last-refresh-token", "claude", "con_another_card"
    )
    client.app.state.card_continuity.hold(
        "stranger-own-refresh-token", "stranger", "con_stranger_card"
    )
    data = {
        "client_id": client_id,
        "scope": "records:read",
        "resource": RESOURCE,
        "access_id": ACCESS_ID,
        "expected_card_revision": "3",
    }
    if proof is not None:
        data["continuity_refresh_token"] = proof

    refused = client.post("/oauth/device_authorization", data=data)

    assert refused.status_code == 400, why
    assert refused.json()["error"] == "card_continuity_required", why
    assert "device_code" not in refused.json()
    assert device_store.request is None, why


def test_a_stranger_cannot_open_another_s_card_with_its_own_proof(device_client):
    client, device_store = device_client
    client.app.state.oauth_delegated_config["public_clients"].append(
        {
            "client_id": "stranger",
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
        }
    )
    client.app.state.card_continuity.hold(
        "stranger-own-refresh-token", "stranger", "con_stranger_card"
    )
    refused = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": "stranger",
            "scope": "records:read",
            "resource": RESOURCE,
            "access_id": ACCESS_ID,
            "continuity_refresh_token": "stranger-own-refresh-token",
        },
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "card_continuity_required"
    assert device_store.request is None
    # The check asked about exactly the requested Card and client.
    assert client.app.state.card_continuity.asked[-1] == (
        "stranger-own-refresh-token", "stranger", ACCESS_ID,
    )


def test_the_machine_that_held_the_card_re_authorizes_it_with_the_same_client(device_client):
    client, device_store = device_client
    prompt = _start(client).json()

    assert prompt["user_code"] == "BCDF-GHJK"
    assert device_store.request["client_id"] == "claude"
    assert device_store.request["requested_access_id"] == ACCESS_ID
    assert client.app.state.card_continuity.asked == [(CARD_PROOF, "claude", ACCESS_ID)]
    # The proof is checked, never stored with the device request.
    assert CARD_PROOF not in json.dumps(device_store.request)


def test_failed_issuance_after_device_consumption_requires_restart(
    device_client, monkeypatch
):
    client, device_store = device_client
    device_store.authorization = {"client_id": "claude"}

    async def _fail_issue(*_args, **_kwargs):
        return JSONResponse(
            status_code=503,
            content={"error": "temporarily_unavailable"},
        )

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _fail_issue)
    failed = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": "device-secret-value",
            "client_id": "claude",
        },
    )

    assert failed.status_code == 503
    assert failed.json() == {
        "error": "device_authorization_restart_required",
        "error_description": (
            "Token issuance failed after approval; restart device authorization."
        ),
    }


def test_card_revision_conflict_after_device_consumption_remains_distinct(
    device_client, monkeypatch
):
    client, device_store = device_client
    device_store.authorization = {"client_id": "claude"}

    async def _conflict(*_args, **_kwargs):
        return JSONResponse(
            status_code=400,
            content={
                "error": "device_card_revision_conflict",
                "error_description": "The delegated access card changed.",
            },
        )

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _conflict)
    failed = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": "device-secret-value",
            "client_id": "claude",
        },
    )

    assert failed.status_code == 400
    assert failed.json()["error"] == "device_card_revision_conflict"


def test_store_failure_after_device_consumption_requires_restart(
    device_client, monkeypatch
):
    client, device_store = device_client
    device_store.authorization = {"client_id": "claude"}

    async def _unavailable(*_args, **_kwargs):
        raise GrantStoreUnavailable("bind_access_grant")

    monkeypatch.setattr(oauth_routes, "_issue_tokens", _unavailable)
    failed = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": "device-secret-value",
            "client_id": "claude",
        },
    )

    assert failed.status_code == 503
    assert failed.json()["error"] == "device_authorization_restart_required"


def test_device_verification_rejects_malformed_stored_authorization_context(
    device_client,
):
    client, device_store = device_client
    prompt = _start(client).json()
    device_store.request["context"]["authorize_params"] = "not-an-object"

    opened = client.get(
        prompt["verification_uri_complete"],
        headers={"Authorization": "Bearer admin-tok"},
        follow_redirects=False,
    )

    assert opened.status_code == 400
    assert "authorization request is unavailable" in opened.text


@pytest.mark.parametrize(
    ("access_id", "revision", "expected_error"),
    [
        ("con_other_card", 3, "device_card_mismatch"),
        (ACCESS_ID, 2, "device_card_revision_conflict"),
    ],
)
def test_device_card_binding_is_terminal_and_distinct(
    device_client, access_id, revision, expected_error
):
    client, device_store = device_client
    client.app.state.card_continuity.hold(CARD_PROOF, "claude", access_id)
    prompt = _start(
        client,
        access_id=access_id,
        expected_revision=revision,
    ).json()
    draft_id = _open_draft(client, prompt)
    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    )
    assert draft.status_code == 409
    assert draft.json()["error"] == expected_error
    assert device_store.terminal_error == expected_error

    token = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": prompt["device_code"],
            "client_id": "claude",
        },
    )
    assert token.status_code == 400
    assert token.json()["error"] == expected_error



# claude-app device authorization, 2026-09-25 22:04Z: the device store handed
# back a Card whose empty operation choice had become an object, and the token
# poll answered HTTP 500. A malformed stored selection is invalid_grant, named
# and logged, and the device client is told why it must restart.
MALFORMED_SELECTION = {
    "https://board.example/mcp/problem_board": ["worker.heartbeat"],
    "https://hub.example/mcp/connection_hub": {},
}


@pytest.mark.asyncio
async def test_issue_tokens_answers_invalid_grant_for_a_malformed_stored_selection(caplog):
    with caplog.at_level("WARNING", logger="kdcube.connection_hub.oauth"):
        response = await oauth_routes._issue_tokens(
            None,
            None,
            sub="user-1",
            scopes=[],
            client_id="claude",
            operations=[],
            resource_operations=MALFORMED_SELECTION,
            registry_access_id="aut_claude_app",
            card_kind="agent",
        )

    assert response.status_code == 400
    body = json.loads(bytes(response.body).decode("utf-8"))
    assert body["error"] == "invalid_grant"
    assert "resource_operations is malformed" in body["error_description"]
    assert "must be a list" in body["error_description"]
    assert any("stored resource_operations is malformed" in r.getMessage() for r in caplog.records)


def test_device_token_for_a_malformed_stored_selection_is_not_a_server_error(device_client):
    client, device_store = device_client
    device_store.authorization = {
        "client_id": "claude",
        "sub": "user-1",
        "registry_access_id": "aut_claude_app",
        "card_kind": "agent",
        "resource_operations": MALFORMED_SELECTION,
    }

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": "device-secret-value",
            "client_id": "claude",
        },
    )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "device_authorization_restart_required"
    assert "resource_operations is malformed" in body["error_description"]

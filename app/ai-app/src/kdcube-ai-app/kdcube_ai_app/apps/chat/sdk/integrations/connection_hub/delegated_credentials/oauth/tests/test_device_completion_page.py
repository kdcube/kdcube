"""The device approval page names the agent that was authorized (W304 E16).

On 2026-09-24 the first full device authorization of a worker on a headless
host ended on "Device connected. Return to the terminal to continue", which
told the person neither which agent they had authorized nor that the terminal
needed nothing from them. The page now names the worker by its alias.
"""

from __future__ import annotations

import urllib.parse as up

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.device import (
    device_completion_name,
    device_completion_page,
)
from connection_hub.delegated_credentials.oauth.device import DEVICE_GRANT_TYPE
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_device_authorization import (  # noqa: F401
    ACCESS_ID,
    RESOURCE,
    _open_draft,
    device_client,
)


def _html(response) -> str:
    return response.body.decode("utf-8")


def test_a_worker_is_named_by_its_alias():
    assert device_completion_name(
        {"client_name": "Connection Hub CLI", "client_metadata": {"kdcube_worker_alias": "claude-ops"}}
    ) == "claude-ops"


def test_any_other_client_is_named_by_its_registration():
    assert device_completion_name({"client_name": "Headless worker"}) == "Headless worker"
    assert device_completion_name({}) == ""
    assert len(device_completion_name({"client_name": "x" * 500})) == 80


def test_the_approved_page_says_which_agent_was_authorized():
    html = _html(device_completion_page(approved=True, agent="claude-ops"))

    assert "<h1>Agent authorized: claude-ops</h1>" in html
    assert "<title>Agent authorized: claude-ops</title>" in html
    assert "The terminal continues on its own. You can close this window." in html


def test_the_name_is_text_never_markup():
    html = _html(device_completion_page(approved=True, agent="<script>alert(1)</script>"))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_unnamed_or_denied_device_keeps_its_page():
    assert "<h1>Device connected</h1>" in _html(device_completion_page(approved=True))
    denied = _html(device_completion_page(approved=False, agent="claude-ops"))
    assert "<h1>Device authorization denied</h1>" in denied
    assert "claude-ops" not in denied


def test_the_completion_route_renders_the_name_it_is_given(device_client):
    client, _store = device_client

    page = client.get("/oauth/device/complete?" + up.urlencode({"result": "approved", "agent": "claude-ops"}))

    assert page.status_code == 200
    assert "<h1>Agent authorized: claude-ops</h1>" in page.text


def test_approving_a_worker_s_device_names_it_on_the_page(device_client):
    client, _store = device_client
    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "Connection Hub CLI",
            "redirect_uris": ["http://127.0.0.1/callback"],
            "application_type": "native",
            "token_endpoint_auth_method": "none",
            "grant_types": [DEVICE_GRANT_TYPE, "refresh_token"],
            "response_types": ["code"],
            "kdcube_worker_alias": "claude-ops",
        },
    ).json()
    prompt = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": registered["client_id"],
            "scope": "records:read",
            "resource": RESOURCE,
            "access_id": ACCESS_ID,
            "expected_card_revision": "3",
        },
    ).json()
    draft_id = _open_draft(client, prompt)
    draft = client.get(
        "/oauth/authorize/consent/draft",
        params={"draft_id": draft_id},
        headers={"Authorization": "Bearer admin-tok"},
    ).json()

    approved = client.post(
        "/oauth/authorize/consent/decision",
        json={
            "draft_id": draft_id,
            "decision": "approve",
            "label": "claude-ops",
            **draft["selection"],
            "expected_card_revision": draft["card_revision"],
            "expected_catalog_version": draft["catalog_version"],
        },
        headers={"Authorization": "Bearer admin-tok"},
    )

    assert approved.status_code == 200, approved.text
    redirect = approved.json()["redirect_url"]
    assert up.parse_qs(up.urlsplit(redirect).query) == {"result": ["approved"], "agent": ["claude-ops"]}
    assert "<h1>Agent authorized: claude-ops</h1>" in client.get(redirect).text

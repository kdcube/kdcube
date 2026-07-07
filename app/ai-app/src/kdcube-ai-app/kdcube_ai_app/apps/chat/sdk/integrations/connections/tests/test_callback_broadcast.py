"""Callback pages push a BroadcastChannel completion to the Settings widget.

Both outcomes of a provider connect (approved / declined) render a page in the
approval tab; each page carries an inline script that posts
`provider_connections.updated` on the `kdcube-connection-hub` channel so the
widget refreshes its catalog immediately (the armed focus refresh stays as
fallback). The script is inline — the pages stay self-contained.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# Importing the package registers the built-in providers (slack, google).
import kdcube_ai_app.apps.chat.sdk.integrations.connections  # noqa: F401
from kdcube_ai_app.apps.chat.sdk.integrations.connections import settings as conn_settings
from kdcube_ai_app.apps.chat.sdk.integrations.connections.store import ConnectionStore, _b64url_json


def _body(resp) -> str:
    return resp.body.decode("utf-8")


def _unsigned_state(provider: str = "slack") -> str:
    return _b64url_json({"v": 1, "provider": provider}) + ".bogus-signature"


@pytest.mark.asyncio
async def test_error_page_broadcasts_provider_connections_updated():
    resp = await conn_settings.callback(
        SimpleNamespace(), state=_unsigned_state("slack"), error="access_denied"
    )
    body = _body(resp)
    assert "new BroadcastChannel" in body
    assert "kdcube-connection-hub" in body
    assert "provider_connections.updated" in body
    # provider comes from the state peek; the failure is announced as ok:false
    assert '"provider": "slack"' in body
    assert '"ok": false' in body
    # the error page stays open and readable
    assert "window.close" not in body
    # self-contained: the inline script is the only script on the page
    assert "<script src" not in body and "src=" not in body


@pytest.mark.asyncio
async def test_success_page_broadcasts_provider_connections_updated(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path, user_id="u1")
    issued = await store.create_oauth_state_async(
        provider="slack",
        secret="s3cret",
        source="kdcube_widget",
        app_id="a1",
    )

    monkeypatch.setattr(conn_settings, "_storage_root", lambda ep: tmp_path)
    monkeypatch.setattr(conn_settings, "_bundle_id", lambda ep=None: "test-bundle")
    monkeypatch.setattr(conn_settings, "integration_definition_value", lambda *a, **k: "")

    async def _secret(ep):
        return "s3cret"

    monkeypatch.setattr(conn_settings, "oauth_state_secret", _secret)

    client_app = SimpleNamespace(
        app_id="a1",
        provider="slack",
        client_id="cid",
        redirect_uri="https://app.example/api/callback",
        enabled=True,
        scopes=["search:read"],
    )
    monkeypatch.setattr(conn_settings, "resolve_client_app", lambda ep, provider, app_id=None: client_app)

    async def _client_secret(bundle_id, provider, app_id):
        return "shh"

    monkeypatch.setattr(conn_settings, "client_app_secret", _client_secret)

    async def _exchange(prov, *, code, redirect_uri, client_id, client_secret):
        return {"access_token": "tok", "scope": "search:read,channels:history"}

    monkeypatch.setattr(conn_settings, "exchange_code", _exchange)

    prov = conn_settings._resolve_provider("slack")

    async def _profile(*, access_token):
        return {
            "external_user_id": "U123",
            "workspace": "acme",
            "display_name": "Ada",
        }

    monkeypatch.setattr(prov, "fetch_profile", _profile)

    resp = await conn_settings.callback(
        SimpleNamespace(), state=issued["state"], code="the-code"
    )
    body = _body(resp)
    assert "Slack connected" in body
    assert "new BroadcastChannel" in body
    assert "kdcube-connection-hub" in body
    assert "provider_connections.updated" in body
    assert '"provider": "slack"' in body
    assert '"ok": true' in body
    # success page may close itself after the push
    assert "window.close" in body
    assert "<script src" not in body and "src=" not in body

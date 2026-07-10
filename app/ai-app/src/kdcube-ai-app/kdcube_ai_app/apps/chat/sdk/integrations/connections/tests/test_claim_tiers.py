"""Claim tiers: our-side consent granularity for all-or-nothing provider screens.

Slack's consent has no per-scope choice, so the connect card picks tiers and we
request exactly those scopes. Tiers must resolve to scope unions, unknown tiers
must fail loudly (never silently widen), and granted scopes must report which
tiers an account holds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# Importing the package registers the built-in providers (slack, google).
import kdcube_ai_app.apps.chat.sdk.integrations.connections  # noqa: F401
from kdcube_ai_app.apps.chat.sdk.integrations.connections import settings as conn_settings
from kdcube_ai_app.apps.chat.sdk.integrations.connections.registry import resolve


def test_slack_tiers_resolve_to_scope_union_in_order():
    prov = resolve("slack")
    assert prov.scopes_for_tiers(["read"]) == ["search:read", "channels:history", "groups:history"]
    assert prov.scopes_for_tiers(["read", "write"]) == [
        "search:read", "channels:history", "groups:history", "chat:write", "im:write",
    ]
    # duplicates collapse, order follows the picked tiers
    assert prov.scopes_for_tiers(["write", "write", "files"]) == [
        "chat:write", "im:write", "files:read", "files:write",
    ]


def test_unknown_tier_raises_instead_of_widening():
    prov = resolve("slack")
    with pytest.raises(ValueError, match="admin"):
        prov.scopes_for_tiers(["read", "admin"])


def test_tier_coverage_reflects_granted_scopes():
    prov = resolve("slack")
    granted = ["search:read", "channels:history", "groups:history", "chat:write", "im:write"]
    coverage = prov.tier_coverage(granted)
    assert coverage == {"read": True, "write": True, "files": False}
    # A token predating im:write covers the write tier only partially.
    assert prov.tier_coverage(granted[:-1])["write"] is False
    assert prov.tier_coverage([]) == {"read": False, "write": False, "files": False}


def test_gmail_tiers_split_read_from_send():
    prov = resolve("google")
    read_scopes = prov.scopes_for_tiers(["read"])
    assert "https://www.googleapis.com/auth/gmail.readonly" in read_scopes
    assert "https://www.googleapis.com/auth/gmail.send" not in read_scopes
    coverage = prov.tier_coverage(read_scopes)
    assert coverage == {"read": True, "send": False}


@pytest.mark.asyncio
async def test_start_oauth_rejects_unknown_tier_with_structured_error():
    result = await conn_settings.start_oauth(
        SimpleNamespace(), provider="slack", tiers=["read", "admin"]
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "slack_unknown_tier"
    assert "admin" in result["error"]["message"]


@pytest.mark.asyncio
async def test_start_oauth_maps_tiers_to_scopes(monkeypatch, tmp_path):
    captured: dict = {}

    def _fake_resolve_client_app(entrypoint, provider, app_id):
        return SimpleNamespace(app_id="a1", client_id="cid", redirect_uri="", enabled=True)

    async def _fake_build(prov, client_app, **kwargs):
        captured["scopes"] = kwargs.get("scopes")
        return {"authorize_url": "https://example/authorize"}

    monkeypatch.setattr(conn_settings, "resolve_client_app", _fake_resolve_client_app)
    monkeypatch.setattr(conn_settings, "build_authorize_url", _fake_build)
    monkeypatch.setattr(
        conn_settings, "store_for", lambda entrypoint, **kw: (SimpleNamespace(), "u1")
    )

    result = await conn_settings.start_oauth(
        SimpleNamespace(), provider="slack", tiers=["read", "files"]
    )
    assert result["ok"] is True
    assert captured["scopes"] == [
        "search:read", "channels:history", "groups:history", "files:read", "files:write", "im:write",
    ]

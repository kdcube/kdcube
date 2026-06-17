from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connections import registry as conn_registry
from kdcube_ai_app.apps.chat.sdk.integrations.connections import store as conn_store
from kdcube_ai_app.apps.chat.sdk.integrations.connections.registry import (
    ConnectionProvider,
    connection_provider,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connections.apps import (
    AmbiguousClientApp,
    ClientApp,
    list_client_apps,
    resolve_client_app,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connections.store import ConnectionStore

# Importing the package registers the built-in providers (e.g. slack).
import kdcube_ai_app.apps.chat.sdk.integrations.connections as connections  # noqa: F401


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_entrypoint(props: dict | None = None, *, bundle_id: str = "test-bundle@1") -> SimpleNamespace:
    prop_map = props or {}

    def bundle_prop(key, default=None):
        return prop_map.get(key, default)

    return SimpleNamespace(
        bundle_prop=bundle_prop,
        bundle_id=bundle_id,
        config=SimpleNamespace(ai_bundle_spec=SimpleNamespace(id=bundle_id)),
    )


def _make_store(tmp_path, user_id="user-1"):
    return ConnectionStore(tmp_path, user_id=user_id, bundle_id="test-bundle@1")


@pytest.fixture(autouse=True)
def _sdk_secret_fakes(monkeypatch):
    async def _empty_get_secret(*args, **kwargs):
        return ""

    async def _noop_set_user_secret(*args, **kwargs):
        return None

    async def _noop_delete_user_secret(*args, **kwargs):
        return None

    monkeypatch.setattr(conn_store, "get_secret", _empty_get_secret)
    monkeypatch.setattr(conn_store, "set_user_secret", _noop_set_user_secret)
    monkeypatch.setattr(conn_store, "delete_user_secret", _noop_delete_user_secret)


# ── registry: register / resolve / catalog ───────────────────────────────────

def test_slack_provider_registered():
    prov = conn_registry.resolve("slack")
    assert prov.provider == "slack"
    assert prov.label == "Slack"
    assert prov.authorize_url == "https://slack.com/oauth/v2/authorize"
    # Slack API methods are dotted — oauth.v2.access. A slash before the method
    # (oauth.v2/access) is a real endpoint typo that returns HTTP 404 at exchange.
    assert prov.token_url == "https://slack.com/api/oauth.v2.access"
    assert "/access" not in prov.token_url  # regression: never the slash form
    assert "search:read" in prov.scopes


def test_resolve_unknown_raises():
    with pytest.raises(KeyError):
        conn_registry.resolve("nope-not-a-provider")


def test_gmail_provider_registered():
    prov = conn_registry.resolve("google")
    assert prov.provider == "google"   # the key connection.get_token("google") resolves
    assert prov.label == "Gmail"
    assert prov.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert prov.token_url == "https://oauth2.googleapis.com/token"
    assert "https://www.googleapis.com/auth/gmail.readonly" in prov.scopes
    assert "https://www.googleapis.com/auth/gmail.send" in prov.scopes


def test_gmail_authorize_extra_params_request_offline_access():
    prov = conn_registry.resolve("google")
    extra = prov.authorize_extra_params()
    assert extra.get("access_type") == "offline"   # required for a refresh_token
    assert extra.get("prompt") == "consent"
    assert extra.get("include_granted_scopes") == "true"


def test_default_authorize_extra_params_empty():
    # Standard providers (e.g. Slack) request no extra authorize params.
    assert conn_registry.resolve("slack").authorize_extra_params() == {}


def test_gmail_scopes_clamped_to_client_app_ceiling():
    from kdcube_ai_app.apps.chat.sdk.integrations.connections.oauth import _scopes_for

    prov = conn_registry.resolve("google")
    app = ClientApp(
        app_id="gmail", provider="google", client_id="cid",
        scopes=(
            "openid", "email", "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ),
    )
    # no per-connect request → full ceiling
    assert _scopes_for(prov, app) == list(app.scopes)
    # a scenario asks for a SUBSET → exactly that subset
    assert _scopes_for(prov, app, requested=["openid", "email"]) == ["openid", "email"]
    # asking for MORE than the ceiling → extra dropped (clamped)
    assert _scopes_for(prov, app, requested=["openid", "https://example/extra"]) == ["openid"]


def test_catalog_contains_slack():
    names = [p.provider for p in conn_registry.catalog()]
    assert "slack" in names


def test_connection_provider_decorator_registers():
    @connection_provider("acme")
    class AcmeConnection(ConnectionProvider):
        provider = "acme"
        label = "Acme"
        authorize_url = "https://acme.example/authorize"
        token_url = "https://acme.example/token"
        scopes = ["read"]

        async def fetch_profile(self, *, access_token: str):
            return {"external_user_id": "x", "display_name": "Acme User"}

    prov = conn_registry.resolve("acme")
    assert prov.label == "Acme"
    assert prov.provider == "acme"


def test_provider_carries_no_credentials():
    # The provider is pure mechanics — no client_id/secret/config-prefix surface.
    prov = conn_registry.resolve("slack")
    for removed in ("client_id", "client_secret", "config_prefix", "secret_prefix", "enabled", "scopes_for"):
        assert not hasattr(prov, removed)


# ── client apps (middle level) ───────────────────────────────────────────────

def _apps_props(provider: str, apps: list[dict]) -> dict:
    return {f"connections.providers.{provider}.apps": apps}


def test_list_client_apps_reads_provider_apps():
    ep = _make_entrypoint(
        _apps_props(
            "slack",
            [
                {"app_id": "acme", "label": "Acme Slack", "client_id": "cid-acme", "scopes": ["search:read"]},
                {"app_id": "beta", "label": "Beta Slack", "client_id": "cid-beta", "enabled": False},
            ],
        )
    )
    apps = list_client_apps(ep, "slack")
    assert [a.app_id for a in apps] == ["acme", "beta"]
    assert all(a.provider == "slack" for a in apps)
    assert apps[0].client_id == "cid-acme"
    assert apps[0].scopes == ("search:read",)
    assert apps[1].enabled is False


def test_resolve_client_app_single_enabled_defaults():
    ep = _make_entrypoint(
        _apps_props("slack", [{"app_id": "acme", "client_id": "cid-acme"}])
    )
    app = resolve_client_app(ep, "slack")
    assert app.app_id == "acme"


def test_resolve_client_app_by_app_id():
    ep = _make_entrypoint(
        _apps_props(
            "slack",
            [
                {"app_id": "acme", "client_id": "cid-acme"},
                {"app_id": "beta", "client_id": "cid-beta"},
            ],
        )
    )
    assert resolve_client_app(ep, "slack", "beta").client_id == "cid-beta"


def test_resolve_client_app_ambiguous_raises():
    # Two enabled apps and no app_id → must NOT silently pick one.
    ep = _make_entrypoint(
        _apps_props(
            "slack",
            [
                {"app_id": "acme", "client_id": "cid-acme"},
                {"app_id": "beta", "client_id": "cid-beta"},
            ],
        )
    )
    with pytest.raises(AmbiguousClientApp) as excinfo:
        resolve_client_app(ep, "slack")
    assert set(excinfo.value.app_ids) == {"acme", "beta"}


def test_resolve_client_app_none_enabled_raises():
    ep = _make_entrypoint(_apps_props("slack", [{"app_id": "acme", "enabled": False}]))
    with pytest.raises(ValueError, match="no enabled client app"):
        resolve_client_app(ep, "slack")


# ── per-connect scope override, clamped to the client-app ceiling ─────────────

def test_scopes_clamped_to_client_app_ceiling():
    from kdcube_ai_app.apps.chat.sdk.integrations.connections.oauth import _scopes_for

    prov = conn_registry.resolve("slack")  # provider defaults exist but are the fallback
    app = ClientApp(app_id="acme", provider="slack", client_id="cid",
                    scopes=("search:read", "channels:history"))  # the admin ceiling

    # no per-connect request → full ceiling (the app's scopes)
    assert _scopes_for(prov, app) == ["search:read", "channels:history"]
    # a scenario asks for a SUBSET → exactly that subset
    assert _scopes_for(prov, app, requested=["search:read"]) == ["search:read"]
    # asking for MORE than the ceiling → the extra scope is dropped (clamped)
    assert _scopes_for(prov, app, requested=["search:read", "admin"]) == ["search:read"]
    # requesting only out-of-ceiling scopes → fall back to the full ceiling (never zero)
    assert _scopes_for(prov, app, requested=["admin"]) == ["search:read", "channels:history"]


# ── ConnectionStore round-trip (no network) ──────────────────────────────────

@pytest.mark.asyncio
async def test_store_upsert_set_tokens_has_token_flag(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    account = await store.upsert_account_async(
        {"provider": "slack", "external_user_id": "T1:U1", "display_name": "Alice @ Acme"}
    )
    account_id = account["account_id"]
    assert account["provider"] == "slack"
    assert account["external_user_id"] == "T1:U1"
    assert account_id.startswith("slack_")
    assert "access_token" not in account

    # Tokens go to the (faked) user-secret store, not the accounts file.
    stored: dict = {}

    async def _set_secret(key, value, **kw):
        stored[key] = value

    async def _get_secret(key, **kw):
        normalized = str(key or "")
        if normalized.startswith("u:"):
            normalized = normalized[2:]
        return stored.get(normalized)

    monkeypatch.setattr(conn_store, "set_user_secret", _set_secret)
    monkeypatch.setattr(conn_store, "get_secret", _get_secret)

    # No token yet → has_token False
    rows = await store.list_accounts_async(provider="slack")
    assert rows[0]["has_token"] is False

    await store.set_tokens_async(account_id, {"access_token": "tok-abc", "expires_in": 3600})

    # Token absent from accounts file
    raw = json.loads(store.accounts_path.read_text())
    assert "access_token" not in json.dumps(raw)

    # has_token now True
    rows = await store.list_accounts_async(provider="slack")
    assert rows[0]["has_token"] is True
    retrieved = await store.get_tokens_async(account_id)
    assert retrieved["access_token"] == "tok-abc"


@pytest.mark.asyncio
async def test_store_list_filters_by_provider(tmp_path):
    store = _make_store(tmp_path)
    await store.upsert_account_async({"provider": "slack", "external_user_id": "T1:U1", "display_name": "Slack One"})
    await store.upsert_account_async({"provider": "linkedin", "external_user_id": "L1", "display_name": "LinkedIn One"})

    slack_rows = await store.list_accounts_async(provider="slack")
    assert [r["display_name"] for r in slack_rows] == ["Slack One"]

    li_rows = await store.list_accounts_async(provider="linkedin")
    assert [r["display_name"] for r in li_rows] == ["LinkedIn One"]

    all_rows = await store.list_accounts_async()
    assert len(all_rows) == 2


@pytest.mark.asyncio
async def test_store_upsert_idempotent_by_external_user_id_provider_and_app(tmp_path):
    store = _make_store(tmp_path)
    first = await store.upsert_account_async(
        {"provider": "slack", "app_id": "acme", "external_user_id": "T1:U1", "display_name": "Alice"}
    )
    await store.upsert_account_async(
        {"provider": "slack", "app_id": "acme", "external_user_id": "T1:U1", "display_name": "Alice Updated"}
    )

    rows = await store.list_accounts_async(provider="slack")
    assert len(rows) == 1
    assert rows[0]["account_id"] == first["account_id"]
    assert rows[0]["app_id"] == "acme"
    assert rows[0]["display_name"] == "Alice Updated"


@pytest.mark.asyncio
async def test_store_upsert_app_id_discriminates_accounts(tmp_path):
    # Same user + workspace connected through two different client apps → two accounts.
    store = _make_store(tmp_path)
    a = await store.upsert_account_async(
        {"provider": "slack", "app_id": "acme", "external_user_id": "T1:U1", "display_name": "Alice"}
    )
    b = await store.upsert_account_async(
        {"provider": "slack", "app_id": "beta", "external_user_id": "T1:U1", "display_name": "Alice"}
    )
    assert a["account_id"] != b["account_id"]
    rows = await store.list_accounts_async(provider="slack")
    assert {r["app_id"] for r in rows} == {"acme", "beta"}


# ── OAuth state sign + consume (single-use, provider-carrying) ────────────────

@pytest.mark.asyncio
async def test_oauth_state_roundtrip_carries_provider(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(
        provider="slack", app_id="acme", secret="s3cr3t", source="settings", return_hint="back"
    )
    state = result["state"]
    assert "." in state
    assert result["payload"]["provider"] == "slack"
    assert result["payload"]["app_id"] == "acme"
    assert result["payload"]["user_id"] == "user-1"
    assert result["payload"]["source"] == "settings"
    assert result["payload"]["return_hint"] == "back"

    consumed = await store.consume_oauth_state_async(state=state, secret="s3cr3t")
    assert consumed["provider"] == "slack"
    assert consumed["app_id"] == "acme"
    assert consumed["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_oauth_state_single_use(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(provider="slack", secret="s3cr3t", source="settings")
    state = result["state"]
    await store.consume_oauth_state_async(state=state, secret="s3cr3t")  # first use ok
    with pytest.raises(ValueError, match="not found"):
        await store.consume_oauth_state_async(state=state, secret="s3cr3t")  # replay rejected


@pytest.mark.asyncio
async def test_oauth_state_wrong_secret_rejected(tmp_path):
    store = _make_store(tmp_path)
    result = await store.create_oauth_state_async(provider="slack", secret="correct", source="settings")
    with pytest.raises(ValueError, match="signature"):
        await store.consume_oauth_state_async(state=result["state"], secret="wrong")


@pytest.mark.asyncio
async def test_oauth_state_requires_provider_and_secret(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="not configured"):
        await store.create_oauth_state_async(provider="slack", secret="", source="settings")
    with pytest.raises(ValueError, match="provider"):
        await store.create_oauth_state_async(provider="", secret="s3cr3t", source="settings")


# ── Slack user-token flow (user_scope + authed_user) ─────────────────────────

def test_slack_requests_user_scope_param():
    # Slack acts AS the user → scopes go under `user_scope`, not `scope`.
    assert conn_registry.resolve("slack").authorize_scope_param() == "user_scope"
    # Standard providers (Google) keep the plain `scope` param.
    assert conn_registry.resolve("google").authorize_scope_param() == "scope"


def test_slack_extract_token_pulls_user_token_not_bot():
    prov = conn_registry.resolve("slack")
    out = prov.extract_token({
        "ok": True,
        "access_token": "xoxb-BOT",   # bot token — must NOT be stored
        "authed_user": {"id": "U1", "token_type": "user",
                        "access_token": "xoxp-USER", "scope": "search:read"},
        "team": {"id": "T1", "name": "Acme"},
    })
    assert out["access_token"] == "xoxp-USER"   # the USER token
    assert out["scope"] == "search:read"
    assert out["team_id"] == "T1"


def test_slack_extract_token_rotation_falls_back_to_top_level():
    # Token rotation: the rotated token comes back at the top level (no authed_user).
    out = conn_registry.resolve("slack").extract_token(
        {"ok": True, "access_token": "xoxp-ROT", "refresh_token": "xoxe-R", "expires_in": 43200}
    )
    assert out["access_token"] == "xoxp-ROT"
    assert out["refresh_token"] == "xoxe-R"


def test_slack_extract_token_raises_on_ok_false():
    # Slack returns ok:false with HTTP 200 — surface it as an error.
    with pytest.raises(RuntimeError, match="Slack OAuth error"):
        conn_registry.resolve("slack").extract_token({"ok": False, "error": "bad_code"})

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions import connections
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
    ConnectedAccount,
    MemoryOAuthStateStore,
    DelegatedToKdcubeBroker,
    DelegatedToKdcubeStore,
    ToolClaimPolicy,
    preflight_tool_claim_policies,
    operations_for_user,
    delegated_to_kdcube_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    DelegatedToKdcubeAdapter,
    register_adapter,
    resolve_adapter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import credential_id_for
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import store as ui_store


def _sample_config():
    return delegated_to_kdcube_config(
        {
            "delegated_to_kdcube": {
                "enabled": True,
                "providers": {
                    "google": {
                        "label": "Google",
                        "adapter": "google.oauth",
                        "claims": {
                            "gmail:read": {
                                "label": "Read Gmail",
                                "description": "Read Gmail messages for this user.",
                                "provider_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                            },
                            "gmail:send": {
                                "label": "Send Gmail",
                                "provider_scopes": ["https://www.googleapis.com/auth/gmail.send"],
                            },
                        },
                        "connector_apps": {
                            "gmail": {
                                "label": "Gmail connector",
                                "client_id": "client-id",
                                "client_secret_ref": "connections.delegated_to_kdcube.providers.google.connector_apps.gmail.client_secret",
                                "allowed_claims": ["gmail:read", "gmail:send"],
                            }
                        },
                    }
                },
            }
        }
    )


class _FakeOAuthAdapter(DelegatedToKdcubeAdapter):
    adapter_id = "test.oauth"
    label = "Test OAuth"
    kind = "oauth2"
    authorize_url = "https://provider.example.test/oauth/authorize"
    token_url = "https://provider.example.test/oauth/token"

    async def exchange_code(self, *, code: str, redirect_uri: str, client_id: str, client_secret: str) -> dict[str, Any]:
        assert code == "code-1"
        assert redirect_uri == "https://kdcube.example.test/oauth/callback"
        assert client_id == "test-client"
        assert client_secret == "test-secret"
        return {
            "access_token": "token-1",
            "refresh_token": "refresh-1",
            "scope": "provider.read",
            "expires_in": 3600,
            "expires_at": 4_000_000_000,
        }

    async def fetch_profile(self, *, access_token: str, token: dict[str, Any] | None = None) -> dict[str, Any]:
        assert access_token == "token-1"
        return {
            "external_subject": "external-user-1",
            "email": "user@example.test",
            "display_name": "User Example",
        }

    async def refresh_credential(self, credential: dict[str, Any], *, client_id: str, client_secret: str) -> dict[str, Any]:
        assert credential["refresh_token"] == "refresh-1"
        assert client_id == "test-client"
        assert client_secret == "test-secret"
        refreshed = dict(credential)
        refreshed.update(
            {
                "access_token": "token-refreshed",
                "expires_at": 4_000_000_000,
                "refresh_token": credential["refresh_token"],
                "refreshed": True,
            }
        )
        return refreshed

    async def normalize_profile(self, credential: dict[str, Any]) -> dict[str, Any]:
        return {"external_subject": "fallback-user"}


def _oauth_sample_config():
    register_adapter(_FakeOAuthAdapter())
    return delegated_to_kdcube_config(
        {
            "enabled": True,
            "providers": {
                "test": {
                    "label": "Test Provider",
                    "adapter": "test.oauth",
                    "claims": {
                        "test:read": {
                            "label": "Read test data",
                            "provider_scopes": ["provider.read"],
                        }
                    },
                    "connector_apps": {
                        "default": {
                            "label": "Default OAuth app",
                            "client_id": "test-client",
                            "client_secret_ref": "connections.delegated_to_kdcube.providers.test.connector_apps.default.client_secret",
                            "allowed_claims": ["test:read"],
                        }
                    },
                }
            },
        }
    )


def _install_fake_storage(monkeypatch):
    props: dict[tuple[str, str, str], Any] = {}
    secrets: dict[tuple[str, str, str], str] = {}

    def get_user_prop(key: str, *, user_id: str | None = None, bundle_id: str | None = None, default: Any = None):
        return props.get((user_id or "", bundle_id or "", key), default)

    def set_user_prop(key: str, value: Any, *, user_id: str | None = None, bundle_id: str | None = None):
        props[(user_id or "", bundle_id or "", key)] = value

    def delete_user_prop(key: str, *, user_id: str | None = None, bundle_id: str | None = None):
        props.pop((user_id or "", bundle_id or "", key), None)

    async def set_user_secret(
        key: str,
        value: str,
        *,
        user_id: str | None = None,
        bundle_id: str | None = None,
    ):
        secrets[(user_id or "", bundle_id or "", key)] = value

    async def get_secret(key: str, *, user_id: str | None = None, bundle_id: str | None = None):
        normalized = key[2:] if key.startswith("u:") else key
        return secrets.get((user_id or "", bundle_id or "", normalized))

    async def delete_user_secret(key: str, *, user_id: str | None = None, bundle_id: str | None = None):
        secrets.pop((user_id or "", bundle_id or "", key), None)

    monkeypatch.setattr(ui_store.sdk_config, "get_user_prop", get_user_prop)
    monkeypatch.setattr(ui_store.sdk_config, "set_user_prop", set_user_prop)
    monkeypatch.setattr(ui_store.sdk_config, "delete_user_prop", delete_user_prop)
    monkeypatch.setattr(ui_store.sdk_config, "set_user_secret", set_user_secret)
    monkeypatch.setattr(ui_store.sdk_config, "get_secret", get_secret)
    monkeypatch.setattr(ui_store.sdk_config, "delete_user_secret", delete_user_secret)
    return props, secrets


def test_config_parses_provider_claims_and_connector_apps():
    config = _sample_config()

    provider = config.provider("google")
    assert config.enabled is True
    assert provider is not None
    assert provider.adapter == "google.oauth"
    assert provider.claims["gmail:read"].provider_scopes == (
        "https://www.googleapis.com/auth/gmail.readonly",
    )
    assert provider.connector_apps["gmail"].client_id == "client-id"
    assert provider.connector_apps["gmail"].allowed_claims == ("gmail:read", "gmail:send")
    public = config.to_dict(include_client_ids=True)
    assert public["providers"]["google"]["connector_apps"]["gmail"]["client_id"] == "client-id"
    assert "client_secret_ref" not in public["providers"]["google"]["connector_apps"]["gmail"]


def test_connections_lazy_surface_exposes_delegated_to_kdcube():
    assert connections.DelegatedToKdcubeClient.__name__ == "DelegatedToKdcubeClient"
    assert connections.DelegatedToKdcubeStore.__name__ == "DelegatedToKdcubeStore"
    assert connections.DelegatedToKdcubeOperations.__name__ == "DelegatedToKdcubeOperations"
    assert connections.preflight_tool_claim_policies.__name__ == "preflight_tool_claim_policies"


@pytest.mark.asyncio
async def test_store_persists_metadata_in_user_props_and_credentials_in_user_secrets(monkeypatch):
    props, secrets = _install_fake_storage(monkeypatch)
    store = DelegatedToKdcubeStore(user_id="user-1")

    stored = await store.upsert_account(
        ConnectedAccount(
            account_id="",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="google-sub-1",
            email="user@example.test",
            display_name="User Example",
            claims=("gmail:read",),
        )
    )
    await store.set_credential(stored.credential_id, {"access_token": "token-1", "refresh_token": "refresh-1"})

    accounts = await store.list_accounts(provider_id="google")
    credential = await store.get_credential(stored.credential_id)

    assert len(accounts) == 1
    assert accounts[0].public_dict()["has_credential"] is True
    assert "credential_id" not in accounts[0].public_dict()
    assert credential["access_token"] == "token-1"
    assert any(key[-1].startswith("delegated_to_kdcube.accounts.") for key in props)
    assert any(key[-1].startswith("delegated_to_kdcube.credentials.") for key in secrets)

    assert await store.disconnect_account(stored.account_id) is True
    assert await store.list_accounts() == []
    assert await store.get_credential(stored.credential_id) == {}


@pytest.mark.asyncio
async def test_broker_returns_credential_when_claim_and_credential_exist(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="google-sub-1",
            claims=("gmail:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(credential_id, {"access_token": "token-1"})

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:read",
    )

    assert result.ok is True
    assert result.credential is not None
    assert result.credential.credential["access_token"] == "token-1"
    assert result.to_dict()["credential"]["has_credential"] is True
    assert "credential" not in result.to_dict()["credential"]
    assert result.to_dict(include_credential=True)["credential"]["credential"]["access_token"] == "token-1"


@pytest.mark.asyncio
async def test_broker_requires_consent_when_account_missing(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:read",
    )

    assert result.ok is False
    assert result.consent_required is True
    assert result.error == "connect_required"
    assert result.retry_hint is True


@pytest.mark.asyncio
async def test_broker_requires_account_id_when_multiple_accounts_can_satisfy_claim(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    for account_id in ("acct-1", "acct-2"):
        credential_id = credential_id_for(account_id)
        await store.upsert_account(
            ConnectedAccount(
                account_id=account_id,
                provider_id="google",
                connector_app_id="gmail",
                external_subject=f"sub-{account_id}",
                claims=("gmail:read",),
                credential_id=credential_id,
            )
        )
        await store.set_credential(credential_id, {"access_token": f"token-{account_id}"})

    ambiguous = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:read",
    )
    selected = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:read",
        account_id="acct-2",
    )

    assert ambiguous.ok is False
    assert ambiguous.error == "account_required"
    assert ambiguous.retry_hint is True
    # Candidates are LABELED account summaries, never bare ids — chat/MCP
    # clients render a real choice list from them.
    assert [item["account_id"] for item in ambiguous.candidates] == ["acct-1", "acct-2"]
    assert all(item["label"] for item in ambiguous.candidates)
    assert selected.ok is True
    assert selected.account_id == "acct-2"


@pytest.mark.asyncio
async def test_broker_resolves_tool_claim_policy_supplied_by_application(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="google-sub-1",
            claims=("gmail:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(credential_id, {"access_token": "token-1"})
    policy = ToolClaimPolicy.from_config(
        "mailbox.search",
        {
            "connected_accounts": [
                {
                    "provider_id": "google",
                    "connector_app_id": "gmail",
                    "claims": ["gmail:read"],
                }
            ]
        },
    )

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_tool_claims(policy=policy)

    assert result["ok"] is True
    assert result["tool_name"] == "mailbox.search"
    assert result["resolved"][0]["provider_id"] == "google"
    assert result["resolved"][0]["claim"] == "gmail:read"


def test_tool_claim_policy_parses_application_tool_config():
    policy = ToolClaimPolicy.from_tool_config(
        "report.post_to_slack",
        {
            "label": "Post report to Slack",
            "connections": {
                "delegated_to_kdcube": {
                    "connected_accounts": [
                        {
                            "provider_id": "slack",
                            "connector_app_id": "demo",
                            "claims": ["slack:post"],
                        }
                    ]
                }
            },
        },
    )

    assert policy.tool_name == "report.post_to_slack"
    assert policy.connected_accounts[0].provider_id == "slack"
    assert policy.connected_accounts[0].connector_app_id == "demo"


@pytest.mark.asyncio
async def test_preflight_returns_connection_hub_consent_payload_when_account_missing(monkeypatch):
    _install_fake_storage(monkeypatch)

    class _Entrypoint:
        redis = None
        bundle_props = {
            "connections": {
                "delegated_to_kdcube": {
                    "enabled": True,
                    "providers": {
                        "google": {
                            "label": "Google",
                            "claims": {
                                "gmail:send": {
                                    "label": "Send Gmail",
                                    "provider_scopes": ["https://www.googleapis.com/auth/gmail.send"],
                                }
                            },
                            "connector_apps": {
                                "gmail": {
                                    "label": "Gmail connector",
                                    "allowed_claims": ["gmail:send"],
                                }
                            },
                        }
                    },
                }
            }
        }

        def runtime_identity(self):
            return {"tenant": "demo-tenant", "project": "demo-project"}

    policy = ToolClaimPolicy.from_config(
        "mailbox.send",
        {
            "connected_accounts": [
                {
                    "provider_id": "google",
                    "connector_app_id": "gmail",
                    "claims": ["gmail:send"],
                }
            ]
        },
    )

    result = await preflight_tool_claim_policies(
        entrypoint=_Entrypoint(),
        user_id="user-1",
        policies=[policy],
        tenant="demo-tenant",
        project="demo-project",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert result["consent"]["provider_id"] == "google"
    assert result["consent"]["connector_app_id"] == "gmail"
    assert result["consent"]["claims"] == ["gmail:send"]
    assert result["consent"]["url"].startswith(
        "/api/integrations/bundles/demo-tenant/demo-project/connection-hub%401-0/widgets/connections_settings?"
    )
    # The consent block carries the broker reason verbatim so chat/MCP can
    # render the right action instead of a generic connect banner.
    assert result["consent"]["reason"] == "connect_required"
    assert result["consent"]["retry_hint"] is True
    assert result["consent"]["action_label"] == "Connect account"
    assert "Connect your Google account" in result["error"]["message"]


@pytest.mark.asyncio
async def test_builtin_adapters_register_and_normalize_profiles():
    import kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers  # noqa: F401

    google = resolve_adapter("google.oauth")
    normalized = await google.normalize_profile({"sub": "s-1", "email": "a@example.test", "name": "A User"})
    scopes = google.provider_scopes_for_claims(
        ["gmail:read"],
        _sample_config().provider("google").claims,
    )

    assert normalized == {
        "external_subject": "s-1",
        "email": "a@example.test",
        "display_name": "A User",
    }
    assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]


@pytest.mark.asyncio
async def test_operations_connect_credential_catalog_and_disconnect(monkeypatch):
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_sample_config())

    connected = await ops.connect_credential(
        {
            "provider_id": "google",
            "connector_app_id": "gmail",
            "external_subject": "google-sub-1",
            "email": "user@example.test",
            "display_name": "User Example",
            "claims": ["gmail:read"],
            "credential": {"access_token": "token-1", "refresh_token": "refresh-1"},
        }
    )
    account_id = connected["account"]["account_id"]
    catalog = await ops.catalog(provider_id="google")
    resolved = await ops.resolve(provider_id="google", claim="gmail:read", account_id=account_id)
    disconnected = await ops.disconnect(account_id=account_id)

    assert connected["ok"] is True
    assert connected["account"]["has_credential"] is True
    assert "credential_id" not in connected["account"]
    assert catalog["accounts"][0]["email"] == "user@example.test"
    assert resolved["ok"] is True
    assert resolved["credential"]["has_credential"] is True
    assert "credential" not in resolved["credential"]
    assert disconnected == {"ok": True, "removed": True, "account_id": account_id}


@pytest.mark.asyncio
async def test_broker_refreshes_expired_oauth_credential_before_returning_it(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _oauth_sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="test",
            connector_app_id="default",
            external_subject="external-user-1",
            claims=("test:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(
        credential_id,
        {
            "oauth": True,
            "access_token": "token-expired",
            "refresh_token": "refresh-1",
            "expires_at": 1,
            "claims": ["test:read"],
        },
    )

    broker = DelegatedToKdcubeBroker(
        config=config,
        store=store,
        client_secret_resolver=lambda **kwargs: "test-secret",
    )
    result = await broker.ensure_claim(
        provider_id="test",
        connector_app_id="default",
        claim="test:read",
        account_id="acct-1",
    )
    stored = await store.get_credential(credential_id)

    assert result.ok is True
    assert result.credential is not None
    assert result.credential.credential["access_token"] == "token-refreshed"
    assert stored["access_token"] == "token-refreshed"
    assert stored["refresh_token"] == "refresh-1"


@pytest.mark.asyncio
async def test_broker_refreshes_oauth_credential_with_refresh_token_but_no_expiry(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _oauth_sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="test",
            connector_app_id="default",
            external_subject="external-user-1",
            claims=("test:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(
        credential_id,
        {
            "oauth": True,
            "access_token": "token-without-expiry",
            "refresh_token": "refresh-1",
            "claims": ["test:read"],
        },
    )

    result = await DelegatedToKdcubeBroker(
        config=config,
        store=store,
        client_secret_resolver=lambda **kwargs: "test-secret",
    ).ensure_claim(
        provider_id="test",
        connector_app_id="default",
        claim="test:read",
        account_id="acct-1",
    )

    assert result.ok is True
    assert result.credential is not None
    assert result.credential.credential["access_token"] == "token-refreshed"


@pytest.mark.asyncio
async def test_broker_requires_reconnect_for_expired_oauth_without_refresh_token(monkeypatch):
    _install_fake_storage(monkeypatch)
    config = _oauth_sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="test",
            connector_app_id="default",
            external_subject="external-user-1",
            claims=("test:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(
        credential_id,
        {
            "oauth": True,
            "access_token": "token-expired",
            "expires_at": 1,
            "claims": ["test:read"],
        },
    )

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="test",
        connector_app_id="default",
        claim="test:read",
        account_id="acct-1",
    )

    assert result.ok is False
    assert result.consent_required is True
    assert "Reconnect" in result.message


@pytest.mark.asyncio
async def test_operations_reject_unknown_claim(monkeypatch):
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_sample_config())

    with pytest.raises(ValueError, match="unknown provider claim"):
        await ops.connect_credential(
            {
                "provider_id": "google",
                "connector_app_id": "gmail",
                "external_subject": "google-sub-1",
                "claims": ["gmail:delete"],
                "credential": {"access_token": "token-1"},
            }
        )


@pytest.mark.asyncio
async def test_operations_start_and_complete_oauth_stores_user_credential(monkeypatch):
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_oauth_sample_config())
    state_store = MemoryOAuthStateStore()

    started = await ops.start_oauth(
        {
            "provider_id": "test",
            "connector_app_id": "default",
            "claims": ["test:read"],
        },
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=state_store,
        state_secret="state-secret",
    )

    assert started["ok"] is True
    assert started["redirect_uri"] == "https://kdcube.example.test/oauth/callback"
    assert "client_id=test-client" in started["authorize_url"]
    assert "scope=provider.read" in started["authorize_url"]

    completed = await ops.complete_oauth(
        code="code-1",
        state=started["authorize_url"].split("state=", 1)[1].split("&", 1)[0],
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=state_store,
        state_secret="state-secret",
        client_secret_resolver=lambda **kwargs: "test-secret",
    )
    catalog = await ops.catalog(provider_id="test")
    resolved = await ops.resolve(
        provider_id="test",
        claim="test:read",
        account_id=completed["account"]["account_id"],
    )

    assert completed["ok"] is True
    assert completed["account"]["email"] == "user@example.test"
    assert catalog["accounts"][0]["has_credential"] is True
    assert resolved["ok"] is True
    assert resolved["credential"]["has_credential"] is True
    assert "credential" not in resolved["credential"]

    with pytest.raises(ValueError, match="already used"):
        await ops.complete_oauth(
            code="code-1",
            state=started["authorize_url"].split("state=", 1)[1].split("&", 1)[0],
            callback_url="https://kdcube.example.test/oauth/callback",
            state_store=state_store,
            state_secret="state-secret",
            client_secret_resolver=lambda **kwargs: "test-secret",
        )


# ── Reason vocabulary + credential health matrix ────────────────────────────
# The broker mints DISTINCT user-actionable reasons; each is a different fix
# in Connection Hub. These tests pin the vocabulary end-to-end.


@pytest.mark.asyncio
async def test_broker_distinguishes_claim_upgrade_from_connect(monkeypatch):
    """Account connected, claim not approved -> claim_upgrade_required (with
    labeled candidates), NOT connect_required."""
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="sub-1",
            display_name="Work Gmail",
            claims=("gmail:read",),          # gmail:send NOT approved
            credential_id=credential_id,
        )
    )
    await store.set_credential(credential_id, {"access_token": "token-1"})

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:send",
    )

    assert result.ok is False
    assert result.error == "claim_upgrade_required"
    assert result.retry_hint is True
    assert result.consent_required is True
    assert [item["account_id"] for item in result.candidates] == ["acct-1"]
    assert result.candidates[0]["label"] == "Work Gmail"


@pytest.mark.asyncio
async def test_broker_missing_credential_is_reconnect_and_marks_account(monkeypatch):
    """Credential record gone -> reconnect_required, and the account's health
    metadata flips to credential_status=missing so Connection Hub shows it."""
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="sub-1",
            claims=("gmail:read",),
            credential_id=credential_id_for("acct-1"),
        )
    )
    # no set_credential: the record is missing

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google",
        claim="gmail:read",
    )

    assert result.ok is False
    assert result.error == "reconnect_required"
    assert result.retry_hint is True
    account = await store.get_account("acct-1")
    assert account.metadata.get("credential_status") == "missing"
    assert account.metadata.get("last_error")


@pytest.mark.asyncio
async def test_broker_unrefreshable_expiry_is_reconnect_and_marks_account(monkeypatch):
    """Expired credential without a refresh token -> reconnect_required and a
    persisted credential_status=reconnect_required health transition."""
    _install_fake_storage(monkeypatch)
    config = _oauth_sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-t")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-t",
            provider_id="test",
            connector_app_id="default",
            external_subject="sub-t",
            claims=("test:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(
        credential_id,
        {"access_token": "stale", "expires_at": 1},  # long expired, no refresh_token
    )

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="test",
        claim="test:read",
    )

    assert result.ok is False
    assert result.error == "reconnect_required"
    assert result.retry_hint is True
    account = await store.get_account("acct-t")
    assert account.metadata.get("credential_status") == "reconnect_required"


@pytest.mark.asyncio
async def test_broker_force_refresh_refreshes_valid_looking_credential(monkeypatch):
    """force_refresh is the live-401 retry lever: the provider rejected a
    token whose timestamps still look valid, so the broker must refresh
    anyway instead of trusting expires_at."""
    _install_fake_storage(monkeypatch)
    config = _oauth_sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    credential_id = credential_id_for("acct-t")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-t",
            provider_id="test",
            connector_app_id="default",
            external_subject="sub-t",
            claims=("test:read",),
            credential_id=credential_id,
        )
    )
    await store.set_credential(
        credential_id,
        {"access_token": "rejected-by-provider", "refresh_token": "refresh-1", "expires_at": 4_000_000_000},
    )

    broker = DelegatedToKdcubeBroker(
        config=config,
        store=store,
        client_secret_resolver=lambda **_kw: "test-secret",
    )
    result = await broker.ensure_claim(
        provider_id="test",
        claim="test:read",
        force_refresh=True,
    )

    assert result.ok is True
    assert result.credential is not None
    assert result.credential.credential.get("access_token") == "token-refreshed"
    # And the refreshed credential is persisted for subsequent calls.
    stored = await store.get_credential(credential_id)
    assert stored.get("access_token") == "token-refreshed"


def test_consent_payload_scopes_claims_to_the_named_provider():
    """Regression: a Gmail connect banner must not list Slack claims.

    One turn can fail preflight for several providers at once (Gmail not
    connected + Slack missing claim approvals). The consent block names one
    provider action; foreign-provider claims leaking into it poisoned both
    the banner text and the Hub deep-link's OAuth claim selection
    ("unknown provider claim: slack:...").
    """
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
        connected_account_consent_payload,
    )

    payload = connected_account_consent_payload(
        tenant="demo-tenant",
        project="demo-project",
        connection_hub_bundle_id="connection-hub@1-0",
        missing=[
            {
                "ok": False,
                "tool_name": "gmail.search_gmail",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "google",
                        "connector_app_id": "gmail",
                        "claim": "gmail:read",
                        "error": "connect_required",
                        "retry_hint": True,
                    },
                    {
                        "ok": False,
                        "provider_id": "google",
                        "connector_app_id": "gmail",
                        "claim": "gmail:send",
                        "error": "connect_required",
                        "retry_hint": True,
                    },
                ],
            },
            {
                "ok": False,
                "tool_name": "slack.read_slack_channel_history",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "slack",
                        "connector_app_id": "demo",
                        "claim": "slack:history",
                        "error": "claim_upgrade_required",
                        "retry_hint": True,
                    }
                ],
            },
        ],
    )

    consent = payload["consent"]
    assert consent["provider_id"] == "google"
    assert consent["claims"] == ["gmail:read", "gmail:send"]
    assert "slack:history" not in consent["url"]
    assert "slack" not in payload["error"]["message"]
    # The other provider's failure stays visible in the raw missing list.
    assert any(item["tool_name"] == "slack.read_slack_channel_history" for item in payload["missing"])


def _multi_claim_oauth_config():
    """OAuth provider with three claims so a user-picked subset is observable
    in the requested scopes (the consent-plan claim checkboxes feed this)."""
    register_adapter(_FakeOAuthAdapter())
    return delegated_to_kdcube_config(
        {
            "enabled": True,
            "providers": {
                "test": {
                    "label": "Test Provider",
                    "adapter": "test.oauth",
                    "claims": {
                        "test:read": {"label": "Read", "provider_scopes": ["provider.read"]},
                        "test:write": {"label": "Write", "provider_scopes": ["provider.write"]},
                        "test:files": {
                            "label": "Files",
                            "provider_scopes": ["provider.files.read", "provider.files.write"],
                        },
                    },
                    "connector_apps": {
                        "default": {
                            "label": "Default OAuth app",
                            "client_id": "test-client",
                            "client_secret_ref": "connections.delegated_to_kdcube.providers.test.connector_apps.default.client_secret",
                            "allowed_claims": ["test:read", "test:write", "test:files"],
                        }
                    },
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_start_oauth_requests_scopes_for_the_selected_claims_only(monkeypatch):
    """Surfaced case: the consent plan lets the user untick requested claims;
    the OAuth start must ask the provider for exactly the picked claims'
    scopes (union, deduped)."""
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_multi_claim_oauth_config())

    started = await ops.start_oauth(
        {
            "provider_id": "test",
            "connector_app_id": "default",
            "claims": ["test:read", "test:files"],
        },
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=MemoryOAuthStateStore(),
        state_secret="state-secret",
    )

    assert started["ok"] is True
    assert started["claims"] == ["test:files", "test:read"]
    assert set(started["provider_scopes"]) == {
        "provider.read",
        "provider.files.read",
        "provider.files.write",
    }
    assert "provider.write" not in started["provider_scopes"]
    assert "provider.write" not in started["authorize_url"]

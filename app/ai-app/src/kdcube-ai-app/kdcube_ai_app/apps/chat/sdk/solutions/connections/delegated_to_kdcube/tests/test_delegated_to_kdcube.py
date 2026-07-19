# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import json
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

    async def get_user_prop(key: str, *, user_id: str | None = None, bundle_id: str | None = None, default: Any = None):
        return props.get((user_id or "", bundle_id or "", key), default)

    async def set_user_prop(key: str, value: Any, *, user_id: str | None = None, bundle_id: str | None = None):
        props[(user_id or "", bundle_id or "", key)] = value

    async def delete_user_prop(key: str, *, user_id: str | None = None, bundle_id: str | None = None):
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
    assert provider.connector_apps["gmail"].client_id == "client-id"
    assert provider.claims["gmail:read"].provider_scopes == ("https://www.googleapis.com/auth/gmail.readonly",)
    assert provider.connector_apps["gmail"].allowed_claims == ("gmail:read", "gmail:send")
    public = config.to_dict(include_client_ids=True)
    assert public["providers"]["google"]["connector_apps"]["gmail"]["client_id"] == "client-id"
    assert "client_secret_ref" not in public["providers"]["google"]["connector_apps"]["gmail"]


def _jwt_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    return f"header.{encoded}.signature"


def _generic_oauth_config():
    return delegated_to_kdcube_config(
        {
            "enabled": True,
            "providers": {
                "s1": {
                    "label": "S1",
                    "adapter": "oidc.generic",
                    "oauth": {
                        "authorize_url": "https://s1.example.test/oauth2/authorize",
                        "token_url": "https://s1.example.test/oauth2/token",
                        "userinfo_url": "https://s1.example.test/oauth2/userInfo",
                        "default_scopes": ["openid", "email", "profile"],
                        "authorize_params": {"audience": "s1-api"},
                        "profile": {
                            "subject": "sub",
                            "email": "email",
                            "display_name": "name",
                            "workspace": "custom.tenant",
                        },
                    },
                    "claims": {
                        "s1:read": {"label": "Read S1", "provider_scopes": ["s1.read"]},
                        "s1:write": {"label": "Write S1", "provider_scopes": ["s1.write"]},
                    },
                    "connector_apps": {
                        "default": {
                            "label": "S1 connector",
                            "client_id": "s1-client",
                            "client_secret_ref": "connections.delegated_to_kdcube.providers.s1.connector_apps.default.client_secret",
                            "allowed_claims": ["s1:read", "s1:write"],
                        }
                    },
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_generic_oauth_adapter_uses_provider_config_for_authorize_url(monkeypatch):
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_generic_oauth_config())

    started = await ops.start_oauth(
        {
            "provider_id": "s1",
            "connector_app_id": "default",
            "claims": ["s1:read"],
        },
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=MemoryOAuthStateStore(),
        state_secret="state-secret",
    )

    assert started["ok"] is True
    assert started["provider_scopes"] == ["s1.read"]
    assert started["authorize_url"].startswith("https://s1.example.test/oauth2/authorize?")
    assert "client_id=s1-client" in started["authorize_url"]
    assert "scope=s1.read" in started["authorize_url"]
    assert "audience=s1-api" in started["authorize_url"]


@pytest.mark.asyncio
async def test_generic_oidc_adapter_can_normalize_profile_from_id_token():
    provider = _generic_oauth_config().provider("s1")
    assert provider is not None
    adapter = resolve_adapter("oidc.generic").bind(provider=provider, connector_app=provider.connector_apps["default"])
    profile = await adapter.normalize_profile(
        {
            "id_token": _jwt_payload(
                {
                    "sub": "subject-1",
                    "email": "user@s1.example.test",
                    "name": "S1 User",
                    "custom": {"tenant": "tenant-a"},
                }
            )
        }
    )

    assert profile == {
        "external_subject": "subject-1",
        "email": "user@s1.example.test",
        "display_name": "S1 User",
        "workspace": "tenant-a",
    }


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
async def test_store_reads_double_encoded_account_props(monkeypatch):
    """Connection Hub catalogs must tolerate user props produced through the
    Postgres JSONB text read path, where historical records can be decoded to
    JSON strings rather than dict/list objects."""
    props, _secrets = _install_fake_storage(monkeypatch)
    store = DelegatedToKdcubeStore(user_id="user-1")
    account = ConnectedAccount(
        account_id="slack_1",
        provider_id="slack",
        connector_app_id="demo",
        external_subject="U123",
        display_name="User @ Workspace",
        workspace="T123",
        claims=("slack:post", "slack:read"),
        credential_id=credential_id_for("slack_1"),
    )
    props[("user-1", "connection-hub@1-0", "delegated_to_kdcube.account_index")] = json.dumps(["slack_1"])
    props[("user-1", "connection-hub@1-0", "delegated_to_kdcube.accounts.slack_1")] = json.dumps(account.to_dict())

    accounts = await store.list_accounts(provider_id="slack")
    loaded = await store.get_account("slack_1")

    assert [item.account_id for item in accounts] == ["slack_1"]
    assert loaded is not None
    assert loaded.display_name == "User @ Workspace"
    assert loaded.claims == ("slack:post", "slack:read")


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
async def test_broker_restricts_to_the_agents_allowed_accounts(monkeypatch):
    # The agent's per-provider account binding (account_scope) restricts which
    # connected account may satisfy a claim. Two Google accounts both allow
    # gmail:read; the agent is bound to acct-2 only.
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

    broker = DelegatedToKdcubeBroker(config=config, store=store)

    # Bound to acct-2 only -> the ambiguity is gone; it resolves to acct-2.
    bound = await broker.ensure_claim(
        provider_id="google", claim="gmail:read", account_claim_scope={"acct-2": ["*"]},
    )
    assert bound.ok is True and bound.account_id == "acct-2"

    # An explicit account_id OUTSIDE the allowed set is refused.
    denied = await broker.ensure_claim(
        provider_id="google", claim="gmail:read",
        account_id="acct-1", account_claim_scope={"acct-2": ["*"]},
    )
    assert denied.ok is False and denied.error == "account_required"

    # "*" (or None) means any account -> unchanged ambiguity across both.
    any_account = await broker.ensure_claim(
        provider_id="google", claim="gmail:read", account_claim_scope={"*": ["*"]},
    )
    assert any_account.ok is False and any_account.error == "account_required"
    assert [c["account_id"] for c in any_account.candidates] == ["acct-1", "acct-2"]


@pytest.mark.asyncio
async def test_broker_enforces_claims_per_account(monkeypatch):
    # The core per-account-claims case: BOTH accounts can send (their own claims
    # allow it), but the agent is bound read+write on acct-1 and read-only on
    # acct-2. The binding — not the account's capability — decides.
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
                claims=("gmail:read", "gmail:send"),  # both accounts CAN send
                credential_id=credential_id,
            )
        )
        await store.set_credential(credential_id, {"access_token": f"token-{account_id}"})

    broker = DelegatedToKdcubeBroker(config=config, store=store)
    scope = {"acct-1": ["gmail:read", "gmail:send"], "acct-2": ["gmail:read"]}

    # Send resolves only to acct-1 (acct-2 is read-only for THIS agent), so the
    # single candidate resolves without ambiguity.
    send = await broker.ensure_claim(
        provider_id="google", claim="gmail:send", account_claim_scope=scope,
    )
    assert send.ok is True and send.account_id == "acct-1"

    # Sending explicitly via acct-2 is refused even though acct-2 itself can send.
    send_acct2 = await broker.ensure_claim(
        provider_id="google", claim="gmail:send", account_id="acct-2", account_claim_scope=scope,
    )
    assert send_acct2.ok is False and send_acct2.error == "account_required"

    # Reading is allowed on both -> ambiguous across acct-1 and acct-2.
    read = await broker.ensure_claim(
        provider_id="google", claim="gmail:read", account_claim_scope=scope,
    )
    assert read.ok is False and read.error == "account_required"
    assert [c["account_id"] for c in read.candidates] == ["acct-1", "acct-2"]


@pytest.mark.asyncio
async def test_broker_bound_to_a_disconnected_account_needs_action(monkeypatch):
    # Bound to acct-2, but only acct-1 has the claim -> the restricted candidate
    # list is empty, so it asks to upgrade/connect the ALLOWED account, not to
    # silently use acct-1.
    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="user-1")
    cred1 = credential_id_for("acct-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-1", provider_id="google", connector_app_id="gmail",
            external_subject="sub-1", claims=("gmail:read",), credential_id=cred1,
        )
    )
    await store.set_credential(cred1, {"access_token": "token-1"})
    cred2 = credential_id_for("acct-2")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-2", provider_id="google", connector_app_id="gmail",
            external_subject="sub-2", claims=(), credential_id=cred2,  # no gmail:read
        )
    )
    await store.set_credential(cred2, {"access_token": "token-2"})

    result = await DelegatedToKdcubeBroker(config=config, store=store).ensure_claim(
        provider_id="google", claim="gmail:read", account_claim_scope={"acct-2": ["*"]},
    )
    assert result.ok is False
    # acct-2 is connected but lacks the claim -> claim upgrade on the allowed one.
    assert result.error == "claim_upgrade_required"


@pytest.mark.asyncio
async def test_client_wrapper_forwards_account_claim_scope(monkeypatch):
    # Regression: the DelegatedToKdcubeClient facade must accept AND forward
    # account_claim_scope to the broker. It once dropped the account binding, so
    # every agent-scoped resolve through the facade raised TypeError at runtime.
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

    client = connections.DelegatedToKdcubeClient(
        broker=DelegatedToKdcubeBroker(config=config, store=store)
    )

    # Bound to acct-2 -> the facade forwards the restriction; no ambiguity.
    bound = await client.ensure_claim(
        provider_id="google", claim="gmail:read", account_claim_scope={"acct-2": ["*"]},
    )
    assert bound.ok is True and bound.account_id == "acct-2"

    # An explicit account outside the allowed set is still refused through the facade.
    denied = await client.ensure_claim(
        provider_id="google", claim="gmail:read",
        account_id="acct-1", account_claim_scope={"acct-2": ["*"]},
    )
    assert denied.ok is False and denied.error == "account_required"


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


@pytest.mark.asyncio
async def test_start_oauth_for_an_existing_account_adds_to_held_claims(monkeypatch):
    """Surfaced case: each incremental consent replaced the account's whole
    claim set, wiping earlier approvals. With account_id, the default add
    mode unions requested + held claims (and requests the union's scopes);
    claims_mode=replace keeps the requested set authoritative, so the manage
    form can remove access by unticking."""
    _install_fake_storage(monkeypatch)
    ops = operations_for_user(user_id="user-1", config=_multi_claim_oauth_config())
    held = await ops.store.upsert_account(
        ConnectedAccount(
            account_id="",
            provider_id="test",
            connector_app_id="default",
            external_subject="subject-1",
            claims=("test:read",),
        )
    )

    added = await ops.start_oauth(
        {
            "provider_id": "test",
            "connector_app_id": "default",
            "claims": ["test:files"],
            "account_id": held.account_id,
        },
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=MemoryOAuthStateStore(),
        state_secret="state-secret",
    )
    assert added["ok"] is True
    assert added["claims"] == ["test:files", "test:read"]
    assert "provider.read" in added["provider_scopes"]

    replaced = await ops.start_oauth(
        {
            "provider_id": "test",
            "connector_app_id": "default",
            "claims": ["test:files"],
            "account_id": held.account_id,
            "claims_mode": "replace",
        },
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=MemoryOAuthStateStore(),
        state_secret="state-secret",
    )
    assert replaced["ok"] is True
    assert replaced["claims"] == ["test:files"]
    assert "provider.read" not in replaced["provider_scopes"]


def test_consent_payload_lists_the_blocked_tools_for_its_provider():
    """The banner's second option ("turn off the tools that need it") needs the
    blocked tool names — scoped to the consent's provider, with tools failed by
    another provider staying out."""
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
                "tool_name": "slack.upload_slack_file",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "slack",
                        "connector_app_id": "demo",
                        "claim": "slack:files:write",
                        "error": "claim_upgrade_required",
                        "retry_hint": True,
                    }
                ],
            },
            {
                "ok": False,
                "tool_name": "slack.post_slack_message",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "slack",
                        "connector_app_id": "demo",
                        "claim": "slack:post",
                        "error": "claim_upgrade_required",
                        "retry_hint": True,
                    }
                ],
            },
            {
                "ok": False,
                "tool_name": "gmail.send_gmail",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "google",
                        "connector_app_id": "gmail",
                        "claim": "gmail:send",
                        "error": "connect_required",
                        "retry_hint": True,
                    }
                ],
            },
        ],
    )

    consent = payload["consent"]
    assert consent["provider_id"] == "slack"
    assert consent["tools"] == ["slack.upload_slack_file", "slack.post_slack_message"]
    # The full cross-provider tool list stays available at the payload top.
    assert "gmail.send_gmail" in payload["tools"]


@pytest.mark.asyncio
async def test_claim_upgrade_becomes_visible_to_the_next_resolution(monkeypatch):
    """Surfaced live: connect read-only -> approve the write claims via the
    consent plan -> the NEXT resolution must see the write claims on the SAME
    account (verdict ok, zero consent_required), with the union persisted."""
    _install_fake_storage(monkeypatch)
    config = _multi_claim_oauth_config()
    ops = operations_for_user(user_id="user-1", config=config)
    state_store = MemoryOAuthStateStore()

    async def _oauth(claims):
        started = await ops.start_oauth(
            {"provider_id": "test", "connector_app_id": "default", "claims": claims},
            user_id="user-1",
            callback_url="https://kdcube.example.test/oauth/callback",
            state_store=state_store,
            state_secret="state-secret",
        )
        return await ops.complete_oauth(
            code="code-1",
            state=started["authorize_url"].split("state=", 1)[1].split("&", 1)[0],
            callback_url="https://kdcube.example.test/oauth/callback",
            state_store=state_store,
            state_secret="state-secret",
            client_secret_resolver=lambda **kwargs: "test-secret",
        )

    # 1. Read-only connect (search works, write claims withheld).
    first = await _oauth(["test:read"])
    account_id = first["account"]["account_id"]
    store = ops.store
    broker = DelegatedToKdcubeBroker(config=config, store=store)

    read_ok = await broker.ensure_claim(provider_id="test", claim="test:read")
    write_blocked = await broker.ensure_claim(provider_id="test", claim="test:write")
    assert read_ok.ok is True
    assert write_blocked.ok is False
    # Account exists -> the verdict is the upgrade, with connect stays out.
    assert write_blocked.error == "claim_upgrade_required"

    # 2. Approve the write claims (the consent plan submits held ∪ ticked).
    second = await _oauth(["test:read", "test:write"])

    # Same account, union persisted.
    assert second["account"]["account_id"] == account_id
    assert sorted(second["account"]["claims"]) == ["test:read", "test:write"]

    # 3. Next resolution sees the write claim live — no consent required.
    write_ok = await broker.ensure_claim(provider_id="test", claim="test:write")
    assert write_ok.ok is True
    assert write_ok.account_id == account_id
    read_still_ok = await broker.ensure_claim(provider_id="test", claim="test:read")
    assert read_still_ok.ok is True


@pytest.mark.asyncio
async def test_credential_read_goes_through_the_process_secret_cache(monkeypatch):
    """The credential secret may be rewritten by ANOTHER process at consent
    time; this process's 120s secret cache must be dropped before each
    credential read so the runtime always judges with live facts."""
    _install_fake_storage(monkeypatch)
    cleared: list[dict[str, Any]] = []

    def _record_clear(**kwargs):
        cleared.append(kwargs)
        return 0

    monkeypatch.setattr(ui_store.sdk_config, "clear_secret_cache", _record_clear)
    store = DelegatedToKdcubeStore(user_id="user-1")
    await store.set_credential("cred-x", {"access_token": "t", "claims": ["test:read"]})

    value = await store.get_credential("cred-x")
    assert value["access_token"] == "t"
    assert cleared, "get_credential clears the process-local secret cache before reading"
    assert cleared[0].get("user_id") == "user-1"


@pytest.mark.asyncio
async def test_claim_coverage_is_read_only_and_per_tool(monkeypatch):
    """Picker consent state: each tool's declared claims against the user's
    connected accounts — account records only (zero credential reads, zero
    events), per tool, connector-scoped."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        claim_coverage_for_policies,
    )

    _install_fake_storage(monkeypatch)
    store = DelegatedToKdcubeStore(user_id="user-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-slack",
            provider_id="slack",
            connector_app_id="demo",
            external_subject="u-slack",
            claims=("slack:search", "slack:channels"),
            credential_id=credential_id_for("acct-slack"),
        )
    )

    credential_reads = []

    async def _no_credential_reads(self, credential_id):
        credential_reads.append(credential_id)
        return {}

    monkeypatch.setattr(DelegatedToKdcubeStore, "get_credential", _no_credential_reads)

    policies = [
        ToolClaimPolicy.from_config("slack.search_slack", {
            "connected_accounts": [
                {"provider_id": "slack", "connector_app_id": "demo", "claims": ["slack:search"]},
            ],
        }),
        ToolClaimPolicy.from_config("slack.post_slack_message", {
            "connected_accounts": [
                {"provider_id": "slack", "connector_app_id": "demo", "claims": ["slack:post"]},
            ],
        }),
        ToolClaimPolicy.from_config("gmail.search_gmail", {
            "connected_accounts": [
                {"provider_id": "google", "connector_app_id": "gmail", "claims": ["gmail:read"]},
            ],
        }),
    ]
    coverage = await claim_coverage_for_policies(user_id="user-1", policies=policies)

    assert coverage["slack.search_slack"] == {
        "provider_id": "slack", "connector_app_id": "demo",
        "claims": ["slack:search"], "unmet": [], "covered": True,
    }
    assert coverage["slack.post_slack_message"]["unmet"] == ["slack:post"]
    assert coverage["slack.post_slack_message"]["covered"] is False
    # A provider with zero connected accounts: everything unmet.
    assert coverage["gmail.search_gmail"]["unmet"] == ["gmail:read"]
    # Read-only contract: the computation touched no credentials.
    assert credential_reads == []


def test_consent_bookkeeping_stays_off_the_event_loop():
    """All consent bookkeeping reaches the native async user-props API."""
    import inspect
    import pathlib

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import consent_demand

    assert inspect.iscoroutinefunction(consent_demand.read_pending_consent)
    assert inspect.iscoroutinefunction(consent_demand.write_pending_consent)
    assert inspect.iscoroutinefunction(consent_demand.record_consent_demand)
    assert inspect.iscoroutinefunction(consent_demand.claim_coverage_for_policies)

    for module in (consent_demand,):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "asyncio.run(" not in source, "no nested event loops in the consent path"
        assert "asyncio.to_thread" not in source, "user props use native async storage"
        assert "await sdk_config.get_user_prop" in source
        assert "await sdk_config.set_user_prop" in source

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import store as store_mod

    store_source = pathlib.Path(store_mod.__file__).read_text(encoding="utf-8")
    assert "list_accounts_sync" not in store_source
    assert "await sdk_config.get_user_prop" in store_source


class _FakeLaneSource:
    """Stands in for RedisConversationExternalEventSource in tests."""

    def __init__(self, entry, fail: bool = False):
        self.entry = dict(entry)
        self.fail = fail
        self.published: list[dict] = []

    async def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("lane unavailable")
        self.published.append(kwargs)
        return kwargs


def _demand_kwargs(**overrides):
    base = dict(
        user_id="user-1",
        bundle_id="workspace@test",
        conversation_id="conv-1",
        provider_id="slack",
        provider_label="Slack",
        connector_app_id="demo",
        claims=["slack:post"],
        tool_name="slack.post_slack_message",
        tenant="demo-tenant",
        project="demo-project",
        agent_id="main",
        connection_hub_bundle_id="connection-hub@1-0",
    )
    base.update(overrides)
    return base


def _install_async_user_prop_store(monkeypatch, sdk_config, store: dict) -> None:
    async def get_user_prop(key, *, user_id=None, bundle_id=None, default=None):
        return store.get((user_id, bundle_id, key), default)

    async def set_user_prop(key, value, *, user_id=None, bundle_id=None):
        store[(user_id, bundle_id, key)] = value

    async def delete_user_prop(key, *, user_id=None, bundle_id=None):
        store.pop((user_id, bundle_id, key), None)

    monkeypatch.setattr(sdk_config, "get_user_prop", get_user_prop)
    monkeypatch.setattr(sdk_config, "set_user_prop", set_user_prop)
    monkeypatch.setattr(sdk_config, "delete_user_prop", delete_user_prop)


@pytest.mark.asyncio
async def test_consent_demand_records_the_conversation_address(monkeypatch):
    """(a) The demand registry entry carries everything needed to author the
    granted event back into the conversation later."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        PENDING_DEMANDS_REGISTRY_KEY,
        record_consent_demand,
    )

    from kdcube_ai_app.apps.chat.sdk import config as sdk_config
    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    assert await record_consent_demand(**_demand_kwargs()) is True

    registry = store[("user-1", "connection-hub@1-0", PENDING_DEMANDS_REGISTRY_KEY)]
    entry = registry["demands"][0]
    assert entry["conversation_id"] == "conv-1"
    assert entry["tenant"] == "demo-tenant"
    assert entry["project"] == "demo-project"
    assert entry["bundle_id"] == "workspace@test"
    assert entry["agent_id"] == "main"
    assert entry["provider_id"] == "slack"
    assert entry["claims"] == ["slack:post"]
    assert entry["tool_name"] == "slack.post_slack_message"


@pytest.mark.asyncio
async def test_consent_completion_authors_one_event_per_demand_and_clears(monkeypatch):
    """(b)(c)(d) Completion authors exactly one lane event per matching demand
    with the granted payload, no task payload (passive: the promoter acks it,
    nothing resembling a turn can start), and clears the records — a second
    completion authors nothing (one event per demand)."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        author_consent_granted_events,
        read_pending_consent,
        record_consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    await record_consent_demand(**_demand_kwargs())

    sources: list[_FakeLaneSource] = []

    def factory(entry):
        source = _FakeLaneSource(entry)
        sources.append(source)
        return source

    authored = await author_consent_granted_events(
        redis=None,
        user_id="user-1",
        provider_id="slack",
        granted_claims=["slack:post", "slack:search"],
        connector_app_id="demo",
        account_id="acct-1",
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=factory,
    )
    assert authored == 1
    assert len(sources) == 1 and len(sources[0].published) == 1
    event = sources[0].published[0]
    # Transport kind is the uniform lane envelope ("external_event") so the
    # react timeline fold renders the event as a visible block — the semantic
    # type rides nested in payload.event.type (followup shape is the
    # reference behavior).
    assert event["kind"] == "external_event"
    assert event["event_source_id"] == "connection_hub.consent"
    # Passive by construction: no task payload -> the promoter acks; no turn.
    assert event["task_payload"] is None
    payload = event["payload"]
    assert payload["provider_id"] == "slack"
    assert payload["connector_app_id"] == "demo"
    assert payload["claims"] == ["slack:post"]
    assert payload["account_id"] == "acct-1"
    assert payload["tools"] == ["slack.post_slack_message"]
    assert payload["text"] == event["text"]
    nested = payload["event"]
    assert nested["type"] == "connections.consent.granted"
    assert nested["event_source_id"] == "connection_hub.consent"
    assert nested["reactive"] is False
    assert nested["timestamp"].endswith("Z")
    # The nested payload.event carries the model-facing sentence + grant facts
    # (the timeline fold surfaces it as the event block's `ret` body).
    nested_body = nested["payload"]["event"]
    assert nested_body["text"] == event["text"]
    assert nested_body["claims"] == ["slack:post"]
    assert nested_body["tools"] == ["slack.post_slack_message"]
    assert "approved Slack access (slack:post)" in event["text"]
    assert "post_slack_message" in event["text"]
    assert "usable now" in event["text"]

    # (e) The conversation's pending snapshot lost the event-covered tool —
    # the turn-start announce stays silent; the event is the record.
    pending = await read_pending_consent(user_id="user-1", bundle_id="workspace@test", conversation_id="conv-1")
    assert pending == []

    # One event per demand: consenting again authors nothing.
    again = await author_consent_granted_events(
        redis=None,
        user_id="user-1",
        provider_id="slack",
        granted_claims=["slack:post", "slack:search"],
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=factory,
    )
    assert again == 0


@pytest.mark.asyncio
async def test_publish_failure_keeps_the_record_for_the_announce_fallback(monkeypatch):
    """(f) When the lane publish fails, the demand stays recorded — the
    turn-start [CONNECTED ACCOUNTS UPDATE] announce covers the grant."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        PENDING_DEMANDS_REGISTRY_KEY,
        author_consent_granted_events,
        read_pending_consent,
        record_consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    await record_consent_demand(**_demand_kwargs())

    authored = await author_consent_granted_events(
        redis=None,
        user_id="user-1",
        provider_id="slack",
        granted_claims=["slack:post"],
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=lambda entry: _FakeLaneSource(entry, fail=True),
    )
    assert authored == 0
    registry = store[("user-1", "connection-hub@1-0", PENDING_DEMANDS_REGISTRY_KEY)]
    assert len(registry["demands"]) == 1
    pending = await read_pending_consent(user_id="user-1", bundle_id="workspace@test", conversation_id="conv-1")
    assert pending and pending[0]["tools"] == ["slack.post_slack_message"]


@pytest.mark.asyncio
async def test_unrelated_grants_author_nothing(monkeypatch):
    """(f cont.) A grant with no recorded demand (proactive menu consent, or a
    different provider/claims) authors no event; records stay untouched."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        author_consent_granted_events,
        record_consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    # No demand at all -> proactive consent authors nothing.
    sources: list = []
    authored = await author_consent_granted_events(
        redis=None, user_id="user-1", provider_id="google", granted_claims=["gmail:read"],
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=lambda entry: sources.append(entry),
    )
    assert authored == 0 and sources == []

    # A demand for ANOTHER provider stays untouched by this grant.
    await record_consent_demand(**_demand_kwargs())
    authored = await author_consent_granted_events(
        redis=None, user_id="user-1", provider_id="google", granted_claims=["gmail:read"],
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=lambda entry: sources.append(entry),
    )
    assert authored == 0 and sources == []


@pytest.mark.asyncio
async def test_agent_grant_closes_only_that_agents_demand(monkeypatch):
    """Per-agent consent (Delegated BY KDCube): the demand records the agent's
    client id in the connector slot, and the grant — which passes the same
    client id — authors the granted event for THAT agent's demand only. The
    other agent's identical-claims demand stays open. Regression: the agent
    grant path used to author no event at all (the conversation never learned
    the consent landed)."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        PENDING_DEMANDS_REGISTRY_KEY,
        author_consent_granted_events,
        record_consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    agent_a = "kdcube-agent:workspace@2026-03-31-13-36:main"
    agent_b = "kdcube-agent:lg-react@2026-07-13:main"
    await record_consent_demand(**_demand_kwargs(
        provider_id="kdcube", provider_label="KDCube",
        connector_app_id=agent_a, claims=["memories:read"], tool_name="mem",
    ))
    await record_consent_demand(**_demand_kwargs(
        provider_id="kdcube", provider_label="KDCube",
        connector_app_id=agent_b, claims=["memories:read"], tool_name="mem",
        conversation_id="conv-2",
    ))

    sources: list[_FakeLaneSource] = []

    def factory(entry):
        source = _FakeLaneSource(entry)
        sources.append(source)
        return source

    authored = await author_consent_granted_events(
        redis=None,
        user_id="user-1",
        provider_id="kdcube",
        granted_claims=["memories:read"],
        connector_app_id=agent_a,
        account_id="agent-abc",
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=factory,
    )
    assert authored == 1
    assert len(sources) == 1 and sources[0].entry["conversation_id"] == "conv-1"
    event = sources[0].published[0]
    assert "approved KDCube access (memories:read)" in event["text"]
    assert event["payload"]["connector_app_id"] == agent_a

    # Agent B's demand survives — granting one agent never closes another's.
    registry = store[("user-1", "connection-hub@1-0", PENDING_DEMANDS_REGISTRY_KEY)]
    assert [d["connector_app_id"] for d in registry["demands"]] == [agent_b]


@pytest.mark.asyncio
async def test_connect_credential_fires_the_consent_granted_notifier(monkeypatch):
    """Hub wiring: a persisted consent (OAuth complete AND the credential-form
    path both land here) hands the granted facts to the notifier that authors
    the conversation events."""
    _install_fake_storage(monkeypatch)
    granted: list[dict] = []

    async def notifier(**kwargs):
        granted.append(kwargs)

    ops = operations_for_user(
        user_id="user-1",
        config=_multi_claim_oauth_config(),
        consent_granted_notifier=notifier,
    )
    state_store = MemoryOAuthStateStore()
    started = await ops.start_oauth(
        {"provider_id": "test", "connector_app_id": "default", "claims": ["test:read", "test:write"]},
        user_id="user-1",
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=state_store,
        state_secret="state-secret",
    )
    completed = await ops.complete_oauth(
        code="code-1",
        state=started["authorize_url"].split("state=", 1)[1].split("&", 1)[0],
        callback_url="https://kdcube.example.test/oauth/callback",
        state_store=state_store,
        state_secret="state-secret",
        client_secret_resolver=lambda **kwargs: "test-secret",
    )
    assert completed["ok"] is True
    assert len(granted) == 1
    assert granted[0]["provider_id"] == "test"
    assert sorted(granted[0]["claims"]) == ["test:read", "test:write"]
    assert granted[0]["account_id"] == completed["account"]["account_id"]


# ── Conversation-less demands (external MCP attempts) ────────────────────────
# An MCP named-service attempt has no chat conversation. Demand bookkeeping is
# a CONVERSATION fact: with no address there is no banner to raise and no lane
# to author the granted event into — record nothing, author nothing, never
# error. The MCP client's consent loop is response + link + retry only.


@pytest.mark.asyncio
async def test_record_consent_demand_without_conversation_records_nothing(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        record_consent_demand,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {}
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    assert await record_consent_demand(**_demand_kwargs(conversation_id="")) is False
    assert await record_consent_demand(**_demand_kwargs(conversation_id="   ")) is False
    assert await record_consent_demand(**_demand_kwargs(user_id="")) is False
    assert await record_consent_demand(**_demand_kwargs(bundle_id="")) is False
    # No pending snapshot, no hub-registry entry — nothing at all.
    assert store == {}


@pytest.mark.asyncio
async def test_grant_drops_legacy_addressless_demand_without_authoring(monkeypatch):
    """A registry entry with no conversation address (legacy write) resolves
    on grant by dropping: no lane event, no error, registry cleaned; addressed
    demands in the same grant still author normally."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        PENDING_DEMANDS_REGISTRY_KEY,
        author_consent_granted_events,
    )
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    store: dict = {
        ("user-1", "connection-hub@1-0", PENDING_DEMANDS_REGISTRY_KEY): {
            "demands": [
                {
                    # Legacy poison: no conversation address.
                    "conversation_id": "",
                    "tenant": "demo-tenant",
                    "project": "demo-project",
                    "bundle_id": "workspace@test",
                    "agent_id": "",
                    "provider_id": "slack",
                    "connector_app_id": "demo",
                    "claims": ["slack:post"],
                    "tool_name": "slack.post_slack_message",
                },
                {
                    "conversation_id": "conv-1",
                    "tenant": "demo-tenant",
                    "project": "demo-project",
                    "bundle_id": "workspace@test",
                    "agent_id": "main",
                    "provider_id": "slack",
                    "connector_app_id": "demo",
                    "claims": ["slack:post"],
                    "tool_name": "slack.post_slack_message",
                },
            ]
        }
    }
    _install_async_user_prop_store(monkeypatch, sdk_config, store)

    sources: list[_FakeLaneSource] = []

    def factory(entry):
        source = _FakeLaneSource(entry)
        sources.append(source)
        return source

    authored = await author_consent_granted_events(
        redis=None,
        user_id="user-1",
        provider_id="slack",
        granted_claims=["slack:post"],
        connector_app_id="demo",
        account_id="acct-1",
        connection_hub_bundle_id="connection-hub@1-0",
        source_factory=factory,
    )

    # Only the ADDRESSED demand authored an event.
    assert authored == 1
    assert len(sources) == 1
    assert sources[0].entry["conversation_id"] == "conv-1"
    # Both entries left the registry: the addressed one by authoring, the
    # address-less one by dropping.
    assert ("user-1", "connection-hub@1-0", PENDING_DEMANDS_REGISTRY_KEY) not in store


# ── Consent deep link is absolute ────────────────────────────────────────────
# The deep link travels beyond the app origin (external MCP agents relay it
# verbatim), so the shared payload builder prefixes the deployment's public
# base URL — the same `connections.oauth.public_base_url` source of truth
# OAuth redirect building uses. Chat and MCP agree because the builder is one.


def test_consent_payload_url_is_absolute_with_public_base():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
        connected_account_consent_payload,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
        set_connection_hub_public_base_url,
    )

    set_connection_hub_public_base_url("https://demo.kdcube.example/")
    try:
        payload = connected_account_consent_payload(
            tenant="demo-tenant",
            project="demo-project",
            connection_hub_bundle_id="connection-hub@1-0",
            missing=[
                {
                    "ok": False,
                    "tool_name": "slack.search_slack",
                    "failures": [
                        {
                            "ok": False,
                            "provider_id": "slack",
                            "connector_app_id": "demo",
                            "claim": "slack:search",
                            "error": "connect_required",
                            "retry_hint": True,
                        }
                    ],
                }
            ],
        )
    finally:
        set_connection_hub_public_base_url("")

    url = payload["consent"]["url"]
    assert url.startswith("https://demo.kdcube.example/api/integrations/bundles/")
    assert "tab=delegated_to_kdcube" in url
    assert "provider_id=slack" in url
    assert "claims=slack%3Asearch" in url
    assert "tool_name=slack.search_slack" in url


def test_consent_payload_url_stays_relative_without_public_base_with_params_intact():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
        connected_account_consent_payload,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
        set_connection_hub_public_base_url,
    )

    set_connection_hub_public_base_url("")
    payload = connected_account_consent_payload(
        tenant="demo-tenant",
        project="demo-project",
        connection_hub_bundle_id="connection-hub@1-0",
        missing=[
            {
                "ok": False,
                "tool_name": "slack.search_slack",
                "failures": [
                    {
                        "ok": False,
                        "provider_id": "slack",
                        "connector_app_id": "demo",
                        "claim": "slack:search",
                        "error": "connect_required",
                        "retry_hint": True,
                    }
                ],
            }
        ],
    )

    url = payload["consent"]["url"]
    assert url.startswith("/api/integrations/bundles/demo-tenant/demo-project/")
    assert "tab=delegated_to_kdcube" in url
    assert "provider_id=slack" in url
    assert "claims=slack%3Asearch" in url


def test_hub_props_public_base_extraction():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
        public_base_url_from_hub_props,
    )

    props = {"connections": {"oauth": {"public_base_url": "https://demo.kdcube.example/"}}}
    assert public_base_url_from_hub_props(props) == "https://demo.kdcube.example"
    assert public_base_url_from_hub_props({}) == ""
    assert public_base_url_from_hub_props(None) == ""
    assert public_base_url_from_hub_props({"connections": {}}) == ""


@pytest.mark.asyncio
async def test_claim_coverage_decorates_namespace_policies_from_realm_requirements(monkeypatch):
    """Realm-backed namespaces (mail -> google claims, slack -> slack claims)
    get the same read-only coverage as dedicated tools: a namespace-named
    policy synthesized from the realm's declared requirements resolves to
    covered/unmet against the user's connected accounts."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand import (
        claim_coverage_for_policies,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import (
        MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
    )
    from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import (
        SLACK_CONNECTED_ACCOUNT_REQUIREMENTS,
    )

    _install_fake_storage(monkeypatch)
    store = DelegatedToKdcubeStore(user_id="user-1")
    await store.upsert_account(
        ConnectedAccount(
            account_id="acct-gmail",
            provider_id="google",
            connector_app_id="gmail",
            external_subject="u-gmail",
            claims=("gmail:read",),
            credential_id=credential_id_for("acct-gmail"),
        )
    )

    policies = [
        ToolClaimPolicy.from_config("mail", {"connected_accounts": MAIL_CONNECTED_ACCOUNT_REQUIREMENTS}),
        ToolClaimPolicy.from_config("slack", {"connected_accounts": SLACK_CONNECTED_ACCOUNT_REQUIREMENTS}),
    ]
    coverage = await claim_coverage_for_policies(user_id="user-1", policies=policies)

    assert coverage["mail"]["provider_id"] == "google"
    assert coverage["mail"]["unmet"] == ["gmail:send"]
    assert coverage["mail"]["covered"] is False
    # No slack account at all: the whole realm claim set is unmet.
    assert coverage["slack"]["covered"] is False
    assert "slack:history" in coverage["slack"]["unmet"]

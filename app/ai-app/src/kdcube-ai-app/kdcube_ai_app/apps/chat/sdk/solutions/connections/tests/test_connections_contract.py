# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Contract test: drive a fake provider through ConnectionsClient (local).

The fake provider is backed by an in-memory dict — no ConnectionStore, no
network, no secrets — so this exercises only the named-service contract and the
typed dataclass round-trips.
"""

from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRegistry,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections import (
    NAMESPACE,
    AmbiguousConnectionAccount,
    CatalogEntry,
    ClientApp,
    Connection,
    ConnectionToken,
    ConnectionsClient,
    ConnectionsError,
    ConnectionsProviderBase,
    build_connection_operations,
)


@named_service_provider(
    provider_id="connections",
    bundle_id="fake@1-0",
    namespace=NAMESPACE,
    operations=build_connection_operations((TRANSPORT_LOCAL, TRANSPORT_API)),
)
class FakeConnectionsProvider(ConnectionsProviderBase):
    def __init__(self) -> None:
        super().__init__()
        # provider -> account_id -> {account fields, "token": {...}}
        self._accounts: dict[str, dict[str, dict[str, Any]]] = {
            "slack": {
                "slack_acc1": {
                    "account_id": "slack_acc1",
                    "provider": "slack",
                    "external_user_id": "U123",
                    "workspace": "T999",
                    "display_name": "alice @ acme",
                    "status": "connected",
                    "scope": ["search:read"],
                    "token": {"access_token": "xoxb-secret", "scope": ["search:read"]},
                }
            },
            "gmail": {},
        }

    def _accounts_for(self, provider: str) -> list[Connection]:
        out: list[Connection] = []
        for acc in self._accounts.get(provider, {}).values():
            data = dict(acc)
            data["has_token"] = bool(acc.get("token"))
            out.append(Connection.from_dict(data))
        return out

    async def list_catalog(self, ctx: NamedServiceContext) -> list[CatalogEntry]:
        entries: list[CatalogEntry] = []
        for provider in ("slack", "gmail"):
            accounts = tuple(self._accounts_for(provider))
            apps = (
                ClientApp(app_id=f"{provider}-app", provider=provider, label=f"{provider.title()} App", enabled=True),
            )
            entries.append(
                CatalogEntry(
                    provider=provider,
                    label=provider.title(),
                    enabled=True,
                    configured=True,
                    connected=any(a.has_token for a in accounts),
                    apps=apps,
                    accounts=accounts,
                )
            )
        return entries

    async def status(self, ctx: NamedServiceContext, *, provider: str) -> dict[str, Any]:
        accounts = self._accounts_for(provider)
        return {
            "ok": True,
            "provider": provider,
            "enabled": True,
            "configured": True,
            "accounts": [a.to_dict() for a in accounts],
        }

    async def get_token(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str | None = None,
    ) -> ConnectionToken | None:
        accounts = self._accounts.get(provider, {})
        if account_id:
            acc = accounts.get(account_id)
        else:
            connected = [a for a in accounts.values() if a.get("token")]
            if not connected:
                return None
            if len(connected) > 1:
                raise AmbiguousConnectionAccount(provider, [a["account_id"] for a in connected])
            acc = connected[0]
        if not acc or not acc.get("token"):
            return None
        return ConnectionToken.from_dict(acc["token"])

    async def disconnect(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str,
    ) -> dict[str, Any]:
        deleted = self._accounts.get(provider, {}).pop(account_id, None) is not None
        return {
            "ok": True,
            "provider": provider,
            "deleted": deleted,
            "accounts": [a.to_dict() for a in self._accounts_for(provider)],
        }

    async def start_oauth(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        app_id: str | None = None,
        scopes: list[str] | None = None,
        return_hint: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "app_id": app_id or f"{provider}-app",
            "scopes": list(scopes or []),
            "authorize_url": f"https://example.test/oauth/{provider}/authorize?app={app_id or ''}&hint={return_hint}",
        }


def _client() -> ConnectionsClient:
    registry = NamedServiceRegistry()
    registry.register(FakeConnectionsProvider())
    return ConnectionsClient(registry, transport=TRANSPORT_LOCAL, context=NamedServiceContext())


@pytest.mark.asyncio
async def test_get_token_round_trip():
    connections = _client()
    token = await connections.get_token("slack")
    assert isinstance(token, ConnectionToken)
    assert token.access_token == "xoxb-secret"
    assert token.scope == ("search:read",)


@pytest.mark.asyncio
async def test_get_token_missing_returns_none():
    connections = _client()
    assert await connections.get_token("gmail") is None


@pytest.mark.asyncio
async def test_catalog_returns_typed_entries():
    connections = _client()
    entries = await connections.catalog()
    assert {e.provider for e in entries} == {"slack", "gmail"}
    slack = next(e for e in entries if e.provider == "slack")
    assert isinstance(slack, CatalogEntry)
    assert slack.connected is True
    assert slack.accounts and isinstance(slack.accounts[0], Connection)
    assert slack.accounts[0].account_id == "slack_acc1"
    # The catalog entry exposes its client apps (the middle level).
    assert slack.apps and isinstance(slack.apps[0], ClientApp)
    assert slack.apps[0].app_id == "slack-app"
    gmail = next(e for e in entries if e.provider == "gmail")
    assert gmail.connected is False


@pytest.mark.asyncio
async def test_status():
    connections = _client()
    info = await connections.status("slack")
    assert info["ok"] is True
    assert info["provider"] == "slack"


@pytest.mark.asyncio
async def test_oauth_start_returns_authorize_url():
    connections = _client()
    result = await connections.start_oauth("slack", return_hint="back")
    assert result["authorize_url"].startswith("https://example.test/oauth/slack/authorize")


@pytest.mark.asyncio
async def test_get_token_multi_account_requires_account_id():
    # A user with several connected accounts for one provider (e.g. two Slack
    # workspaces): get_token without account_id must NOT silently pick one.
    registry = NamedServiceRegistry()
    prov = FakeConnectionsProvider()
    prov._accounts["slack"]["slack_acc2"] = {
        "account_id": "slack_acc2", "provider": "slack", "external_user_id": "U456",
        "workspace": "T888", "display_name": "alice @ other", "status": "connected",
        "scope": ["search:read"], "token": {"access_token": "xoxb-second", "scope": ["search:read"]},
    }
    registry.register(prov)
    connections = ConnectionsClient(registry, transport=TRANSPORT_LOCAL, context=NamedServiceContext())
    with pytest.raises(ConnectionsError):
        await connections.get_token("slack")            # ambiguous → surfaced error
    token = await connections.get_token("slack", account_id="slack_acc2")  # explicit → resolves
    assert token is not None and token.access_token == "xoxb-second"


@pytest.mark.asyncio
async def test_disconnect():
    connections = _client()
    result = await connections.disconnect("slack", "slack_acc1")
    assert result["deleted"] is True
    # token is gone after disconnect
    assert await connections.get_token("slack") is None


@named_service_provider(
    provider_id="connections",
    bundle_id="fake@1-0",
    namespace=NAMESPACE,
    operations=build_connection_operations((TRANSPORT_LOCAL,)),
)
class _AgentGrantProvider(FakeConnectionsProvider):
    """Adds a per-agent grant: consent given only for one (client_id, resource)."""

    async def agent_grant_token(self, ctx, *, client_id, resource):
        if client_id == "kdcube-agent:app:lg-react" and resource == "https://h/mcp/mem":
            return ConnectionToken(access_token="agent-bound-tok", scope=("memories:read",))
        return None


def _agent_client() -> ConnectionsClient:
    registry = NamedServiceRegistry()
    registry.register(_AgentGrantProvider())
    return ConnectionsClient(registry, transport=TRANSPORT_LOCAL, context=NamedServiceContext())


@pytest.mark.asyncio
async def test_agent_grant_token_round_trip():
    connections = _agent_client()
    token = await connections.agent_grant_token("kdcube-agent:app:lg-react", "https://h/mcp/mem")
    assert isinstance(token, ConnectionToken)
    assert token.access_token == "agent-bound-tok"
    assert token.scope == ("memories:read",)


@pytest.mark.asyncio
async def test_agent_grant_token_pending_returns_none():
    connections = _agent_client()
    # A different agent has no grant -> consent pending -> None (not an error).
    assert await connections.agent_grant_token("kdcube-agent:app:other", "https://h/mcp/mem") is None


@pytest.mark.asyncio
async def test_agent_grant_default_provider_has_no_grants():
    # The base provider does not back per-agent grants -> concrete default None.
    connections = _client()
    assert await connections.agent_grant_token("kdcube-agent:app:lg-react", "https://h/mcp/mem") is None


@pytest.mark.asyncio
async def test_agent_grant_requires_client_id_and_resource():
    connections = _agent_client()
    with pytest.raises(ConnectionsError):
        await connections.agent_grant_token("", "https://h/mcp/mem")
    with pytest.raises(ConnectionsError):
        await connections.agent_grant_token("kdcube-agent:app:lg-react", "")

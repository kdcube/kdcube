# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Declaration parity for plain MCP tools: ``enforce_tool_requirements``
answers with the SAME demand ordering as the named-services door.

- every declared claim resolves -> None (the tool body proceeds);
- zero accounts on the backing provider -> the connect-first denial via the
  explicit ``requirements=`` path (no discovery);
- an account exists but cannot satisfy the call -> the account-level consent
  the resolver already produced.
"""

from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_tool_enforcement as enforcement_mod
from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_tool_enforcement import (
    enforce_tool_requirements,
)

_SLACK_REQUIREMENT = {"provider_id": "slack", "claims": ["slack:search"]}
_MAIL_REQUIREMENT = {"provider_id": "google", "claims": ["gmail:read"]}


def _view(monkeypatch, *, grantor="user-1", client="kdcube-agent:app:main"):
    monkeypatch.setattr(
        enforcement_mod,
        "delegated_credential_view",
        lambda request: SimpleNamespace(
            grantor_user_id=grantor,
            agent_client_id=client,
            client_id=client,
            resource="*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/productivity*",
        ),
    )


def _identity(monkeypatch):
    monkeypatch.setattr(
        enforcement_mod,
        "get_current_user_identity",
        lambda: {"tenant_id": "t", "project_id": "p", "user_id": "user-1"},
    )


def _resolver(monkeypatch, credential_by_claim):
    calls = []

    async def _resolve(source, *, provider_id, connector_app_id, claim, tool_name, **kwargs):
        calls.append({
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "claim": claim,
            "tool_name": tool_name,
            "account_id": kwargs.get("account_id", ""),
        })
        return credential_by_claim[claim]

    monkeypatch.setattr(enforcement_mod, "resolve_connected_account_claim", _resolve)
    return calls


def _ok_credential(claim, provider_id="slack"):
    return ConnectedAccountCredential(
        ok=True,
        access_token="token",
        provider_id=provider_id,
        claim=claim,
        account_id="acc-1",
    )


def _failed_credential(claim, provider_id="slack", reason="connect_required"):
    return ConnectedAccountCredential(
        ok=False,
        provider_id=provider_id,
        claim=claim,
        tool_name="productivity_slack_search",
        tenant="t",
        project="p",
        error_payload={
            "ok": False,
            "error": {"code": "needs_connected_account_consent", "message": "consent"},
            "consent": {"reason": reason, "provider_id": provider_id},
        },
    )


@pytest.mark.asyncio
async def test_all_claims_resolvable_returns_none(monkeypatch):
    _view(monkeypatch)
    _identity(monkeypatch)
    calls = _resolver(monkeypatch, {"slack:search": _ok_credential("slack:search")})

    async def _no_connect_first(**kwargs):  # pragma: no cover - must not be hit
        raise AssertionError("connect-first must not be consulted on success")

    monkeypatch.setattr(enforcement_mod, "connect_first_denial_for_identity", _no_connect_first)

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_slack_search",
        operation="search",
        requirements=[_SLACK_REQUIREMENT],
    )

    assert result is None
    assert calls == [{
        "provider_id": "slack",
        "connector_app_id": "",
        "claim": "slack:search",
        "tool_name": "productivity_slack_search",
        "account_id": "",
    }]


@pytest.mark.asyncio
async def test_zero_accounts_returns_connect_first_denial(monkeypatch):
    _view(monkeypatch)
    _identity(monkeypatch)
    _resolver(monkeypatch, {"slack:search": _failed_credential("slack:search")})

    seen = {}

    async def _connect_first(**kwargs):
        seen.update(kwargs)
        return {"ok": False, "reason": "connect_required", "retry_hint": True}

    monkeypatch.setattr(enforcement_mod, "connect_first_denial_for_identity", _connect_first)

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_slack_search",
        operation="search",
        requirements=[_SLACK_REQUIREMENT],
    )

    assert result == {"ok": False, "reason": "connect_required", "retry_hint": True}
    # The explicit requirements path: THIS requirement, no discovery.
    assert seen["requirements"] == [_SLACK_REQUIREMENT]
    assert seen["namespace"] == "productivity_slack_search"
    assert seen["tool"] == "productivity_slack_search"
    assert seen["operation"] == "search"
    assert seen["required"] == ["slack:search"]
    assert seen["missing"] == ["slack:search"]
    assert seen["grantor_user_id"] == "user-1"
    assert seen["agent_client_id"] == "kdcube-agent:app:main"
    assert seen["tenant"] == "t"
    assert seen["project"] == "p"


@pytest.mark.asyncio
async def test_account_present_returns_account_level_consent(monkeypatch):
    """Accounts exist (connect-first declines the ordering) -> the resolver's
    own consent envelope is the answer (claim upgrade / agent grant / ...)."""
    _view(monkeypatch)
    _identity(monkeypatch)
    failed = _failed_credential(
        "gmail:read", provider_id="google", reason="agent_grant_required"
    )
    _resolver(monkeypatch, {"gmail:read": failed})

    async def _connect_first(**kwargs):
        return None  # an account exists - ordering does not apply

    monkeypatch.setattr(enforcement_mod, "connect_first_denial_for_identity", _connect_first)

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_mail_search",
        operation="search",
        requirements=[_MAIL_REQUIREMENT],
    )

    assert result is not None
    assert result["ok"] is False
    assert result["consent_required"] is True
    assert result["error"]["code"] == "needs_connected_account_consent"
    assert result["consent"]["reason"] == "agent_grant_required"


@pytest.mark.asyncio
async def test_requirement_without_matching_operation_claims_is_skipped(monkeypatch):
    """claims_by_operation scopes the check: an operation with no mapped claims
    and no flat claims enforces nothing."""
    _view(monkeypatch)
    _identity(monkeypatch)

    async def _resolve(*args, **kwargs):  # pragma: no cover - must not be hit
        raise AssertionError("no claim should be resolved")

    monkeypatch.setattr(enforcement_mod, "resolve_connected_account_claim", _resolve)

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_mail_search",
        operation="about",
        requirements=[{
            "provider_id": "google",
            "claims": [],
            "claims_by_operation": {"search": ["gmail:read"]},
        }],
    )

    assert result is None


@pytest.mark.asyncio
async def test_account_required_is_returned_when_tool_input_is_ambiguous(monkeypatch):
    _view(monkeypatch)
    _identity(monkeypatch)
    _resolver(
        monkeypatch,
        {"slack:search": _failed_credential("slack:search", reason="account_required")},
    )

    async def _accounts_exist(**kwargs):
        return None

    monkeypatch.setattr(enforcement_mod, "connect_first_denial_for_identity", _accounts_exist)

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_slack_search",
        operation="search",
        requirements=[_SLACK_REQUIREMENT],
    )

    assert result is not None
    assert result["consent"]["reason"] == "account_required"


@pytest.mark.asyncio
async def test_explicit_account_id_reaches_the_shared_resolver(monkeypatch):
    _view(monkeypatch)
    _identity(monkeypatch)
    calls = _resolver(
        monkeypatch,
        {"slack:search": _ok_credential("slack:search")},
    )

    result = await enforce_tool_requirements(
        object(),
        tool_name="productivity_slack_search",
        operation="search",
        requirements=[_SLACK_REQUIREMENT],
        account_id="slack-account-2",
    )

    assert result is None
    assert calls[0]["account_id"] == "slack-account-2"

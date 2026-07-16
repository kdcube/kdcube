# SPDX-License-Identifier: MIT

"""Demand-driven consent at the tool ATTEMPT.

An invocation whose claim is unmet returns the structured consent envelope to
the agent (machine-readable consent + a short instruction to keep the turn
productive), emits ONE chat consent event scoped to that tool's claims (the
banner), and records the pending demand for the conversation's transition
check. Retrying the same tool stays quiet server-side.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts as ca
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ClaimResolution,
)


class _FakeComm:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def event(self, **kwargs):
        self.events.append(kwargs)


class _FakeClient:
    def __init__(self, resolution: ClaimResolution):
        self._resolution = resolution
        self.calls: list[dict[str, Any]] = []

    async def ensure_claim(self, **kwargs):
        self.calls.append(kwargs)
        return self._resolution


def _install_fakes(monkeypatch, resolution: ClaimResolution):
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import consent_demand
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    props: dict[tuple[str, str, str], Any] = {}

    async def get_user_prop(key, *, user_id=None, bundle_id=None, default=None):
        return props.get((user_id or "", bundle_id or "", key), default)

    async def set_user_prop(key, value, *, user_id=None, bundle_id=None):
        props[(user_id or "", bundle_id or "", key)] = value

    async def delete_user_prop(key, *, user_id=None, bundle_id=None):
        props.pop((user_id or "", bundle_id or "", key), None)

    monkeypatch.setattr(sdk_config, "get_user_prop", get_user_prop)
    monkeypatch.setattr(sdk_config, "set_user_prop", set_user_prop)
    monkeypatch.setattr(sdk_config, "delete_user_prop", delete_user_prop)

    monkeypatch.setattr(ca, "get_current_user_identity", lambda: {
        "tenant_id": "acme",
        "project_id": "demo",
        "user_id": "u1",
        "bundle_id": "workspace@test",
        "conversation_id": "conv-1",
    })

    client = _FakeClient(resolution)

    async def _from_connection_hub(cls_or_entrypoint, **kwargs):
        return client

    monkeypatch.setattr(
        ca.DelegatedToKdcubeClient, "from_connection_hub", classmethod(
            lambda cls, entrypoint, **kwargs: _from_connection_hub(entrypoint, **kwargs)
        ),
    )

    comm = _FakeComm()
    source = {
        "_SERVICE": SimpleNamespace(comm_context=None),
        "_TOOL_SUBSYSTEM": SimpleNamespace(registry={}, comm=None),
        "_COMMUNICATOR": comm,
    }
    return source, comm, props, client


def _unmet(claim: str) -> ClaimResolution:
    return ClaimResolution(
        ok=False,
        provider_id="slack",
        claim=claim,
        connector_app_id="demo",
        error="connect_required",
        message=f"Connect Slack and approve {claim}.",
        retry_hint=True,
    )


@pytest.mark.asyncio
async def test_attempt_returns_structured_consent_and_emits_the_scoped_banner(monkeypatch):
    source, comm, props, client = _install_fakes(monkeypatch, _unmet("slack:post"))

    credential = await ca.resolve_connected_account_claim(
        source,
        provider_id="slack",
        connector_app_id="demo",
        claim="slack:post",
        tool_name="slack.post_slack_message",
    )
    assert credential.ok is False

    # The agent-facing structured result (one contract with the MCP path).
    envelope = credential.error_envelope(where="slack.post_slack_message")
    assert envelope["ok"] is False
    assert envelope["consent_required"] is True
    assert envelope["error"]["code"] == "needs_connected_account_consent"
    assert "continue with the" in envelope["instructions"]
    consent = envelope["consent"]
    assert consent["provider_id"] == "slack"
    # Scoped to THIS tool: its claim and its name only.
    assert consent["claims"] == ["slack:post"]
    assert consent["tools"] == ["slack.post_slack_message"]
    assert "tab=delegated_to_kdcube" in consent["url"]
    assert "claims=slack%3Apost" in consent["url"]

    # ONE chat consent event, carrying the same payload (the banner source).
    assert len(comm.events) == 1
    event = comm.events[0]
    assert event["step"] == "delegated_to_kdcube.consent"
    assert event["agent"] == "connection-hub"
    assert event["data"]["error"]["code"] == "needs_connected_account_consent"
    assert event["data"]["consent"]["claims"] == ["slack:post"]

    # The pending demand is recorded for the conversation's transition check.
    assert props, "attempt recorded the pending consent demand"


@pytest.mark.asyncio
async def test_same_tool_retry_in_one_conversation_emits_once(monkeypatch):
    source, comm, _props, _client = _install_fakes(monkeypatch, _unmet("slack:post"))

    for _ in range(3):
        await ca.resolve_connected_account_claim(
            source,
            provider_id="slack",
            connector_app_id="demo",
            claim="slack:post",
            tool_name="slack.post_slack_message",
        )
    assert len(comm.events) == 1

    # A DIFFERENT tool's demand is its own ask.
    source2, comm2, _p, _c = source, comm, _props, _client
    await ca.resolve_connected_account_claim(
        source2,
        provider_id="slack",
        connector_app_id="demo",
        claim="slack:files:write",
        tool_name="slack.upload_slack_file",
    )
    assert len(comm2.events) == 2


@pytest.mark.asyncio
async def test_config_errors_stay_tool_errors_without_a_banner(monkeypatch):
    resolution = ClaimResolution(
        ok=False,
        provider_id="slack",
        claim="slack:bogus",
        connector_app_id="demo",
        error="claim_not_configured",
        message="Claim slack:bogus is not configured for provider slack.",
        retry_hint=False,
    )
    source, comm, props, _client = _install_fakes(monkeypatch, resolution)
    credential = await ca.resolve_connected_account_claim(
        source,
        provider_id="slack",
        connector_app_id="demo",
        claim="slack:bogus",
        tool_name="slack.post_slack_message",
    )
    assert credential.ok is False
    assert comm.events == []
    assert not props

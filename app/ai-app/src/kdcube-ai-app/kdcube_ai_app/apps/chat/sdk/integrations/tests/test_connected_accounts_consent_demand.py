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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import models
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
async def test_agent_binding_miss_routes_to_agent_card_not_connect(monkeypatch):
    # Regression: this path (a per-account claim the account CAN do but THIS
    # agent is not bound for) once crashed (`_announce_agent_grant_demand()`
    # missing 'source') and once mis-routed to the connect-account banner. It
    # must raise ONE agent-grant banner (agent_client_id + the Delegated by
    # KDCube deep link, focused on the exact account+claim) and never crash.
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
        clear_agent_account_scope,
        set_agent_account_scope,
        set_agent_identity,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
        public_base,
    )

    resolution = ClaimResolution(
        ok=False,
        provider_id="google",
        claim="gmail:send",
        connector_app_id="gmail",
        account_id="acct-send",
        error="agent_grant_required",
        message="Grant this agent gmail:send on account acct-send.",
        retry_hint=True,
    )
    source, comm, props, _client = _install_fakes(monkeypatch, resolution)
    monkeypatch.setattr(public_base, "connection_hub_public_base_url", lambda: "https://hub.test")
    set_agent_account_scope({"google": {"acct-send": ["gmail:read"]}})
    set_agent_identity(
        client_id="kdcube-agent:workspace@1-0:main",
        resource="*/kdcube-services@1-0/public/mcp/named_services*",
    )
    try:
        credential = await ca.resolve_connected_account_claim(
            source,
            provider_id="google",
            connector_app_id="gmail",
            claim="gmail:send",
            tool_name="mail.send",
        )
    finally:
        clear_agent_account_scope()

    assert credential.ok is False
    # ONE banner, an AGENT-grant demand pointing at the agent's own card.
    assert len(comm.events) == 1
    consent = comm.events[0]["data"]["consent"]
    assert consent["agent_client_id"] == "kdcube-agent:workspace@1-0:main"
    assert "tab=delegated_by_kdcube" in consent["url"]
    assert "account_claim=gmail%3Asend" in consent["url"]
    # The agent-facing result is explainable and NOT re-routed as a connect demand.
    envelope = credential.error_envelope(where="mail.send")
    assert envelope["error"]["code"] == "agent_account_binding_required"
    # Retrying after the user ticks the claim is the whole point of this demand.
    # The block is rebuilt from a synthetic denial, so nothing else carries the
    # broker's verdict into it - assert the field an MCP client actually reads,
    # at the end of the translation the named-service door performs.
    assert credential.error_payload["consent"]["retry_hint"] is True

    from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
        tool_error_response,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.provider import (
        NamedServiceRequest,
    )

    response = tool_error_response(
        credential.error_envelope(where="mail.send"),
        request=NamedServiceRequest(operation="object.action", namespace="mail"),
        namespace="mail",
        provider_identity={"provider_id": "mail", "namespace": "mail"},
        default_code="mail_send_failed",
        fallback_message="Mail could not be sent.",
    )
    assert response.error.details["retry_hint"] is True


@pytest.mark.asyncio
async def test_full_forward_two_gate_consent_sequence(monkeypatch):
    """End-to-end SIMULATION of forwarding from a read-only-bound account, driven
    through the REAL broker + resolver, mutating state between phases exactly as
    the user does. Asserts the whole sequence: ONE banner per phase, the right
    routing (provider connect -> agent card), and success once both gates pass.

    Phase 1  provider gate  — the connected account has not approved gmail:send
             -> connect-account banner (Delegated to KDCube), NOT an agent demand.
    Phase 2  agent gate     — provider approved, but THIS agent is bound read-only
             -> agent-card banner (Delegated by KDCube), focused on the account+claim.
    Phase 3  both pass      — the credential resolves, no banner.
    """
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.solutions import connections
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.agent_account_scope import (
        clear_agent_account_scope,
        set_agent_account_scope,
        set_agent_identity,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
        ConnectedAccount,
        DelegatedToKdcubeBroker,
        DelegatedToKdcubeStore,
        public_base,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
        credential_id_for,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.tests.test_delegated_to_kdcube import (
        _install_fake_storage,
        _sample_config,
    )

    _install_fake_storage(monkeypatch)
    config = _sample_config()
    store = DelegatedToKdcubeStore(user_id="u1")
    cred = credential_id_for("cached-buffer")

    async def _seed(*claims: str) -> None:
        await store.upsert_account(ConnectedAccount(
            account_id="cached-buffer", provider_id="google", connector_app_id="gmail",
            external_subject="cb", email="cached.buffer@gmail.com",
            claims=tuple(claims), credential_id=cred,
        ))
        await store.set_credential(cred, {"access_token": "tok"})

    await _seed("gmail:read")  # connected with READ only at the provider
    client = connections.DelegatedToKdcubeClient(broker=DelegatedToKdcubeBroker(config=config, store=store))

    monkeypatch.setattr(ca, "get_current_user_identity", lambda: {
        "tenant_id": "acme", "project_id": "demo", "user_id": "u1",
        "bundle_id": "workspace@1-0", "conversation_id": "conv-1",
    })

    async def _from_hub(entrypoint, **kwargs):
        return client

    monkeypatch.setattr(
        ca.DelegatedToKdcubeClient, "from_connection_hub",
        classmethod(lambda cls, entrypoint, **kwargs: _from_hub(entrypoint, **kwargs)),
    )
    monkeypatch.setattr(public_base, "connection_hub_public_base_url", lambda: "https://hub.test")

    comm = _FakeComm()
    source = {
        "_SERVICE": SimpleNamespace(comm_context=None),
        "_TOOL_SUBSYSTEM": SimpleNamespace(registry={}, comm=None),
        "_COMMUNICATOR": comm,
    }
    set_agent_account_scope({"google": {"cached-buffer": ["gmail:read"]}})  # bound READ only
    set_agent_identity(
        client_id="kdcube-agent:workspace@1-0:main",
        resource="*/kdcube-services@1-0/public/mcp/named_services*",
    )

    async def _forward_send():
        return await ca.resolve_connected_account_claim(
            source,
            provider_id="google",
            connector_app_id="gmail",
            claim="gmail:send",
            tool_name="mail.forward",
            account_id="cached-buffer",  # a forward targets the ref's account
        )

    try:
        # PHASE 1 — provider gate.
        c1 = await _forward_send()
        assert c1.ok is False
        assert len(comm.events) == 1, f"phase 1 must raise exactly ONE banner, got {len(comm.events)}"
        e1 = comm.events[0]["data"]
        assert e1["error"]["code"] == "needs_connected_account_consent"
        assert e1["consent"]["provider_id"] == "google"
        assert "tab=delegated_to_kdcube" in e1["consent"]["url"]
        assert not e1["consent"].get("agent_client_id")  # a CONNECT demand, not an agent demand
        # ...but the connect deep-link carries the agent context, so the connect
        # panel can hand off to the agent card once the provider step is done.
        assert "agent_client_id=kdcube-agent" in e1["consent"]["url"]
        assert "agent_resource=" in e1["consent"]["url"]

        # The user approves gmail:send with Google (provider gate satisfied).
        await _seed("gmail:read", "gmail:send")
        comm.events.clear()

        # PHASE 2 — agent gate.
        c2 = await _forward_send()
        assert c2.ok is False
        assert len(comm.events) == 1, f"phase 2 must raise exactly ONE banner, got {len(comm.events)}"
        e2 = comm.events[0]["data"]
        assert e2["consent"]["agent_client_id"] == "kdcube-agent:workspace@1-0:main"
        assert "tab=delegated_by_kdcube" in e2["consent"]["url"]
        assert "account_claim=gmail%3Asend" in e2["consent"]["url"]
        assert c2.error_envelope(where="mail.forward")["error"]["code"] == "agent_account_binding_required"

        # The user grants THIS agent gmail:send on cached.buffer (agent gate satisfied).
        set_agent_account_scope({"google": {"cached-buffer": ["gmail:read", "gmail:send"]}})
        comm.events.clear()

        # PHASE 3 — both gates pass.
        c3 = await _forward_send()
        assert c3.ok is True
        assert c3.access_token == "tok"
        assert comm.events == [], "no banner once both gates pass"
    finally:
        clear_agent_account_scope()


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


@pytest.mark.parametrize(
    "reason",
    [
        models.REASON_CONNECT_REQUIRED,
        models.REASON_CLAIM_UPGRADE_REQUIRED,
        models.REASON_RECONNECT_REQUIRED,
        models.REASON_ACCOUNT_REQUIRED,
        models.REASON_AGENT_GRANT_REQUIRED,
        models.REASON_AGENT_ACCOUNT_BINDING_REQUIRED,
    ],
)
def test_every_reason_reads_as_a_sentence_without_a_provider_id(reason: str):
    """The provider label carries no article; each branch supplies its own."""
    from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
        _consent_instructions,
    )

    text = _consent_instructions(
        reason=reason,
        provider_id="",
        claims=["slack:post"],
        connection_hub_url="https://hub.example/card",
    )
    assert "the provider account" not in text
    assert "the provider accounts" not in text


def test_a_missing_account_binding_does_not_read_as_a_missing_connection():
    """The account is connected and holds the claim; only the binding is absent.

    Falling through to the connect-first wording sends the user to the provider
    tab, where there is nothing to fix.
    """
    from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
        _consent_instructions,
    )

    text = _consent_instructions(
        reason=models.REASON_AGENT_ACCOUNT_BINDING_REQUIRED,
        provider_id="linkedin",
        claims=["linkedin:post"],
        connection_hub_url="https://hub.example/card",
    )
    assert "is connected and holds linkedin:post" in text
    assert "not bound to it" in text
    assert "No Linkedin account is connected" not in text

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The native named-service gate keeps three outcomes apart.

Open when the boundary does not apply, refuse without asking for consent when
the deployment no longer offers the capability, and refuse retryably when
current authority cannot be established.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import tools

AGENT_IDENTITY = {"user_id": "platform-user-1", "bundle_id": "app@1-0"}
REMOVED = {
    "ok": False,
    "error": {
        "code": "delegated_capability_no_longer_available",
        "message": "gone",
        "where": "delegated_catalog.authorization",
        "retryable": False,
    },
    "ret": {"requested_capability": {"kind": "named_service_operation", "namespace": "mail"}},
}


def _bind_agent_turn(monkeypatch, *, agent: bool = True):
    monkeypatch.setattr(
        comm_ctx, "get_current_user_identity", lambda: dict(AGENT_IDENTITY) if agent else {}
    )
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: SimpleNamespace(event=SimpleNamespace(agent_id="lg-react" if agent else "")),
    )


def _bind_hub(monkeypatch, answer):
    async def _call(**_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(value={"ok": True, "ret": {"object": answer}})

    monkeypatch.setattr(bundle_operations, "call_bundle_named_service", _call)


def _forbid_consent(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.solutions.connections import mcp_consent

    async def _announce(_consent):
        raise AssertionError("a removed capability must not ask for consent")

    monkeypatch.setattr(mcp_consent, "announce_agent_consent", _announce)


@pytest.mark.asyncio
async def test_a_non_agent_turn_is_not_gated(monkeypatch):
    _bind_agent_turn(monkeypatch, agent=False)

    assert await tools._agent_grant_gate("mail", "object.search", "search") is None


@pytest.mark.asyncio
async def test_a_granted_capability_opens_the_gate(monkeypatch):
    _bind_agent_turn(monkeypatch)
    _bind_hub(monkeypatch, {"governed": True, "granted": True, "resource": "r", "claims": []})

    assert await tools._agent_grant_gate("mail", "object.search", "search") is None


@pytest.mark.asyncio
async def test_an_ungoverned_namespace_opens_the_gate(monkeypatch):
    _bind_agent_turn(monkeypatch)
    _bind_hub(monkeypatch, {"governed": False})

    assert await tools._agent_grant_gate("calendar", "object.search", "search") is None


@pytest.mark.asyncio
async def test_a_removed_capability_is_refused_without_asking_for_consent(monkeypatch):
    _bind_agent_turn(monkeypatch)
    _forbid_consent(monkeypatch)
    _bind_hub(
        monkeypatch, {"governed": True, "granted": False, "removed": REMOVED}
    )

    result = await tools._agent_grant_gate("mail", "object.search", "search")

    assert result is not None
    assert result["error"]["code"] == "delegated_capability_no_longer_available"
    assert result["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_an_unavailable_catalog_refuses_retryably(monkeypatch):
    _bind_agent_turn(monkeypatch)
    _bind_hub(monkeypatch, {"unavailable": "active_catalog_not_registered"})

    result = await tools._agent_grant_gate("mail", "object.search", "search")

    assert result is not None
    assert result["error"]["code"] == "temporarily_unavailable"
    assert result["error"]["retryable"] is True
    assert result["ret"]["reason"] == "active_catalog_not_registered"
    assert result["ret"]["namespace"] == "mail"


@pytest.mark.asyncio
async def test_an_unreachable_hub_refuses_instead_of_failing_open(monkeypatch):
    """The boundary applies and cannot be read: the call must not proceed on
    the connected-account boundary alone."""
    _bind_agent_turn(monkeypatch)
    _bind_hub(monkeypatch, RuntimeError("hub unreachable"))

    result = await tools._agent_grant_gate("mail", "object.search", "search")

    assert result is not None
    assert result["error"]["code"] == "temporarily_unavailable"
    assert result["ret"]["reason"] == "agent_grant_check_unavailable"

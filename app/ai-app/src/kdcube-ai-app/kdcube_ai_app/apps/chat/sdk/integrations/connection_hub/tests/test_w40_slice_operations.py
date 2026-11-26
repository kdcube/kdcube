"""W40: the conversation-slice operations reach the Hub only for the calling app and its signed-in user."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.hub.provider_impl import ConnectionHubProvider
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceContext, NamedServiceRequest,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.tests.test_connection_hub_boundary import (
    SOURCE_BUNDLE_ID_METADATA,
)

APP = "problem-board@1-0"


class _Service:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def call(user, **kwargs):
            self.calls.append((name, dict(user), kwargs))
            return {"ok": True, "turn_token": "ts1.x.y"} if name == "begin_agent_turn" else {"ok": True}
        return call


def _provider():
    service = _Service()
    return ConnectionHubProvider(entrypoint=object(), automation_access_factory=lambda: service), service


def _context(user="user-1", source=APP):
    return NamedServiceContext(user_id=user, user_type="registered", metadata={SOURCE_BUNDLE_ID_METADATA: source})


def _request(operation, **payload):
    return NamedServiceRequest(operation=operation, namespace="connections", payload=payload)


@pytest.mark.asyncio
async def test_the_turn_operation_reaches_the_hub_for_the_calling_app_and_user():
    provider, service = _provider()
    response = await provider.dispatch(_context(), _request("agent_capability.turn", application=APP, agent_id="main",
                                                            conversation_id="c1", turn_id="t1"))
    assert response.ok, response
    assert service.calls == [("begin_agent_turn", {"user_id": "user-1"}, {
        "application": APP, "agent_id": "main", "conversation_id": "c1", "turn_id": "t1", "turn_token": "", "mode": "renew",
        "conversation_epoch": None})]


@pytest.mark.asyncio
@pytest.mark.parametrize("context,code", [
    (_context(source="another-app@1-0"), "agent_capability_source_mismatch"),
    (_context(user="application-authority:abc"), "delegated_access_requires_authenticated_user"),
])
async def test_another_app_or_an_application_holder_is_refused(context, code):
    provider, service = _provider()
    response = await provider.dispatch(context, _request("agent_capability.conversation", application=APP,
                                                         agent_id="main", conversation_id="c1", selection={}))
    assert response.ok is False and response.error.code == code
    assert service.calls == []


@pytest.mark.asyncio
async def test_the_picker_and_the_turn_pass_the_conversations_deletion_epoch():
    provider, service = _provider()
    await provider.dispatch(_context(), _request("agent_capability.conversation", application=APP, agent_id="main",
                                                 conversation_id="c1", selection={"resource_families": []},
                                                 expected_slice_revision=3, conversation_epoch=5000))
    assert [name for name, _, _ in service.calls] == ["update_conversation_selection"]
    assert service.calls[0][2]["expected_slice_revision"] == 3 and service.calls[0][2]["conversation_epoch"] == 5000
    await provider.dispatch(_context(), _request("agent_capability.conversation", application=APP, agent_id="main",
                                                 conversation_id="c1", selection={"resource_families": []}))
    assert service.calls[1][2]["conversation_epoch"] is None
    unknown = await provider.dispatch(_context(), _request("agent_capability.conversation_delete",
                                                           conversation_id="c1", phase="mark"))
    assert unknown.ok is False

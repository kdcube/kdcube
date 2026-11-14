"""W38: the dispatch gate reads an agent's action list by the one ceiling rule.

| allowed for a namespace         | action call                         |
| object.action (± a list)        | every action                        |
| a list of object.action.<name>  | exactly the listed actions          |
"""
import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    bind_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceProviderSpec,
    NamedServiceResponse,
    RedisNamedServiceDiscovery,
    bind_named_service_discovery,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tests.test_named_services_providers import (
    FakeDiscoveryRedis,
    _application_admission,
    named_service_client_tools,
)


@pytest.fixture(autouse=True)
def _isolate_native_admission(monkeypatch):
    async def _admission(*_args, **_kwargs):
        return _application_admission(), None

    monkeypatch.setattr(named_service_client_tools, "_agent_grant_admission", _admission)


async def _call(allowed: list[str]):
    redis = FakeDiscoveryRedis()
    discovery = RedisNamedServiceDiscovery(redis, tenant="tenant-a", project="project-a")
    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id="kdcube.mail", bundle_id="kdcube-services@1-0", namespace="mail",
            refs=("mail:*",), object_kinds=("mail.message",),
            operations={"object.list": {"transports": ["local"]}, "object.action": {"transports": ["local"]}},
        ),
        bundle_id="kdcube-services@1-0",
    )
    props = {"named_services": {"namespaces": {"mail": {"clients": {"main": {"tools": {"allowed_operations": allowed}}}}}}}
    calls = []

    async def caller(call):
        calls.append(call)
        return BundleNamedServiceResult(NamedServiceResponse.ok_response(
            provider={"provider_id": call.request.provider}, namespace=call.request.namespace,
            extra={"operation": call.request.operation, "action": call.request.action}))

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "main"})
    with bind_named_service_discovery(discovery), bind_bundle_named_service_caller(caller):
        listed = await named_service_client_tools.object_action(
            namespace="mail", object_ref="mail:gmail:acct-1:message:m-1", action="send")
        unlisted = await named_service_client_tools.object_action(
            namespace="mail", object_ref="mail:gmail:acct-1:message:m-1", action="download_attachments")
    return listed, unlisted, [(c.request.operation, c.request.action) for c in calls]


@pytest.mark.asyncio
async def test_a_list_without_the_parent_admits_exactly_the_listed_actions():
    listed, unlisted, calls = await _call(["object.list", "object.action.send"])
    assert listed["ok"] is True, listed
    assert unlisted["ok"] is False
    assert unlisted["error"] == "named_service_action_not_allowed_for_client"
    assert unlisted["fix"]["actor"] == "admin", "the descriptor decides it, not the user's toggle"
    assert calls == [("object.action", "send")]


@pytest.mark.asyncio
async def test_the_parent_admits_every_action_with_or_without_a_list():
    for allowed in (["object.list", "object.action"], ["object.list", "object.action", "object.action.send"]):
        listed, unlisted, calls = await _call(allowed)
        assert listed["ok"] is True and unlisted["ok"] is True, (allowed, listed, unlisted)
        assert calls == [("object.action", "send"), ("object.action", "download_attachments")]

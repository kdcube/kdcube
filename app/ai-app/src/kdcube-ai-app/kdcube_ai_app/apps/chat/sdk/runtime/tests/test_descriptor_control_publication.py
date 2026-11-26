# SPDX-License-Identifier: MIT

"""The application's deployment publishes its agents' shared descriptor Control.

A user's sync never writes the Control. When the Hub answers that this
descriptor revision is not published yet, the runtime publishes it itself,
but only a descriptor equal to the props authority's at that moment (so an
instance holding older props never overwrites a newer publication), seeded
with the descriptor's own default and never with this user's picks; then it
syncs once more.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    bind_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import (
    AgentCapabilityControlUnavailable,
    publish_agent_capability_control,
    selected_capabilities_from_disabled,
    sync_agent_capability_projection,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tests.test_agent_capability_control import (
    AGENT, APPLICATION, PROJECT, TENANT, _catalog, _override_props, _props,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import NamedServiceResponse

SYNC, PUBLISH = "agent_capability.sync", "agent_capability.publish"


def _owner(props=None):
    return SimpleNamespace(bundle_props=props or _props(), bundle_props_defaults={},
                           _agent_selection_identity=lambda: {"tenant": TENANT, "project": PROJECT,
                                                              "user_id": "user-a", "bundle_id": APPLICATION})


def _hub(*answers):
    """A Hub that answers each call in turn and records it."""
    calls, queue = [], list(answers)

    async def call(request):
        calls.append(request.request)
        answer = queue.pop(0)
        if answer == "ok":
            payload = request.request["payload"]
            selection = payload.get("selected_capabilities") or {}
            value = NamedServiceResponse.ok_response(object={"ok": True, "selection": selection,
                                                             "projection": selection, "states": {}})
        else:
            value = NamedServiceResponse.error_response(code=answer, message=answer, status=409,
                                                        details={"error": answer, "retryable": True, "status": 409})
        return BundleNamedServiceResult(value=value)

    return calls, call


@pytest.fixture
def authority(monkeypatch):
    holder = {"props": _props()}

    async def read(**_kwargs):
        return copy.deepcopy(holder["props"])

    monkeypatch.setattr("kdcube_ai_app.infra.plugin.bundle_store.get_bundle_props_from_authority", read)
    return holder


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["agent_descriptor_control_not_published", "agent_descriptor_control_not_current"])
async def test_an_unpublished_descriptor_is_published_then_synced_once(authority, refusal) -> None:
    calls, call = _hub(refusal, "ok", "ok")
    with bind_bundle_named_service_caller(call):
        result = await sync_agent_capability_projection(
            _owner(), catalog=_catalog(), agent_id=AGENT, initial_disabled={"skills": ["work.review"]})

    assert [c["operation"] for c in calls] == [SYNC, PUBLISH, SYNC]
    assert result["ok"] is True
    published = calls[1]["payload"]
    # The Control is seeded with the descriptor's own default, not with this user's picks.
    assert published["selected_capabilities"] == selected_capabilities_from_disabled(
        authority=published["capability_authority"], catalog=_catalog(), disabled={})
    assert not any(key.startswith("selected_resource") or key.startswith("selected_named") or key == "replace_selection"
                   for key in published)
    assert published["descriptor_revision"] == calls[0]["payload"]["descriptor_revision"]


@pytest.mark.asyncio
async def test_a_runtime_with_props_older_than_the_authority_never_publishes(authority) -> None:
    # The authority already holds a newer descriptor for the agent (an
    # administrator override); this runtime still runs the older props.
    authority["props"] = _override_props()
    calls, call = _hub("agent_descriptor_control_not_current")
    with bind_bundle_named_service_caller(call):
        with pytest.raises(AgentCapabilityControlUnavailable) as caught:
            await sync_agent_capability_projection(_owner(), catalog=_catalog(), agent_id=AGENT)

    assert caught.value.code == "agent_descriptor_control_not_current"
    assert [c["operation"] for c in calls] == [SYNC]


@pytest.mark.asyncio
async def test_a_refused_publication_leaves_the_sync_refusal(authority) -> None:
    calls, call = _hub("agent_descriptor_control_not_published", "agent_descriptor_publication_requires_deployment")
    with bind_bundle_named_service_caller(call):
        with pytest.raises(AgentCapabilityControlUnavailable) as caught:
            await sync_agent_capability_projection(_owner(), catalog=_catalog(), agent_id=AGENT)
    assert [c["operation"] for c in calls] == [SYNC, PUBLISH]
    assert caught.value.code == "agent_descriptor_control_not_published"


@pytest.mark.asyncio
async def test_other_sync_refusals_never_publish(authority) -> None:
    calls, call = _hub("agent_capability_selection_outside_control")
    with bind_bundle_named_service_caller(call):
        with pytest.raises(AgentCapabilityControlUnavailable):
            await sync_agent_capability_projection(_owner(), catalog=_catalog(), agent_id=AGENT)
    assert [c["operation"] for c in calls] == [SYNC]


@pytest.mark.asyncio
async def test_the_deployment_publication_uses_the_props_it_was_given() -> None:
    calls, call = _hub("ok")
    with bind_bundle_named_service_caller(call):
        answer = await publish_agent_capability_control(_owner(props={}), catalog=_catalog(), agent_id=AGENT,
                                                        bundle_props=_props())
    assert answer["ok"] is True
    assert [c["operation"] for c in calls] == [PUBLISH]
    assert calls[0]["payload"]["application"] == APPLICATION and calls[0]["payload"]["agent_id"] == AGENT


@pytest.mark.asyncio
async def test_an_authority_that_moves_during_the_publication_stops_the_retry(authority) -> None:
    calls, call = _hub("agent_descriptor_control_not_published", "ok")
    reads = {"n": 0}
    original = authority["props"]

    async def moving(**_kwargs):
        reads["n"] += 1
        return copy.deepcopy(original if reads["n"] == 1 else _override_props())

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
    bundle_store.get_bundle_props_from_authority, saved = moving, bundle_store.get_bundle_props_from_authority
    try:
        with bind_bundle_named_service_caller(call):
            with pytest.raises(AgentCapabilityControlUnavailable) as caught:
                await sync_agent_capability_projection(_owner(), catalog=_catalog(), agent_id=AGENT)
    finally:
        bundle_store.get_bundle_props_from_authority = saved
    assert [c["operation"] for c in calls] == [SYNC, PUBLISH]
    assert caught.value.code == "agent_descriptor_control_not_published"

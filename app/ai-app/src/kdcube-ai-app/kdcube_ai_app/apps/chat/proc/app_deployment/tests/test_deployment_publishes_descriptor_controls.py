# SPDX-License-Identifier: MIT

"""An application's deployment publishes its agents' shared descriptor Controls.

The publication runs in the application's own identity-free context (as a
headless bundle job's), so the Connection Hub host sees the calling app as the
platform-bound source. It uses the props the deployment re-read under its
lock. An unreachable or busy Hub is retried in the background; a final
refusal fails the resource phase, and its text names each refused agent. Connection Hub can ask for one agent's publication now, for an
application that is ready in this process.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.proc.app_deployment import descriptor_controls
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import get_current_bundle_named_service_caller
from kdcube_ai_app.apps.chat.sdk.runtime import agent_capability_control, agent_inventory
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

PROPS = {"surfaces": {"as_consumer": {"agents": {"main": {}, "helper": {}, "broken": "not-a-block"}}}}


def test_declared_agents_are_the_consumer_agent_blocks() -> None:
    assert descriptor_controls.declared_agents(PROPS) == ["helper", "main"]
    assert descriptor_controls.declared_agents({}) == []
    assert descriptor_controls.declared_agents({"surfaces": {"as_consumer": {"agents": []}}}) == []


@pytest.mark.asyncio
async def test_each_agent_is_published_as_the_application_with_the_reread_props(monkeypatch) -> None:
    seen = []

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        context = get_current_request_context()
        seen.append({
            "agent": agent_id,
            "props": bundle_props,
            "identity": identity,
            "source_bundle": context.routing.bundle_id,
            "user_type": context.user.user_type,
            "caller_bound": get_current_bundle_named_service_caller() is not None,
            "catalog": catalog,
        })
        return {"ok": True, "control_changed": agent_id == "main"}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog",
                        lambda props, agent_id, bundle_root=None: {"agent": agent_id})

    outcomes = await descriptor_controls.publish_agent_descriptor_controls(
        workflow=SimpleNamespace(_bundle_root=lambda: "/bundle"), tenant="tenant-a", project="project-a",
        bundle_id="app@1-0", props=PROPS, redis=None, pg_pool=None)

    assert outcomes == {"helper": "unchanged", "main": "published"}
    for row in seen:
        assert row["props"] is PROPS
        assert row["identity"] == {"tenant": "tenant-a", "project": "project-a", "bundle_id": "app@1-0"}
        # The host binds the calling app; no user is involved.
        assert row["source_bundle"] == "app@1-0" and row["user_type"] == "anonymous"
        assert row["caller_bound"] and row["catalog"] == {"agent": row["agent"]}
    assert get_current_bundle_named_service_caller() is None


@pytest.mark.asyncio
async def test_a_refusal_fails_the_deployment_and_an_unreachable_hub_is_retried(monkeypatch) -> None:
    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        if agent_id == "main":
            raise RuntimeError("hub unavailable")
        return {"ok": False, "error": "issuer_managed_card", "reason": "issuer_managed_card",
                "retryable": False, "status": 409}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    with pytest.raises(descriptor_controls.DescriptorControlRefused) as refused:
        await descriptor_controls.publish_agent_descriptor_controls(
            workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0", props=PROPS,
            redis=None, pg_pool=None)
    assert refused.value.outcomes == {"helper": "refused:issuer_managed_card"}


@pytest.mark.asyncio
async def test_the_refusal_text_names_the_application_each_agent_and_the_hub_codes(monkeypatch) -> None:
    from kdcube_ai_app.infra.plugin.app_readiness import (
        ApplicationLifecycleState,
        ApplicationReadinessRegistry,
        DesiredApplicationState,
    )

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        return {"ok": False, "error": "agent_descriptor_invalid", "reason": "unknown_operation",
                "retryable": False, "status": 422}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    with pytest.raises(descriptor_controls.DescriptorControlRefused) as refused:
        await descriptor_controls.publish_agent_descriptor_controls(
            workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0",
            props={"surfaces": {"as_consumer": {"agents": {"main": {}}}}}, redis=None, pg_pool=None)
    text = str(refused.value)
    assert text == ("descriptor_control_refused: application=app@1-0 Connection Hub refused the descriptor "
                    "Control of agent=main error=agent_descriptor_invalid reason=unknown_operation status=422. "
                    "Fix the agent's descriptor, then reload the application.")

    registry = ApplicationReadinessRegistry()
    registry.replace_desired(tenant="t", project="p",
                             applications={"app@1-0": DesiredApplicationState(generation="g1")})
    registry.transition(tenant="t", project="p", application_id="app@1-0", generation="g1",
                        state=ApplicationLifecycleState.RETRYING, attempt=1, error=refused.value)
    snapshot = registry.snapshot(tenant="t", project="p", application_id="app@1-0")
    assert snapshot.error_code == "DescriptorControlRefused" and snapshot.error_message == text


@pytest.mark.asyncio
async def test_a_busy_hub_answer_is_retried_and_never_fails_the_deployment(monkeypatch) -> None:
    import asyncio

    answers = [{"ok": False, "error": "card_lock_busy", "retryable": True, "status": 503},
               {"ok": True, "control_changed": True}]

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        return answers.pop(0)

    props = {"surfaces": {"as_consumer": {"agents": {"main": {}}}}}

    async def reread():
        return props

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    monkeypatch.setattr(descriptor_controls, "RETRY_DELAYS_SECONDS", (0.0,))
    outcomes = await descriptor_controls.publish_agent_descriptor_controls(
        workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0", props=props,
        redis=None, pg_pool=None, reread=reread)
    assert outcomes == {"main": "failed:card_lock_busy"}
    for _ in range(50):
        if not answers:
            break
        await asyncio.sleep(0.01)
    assert answers == []


@pytest.mark.asyncio
async def test_an_unreachable_hub_retries_in_the_background_from_the_reread_props(monkeypatch) -> None:
    import asyncio

    calls = {"n": 0}

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("hub not loaded yet")
        return {"ok": True, "control_changed": True}

    rereads = []

    async def reread():
        rereads.append(1)
        return {"surfaces": {"as_consumer": {"agents": {"main": {}}}}}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    monkeypatch.setattr(descriptor_controls, "RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    outcomes = await descriptor_controls.publish_agent_descriptor_controls(
        workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0",
        props={"surfaces": {"as_consumer": {"agents": {"main": {}}}}}, redis=None, pg_pool=None, reread=reread)
    assert outcomes == {"main": "failed:RuntimeError"}, "the deployment goes on"
    for _ in range(50):
        if calls["n"] >= 3:
            break
        await asyncio.sleep(0.01)
    assert calls["n"] == 3 and len(rereads) == 2


@pytest.mark.asyncio
async def test_an_application_without_agents_publishes_nothing(monkeypatch) -> None:
    async def publish(*a, **k):
        raise AssertionError("no agent, no publication")

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    assert await descriptor_controls.publish_agent_descriptor_controls(
        workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0", props={},
        redis=None, pg_pool=None) == {}


@pytest.mark.asyncio
async def test_the_app_deployment_publishes_from_the_props_reread_under_its_lock(monkeypatch, tmp_path) -> None:
    from kdcube_ai_app.apps.chat.proc.app_deployment import coordinator
    from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import deploy_loaded_bundle_app_resources
    from kdcube_ai_app.apps.chat.proc.app_deployment.tests.test_coordinator import _Workflow
    from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec

    source_root, storage_root = tmp_path / "source", tmp_path / "storage"
    source_root.mkdir()
    reads = {"count": 0}

    async def authority(**kwargs):
        reads["count"] += 1
        # The second read (under the lock) is newer than the first.
        return {"surfaces": {"as_consumer": {"agents": {"main": {"revision": reads["count"]}}}}}

    async def storage(**kwargs):
        return storage_root

    published = []

    async def publish(**kwargs):
        published.append(kwargs)
        return {"main": "published"}

    monkeypatch.setattr(coordinator, "static_widget_deployment_enabled", lambda: False)
    monkeypatch.setattr(coordinator, "get_bundle_props_from_authority", authority)
    monkeypatch.setattr(coordinator, "resolve_app_storage_root", storage)
    monkeypatch.setattr(coordinator, "get_settings", lambda: SimpleNamespace(PLATFORM=SimpleNamespace(
        APPLICATIONS=SimpleNamespace(BUNDLES_PRELOAD_LOCK_TTL_SECONDS=30, BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS=30))))
    monkeypatch.setattr(descriptor_controls, "publish_agent_descriptor_controls", publish)

    spec = SimpleNamespace(id="app@1-0", path=str(source_root), module="entrypoint", singleton=True, repo=None,
                           ref=None, subdir=None, git_commit="commit-1")
    await deploy_loaded_bundle_app_resources(
        workflow=_Workflow(storage_root), module=SimpleNamespace(),
        agentic_spec=BundleSpec(id="app@1-0", path=str(source_root), module="entrypoint", singleton=True),
        bundle_spec=spec, tenant="tenant-a", project="project-a")

    assert len(published) == 1
    call = published[0]
    assert (call["tenant"], call["project"], call["bundle_id"]) == ("tenant-a", "project-a", "app@1-0")
    assert call["props"]["surfaces"]["as_consumer"]["agents"]["main"]["revision"] == reads["count"] > 1


class _Workflow(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_an_on_demand_publication_is_the_applications_even_inside_an_administrators_request(
        monkeypatch, tmp_path) -> None:
    from kdcube_ai_app.apps.chat.sdk.protocol import (
        ExternalEventActor, ExternalEventPayload, ExternalEventRouting, ExternalEventUser,
    )
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context

    seen = []

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        context = get_current_request_context()
        seen.append((agent_id, context.routing.bundle_id, context.user.user_type))
        return {"ok": True, "control_changed": False}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    admin = ExternalEventPayload(actor=ExternalEventActor(tenant_id="t", project_id="p"),
                                 routing=ExternalEventRouting(bundle_id="connection-hub@1-0", session_id="s"),
                                 user=ExternalEventUser(user_type="privileged", user_id="admin-1"))
    with bind_current_request_context(admin, comm=None):
        outcome = await descriptor_controls.publish_agent_descriptor_control_now(
            workflow=_Workflow(), tenant="t", project="p", bundle_id="app@1-0", agent_id="main", props=PROPS,
            redis=None, pg_pool=None, storage_root=tmp_path)
    assert outcome == "unchanged"
    assert seen == [("main", "app@1-0", "anonymous")], "published as the application, never as the administrator"


@pytest.mark.asyncio
async def test_an_on_demand_publication_refuses_an_agent_the_application_no_longer_declares(tmp_path) -> None:
    outcome = await descriptor_controls.publish_agent_descriptor_control_now(
        workflow=_Workflow(), tenant="t", project="p", bundle_id="app@1-0", agent_id="gone", props=PROPS,
        redis=None, pg_pool=None, storage_root=tmp_path)
    assert outcome == "refused:agent_not_declared"


@pytest.mark.asyncio
async def test_an_on_demand_publication_is_busy_while_a_deployment_publishes(monkeypatch, tmp_path) -> None:
    async def publish(*_a, **_k):
        raise AssertionError("must not publish while the deployment holds the lock")

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    async with descriptor_controls.descriptor_control_publish_lock(tmp_path, bundle_id="app@1-0", wait_seconds=1):
        outcome = await descriptor_controls.publish_agent_descriptor_control_now(
            workflow=_Workflow(), tenant="t", project="p", bundle_id="app@1-0", agent_id="main", props=PROPS,
            redis=None, pg_pool=None, storage_root=tmp_path, wait_seconds=0.2)
    assert outcome == "busy"


@pytest.mark.asyncio
async def test_the_publisher_never_loads_an_application_that_is_not_ready(monkeypatch) -> None:
    from kdcube_ai_app.infra.plugin import app_readiness, bundle_loader

    async def load(*_a, **_k):
        raise AssertionError("an editor request must not load an application")

    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", load)
    monkeypatch.setattr(app_readiness.application_readiness_registry, "snapshot", lambda **_k: None)
    publisher = descriptor_controls.make_descriptor_control_publisher(tenant="t", project="p", redis=None,
                                                                      pg_pool=None)
    assert await publisher("app@1-0", "main") == "unavailable"


@pytest.mark.asyncio
async def test_the_publisher_reads_the_props_the_deployment_reads(monkeypatch, tmp_path) -> None:
    """Code defaults merged through the loaded instance: an agent declared only there is published too."""
    from kdcube_ai_app.apps.chat.proc.app_deployment import coordinator
    from kdcube_ai_app.infra.plugin import app_readiness, bundle_loader, bundle_store
    from kdcube_ai_app.infra.service_hub import inventory

    workflow = _Workflow(bundle_props_defaults={"surfaces": {"as_consumer": {"agents": {"coded": {}}}}},
                         bundle_storage_root=lambda: tmp_path)

    async def load(*_a, **_k):
        return workflow, None

    async def authority(**_k):
        return {"surfaces": {"as_consumer": {"agents": {"main": {}}}}}

    async def spec(*_a, **_k):
        return SimpleNamespace(id="app@1-0", path="/bundles/app", module="entrypoint", singleton=False)

    async def secrets(request, **_k):
        return request

    published = []

    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        published.append((agent_id, sorted(bundle_props["surfaces"]["as_consumer"]["agents"])))
        return {"ok": True, "control_changed": True}

    monkeypatch.setattr(app_readiness.application_readiness_registry, "snapshot",
                        lambda **_k: SimpleNamespace(ready=True))
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", load)
    monkeypatch.setattr(bundle_store, "resolve_bundle_spec_from_store", spec)
    monkeypatch.setattr(inventory, "resolve_config_request_secrets", secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda request: SimpleNamespace())
    monkeypatch.setattr(coordinator, "get_bundle_props_from_authority", authority)
    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})
    publisher = descriptor_controls.make_descriptor_control_publisher(tenant="t", project="p", redis=None,
                                                                      pg_pool=None)
    assert await publisher("app@1-0", "coded") == "published"
    assert published == [("coded", ["coded", "main"])]


def test_only_connection_hub_requests_get_a_publisher() -> None:
    from kdcube_ai_app.apps.chat.sdk.infra.descriptor_control_publisher import (
        get_current_descriptor_control_publisher,
    )

    assert get_current_descriptor_control_publisher() is None
    source = (pathlib.Path(descriptor_controls.__file__).parents[1] / "rest" / "integrations"
              / "integrations.py").read_text()
    assert "if spec_resolved.id == CONNECTION_HUB_BUNDLE_ID else None" in source

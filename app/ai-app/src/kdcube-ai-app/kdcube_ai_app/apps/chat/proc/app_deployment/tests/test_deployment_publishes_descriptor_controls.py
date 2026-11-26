# SPDX-License-Identifier: MIT

"""An application's deployment publishes its agents' shared descriptor Controls.

The publication runs in the application's own identity-free context (as a
headless bundle job's), so the Connection Hub host sees the calling app as the
platform-bound source. It uses the props the deployment re-read under its
lock, and a refusal or failure never fails the deployment.
"""

from __future__ import annotations

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
async def test_a_refused_or_failing_publication_never_fails_the_deployment(monkeypatch) -> None:
    async def publish(workflow, *, catalog, agent_id, bundle_props, identity):
        if agent_id == "main":
            raise RuntimeError("hub unavailable")
        return {"ok": False, "error": "issuer_managed_card"}

    monkeypatch.setattr(agent_capability_control, "publish_agent_capability_control", publish)
    monkeypatch.setattr(agent_inventory, "agent_capabilities_catalog", lambda *a, **k: {})

    outcomes = await descriptor_controls.publish_agent_descriptor_controls(
        workflow=SimpleNamespace(), tenant="t", project="p", bundle_id="app@1-0", props=PROPS,
        redis=None, pg_pool=None)

    assert outcomes == {"helper": "refused:issuer_managed_card", "main": "failed:RuntimeError"}


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

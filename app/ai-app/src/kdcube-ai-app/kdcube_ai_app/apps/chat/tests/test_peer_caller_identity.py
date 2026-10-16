"""W372: the platform names the calling bundle on every peer call, and an operation can admit only some.

The caller is bound per invocation, around the target handler, from the calling bundle's own
platform-built context: the REST and MCP peer closures pass their own bundle, the local invoke
path reads the source context's routing. Nothing in the payload or the headers can set it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations as operations
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BUNDLE_OPERATION_CALLER_NOT_ALLOWED,
    BundleOperationCall,
    BundleOperationCallerNotAllowed,
    call_bundle_operation,
    current_peer_caller_bundle_id,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.infra.plugin.bundle_loader import API_METHOD_ATTR, api

from test_integrations_operations_auth import _patch_bundle_runtime, _request, _session

HUB = "connection-hub@1-0"
SEEN: list[tuple[str, str]] = []


class _Workflow:
    @api(alias="open_op")
    async def open_op(self, **kwargs):
        SEEN.append(("open_op", current_peer_caller_bundle_id()))
        return {"ok": True}

    @api(alias="hub_only", peer_callers=(HUB,))
    async def hub_only(self, **kwargs):
        SEEN.append(("hub_only", current_peer_caller_bundle_id()))
        return {"ok": True}

    @api(alias="relay")
    async def relay(self, target_bundle: str = "", spawn: bool = False, **kwargs):
        """B: records its own caller, then calls C, directly or from a task it spawned."""
        SEEN.append(("relay", current_peer_caller_bundle_id()))
        if spawn:
            await asyncio.create_task(call_bundle_operation(bundle_id=target_bundle, operation="open_op"))
        else:
            await call_bundle_operation(bundle_id=target_bundle, operation="open_op")
        return {"ok": True}


def _patch(monkeypatch):
    _patch_bundle_runtime(monkeypatch)

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        return _Workflow(), None

    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    SEEN.clear()


async def _rest(operation, *, bundle="b@1", data=None, headers_token="", peer_caller=None):
    """A direct request, or (peer_caller set) the call a peer closure makes for that bundle."""
    return await integrations._call_bundle_op_inner(
        tenant="tenant-a", project="project-a", bundle_id=bundle,
        payload=integrations.BundleSuggestionsRequest(bundle_id=bundle, data=dict(data or {})),
        request=_request(authorization=headers_token), operation=operation, route="operations",
        session=_session(["kdcube:role:member"]),
        _internal_peer_call=peer_caller is not None, _peer_caller_bundle_id=peer_caller or "",
    )


def _refused(exc_info, caller):
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": BUNDLE_OPERATION_CALLER_NOT_ALLOWED, "caller": caller}


@pytest.mark.asyncio
async def test_an_undeclared_operation_is_unchanged(monkeypatch):
    _patch(monkeypatch)
    assert (await _rest("open_op"))["open_op"] == {"ok": True}
    assert (await _rest("open_op", peer_caller="any@1"))["open_op"] == {"ok": True}
    assert SEEN == [("open_op", ""), ("open_op", "any@1")]
    assert current_peer_caller_bundle_id() == ""  # reset after each invocation


@pytest.mark.asyncio
async def test_a_declared_operation_admits_its_listed_peer_and_tells_the_handler(monkeypatch):
    _patch(monkeypatch)
    assert (await _rest("hub_only", peer_caller=HUB))["hub_only"] == {"ok": True}
    assert SEEN == [("hub_only", HUB)]


@pytest.mark.asyncio
async def test_a_peer_from_another_bundle_is_refused_by_name(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as refused:
        await _rest("hub_only", peer_caller="other@1")
    _refused(refused, "other@1")
    assert SEEN == []


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof", [
    {"data": {"_peer_caller_bundle_id": HUB, "peer_caller": HUB, "caller_bundle_id": HUB}},
    {"headers_token": f"Bearer {HUB}"},
], ids=["payload", "header"])
async def test_a_direct_request_is_refused_and_cannot_spoof_a_caller(monkeypatch, spoof):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as refused:
        await _rest("hub_only", **spoof)
    _refused(refused, "")
    assert SEEN == []


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn", [False, True], ids=["awaited", "spawned-task"])
async def test_a_nested_call_names_its_immediate_caller(monkeypatch, spawn):
    """A -> B -> C: C sees B, also when B makes the call from a task it spawned."""
    _patch(monkeypatch)
    await _rest("relay", bundle="b@1", data={"target_bundle": "c@1", "spawn": spawn}, peer_caller="a@1")
    assert SEEN == [("relay", "a@1"), ("open_op", "b@1")]
    assert current_peer_caller_bundle_id() == ""


# ---- the local invoke path (processor, job context, Data Bus worker, nested local callers) ----

def _context(bundle_id: str) -> ExternalEventPayload:
    return ExternalEventPayload(
        request=ExternalEventRequest(request_id="r-1"),
        routing=ExternalEventRouting(session_id="s-1", bundle_id=bundle_id),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="user-1", roles=["kdcube:role:member"]),
    )


def _patch_local(monkeypatch):
    from kdcube_ai_app.infra.plugin import bundle_loader, bundle_store
    from kdcube_ai_app.infra.service_hub import inventory

    async def resolve_spec(redis, *, tenant, project, bundle_id):
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    async def props(redis, *, tenant, project, bundle_id):
        return {}

    async def secrets(cfg_req, *, bundle_id):
        return cfg_req

    async def workflow(spec, wf_config, *, comm_context, redis, pg_pool):
        return _Workflow(), None

    async def session(redis, comm_context, *, tenant, project):
        return _session(["kdcube:role:member"])

    monkeypatch.setattr(bundle_store, "resolve_bundle_spec_from_store", resolve_spec)
    monkeypatch.setattr(bundle_store, "get_bundle_props", props)
    monkeypatch.setattr(inventory, "resolve_config_request_secrets", secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda cfg: SimpleNamespace(ai_bundle_spec=None))
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", workflow)
    monkeypatch.setattr(operations, "_session_for_comm_context", session)
    SEEN.clear()


async def _local(operation, *, source_bundle, target_bundle="b@1", data=None):
    return await operations._invoke_local_bundle_operation_raw(
        BundleOperationCall(bundle_id=target_bundle, operation=operation, data=dict(data or {})),
        comm_context=_context(source_bundle), redis=None, pg_pool=None,
    )


@pytest.mark.asyncio
async def test_the_local_path_binds_the_source_bundle_as_the_caller(monkeypatch):
    _patch_local(monkeypatch)
    await _local("hub_only", source_bundle=HUB)
    assert SEEN == [("hub_only", HUB)]
    with pytest.raises(BundleOperationCallerNotAllowed) as refused:
        await _local("hub_only", source_bundle="job@1")
    assert refused.value.caller == "job@1" and refused.value.code == BUNDLE_OPERATION_CALLER_NOT_ALLOWED
    assert current_peer_caller_bundle_id() == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn", [False, True], ids=["awaited", "spawned-task"])
async def test_the_local_path_names_the_immediate_caller_on_nested_calls(monkeypatch, spawn):
    _patch_local(monkeypatch)
    await _local("relay", source_bundle="a@1", target_bundle="b@1", data={"target_bundle": "c@1", "spawn": spawn})
    assert SEEN == [("relay", "a@1"), ("open_op", "b@1")]


def test_peer_callers_is_a_decorator_declaration_of_bundle_ids():
    with pytest.raises(ValueError):
        api(alias="x", peer_callers=HUB)  # one string, not a list
    assert getattr(_Workflow.hub_only, API_METHOD_ATTR).peer_callers == (HUB,)
    assert getattr(_Workflow.open_op, API_METHOD_ATTR).peer_callers == ()

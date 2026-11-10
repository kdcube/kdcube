# SPDX-License-Identifier: MIT

"""Proc runtime services reach a workflow on the local (request-less) call paths.

The chat processor puts ``chat_submitter`` and ``conversation_browser`` on
``app.state``. The bundle REST path binds them from ``request.app.state``. The
local named-service and operation call paths have no request; before this fix a
workflow reached through them had neither service, so a named service that
submits a chat turn could not find the submitter.
"""

from __future__ import annotations

import ast
import pathlib
import types

import pytest

from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations as ops

WEB_APP = pathlib.Path(ops.__file__).resolve().parents[2] / "proc" / "web_app.py"


class _Workflow:
    """Exposes neither a named-service registry nor an API endpoint."""


@pytest.fixture(autouse=True)
def _empty_holder():
    ops.register_proc_runtime_services(chat_submitter=None, conversation_browser=None)
    yield
    ops.register_proc_runtime_services(chat_submitter=None, conversation_browser=None)


def _stub_loader(monkeypatch, workflow):
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
    import kdcube_ai_app.infra.service_hub.inventory as inventory

    spec = types.SimpleNamespace(id="target@1", path="/b", module="entrypoint", singleton=False)

    async def _resolve_spec(*_a, **_k):
        return spec

    async def _secrets(req, **_k):
        return req

    async def _instance(*_a, **_k):
        return workflow, None

    async def _props(*_a, **_k):
        return {}

    async def _session(*_a, **_k):
        return types.SimpleNamespace(user_type="registered", session_id="s")

    async def _apply_props(**_k):
        return None

    monkeypatch.setattr(bundle_store, "resolve_bundle_spec_from_store", _resolve_spec)
    monkeypatch.setattr(bundle_store, "get_bundle_props", _props)
    monkeypatch.setattr(inventory, "ConfigRequest", lambda **kw: types.SimpleNamespace(**kw))
    monkeypatch.setattr(inventory, "resolve_config_request_secrets", _secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda _req: types.SimpleNamespace())
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", _instance)
    monkeypatch.setattr(ops, "_session_for_comm_context", _session)
    monkeypatch.setattr(ops, "_target_comm_context", lambda cc, **_k: cc)
    monkeypatch.setattr(ops, "_apply_bundle_props_to_workflow", _apply_props)


async def _invoke_named_service(monkeypatch, workflow):
    _stub_loader(monkeypatch, workflow)
    call = ops.BundleNamedServiceCall(
        bundle_id="target@1", tenant="t", project="p", registry_method="named_services", request={},
    )
    # The stub workflow has no registry: the call stops right after the binding point.
    with pytest.raises(RuntimeError, match="does not expose named-service registry"):
        await ops.invoke_local_bundle_named_service(
            call, comm_context=types.SimpleNamespace(actor=None), redis=None, pg_pool=None,
        )
    return workflow


async def _invoke_operation(monkeypatch, workflow):
    import kdcube_ai_app.infra.plugin.bundle_loader as bundle_loader

    _stub_loader(monkeypatch, workflow)
    endpoint = types.SimpleNamespace(
        alias="op", http_method="POST", route="operations", user_types=(), roles=(), method_name="op",
    )
    monkeypatch.setattr(bundle_loader, "resolve_bundle_api_endpoint", lambda *_a, **_k: (endpoint, ["POST"]))
    monkeypatch.setattr(bundle_loader, "apply_api_overrides", lambda spec, _props: spec)
    monkeypatch.setattr(bundle_loader, "provider_surface_auth", lambda *_a, **_k: None)
    monkeypatch.setattr(ops, "_endpoint_visible", lambda *_a, **_k: True)

    def _api_disabled(_props, _spec):
        raise RuntimeError("stop after binding")

    monkeypatch.setattr(ops, "_api_enabled", _api_disabled)
    call = ops.BundleOperationCall(bundle_id="target@1", operation="op", tenant="t", project="p", data={})
    with pytest.raises(RuntimeError, match="stop after binding"):
        await ops._invoke_local_bundle_operation_raw(
            call, comm_context=types.SimpleNamespace(actor=None), redis=None, pg_pool=None,
        )
    return workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("invoke", [_invoke_named_service, _invoke_operation], ids=["named_service", "operation"])
async def test_registered_services_are_bound_on_local_call_paths(monkeypatch, invoke):
    submitter, browser = object(), object()
    ops.register_proc_runtime_services(chat_submitter=submitter, conversation_browser=browser)

    wf = await invoke(monkeypatch, _Workflow())

    assert wf.chat_submitter is submitter
    assert wf.conversation_browser is browser


@pytest.mark.asyncio
@pytest.mark.parametrize("invoke", [_invoke_named_service, _invoke_operation], ids=["named_service", "operation"])
async def test_nothing_registered_binds_nothing(monkeypatch, invoke):
    """Ingress, workers and any process that never registers keep their behaviour."""
    wf = await invoke(monkeypatch, _Workflow())

    assert not hasattr(wf, "chat_submitter")
    assert not hasattr(wf, "conversation_browser")


@pytest.mark.asyncio
async def test_a_service_already_on_the_workflow_is_kept(monkeypatch):
    mine, registered, browser = object(), object(), object()
    workflow = _Workflow()
    workflow.chat_submitter = mine
    ops.register_proc_runtime_services(chat_submitter=registered, conversation_browser=browser)

    wf = await _invoke_named_service(monkeypatch, workflow)

    assert wf.chat_submitter is mine
    assert wf.conversation_browser is browser


def test_register_rejects_unknown_names_and_none_forgets():
    with pytest.raises(ValueError):
        ops.register_proc_runtime_services(conversation_store=object())
    ops.register_proc_runtime_services(chat_submitter=object())
    ops.register_proc_runtime_services(chat_submitter=None)
    assert ops._PROC_RUNTIME_SERVICES == {}


def _register_calls(tree: ast.AST) -> list[dict[str, str]]:
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "register_proc_runtime_services":
            calls.append({k.arg: ast.unparse(k.value) for k in node.keywords})
    return calls


def test_the_proc_lifespan_registers_its_services_and_forgets_them_on_shutdown():
    """web_app.py needs Redis and Postgres to import; check its lifespan by AST."""
    calls = _register_calls(ast.parse(WEB_APP.read_text(encoding="utf-8")))

    assert {
        "chat_submitter": "app.state.chat_submitter",
        "conversation_browser": "conversation_browser",
    } in calls
    assert {"chat_submitter": "None", "conversation_browser": "None"} in calls

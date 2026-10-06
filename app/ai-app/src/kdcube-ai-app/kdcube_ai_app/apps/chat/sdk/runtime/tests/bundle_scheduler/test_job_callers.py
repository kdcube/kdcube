# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Scheduled caller scope/lifetime and normal local dispatch with fixture bundles."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations as operations
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    AuthContext,
    bind_auth_context,
    get_current_auth_context,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime import bundle_scheduler as scheduler
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_id,
    bind_current_request_context,
    get_current_bundle_id,
    get_current_comm,
    get_current_request_context,
)


@pytest.fixture
def invocation(monkeypatch):
    from kdcube_ai_app.apps.chat.ingress import resolvers
    from kdcube_ai_app.infra.plugin import bundle_loader

    calls = []
    contexts = []
    callbacks = {}
    pool = object()
    monkeypatch.setattr(resolvers, "get_pg_pool", AsyncMock(return_value=pool))

    async def load(spec, config, *, comm_context, redis, pg_pool):
        contexts.append(comm_context)
        assert pg_pool is pool
        return SimpleNamespace(run=callbacks[spec.id]), None

    async def dispatch(call, *, comm_context, redis, pg_pool):
        assert pg_pool is pool
        calls.append((call, comm_context, get_current_auth_context(), redis))
        return {"data": call.data, "operation": call.operation}

    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", load)
    monkeypatch.setattr(operations, "invoke_local_bundle_operation", dispatch)

    async def invoke(callback, *, bundle="source@1-0", tenant="tenant", project="project"):
        callbacks[bundle] = callback
        await scheduler._invoke_job(
            bundle_id=bundle,
            job_alias="recover",
            method_name="run",
            bundle_spec=SimpleNamespace(id=bundle),
            bundle_config=SimpleNamespace(tenant=tenant, project=project, redis=object()),
        )

    return SimpleNamespace(invoke=invoke, calls=calls, contexts=contexts)


def test_job_calls_normal_local_bridge_as_its_service_actor(invocation):
    async def run():
        auth = get_current_auth_context()
        assert auth.principal_kind == "job"
        assert auth.principal_id == "source@1-0:recover"
        assert auth.metadata == {"job_alias": "recover"}
        assert auth.user_type == "service" and auth.user_id is None
        assert get_current_bundle_id() == "source@1-0"
        assert get_current_comm() is None
        result = await operations.call_bundle_operation(
            bundle_id="target@1-0", operation="finish", data={"intent": "recorded-intent"},
        )
        assert result == {"operation": "finish", "data": {"intent": "recorded-intent"}}
        _, context, caller_auth, _ = invocation.calls[0]
        assert caller_auth is auth
        assert context == get_current_request_context()
        assert context is not get_current_request_context()
        assert context.actor.tenant_id == "tenant"
        assert context.actor.project_id == "project"
        assert context.routing.bundle_id == "source@1-0"
        assert context.routing.session_id == ""
        assert context.user.user_type == "service"
        assert context.user.user_id is None
        assert context.user.roles == [] and context.user.permissions == []
        assert context.user.identity_authority == {}
        assert invocation.contexts[0] == context

    asyncio.run(invocation.invoke(run))
    assert operations.get_current_bundle_operation_caller() is None
    assert get_current_auth_context() is None
    assert get_current_request_context() is None


@pytest.mark.parametrize("outcome", ["success", "exception", "cancellation", "sync"])
def test_job_restores_ambient_context_on_every_exit(invocation, outcome):
    ambient_caller = AsyncMock(side_effect=AssertionError("ambient caller was reused"))
    ambient_auth = AuthContext.from_mapping({"tenant": "other", "project": "other", "user_id": "interactive"})
    ambient_request = ExternalEventPayload(
        actor=ExternalEventActor(tenant_id="other", project_id="other"),
        routing=ExternalEventRouting(bundle_id="interactive@1-0", session_id="interactive-session"),
        user=ExternalEventUser(user_type="registered", user_id="interactive", roles=["admin"]),
    )
    communicator = object()

    def check():
        assert operations.get_current_bundle_operation_caller() is not ambient_caller
        assert operations.get_current_bundle_operation_caller() is not None
        assert get_current_auth_context().principal_kind == "job"
        assert get_current_request_context().user.user_id is None
        assert get_current_comm() is None
        assert operations.get_current_bundle_mcp_caller() is None
        assert operations.get_current_bundle_named_service_caller() is None
        assert operations.get_current_bundle_operation_stream_caller() is None

    async def run():
        check()
        if outcome == "exception":
            raise ValueError("job failed")
        if outcome == "cancellation":
            raise asyncio.CancelledError()

    async def exercise():
        with (
            operations.bind_bundle_operation_caller(ambient_caller),
            operations.bind_bundle_mcp_caller(ambient_caller),
            operations.bind_bundle_named_service_caller(ambient_caller),
            operations.bind_bundle_operation_stream_caller(ambient_caller),
            bind_auth_context(ambient_auth),
            bind_current_request_context(ambient_request, comm=communicator),
        ):
            try:
                if outcome in {"exception", "cancellation"}:
                    expected = ValueError if outcome == "exception" else asyncio.CancelledError
                    with pytest.raises(expected):
                        await invocation.invoke(run)
                else:
                    await invocation.invoke(check if outcome == "sync" else run)
            finally:
                assert operations.get_current_bundle_operation_caller() is ambient_caller
                assert get_current_auth_context() is ambient_auth
                assert get_current_request_context() is ambient_request
                assert get_current_comm() is communicator
                assert get_current_bundle_id() == "interactive@1-0"
                assert operations.get_current_bundle_mcp_caller() is ambient_caller
                assert operations.get_current_bundle_named_service_caller() is ambient_caller
                assert operations.get_current_bundle_operation_stream_caller() is ambient_caller
        ambient_caller.assert_not_called()

    asyncio.run(exercise())


@pytest.mark.parametrize("override", [{"tenant": "other"}, {"project": "other"}])
def test_job_refuses_scope_override_before_dispatch(invocation, override):
    async def run():
        with pytest.raises(RuntimeError, match="scope"):
            await operations.call_bundle_operation(bundle_id="target", operation="finish", **override)
        assert invocation.calls == []

    asyncio.run(invocation.invoke(run))


@pytest.mark.parametrize("missing", ["tenant", "project", "bundle"])
def test_missing_job_scope_is_refused_before_loading(invocation, missing):
    async def exercise():
        with pytest.raises(RuntimeError, match="scope"):
            await invocation.invoke(AsyncMock(), **{missing: ""})
        assert invocation.contexts == []
        assert operations.get_current_bundle_operation_caller() is None

    asyncio.run(exercise())


@pytest.mark.parametrize("wrong", ["auth", "bundle", "request"])
def test_captured_caller_requires_its_active_job_context(invocation, wrong):
    async def run():
        caller = operations.get_current_bundle_operation_caller()
        assert caller is not None
        boundary = {
            "auth": lambda: bind_auth_context(None),
            "bundle": lambda: bind_current_bundle_id("other"),
            "request": lambda: bind_current_request_context(ExternalEventPayload()),
        }[wrong]()
        with boundary, pytest.raises(RuntimeError, match="job context"):
            await caller(operations.BundleOperationCall(bundle_id="target", operation="finish"))
        assert invocation.calls == []

    asyncio.run(invocation.invoke(run))


def test_nested_job_restores_the_outer_jobs_caller(invocation):
    async def inner():
        await operations.call_bundle_operation(bundle_id="target", operation="inner")

    async def outer():
        caller = operations.get_current_bundle_operation_caller()
        assert caller is not None
        await invocation.invoke(inner, bundle="inner@1-0", tenant="inner-tenant", project="inner-project")
        assert operations.get_current_bundle_operation_caller() is caller
        await operations.call_bundle_operation(bundle_id="target", operation="outer")

    asyncio.run(invocation.invoke(outer))
    assert [(call.operation, context.actor.tenant_id, context.routing.bundle_id)
            for call, context, _, _ in invocation.calls] == [
        ("inner", "inner-tenant", "inner@1-0"), ("outer", "tenant", "source@1-0"),
    ]


def test_concurrent_jobs_have_separate_scoped_callers(invocation):
    async def exercise():
        barrier = asyncio.Barrier(2)

        async def run():
            await barrier.wait()
            await operations.call_bundle_operation(bundle_id="target", operation="recover")

        await asyncio.gather(
            invocation.invoke(run, bundle="one", tenant="one-tenant"),
            invocation.invoke(run, bundle="two", tenant="two-tenant"),
        )

    asyncio.run(exercise())
    assert {(context.routing.bundle_id, context.actor.tenant_id) for _, context, _, _ in invocation.calls} == {
        ("one", "one-tenant"), ("two", "two-tenant"),
    }


def test_peer_scope_is_independent_of_mutable_job_projection(invocation):
    async def run():
        request = get_current_request_context()
        request.actor.tenant_id = "other"
        request.user.user_id = "interactive"
        request.user.roles.append("admin")
        await operations.call_bundle_operation(bundle_id="target", operation="finish")

    asyncio.run(invocation.invoke(run))
    _, context, auth, _ = invocation.calls[0]
    assert context.actor.tenant_id == auth.tenant == "tenant"
    assert context.user.user_id is None and context.user.roles == []


def test_external_cancellation_expires_inherited_caller(invocation):
    async def exercise():
        started = asyncio.Event()
        captured = []

        async def run():
            captured.append(operations.get_current_bundle_operation_caller())
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(invocation.invoke(run))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RuntimeError, match="job context"):
            await captured[0](operations.BundleOperationCall(bundle_id="target", operation="finish"))
        assert operations.get_current_bundle_operation_caller() is None
        assert invocation.calls == []

    asyncio.run(exercise())


def test_child_task_cannot_keep_a_finished_jobs_caller(invocation):
    async def exercise():
        released = asyncio.Event()
        children = []

        async def delayed_call():
            await released.wait()
            return await operations.call_bundle_operation(bundle_id="target", operation="finish")

        async def run():
            assert operations.get_current_bundle_operation_caller() is not None
            children.append(asyncio.create_task(delayed_call()))

        await invocation.invoke(run)
        released.set()
        with pytest.raises(RuntimeError, match="job context"):
            await children[0]
        assert invocation.calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("restricted", [False, True])
def test_scheduled_job_uses_real_dispatch_and_target_admission(monkeypatch, restricted):
    """Registry/config/loader are fixtures; the SDK caller and dispatch are real."""
    from kdcube_ai_app.apps.chat.ingress import resolvers
    from kdcube_ai_app.infra.plugin import bundle_loader, bundle_store
    from kdcube_ai_app.infra.service_hub import inventory

    seen = []
    pool = object()
    monkeypatch.setattr(resolvers, "get_pg_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(bundle_store, "resolve_bundle_spec_from_store", AsyncMock(return_value=SimpleNamespace(
        id="peer@1-0", path="fixture-peer", module="entrypoint", singleton=False,
    )))
    monkeypatch.setattr(bundle_store, "get_bundle_props", AsyncMock(return_value={}))

    async def resolve_secrets(request, **kwargs):
        seen.append(("config", request.tenant, request.project, kwargs["bundle_id"]))
        return request

    monkeypatch.setattr(inventory, "resolve_config_request_secrets", resolve_secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda request: SimpleNamespace())

    class Peer:
        @bundle_loader.api(alias="finish", route="public", roles=["kdcube:role:super-admin"] if restricted else [])
        async def finish(self, *, data, user_id, fingerprint):
            context = get_current_request_context()
            assert context.user.user_type == "service"
            assert context.user.user_id is None and context.user.identity_authority == {}
            assert context.routing.session_id == "" and context.routing.bundle_id == "peer@1-0"
            assert (context.actor.tenant_id, context.actor.project_id) == ("tenant", "project")
            assert get_current_auth_context().principal_id == "source@1-0:recover"
            assert user_id is None and fingerprint is None
            seen.append(("dispatch", data))
            return {"ok": True, "data": data}

    async def run():
        caller = operations.get_current_bundle_operation_caller()
        if restricted:
            with pytest.raises(RuntimeError, match="not visible"):
                await operations.call_bundle_operation(bundle_id="peer@1-0", operation="finish", route="public")
        else:
            result = await operations.call_bundle_operation(
                bundle_id="peer@1-0", operation="finish", route="public", data={"data": {"intent": "original"}},
            )
            assert result == {"ok": True, "data": {"intent": "original"}}
        assert operations.get_current_bundle_operation_caller() is caller
        assert get_current_bundle_id() == "source@1-0"

    async def load(spec, config, **kwargs):
        assert kwargs["pg_pool"] is pool
        return (SimpleNamespace(run=run), None) if spec.id == "source@1-0" else (Peer(), None)

    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", load)
    asyncio.run(scheduler._invoke_job(
        bundle_id="source@1-0", job_alias="recover", method_name="run",
        bundle_spec=SimpleNamespace(id="source@1-0"),
        bundle_config=SimpleNamespace(tenant="tenant", project="project", redis=object()),
    ))
    assert seen == [("config", "tenant", "project", "peer@1-0")] + (
        [] if restricted else [("dispatch", {"intent": "original"})]
    )
    assert operations.get_current_bundle_operation_caller() is None

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import PRINCIPAL_JOB, AuthContext, bind_auth_context
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import bind_bundle_operation_caller
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceApiEndpoint,
    NamedServiceCanvasObjectResolver,
    NamedServiceClient,
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRegistry,
    NamedServiceRequest,
    NamedServiceResponse,
    call_named_service_api_endpoint,
    dispatch_named_service_api_request,
    build_default_operations,
    extend_tool_specs_for_named_services,
    named_service_provider,
    named_service_namespaces,
    register_configured_named_service_canvas_resolvers,
)
import kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools as named_service_client_tools
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import CanvasObjectResolverRegistry


@named_service_provider(
    provider_id="task.issue",
    bundle_id="task-tracker@1-0",
    namespace="task",
    refs=("task:issues/*",),
    object_kinds=("task.issue",),
    operations=build_default_operations(("local", "api", "mcp", "data_bus")),
)
class TaskIssueProvider(NamedServiceProvider):
    async def provider_about(self, ctx, request):
        return {
            "ok": True,
            "data": {
                "label": "Task issues",
                "tenant": ctx.tenant,
                "user_id": ctx.user_id,
            },
        }

    async def object_search(self, ctx, request):
        return {
            "ok": True,
            "items": [{"object_ref": "task:issues/BUG-123", "title": request.query}],
            "next_cursor": None,
        }

    async def object_action(self, ctx, request):
        return {
            "ok": True,
            "object_ref": request.object_ref,
            "ui_event": {
                "type": "kdcube.ui.object.open.requested",
                "target_surface": "task_tracker.issue_editor",
                "object_ref": request.object_ref,
                "params": {"issue_id": request.object_ref.rsplit("/", 1)[-1]},
            },
            "data": {"actor": ctx.user_id, "action": request.action},
        }


@pytest.mark.asyncio
async def test_client_routes_by_object_ref_and_preserves_context():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    client = NamedServiceClient(
        registry,
        context=NamedServiceContext(tenant="t", project="p", user_id="u1", roles=("kdcube:role:operator",)),
    )

    response = await client.action(object_ref="task:issues/BUG-123", action="open")

    assert response.ok is True
    assert response.provider["provider_id"] == "task.issue"
    assert response.namespace == "task"
    assert response.ui_event["target_surface"] == "task_tracker.issue_editor"
    assert response.data["actor"] == "u1"


@pytest.mark.asyncio
async def test_client_can_hydrate_context_from_current_request():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    payload = ExternalEventPayload(
        meta=ExternalEventMeta(task_id="req-1", created_at=1.0),
        routing=ExternalEventRouting(bundle_id="task-tracker@1-0", session_id="session-1"),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="user-1"),
    )

    with bind_current_request_context(payload):
        client = NamedServiceClient.from_current_request(registry)
        response = await client.about(namespace="task")

    assert response.ok is True
    assert response.data["tenant"] == "tenant-a"
    assert response.data["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_api_transport_dispatches_through_local_loop_with_bound_context():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    payload = ExternalEventPayload(
        meta=ExternalEventMeta(task_id="req-1", created_at=1.0),
        routing=ExternalEventRouting(bundle_id="task-tracker@1-0", session_id="session-1"),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="api-user"),
    )

    with bind_current_request_context(payload):
        response = await dispatch_named_service_api_request(
            registry,
            {
                "operation": "provider.about",
                "namespace": "task",
            },
        )

    assert response["ok"] is True
    assert response["data"]["tenant"] == "tenant-a"
    assert response["data"]["user_id"] == "api-user"


@pytest.mark.asyncio
async def test_api_transport_accepts_wrapped_request_payload():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())

    response = await dispatch_named_service_api_request(
        registry,
        {
            "data": {
                "operation": "object.action",
                "object_ref": "task:issues/BUG-123",
                "action": "open",
            }
        },
        auth_context=AuthContext.from_mapping(
            {
                "tenant": "tenant-a",
                "project": "project-a",
                "user_id": "api-user",
            }
        ),
    )

    assert response["ok"] is True
    assert response["ui_event"]["target_surface"] == "task_tracker.issue_editor"
    assert response["data"]["actor"] == "api-user"


@pytest.mark.asyncio
async def test_api_endpoint_client_calls_bound_bundle_operation_and_unwraps_response():
    async def _caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.operation == "named_service"
        assert call.route == "operations"
        assert call.data["operation"] == "provider.about"
        assert call.data["provider"] == "task.issue"
        assert call.data["namespace"] == "task"
        return {
            "named_service": {
                "ok": True,
                "provider": {"provider_id": "task.issue"},
                "namespace": "task",
                "data": {"label": "Task issues"},
            }
        }

    endpoint = NamedServiceApiEndpoint(
        bundle_id="task-tracker@1-0",
        provider="task.issue",
        namespace="task",
    )

    with bind_bundle_operation_caller(_caller):
        response = await call_named_service_api_endpoint(
            endpoint,
            NamedServiceRequest(operation="provider.about"),
        )

    assert response.ok is True
    assert response.provider == {"provider_id": "task.issue"}
    assert response.namespace == "task"
    assert response.data == {"label": "Task issues"}


@pytest.mark.asyncio
async def test_canvas_resolver_maps_named_service_object_action():
    async def _caller(call):
        assert call.data["operation"] == "object.action"
        assert call.data["object_ref"] == "task:issues/BUG-123"
        assert call.data["action"] == "open"
        assert call.data["context"]["source"] == "canvas.object_action"
        return {
            "named_service": {
                "ok": True,
                "provider": {"provider_id": "task.issue"},
                "namespace": "task",
                "object_ref": "task:issues/BUG-123",
                "object": {
                    "title": "Broken auth flow",
                    "object_kind": "task.issue",
                },
                "ui_event": {
                    "type": "kdcube.ui.object.open.requested",
                    "target_surface": "task_tracker.issue_editor",
                    "object_ref": "task:issues/BUG-123",
                },
                "data": {"action": "open"},
            }
        }

    resolver = NamedServiceCanvasObjectResolver(
        namespace="task",
        endpoint=NamedServiceApiEndpoint(
            bundle_id="task-tracker@1-0",
            provider="task.issue",
            namespace="task",
        ),
    )

    with bind_bundle_operation_caller(_caller):
        result = await resolver.object_action(
            {"object_ref": "task:issues/BUG-123"},
            user_id="user-1",
            story_id="story-1",
            action="open",
        )

    assert result["ok"] is True
    assert result["resolver"] == "named_service.task.issue"
    assert result["resolver_status"] == "configured"
    assert result["title"] == "Broken auth flow"
    assert result["ui_event"]["target_surface"] == "task_tracker.issue_editor"


@pytest.mark.asyncio
async def test_configured_canvas_resolver_helper_registers_namespace_resolver():
    async def _caller(call):
        assert call.tenant == "tenant-a"
        assert call.project == "project-a"
        assert call.bundle_id == "task-tracker@1-0"
        assert call.data["provider"] == "task.issue"
        return {
            "named_service": {
                "ok": True,
                "namespace": "task",
                "object_ref": call.data["object_ref"],
                "object": {"title": "Registered from config"},
            }
        }

    registry = CanvasObjectResolverRegistry()
    count = register_configured_named_service_canvas_resolvers(
        registry,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "task": {
                "provider": {
                    "bundle_id": "task-tracker@1-0",
                    "provider": "task.issue",
                },
            }
        },
    )

    assert count == 1
    with bind_bundle_operation_caller(_caller):
        result = await registry.object_action(
            {"object_ref": "task:issues/BUG-123", "action": "preview"},
            user_id="user-1",
            story_id="story-1",
        )

    assert result["ok"] is True
    assert result["resolver"] == "named_service.task.issue"
    assert result["title"] == "Registered from config"


@pytest.mark.asyncio
async def test_client_supports_headless_bundle_job_context():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    client = NamedServiceClient.for_bundle_job(
        registry,
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        job_alias="nightly-index",
    )

    response = await client.action(object_ref="task:issues/BUG-123", action="open")

    assert response.ok is True
    assert response.data["actor"] is None
    assert client.context.auth_context is not None
    assert client.context.auth_context.principal_kind == PRINCIPAL_JOB


@pytest.mark.asyncio
async def test_client_defaults_to_bound_auth_context_without_ingress():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    auth = AuthContext.for_bundle_job(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        job_alias="nightly-index",
    )

    with bind_auth_context(auth):
        client = NamedServiceClient(registry)
        response = await client.about(namespace="task")

    assert response.ok is True
    assert response.data["tenant"] == "tenant-a"
    assert response.data["user_id"] is None
    assert client.context.auth_context is auth


@pytest.mark.asyncio
async def test_client_can_hydrate_context_from_data_bus_context():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    data_bus_context = SimpleNamespace(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        stream_id="stream-1",
        actor={"user_id": "data-bus-user", "user_type": "registered"},
    )

    client = NamedServiceClient.from_data_bus_context(registry, data_bus_context)
    response = await client.about(namespace="task")

    assert response.ok is True
    assert response.data["tenant"] == "tenant-a"
    assert response.data["user_id"] == "data-bus-user"


@pytest.mark.asyncio
async def test_client_routes_by_namespace_for_search():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    client = NamedServiceClient(registry)

    response = await client.search(namespace="task", query="blocked auth")

    assert response.ok is True
    assert response.items == [{"object_ref": "task:issues/BUG-123", "title": "blocked auth"}]


@pytest.mark.asyncio
async def test_unknown_provider_returns_bounded_error_response():
    client = NamedServiceClient(NamedServiceRegistry())

    response = await client.get(object_ref="task:issues/BUG-404")

    assert response.ok is False
    assert response.status == 404
    assert response.error.code == "named_service_provider_not_found"
    assert response.object_ref == "task:issues/BUG-404"


@pytest.mark.asyncio
async def test_transport_must_be_declared_for_operation():
    spec = NamedServiceProviderSpec(
        provider_id="task.issue",
        namespace="task",
        refs=("task:issues/*",),
        operations=build_default_operations(("local",)),
    )
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider(spec=spec))
    client = NamedServiceClient(registry, transport="mcp")

    response = await client.search(namespace="task", query="anything")

    assert response.ok is False
    assert response.status == 400
    assert response.error.code == "named_service_transport_not_supported"


@pytest.mark.asyncio
async def test_provider_methods_must_be_async():
    class BadProvider(NamedServiceProvider):
        def object_get(self, ctx, request):
            return {"ok": True}

    spec = NamedServiceProviderSpec(provider_id="bad.provider", namespace="bad")
    registry = NamedServiceRegistry()
    registry.register(BadProvider(spec=spec))
    client = NamedServiceClient(registry)

    with pytest.raises(TypeError, match="must be async"):
        await client.get(namespace="bad", object_id="x")


def test_named_service_tools_are_added_only_for_configured_client():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "provider": {
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                    },
                    "clients": {
                        "main": {
                            "tools": {
                                "operations": ["object.search", "object.get"],
                            },
                        },
                    },
                }
            },
        }
    }

    specs = extend_tool_specs_for_named_services(
        [{"module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools", "alias": "io_tools"}],
        bundle_props=props,
        client_id="main",
    )
    disabled_specs = extend_tool_specs_for_named_services(
        [{"module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools", "alias": "io_tools"}],
        bundle_props=props,
        client_id="reviewer",
    )

    assert named_service_namespaces(props)["task"]["provider"]["bundle_id"] == "task-tracker@1-0"
    assert any(spec["alias"] == "named_services" for spec in specs)
    assert not any(spec["alias"] == "named_services" for spec in disabled_specs)


def test_named_service_tools_support_default_client_policy():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "provider": {
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                    },
                    "clients": {
                        "default_client": {
                            "tools": {
                                "operations": ["provider.about", "object.search"],
                            },
                        },
                    },
                }
            },
        }
    }

    specs = extend_tool_specs_for_named_services(
        [{"module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools", "alias": "io_tools"}],
        bundle_props=props,
        client_id="solver.react.v2.decision.v2.strong",
    )

    assert any(spec["alias"] == "named_services" for spec in specs)


@pytest.mark.asyncio
async def test_named_service_client_tool_uses_client_policy_and_cursor():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "provider": {
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                    },
                    "clients": {
                        "main": {
                            "tools": {
                                "operations": ["object.search", "object.get", "object.action"],
                                "actions": ["preview", "open"],
                            },
                        },
                    },
                }
            },
        }
    }
    calls = []

    async def _caller(call):
        calls.append(call)
        assert call.bundle_id == "task-tracker@1-0"
        assert call.data["operation"] == "object.search"
        assert call.data["provider"] == "task.issue"
        assert call.data["namespace"] == "task"
        assert call.data["query"] == "blocked auth"
        assert call.data["cursor"] == "page-2"
        return {
            "named_service": {
                "ok": True,
                "namespace": "task",
                "items": [{"object_ref": "task:issues/BUG-123"}],
                "next_cursor": "page-3",
            }
        }

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "main"})
    with bind_bundle_operation_caller(_caller):
        result = await named_service_client_tools.search_objects(
            namespace="task",
            query="blocked auth",
            cursor="page-2",
            limit=5,
        )

    assert result["ok"] is True
    assert result["next_cursor"] == "page-3"
    assert calls


@pytest.mark.asyncio
async def test_named_service_client_tool_denies_unconfigured_mutation():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "provider": {
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                    },
                    "clients": {
                        "main": {
                            "tools": {
                                "operations": ["object.search", "object.get", "object.action"],
                            },
                        },
                    },
                }
            },
        }
    }

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "main"})
    result = await named_service_client_tools.upsert_object(
        namespace="task",
        object_json='{"title":"New task"}',
    )

    assert result["ok"] is False
    assert result["error"] == "named_service_operation_not_allowed_for_client"

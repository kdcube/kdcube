# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import PRINCIPAL_JOB, AuthContext, bind_auth_context
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    BundleOperationStreamResult,
    bind_bundle_named_service_caller,
    bind_bundle_operation_caller,
    bind_bundle_operation_stream_caller,
)
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleStreamResponse
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx as comm_ctx_mod
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceEndpoint,
    NamedServiceCanvasObjectResolver,
    NamedServiceClient,
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRegistry,
    NamedServiceRequest,
    NamedServiceResponse,
    RedisNamedServiceDiscovery,
    bind_named_service_discovery,
    call_named_service_endpoint,
    call_named_service_endpoint_stream,
    dispatch_named_service_api_request,
    build_default_operations,
    extend_tool_specs_for_named_services,
    named_service_provider,
    named_service_namespaces,
    register_configured_named_service_artifact_rehosters,
    register_configured_named_service_canvas_resolvers,
    register_configured_named_service_event_sources,
)
import kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery as discovery_mod
import kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools as named_service_client_tools
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import CanvasObjectResolverRegistry


class FakeDiscoveryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.set_ex: dict[str, Any] = {}
        self.expire_calls: list[tuple[str, Any]] = []
        self.persist_calls: list[str] = []

    async def set(self, key, value, ex=None):
        self.values[str(key)] = value
        self.set_ex[str(key)] = ex
        return True

    async def get(self, key):
        return self.values.get(str(key))

    async def sadd(self, key, *values):
        bucket = self.sets.setdefault(str(key), set())
        before = len(bucket)
        for value in values:
            bucket.add(str(value))
        return len(bucket) - before

    async def smembers(self, key):
        return set(self.sets.get(str(key), set()))

    async def expire(self, key, seconds):
        self.expire_calls.append((str(key), seconds))
        return True

    async def persist(self, key):
        self.persist_calls.append(str(key))
        return True


def _canonical_issue_object(ref: str = "task:issues/BUG-123", title: str = "Issue") -> dict[str, Any]:
    issue_id = ref.rsplit("/", 1)[-1]
    return {
        "schema": "kdcube.named_service.object.v1",
        "identity": {
            "object_ref": ref,
            "object_id": issue_id,
            "object_kind": "task.issue",
            "namespace": "task",
        },
        "meta": {
            "mime": "application/vnd.kdcube.task.issue+json;version=1",
            "revision": "",
        },
        "body": {
            "title": title,
            "description": "",
            "state": "open",
            "assignee": "",
            "tags": [],
            "attrs": {},
            "attachments": [],
        },
    }


def test_named_service_request_coerce_accepts_equivalent_request_object():
    class ForeignNamedServiceRequest:
        def to_dict(self):
            return {
                "schema": "kdcube.named_service.request.v1",
                "operation": "object.get",
                "namespace": "task",
                "object_ref": "task:issues/BUG-123",
            }

    request = NamedServiceRequest.coerce(ForeignNamedServiceRequest())

    assert request.operation == "object.get"
    assert request.namespace == "task"
    assert request.object_ref == "task:issues/BUG-123"


def test_named_service_response_coerce_accepts_equivalent_response_object():
    class ForeignNamedServiceResponse:
        def to_dict(self):
            return {
                "ok": True,
                "ret": {
                    "attrs": {
                        "namespace": "task",
                        "object_ref": "task:issues/BUG-123",
                    },
                    "object": _canonical_issue_object(),
                },
            }

    response = NamedServiceResponse.coerce(ForeignNamedServiceResponse())

    assert response.ok is True
    assert response.namespace == "task"
    assert response.object_ref == "task:issues/BUG-123"
    assert response.object["identity"]["object_ref"] == "task:issues/BUG-123"
    assert response.object["body"]["title"] == "Issue"


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
            "ret": {
                "extra": {
                    "label": "Task issues",
                    "tenant": ctx.tenant,
                    "user_id": ctx.user_id,
                },
            },
        }

    async def object_search(self, ctx, request):
        return {
            "ok": True,
            "ret": {
                "items": [_canonical_issue_object(title=request.query)],
                "attrs": {"next_cursor": None},
            },
        }

    async def object_action(self, ctx, request):
        return {
            "ok": True,
            "ret": {
                "attrs": {"object_ref": request.object_ref},
                "ui_event": {
                    "type": "kdcube.ui.object.open.requested",
                    "target_surface": "task_tracker.issue_editor",
                    "object_ref": request.object_ref,
                    "params": {"issue_id": request.object_ref.rsplit("/", 1)[-1]},
                },
                "extra": {"actor": ctx.user_id, "action": request.action},
            },
        }

    async def block_produce(self, ctx, request):
        return {
            "ok": True,
            "ret": {
                "attrs": {"object_ref": request.object_ref},
                "extra": {
                    "blocks": [
                        {
                            "type": "named_service.object",
                            "event_source_id": "named_services.task",
                            "object_ref": request.object_ref,
                            "markdown": f"Task block for {request.object_ref}",
                        }
                    ]
                },
            },
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
    assert response.extra["actor"] == "u1"


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
    assert response.extra["tenant"] == "tenant-a"
    assert response.extra["user_id"] == "user-1"


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
    assert response["ret"]["extra"]["tenant"] == "tenant-a"
    assert response["ret"]["extra"]["user_id"] == "api-user"


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
    assert response["ret"]["ui_event"]["target_surface"] == "task_tracker.issue_editor"
    assert response["ret"]["extra"]["actor"] == "api-user"


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
                "ret": {
                    "attrs": {
                        "provider": {"provider_id": "task.issue"},
                        "namespace": "task",
                    },
                    "extra": {"label": "Task issues"},
                },
            }
        }

    endpoint = NamedServiceEndpoint(
        transport="bundle_operation",
        bundle_id="task-tracker@1-0",
        provider="task.issue",
        namespace="task",
    )

    with bind_bundle_operation_caller(_caller):
        response = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(operation="provider.about"),
        )

    assert response.ok is True
    assert response.provider == {"provider_id": "task.issue"}
    assert response.namespace == "task"
    assert response.extra == {"label": "Task issues"}


@pytest.mark.asyncio
async def test_endpoint_defaults_to_direct_bundle_registry_when_bound():
    operation_calls: list[Any] = []

    async def _named_service_caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.registry_method == "named_services"
        assert call.request.operation == "provider.about"
        return BundleNamedServiceResult(
            NamedServiceResponse.ok_response(
                provider={"provider_id": "task.issue"},
                namespace="task",
                extra={"transport": "bundle_registry"},
            )
        )

    async def _operation_caller(call):
        operation_calls.append(call)
        return {}

    endpoint = NamedServiceEndpoint(
        bundle_id="task-tracker@1-0",
        provider="task.issue",
        namespace="task",
    )

    with bind_bundle_named_service_caller(_named_service_caller), bind_bundle_operation_caller(_operation_caller):
        response = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(operation="provider.about"),
        )

    assert response.ok is True
    assert response.extra == {"transport": "bundle_registry"}
    assert operation_calls == []


@pytest.mark.asyncio
async def test_bundle_registry_endpoint_accepts_equivalent_response_object():
    class ForeignNamedServiceResponse:
        def to_dict(self):
            return {
                "ok": True,
                "ret": {
                    "attrs": {
                        "provider": {"provider_id": "task.issue"},
                        "namespace": "task",
                        "object_ref": "task:issues/BUG-123",
                    },
                    "object": _canonical_issue_object(),
                },
            }

    async def _named_service_caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.request.operation == "object.get"
        return BundleNamedServiceResult(ForeignNamedServiceResponse())

    endpoint = NamedServiceEndpoint(
        bundle_id="task-tracker@1-0",
        provider="task.issue",
        namespace="task",
    )

    with bind_bundle_named_service_caller(_named_service_caller):
        response = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(operation="object.get", namespace="task", object_ref="task:issues/BUG-123"),
        )

    assert response.ok is True
    assert response.provider == {"provider_id": "task.issue"}
    assert response.namespace == "task"
    assert response.object_ref == "task:issues/BUG-123"
    assert response.object["identity"]["object_ref"] == "task:issues/BUG-123"
    assert response.object["body"]["title"] == "Issue"


@pytest.mark.asyncio
async def test_discovery_routes_one_namespace_to_multiple_provider_bundles_by_operation_and_ref():
    redis = FakeDiscoveryRedis()
    discovery = RedisNamedServiceDiscovery(redis, tenant="tenant-a", project="project-a")
    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id="task.issue.crud",
            bundle_id="task-crud@1-0",
            namespace="task",
            refs=("task:issues/*",),
            object_kinds=("task.issue",),
            operations={"object.get": {"transports": ["local"]}},
        ),
        bundle_id="task-crud@1-0",
    )
    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id="task.attachment.bytes",
            bundle_id="task-files@1-0",
            namespace="task",
            refs=("task:issues/*/attachments/*",),
            object_kinds=("task.attachment",),
            operations={"object.action": {"transports": ["local"]}},
        ),
        bundle_id="task-files@1-0",
    )
    calls: list[tuple[str, str, str]] = []

    async def _named_service_caller(call):
        calls.append((call.bundle_id, call.request.operation, call.request.object_ref or ""))
        return BundleNamedServiceResult(
            NamedServiceResponse.ok_response(
                provider={"provider_id": call.request.provider},
                namespace=call.request.namespace,
                object_ref=call.request.object_ref,
                extra={"bundle": call.bundle_id},
            )
        )

    endpoint = NamedServiceEndpoint(namespace="task")
    with bind_named_service_discovery(discovery), bind_bundle_named_service_caller(_named_service_caller):
        issue = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(operation="object.get", namespace="task", object_ref="task:issues/BUG-123"),
        )
        attachment = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(
                operation="object.action",
                namespace="task",
                object_ref="task:issues/BUG-123/attachments/a1/v000001/evidence.md",
                action="rehost",
                payload={"object_kind": "task.attachment"},
            ),
        )

    assert issue.ok is True
    assert issue.extra["bundle"] == "task-crud@1-0"
    assert attachment.ok is True
    assert attachment.extra["bundle"] == "task-files@1-0"
    assert calls == [
        ("task-crud@1-0", "object.get", "task:issues/BUG-123"),
        ("task-files@1-0", "object.action", "task:issues/BUG-123/attachments/a1/v000001/evidence.md"),
    ]


@pytest.mark.asyncio
async def test_discovery_registration_is_persistent_by_default():
    redis = FakeDiscoveryRedis()
    discovery = RedisNamedServiceDiscovery(redis, tenant="tenant-a", project="project-a")

    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id="task.issue",
            bundle_id="task-tracker@1-0",
            namespace="task",
            refs=("task:issues/*",),
            operations={"provider.about": {"transports": ["local"]}},
        ),
        bundle_id="task-tracker@1-0",
    )

    provider_key = "kdcube:named_services:tenant-a:project-a:provider:task-tracker@1-0::task.issue"
    assert redis.set_ex[provider_key] is None
    assert redis.expire_calls == []
    assert "kdcube:named_services:tenant-a:project-a:providers" in redis.persist_calls
    assert "kdcube:named_services:tenant-a:project-a:namespace:task" in redis.persist_calls


def test_discovery_context_is_portable_through_comm_ctx(monkeypatch):
    redis = FakeDiscoveryRedis()
    discovery = RedisNamedServiceDiscovery(redis, tenant="tenant-a", project="project-a")

    with bind_named_service_discovery(discovery):
        snapshot = comm_ctx_mod.snapshot_ctxvars()

    try:
        comm_ctx_mod.restore_ctxvars(snapshot)
        monkeypatch.setattr(discovery_mod, "_redis_client_from_settings", lambda: redis)

        restored = discovery_mod.get_current_named_service_discovery()

        assert isinstance(restored, RedisNamedServiceDiscovery)
        assert restored.redis is redis
        assert restored.tenant == "tenant-a"
        assert restored.project == "project-a"
    finally:
        discovery_mod._DISCOVERY_CV.set(discovery_mod._DISCOVERY_UNSET)
        comm_ctx_mod.set_current_named_service_discovery_context({})


def test_discovery_reconstructs_from_restored_request_context(monkeypatch):
    redis = FakeDiscoveryRedis()
    request_context = ExternalEventPayload(
        meta=ExternalEventMeta(task_id="req-1", created_at=1.0),
        routing=ExternalEventRouting(bundle_id="versatile@1-0", session_id="session-1"),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="user-1"),
    )

    try:
        comm_ctx_mod.restore_ctxvars({
            "REQUEST_CONTEXT": request_context.model_dump(),
            "BUNDLE_ID": "versatile@1-0",
            "BUNDLE_CALL_CONTEXT": {},
        })
        monkeypatch.setattr(discovery_mod, "_redis_client_from_settings", lambda: redis)

        restored = discovery_mod.get_current_named_service_discovery()

        assert isinstance(restored, RedisNamedServiceDiscovery)
        assert restored.redis is redis
        assert restored.tenant == "tenant-a"
        assert restored.project == "project-a"
    finally:
        discovery_mod._DISCOVERY_CV.set(discovery_mod._DISCOVERY_UNSET)
        comm_ctx_mod.set_current_request_context(None)
        comm_ctx_mod.set_current_named_service_discovery_context({})


@pytest.mark.asyncio
async def test_discovery_backed_client_tools_do_not_require_provider_config():
    redis = FakeDiscoveryRedis()
    discovery = RedisNamedServiceDiscovery(redis, tenant="tenant-a", project="project-a")
    await discovery.register_provider(
        NamedServiceProviderSpec(
            provider_id="task.issue",
            bundle_id="task-tracker@1-0",
            namespace="task",
            refs=("task:issues/*",),
            object_kinds=("task.issue",),
            operations={
                "provider.about": {"transports": ["local"]},
                "object.schema": {"transports": ["local"]},
            },
        ),
        bundle_id="task-tracker@1-0",
    )
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "clients": {
                        "main": {
                            "tools": {
                                "allowed_operations": ["provider.about", "object.schema"],
                            },
                        },
                    },
                }
            },
        }
    }
    calls = []

    async def _named_service_caller(call):
        calls.append(call)
        assert call.bundle_id == "task-tracker@1-0"
        return BundleNamedServiceResult(
            NamedServiceResponse.ok_response(
                provider={"provider_id": call.request.provider},
                namespace=call.request.namespace,
                extra={"operation": call.request.operation},
            )
        )

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "main"})
    with bind_named_service_discovery(discovery), bind_bundle_named_service_caller(_named_service_caller):
        about = await named_service_client_tools.provider_about(namespace="task")
        schema = await named_service_client_tools.object_schema(namespace="task", object_kind="task.issue")

    assert about["ok"] is True
    assert about["ret"]["extra"]["operation"] == "provider.about"
    assert schema["ok"] is True
    assert schema["ret"]["extra"]["operation"] == "object.schema"
    assert [call.request.operation for call in calls] == ["provider.about", "object.schema"]


@pytest.mark.asyncio
async def test_endpoint_streams_direct_bundle_registry_bytes_when_bound():
    async def _chunks():
        yield b"one"
        yield b"two"

    async def _named_service_caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.request.operation == "object.action"
        assert call.request.action == "rehost"
        return BundleNamedServiceResult(
            BundleStreamResponse(
                chunks=_chunks(),
                filename="evidence.md",
                media_type="text/markdown",
            )
        )

    endpoint = NamedServiceEndpoint(
        bundle_id="task-tracker@1-0",
        provider="task.issue",
        namespace="task",
    )

    with bind_bundle_named_service_caller(_named_service_caller):
        result = await call_named_service_endpoint_stream(
            endpoint,
            NamedServiceRequest(
                operation="object.action",
                object_ref="task:issues/BUG-123/attachments/ta_1/v000001/evidence.md",
                action="rehost",
            ),
        )

    data = b""
    async for chunk in result.chunks:
        data += chunk
    assert data == b"onetwo"
    assert result.filename == "evidence.md"
    assert result.media_type == "text/markdown"


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
                "ret": {
                    "attrs": {
                        "provider": {"provider_id": "task.issue"},
                        "namespace": "task",
                        "object_ref": "task:issues/BUG-123",
                    },
                    "object": _canonical_issue_object(title="Broken auth flow"),
                    "ui_event": {
                        "type": "kdcube.ui.object.open.requested",
                        "target_surface": "task_tracker.issue_editor",
                        "object_ref": "task:issues/BUG-123",
                    },
                    "extra": {"action": "open"},
                },
            }
        }

    resolver = NamedServiceCanvasObjectResolver(
        namespace="task",
        endpoint=NamedServiceEndpoint(
            transport="bundle_operation",
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
                "ret": {
                    "attrs": {
                        "namespace": "task",
                        "object_ref": call.data["object_ref"],
                    },
                    "object": _canonical_issue_object(ref=call.data["object_ref"], title="Registered from config"),
                },
            }
        }

    registry = CanvasObjectResolverRegistry()
    count = register_configured_named_service_canvas_resolvers(
        registry,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "task": {
                "clients": {
                    "canvas": {
                        "resolver": {
                            "enabled": True,
                        },
                    },
                },
                "providers": [
                    {
                        "transport": "bundle_operation",
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                        "operations": ["object.action"],
                    }
                ],
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
    assert result["resolver"] == "named_service.task"
    assert result["title"] == "Registered from config"


def test_configured_canvas_resolver_helper_requires_canvas_resolver_enabled():
    registry = CanvasObjectResolverRegistry()

    count = register_configured_named_service_canvas_resolvers(
        registry,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "task": {
                "clients": {
                    "default_client": {
                        "tools": {
                            "allowed_operations": ["object.action"],
                        },
                    },
                },
            }
        },
    )

    assert count == 0


@pytest.mark.asyncio
async def test_configured_canvas_resolver_delegates_capabilities_to_provider():
    calls = []

    async def _caller(call):
        calls.append(call)
        assert call.data["operation"] == "object.action"
        assert call.data["action"] == "capabilities"
        return {
            "named_service": {
                "ok": True,
                "ret": {
                    "attrs": {
                        "namespace": "task",
                        "object_ref": call.data["object_ref"],
                        "capabilities": {"preview": True, "open": True, "download": False, "rehost": True},
                    },
                },
            }
        }

    registry = CanvasObjectResolverRegistry()
    count = register_configured_named_service_canvas_resolvers(
        registry,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "task": {
                "clients": {
                    "canvas": {
                        "resolver": {
                            "enabled": True,
                        },
                    },
                },
                "providers": [
                    {
                        "transport": "bundle_operation",
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                        "operations": ["object.action"],
                    }
                ],
            }
        },
    )

    assert count == 1
    with bind_bundle_operation_caller(_caller):
        result = await registry.object_action(
            {"object_ref": "task:issues/BUG-123", "action": "capabilities"},
            user_id="user-1",
            story_id="story-1",
        )

    assert calls
    assert result["ok"] is True
    assert result["capabilities"]["open"] is True


@pytest.mark.asyncio
async def test_configured_artifact_rehoster_streams_named_service_bytes(tmp_path):
    async def _chunks():
        yield b"alpha"
        yield b"beta"

    async def _stream_caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.operation == "named_service"
        assert call.data["operation"] == "object.action"
        assert call.data["action"] == "rehost"
        assert call.data["object_ref"] == "task:issues/BUG-123/attachments/ta_1/v000001/evidence.md"
        return BundleOperationStreamResult(
            chunks=_chunks(),
            filename="evidence.md",
            media_type="text/markdown",
        )

    event_sources = EventSourceSubsystem()
    count = register_configured_named_service_artifact_rehosters(
        event_sources,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "task": {
                "providers": [
                    {
                        "transport": "bundle_operation",
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                        "operations": ["object.action"],
                    }
                ],
            }
        },
    )

    assert count == 1
    assert event_sources.namespace_rehoster("task") is not None
    runtime = SimpleNamespace(turn_id="turn_rehost")
    ctx_browser = SimpleNamespace(runtime_ctx=runtime)
    with bind_bundle_operation_stream_caller(_stream_caller):
        result = await event_sources.rehost_namespace_ref(
            "task:issues/BUG-123/attachments/ta_1/v000001/evidence.md",
            ctx_browser=ctx_browser,
            outdir=tmp_path,
        )

    assert result["errors"] == []
    assert result["missing"] == []
    assert result["materialized"][0]["logical_path"].startswith("fi:")
    assert result["materialized"][0]["mime"] == "text/markdown"
    assert result["materialized"][0]["size_bytes"] == len(b"alphabeta")
    target = tmp_path / "workdir" / result["materialized"][0]["physical_path"]
    assert target.read_bytes() == b"alphabeta"


@pytest.mark.asyncio
async def test_configured_event_source_delegates_block_production_to_named_service():
    async def _caller(call):
        assert call.bundle_id == "task-tracker@1-0"
        assert call.data["operation"] == "block.produce"
        assert call.data["object_ref"] == "task:issues/BUG-123"
        return {
            "named_service": {
                "ok": True,
                "ret": {
                    "attrs": {
                        "namespace": "task",
                        "object_ref": "task:issues/BUG-123",
                    },
                    "extra": {
                        "blocks": [
                            {
                                "type": "named_service.object",
                                "event_source_id": "named_services.task",
                                "object_ref": "task:issues/BUG-123",
                                "markdown": "Task block",
                            }
                        ]
                    },
                },
            }
        }

    event_sources = EventSourceSubsystem()
    register_configured_named_service_event_sources(
        event_sources,
        namespaces={
            "task": {
                "providers": [
                    {
                        "transport": "bundle_operation",
                        "bundle_id": "task-tracker@1-0",
                        "provider": "task.issue",
                        "operations": ["block.produce"],
                    }
                ],
            }
        },
    )
    target = {
        "event_source_id": "named_services.task",
        "logical_path": "task:issues/BUG-123",
        "blocks": [],
    }

    with bind_bundle_operation_caller(_caller):
        await event_sources.apply_react_phase_policies_async(
            "block_production",
            "named_services.task",
            target,
        )

    assert target["blocks_produced"] is True
    assert target["blocks"][0]["object_ref"] == "task:issues/BUG-123"


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
    assert response.extra["actor"] is None
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
    assert response.extra["tenant"] == "tenant-a"
    assert response.extra["user_id"] is None
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
    assert response.extra["tenant"] == "tenant-a"
    assert response.extra["user_id"] == "data-bus-user"


@pytest.mark.asyncio
async def test_client_routes_by_namespace_for_search():
    registry = NamedServiceRegistry()
    registry.register(TaskIssueProvider())
    client = NamedServiceClient(registry)

    response = await client.search(namespace="task", query="blocked auth")

    assert response.ok is True
    assert response.items[0]["identity"]["object_ref"] == "task:issues/BUG-123"
    assert response.items[0]["body"]["title"] == "blocked auth"


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
                    "clients": {
                        "main": {
                            "tools": {
                                "allowed_operations": ["object.search", "object.get"],
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

    assert "main" in named_service_namespaces(props)["task"]["clients"]
    assert any(spec["alias"] == "named_services" for spec in specs)
    assert not any(spec["alias"] == "named_services" for spec in disabled_specs)


def test_named_service_tools_support_default_client_policy():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "clients": {
                        "default_client": {
                            "tools": {
                                "allowed_operations": ["provider.about", "object.search"],
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


def test_named_service_tool_catalog_hides_operations_not_allowed_for_client():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "clients": {
                        "default_client": {
                            "tools": {
                                "allowed_operations": [
                                    "provider.about",
                                    "object.get",
                                    "object.schema",
                                    "object.upsert",
                                ],
                            },
                        },
                        "canvas": {
                            "resolver": {
                                "enabled": True,
                            },
                        },
                    },
                }
            },
        }
    }

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "solver.react.v2.decision.v2.strong"})
    try:
        catalog = named_service_client_tools.list_tools()
    finally:
        named_service_client_tools.bind_registry({})

    assert "provider_about" in catalog
    assert "get_object" in catalog
    assert "object_schema" in catalog
    assert "upsert_object" in catalog
    assert "object_action" not in catalog
    assert "delete_object" not in catalog


def test_named_service_tool_catalog_defaults_do_not_expose_object_action():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "clients": {
                        "default_client": {
                            "tools": {
                                "enabled": True,
                            },
                        },
                        "canvas": {
                            "resolver": {
                                "enabled": True,
                            },
                        },
                    },
                }
            },
        }
    }

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "solver.react.v2.decision.v2.strong"})
    try:
        catalog = named_service_client_tools.list_tools()
    finally:
        named_service_client_tools.bind_registry({})

    assert "provider_about" in catalog
    assert "get_object" in catalog
    assert "object_schema" in catalog
    assert "object_action" not in catalog


@pytest.mark.asyncio
async def test_named_service_client_tool_uses_client_policy_and_cursor():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "providers": [
                        {
                            "transport": "bundle_operation",
                            "bundle_id": "task-tracker@1-0",
                            "provider": "task.issue",
                            "operations": ["object.search"],
                        }
                    ],
                    "clients": {
                        "main": {
                            "tools": {
                                "allowed_operations": ["object.search", "object.get", "object.action"],
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
                "ret": {
                    "attrs": {
                        "namespace": "task",
                        "next_cursor": "page-3",
                    },
                    "items": [_canonical_issue_object()],
                },
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
    assert result["ret"]["attrs"]["next_cursor"] == "page-3"
    assert calls


@pytest.mark.asyncio
async def test_named_service_client_tool_denies_unconfigured_mutation():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "clients": {
                        "main": {
                            "tools": {
                                "allowed_operations": ["object.search", "object.get", "object.action"],
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


@pytest.mark.asyncio
async def test_named_service_client_tool_reads_object_schema():
    props = {
        "named_services": {
            "namespaces": {
                "task": {
                    "providers": [
                        {
                            "transport": "bundle_operation",
                            "bundle_id": "task-tracker@1-0",
                            "provider": "task.issue",
                            "operations": ["object.schema"],
                        }
                    ],
                    "clients": {
                        "main": {
                            "tools": {
                                "allowed_operations": ["provider.about", "object.schema"],
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
        assert call.data["operation"] == "object.schema"
        assert call.data["payload"]["object_kind"] == "task.issue"
        return {
            "named_service": {
                "ok": True,
                "ret": {
                    "attrs": {"namespace": "task"},
                    "extra": {
                        "schema": {
                            "object_kind": "task.issue",
                            "fields": {"title": {"type": "string"}},
                        }
                    },
                },
            }
        }

    named_service_client_tools.bind_registry({"bundle_props": props, "client_id": "main"})
    with bind_bundle_operation_caller(_caller):
        result = await named_service_client_tools.object_schema(
            namespace="task",
            object_kind="task.issue",
        )

    assert result["ok"] is True
    assert result["ret"]["extra"]["schema"]["object_kind"] == "task.issue"
    assert calls

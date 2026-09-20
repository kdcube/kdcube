# SPDX-License-Identifier: MIT

"""Every conversation read is scoped to the application that called.

kdcube-services serves every `conv` call, so the bundle id on the provider's
own request context is always kdcube-services. These tests drive the real
dispatch of each path into the real provider wiring of this bundle: the managed
MCP door and the native door through the bundle-registry transport, and a
detached runtime through the Data Bus relay handler. Only bundle loading and
the read/search backends are replaced.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from connection_hub.authority_registry import CredentialEnvelope
from connection_hub.delegated_credentials.catalog.authorization import (
    ActiveCatalogCapabilities,
)
from connection_hub.delegated_credentials.catalog.models import CatalogDocument
from connection_hub.named_service_admission import native_agent_selector

from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext, bind_auth_context
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    bind_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
    managed_named_service_admission,
    native_agent_admission_from_state,
    native_agent_admission_selector,
    store_managed_named_service_admission_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.target_policy import ConversationTargetPolicy
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import relay
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.registry import (
    NamedServiceRegistry,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.transports.api_client import (
    NamedServiceEndpoint,
    call_named_service_endpoint,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceRequest,
    NamedServiceResponse,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

SERVICES_BUNDLE = "kdcube-services@1-0"
AGENT_BUNDLE = "ported-langgraph-agents@2026-07-13"
AGENT_ID = "lg-react"
AGENT_CLIENT = f"kdcube-agent:{AGENT_BUNDLE}:{AGENT_ID}"
EXTERNAL_CLIENT = "https://claude.ai/oauth/claude-code-client-metadata"
OTHER_BUNDLE = "workspace@2026-03-31-13-36"
USER = "user-1"

RESOURCE = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
REQUEST_RESOURCE = "/api/integrations/bundles/t/p/kdcube-services@1-0/public/mcp/named_services"
CARD_VERSION = "delegated_catalog_2026-08-09-09-00-00-000_a1b2c3d4e5f6"
NAMED_SERVICES = {
    "namespaces": {
        "conv": {
            "tools": {
                "list": {"operation": "object.list", "grants": ["conversations:read"]},
                "search": {"operation": "object.search", "grants": ["conversations:read"]},
                "get": {"operation": "object.get", "grants": ["conversations:read"]},
            },
        },
    },
}
CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": RESOURCE,
                    "grants": [
                        "conversations:read",
                        "conversations:read:any_user",
                    ],
                    "tools": {
                        "named_services_list": {"grants": ["conversations:read"]},
                        "named_services_search": {"grants": ["conversations:read"]},
                        "named_services_get": {"grants": ["conversations:read"]},
                    },
                    "named_services": copy.deepcopy(NAMED_SERVICES),
                },
            ],
        },
    },
}


class _Backend:
    def __init__(self) -> None:
        self.search_kwargs: dict[str, Any] = {}
        self.served_bundle_ids: list[Any] = []
        self.materialize_calls: list[tuple[str, str]] = []

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return "", []

    async def search_turn_catalog(self, **kwargs):
        return []

    async def get_turn_log(self, **kwargs):
        return {}

    async def materialize_file(self, *, fi_ref, conversation_id=""):
        self.materialize_calls.append((fi_ref, conversation_id))
        return {
            "ok": True,
            "filename": "summary.md",
            "mime": "text/markdown",
            "size": 5,
            "data": b"hello",
        }


class _ReadService:
    def __init__(self) -> None:
        self.list_requests = []
        self.fetch_requests = []

    async def list_user_conversations(self, request):
        self.list_requests.append(request)
        return [{
            "conversation_id": "c1",
            "user_id": USER,
            "title": "One",
            "turn_count": 1,
        }]

    async def fetch_conversation(self, request):
        self.fetch_requests.append(request)
        return {
            "conversation_id": "c1",
            "user_id": USER,
            "turns": [{
                "turn_id": "1",
                "artifacts": [{
                    "type": "artifact:assistant.file",
                    "data": {"payload": {
                        "filename": "summary.md",
                        "artifact_path": "conv:fi:turn_1.files/summary.md",
                    }},
                }],
            }],
        }


def _conv_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "conversations" / "named_service.py"
    )
    return module


@pytest.mark.asyncio
async def test_hosted_target_policy_uses_source_bundle_and_saved_selection(monkeypatch):
    module = _conv_module()
    policy_module = sys.modules[module.hosted_conversation_target_policy.__module__]
    seen = {}

    async def _props(redis, *, tenant, project, bundle_id):
        seen["props"] = (redis, tenant, project, bundle_id)
        return {"surfaces": {"as_consumer": {"agents": {AGENT_ID: {"tools": [{
            "kind": "named_service", "namespaces": {"conv": {"targets": [OTHER_BUNDLE]}},
        }]}}}}}

    class _Selections:
        def __init__(self, **kwargs):
            seen["store"] = kwargs

        async def get_selection(self, **kwargs):
            seen["selection"] = kwargs
            return {"disabled": {"conversation_targets": {OTHER_BUNDLE: True}}}

    monkeypatch.setattr(policy_module, "get_bundle_props", _props)
    monkeypatch.setattr(policy_module, "UserAgentSelectionStore", _Selections)
    monkeypatch.setattr(
        policy_module, "named_service_caller",
        lambda _ctx: SimpleNamespace(bundle_id=AGENT_BUNDLE, agent_id=AGENT_ID),
    )
    resolved = await module.hosted_conversation_target_policy(
        SimpleNamespace(tenant="t", project="p", user_id=USER, conversation_id="conv-now"),
        redis="redis", pg_pool="pool",
    )
    assert resolved == ConversationTargetPolicy(
        configured=(OTHER_BUNDLE,), disabled=(OTHER_BUNDLE,)
    )
    assert seen["props"] == ("redis", "t", "p", AGENT_BUNDLE)
    assert seen["selection"] == {
        "user_id": USER, "bundle_id": AGENT_BUNDLE,
        "agent_id": AGENT_ID, "conversation_id": "conv-now",
    }


@pytest.fixture
def backend() -> _Backend:
    return _Backend()


@pytest.fixture
def read_service() -> _ReadService:
    return _ReadService()


@pytest.fixture
def registry(monkeypatch, backend, read_service, tmp_path) -> NamedServiceRegistry:
    module = _conv_module()

    def _backend_for(**kwargs):
        return backend

    async def _registered(_ctx, bundle_id):
        return bundle_id in {AGENT_BUNDLE, OTHER_BUNDLE}

    monkeypatch.setattr(module, "make_conversation_search_backend", _backend_for)
    provider = module.build_conversation_named_service_provider(
        pool_factory=lambda: None,
        model_service_factory=lambda: None,
        storage_path=str(tmp_path),
        bundle_id=SERVICES_BUNDLE,
        bundle_validator=_registered,
    )
    provider._read_service_factory = lambda _ctx: read_service
    async def _policy(_ctx):
        return ConversationTargetPolicy(configured=(OTHER_BUNDLE,))

    provider._target_policy_factory = _policy
    context_factory = provider._context_factory

    def _recording_context(ns_ctx):
        backend.served_bundle_ids.append(ns_ctx.bundle_id)
        return context_factory(ns_ctx)

    provider._context_factory = _recording_context
    registry = NamedServiceRegistry()
    registry.register(provider)
    return registry


@pytest.fixture
def local_bundle_loading(monkeypatch, registry):
    """Load kdcube-services as the in-process peer call would, minus the store."""

    from kdcube_ai_app.infra.plugin import bundle_loader, bundle_store
    from kdcube_ai_app.infra.service_hub import inventory

    async def _spec(redis, *, tenant=None, project=None, bundle_id=None, override=None):
        assert bundle_id == SERVICES_BUNDLE
        return SimpleNamespace(id=SERVICES_BUNDLE, path="/bundles/kdcube-services", module=None, singleton=False)

    async def _secrets(request, *, bundle_id=None):
        return request

    async def _workflow(spec, config, **kwargs):
        return SimpleNamespace(named_services=lambda: registry), None

    async def _props(redis, *, tenant=None, project=None, bundle_id=None):
        return {}

    monkeypatch.setattr(bundle_store, "resolve_bundle_spec_from_store", _spec)
    monkeypatch.setattr(bundle_store, "get_bundle_props", _props)
    monkeypatch.setattr(inventory, "resolve_config_request_secrets", _secrets)
    monkeypatch.setattr(inventory, "create_workflow_config", lambda request: SimpleNamespace())
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", _workflow)


def _request_context(*, routing_bundle: str) -> ExternalEventPayload:
    return ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id=routing_bundle, session_id="sess-1", conversation_id="conv-now"),
        actor=ExternalEventActor(tenant_id="t", project_id="p"),
        user=ExternalEventUser(user_type="registered", user_id=USER, roles=["kdcube:role:registered"]),
    )


def _search(filters: dict | None = None) -> NamedServiceRequest:
    return NamedServiceRequest(
        operation="object.search",
        namespace="conv",
        query="supplier comparison",
        filters=dict(filters or {}),
    )


def _read_request(
    read_kind: str,
    filters: dict | None = None,
) -> NamedServiceRequest:
    if read_kind == "search":
        return _search(filters)
    payload: dict[str, Any] = {
        "operation": "object.list" if read_kind == "list" else "object.get",
        "namespace": "conv",
        "filters": dict(filters or {}),
    }
    if read_kind == "get":
        payload["object_ref"] = "conv:conversation:c1"
    elif read_kind == "file":
        payload["object_ref"] = "conv:fi:conv_c1.turn_1.files/summary.md"
    elif read_kind != "list":
        raise AssertionError(f"unknown read kind: {read_kind}")
    return NamedServiceRequest.from_dict(payload)


async def _through_bundle_registry(admission: NamedServiceAdmission, request, *, routing_bundle: str):
    comm_context = _request_context(routing_bundle=routing_bundle)
    caller = bundle_operations.make_local_bundle_named_service_caller(
        redis=None, pg_pool=None, comm_context=comm_context,
    )
    with bind_current_request_context(comm_context), bind_bundle_named_service_caller(caller):
        return await call_named_service_endpoint(
            NamedServiceEndpoint(bundle_id=SERVICES_BUNDLE, tenant="t", project="p", namespace="conv"),
            request,
            admission=admission,
        )


def _mcp_request(
    client_id: str,
    *,
    operation: str = "object.search",
    targets: tuple[str, ...] = (),
    claims: tuple[str, ...] = ("conversations:read",),
):
    grant_record = {
        "client_id": client_id,
        "registry_access_id": "oauth-5aa44826664a0bdd",
        "grantor_subject": USER,
        "delegate_subject": f"integration:{client_id}:{USER}",
        "card_revision": 3,
        "catalog_version": CARD_VERSION,
        "resource_grants": {RESOURCE: list(claims)},
        "account_scope": {},
        "named_services": copy.deepcopy(NAMED_SERVICES),
    }
    credential = CredentialEnvelope(
        subject=f"integration:{client_id}:{USER}",
        attrs={
            "client_id": client_id,
            "grantor_subject": USER,
            "grants": list(claims),
            "resource": RESOURCE,
        },
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            delegated_credential={"credential": credential.to_dict(), "grant_record": grant_record}
        )
    )
    store_managed_named_service_admission_snapshot(
        request,
        catalog=ActiveCatalogCapabilities(CatalogDocument.build(CONNECTIONS)),
        grant_record=grant_record,
        credential=credential,
        resource=RESOURCE,
        request_resource=REQUEST_RESOURCE,
        outer_operation=(
            "named_services_list"
            if operation == "object.list"
            else "named_services_get"
            if operation == "object.get"
            else "named_services_search"
        ),
        card_composition=SimpleNamespace(effective_card=SimpleNamespace(
            properties={"kdcube.conversation_targets": list(targets)}
        )),
    )
    return request


# -- managed MCP door --------------------------------------------------------


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_a_hosted_agent_searches_its_own_application(backend):
    admission = managed_named_service_admission(_mcp_request(AGENT_CLIENT))

    response = await _through_bundle_registry(admission, _search(), routing_bundle=SERVICES_BUNDLE)

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] == AGENT_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_an_external_client_has_no_implicit_conversation_target(backend):
    admission = managed_named_service_admission(_mcp_request(EXTERNAL_CLIENT))

    response = await _through_bundle_registry(admission, _search(), routing_bundle=SERVICES_BUNDLE)

    assert response.status == 403
    assert response.error.code == "conversation_target_not_granted"
    assert backend.search_kwargs == {}


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_an_external_client_can_name_only_a_granted_application(backend):
    admission = managed_named_service_admission(_mcp_request(EXTERNAL_CLIENT, targets=(OTHER_BUNDLE,)))

    response = await _through_bundle_registry(
        admission, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=SERVICES_BUNDLE,
    )

    assert response.ok, response.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_a_hosted_agent_cannot_name_an_ungranted_application(backend):
    admission = managed_named_service_admission(_mcp_request(AGENT_CLIENT))

    response = await _through_bundle_registry(
        admission, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=SERVICES_BUNDLE,
    )

    assert response.status == 403
    assert response.error.code == "conversation_target_not_granted"
    assert backend.search_kwargs == {}


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_a_hosted_agent_can_name_a_granted_application(backend):
    admission = managed_named_service_admission(_mcp_request(AGENT_CLIENT, targets=(OTHER_BUNDLE,)))

    response = await _through_bundle_registry(
        admission, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=SERVICES_BUNDLE,
    )

    assert response.ok, response.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_unknown_target_is_404_even_before_card_denial(backend):
    admission = managed_named_service_admission(_mcp_request(AGENT_CLIENT))
    response = await _through_bundle_registry(
        admission, _search({"bundle_id": "missing@1-0"}), routing_bundle=SERVICES_BUNDLE,
    )
    assert response.status == 404
    assert response.error.code == "conversation_bundle_not_found"
    assert backend.search_kwargs == {}


# -- native door ---------------------------------------------------------------


@pytest.mark.usefixtures("local_bundle_loading")
async def test_native_door_a_hosted_agent_searches_its_own_application(backend):
    selector = native_agent_admission_selector(
        source_bundle_id=AGENT_BUNDLE,
        source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT,
        grantor_user_id=USER,
    )
    admission = native_agent_admission_from_state(
        selector=selector,
        state={"granted": True, "resource": RESOURCE},
    )

    response = await _through_bundle_registry(admission, _search(), routing_bundle=AGENT_BUNDLE)

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] == AGENT_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_native_cross_bundle_read_requires_card_target(backend):
    selector = native_agent_admission_selector(
        source_bundle_id=AGENT_BUNDLE,
        source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT,
        grantor_user_id=USER,
    )
    denied = native_agent_admission_from_state(
        selector=selector,
        state={"granted": True, "resource": RESOURCE},
    )
    response = await _through_bundle_registry(
        denied, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=AGENT_BUNDLE,
    )
    assert response.status == 403
    assert backend.search_kwargs == {}

    allowed = native_agent_admission_from_state(
        selector=selector,
        state={"granted": True, "resource": RESOURCE, "conversation_targets": [OTHER_BUNDLE]},
    )
    response = await _through_bundle_registry(
        allowed, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=AGENT_BUNDLE,
    )
    assert response.ok, response.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_native_unknown_target_is_404(backend):
    selector = native_agent_admission_selector(
        source_bundle_id=AGENT_BUNDLE, source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT, grantor_user_id=USER,
    )
    admission = native_agent_admission_from_state(
        selector=selector, state={"granted": True, "resource": RESOURCE},
    )
    response = await _through_bundle_registry(
        admission, _search({"bundle_id": "missing@1-0"}), routing_bundle=AGENT_BUNDLE,
    )
    assert response.status == 404
    assert response.error.code == "conversation_bundle_not_found"
    assert backend.search_kwargs == {}


# -- Data Bus relay ------------------------------------------------------------


class _RelayRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value


async def _through_relay(
    registry,
    *,
    selector: dict,
    actor: dict,
    request: NamedServiceRequest,
    targets: tuple[str, ...] = (),
    claims: tuple[str, ...] = ("conversations:read",),
):
    message = SimpleNamespace(
        tenant="t",
        project="p",
        bundle_id=SERVICES_BUNDLE,
        message_id="nsrelay_conv_1",
        actor=actor,
        payload={"request": request.to_dict(), "admission": selector},
    )
    ctx = SimpleNamespace(bundle=SimpleNamespace(redis=_RelayRedis(), named_services=lambda: registry))
    # The Data Bus worker binds the provider bundle's own context before the handler runs.
    worker_auth = AuthContext.from_mapping(
        {
            "tenant": "t",
            "project": "p",
            "bundle_id": SERVICES_BUNDLE,
            "stream_id": "stream-1",
            "actor": dict(actor),
            "request_id": message.message_id,
        },
        source="data_bus",
    )

    async def _connection_hub(call):
        assert call.request["payload"]["client_id"] == selector["client_id"]
        return BundleNamedServiceResult(
            value=NamedServiceResponse.ok_response(object={
                "granted": True,
                "resource": RESOURCE,
                "resource_claims": list(claims),
                "conversation_targets": list(targets),
            })
        )

    with bind_auth_context(worker_auth), bind_bundle_named_service_caller(_connection_hub):
        result = await relay.handle_named_service_relay(ctx, message)
    return NamedServiceResponse.from_dict(result["data"]["response"])


def _relay_actor(*, permissions: tuple[str, ...] = ()) -> dict:
    return {
        "user_id": USER,
        "user_type": "registered",
        "roles": ["kdcube:role:registered"],
        "permissions": list(permissions),
        "identity_authority": {},
        "session_id": "sess-1",
        "source_bundle_id": AGENT_BUNDLE,
        "source_agent_id": AGENT_ID,
    }


async def test_relay_a_hosted_agent_searches_its_own_application(registry, backend):
    selector = native_agent_selector(
        source_bundle_id=AGENT_BUNDLE,
        source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT,
        grantor_user_id=USER,
    )

    response = await _through_relay(registry, selector=selector, actor=_relay_actor(), request=_search())

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] == AGENT_BUNDLE


async def test_relay_cross_bundle_read_requires_hub_card_target(registry, backend):
    selector = native_agent_selector(
        source_bundle_id=AGENT_BUNDLE,
        source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT,
        grantor_user_id=USER,
    )
    denied = await _through_relay(
        registry, selector=selector, actor=_relay_actor(),
        request=_search({"bundle_id": OTHER_BUNDLE}),
    )
    assert denied.status == 403
    assert backend.search_kwargs == {}

    allowed = await _through_relay(
        registry, selector=selector, actor=_relay_actor(),
        request=_search({"bundle_id": OTHER_BUNDLE}), targets=(OTHER_BUNDLE,),
    )
    assert allowed.ok, allowed.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


async def test_relay_unknown_target_is_404(registry, backend):
    selector = native_agent_selector(
        source_bundle_id=AGENT_BUNDLE, source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT, grantor_user_id=USER,
    )
    response = await _through_relay(
        registry, selector=selector, actor=_relay_actor(),
        request=_search({"bundle_id": "missing@1-0"}),
    )
    assert response.status == 404
    assert response.error.code == "conversation_bundle_not_found"
    assert backend.search_kwargs == {}


async def test_relay_an_application_call_searches_its_source_application(registry, backend):
    selector = NamedServiceAdmission.application(source="test.relay").relay_selector()

    response = await _through_relay(registry, selector=selector, actor=_relay_actor(), request=_search())

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] == AGENT_BUNDLE


def _native_admission(
    *,
    targets: tuple[str, ...] = (),
    claims: tuple[str, ...] = ("conversations:read",),
):
    selector = native_agent_admission_selector(
        source_bundle_id=AGENT_BUNDLE,
        source_agent_id=AGENT_ID,
        client_id=AGENT_CLIENT,
        grantor_user_id=USER,
    )
    return native_agent_admission_from_state(
        selector=selector,
        state={
            "granted": True,
            "resource": RESOURCE,
            "resource_claims": list(claims),
            "conversation_targets": list(targets),
        },
    )


async def _through_read_door(
    door: str,
    registry: NamedServiceRegistry,
    request: NamedServiceRequest,
    *,
    targets: tuple[str, ...] = (),
    claims: tuple[str, ...] = ("conversations:read",),
):
    if door == "managed_mcp":
        admission = managed_named_service_admission(
            _mcp_request(
                AGENT_CLIENT,
                operation=request.operation,
                targets=targets,
                claims=claims,
            )
        )
        return await _through_bundle_registry(
            admission,
            request,
            routing_bundle=SERVICES_BUNDLE,
        )
    if door == "native":
        return await _through_bundle_registry(
            _native_admission(targets=targets, claims=claims),
            request,
            routing_bundle=AGENT_BUNDLE,
        )
    if door == "data_bus":
        selector = native_agent_selector(
            source_bundle_id=AGENT_BUNDLE,
            source_agent_id=AGENT_ID,
            client_id=AGENT_CLIENT,
            grantor_user_id=USER,
        )
        return await _through_relay(
            registry,
            selector=selector,
            actor=_relay_actor(),
            request=request,
            targets=targets,
            claims=claims,
        )
    raise AssertionError(f"unknown read door: {door}")


def _assert_not_executed(
    read_kind: str,
    backend: _Backend,
    read_service: _ReadService,
) -> None:
    assert backend.search_kwargs == {}
    assert backend.materialize_calls == []
    assert read_service.list_requests == []
    assert read_service.fetch_requests == []


def _assert_executed_for_target(
    read_kind: str,
    target: str,
    backend: _Backend,
    read_service: _ReadService,
) -> None:
    if read_kind == "search":
        assert backend.search_kwargs["bundle_id"] == target
    elif read_kind == "list":
        assert read_service.list_requests[-1].bundle_id == target
    else:
        assert read_service.fetch_requests[-1].bundle_id == target
        if read_kind == "file":
            assert backend.materialize_calls == [
                ("conv:fi:conv_c1.turn_1.files/summary.md", "c1")
            ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_bundle_loading")
@pytest.mark.parametrize("door", ["managed_mcp", "native", "data_bus"])
@pytest.mark.parametrize("read_kind", ["search", "list", "get", "file"])
async def test_every_read_door_requires_and_honors_cross_bundle_target(
    door,
    read_kind,
    registry,
    backend,
    read_service,
):
    request = _read_request(read_kind, {"bundle_id": OTHER_BUNDLE})

    denied = await _through_read_door(door, registry, request)
    assert denied.status == 403
    assert denied.error.code == "conversation_target_not_granted"
    _assert_not_executed(read_kind, backend, read_service)

    allowed = await _through_read_door(
        door,
        registry,
        request,
        targets=(OTHER_BUNDLE,),
    )
    assert allowed.ok, allowed.error
    _assert_executed_for_target(
        read_kind,
        OTHER_BUNDLE,
        backend,
        read_service,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_bundle_loading")
@pytest.mark.parametrize("door", ["managed_mcp", "native", "data_bus"])
@pytest.mark.parametrize("read_kind", ["search", "list", "get", "file"])
async def test_every_read_door_returns_404_for_unknown_target(
    door,
    read_kind,
    registry,
    backend,
    read_service,
):
    response = await _through_read_door(
        door,
        registry,
        _read_request(read_kind, {"bundle_id": "missing@1-0"}),
    )

    assert response.status == 404
    assert response.error.code == "conversation_bundle_not_found"
    _assert_not_executed(read_kind, backend, read_service)


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_bundle_loading")
@pytest.mark.parametrize("read_kind", ["search", "list", "get", "file"])
async def test_external_mcp_client_has_no_implicit_target_for_any_read(
    read_kind,
    backend,
    read_service,
):
    request = _read_request(read_kind)
    admission = managed_named_service_admission(
        _mcp_request(EXTERNAL_CLIENT, operation=request.operation)
    )

    response = await _through_bundle_registry(
        admission,
        request,
        routing_bundle=SERVICES_BUNDLE,
    )

    assert response.status == 403
    assert response.error.code == "conversation_target_not_granted"
    _assert_not_executed(read_kind, backend, read_service)


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_bundle_loading")
@pytest.mark.parametrize("door", ["managed_mcp", "native", "data_bus"])
@pytest.mark.parametrize("read_kind", ["list", "get", "file"])
async def test_every_read_door_denies_ungranted_cross_user_scope(
    door,
    read_kind,
    registry,
    backend,
    read_service,
):
    response = await _through_read_door(
        door,
        registry,
        _read_request(
            read_kind,
            {"scope": {"mode": "user", "user_id": "other-user"}},
        ),
    )

    assert response.status == 403
    assert response.error.code == "conversation_user_not_granted"
    _assert_not_executed(read_kind, backend, read_service)


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_bundle_loading")
@pytest.mark.parametrize("door", ["managed_mcp", "native", "data_bus"])
@pytest.mark.parametrize("read_kind", ["list", "get", "file"])
async def test_every_read_door_honors_admitted_any_user_claim(
    door,
    read_kind,
    registry,
    backend,
    read_service,
):
    response = await _through_read_door(
        door,
        registry,
        _read_request(
            read_kind,
            {"scope": {"mode": "user", "user_id": "other-user"}},
        ),
        claims=("conversations:read", "conversations:read:any_user"),
    )

    assert response.ok, response.error
    _assert_executed_for_target(
        read_kind,
        AGENT_BUNDLE,
        backend,
        read_service,
    )

    after_scope = await _through_read_door(
        door,
        registry,
        _read_request(
            read_kind,
            {"scope": {"mode": "user", "user_id": "other-user"}},
        ),
    )
    assert after_scope.status == 403
    assert after_scope.error.code == "conversation_user_not_granted"

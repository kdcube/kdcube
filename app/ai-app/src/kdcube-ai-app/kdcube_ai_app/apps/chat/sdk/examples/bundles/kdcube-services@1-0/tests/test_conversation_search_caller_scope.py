# SPDX-License-Identifier: MIT

"""A conv search is scoped to the application that called, on every path.

kdcube-services serves every `conv` call, so the bundle id on the provider's
own request context is always kdcube-services. These tests drive the real
dispatch of each path into the real provider wiring of this bundle: the managed
MCP door and the native door through the bundle-registry transport, and a
detached runtime through the Data Bus relay handler. Only bundle loading and
the search backend are replaced.
"""

from __future__ import annotations

import copy
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
                "search": {"operation": "object.search", "grants": ["conversations:read"]},
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
                    "grants": ["conversations:read"],
                    "tools": {"named_services_search": {"grants": ["conversations:read"]}},
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

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return "", []

    async def search_turn_catalog(self, **kwargs):
        return []

    async def get_turn_log(self, **kwargs):
        return {}


def _conv_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "conversations" / "named_service.py"
    )
    return module


@pytest.fixture
def backend() -> _Backend:
    return _Backend()


@pytest.fixture
def registry(monkeypatch, backend, tmp_path) -> NamedServiceRegistry:
    module = _conv_module()

    def _backend_for(**kwargs):
        return backend

    monkeypatch.setattr(module, "make_conversation_search_backend", _backend_for)
    provider = module.build_conversation_named_service_provider(
        pool_factory=lambda: None,
        model_service_factory=lambda: None,
        storage_path=str(tmp_path),
        bundle_id=SERVICES_BUNDLE,
    )
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


def _mcp_request(client_id: str):
    grant_record = {
        "client_id": client_id,
        "registry_access_id": "oauth-5aa44826664a0bdd",
        "grantor_subject": USER,
        "delegate_subject": f"integration:{client_id}:{USER}",
        "card_revision": 3,
        "catalog_version": CARD_VERSION,
        "resource_grants": {RESOURCE: ["conversations:read"]},
        "account_scope": {},
        "named_services": copy.deepcopy(NAMED_SERVICES),
    }
    credential = CredentialEnvelope(
        subject=f"integration:{client_id}:{USER}",
        attrs={
            "client_id": client_id,
            "grantor_subject": USER,
            "grants": ["conversations:read"],
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
        outer_operation="named_services_search",
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
async def test_mcp_an_external_client_searches_every_conversation_of_the_user(backend):
    admission = managed_named_service_admission(_mcp_request(EXTERNAL_CLIENT))

    response = await _through_bundle_registry(admission, _search(), routing_bundle=SERVICES_BUNDLE)

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] is None


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_an_external_client_can_name_the_application(backend):
    admission = managed_named_service_admission(_mcp_request(EXTERNAL_CLIENT))

    response = await _through_bundle_registry(
        admission, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=SERVICES_BUNDLE,
    )

    assert response.ok, response.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


@pytest.mark.usefixtures("local_bundle_loading")
async def test_mcp_a_hosted_agent_naming_another_application_gets_that_application(backend):
    admission = managed_named_service_admission(_mcp_request(AGENT_CLIENT))

    response = await _through_bundle_registry(
        admission, _search({"bundle_id": OTHER_BUNDLE}), routing_bundle=SERVICES_BUNDLE,
    )

    assert response.ok, response.error
    assert backend.search_kwargs["bundle_id"] == OTHER_BUNDLE


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


# -- Data Bus relay ------------------------------------------------------------


class _RelayRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value


async def _through_relay(registry, *, selector: dict, actor: dict, request: NamedServiceRequest):
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
            value=NamedServiceResponse.ok_response(object={"granted": True, "resource": RESOURCE})
        )

    with bind_auth_context(worker_auth), bind_bundle_named_service_caller(_connection_hub):
        result = await relay.handle_named_service_relay(ctx, message)
    return NamedServiceResponse.from_dict(result["data"]["response"])


def _relay_actor() -> dict:
    return {
        "user_id": USER,
        "user_type": "registered",
        "roles": ["kdcube:role:registered"],
        "permissions": [],
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


async def test_relay_an_application_call_searches_its_source_application(registry, backend):
    selector = NamedServiceAdmission.application(source="test.relay").relay_selector()

    response = await _through_relay(registry, selector=selector, actor=_relay_actor(), request=_search())

    assert response.ok, response.error
    assert backend.served_bundle_ids == [SERVICES_BUNDLE]
    assert backend.search_kwargs["bundle_id"] == AGENT_BUNDLE

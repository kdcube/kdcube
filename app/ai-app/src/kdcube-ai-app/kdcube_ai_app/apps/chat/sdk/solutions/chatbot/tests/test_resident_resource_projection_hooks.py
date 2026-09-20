# SPDX-License-Identifier: MIT

"""Hosted workflow hooks for current resident-resource projection."""

from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control as agent_capability_control
import kdcube_ai_app.apps.chat.sdk.runtime.agent_inventory as agent_inventory_module
import kdcube_ai_app.apps.chat.sdk.runtime.resident_resources as resident_resources_module
from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources import (
    AuthoritySource,
    AvailabilityReason,
    EffectiveResidentInventory,
    EffectiveResidentRuntimeProjection,
    GatewayRequestableResource,
    GatewayRuntimeConnection,
    GatewayRuntimePlan,
    ResolvedResidentResource,
    ResolvedResidentTool,
    ResourceBinding,
)
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import AgentSkillConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


TENANT = "tenant-a"
PROJECT = "project-a"
APPLICATION = "workspace@1-0"
AGENT = "main"
USER = "user-a"
ACCESS_ID = "resident-card-1"
RESOURCE = "urn:connection-hub:remote-mcp:connector-1"
TOOL = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
SERVER = "connection_hub_delegated"
ALIAS = "connection_hub"
ENDPOINT = "https://hub.example.test/mcp/delegated_mcp_gateway"


def _bundle_props() -> dict:
    return {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    AGENT: {
                        "delegated_resource_families": [
                            {
                                "id": "user_external_mcp",
                                "resource_kinds": ["remote_mcp"],
                                "transports": ["streamable-http"],
                                "resource_patterns": [
                                    "urn:connection-hub:remote-mcp:*"
                                ],
                                "allowed_tools": ["*"],
                            }
                        ]
                    }
                }
            }
        }
    }


def _projection() -> EffectiveResidentRuntimeProjection:
    binding = ResourceBinding(
        mode="gateway",
        server_id=SERVER,
        alias=ALIAS,
        transport="streamable-http",
        endpoint=ENDPOINT,
    )
    resource = ResolvedResidentResource(
        resource_id=RESOURCE,
        resource_kind="remote_mcp",
        server_id=SERVER,
        alias=ALIAS,
        display_name="Fixture",
        authority_source=AuthoritySource.DELEGATED_CARD,
        identity_scope="grantor",
        available=True,
        reason=AvailabilityReason.AVAILABLE,
        tools=(
            ResolvedResidentTool(
                name=TOOL,
                operation="search",
                description="Search fixture records",
                input_schema={"type": "object"},
                output_schema=None,
                available=True,
                reason=AvailabilityReason.AVAILABLE,
            ),
        ),
        binding=binding,
        access_id=ACCESS_ID,
        card_revision=4,
    )
    inventory = EffectiveResidentInventory(
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        resources=(resource,),
        rejected=(),
    )
    plan = GatewayRuntimePlan(
        connections=(
            GatewayRuntimeConnection(
                server_id=SERVER,
                alias=ALIAS,
                transport="streamable-http",
                endpoint=ENDPOINT,
                access_id=ACCESS_ID,
                card_revision=4,
                identity_scope="grantor",
                resource_ids=(RESOURCE,),
                tool_names=(TOOL,),
            ),
        )
    )
    return EffectiveResidentRuntimeProjection(
        inventory=inventory,
        gateway_plan=plan,
        requestable_resources_by_access_id={
            ACCESS_ID: (
                GatewayRequestableResource(
                    resource_id="urn:connection-hub:remote-mcp:connector-2",
                    resource_kind="remote_mcp",
                    display_name="Second fixture",
                    identity_scope="grantor",
                    reason="owner_delegable",
                    recovery={"href": "/connection-hub/card"},
                ),
            )
        },
        requestable_discovery_by_access_id={ACCESS_ID: "permitted"},
    )


def _workflow_stub(*, bearer_provider=None):
    runtime_ctx = SimpleNamespace(
        tenant=TENANT,
        project=PROJECT,
        user_id=USER,
        bundle_id=APPLICATION,
        agent_id=AGENT,
        conversation_id="conversation-1",
        effective_resident_inventory="stale-inventory",
        resident_gateway_runtime_plan="stale-plan",
        resident_mcp_auth_headers_provider="stale-provider",
        resident_requestable_resources="stale-resources",
        resident_requestable_discovery="stale-discovery",
    )
    values = {
        "resident_resource_facts_loader": object(),
        "runtime_ctx": runtime_ctx,
        "config": SimpleNamespace(
            ai_bundle_spec=SimpleNamespace(id=APPLICATION),
        ),
        "bundle_props": _bundle_props(),
        "bundle_root": lambda: ".",
        "_conversation_cache_is_warm": lambda _timeline: False,
        "logger": SimpleNamespace(log=lambda *_args, **_kwargs: None),
        "pg_pool": None,
    }
    if bearer_provider is not None:
        values["resident_gateway_bearer_provider"] = bearer_provider
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _card_projection_allows_the_fixture_catalog(monkeypatch):
    """These tests isolate resident-resource binding after Card narrowing."""

    async def _sync(*_args, **_kwargs):
        return {"projection": {}}

    monkeypatch.setattr(
        agent_capability_control,
        "sync_agent_capability_projection",
        _sync,
    )
    monkeypatch.setattr(
        agent_capability_control,
        "disabled_from_projection",
        lambda _catalog, _projection: {},
    )


@pytest.mark.asyncio
async def test_turn_clears_stale_gateway_binding_when_bearer_port_is_absent(
    monkeypatch,
):
    projection = _projection()

    async def _resolve(**_kwargs):
        return projection

    monkeypatch.setattr(
        resident_resources_module,
        "resolve_current_resident_runtime_projection",
        _resolve,
    )
    monkeypatch.setattr(
        agent_inventory_module,
        "agent_capabilities_catalog",
        lambda *_args, **_kwargs: {"mcp": []},
    )
    workflow = _workflow_stub()

    tools, _skills = await BaseWorkflow.apply_user_agent_selection(
        workflow,
        AgentToolConfig(),
        AgentSkillConfig(),
    )

    assert tools.mcp_tool_specs == []
    assert workflow.runtime_ctx.effective_resident_inventory is projection.inventory
    assert workflow.runtime_ctx.resident_gateway_runtime_plan is None
    assert workflow.runtime_ctx.resident_mcp_auth_headers_provider is None
    assert workflow.runtime_ctx.resident_requestable_resources == (
        projection.requestable_resources_by_access_id
    )


@pytest.mark.asyncio
async def test_turn_binds_current_gateway_plan_without_fetching_bearer(monkeypatch):
    projection = _projection()
    bearer_calls = []

    async def _resolve(**_kwargs):
        return projection

    async def _bearer_provider(connection, subject):
        bearer_calls.append((connection, subject))
        return "turn-only-token"

    monkeypatch.setattr(
        resident_resources_module,
        "resolve_current_resident_runtime_projection",
        _resolve,
    )
    monkeypatch.setattr(
        agent_inventory_module,
        "agent_capabilities_catalog",
        lambda *_args, **_kwargs: {"mcp": []},
    )
    workflow = _workflow_stub(bearer_provider=_bearer_provider)

    tools, _skills = await BaseWorkflow.apply_user_agent_selection(
        workflow,
        AgentToolConfig(),
        AgentSkillConfig(),
    )

    assert tools.mcp_tool_specs == [
        projection.gateway_plan.connections[0].to_mcp_tool_spec()
    ]
    assert workflow.runtime_ctx.resident_gateway_runtime_plan is projection.gateway_plan
    assert callable(workflow.runtime_ctx.resident_mcp_auth_headers_provider)
    assert bearer_calls == []


@pytest.mark.asyncio
async def test_delegated_direct_mcp_is_replaced_by_the_one_card_gateway(monkeypatch):
    projection = _projection()
    seen = []

    async def _resolve(**kwargs):
        seen.append(kwargs)
        return projection

    async def _bearer_provider(_connection, _subject):
        return "turn-only-token"

    monkeypatch.setattr(
        resident_resources_module,
        "resolve_current_resident_runtime_projection",
        _resolve,
    )
    monkeypatch.setattr(
        agent_inventory_module,
        "agent_capabilities_catalog",
        lambda *_args, **_kwargs: {
            "mcp": [
                {
                    "server_id": "knowledge",
                    "alias": "knowledge",
                    "resource_id": "urn:kdcube:mcp:knowledge",
                    "authority_source": "application",
                },
                {
                    "server_id": "delegated_knowledge",
                    "alias": "delegated_knowledge",
                    "resource_id": "urn:kdcube:mcp:knowledge-delegated",
                    "authority_source": "delegated_card",
                },
            ]
        },
    )
    workflow = _workflow_stub(bearer_provider=_bearer_provider)
    configured = AgentToolConfig(
        mcp_tool_specs=[
            {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]},
            {
                "server_id": "delegated_knowledge",
                "alias": "delegated_knowledge",
                "tools": ["search"],
            },
        ],
        allowed_plugins=["knowledge", "delegated_knowledge"],
        allowed_tool_names_by_alias={
            "knowledge": None,
            "delegated_knowledge": ["search"],
        },
    )

    tools, _skills = await BaseWorkflow.apply_user_agent_selection(
        workflow,
        configured,
        AgentSkillConfig(),
    )

    assert [item["server_id"] for item in tools.mcp_tool_specs] == [
        "knowledge",
        SERVER,
    ]
    assert "delegated_knowledge" not in tools.allowed_plugins
    assert seen[0]["ceiling"].declared_resource_ids == (
        "urn:kdcube:mcp:knowledge-delegated",
    )


@pytest.mark.asyncio
async def test_capability_catalog_uses_same_current_projection(monkeypatch):
    projection = _projection()
    seen = []

    async def _resolve(**kwargs):
        seen.append(kwargs)
        return projection

    monkeypatch.setattr(
        resident_resources_module,
        "resolve_current_resident_runtime_projection",
        _resolve,
    )
    entrypoint = SimpleNamespace(
        resident_resource_facts_loader=object(),
        pg_pool=None,
        bundle_props=_bundle_props(),
        RESIDENT_RESOURCE_CATALOG_BUDGET_SECONDS=3.0,
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": USER,
            "bundle_id": APPLICATION,
        },
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )
    catalog = {"schema": "agent-capabilities.v1", "mcp": []}

    result = await BaseEntrypoint._attach_resident_resource_catalog(
        entrypoint,
        catalog,
        AGENT,
        conversation_id="conversation-1",
    )

    assert "resources" not in catalog
    assert result["resources"][0]["resource_id"] == RESOURCE
    assert result["resources"][0]["tools"][0]["name"] == TOOL
    assert result["resource_offers_by_access_id"][ACCESS_ID][0][
        "resource_id"
    ].endswith("connector-2")
    assert result["resource_discovery_by_access_id"] == {
        ACCESS_ID: "permitted"
    }
    assert len(seen) == 1
    assert seen[0]["grantor_subject"] == USER


@pytest.mark.asyncio
async def test_capability_catalog_failure_keeps_delegated_mcp_default_closed(
    monkeypatch,
):
    async def _resolve(**_kwargs):
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr(
        resident_resources_module,
        "resolve_current_resident_runtime_projection",
        _resolve,
    )
    entrypoint = SimpleNamespace(
        resident_resource_facts_loader=object(),
        pg_pool=None,
        bundle_props=_bundle_props(),
        RESIDENT_RESOURCE_CATALOG_BUDGET_SECONDS=3.0,
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": USER,
            "bundle_id": APPLICATION,
        },
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )
    catalog = {
        "schema": "agent-capabilities.v1",
        "mcp": [
            {
                "server_id": "application_docs",
                "authority_source": "application",
            },
            {
                "server_id": "knowledge_managed",
                "authority_source": "delegated_card",
            },
        ],
    }

    result = await BaseEntrypoint._attach_resident_resource_catalog(
        entrypoint,
        catalog,
        AGENT,
    )

    assert result["mcp"] == [
        {
            "server_id": "application_docs",
            "authority_source": "application",
        }
    ]


@pytest.mark.asyncio
async def test_capability_catalog_missing_identity_keeps_delegated_mcp_default_closed():
    entrypoint = SimpleNamespace(
        resident_resource_facts_loader=object(),
        pg_pool=None,
        bundle_props=_bundle_props(),
        RESIDENT_RESOURCE_CATALOG_BUDGET_SECONDS=3.0,
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": "",
            "bundle_id": APPLICATION,
        },
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )
    catalog = {
        "mcp": [
            {"server_id": "application_docs", "authority_source": "application"},
            {"server_id": "knowledge_managed", "authority_source": "delegated_card"},
        ]
    }

    result = await BaseEntrypoint._attach_resident_resource_catalog(
        entrypoint,
        catalog,
        AGENT,
    )

    assert result["mcp"] == [
        {"server_id": "application_docs", "authority_source": "application"}
    ]


@pytest.mark.asyncio
async def test_capability_catalog_import_failure_keeps_delegated_mcp_default_closed(
    monkeypatch,
):
    real_import = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "kdcube_ai_app.apps.chat.sdk.runtime.resident_resources":
            raise ImportError("resident projection unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import)
    entrypoint = SimpleNamespace(
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )
    catalog = {
        "mcp": [
            {"server_id": "application_docs", "authority_source": "application"},
            {"server_id": "knowledge_managed", "authority_source": "delegated_card"},
        ]
    }

    result = await BaseEntrypoint._attach_resident_resource_catalog(
        entrypoint,
        catalog,
        AGENT,
    )

    assert result["mcp"] == [
        {"server_id": "application_docs", "authority_source": "application"}
    ]


@pytest.mark.asyncio
async def test_capability_catalog_is_unchanged_without_host_loader():
    entrypoint = SimpleNamespace()
    catalog = {"schema": "agent-capabilities.v1", "mcp": []}

    result = await BaseEntrypoint._attach_resident_resource_catalog(
        entrypoint,
        catalog,
        AGENT,
    )

    assert result is catalog

# SPDX-License-Identifier: MIT

"""One effective resident inventory drives every maintained MCP adapter."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_mcp import (
    resolve_mcp_server_map,
)
from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources import (
    AuthoritySource,
    AvailabilityReason,
    EffectiveResidentInventory,
    GatewayRuntimeConnection,
    GatewayRuntimeHeadersProvider,
    GatewayRuntimePlan,
    ResidentRuntimeBindingError,
    ResidentToolDescriptor,
    ResolvedResidentResource,
    ResolvedResidentTool,
    ResourceBinding,
    apply_gateway_runtime_connections,
    bind_gateway_runtime_context,
    delegated_mcp_bindings_from_catalog,
    gateway_connection_descriptors,
    gateway_runtime_connections,
    gateway_runtime_plan,
    gateway_services_config,
    gateway_tool_overrides,
    merge_gateway_services_config,
    remove_direct_delegated_mcp_bindings,
)
from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_adapter import (
    MCPServerSpec,
    MCPToolSchema,
)
from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import (
    MCPToolsSubsystem,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.solutions.foreign_runtime.mcp_bridge import (
    claude_code_mcp_servers,
    claude_code_tool_rules,
)

TENANT = "tenant-a"
PROJECT = "project-a"
APPLICATION = "workspace@1-0"
AGENT = "main"
ACCESS = "resident-card-1"
GATEWAY_SERVER = "connection_hub_delegated"
GATEWAY_ALIAS = "connection_hub"
ENDPOINT = "https://hub.example.test/mcp/delegated_mcp_gateway"


def _binding(*, server_id: str = GATEWAY_SERVER) -> ResourceBinding:
    return ResourceBinding(
        mode="gateway",
        server_id=server_id,
        alias=GATEWAY_ALIAS,
        transport="streamable-http",
        endpoint=ENDPOINT,
    )


def _tool(
    name: str,
    operation: str,
    *,
    available: bool = True,
    reason: AvailabilityReason = AvailabilityReason.AVAILABLE,
) -> ResolvedResidentTool:
    return ResolvedResidentTool(
        name=name,
        operation=operation,
        description=operation,
        input_schema={"type": "object"},
        output_schema=None,
        available=available,
        reason=reason,
    )


def _resource(
    resource_id: str,
    *tools: ResolvedResidentTool,
    source: AuthoritySource = AuthoritySource.DELEGATED_CARD,
    access_id: str = ACCESS,
    reason: AvailabilityReason = AvailabilityReason.AVAILABLE,
    binding: ResourceBinding | None = None,
) -> ResolvedResidentResource:
    return ResolvedResidentResource(
        resource_id=resource_id,
        resource_kind=("remote_mcp" if source is AuthoritySource.DELEGATED_CARD else "kdcube_mcp"),
        server_id=(binding or _binding()).server_id,
        alias=(binding or _binding()).alias,
        display_name=resource_id,
        authority_source=source,
        identity_scope="grantor",
        available=reason is AvailabilityReason.AVAILABLE,
        reason=reason,
        tools=tuple(tools),
        binding=binding or _binding(),
        access_id=access_id,
        card_revision=4 if access_id else 0,
    )


def _inventory(*resources: ResolvedResidentResource) -> EffectiveResidentInventory:
    return EffectiveResidentInventory(
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        resources=tuple(resources),
        rejected=(),
    )


def _meta() -> ResidentToolDescriptor:
    return ResidentToolDescriptor(
        name="connection_hub_access_describe",
        operation="connection_hub_access_describe",
        description="Describe current delegated access.",
        input_schema={"type": "object"},
    )


def test_one_card_collapses_multiple_resources_and_keeps_direct_mcp_separate():
    search = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    read = "ch_remote_mcp_cccccccccccccccc__read_dddddddddddddddd"
    denied = "ch_remote_mcp_cccccccccccccccc__delete_eeeeeeeeeeeeeeee"
    direct_binding = ResourceBinding(
        mode="direct",
        server_id="knowledge",
        alias="knowledge",
        transport="streamable-http",
        endpoint="https://runtime.example.test/mcp/knowledge",
    )
    inventory = _inventory(
        _resource("urn:external:a", _tool(search, "search")),
        _resource(
            "urn:external:b",
            _tool(read, "read"),
            _tool(
                denied,
                "delete",
                available=False,
                reason=AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED,
            ),
        ),
        _resource(
            "urn:kdcube:mcp:knowledge",
            _tool("knowledge_search", "search"),
            source=AuthoritySource.APPLICATION,
            access_id="",
            binding=direct_binding,
        ),
    )

    connections = gateway_runtime_connections(
        inventory,
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )

    assert len(connections) == 1
    connection = connections[0]
    assert connection.resource_ids == ("urn:external:a", "urn:external:b")
    assert connection.tool_names == tuple(
        sorted(("connection_hub_access_describe", read, search))
    )
    assert denied not in connection.tool_names
    assert "knowledge_search" not in connection.tool_names


def test_runtime_projection_adds_one_native_spec_without_changing_static_tools():
    dynamic_name = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    connection = gateway_runtime_connections(
        _inventory(_resource("urn:external:a", _tool(dynamic_name, "search"))),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )[0]
    base = AgentToolConfig(
        tool_specs=[{"alias": "io", "module": "example.io"}],
        mcp_tool_specs=[
            {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]}
        ],
        tool_runtime={"io.run": "subprocess"},
        tool_traits={"io.run": {"strategy": ["exploitation"]}},
        allowed_plugins=["io", "knowledge"],
        allowed_tool_names_by_alias={"io": ["run"], "knowledge": None},
    )

    projected = apply_gateway_runtime_connections(base, (connection,))

    assert base.mcp_tool_specs == [
        {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]}
    ]
    assert projected.mcp_tool_specs[-1] == connection.to_mcp_tool_spec()
    assert projected.allowed_tool_names_by_alias[GATEWAY_ALIAS] == list(
        connection.tool_names
    )
    assert projected.allowed_plugins[-1] == GATEWAY_ALIAS
    assert projected.tool_specs == base.tool_specs
    assert projected.tool_runtime["io.run"] == "subprocess"
    assert {
        key: value
        for key, value in projected.tool_runtime.items()
        if key.startswith(f"mcp.{GATEWAY_ALIAS}.")
    } == {
        f"mcp.{GATEWAY_ALIAS}.{name}": "none"
        for name in connection.tool_names
    }


def test_card_governed_static_mcp_moves_off_the_direct_runtime_path():
    catalog = {
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
    }
    resources, servers, aliases = delegated_mcp_bindings_from_catalog(catalog)
    assert resources == ("urn:kdcube:mcp:knowledge-delegated",)
    assert servers == ("delegated_knowledge",)
    assert aliases == ("delegated_knowledge",)

    config = AgentToolConfig(
        mcp_tool_specs=[
            {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]},
            {
                "server_id": "delegated_knowledge",
                "alias": "delegated_knowledge",
                "tools": ["search"],
            },
        ],
        tool_runtime={
            "mcp.knowledge.search": "none",
            "mcp.delegated_knowledge.search": "none",
        },
        tool_traits={
            "mcp.knowledge.*": {"strategy": ["exploration"]},
            "mcp.delegated_knowledge.*": {"strategy": ["exploration"]},
        },
        allowed_plugins=["knowledge", "delegated_knowledge"],
        allowed_tool_names_by_alias={
            "knowledge": None,
            "delegated_knowledge": ["search"],
        },
        tool_claim_policies=[
            SimpleNamespace(tool_name="mcp.knowledge.search"),
            SimpleNamespace(tool_name="mcp.delegated_knowledge.search"),
        ],
    )

    result = remove_direct_delegated_mcp_bindings(
        config,
        server_ids=servers,
        aliases=aliases,
    )

    assert [row["server_id"] for row in result.mcp_tool_specs] == ["knowledge"]
    assert result.allowed_plugins == ["knowledge"]
    assert result.allowed_tool_names_by_alias == {"knowledge": None}
    assert set(result.tool_runtime) == {"mcp.knowledge.search"}
    assert set(result.tool_traits) == {"mcp.knowledge.*"}
    assert [policy.tool_name for policy in result.tool_claim_policies] == [
        "mcp.knowledge.search"
    ]


def test_same_neutral_connection_drives_langgraph_and_claude_code_server_map():
    dynamic_name = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    connection = gateway_runtime_connections(
        _inventory(_resource("urn:external:a", _tool(dynamic_name, "search"))),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )[0]
    descriptors = gateway_connection_descriptors((connection,))
    seen = []

    async def bearer_provider(row, user_sub):
        seen.append((dict(row), user_sub))
        return "bounded-card-bearer"

    server_map = asyncio.run(
        resolve_mcp_server_map(
            descriptors,
            user_sub="user-a",
            client_id="kdcube-agent:workspace@1-0:main",
            bearer_provider=bearer_provider,
        )
    )

    assert list(server_map) == [GATEWAY_SERVER]
    assert server_map[GATEWAY_SERVER]["url"] == ENDPOINT
    assert server_map[GATEWAY_SERVER]["transport"] == "streamable-http"
    assert server_map[GATEWAY_SERVER]["headers"] == {
        "Authorization": "Bearer bounded-card-bearer"
    }
    assert seen[0][0]["access_id"] == ACCESS
    assert seen[0][0]["resources"] == ["urn:external:a"]

    claude = claude_code_mcp_servers(server_map)
    assert claude[GATEWAY_SERVER] == {
        "type": "http",
        "url": ENDPOINT,
        "headers": {"Authorization": "Bearer bounded-card-bearer"},
    }
    allowed, denied = claude_code_tool_rules(
        descriptors,
        server_ids=tuple(server_map),
        tool_overrides=gateway_tool_overrides((connection,)),
    )
    assert allowed == tuple(
        f"mcp__{GATEWAY_SERVER}__{name}" for name in connection.tool_names
    )
    assert denied == ()


def test_revoked_card_and_unavailable_operation_never_enter_runtime_allowlist():
    tool_name = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    revoked = _resource(
        "urn:external:a",
        _tool(
            tool_name,
            "search",
            available=False,
            reason=AvailabilityReason.CARD_REVOKED,
        ),
        reason=AvailabilityReason.CARD_REVOKED,
    )
    assert gateway_runtime_connections(_inventory(revoked)) == ()

    unavailable = _resource(
        "urn:external:a",
        _tool(
            tool_name,
            "search",
            available=False,
            reason=AvailabilityReason.RESOURCE_PROVIDER_UNAVAILABLE,
        ),
        reason=AvailabilityReason.RESOURCE_PROVIDER_UNAVAILABLE,
    )
    connection = gateway_runtime_connections(
        _inventory(unavailable),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )[0]
    assert connection.tool_names == ("connection_hub_access_describe",)


def test_two_cards_cannot_reuse_one_runtime_server_id():
    tool_a = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    tool_b = "ch_remote_mcp_cccccccccccccccc__read_dddddddddddddddd"
    second = replace(
        _resource("urn:external:b", _tool(tool_b, "read")),
        access_id="resident-card-2",
        identity_scope="selected_identities",
    )
    with pytest.raises(
        ResidentRuntimeBindingError, match="gateway_runtime_server_id_collision"
    ):
        gateway_runtime_connections(
            _inventory(
                _resource("urn:external:a", _tool(tool_a, "search")),
                second,
            )
        )


def test_static_server_or_alias_collision_fails_closed():
    connection = GatewayRuntimeConnection(
        server_id=GATEWAY_SERVER,
        alias=GATEWAY_ALIAS,
        transport="streamable-http",
        endpoint=ENDPOINT,
        access_id=ACCESS,
        card_revision=4,
        identity_scope="grantor",
        resource_ids=("urn:external:a",),
        tool_names=("connection_hub_access_describe",),
    )
    with pytest.raises(
        ResidentRuntimeBindingError, match="gateway_runtime_server_id_collision"
    ):
        apply_gateway_runtime_connections(
            AgentToolConfig(
                mcp_tool_specs=[
                    {
                        "server_id": GATEWAY_SERVER,
                        "alias": "other",
                        "tools": ["*"],
                    }
                ]
            ),
            (connection,),
        )


def test_credential_free_runtime_projection_contains_no_token_value():
    connection = gateway_runtime_connections(
        _inventory(
            _resource(
                "urn:external:a",
                _tool(
                    "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb",
                    "search",
                ),
            )
        ),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )[0]
    rendered = repr((connection, gateway_connection_descriptors((connection,)))).lower()
    assert "access_token" not in rendered
    assert "authorization" not in rendered
    assert "bearer" not in rendered


def test_one_runtime_plan_drives_native_config_services_and_foreign_overrides():
    dynamic_name = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    plan = gateway_runtime_plan(
        _inventory(_resource("urn:external:a", _tool(dynamic_name, "search"))),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )
    assert isinstance(plan, GatewayRuntimePlan)
    assert len(plan.connections) == 1

    native = plan.native_tool_config(
        AgentToolConfig(
            mcp_tool_specs=[
                {"server_id": "knowledge", "alias": "knowledge", "tools": ["*"]}
            ],
            allowed_plugins=["knowledge"],
            allowed_tool_names_by_alias={"knowledge": None},
        )
    )
    services = plan.native_services_config(
        {
            "mcpServers": {
                "knowledge": {
                    "transport": "http",
                    "url": "https://runtime.example/mcp/knowledge",
                }
            }
        }
    )

    assert [row["server_id"] for row in native.mcp_tool_specs] == [
        "knowledge",
        GATEWAY_SERVER,
    ]
    assert services["mcpServers"][GATEWAY_SERVER] == {
        "transport": "streamable-http",
        "url": ENDPOINT,
        "auth": {"type": "trusted_process"},
        "ttl_seconds": 0,
    }
    assert plan.connection_descriptors() == gateway_connection_descriptors(
        plan.connections
    )
    assert plan.tool_overrides() == {GATEWAY_SERVER: plan.connections[0].tool_names}


def test_runtime_headers_are_resolved_on_demand_for_exact_access_id():
    plan = gateway_runtime_plan(
        _inventory(
            _resource(
                "urn:external:a",
                _tool(
                    "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb",
                    "search",
                ),
            )
        ),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )
    calls = []

    async def _bearer_provider(connection, user_subject):
        calls.append((dict(connection), user_subject))
        return "short-lived-card-token"

    provider = plan.auth_headers_provider(
        user_subject="user-a",
        bearer_provider=_bearer_provider,
    )
    assert isinstance(provider, GatewayRuntimeHeadersProvider)
    assert calls == []
    assert "short-lived-card-token" not in repr(provider)

    headers = asyncio.run(provider(GATEWAY_SERVER))

    assert headers == {"Authorization": "Bearer short-lived-card-token"}
    assert calls == [
        (
            {
                "kind": "mcp",
                "server_id": GATEWAY_SERVER,
                "alias": GATEWAY_ALIAS,
                "url": ENDPOINT,
                "transport": "streamable-http",
                "delegated": True,
                "resource": "urn:external:a",
                    "resources": ["urn:external:a"],
                    "access_id": ACCESS,
                    "card_revision": 4,
                    "identity_scope": "grantor",
                "allowed": list(plan.connections[0].tool_names),
            },
            "user-a",
        )
    ]


def test_runtime_header_provider_collapses_secret_bearing_errors():
    connection = GatewayRuntimeConnection(
        server_id=GATEWAY_SERVER,
        alias=GATEWAY_ALIAS,
        transport="streamable-http",
        endpoint=ENDPOINT,
        access_id=ACCESS,
        card_revision=4,
        identity_scope="grantor",
        resource_ids=("urn:external:a",),
        tool_names=("connection_hub_access_describe",),
    )
    marker = "token-from-failed-provider"

    async def _failing_provider(_connection, _user_subject):
        raise RuntimeError(marker)

    provider = GatewayRuntimeHeadersProvider(
        (connection,),
        user_subject="user-a",
        bearer_provider=_failing_provider,
    )
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(provider(GATEWAY_SERVER))
    assert str(caught.value) == "gateway_runtime_authorization_unavailable"
    assert marker not in str(caught.value)


def test_native_mcp_binding_executes_with_header_but_exports_no_bearer():
    dynamic_name = "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb"
    plan = gateway_runtime_plan(
        _inventory(_resource("urn:external:a", _tool(dynamic_name, "search"))),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )
    seen = []

    async def _bearer_provider(connection, user_subject):
        seen.append((connection["access_id"], user_subject))
        return "runtime-only-token"

    headers_provider = plan.auth_headers_provider(
        user_subject="user-a",
        bearer_provider=_bearer_provider,
    )

    class _Adapter:
        def __init__(self, server: MCPServerSpec):
            self.server = server

        async def list_tools(self):
            assert self.server.auth_headers_provider is headers_provider
            assert await self.server.auth_headers_provider(self.server.server_id) == {
                "Authorization": "Bearer runtime-only-token"
            }
            return [
                MCPToolSchema(
                    id=name,
                    name=name,
                    description=name,
                    params_schema={"type": "object"},
                )
                for name in plan.connections[0].tool_names
            ]

        async def call_tool(self, tool_id, params, *, trace_id=None):
            assert await self.server.auth_headers_provider(self.server.server_id) == {
                "Authorization": "Bearer runtime-only-token"
            }
            return {"ok": True, "tool": tool_id, "trace_id": trace_id}

    subsystem = MCPToolsSubsystem(
        bundle_id=APPLICATION,
        mcp_tool_specs=[plan.connections[0].to_mcp_tool_spec()],
        adapter_factory=_Adapter,
        cache=None,
        services_config=plan.native_services_config(),
        auth_headers_provider=headers_provider,
    )
    subsystem.cache = None

    async def _exercise():
        entries = await subsystem.build_tool_entries()
        called = await subsystem.call_tool(
            alias=GATEWAY_ALIAS,
            tool_id=dynamic_name,
            params={"query": "test"},
            trace_id="invocation-1",
        )
        return entries, called

    entries, called = asyncio.run(_exercise())
    assert {entry["id"] for entry in entries} == {
        f"mcp.{GATEWAY_ALIAS}.{name}" for name in plan.connections[0].tool_names
    }
    assert called == {"ok": True, "tool": dynamic_name, "trace_id": "invocation-1"}
    assert seen == [(ACCESS, "user-a"), (ACCESS, "user-a")]

    manager = ToolSubsystem(
        service=None,
        comm=None,
        logger=None,
        bundle_spec=None,
        context_rag_client=None,
        mcp_subsystem=subsystem,
        tool_runtime=plan.native_tool_config(AgentToolConfig()).tool_runtime,
    )
    exported = manager.export_runtime_globals()
    rendered = json.dumps(exported, sort_keys=True)
    assert "runtime-only-token" not in rendered
    assert "Authorization" not in rendered
    assert exported["MCP_SERVICES"] == gateway_services_config(plan.connections)


def test_gateway_service_merge_rejects_static_server_collision():
    connection = GatewayRuntimeConnection(
        server_id=GATEWAY_SERVER,
        alias=GATEWAY_ALIAS,
        transport="streamable-http",
        endpoint=ENDPOINT,
        access_id=ACCESS,
        card_revision=4,
        identity_scope="grantor",
        resource_ids=("urn:external:a",),
        tool_names=("connection_hub_access_describe",),
    )
    with pytest.raises(
        ResidentRuntimeBindingError,
        match="gateway_runtime_server_id_collision",
    ):
        merge_gateway_services_config(
            {"mcpServers": {GATEWAY_SERVER: {"url": "https://other.example/mcp"}}},
            (connection,),
        )


def test_turn_context_binding_feeds_react_service_resolution_without_fetching_token():
    plan = gateway_runtime_plan(
        _inventory(
            _resource(
                "urn:external:a",
                _tool(
                    "ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb",
                    "search",
                ),
            )
        ),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )
    calls = []

    async def _bearer_provider(connection, user_subject):
        calls.append((connection, user_subject))
        return "turn-token"

    runtime_ctx = SimpleNamespace()
    bind_gateway_runtime_context(
        runtime_ctx,
        plan,
        user_subject="user-a",
        bearer_provider=_bearer_provider,
    )
    assert calls == []

    workflow = object.__new__(BaseWorkflow)
    workflow.runtime_ctx = runtime_ctx
    workflow.bundle_props = {
        "surfaces": {
            "as_consumer": {
                "mcp": {
                    "services": {
                        "mcpServers": {
                            "knowledge": {
                                "transport": "http",
                                "url": "https://runtime.example/mcp/knowledge",
                            }
                        }
                    }
                }
            }
        }
    }
    workflow.logger = SimpleNamespace(log=lambda *_args, **_kwargs: None)

    resolved = workflow._resolve_mcp_services_config()

    assert set(resolved["mcpServers"]) == {"knowledge", GATEWAY_SERVER}
    assert resolved["mcpServers"][GATEWAY_SERVER]["auth"] == {
        "type": "trusted_process"
    }
    assert calls == []


def _w40_plan():
    return gateway_runtime_plan(
        _inventory(_resource("urn:external:a", _tool("ch_remote_mcp_aaaaaaaaaaaaaaaa__search_bbbbbbbbbbbbbbbb",
                                                     "search"))),
        meta_tools_by_access_id={ACCESS: (_meta(),)},
    )


def test_every_gateway_call_carries_this_turns_token():
    async def _bearer(connection, user_subject):
        return "card-token"

    async def _turn():
        return "ts1.turn.token"

    provider = _w40_plan().auth_headers_provider(user_subject="user-a", bearer_provider=_bearer,
                                                 turn_token_provider=_turn)
    headers = asyncio.run(provider(GATEWAY_SERVER))
    assert headers == {"Authorization": "Bearer card-token", "x-connection-hub-turn": "ts1.turn.token"}
    assert "ts1.turn.token" not in repr(provider)


def test_a_lost_turn_token_refuses_the_call_instead_of_using_the_card_defaults():
    async def _bearer(connection, user_subject):
        return "card-token"

    async def _lost():
        raise RuntimeError("hub unreachable")

    provider = _w40_plan().auth_headers_provider(user_subject="user-a", bearer_provider=_bearer,
                                                 turn_token_provider=_lost)
    with pytest.raises(RuntimeError, match="gateway_runtime_turn_token_unavailable"):
        asyncio.run(provider(GATEWAY_SERVER))


def test_the_turn_token_is_renewed_before_it_lapses(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.runtime import agent_capability_control as control

    clock, calls = {"now": 0.0}, []

    async def _renew(entrypoint, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "turn_token": "ts1.renewed"}

    monkeypatch.setattr(control, "request_agent_turn_token", _renew)
    provider = control.TurnTokenProvider(object(), agent_id="main", conversation_id="c1", turn_id="t1",
                                         token="ts1.first", clock=lambda: clock["now"])
    assert asyncio.run(provider()) == "ts1.first" and calls == []
    clock["now"] = 481.0  # within two minutes of the ten-minute lifetime
    assert asyncio.run(provider()) == "ts1.renewed"
    assert calls == [{"agent_id": "main", "conversation_id": "c1", "turn_id": "t1", "turn_token": "ts1.first"}]


def test_an_automation_run_is_recognised_from_its_call_context():
    from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import turn_is_automation_execution
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context

    assert turn_is_automation_execution() is False
    with bind_current_bundle_call_context({"kind": "automation_execution"}):
        assert turn_is_automation_execution() is True
    with bind_current_bundle_call_context({"kind": "chat"}):
        assert turn_is_automation_execution() is False

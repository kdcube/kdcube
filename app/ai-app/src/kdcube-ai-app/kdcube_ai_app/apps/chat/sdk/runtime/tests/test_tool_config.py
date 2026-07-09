from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import agent_tool_config_from_bundle_props
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import ToolSubsystem


def test_agent_tool_config_translates_named_service_operations_to_agent_tools() -> None:
    cfg = agent_tool_config_from_bundle_props(
        {
            "tools": {
                "agents": {
                    "default_agent": [
                        {
                            "kind": "named_service",
                            "alias": "named_services",
                            "namespaces": {
                                "task": {
                                    "allowed_operations": [
                                        "provider.about",
                                        "object.get",
                                        "object.schema",
                                        "object.upsert",
                                        "object.delete",
                                        "object.action",
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        },
        "default_agent",
    )

    assert cfg.allowed_plugins == ["named_services"]
    assert cfg.tool_specs == [
        {
            "module": "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools",
            "alias": "named_services",
            "use_sk": False,
        }
    ]
    assert cfg.allowed_tool_names_by_alias["named_services"] == [
        "provider_about",
        "get_object",
        "object_schema",
        "upsert_object",
        "delete_object",
        "object_action",
    ]


def test_agent_tool_config_reads_as_consumer_surface_tools() -> None:
    cfg = agent_tool_config_from_bundle_props(
        {
            "surfaces": {
                "as_consumer": {
                    "agents": {
                        "main": {
                            "tools": [
                                {
                                    "kind": "named_service",
                                    "alias": "named_services",
                                    "namespaces": {
                                        "task": {
                                            "allowed": [
                                                "provider.about",
                                                "object.search",
                                                "object.schema",
                                                "object.upsert",
                                                "object.host_file",
                                                "object.delete",
                                            ],
                                        },
                                    },
                                    "tool_traits": {
                                        "search_objects": {"strategy": ["exploration"]},
                                        "upsert_object": {"strategy": ["exploitation"]},
                                        "host_file": {"strategy": ["exploitation"]},
                                    },
                                },
                            ],
                        },
                    },
                },
            },
        },
        "default.react.agent",
    )

    assert cfg.allowed_plugins == ["named_services"]
    assert cfg.allowed_tool_names_by_alias["named_services"] == [
        "provider_about",
        "search_objects",
        "object_schema",
        "upsert_object",
        "host_file",
        "delete_object",
    ]
    assert "get_object" not in cfg.allowed_tool_names_by_alias["named_services"]
    assert cfg.tool_traits == {
        "named_services.search_objects": {"strategy": ["exploration"]},
        "named_services.upsert_object": {"strategy": ["exploitation"]},
        "named_services.host_file": {"strategy": ["exploitation"]},
    }


def test_no_namespace_with_object_get_drops_get_object_from_the_roster() -> None:
    """The surfaced case: mail and slack consumed as named-service namespaces,
    reading riding react.pull — with object.get in NO namespace's allowed list
    (declared as an intentional exclusion instead), the get_object grammar
    tool must not be advertised to ReAct at all."""
    cfg = agent_tool_config_from_bundle_props(
        {
            "surfaces": {
                "as_consumer": {
                    "agents": {
                        "main": {
                            "tools": [
                                {
                                    "kind": "named_service",
                                    "alias": "named_services",
                                    "namespaces": {
                                        "mail": {
                                            "allowed": [
                                                "provider.about",
                                                "object.list",
                                                "object.search",
                                                "object.schema",
                                                "object.action",
                                            ],
                                            "excluded": {
                                                "object.get": {
                                                    "reason": "Reading rides the context tools — the agent pulls mail refs directly.",
                                                    "agent_hint": "Pull the ref with react.pull; read the materialized artifact with react.read.",
                                                },
                                            },
                                        },
                                        "slack": {
                                            "allowed": [
                                                "provider.about",
                                                "object.list",
                                                "object.search",
                                                "object.schema",
                                                "object.action",
                                            ],
                                            "excluded": {
                                                "object.get": {
                                                    "reason": "Reading rides the context tools — the agent pulls slack refs directly.",
                                                    "agent_hint": "Pull the ref with react.pull; read the materialized artifact with react.read.",
                                                },
                                            },
                                        },
                                    },
                                },
                            ],
                        },
                    },
                },
            },
        },
        "main",
    )

    advertised = cfg.allowed_tool_names_by_alias["named_services"]
    assert "get_object" not in advertised
    assert advertised == [
        "provider_about",
        "list_objects",
        "search_objects",
        "object_schema",
        "object_action",
    ]


def test_agent_tool_config_reads_tool_connected_account_claims() -> None:
    cfg = agent_tool_config_from_bundle_props(
        {
            "surfaces": {
                "as_consumer": {
                    "agents": {
                        "main": {
                            "tools": [
                                {
                                    "kind": "python",
                                    "alias": "report",
                                    "module": "demo.report_tools",
                                    "allowed": ["post_to_slack"],
                                    "tool_claims": {
                                        "post_to_slack": {
                                            "connections": {
                                                "delegated_to_kdcube": {
                                                    "connected_accounts": [
                                                        {
                                                            "provider_id": "slack",
                                                            "connector_app_id": "demo",
                                                            "claims": ["slack:post"],
                                                        }
                                                    ]
                                                }
                                            }
                                        }
                                    },
                                },
                            ],
                        },
                    },
                },
            },
        },
        "main",
    )

    assert len(cfg.tool_claim_policies) == 1
    policy = cfg.tool_claim_policies[0]
    assert policy.tool_name == "report.post_to_slack"
    assert policy.connected_accounts[0].provider_id == "slack"
    assert policy.connected_accounts[0].connector_app_id == "demo"
    assert policy.connected_accounts[0].claims == ("slack:post",)


def test_alias_tool_filter_keeps_exact_agent_surface() -> None:
    allowed = {
        "named_services": ["get_object"],
        "knowledge": None,
    }

    assert ToolSubsystem._entry_allowed_by_alias_names(
        {"id": "named_services.get_object", "plugin_alias": "named_services"},
        allowed,
    )
    assert not ToolSubsystem._entry_allowed_by_alias_names(
        {"id": "named_services.object_action", "plugin_alias": "named_services"},
        allowed,
    )
    assert ToolSubsystem._entry_allowed_by_alias_names(
        {"id": "mcp.knowledge.search", "plugin_alias": "knowledge"},
        allowed,
    )

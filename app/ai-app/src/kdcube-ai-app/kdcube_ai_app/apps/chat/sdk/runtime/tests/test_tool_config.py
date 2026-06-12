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
                                                "object.delete",
                                            ],
                                        },
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
        "delete_object",
    ]
    assert "get_object" not in cfg.allowed_tool_names_by_alias["named_services"]


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

# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.agent_capability_policy import (
    AGENT_CAPABILITY_POLICY_SCHEMA,
    CAPABILITY_ALLOWED_SELECTED,
    CAPABILITY_ALLOWED_UNSELECTED,
    CAPABILITY_NOT_ALLOWED,
)
from connection_hub.delegated_credentials.application_resources import (
    ApplicationResource,
    application_resource,
)

from kdcube_ai_app.apps.chat.sdk.runtime.agent_capability_control import (
    CONTROL_OVERRIDE_SCHEMA,
    CONTROL_OVERRIDES_PROPERTY,
    annotate_capability_states,
    capability_preferences_from_projection,
    conversation_capability_projections,
    deny_all_capabilities,
    descriptor_capability_payload,
    disabled_from_projection,
    missing_control_capabilities,
    selected_capabilities_from_disabled,
    sync_agent_capability_projection,
    unavailable_capability_states,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceResult,
    bind_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceResponse,
)


TENANT = "tenant-a"
PROJECT = "project-a"
APPLICATION = "problem-board@1-0"
AGENT = "worker"
NAMED_SERVICES_RESOURCE = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"


def _catalog(*, include_future: bool = False) -> dict:
    tools = [
        {"name": "System", "alias": "io_tools", "system": True, "tools": [{"name": "tool_call"}]},
        {
            "name": "Web",
            "alias": "web",
            "system": False,
            "tools": [
                {"name": "search", "description": "Search records."},
                *([{"name": "future", "description": "A newly published tool."}] if include_future else []),
            ],
        },
    ]
    return {
        "agent": AGENT,
        "tools": tools,
        "mcp": [],
        "named_services": [
            {
                "namespace": "work",
                "alias": "named_services",
                "operations": ["object.search", "object.action.review.accept"],
                "realm": {
                    "label": "Problem Board",
                    "operations": [
                        {"name": "object.search", "description": "Search work."},
                        {
                            "name": "object.delete",
                            "enabled_for_agent": False,
                            "description": "Delete work.",
                        },
                    ],
                    "actions": [
                        {"name": "review.accept", "description": "Accept reviewed work."},
                    ],
                },
            }
        ],
        "resources": [],
        "skills": [{"id": "work.review"}],
        "supported_models": [
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "label": "Sonnet 4.6",
            },
            {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "label": "Haiku 4.5",
            },
        ],
        "default_model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        },
        "instruction_profiles": {
            "default": "full",
            "options": [
                {"id": "full", "label": "Full"},
                {"id": "compact", "label": "Compact"},
            ],
        },
        "conversation_targets": [{"bundle_id": "workspace@1-0"}],
        "delegated_resource_families": [{"id": "user_external_mcp"}],
        "subagents": {"available": True, "default_on": True},
    }


def _props() -> dict:
    return {
        "delegated_catalog": {
            "version": "1",
            "named_service_namespaces": [
                {
                    "resource": NAMED_SERVICES_RESOURCE,
                    "namespaces": {
                        "work": {
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "grants": ["named_services:use", "work:observe"],
                                },
                                "action": {
                                    "operation": "object.action",
                                    "operations": {
                                        "object.action.review.accept": {
                                            "grants": ["named_services:use", "work:review"],
                                        }
                                    },
                                },
                            }
                        }
                    },
                }
            ],
        }
    }


def _override_props() -> dict:
    resource = application_resource(
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent=AGENT,
    )
    props = _props()
    props[CONTROL_OVERRIDES_PROPERTY] = {
        AGENT: [{
            "schema": CONTROL_OVERRIDE_SCHEMA,
            "capability_defaults": {
                "schema": AGENT_CAPABILITY_POLICY_SCHEMA,
                "resource": resource,
                "capabilities": {
                    "tools": ["web/search", "web/not-declared"],
                    "models": ["anthropic/claude-haiku-4-5"],
                    "instruction_profiles": ["compact"],
                    "resource_families": ["user_external_mcp"],
                },
            },
            "resource_grants": {
                NAMED_SERVICES_RESOURCE: [
                    "named_services:use",
                    "work:observe",
                ],
            },
            "resource_operations": {
                NAMED_SERVICES_RESOURCE: ["named_services_search"],
            },
            "named_service_operations": {
                NAMED_SERVICES_RESOURCE: {
                    "work": ["object.search"],
                },
            },
            "properties": {
                "kdcube.application_operations": {
                    "schema": "connection_hub.application_operations.v1",
                    "operations": ["work.search"],
                },
                "kdcube.conversation_targets": [
                    application_resource(
                        tenant=TENANT,
                        project=PROJECT,
                        application="workspace@1-0",
                        agent="*",
                    ),
                    application_resource(
                        tenant=TENANT,
                        project=PROJECT,
                        application="not-declared@1-0",
                        agent="*",
                    ),
                ],
                "not.persisted": True,
            },
        }],
    }
    return props


def _payload(*, include_future: bool = False) -> dict:
    return descriptor_capability_payload(
        bundle_props=_props(),
        catalog=_catalog(include_future=include_future),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )


def test_descriptor_projection_separates_authority_from_metadata() -> None:
    payload = _payload()
    authority = payload["capability_authority"]
    catalog = payload["capability_catalog"]

    assert authority["schema"] == AGENT_CAPABILITY_POLICY_SCHEMA
    assert authority["resource"] == application_resource(
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent=AGENT,
    )
    assert authority["capabilities"]["tool_groups"] == ["web"]
    assert all("io_tools" not in value for value in authority["capabilities"].values())
    assert "work/object.delete" not in authority["capabilities"]["named_service_operations"]
    assert "work/object.delete" in catalog["capabilities"]["named_service_operations"]
    assert payload["capability_metadata"]["entries"]["tools"]["web/search"] == {
        "description": "Search records.",
        "title": "search",
    }
    assert authority["capabilities"]["models"] == [
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-sonnet-4-6",
    ]
    assert authority["capabilities"]["instruction_profiles"] == [
        "compact",
        "full",
    ]
    assert "entries" not in authority


def test_agent_card_starts_with_the_descriptor_model_and_instruction_defaults() -> None:
    catalog = _catalog()
    authority = _payload()["capability_authority"]

    selected = selected_capabilities_from_disabled(
        authority=authority,
        catalog=catalog,
        disabled={},
    )

    assert selected["capabilities"]["models"] == [
        "anthropic/claude-sonnet-4-6"
    ]
    assert selected["capabilities"]["instruction_profiles"] == ["full"]
    assert capability_preferences_from_projection(catalog, selected) == {
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        },
        "instructions": "full",
    }


def test_operation_to_grant_mapping_is_projected_from_the_owner_descriptor() -> None:
    payload = _payload()

    assert payload["resource_grants"] == {
        NAMED_SERVICES_RESOURCE: [
            "named_services:use",
            "work:observe",
            "work:review",
        ]
    }
    assert payload["named_service_operations"] == {
        NAMED_SERVICES_RESOURCE: {
            "work": ["object.action.review.accept", "object.search"]
        }
    }


def test_consumer_descriptor_carries_standard_card_requests_without_provider_catalog() -> None:
    catalog = _catalog()
    catalog["mcp"] = [
        {
            "server_id": "knowledge",
            "delegated": True,
            "resource": "*/api/integrations/bundles/*/*/knowledge@1-0/public/mcp/knowledge_managed*",
            "claims": ["knowledge:read"],
            "tools": ["*"],
        }
    ]

    payload = descriptor_capability_payload(
        bundle_props={},
        catalog=catalog,
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )

    assert payload["resource_grants"] == {}
    assert payload["descriptor_payload"]["standard_authority"] == {
        "resources": [
            {
                "server_id": "knowledge",
                "resource": "*/api/integrations/bundles/*/*/knowledge@1-0/public/mcp/knowledge_managed*",
                "grants": ["knowledge:read"],
                "operations": ["*"],
            }
        ],
        "named_services": [
            {
                "namespace": "work",
                "operations": ["object.action.review.accept", "object.search"],
            }
        ],
        "resource_families": [{"id": "user_external_mcp"}],
    }


def test_control_override_is_descriptor_owned_and_bounded_to_native_authority() -> None:
    baseline = _payload()
    payload = descriptor_capability_payload(
        bundle_props=_override_props(),
        catalog=_catalog(),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )

    assert payload["descriptor_revision"] != baseline["descriptor_revision"]
    assert payload["descriptor_payload"]["standard_authority_overridden"] is True
    defaults = payload["capability_defaults"]["capabilities"]
    assert defaults["instruction_profiles"] == ["compact"]
    assert defaults["models"] == ["anthropic/claude-haiku-4-5"]
    assert defaults["resource_families"] == ["user_external_mcp"]
    assert defaults["tools"] == ["web/search"]
    assert "web/not-declared" not in defaults["tools"]
    assert payload["resource_grants"] == {
        NAMED_SERVICES_RESOURCE: ["named_services:use", "work:observe"],
    }
    assert payload["resource_operations"] == {
        NAMED_SERVICES_RESOURCE: ["named_services_search"],
    }
    assert payload["named_service_operations"] == {
        NAMED_SERVICES_RESOURCE: {"work": ["object.search"]},
    }
    assert payload["properties"] == {
        "kdcube.application_operations": {
            "schema": "connection_hub.application_operations.v1",
            "operations": ["work.search"],
        },
        "kdcube.conversation_targets": [
            application_resource(
                tenant=TENANT,
                project=PROJECT,
                application="workspace@1-0",
                agent="*",
            ),
        ],
    }


def test_resource_family_metadata_carries_custom_connector_limits() -> None:
    catalog = _catalog()
    catalog["delegated_resource_families"] = [{
        "id": "user_external_mcp",
        "label": "My MCP connectors",
        "description": "User-selected MCP servers.",
        "resource_kinds": ["mcp"],
        "authority_sources": ["user"],
        "transports": ["streamable-http"],
        "resource_patterns": ["urn:connection-hub:mcp:user:*"],
        "allowed_tools": ["search", "read"],
        "max_resources": 3,
        "max_tools_per_resource": 8,
    }]

    payload = descriptor_capability_payload(
        bundle_props=_props(),
        catalog=catalog,
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )

    assert payload["capability_metadata"]["entries"]["resource_families"][
        "user_external_mcp"
    ] == {
        "title": "My MCP connectors",
        "description": "User-selected MCP servers.",
        "resource_kinds": ["mcp"],
        "authority_sources": ["user"],
        "transports": ["streamable-http"],
        "resource_patterns": ["urn:connection-hub:mcp:user:*"],
        "allowed_tools": ["search", "read"],
        "max_resources": 3,
        "max_tools_per_resource": 8,
    }


def test_metadata_changes_do_not_change_the_descriptor_revision() -> None:
    first_catalog = _catalog()
    second_catalog = _catalog()
    second_catalog["tools"][1]["tools"][0]["description"] = "Changed presentation only."

    first = descriptor_capability_payload(
        bundle_props=_props(), catalog=first_catalog,
        tenant=TENANT, project=PROJECT, application=APPLICATION, agent_id=AGENT,
    )
    second = descriptor_capability_payload(
        bundle_props=_props(), catalog=second_catalog,
        tenant=TENANT, project=PROJECT, application=APPLICATION, agent_id=AGENT,
    )

    assert first["descriptor_revision"] == second["descriptor_revision"]
    assert first["capability_metadata"] != second["capability_metadata"]


def test_new_descriptor_capability_is_unselected_until_the_user_adds_it() -> None:
    original = _payload()
    selected = selected_capabilities_from_disabled(
        authority=original["capability_authority"],
        catalog=_catalog(),
        disabled={},
    )
    expanded = _payload(include_future=True)

    disabled = disabled_from_projection(_catalog(include_future=True), selected)

    assert disabled["tools"] == {"web": ["future"]}
    assert "web/future" in expanded["capability_authority"]["capabilities"]["tools"]


def test_conversation_selection_starts_from_agent_card_and_only_control_is_ceiling() -> None:
    original_catalog = _catalog()
    original_authority = _payload()["capability_authority"]
    started_with = selected_capabilities_from_disabled(
        authority=original_authority,
        catalog=original_catalog,
        disabled={},
    )
    stored = {
        "base_projection": started_with,
        "projection": started_with,
    }

    expanded_catalog = _catalog(include_future=True)
    expanded_control = _payload(include_future=True)["capability_authority"]
    expanded_default = selected_capabilities_from_disabled(
        authority=expanded_control,
        catalog=expanded_catalog,
        disabled={},
    )
    inherited, effective = conversation_capability_projections(
        expanded_control,
        expanded_default,
        stored,
    )

    assert disabled_from_projection(expanded_catalog, inherited)["tools"] == {
        "web": ["future"]
    }
    assert disabled_from_projection(expanded_catalog, effective)["tools"] == {
        "web": ["future"]
    }

    selected_future = {
        **stored,
        "projection": expanded_default,
    }
    _inherited, expanded_effective = conversation_capability_projections(
        expanded_control,
        expanded_default,
        selected_future,
    )
    expanded_disabled = disabled_from_projection(
        expanded_catalog,
        expanded_effective,
    )
    assert "future" not in expanded_disabled.get("tools", {}).get("web", [])

    revoked_control = selected_capabilities_from_disabled(
        authority=original_authority,
        catalog=original_catalog,
        disabled={"tools": {"web": ["search"]}},
    )
    _inherited, revoked_effective = conversation_capability_projections(
        revoked_control,
        started_with,
        stored,
    )
    assert disabled_from_projection(original_catalog, revoked_effective)["tools"] == {
        "web": ["search"]
    }


def test_saved_agent_card_values_removed_from_control_are_named_as_missing() -> None:
    current_control = _payload()["capability_authority"]
    prior_selection = _payload(include_future=True)["capability_authority"]

    assert missing_control_capabilities(
        {
            "authority": current_control,
            "selection": prior_selection,
        }
    ) == [
        {
            "category": "tools",
            "capability": "web/future",
            "reason": "missing_from_control_card",
        }
    ]


def test_user_selection_round_trips_through_the_positive_card_policy() -> None:
    catalog = _catalog()
    payload = _payload()
    requested_disabled = {
        "tools": {"web": ["search"]},
        "named_services": {"work": ["object.action.review.accept"]},
        "skills": ["work.review"],
        "conversation_targets": {"workspace@1-0": True},
        "subagents": True,
    }

    selection = selected_capabilities_from_disabled(
        authority=payload["capability_authority"],
        catalog=catalog,
        disabled=requested_disabled,
    )
    effective = disabled_from_projection(catalog, selection)

    assert effective["tools"] == {"web": ["search"]}
    assert effective["named_services"] == {
        "work": ["object.action.review.accept", "object.delete"]
    }
    assert effective["skills"] == ["work.review"]
    assert effective["conversation_targets"] == {"workspace@1-0": True}
    assert effective["subagents"] is True


def test_three_state_annotation_keeps_not_allowed_distinct_from_unselected() -> None:
    catalog = _catalog()
    catalog["resources"] = [
        {
            "resource_id": "mcp://example/resource-a",
            "tools": [{"operation": "read"}],
        }
    ]
    target = application_resource(
        tenant=TENANT,
        project=PROJECT,
        application="workspace@1-0",
        agent="*",
    )
    states = {
        "tool_groups": {"web": CAPABILITY_ALLOWED_SELECTED},
        "tools": {"web/search": CAPABILITY_ALLOWED_UNSELECTED},
        "named_services": {"work": CAPABILITY_ALLOWED_SELECTED},
        "named_service_operations": {
            "work/object.search": CAPABILITY_ALLOWED_SELECTED,
            "work/object.delete": CAPABILITY_NOT_ALLOWED,
        },
        "resources": {"mcp://example/resource-a": CAPABILITY_ALLOWED_SELECTED},
        "resource_operations": {
            "mcp%3A%2F%2Fexample%2Fresource-a/read": CAPABILITY_NOT_ALLOWED,
        },
        "skills": {"work.review": CAPABILITY_ALLOWED_UNSELECTED},
        "models": {
            "anthropic/claude-sonnet-4-6": CAPABILITY_ALLOWED_SELECTED,
            "anthropic/claude-haiku-4-5": CAPABILITY_ALLOWED_UNSELECTED,
        },
        "instruction_profiles": {
            "full": CAPABILITY_ALLOWED_SELECTED,
            "compact": CAPABILITY_ALLOWED_UNSELECTED,
        },
        "conversation_targets": {target: CAPABILITY_NOT_ALLOWED},
        "resource_families": {"user_external_mcp": CAPABILITY_ALLOWED_SELECTED},
        "subagents": {"enabled": CAPABILITY_ALLOWED_UNSELECTED},
    }

    annotated = annotate_capability_states(catalog, states)

    assert annotated["tools"][1]["authority_state"] == CAPABILITY_ALLOWED_SELECTED
    assert annotated["tools"][1]["tools"][0]["authority_state"] == (
        CAPABILITY_ALLOWED_UNSELECTED
    )
    assert annotated["named_services"][0]["operation_authority_states"] == {
        "object.action.review.accept": "",
        "object.delete": CAPABILITY_NOT_ALLOWED,
        "object.search": CAPABILITY_ALLOWED_SELECTED,
    }
    assert annotated["named_services"][0]["realm"]["operations"][1][
        "authority_state"
    ] == CAPABILITY_NOT_ALLOWED
    assert annotated["resources"][0]["authority_state"] == CAPABILITY_ALLOWED_SELECTED
    assert annotated["resources"][0]["tools"][0]["authority_state"] == (
        CAPABILITY_NOT_ALLOWED
    )
    assert annotated["skills"][0]["authority_state"] == CAPABILITY_ALLOWED_UNSELECTED
    assert annotated["supported_models"][0]["authority_state"] == (
        CAPABILITY_ALLOWED_SELECTED
    )
    assert annotated["supported_models"][1]["authority_state"] == (
        CAPABILITY_ALLOWED_UNSELECTED
    )
    assert annotated["instruction_profiles"]["options"][0]["authority_state"] == (
        CAPABILITY_ALLOWED_SELECTED
    )
    assert annotated["conversation_targets"][0]["authority_state"] == (
        CAPABILITY_NOT_ALLOWED
    )
    assert annotated["delegated_resource_families"][0]["authority_state"] == (
        CAPABILITY_ALLOWED_SELECTED
    )
    assert annotated["subagents"]["authority_state"] == CAPABILITY_ALLOWED_UNSELECTED
    assert annotated["capability_states"] == states


def test_unavailable_state_marks_every_descriptor_capability_not_allowed() -> None:
    catalog = _catalog()

    states = unavailable_capability_states(
        catalog,
        tenant=TENANT,
        project=PROJECT,
    )
    annotated = annotate_capability_states(catalog, states)

    assert states
    assert all(
        state == CAPABILITY_NOT_ALLOWED
        for category in states.values()
        for state in category.values()
    )
    assert annotated["tools"][1]["authority_state"] == CAPABILITY_NOT_ALLOWED
    assert annotated["skills"][0]["authority_state"] == CAPABILITY_NOT_ALLOWED
    assert annotated["supported_models"][0]["authority_state"] == CAPABILITY_NOT_ALLOWED
    assert annotated["instruction_profiles"]["options"][0]["authority_state"] == (
        CAPABILITY_NOT_ALLOWED
    )
    assert annotated["conversation_targets"][0]["authority_state"] == (
        CAPABILITY_NOT_ALLOWED
    )
    assert annotated["subagents"]["authority_state"] == CAPABILITY_NOT_ALLOWED


def test_unavailable_projection_closes_every_selectable_capability() -> None:
    denied = deny_all_capabilities(
        catalog=_catalog(),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )

    assert denied["tools"] == {"web": True}
    assert denied["named_services"] == {"work": True}
    assert denied["skills"] == ["work.review"]
    assert denied["conversation_targets"] == {"workspace@1-0": True}
    assert denied["subagents"] is True


def test_selected_subagents_only_emit_false_to_override_default_off() -> None:
    selected = _payload()["capability_authority"]

    default_on = disabled_from_projection(_catalog(), selected)
    assert "subagents" not in default_on

    catalog = _catalog()
    catalog["subagents"]["default_on"] = False
    default_off = disabled_from_projection(catalog, selected)
    assert default_off["subagents"] is False


def test_broad_target_stays_inside_one_deployment_and_ownership_stays_separate() -> None:
    target = application_resource(
        tenant=TENANT,
        project=PROJECT,
        application="*",
        agent="*",
    )
    parsed = ApplicationResource.parse(target)
    catalog = _catalog()
    catalog["conversation_targets"] = [{"resource": target}]
    payload = descriptor_capability_payload(
        bundle_props=_props(), catalog=catalog,
        tenant=TENANT, project=PROJECT, application=APPLICATION, agent_id=AGENT,
    )

    assert parsed.matches(ApplicationResource(TENANT, PROJECT, "workspace@1-0", "worker"))
    assert not parsed.matches(ApplicationResource(TENANT, "other-project", "workspace@1-0", "worker"))
    assert payload["conversation_target_resources"] == [target]
    assert disabled_from_projection(
        catalog,
        {
            "schema": AGENT_CAPABILITY_POLICY_SCHEMA,
            "resource": payload["capability_authority"]["resource"],
            "capabilities": {},
        },
    )["conversation_targets"] == {target: True}
    # Target scope says where calls may go. The provider resource remains
    # independent and resolves its own grantor/identity-scope policy.
    assert NAMED_SERVICES_RESOURCE != target


@pytest.mark.asyncio
async def test_sync_uses_the_local_trusted_connection_hub_operation() -> None:
    calls = []
    owner = SimpleNamespace(
        bundle_props=_props(),
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": "user-a",
            "bundle_id": APPLICATION,
        },
    )

    async def _call(call):
        calls.append(call)
        payload = call.request["payload"]
        selection = payload["selected_capabilities"]
        return BundleNamedServiceResult(
            value=NamedServiceResponse.ok_response(
                object={
                    "ok": True,
                    "authority": payload["capability_authority"],
                    "selection": selection,
                    "projection": selection,
                    "states": {},
                }
            )
        )

    with bind_bundle_named_service_caller(_call):
        result = await sync_agent_capability_projection(
            owner,
            catalog=_catalog(),
            agent_id=AGENT,
            initial_disabled={"skills": ["work.review"]},
        )

    assert len(calls) == 1
    assert calls[0].bundle_id == "connection-hub@1-0"
    assert calls[0].request["namespace"] == "connections"
    assert calls[0].request["operation"] == "agent_capability.sync"
    wire_payload = calls[0].request["payload"]
    assert wire_payload["selected_resource_grants"] == {
        NAMED_SERVICES_RESOURCE: [
            "named_services:use",
            "work:observe",
            "work:review",
        ]
    }
    assert wire_payload["selected_named_service_operations"] == {
        NAMED_SERVICES_RESOURCE: {
            "work": ["object.action.review.accept", "object.search"],
        }
    }
    assert result["projection"]["capabilities"]["skills"] == []


@pytest.mark.asyncio
async def test_sync_without_a_saved_preference_uses_the_descriptor_default() -> None:
    calls = []
    owner = SimpleNamespace(
        bundle_props=_props(),
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": "user-a",
            "bundle_id": APPLICATION,
        },
    )

    async def _call(call):
        calls.append(call)
        payload = call.request["payload"]
        selection = payload["selected_capabilities"]
        return BundleNamedServiceResult(
            value=NamedServiceResponse.ok_response(
                object={
                    "ok": True,
                    "authority": payload["capability_authority"],
                    "selection": selection,
                    "projection": selection,
                    "states": {},
                }
            )
        )

    with bind_bundle_named_service_caller(_call):
        result = await sync_agent_capability_projection(
            owner,
            catalog=_catalog(),
            agent_id=AGENT,
        )

    payload = calls[0].request["payload"]
    assert payload["selected_capabilities"] != payload["capability_authority"]
    assert payload["selected_capabilities"]["capabilities"]["models"] == [
        "anthropic/claude-sonnet-4-6"
    ]
    assert payload["selected_capabilities"]["capabilities"][
        "instruction_profiles"
    ] == ["full"]
    assert result["projection"] == payload["selected_capabilities"]


@pytest.mark.asyncio
async def test_sync_rebuilds_agent_defaults_from_the_saved_control_override() -> None:
    calls = []
    owner = SimpleNamespace(
        bundle_props=_override_props(),
        _agent_selection_identity=lambda: {
            "tenant": TENANT,
            "project": PROJECT,
            "user_id": "user-a",
            "bundle_id": APPLICATION,
        },
    )

    async def _call(call):
        calls.append(call)
        payload = call.request["payload"]
        selection = payload["selected_capabilities"]
        return BundleNamedServiceResult(
            value=NamedServiceResponse.ok_response(
                object={
                    "ok": True,
                    "authority": payload["capability_authority"],
                    "selection": selection,
                    "projection": selection,
                    "states": {},
                }
            )
        )

    with bind_bundle_named_service_caller(_call):
        result = await sync_agent_capability_projection(
            owner,
            catalog=_catalog(),
            agent_id=AGENT,
        )

    payload = calls[0].request["payload"]
    assert payload["selected_capabilities"] == payload["capability_defaults"]
    assert payload["selected_resource_grants"] == payload["resource_grants"]
    assert payload["selected_resource_operations"] == payload["resource_operations"]
    assert (
        payload["selected_named_service_operations"]
        == payload["named_service_operations"]
    )
    assert result["projection"] == payload["capability_defaults"]


def _namespace_operation_catalog() -> dict:
    return {
        "agent": AGENT,
        "tools": [],
        "mcp": [],
        "named_services": [
            {
                "namespace": "slack",
                "alias": "named_services",
                "operations": ["object.list", "object.action"],
                "realm": {
                    "label": "Slack",
                    "operations": [{"name": "object.list"}],
                    "actions": [
                        {"name": "post_message", "description": "Post a message."},
                        {"name": "upload_file", "description": "Upload a file."},
                    ],
                },
            }
        ],
        "resources": [],
        "skills": [],
        "conversation_targets": [],
        "delegated_resource_families": [],
        "subagents": {"available": False},
    }


def _namespace_operation_props() -> dict:
    return {
        "delegated_catalog": {
            "version": "1",
            "named_service_namespaces": [
                {
                    "resource": NAMED_SERVICES_RESOURCE,
                    "namespaces": {
                        "slack": {
                            "tools": {
                                "list": {
                                    "operation": "object.list",
                                    "grants": ["named_services:use"],
                                },
                                "action": {
                                    "operation": "object.action",
                                    "operations": {
                                        "object.action.post_message": {
                                            "grants": [
                                                "named_services:use",
                                                "slack:post",
                                            ],
                                        },
                                        "object.action.upload_file": {
                                            "grants": [
                                                "named_services:use",
                                                "slack:files:write",
                                            ],
                                        },
                                    },
                                },
                            }
                        }
                    },
                }
            ],
        }
    }


def test_granting_a_namespace_operation_grants_the_actions_reached_through_it() -> None:
    payload = descriptor_capability_payload(
        bundle_props=_namespace_operation_props(),
        catalog=_namespace_operation_catalog(),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )
    authority = payload["capability_authority"]["capabilities"][
        "named_service_operations"
    ]
    catalog = payload["capability_catalog"]["capabilities"][
        "named_service_operations"
    ]

    assert "slack/object.action.post_message" in authority
    assert "slack/object.action.upload_file" in authority
    assert set(catalog) <= set(authority)


def test_a_granted_action_carries_the_claims_that_action_needs() -> None:
    payload = descriptor_capability_payload(
        bundle_props=_namespace_operation_props(),
        catalog=_namespace_operation_catalog(),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
    )

    assert payload["resource_grants"][NAMED_SERVICES_RESOURCE] == [
        "named_services:use",
        "slack:files:write",
        "slack:post",
    ]
    assert payload["named_service_operations"][NAMED_SERVICES_RESOURCE]["slack"] == [
        "object.action.post_message",
        "object.action.upload_file",
        "object.list",
    ]

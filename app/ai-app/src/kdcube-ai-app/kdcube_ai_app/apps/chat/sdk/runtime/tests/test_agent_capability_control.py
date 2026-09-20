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
    annotate_capability_states,
    deny_all_capabilities,
    descriptor_capability_payload,
    disabled_from_projection,
    selected_capabilities_from_disabled,
    sync_agent_capability_projection,
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
    assert "entries" not in authority


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
    states = {
        "tool_groups": {"web": CAPABILITY_ALLOWED_SELECTED},
        "tools": {"web/search": CAPABILITY_ALLOWED_UNSELECTED},
        "named_services": {"work": CAPABILITY_ALLOWED_SELECTED},
        "named_service_operations": {
            "work/object.search": CAPABILITY_ALLOWED_SELECTED,
            "work/object.delete": CAPABILITY_NOT_ALLOWED,
        },
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
    assert annotated["capability_states"] == states


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
    assert result["projection"]["capabilities"]["skills"] == []

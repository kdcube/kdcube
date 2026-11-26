# SPDX-License-Identifier: MIT

"""Resident resource projection: every authority layer narrows, none widens."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources import (
    AuthoritySource,
    AvailabilityReason,
    ConnectedAccountStatus,
    ConversationNarrowing,
    CurrentResidentResourceFacts,
    DelegatedCardSnapshot,
    DelegatedResourceGrant,
    InvocationPolicyState,
    ResidentAgentCeiling,
    ResidentResourceCandidate,
    ResidentResourceDescriptorError,
    ResidentToolDescriptor,
    ResourceBinding,
    ResourceFamilyCeiling,
    attach_effective_resource_catalog,
    conversation_narrowing_from_selection,
    resident_agent_ceiling_from_bundle_props,
    resolve_current_resident_resources,
    resolve_effective_resident_resources,
)


TENANT = "tenant-a"
PROJECT = "project-a"
APPLICATION = "workspace@1-0"
AGENT = "main"
USER = "user-a"
NOW = 1_800_000_000
EXTERNAL_1 = "urn:connection-hub:remote-mcp:connector-1"
EXTERNAL_2 = "urn:connection-hub:remote-mcp:connector-2"
KNOWLEDGE = "urn:kdcube:mcp:knowledge"
MANAGED_KNOWLEDGE = "urn:kdcube:mcp:knowledge-managed"


def _tool(name: str) -> ResidentToolDescriptor:
    return ResidentToolDescriptor(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
    )


def _binding(
    server_id: str,
    *,
    mode: str = "gateway",
    endpoint: str = "https://hub.example.test/mcp/delegated",
) -> ResourceBinding:
    return ResourceBinding(
        mode=mode,
        server_id=server_id,
        alias=server_id,
        transport="streamable-http",
        endpoint=endpoint,
    )


def _external(
    resource_id: str = EXTERNAL_1,
    *,
    tools: tuple[str, ...] = ("search", "delete"),
    user: str = USER,
    application: str = APPLICATION,
    agent_id: str = AGENT,
    identity_scope: str = "grantor",
    enabled: bool = True,
    credential_ready: bool = True,
    descriptor_accepted: bool = True,
    provider_revision: str = "provider-1",
    unavailable_reason: AvailabilityReason = AvailabilityReason.AVAILABLE,
    tool_status=None,
) -> ResidentResourceCandidate:
    server_id = resource_id.rsplit(":", 1)[-1]
    return ResidentResourceCandidate(
        resource_id=resource_id,
        resource_kind="remote_mcp",
        server_id=server_id,
        alias=server_id,
        display_name=f"External {server_id}",
        authority_source=AuthoritySource.DELEGATED_CARD,
        tools=tuple(_tool(name) for name in tools),
        binding=_binding("connection_hub"),
        tenant=TENANT,
        project=PROJECT,
        application=application,
        agent_id=agent_id,
        provider_endpoint="https://mcp.vendor.example/tools",
        grantor_subject=user,
        identity_scope=identity_scope,
        family_id="user_external_mcp",
        required_claims=("external_mcp:use",),
        descriptor_revision="descriptor-1",
        provider_revision=provider_revision,
        unavailable_reason=unavailable_reason,
        tool_status=tool_status or {},
        enabled=enabled,
        credential_ready=credential_ready,
        descriptor_accepted=descriptor_accepted,
        recovery={"operation": "external_mcp_connector_edit"},
    )


def _knowledge(*, delegated: bool = False) -> ResidentResourceCandidate:
    resource_id = MANAGED_KNOWLEDGE if delegated else KNOWLEDGE
    source = AuthoritySource.DELEGATED_CARD if delegated else AuthoritySource.APPLICATION
    return ResidentResourceCandidate(
        resource_id=resource_id,
        resource_kind="catalog" if delegated else "kdcube_mcp",
        server_id="knowledge_managed" if delegated else "knowledge",
        alias="knowledge",
        display_name="Knowledge",
        authority_source=source,
        tools=(_tool("search"), _tool("read")),
        binding=_binding(
            "connection_hub" if delegated else "knowledge",
            mode="gateway" if delegated else "direct",
            endpoint=(
                "https://hub.example.test/mcp/delegated"
                if delegated
                else "https://runtime.example.test/mcp/knowledge"
            ),
        ),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        grantor_subject=USER if delegated else "",
        required_claims=("knowledge:read",) if delegated else (),
        descriptor_revision="descriptor-1",
        provider_revision="knowledge-1",
    )


def _family(*, max_resources: int = 8, max_tools: int = 64) -> ResourceFamilyCeiling:
    return ResourceFamilyCeiling(
        family_id="user_external_mcp",
        resource_kinds=("remote_mcp",),
        authority_sources=(AuthoritySource.DELEGATED_CARD,),
        transports=("streamable-http",),
        resource_patterns=("urn:connection-hub:remote-mcp:*",),
        allowed_tool_patterns=("search", "delete"),
        endpoint_schemes=("https",),
        endpoint_hosts=("*.example", "*.example.test"),
        max_resources=max_resources,
        max_tools_per_resource=max_tools,
    )


def _ceiling(
    *,
    declared: tuple[str, ...] = (KNOWLEDGE,),
    families: tuple[ResourceFamilyCeiling, ...] | None = None,
) -> ResidentAgentCeiling:
    return ResidentAgentCeiling(
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        declared_resource_ids=declared,
        resource_families=(_family(),) if families is None else families,
        descriptor_revision="descriptor-1",
    )


def _card(
    *grants: DelegatedResourceGrant,
    active: bool = True,
    expires_at: int | None = NOW + 3600,
    user: str = USER,
    application: str = APPLICATION,
    agent_id: str = AGENT,
    identity_scope: str = "grantor",
    access_id: str = "resident-card-1",
    revision: int = 3,
) -> DelegatedCardSnapshot:
    return DelegatedCardSnapshot(
        access_id=access_id,
        client_id=f"kdcube-agent:{APPLICATION}:{AGENT}",
        revision=revision,
        tenant=TENANT,
        project=PROJECT,
        grantor_subject=user,
        application=application,
        agent_id=agent_id,
        identity_scope=identity_scope,
        source="resident",
        resources=tuple(grants),
        active=active,
        expires_at_epoch=expires_at,
    )


def _grant(
    resource_id: str = EXTERNAL_1,
    *,
    operations: tuple[str, ...] = ("search",),
    claims: tuple[str, ...] = ("external_mcp:use",),
    policies=None,
    resource_state: AvailabilityReason = AvailabilityReason.AVAILABLE,
    operation_states=None,
    operation_recovery=None,
    operation_accepted_digests=None,
    operation_current_digests=None,
    identity_scope: str = "grantor",
    resource_kind: str | None = None,
) -> DelegatedResourceGrant:
    if resource_kind is None:
        resource_kind = (
            "catalog" if resource_id == MANAGED_KNOWLEDGE else "remote_mcp"
        )
    return DelegatedResourceGrant(
        resource_id=resource_id,
        resource_kind=resource_kind,
        identity_scope=identity_scope,
        claims=claims,
        operations=operations,
        invocation_policies=policies or {},
        resource_state=resource_state,
        operation_states=operation_states or {},
        operation_recovery=operation_recovery or {},
        operation_accepted_digests=operation_accepted_digests or {},
        operation_current_digests=operation_current_digests or {},
    )


def _resolve(
    candidates,
    *,
    ceiling=None,
    card=None,
    conversation=None,
    user=USER,
):
    return resolve_effective_resident_resources(
        ceiling=ceiling or _ceiling(),
        grantor_subject=user,
        candidates=candidates,
        card=card,
        conversation=conversation,
        now_epoch=NOW,
    )


def test_descriptor_parses_a_bounded_dynamic_family_without_connector_ids():
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {
                    "main": {
                        "delegated_resource_families": [
                            {
                                "id": "user_external_mcp",
                                "resource_kinds": ["remote_mcp"],
                                "transports": ["streamable_http"],
                                "resource_patterns": ["urn:connection-hub:remote-mcp:*"],
                                "allowed_tools": ["search_*", "read"],
                                "endpoint_schemes": ["https"],
                                "endpoint_hosts": ["*.example"],
                                "max_resources": 4,
                                "max_tools_per_resource": 20,
                            }
                        ]
                    }
                }
            }
        }
    }
    ceiling = resident_agent_ceiling_from_bundle_props(
        props,
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        declared_resource_ids=(KNOWLEDGE,),
        descriptor_revision="rev-7",
    )
    assert ceiling.declared_resource_ids == (KNOWLEDGE,)
    family = ceiling.resource_families[0]
    assert family.authority_sources == (AuthoritySource.DELEGATED_CARD,)
    assert family.transports == ("streamable-http",)
    assert family.max_resources == 4
    assert EXTERNAL_1 not in repr(ceiling)


@pytest.mark.parametrize(
    "family,error",
    [
        ({}, "resource_family_id_required"),
        ("not-a-mapping", "delegated_resource_family_must_be_mapping"),
        ({"id": "x", "resource_kinds": ["remote_mcp"]}, "resource_family_transports_required:x"),
        (
            {"id": "x", "resource_kinds": ["remote_mcp"], "transports": ["http"]},
            "resource_family_patterns_required:x",
        ),
    ],
)
def test_descriptor_rejects_ambiguous_resource_family(family, error):
    props = {
        "surfaces": {
            "as_consumer": {
                "agents": {"main": {"delegated_resource_families": [family]}}
            }
        }
    }
    with pytest.raises(ResidentResourceDescriptorError, match=error):
        resident_agent_ceiling_from_bundle_props(
            props,
            tenant=TENANT,
            project=PROJECT,
            application=APPLICATION,
            agent_id=AGENT,
        )


def test_dynamic_family_ceiling_never_falls_back_from_an_unknown_agent_to_main():
    props = {
        "surfaces": {
            "as_consumer": {
                "default_agent": "main",
                "agents": {
                    "main": {
                        "delegated_resource_families": [
                            {
                                "id": "user_external_mcp",
                                "resource_kinds": ["remote_mcp"],
                                "transports": ["streamable-http"],
                                "resource_patterns": [
                                    "urn:connection-hub:remote-mcp:*"
                                ],
                            }
                        ]
                    }
                },
            }
        }
    }
    ceiling = resident_agent_ceiling_from_bundle_props(
        props,
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id="other",
    )
    assert ceiling.resource_families == ()


def test_descriptor_ceiling_without_card_grants_no_dynamic_resource():
    result = _resolve([_external()])
    assert result.effective_resources == ()
    assert result.resources[0].reason is AvailabilityReason.CARD_MISSING
    assert all(not tool.available for tool in result.resources[0].tools)


def test_candidate_without_family_hint_is_matched_by_descriptor_facts():
    candidate = replace(_external(), family_id="")
    result = _resolve([candidate], card=_card(_grant()))

    assert result.resources[0].family_id == "user_external_mcp"
    assert result.resources[0].available is True


def test_gateway_endpoint_cannot_replace_missing_provider_origin_for_ceiling():
    candidate = replace(_external(), family_id="", provider_endpoint="")
    result = _resolve([candidate], card=_card(_grant()))

    assert result.resources == ()
    assert result.rejected[0].reason is AvailabilityReason.RESOURCE_OUTSIDE_CEILING


def test_card_cannot_add_resource_outside_descriptor_ceiling():
    result = _resolve(
        [_external()],
        ceiling=_ceiling(families=()),
        card=_card(_grant()),
    )
    assert result.resources == ()
    assert result.rejected[0].reason is AvailabilityReason.RESOURCE_OUTSIDE_CEILING


def test_exact_connector_tools_are_intersection_of_family_card_and_provider():
    result = _resolve([_external()], card=_card(_grant()))
    resource = result.resources[0]
    assert resource.available is True
    assert resource.access_id == "resident-card-1"
    assert resource.card_revision == 3
    assert [(tool.name, tool.available, tool.reason.value) for tool in resource.tools] == [
        ("delete", False, "operation_not_granted"),
        ("search", True, "available"),
    ]
    assert [item.resource_id for item in result.effective_resources] == [EXTERNAL_1]


def test_gateway_qualified_name_keeps_upstream_operation_as_card_authority():
    candidate = _external(tools=())
    candidate = replace(
        candidate,
        tools=(
            ResidentToolDescriptor(
                name="ch_remote_mcp_a__search_b",
                operation="search",
                description="Search fixture records",
            ),
        ),
    )
    result = _resolve([candidate], card=_card(_grant(operations=("search",))))
    tool = result.resources[0].tools[0]
    assert tool.name == "ch_remote_mcp_a__search_b"
    assert tool.operation == "search"
    assert tool.available is True

    narrowed = _resolve(
        [candidate],
        card=_card(_grant(operations=("search",))),
        conversation=ConversationNarrowing(
            disabled_resources={EXTERNAL_1: ("search",)}
        ),
    )
    assert (
        narrowed.resources[0].tools[0].reason
        is AvailabilityReason.CONVERSATION_DISABLED
    )


def test_two_resources_share_one_stable_card_profile():
    result = _resolve(
        [_external(EXTERNAL_2), _external(EXTERNAL_1)],
        card=_card(_grant(EXTERNAL_2), _grant(EXTERNAL_1)),
    )
    assert [resource.resource_id for resource in result.effective_resources] == [
        EXTERNAL_1,
        EXTERNAL_2,
    ]
    assert {resource.access_id for resource in result.resources} == {"resident-card-1"}
    assert {resource.card_revision for resource in result.resources} == {3}


def test_conversation_selection_only_subtracts_from_card_authority():
    result = _resolve(
        [_external()],
        card=_card(_grant(operations=("search", "delete"))),
        conversation=ConversationNarrowing(
            disabled_resources={EXTERNAL_1: ("delete", "not-a-provider-tool")}
        ),
    )
    by_name = {tool.name: tool for tool in result.resources[0].tools}
    assert by_name["search"].available is True
    assert by_name["delete"].reason is AvailabilityReason.CONVERSATION_DISABLED
    assert "not-a-provider-tool" not in by_name


@pytest.mark.parametrize(
    "candidate,card,user",
    [
        (_external(user="user-b"), _card(_grant()), USER),
        (_external(application="other@1-0"), _card(_grant()), USER),
        (_external(agent_id="other"), _card(_grant()), USER),
        (_external(), _card(_grant(), user="user-b"), USER),
        (_external(), _card(_grant(), application="other@1-0"), USER),
        (_external(), _card(_grant(), agent_id="other"), USER),
    ],
)
def test_cross_user_app_and_agent_authority_never_becomes_effective(candidate, card, user):
    result = _resolve([candidate], card=card, user=user)
    assert result.effective_resources == ()
    reasons = {item.reason for item in result.resources} | {
        item.reason for item in result.rejected
    }
    assert AvailabilityReason.SCOPE_MISMATCH in reasons


def test_app_owned_knowledge_is_visible_and_ignores_user_card_contents():
    malicious_grant = _grant(
        KNOWLEDGE,
        operations=(),
        claims=(),
    )
    result = _resolve([_knowledge()], card=_card(malicious_grant))
    resource = result.resources[0]
    assert resource.authority_source is AuthoritySource.APPLICATION
    assert resource.available is True
    assert resource.access_id == ""
    assert all(tool.available for tool in resource.tools)
    assert resource.provenance["delegated_card"] is None


def test_managed_knowledge_is_controlled_by_the_card_when_declared_that_way():
    ceiling = _ceiling(declared=(MANAGED_KNOWLEDGE,), families=())
    pending = _resolve([_knowledge(delegated=True)], ceiling=ceiling)
    assert pending.resources[0].reason is AvailabilityReason.CARD_MISSING

    granted = _resolve(
        [_knowledge(delegated=True)],
        ceiling=ceiling,
        card=_card(
            _grant(
                MANAGED_KNOWLEDGE,
                operations=("search",),
                claims=("knowledge:read",),
            )
        ),
    )
    by_name = {tool.name: tool for tool in granted.resources[0].tools}
    assert by_name["search"].available is True
    assert by_name["read"].reason is AvailabilityReason.OPERATION_NOT_GRANTED


@pytest.mark.parametrize(
    "candidate,card,reason",
    [
        (_external(enabled=False), _card(_grant()), AvailabilityReason.CONNECTOR_DISABLED),
        (_external(credential_ready=False), _card(_grant()), AvailabilityReason.CREDENTIAL_MISSING),
        (
            _external(descriptor_accepted=False),
            _card(_grant()),
            AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED,
        ),
        (_external(), _card(_grant(), active=False), AvailabilityReason.CARD_REVOKED),
        (
            _external(),
            _card(_grant(), expires_at=NOW),
            AvailabilityReason.CARD_EXPIRED,
        ),
        (
            _external(),
            _card(_grant(claims=())),
            AvailabilityReason.CLAIM_NOT_GRANTED,
        ),
    ],
)
def test_live_resource_and_card_state_is_reflected_on_each_resolution(candidate, card, reason):
    result = _resolve([candidate], card=card)
    assert result.effective_resources == ()
    assert result.resources[0].reason is reason


def test_provider_and_card_loader_failures_remain_distinct_and_fail_closed():
    provider_down = _resolve(
        [
            _external(
                unavailable_reason=AvailabilityReason.RESOURCE_PROVIDER_UNAVAILABLE
            )
        ],
        card=_card(_grant()),
    )
    assert (
        provider_down.resources[0].reason
        is AvailabilityReason.RESOURCE_PROVIDER_UNAVAILABLE
    )

    card_down = resolve_effective_resident_resources(
        ceiling=_ceiling(),
        grantor_subject=USER,
        candidates=(_knowledge(), _external()),
        card=None,
        card_unavailable_reasons={
            "grantor": AvailabilityReason.CARD_AUTHORITY_UNAVAILABLE,
        },
        now_epoch=NOW,
    )
    by_id = {resource.resource_id: resource for resource in card_down.resources}
    assert by_id[KNOWLEDGE].available is True
    assert (
        by_id[EXTERNAL_1].reason
        is AvailabilityReason.CARD_AUTHORITY_UNAVAILABLE
    )


def test_one_card_cannot_authorize_a_conflicting_identity_scope():
    account_scope = "grantor_identity_family"
    result = resolve_effective_resident_resources(
        ceiling=_ceiling(),
        grantor_subject=USER,
        candidates=(
            _external(EXTERNAL_1),
            _external(EXTERNAL_2, identity_scope=account_scope),
        ),
        card=_card(_grant(EXTERNAL_1)),
        now_epoch=NOW,
    )
    by_id = {resource.resource_id: resource for resource in result.resources}
    assert by_id[EXTERNAL_1].available is True
    assert by_id[EXTERNAL_2].reason is AvailabilityReason.SCOPE_MISMATCH


def test_connected_account_readiness_is_per_tool_and_after_conversation_narrowing():
    account_status = ConnectedAccountStatus(
        ready=False,
        reason=AvailabilityReason.CONNECTED_ACCOUNT_MISSING,
        recovery={"operation": "provider_connection_create"},
    )
    result = _resolve(
        [_external(tool_status={"search": account_status})],
        card=_card(_grant(operations=("search", "delete"))),
    )
    by_name = {tool.name: tool for tool in result.resources[0].tools}
    assert by_name["delete"].available is True
    assert by_name["search"].reason is AvailabilityReason.CONNECTED_ACCOUNT_MISSING
    assert by_name["search"].recovery == {"operation": "provider_connection_create"}

    disabled = _resolve(
        [_external(tool_status={"search": account_status})],
        card=_card(_grant(operations=("search", "delete"))),
        conversation=ConversationNarrowing(disabled_resources={EXTERNAL_1: ("search",)}),
    )
    disabled_search = {tool.name: tool for tool in disabled.resources[0].tools}["search"]
    assert disabled_search.reason is AvailabilityReason.CONVERSATION_DISABLED
    assert disabled_search.recovery == {}


def test_once_exhaustion_and_card_edit_change_the_next_resolution():
    exhausted = InvocationPolicyState(mode="once", remaining=0, revision=4)
    before = _resolve(
        [_external()],
        card=_card(_grant(policies={"search": exhausted}), revision=4),
    )
    assert before.resources[0].reason is AvailabilityReason.ONCE_EXHAUSTED

    after = _resolve(
        [_external()],
        card=_card(
            _grant(
                operations=("search", "delete"),
                policies={"search": InvocationPolicyState(mode="always", revision=5)},
            ),
            revision=5,
        ),
    )
    assert after.resources[0].available is True
    assert all(tool.available for tool in after.resources[0].tools)
    assert after.resources[0].card_revision == 5


def test_descriptor_drift_acceptance_changes_only_fresh_projection():
    stale = _resolve(
        [_external(descriptor_accepted=False, provider_revision="provider-2")],
        card=_card(_grant()),
    )
    accepted = _resolve(
        [_external(descriptor_accepted=True, provider_revision="provider-2")],
        card=_card(_grant()),
    )
    assert stale.resources[0].reason is AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED
    assert accepted.resources[0].available is True
    assert accepted.resources[0].provider_revision == "provider-2"


def test_operation_drift_suspends_only_the_changed_selected_operation():
    changed = AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED
    result = _resolve(
        [_external()],
        card=_card(
            _grant(
                operations=("search", "delete"),
                operation_states={"delete": changed},
                operation_recovery={
                    "delete": {"operation": "accept_operation_descriptor"}
                },
                operation_accepted_digests={
                    "search": "search-v1",
                    "delete": "delete-v1",
                },
                operation_current_digests={
                    "search": "search-v1",
                    "delete": "delete-v2",
                },
            )
        ),
    )
    by_name = {tool.name: tool for tool in result.resources[0].tools}
    assert result.resources[0].available is True
    assert by_name["search"].available is True
    assert by_name["delete"].reason is changed
    assert by_name["delete"].recovery == {
        "operation": "accept_operation_descriptor"
    }
    assert by_name["delete"].accepted_descriptor_identity == "delete-v1"
    assert by_name["delete"].current_descriptor_identity == "delete-v2"


def test_unknown_wildcard_operation_state_suspends_every_provider_tool():
    result = _resolve(
        [_external()],
        card=_card(
            _grant(
                operations=("*",),
                operation_states={
                    "*": AvailabilityReason.OPERATION_STATE_UNKNOWN,
                },
            )
        ),
    )
    assert result.effective_resources == ()
    assert {
        (tool.name, tool.reason) for tool in result.resources[0].tools
    } == {
        ("delete", AvailabilityReason.OPERATION_STATE_UNKNOWN),
        ("search", AvailabilityReason.OPERATION_STATE_UNKNOWN),
    }


def test_card_resource_kind_mismatch_fails_closed():
    result = _resolve(
        [_external()],
        card=_card(_grant(resource_kind="catalog")),
    )
    assert result.effective_resources == ()
    assert result.resources[0].reason is AvailabilityReason.RESOURCE_KIND_MISMATCH


def test_family_resource_and_tool_limits_are_deterministic():
    family = _family(max_resources=1, max_tools=1)
    result = _resolve(
        [_external(EXTERNAL_2), _external(EXTERNAL_1)],
        ceiling=_ceiling(families=(family,)),
        card=_card(
            _grant(EXTERNAL_1, operations=("search", "delete")),
            _grant(EXTERNAL_2, operations=("search", "delete")),
        ),
    )
    assert [resource.resource_id for resource in result.resources] == [EXTERNAL_1]
    assert result.rejected[0].resource_id == EXTERNAL_2
    assert result.rejected[0].reason is AvailabilityReason.RESOURCE_LIMIT_EXCEEDED
    by_name = {tool.name: tool for tool in result.resources[0].tools}
    assert by_name["delete"].available is True
    assert by_name["search"].reason is AvailabilityReason.TOOL_LIMIT_EXCEEDED


def test_ungranted_requestable_resources_do_not_consume_delegated_card_quota():
    granted = "urn:connection-hub:remote-mcp:z-granted"
    result = _resolve(
        [_external(EXTERNAL_1), _external(EXTERNAL_2), _external(granted)],
        ceiling=_ceiling(families=(_family(max_resources=1),)),
        card=_card(_grant(granted)),
    )
    by_id = {resource.resource_id: resource for resource in result.resources}
    assert by_id[granted].available is True
    assert by_id[EXTERNAL_1].reason is AvailabilityReason.RESOURCE_NOT_GRANTED
    assert by_id[EXTERNAL_2].reason is AvailabilityReason.RESOURCE_NOT_GRANTED
    assert not any(
        entry.resource_id == granted
        and entry.reason is AvailabilityReason.RESOURCE_LIMIT_EXCEEDED
        for entry in result.rejected
    )


def test_exact_declared_resource_does_not_consume_dynamic_family_quota():
    family = _family(max_resources=1)
    result = _resolve(
        [_external(EXTERNAL_1), _external(EXTERNAL_2)],
        ceiling=_ceiling(declared=(EXTERNAL_1,), families=(family,)),
        card=_card(_grant(EXTERNAL_1), _grant(EXTERNAL_2)),
    )
    assert [resource.resource_id for resource in result.resources] == [
        EXTERNAL_1,
        EXTERNAL_2,
    ]


def test_connected_account_status_rejects_inconsistent_state():
    with pytest.raises(ValueError, match="tool readiness and reason disagree"):
        ConnectedAccountStatus(
            ready=False,
            reason=AvailabilityReason.AVAILABLE,
        )


def test_unknown_card_resource_and_duplicate_provider_resource_fail_closed():
    duplicate = _external()
    result = _resolve(
        [duplicate, duplicate],
        card=_card(_grant(), _grant("urn:connection-hub:remote-mcp:gone")),
    )
    assert result.resources == ()
    reasons = {(item.resource_id, item.reason) for item in result.rejected}
    assert (EXTERNAL_1, AvailabilityReason.DUPLICATE_RESOURCE) in reasons
    assert (
        "urn:connection-hub:remote-mcp:gone",
        AvailabilityReason.RESOURCE_NOT_CURRENT,
    ) in reasons


def test_duplicate_card_resource_fails_closed_without_resolving_a_candidate():
    result = _resolve(
        [_external()],
        card=_card(_grant(operations=("search",)), _grant(operations=("delete",))),
    )
    assert result.resources == ()
    assert [entry.to_dict() for entry in result.rejected] == [
        {
            "resource_id": EXTERNAL_1,
            "reason": "duplicate_resource",
            "detail": "card",
            "identity_scope": "grantor",
        }
    ]


def test_existing_mcp_selection_and_stable_resource_selection_share_one_adapter():
    candidates = (_knowledge(), _external())
    narrowing = conversation_narrowing_from_selection(
        {
            "mcp": {"knowledge": True},
            "resources": {EXTERNAL_1: ["delete"], "unknown": True},
        },
        candidates,
    )
    assert narrowing.disabled_resources == {
        KNOWLEDGE: None,
        EXTERNAL_1: ("delete",),
    }


def test_legacy_gateway_toggle_applies_to_every_resource_and_stable_key_wins():
    narrowing = conversation_narrowing_from_selection(
        {
            "mcp": {"connection_hub": True},
            "resources": {EXTERNAL_1: ["delete"]},
        },
        (_external(EXTERNAL_1), _external(EXTERNAL_2)),
    )
    assert narrowing.disabled_resources == {
        EXTERNAL_1: ("delete",),
        EXTERNAL_2: None,
    }


def test_output_is_deterministic_and_contains_no_credentials():
    first = _resolve(
        [_external(EXTERNAL_2), _knowledge(), _external(EXTERNAL_1)],
        card=_card(_grant(EXTERNAL_2), _grant(EXTERNAL_1)),
    ).to_dict()
    second = _resolve(
        [_external(EXTERNAL_1), _external(EXTERNAL_2), _knowledge()],
        card=_card(_grant(EXTERNAL_1), _grant(EXTERNAL_2)),
    ).to_dict()
    assert first == second
    rendered = repr(first).lower()
    assert "access_token" not in rendered
    assert "authorization" not in rendered
    assert "provider_credential" not in rendered


def test_effective_inventory_projects_to_capability_catalog_without_mutation():
    inventory = _resolve([_external()], card=_card(_grant()))
    original = {"agent": AGENT, "mcp": []}
    projected = attach_effective_resource_catalog(original, inventory)
    assert original == {"agent": AGENT, "mcp": []}
    assert projected["resources"][0]["resource_id"] == EXTERNAL_1
    assert projected["resources"][0]["authority_source"] == "delegated_card"
    assert projected["resources"][0]["binding"]["mode"] == "gateway"
    assert projected["resource_rejections"] == []


def test_one_resident_card_projects_multiple_resources_without_mcp_duplicates():
    inventory = _resolve(
        [_knowledge(delegated=True), _external()],
        ceiling=_ceiling(declared=(MANAGED_KNOWLEDGE,)),
        card=_card(
            _grant(
                MANAGED_KNOWLEDGE,
                operations=("search",),
                claims=("knowledge:read",),
            ),
            _grant(EXTERNAL_1),
        ),
    )
    original = {
        "agent": AGENT,
        "mcp": [
            {
                "server_id": "application_docs",
                "authority_source": "application",
            },
            {
                "server_id": "knowledge_managed",
                "authority_source": "delegated_card",
                "resource_id": MANAGED_KNOWLEDGE,
            },
        ],
    }

    projected = attach_effective_resource_catalog(original, inventory)

    assert [row["server_id"] for row in projected["mcp"]] == [
        "application_docs"
    ]
    assert [row["resource_id"] for row in projected["resources"]] == [
        EXTERNAL_1,
        MANAGED_KNOWLEDGE,
    ]
    assert {row["access_id"] for row in projected["resources"]} == {
        "resident-card-1"
    }
    assert len(original["mcp"]) == 2


def test_missing_resident_card_does_not_restore_delegated_direct_mcp_row():
    inventory = _resolve(
        [],
        ceiling=_ceiling(declared=(MANAGED_KNOWLEDGE,), families=()),
    )
    projected = attach_effective_resource_catalog(
        {
            "mcp": [
                {
                    "server_id": "knowledge_managed",
                    "authority_source": "delegated_card",
                    "resource_id": MANAGED_KNOWLEDGE,
                }
            ]
        },
        inventory,
    )

    assert projected["mcp"] == []
    assert projected["resources"] == []


def test_per_turn_service_loads_fresh_card_and_provider_facts_without_cache():
    class ChangingLoader:
        def __init__(self):
            self.calls = 0

        async def load_current(self, *, ceiling, grantor_subject):
            assert ceiling.agent_id == AGENT
            assert grantor_subject == USER
            self.calls += 1
            if self.calls == 1:
                return CurrentResidentResourceFacts(
                    candidates=(_external(provider_revision="provider-1"),),
                    card=_card(_grant(), revision=3),
                    observed_at_epoch=NOW,
                )
            return CurrentResidentResourceFacts(
                candidates=(_external(provider_revision="provider-2"),),
                card=_card(_grant(), active=False, revision=4),
                observed_at_epoch=NOW + 1,
            )

    loader = ChangingLoader()
    first = asyncio.run(
        resolve_current_resident_resources(
            loader=loader,
            ceiling=_ceiling(),
            grantor_subject=USER,
        )
    )
    second = asyncio.run(
        resolve_current_resident_resources(
            loader=loader,
            ceiling=_ceiling(),
            grantor_subject=USER,
        )
    )

    assert loader.calls == 2
    assert first.resources[0].available is True
    assert first.resources[0].card_revision == 3
    assert first.resources[0].provider_revision == "provider-1"
    assert second.effective_resources == ()
    assert second.resources[0].reason is AvailabilityReason.CARD_REVOKED
    assert second.resources[0].card_revision == 4
    assert second.resources[0].provider_revision == "provider-2"


def test_a_conversation_that_turns_a_resource_family_off_loses_every_resource_it_admits():
    """The picker's deny map names the family; the turn honours it."""

    narrowing = conversation_narrowing_from_selection(
        {"delegated_resource_families": ["user_external_mcp"]},
        [_external()],
    )
    assert narrowing.disabled_families == frozenset({"user_external_mcp"})

    off = _resolve(
        [_external()],
        card=_card(_grant(operations=("search", "delete"))),
        conversation=narrowing,
    )
    assert off.resources[0].reason is AvailabilityReason.CONVERSATION_DISABLED
    assert all(tool.available is False for tool in off.resources[0].tools)

    on = _resolve(
        [_external()],
        card=_card(_grant(operations=("search", "delete"))),
        conversation=conversation_narrowing_from_selection({}, [_external()]),
    )
    assert on.resources[0].reason is AvailabilityReason.AVAILABLE

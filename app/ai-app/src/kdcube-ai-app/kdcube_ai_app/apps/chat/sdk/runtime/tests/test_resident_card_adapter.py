# SPDX-License-Identifier: MIT

"""Projection consumes Card's public read model without parsing persistence."""

from __future__ import annotations

import pytest

from connection_hub.delegated_credentials.cards.identity import (
    CARD_KIND_AGENT,
    ResidentCallerProfile,
)
from connection_hub.delegated_credentials.cards.model import (
    CardAuthority,
    NamedServiceSelection,
)
from connection_hub.delegated_credentials.cards.read_model import (
    CALLER_KIND_MANUAL,
    CALLER_KIND_RESIDENT,
    CardOperationView,
    CardResourceView,
    DelegatedCardView,
    build_card_view,
)

from kdcube_ai_app.apps.chat.sdk.runtime.resident_resources import (
    AuthoritySource,
    AvailabilityReason,
    CardBackedResidentResourceFactsLoader,
    ResidentAgentCeiling,
    ResidentCardAdapterError,
    ResidentResourceCandidate,
    ResidentToolDescriptor,
    ResourceBinding,
    delegated_card_snapshot_from_view,
)


TENANT = "tenant-a"
PROJECT = "project-a"
APPLICATION = "workspace@1-0"
AGENT = "main"
USER = "user-a"
RESOURCE = "urn:connection-hub:remote-mcp:connector-1"


def _profile() -> ResidentCallerProfile:
    return ResidentCallerProfile(
        grantor_subject=USER,
        application=APPLICATION,
        agent_id=AGENT,
    )


def _resource(
    *,
    identity_scope: str = "grantor",
    state: str = "changed",
) -> CardResourceView:
    return CardResourceView(
        resource=RESOURCE,
        kind="remote_mcp",
        provider="owner-overlay",
        label="Fixture",
        state=state,
        identity_scope=identity_scope,
        grants=("external_mcp:use",),
        operations=(
            CardOperationView(
                name="search",
                state="current",
                accepted_digest="search-v1",
                current_digest="search-v1",
                policy={"mode": "always", "remaining": None, "revision": 3},
            ),
            CardOperationView(
                name="delete",
                state="changed",
                accepted_digest="delete-v1",
                current_digest="delete-v2",
                policy={"mode": "once", "remaining": 1, "revision": 4},
            ),
        ),
        accepted_revision="connector-1",
        current_revision="connector-2",
        accepted_digest="resource-v1",
        current_digest="resource-v2",
        named_service_operations={"slack": ("object.search", "object.action")},
    )


def _view(
    *,
    profile: ResidentCallerProfile | None = None,
    caller_kind: str = CALLER_KIND_RESIDENT,
    access_id: str = "agent-legacy-resource-derived",
    resources: tuple[CardResourceView, ...] | None = None,
    identity_scope: str = "grantor",
) -> DelegatedCardView:
    resident = profile or _profile()
    return DelegatedCardView(
        access_id=access_id,
        client_id=resident.client_id,
        caller_kind=caller_kind,
        profile=resident,
        grantor_subject=resident.grantor_subject,
        delegate_subject="",
        source="agent",
        label="Workspace main",
        card_revision=7,
        catalog_version="catalog-5",
        state="active",
        created_at=1_700_000_000,
        expires_at=1_900_000_000,
        identity_scope=identity_scope,
        resources=resources if resources is not None else (_resource(),),
        account_scope={"slack": {"account-a": ("slack:search",)}},
    )


def test_adapter_preserves_card_resource_operation_policy_and_account_facts():
    snapshot = delegated_card_snapshot_from_view(
        _view(),
        tenant=TENANT,
        project=PROJECT,
    )

    assert snapshot.access_id == "agent-legacy-resource-derived"
    assert snapshot.client_id == _profile().client_id
    assert snapshot.application == APPLICATION
    assert snapshot.agent_id == AGENT
    assert snapshot.catalog_version == "catalog-5"
    assert snapshot.account_scope == {
        "slack": {"account-a": ("slack:search",)}
    }
    resource = snapshot.resources[0]
    assert resource.resource_state is AvailabilityReason.AVAILABLE
    assert resource.operation_states == {
        "search": AvailabilityReason.AVAILABLE,
        "delete": AvailabilityReason.OPERATION_DESCRIPTOR_CHANGED,
    }
    assert resource.invocation_policies["delete"].mode == "once"
    assert resource.invocation_policies["delete"].remaining == 1
    assert resource.operation_accepted_digests["delete"] == "delete-v1"
    assert resource.operation_current_digests["delete"] == "delete-v2"
    assert resource.named_service_operations == {
        "slack": ("object.action", "object.search")
    }


def test_adapter_accepts_a_view_built_by_the_published_card_contract():
    authority = CardAuthority(
        access_id="agent-pre-migration",
        card_kind=CARD_KIND_AGENT,
        client_id=_profile().client_id,
        grantor_subject=USER,
        delegate_subject=f"integration:{_profile().client_id}:{USER}",
        source="agent",
        label="Workspace main",
        card_revision=2,
        catalog_version="catalog-1",
        resource_grants={RESOURCE: ("external_mcp:use",)},
        resource_operations={RESOURCE: ("search",)},
        named_service_operations=NamedServiceSelection.none(),
        identity_scope="grantor",
        created_at=1_700_000_000,
        expires_at=1_900_000_000,
    )
    snapshot = delegated_card_snapshot_from_view(
        build_card_view(authority),
        tenant=TENANT,
        project=PROJECT,
    )
    assert snapshot.access_id == "agent-pre-migration"
    assert snapshot.resources[0].operations == ("search",)
    assert snapshot.resources[0].operation_states == {
        "search": AvailabilityReason.OPERATION_STATE_UNKNOWN,
    }


def test_legacy_access_id_is_allowed_but_profile_fields_must_match():
    resident = _profile()
    legacy = delegated_card_snapshot_from_view(
        _view(profile=resident, access_id="agent-old"),
        tenant=TENANT,
        project=PROJECT,
    )
    assert legacy.access_id == "agent-old"
    assert legacy.access_id != resident.access_id

    mismatched = _view(profile=resident)
    object.__setattr__(mismatched, "grantor_subject", "other-user")
    with pytest.raises(ResidentCardAdapterError, match="resident_card_profile_mismatch"):
        delegated_card_snapshot_from_view(
            mismatched,
            tenant=TENANT,
            project=PROJECT,
        )


def test_nonresident_and_cross_scope_views_fail_closed():
    with pytest.raises(ResidentCardAdapterError, match="resident_card_profile_required"):
        delegated_card_snapshot_from_view(
            _view(caller_kind=CALLER_KIND_MANUAL),
            tenant=TENANT,
            project=PROJECT,
        )

    with pytest.raises(
        ResidentCardAdapterError,
        match="resident_card_resource_identity_scope_mismatch",
    ):
        delegated_card_snapshot_from_view(
            _view(resources=(_resource(identity_scope="account:other"),)),
            tenant=TENANT,
            project=PROJECT,
        )


@pytest.mark.parametrize(
    "policy,error",
    [
        ({"mode": "sometimes", "revision": 1}, "policy_mode_invalid"),
        ({"mode": "once", "remaining": 2, "revision": 1}, "policy_remaining_invalid"),
        ({"mode": "always", "remaining": 1, "revision": 1}, "policy_remaining_invalid"),
        ({"mode": "once", "remaining": 1, "revision": 0}, "policy_revision_invalid"),
    ],
)
def test_malformed_public_invocation_policy_fails_closed(policy, error):
    operation = CardOperationView(name="search", state="current", policy=policy)
    resource = CardResourceView(
        resource=RESOURCE,
        kind="remote_mcp",
        state="current",
        identity_scope="grantor",
        grants=("external_mcp:use",),
        operations=(operation,),
    )
    with pytest.raises(ResidentCardAdapterError, match=error):
        delegated_card_snapshot_from_view(
            _view(resources=(resource,)),
            tenant=TENANT,
            project=PROJECT,
        )


def test_unknown_wildcard_operation_fails_closed_for_every_provider_tool():
    wildcard = CardOperationView(name="*", state="unknown")
    resource = CardResourceView(
        resource=RESOURCE,
        kind="remote_mcp",
        state="unknown",
        identity_scope="grantor",
        grants=("external_mcp:use",),
        operations=(wildcard,),
    )
    snapshot = delegated_card_snapshot_from_view(
        _view(resources=(resource,)),
        tenant=TENANT,
        project=PROJECT,
    )
    assert snapshot.resources[0].operation_states == {
        "*": AvailabilityReason.OPERATION_STATE_UNKNOWN
    }


def test_adapted_snapshot_contains_no_credentials():
    rendered = repr(
        delegated_card_snapshot_from_view(
            _view(),
            tenant=TENANT,
            project=PROJECT,
        )
    ).lower()
    assert "access_token" not in rendered
    assert "refresh_token" not in rendered
    assert "authorization" not in rendered
    assert "provider_credential" not in rendered


def _candidate(resource: str, *, identity_scope: str) -> ResidentResourceCandidate:
    return ResidentResourceCandidate(
        resource_id=resource,
        resource_kind="remote_mcp",
        server_id=resource.rsplit(":", 1)[-1],
        alias=resource.rsplit(":", 1)[-1],
        display_name=resource,
        authority_source=AuthoritySource.DELEGATED_CARD,
        tools=(ResidentToolDescriptor(name="search"),),
        binding=ResourceBinding(
            mode="gateway",
            server_id="connection_hub",
            alias="connection_hub",
            transport="streamable-http",
            endpoint="https://hub.example/mcp",
        ),
        tenant=TENANT,
        project=PROJECT,
        application=APPLICATION,
        agent_id=AGENT,
        grantor_subject=USER,
        identity_scope=identity_scope,
    )


@pytest.mark.asyncio
async def test_card_backed_loader_reads_the_resident_profile_card_once():
    resource_two = "urn:connection-hub:remote-mcp:connector-2"

    class Candidates:
        async def load_candidates(self, *, ceiling, grantor_subject):
            assert ceiling.application == APPLICATION
            assert grantor_subject == USER
            return (
                _candidate(RESOURCE, identity_scope="grantor"),
                _candidate(resource_two, identity_scope="grantor"),
                _candidate(resource_two, identity_scope="grantor"),
            )

    class Cards:
        def __init__(self):
            self.calls = []

        async def resident_profile_card(self, *, grantor_subject, client_id):
            self.calls.append((grantor_subject, client_id))
            return _view(
                access_id="one-profile-card",
                resources=(
                    CardResourceView(
                        resource=RESOURCE,
                        kind="remote_mcp",
                        state="current",
                        identity_scope="grantor",
                        grants=("external_mcp:use",),
                        operations=(
                            CardOperationView(name="search", state="current"),
                        ),
                    ),
                    CardResourceView(
                        resource=resource_two,
                        kind="remote_mcp",
                        state="current",
                        identity_scope="grantor",
                        grants=("external_mcp:use",),
                        operations=(
                            CardOperationView(name="search", state="current"),
                        ),
                    ),
                ),
            )

    cards = Cards()
    loader = CardBackedResidentResourceFactsLoader(
        candidate_loader=Candidates(),
        card_reader=cards,
        clock=lambda: 1_800_000_000,
    )
    facts = await loader.load_current(
        ceiling=ResidentAgentCeiling(
            tenant=TENANT,
            project=PROJECT,
            application=APPLICATION,
            agent_id=AGENT,
        ),
        grantor_subject=USER,
    )

    assert facts.card is not None
    assert {resource.resource_id for resource in facts.card.resources} == {
        RESOURCE,
        resource_two,
    }
    assert cards.calls == [(USER, _profile().client_id)]
    assert facts.card_unavailable_reasons == {}
    assert facts.observed_at_epoch == 1_800_000_000


@pytest.mark.asyncio
async def test_card_backed_loader_maps_one_card_failure_to_candidate_scopes():
    account_scope = "grantor_identity_family"

    class Candidates:
        async def load_candidates(self, *, ceiling, grantor_subject):
            return (
                _candidate(RESOURCE, identity_scope="grantor"),
                _candidate(
                    "urn:connection-hub:remote-mcp:connector-2",
                    identity_scope=account_scope,
                ),
            )

    class Cards:
        async def resident_profile_card(self, *, grantor_subject, client_id):
            raise RuntimeError("storage details stay internal")

    facts = await CardBackedResidentResourceFactsLoader(
        candidate_loader=Candidates(),
        card_reader=Cards(),
        clock=lambda: 1_800_000_000,
    ).load_current(
        ceiling=ResidentAgentCeiling(
            tenant=TENANT,
            project=PROJECT,
            application=APPLICATION,
            agent_id=AGENT,
        ),
        grantor_subject=USER,
    )

    assert facts.card is None
    assert facts.card_unavailable_reasons == {
        "grantor": AvailabilityReason.CARD_AUTHORITY_UNAVAILABLE,
        account_scope: AvailabilityReason.CARD_AUTHORITY_UNAVAILABLE,
    }
    assert "storage details" not in repr(facts)

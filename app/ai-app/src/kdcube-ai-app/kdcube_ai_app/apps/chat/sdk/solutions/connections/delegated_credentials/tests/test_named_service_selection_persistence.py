# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named-service selection persistence across delegated-card updates."""

import json
import os
import uuid

import pytest

from test_automation_access import (
    AutomationAccessService,
    _CatalogResolver,
    _Authority,
    _minter,
    _named_services_config,
    _named_services_connections,
    _NamedServiceDiscovery,
    _Redis,
    _Store,
)

from dataclasses import replace

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    AutomationAccessRecord,
    _subject_key,
    card_authority_from_record,
    card_handles_from_record,
    oauth_access_id,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.persistence import (
    DurableCardPersistence,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)

REDIS_URL = os.environ.get("REDIS_URL") or ""

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="REDIS_URL is not set; delegated-card persistence needs a real Redis",
)


@pytest.fixture
async def card_persistence(tmp_path):
    """Production card persistence: durable revisions plus a Redis projection."""
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Redis at REDIS_URL is unreachable: {exc}")
    yield DurableCardPersistence(
        redis=client,
        tenant=f"t-{uuid.uuid4().hex[:8]}",
        project=f"p-{uuid.uuid4().hex[:8]}",
        card_store=BundleStorageDelegatedCardStore(tmp_path),
    )
    await client.aclose()

RESOURCE = "https://example.test/mcp/named-services"
GRANTS = ["named_services:use", "slack:read", "slack:write"]
USER = {"user_id": "platform-user-1", "roles": ["kdcube:role:registered"], "permissions": []}


def _service(card_persistence) -> AutomationAccessService:
    return AutomationAccessService(
        catalog_resolver=_CatalogResolver(connections=_named_services_connections()),
        card_persistence=card_persistence,
        redis=_Redis(),
        tenant="demo-tenant",
        project="demo-project",
        config=_named_services_config(),
        grant_store=_Store(),
        authority=_Authority(),
        minter=_minter,
        named_service_discovery=_NamedServiceDiscovery({}),
    )


async def _stored(
    service: AutomationAccessService, access_id: str
) -> tuple[object, list[str]]:
    """Read the committed card through the persistence port."""
    record = await service._load_record(access_id, grantor_subject=USER["user_id"])
    assert record is not None, f"card {access_id} is not committed"
    raw = record.to_dict()
    namespaces = (raw.get("named_services") or {}).get("namespaces") or {}
    return raw.get("named_service_operations", "<absent>"), sorted(namespaces)


async def _put(service: AutomationAccessService, record: AutomationAccessRecord) -> None:
    """Commit a hand-built record through the persistence port."""
    await service._persistence.persist(
        card_authority_from_record(record),
        card_handles_from_record(record),
        subject_hash=_subject_key(record.grantor_subject),
        expected_revision=record.card_revision,
    )


@pytest.mark.asyncio
async def test_manual_clear_survives_an_unrelated_update(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS}
    )
    access_id = created["access"]["access_id"]

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS},
        named_service_operations={},
    )
    assert await _stored(service, access_id) == ({}, [])

    renamed = await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS}, label="Renamed",
    )
    assert renamed["ok"] is True
    assert await _stored(service, access_id) == ({}, [])
    assert renamed["access"]["named_service_operations"] == {}


@pytest.mark.asyncio
async def test_agent_clear_survives_a_replace_edit_that_omits_the_field(card_persistence):
    service = _service(card_persistence)
    await service.create_access(
        USER, label="agent", client_id="kdcube-agent:app:a1",
        resource_grants={RESOURCE: GRANTS},
        named_service_operations={},
    )
    access_id = (
        await service.create_access(
            USER, label="agent renamed", client_id="kdcube-agent:app:a1",
            resource_grants={RESOURCE: GRANTS}, merge_existing=False,
        )
    )["access"]["access_id"]

    assert await _stored(service, access_id) == ({}, [])


@pytest.mark.asyncio
async def test_oauth_refresh_does_not_widen_a_cleared_card(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS},
        named_service_operations={RESOURCE: {"slack": ["object.search"]}},
    )
    access_id = created["access"]["access_id"]
    # Re-key the manual card as an OAuth card so the refresh path reads it.
    card_id = oauth_access_id("platform-user-1", "claude", RESOURCE)
    manual = await service._load_record(access_id, grantor_subject=USER["user_id"])
    await _put(service, replace(manual, access_id=card_id, source="oauth", card_revision=0))

    await service.record_oauth_grant(
        grantor_subject="platform-user-1",
        client_id="claude",
        scopes=GRANTS,
        resource=RESOURCE,
        access_token="at",
        refresh_token="rt",
    )

    selection, namespaces = await _stored(service, card_id)
    assert selection == {RESOURCE: {"slack": ["object.search"]}}
    assert namespaces == ["slack"]


@pytest.mark.asyncio
async def test_wildcard_is_persisted_and_survives_a_rename(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="all ops", resource_grants={RESOURCE: GRANTS}
    )
    access_id = created["access"]["access_id"]

    # A create that names no selection is stored explicitly, not as absence.
    assert await _stored(service, access_id) == ("*", ["mail", "slack"])
    assert created["access"]["named_service_operations"] == "*"

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS}, label="Renamed",
    )
    assert await _stored(service, access_id) == ("*", ["mail", "slack"])


@pytest.mark.asyncio
async def test_exact_selection_survives_a_rename(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="search only", resource_grants={RESOURCE: GRANTS},
        named_service_operations={RESOURCE: {"slack": ["object.search"]}},
    )
    access_id = created["access"]["access_id"]

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS}, label="Renamed",
    )
    selection, namespaces = await _stored(service, access_id)
    assert selection == {RESOURCE: {"slack": ["object.search"]}}
    assert namespaces == ["slack"]


@pytest.mark.asyncio
async def test_an_explicit_wildcard_update_reopens_a_cleared_card(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS},
        named_service_operations={},
    )
    access_id = created["access"]["access_id"]
    assert await _stored(service, access_id) == ({}, [])

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS},
        named_service_operations="*",
    )
    assert await _stored(service, access_id) == ("*", ["mail", "slack"])


def test_pre_migration_record_is_projected_as_legacy_not_as_empty():
    """A pre-encoding card stored {} while carrying the full boundary."""
    legacy = AutomationAccessRecord.from_mapping(
        {
            "schema": "connection_hub.automation_access.v1",
            "access_id": "aut_legacy",
            "client_id": "automation:aut_legacy",
            "grantor_subject": "platform-user-1",
            "delegate_subject": "integration:automation:aut_legacy",
            "named_service_operations": {},
            "named_services": {"namespaces": {"slack": {"tools": {}}}},
            "resource_grants": {RESOURCE: ["slack:read"]},
            "operations": [],
            "expires_at": 1_780_003_600,
        }
    )
    assert legacy.named_service_operations.is_unknown

    closed = AutomationAccessRecord.from_mapping(
        {
            "schema": "connection_hub.automation_access.v1",
            "access_id": "aut_closed",
            "client_id": "automation:aut_closed",
            "grantor_subject": "platform-user-1",
            "delegate_subject": "integration:automation:aut_closed",
            "named_service_operations": {},
            "named_services": {"namespaces": {}},
            "resource_grants": {RESOURCE: ["slack:read"]},
            "operations": [],
            "expires_at": 1_780_003_600,
        }
    )
    assert closed.named_service_operations.is_none


def test_public_projection_distinguishes_all_four_states():
    def _public(selection_payload, named_services):
        payload = {
            "schema": "connection_hub.automation_access.v1",
            "access_id": "aut_x",
            "client_id": "automation:aut_x",
            "grantor_subject": "platform-user-1",
            "delegate_subject": "integration:automation:aut_x",
            "named_services": named_services,
            "resource_grants": {RESOURCE: ["slack:read"]},
            "operations": [],
            "expires_at": 1_780_003_600,
        }
        if selection_payload != "<absent>":
            payload["named_service_operations"] = selection_payload
        return AutomationAccessRecord.from_mapping(payload).to_public_dict()

    assert _public("*", {})["named_service_operations"] == "*"
    assert _public({}, {"namespaces": {}})["named_service_operations"] == {}
    assert _public({RESOURCE: {"slack": ["object.search"]}}, {})[
        "named_service_operations"
    ] == {RESOURCE: {"slack": ["object.search"]}}
    assert "named_service_operations" not in _public("<absent>", {})


def _service_without_catalog(card_persistence) -> AutomationAccessService:
    from test_automation_access import _CatalogResolver

    return AutomationAccessService(
        catalog_resolver=_CatalogResolver(unavailable=True),
        card_persistence=card_persistence,
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )


@pytest.mark.asyncio
async def test_create_fails_closed_when_the_catalog_cannot_be_resolved(card_persistence):
    service = _service_without_catalog(card_persistence)
    result = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS}
    )
    assert result["ok"] is False
    assert result["error"] == "delegated_catalog_unavailable"
    assert result["retryable"] is True
    assert service._redis.values == {}


@pytest.mark.asyncio
async def test_an_unconfigured_resolver_also_fails_closed():
    service = AutomationAccessService(
        redis=_Redis(), tenant="demo-tenant", project="demo-project",
        config=_named_services_config(), grant_store=_Store(), authority=_Authority(),
        minter=_minter, named_service_discovery=_NamedServiceDiscovery({}),
    )
    result = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS}
    )
    assert result["ok"] is False
    assert result["reason"] == "catalog_resolver_not_configured"


@pytest.mark.asyncio
async def test_saves_stamp_the_active_version_and_advance_the_revision(card_persistence):
    from test_automation_access import TEST_CATALOG_VERSION

    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS}
    )
    access_id = created["access"]["access_id"]
    stored = await service._load_record(access_id, grantor_subject=USER["user_id"])
    assert stored.catalog_version == TEST_CATALOG_VERSION
    assert stored.card_revision == 1

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS}, label="Renamed",
    )
    stored = await service._load_record(access_id, grantor_subject=USER["user_id"])
    assert stored.card_revision == 2


@pytest.mark.asyncio
async def test_a_legacy_card_becomes_explicit_on_its_next_save(card_persistence):
    service = _service(card_persistence)
    created = await service.create_access(
        USER, label="CI bot", resource_grants={RESOURCE: GRANTS}
    )
    access_id = created["access"]["access_id"]

    current = await service._load_record(access_id, grantor_subject=USER["user_id"])
    await _put(
        service,
        replace(
            current,
            named_service_operations=NamedServiceSelection.unknown(),
            catalog_version="",
        ),
    )
    assert await _stored(service, access_id) == ("<absent>", ["mail", "slack"])

    await service.update_access(
        USER, access_id=access_id, resource_grants={RESOURCE: GRANTS}, label="Renamed",
    )
    selection, namespaces = await _stored(service, access_id)
    assert selection == "*"
    assert namespaces == ["mail", "slack"]
    saved = await service._load_record(access_id, grantor_subject=USER["user_id"])
    assert saved.catalog_version

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

import hashlib
from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    CARD_STATE_REVOKED,
    CardAuthority,
    CardCredentialHandles,
    CardRecordError,
    NamedServiceSelection,
    card_revision_name,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
    CardStorageError,
)

RESOURCE = "https://example.test/mcp/named-services"
SUBJECT_HASH = hashlib.sha256(b"platform-user-1").hexdigest()

EXACT = {RESOURCE: {"slack": ["object.search"]}}


def _authority(**overrides) -> CardAuthority:
    base = dict(
        access_id="aut_abc123",
        client_id="automation:abc",
        grantor_subject="platform-user-1",
        delegate_subject="integration:automation:abc",
        source="manual",
        label="CI bot",
        card_revision=1,
        catalog_version="delegated_catalog_2026-08-11-10-30-00-123_d4e5f6a7b8c9",
        resource_grants={RESOURCE: ("named_services:use", "slack:read")},
        named_service_operations=NamedServiceSelection.exact(EXACT),
        operations=("named_services_schema",),
        created_at=1_780_000_000,
        expires_at=1_780_003_600,
    )
    base.update(overrides)
    return CardAuthority(**base)


# -- three-state named-service selection --------------------------------------


def test_absent_field_is_unknown_and_explicit_empty_is_none():
    absent = NamedServiceSelection.from_stored(None, present=False)
    empty = NamedServiceSelection.from_stored({}, present=True)

    assert absent.is_unknown
    assert empty.is_none
    assert absent != empty


def test_wildcard_and_exact_states_parse():
    assert NamedServiceSelection.from_stored("*", present=True).is_all
    exact = NamedServiceSelection.from_stored(EXACT, present=True)
    assert exact.is_exact
    assert exact.operations == {RESOURCE: {"slack": ("object.search",)}}


def test_exact_with_no_surviving_entry_is_none_not_exact():
    assert NamedServiceSelection.exact({}).is_none
    assert NamedServiceSelection.exact({"   ": {}}).is_none


def test_every_written_state_round_trips():
    for selection in (
        NamedServiceSelection.all(),
        NamedServiceSelection.none(),
        NamedServiceSelection.exact(EXACT),
    ):
        stored = selection.to_stored()
        assert NamedServiceSelection.from_stored(stored, present=True) == selection


def test_unknown_writes_nothing():
    assert NamedServiceSelection.unknown().to_stored() is None


def test_namespace_keys_are_normalized():
    selection = NamedServiceSelection.exact({RESOURCE: {"Slack:": ["object.search"]}})
    assert selection.operations == {RESOURCE: {"slack": ("object.search",)}}


def test_a_non_wildcard_string_is_rejected():
    with pytest.raises(CardRecordError) as exc:
        NamedServiceSelection.from_stored("all", present=True)
    assert exc.value.reason == "named_service_operations_invalid"


# -- card authority -----------------------------------------------------------


def test_explicit_empty_survives_serialization_but_unknown_does_not_appear():
    empty = _authority(named_service_operations=NamedServiceSelection.none()).to_dict()
    unknown = _authority(named_service_operations=NamedServiceSelection.unknown()).to_dict()

    assert empty["named_service_operations"] == {}
    assert "named_service_operations" not in unknown


@pytest.mark.parametrize(
    "selection",
    [
        NamedServiceSelection.all(),
        NamedServiceSelection.none(),
        NamedServiceSelection.exact(EXACT),
        NamedServiceSelection.unknown(),
    ],
)
def test_authority_round_trips_every_selection_state(selection):
    authority = _authority(named_service_operations=selection)
    assert CardAuthority.from_mapping(authority.to_dict()) == authority


def test_content_hash_is_stable_and_selection_sensitive():
    authority = _authority()
    assert authority.content_hash() == _authority().content_hash()
    assert (
        _authority(named_service_operations=NamedServiceSelection.none()).content_hash()
        != _authority(named_service_operations=NamedServiceSelection.unknown()).content_hash()
    )


def test_unknown_state_value_is_rejected():
    payload = _authority().to_dict()
    payload["state"] = "suspended"
    with pytest.raises(CardRecordError) as exc:
        CardAuthority.from_mapping(payload)
    assert exc.value.reason == "state_invalid"


def test_credential_handles_are_separate_from_authority():
    assert "access_token" not in _authority().to_dict()
    assert "refresh_token" not in _authority().to_dict()
    assert "session_id" not in _authority().to_dict()
    assert CardCredentialHandles(access_id="aut_abc123").empty is True


def test_revision_name_is_sortable_and_verifiable():
    moment = datetime(2026, 8, 11, 15, 4, 19, 881_000, tzinfo=timezone.utc)
    name = card_revision_name(card_revision=2, content_hash="b9d06ee7124a" + "0" * 52, updated_at=moment)
    assert name == "card_revision_2026-08-11-15-04-19-881_00000002_b9d06ee7124a.json"


# -- durable card store -------------------------------------------------------


@pytest.mark.asyncio
async def test_card_store_commit_and_read_back(tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    authority = _authority()
    moment = datetime.now(timezone.utc)

    assert await store.read_current_authority(subject_hash=SUBJECT_HASH, access_id=authority.access_id) is None

    pointer = await store.write_revision(
        subject_hash=SUBJECT_HASH, authority=authority, updated_at=moment
    )
    await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)

    loaded = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=authority.access_id
    )
    assert loaded is not None
    loaded_pointer, loaded_authority = loaded
    assert loaded_authority == authority
    assert loaded_pointer.card_revision == 1
    assert loaded_pointer.state == CARD_STATE_ACTIVE
    assert await store.list_card_ids(subject_hash=SUBJECT_HASH) == [authority.access_id]


@pytest.mark.asyncio
async def test_revisions_are_immutable_and_accumulate(tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    first = _authority()
    second = _authority(card_revision=2, state=CARD_STATE_REVOKED)

    for authority in (first, second):
        pointer = await store.write_revision(
            subject_hash=SUBJECT_HASH, authority=authority, updated_at=datetime.now(timezone.utc)
        )
        await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)

    names = await store.list_revision_names(subject_hash=SUBJECT_HASH, access_id=first.access_id)
    assert len(names) == 2
    assert names == sorted(names)
    _, current = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=first.access_id
    )
    assert current.state == CARD_STATE_REVOKED


@pytest.mark.asyncio
async def test_pointer_naming_a_missing_revision_is_corruption_not_absence(tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    authority = _authority()
    pointer = await store.write_revision(
        subject_hash=SUBJECT_HASH, authority=authority, updated_at=datetime.now(timezone.utc)
    )
    await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)
    store.revision_path(
        subject_hash=SUBJECT_HASH,
        access_id=authority.access_id,
        revision_name=pointer.revision_name,
    ).unlink()

    with pytest.raises(CardStorageError) as exc:
        await store.read_current_authority(subject_hash=SUBJECT_HASH, access_id=authority.access_id)
    assert exc.value.reason == "current_revision_missing"


@pytest.mark.asyncio
async def test_store_rejects_unsafe_path_segments(tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    with pytest.raises(CardStorageError) as exc:
        await store.read_current(subject_hash="not-a-hash", access_id="aut_abc123")
    assert exc.value.reason == "subject_hash_invalid"
    with pytest.raises(CardStorageError) as exc:
        await store.read_current(subject_hash=SUBJECT_HASH, access_id="../../escape")
    assert exc.value.reason == "access_id_invalid"


# -- record <-> authority/handles conversion -----------------------------------


def _record_with_secrets():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        AutomationAccessRecord,
    )

    return AutomationAccessRecord(
        access_id="aut_abc123",
        label="CI bot",
        client_id="automation:abc",
        grantor_subject="platform-user-1",
        delegate_subject="integration:automation:abc",
        operations=("named_services_schema",),
        resource_grants={RESOURCE: ("slack:read",)},
        named_service_operations=NamedServiceSelection.exact(EXACT),
        named_services={"namespaces": {"slack": {}}},
        identity_scope="grantor",
        catalog_version="delegated_catalog_2026-08-11-10-30-00-123_d4e5f6a7b8c9",
        card_revision=7,
        session_id="sess-secret",
        created_at=1_780_000_000,
        expires_at=1_780_003_600,
        last_four="9abc",
        source="manual",
        refresh_token="rt-secret",
        access_token="at-secret",
        last_issued_at=1_780_000_000,
    )


def test_authority_carries_no_credential_material():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        card_authority_from_record,
        card_handles_from_record,
    )

    record = _record_with_secrets()
    authority = card_authority_from_record(record)
    handles = card_handles_from_record(record)

    serialized = str(authority.to_dict())
    for secret in ("rt-secret", "at-secret", "sess-secret"):
        assert secret not in serialized

    assert handles.access_token == "at-secret"
    assert handles.refresh_token == "rt-secret"
    assert handles.session_id == "sess-secret"


def test_record_round_trips_through_the_split():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        card_authority_from_record,
        card_handles_from_record,
        record_from_card,
    )

    record = _record_with_secrets()
    rebuilt = record_from_card(
        card_authority_from_record(record), card_handles_from_record(record)
    )
    assert rebuilt == record


def test_recombining_without_handles_yields_no_secrets():
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
        card_authority_from_record,
        record_from_card,
    )

    record = _record_with_secrets()
    restored = record_from_card(card_authority_from_record(record))

    assert restored.access_token == ""
    assert restored.refresh_token == ""
    assert restored.session_id == ""
    assert restored.resource_grants == record.resource_grants
    assert restored.named_service_operations == record.named_service_operations
    assert restored.card_revision == 7

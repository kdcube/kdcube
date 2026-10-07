from dataclasses import FrozenInstanceError, replace

import pytest

from kdcube_ai_app.auth.bundle.session_issuance import (
    SessionIssuanceBinding,
    SessionIssuanceReceipt,
    SessionIssuanceRefused,
)


def binding():
    return SessionIssuanceBinding(
        tenant="tenant-a", project="project-a", transaction_id="a" * 64,
        slot="grant:card-a", actor_digest="b" * 64, initiator_digest="c" * 64,
        effect_digest="d" * 64, receipt_digest="e" * 64, access_id="card-a",
        before_card_revision=1, before_content_hash="f" * 64,
        after_card_revision=2, expires_at=2_000_000_000,
    )


def test_same_binding_has_one_stable_identity_and_fingerprint():
    original = binding()
    copied = SessionIssuanceBinding(**original.to_record())
    assert original.identity == copied.identity
    assert original.fingerprint == copied.fingerprint
    with pytest.raises(FrozenInstanceError):
        original.expires_at = 2_000_000_001
    independent = original.to_record()
    independent["expires_at"] = 2_000_000_001
    assert original.expires_at == 2_000_000_000


@pytest.mark.parametrize("field,value", [
    ("actor_digest", "1" * 64), ("initiator_digest", "1" * 64),
    ("effect_digest", "1" * 64), ("receipt_digest", "1" * 64),
    ("access_id", "card-b"), ("before_card_revision", 0),
    ("before_content_hash", "1" * 64), ("after_card_revision", 3),
    ("expires_at", 2_000_000_001),
])
def test_same_identity_keeps_every_other_input_in_replay_fingerprint(field, value):
    original = binding()
    changed = replace(original, **{field: value})
    assert changed.identity == original.identity
    assert changed.fingerprint != original.fingerprint


@pytest.mark.parametrize("field,value", [
    ("tenant", "tenant-b"), ("project", "project-b"),
    ("transaction_id", "1" * 64), ("slot", "grant:card-b"),
])
def test_namespace_transaction_and_slot_make_distinct_identities(field, value):
    assert replace(binding(), **{field: value}).identity != binding().identity


@pytest.mark.parametrize("field,value,reason", [
    ("tenant", "", "identity"), ("project", " project-a", "identity"),
    ("access_id", "x" * 257, "identity"), ("access_id", "a\nb", "identity"),
    ("tenant", "\ud800", "identity"),
    ("slot", "x" * 257, "slot"), ("slot", "é", "slot"),
    ("actor_digest", "B" * 64, "digest"), ("initiator_digest", None, "digest"),
    ("effect_digest", "g" * 64, "digest"), ("receipt_digest", "e" * 63, "digest"),
    ("transaction_id", {}, "digest"),
    ("before_card_revision", True, "incarnation"),
    ("before_card_revision", -1, "incarnation"),
    ("after_card_revision", True, "incarnation"),
    ("after_card_revision", 1, "incarnation"),
    ("before_content_hash", None, "incarnation"),
    ("expires_at", True, "expiry"), ("expires_at", 0, "expiry"),
    ("expires_at", "2000000000", "expiry"),
])
def test_invalid_binding_refuses_without_echoing_supplied_input(field, value, reason):
    with pytest.raises(SessionIssuanceRefused) as caught:
        replace(binding(), **{field: value}).validated()
    assert str(caught.value) == f"session_issuance_{reason}_invalid"


def test_first_incarnation_represents_absent_original_without_inventing_a_hash():
    assert replace(binding(), before_card_revision=0,
                   before_content_hash=None, after_card_revision=1).validated()


def test_public_receipt_contains_only_the_agreed_non_secret_fields():
    receipt = SessionIssuanceReceipt("bsn_synthetic", "a" * 32, "b" * 64)
    assert receipt.to_public_dict() == {
        "session_id": "bsn_synthetic", "secret_ref": "a" * 32,
        "bearer_sha256": "b" * 64, "outcome": "issued",
    }


def test_recovery_outcome_keeps_the_original_result_coordinates():
    original = SessionIssuanceReceipt("bsn_synthetic", "a" * 32, "b" * 64)
    recovered = replace(original, outcome="recovered")
    assert recovered.validated()
    assert {key: value for key, value in recovered.to_public_dict().items()
            if key != "outcome"} == {
        key: value for key, value in original.to_public_dict().items()
        if key != "outcome"
    }


@pytest.mark.parametrize("field,value", [
    ("session_id", ""), ("secret_ref", "a" * 31),
    ("secret_ref", "A" * 32), ("bearer_sha256", "g" * 64),
    ("outcome", "applied"),
])
def test_invalid_public_outcome_refuses(field, value):
    with pytest.raises(SessionIssuanceRefused, match="session_issuance_result_invalid"):
        replace(SessionIssuanceReceipt("bsn_synthetic", "a" * 32, "b" * 64),
                **{field: value}).validated()

"""Pure integrity regressions for scoped typed checkpoint bytes."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.archive import CheckpointArchive
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.errors import (
    CheckpointAgeUnprovenError, CheckpointIntegrityError, CheckpointUnavailableError,
)
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.manifest import (
    CreationProvenance, PayloadManifest, TypedPayload,
)
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.scope import CheckpointScope, PayloadKey

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
SCOPE = CheckpointScope("tenant", "project", "bundle", "agent", "user", "conversation", "opaque-thread")
KEY = PayloadKey("blob", SCOPE.thread_id, channel="messages", version="00001.123")
CREATION = CreationProvenance(NOW - timedelta(days=20), "insert")
PAYLOAD = TypedPayload("msgpack", b"\x00\xffexact original bytes")


class MemoryBackend:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.writes = []
        self.broken_read = False
        self.broken_write = False
        self.corrupt_write = False

    async def exists_a(self, path):
        return path in self.objects

    async def read_bytes_a(self, path):
        self.reads.append(path)
        if self.broken_read:
            raise OSError("unavailable")
        return self.objects[path]

    async def write_bytes_a(self, path, data, meta=None):
        self.writes.append(path)
        if self.broken_write:
            raise OSError("unavailable")
        self.objects[path] = b"corrupt" if self.corrupt_write else data


@pytest.mark.asyncio
async def test_copy_reads_back_exact_typed_bytes_and_retry_never_rewrites():
    backend = MemoryBackend()
    archive = CheckpointArchive(backend)
    manifest = await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    assert await archive.resolve_checkpoint_payload(SCOPE, KEY, manifest) == PAYLOAD
    assert await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION) == manifest
    assert len(backend.writes) == 1 and len(backend.reads) == 3
    assert set(backend.objects) == {manifest.cold_key}


@pytest.mark.parametrize("field", ["tenant", "project", "bundle_id", "agent_id", "user_id", "conversation_id"])
@pytest.mark.asyncio
async def test_scope_mismatch_is_refused_before_any_backend_read(field):
    backend = MemoryBackend()
    archive = CheckpointArchive(backend)
    manifest = await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    other = replace(SCOPE, **{field: "other"})
    before = len(backend.reads)
    with pytest.raises(CheckpointIntegrityError):
        await archive.resolve_checkpoint_payload(other, KEY, manifest)
    assert len(backend.reads) == before


@pytest.mark.parametrize("change", [{"checkpoint_ns": "subgraph"}, {"channel": "tasks"}, {"version": "2"}])
@pytest.mark.asyncio
async def test_composite_key_mismatch_is_refused(change):
    backend = MemoryBackend()
    archive = CheckpointArchive(backend)
    manifest = await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    with pytest.raises(CheckpointIntegrityError):
        await archive.resolve_checkpoint_payload(SCOPE, replace(KEY, **change), manifest)


@pytest.mark.parametrize("field,value", [("serialization_type", "json"), ("data", "dGFtcGVyZWQ="),
                                         ("data", "not-base64"), ("format", 2)])
@pytest.mark.asyncio
async def test_corrupt_envelope_never_returns_payload(field, value):
    backend = MemoryBackend()
    archive = CheckpointArchive(backend)
    manifest = await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    envelope = json.loads(backend.objects[manifest.cold_key])
    envelope[field] = value
    backend.objects[manifest.cold_key] = json.dumps(envelope).encode()
    with pytest.raises(CheckpointIntegrityError):
        await archive.resolve_checkpoint_payload(SCOPE, KEY, manifest)


@pytest.mark.asyncio
async def test_corrupt_readback_has_no_success_manifest_and_is_not_overwritten_on_retry():
    backend = MemoryBackend()
    backend.corrupt_write = True
    archive = CheckpointArchive(backend)
    with pytest.raises(CheckpointIntegrityError):
        await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    backend.corrupt_write = False
    with pytest.raises(CheckpointIntegrityError):
        await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    assert len(backend.writes) == 1


@pytest.mark.parametrize("failure", ["broken_write", "broken_read"])
@pytest.mark.asyncio
async def test_unavailable_backend_is_a_distinct_recovery_failure(failure):
    backend = MemoryBackend()
    setattr(backend, failure, True)
    with pytest.raises(CheckpointUnavailableError):
        await CheckpointArchive(backend).copy_verified(SCOPE, KEY, PAYLOAD, CREATION)


@pytest.mark.asyncio
async def test_missing_exact_part_is_not_empty_success():
    archive = CheckpointArchive(MemoryBackend())
    manifest = PayloadManifest.for_payload(SCOPE, KEY, PAYLOAD, CREATION)
    with pytest.raises(CheckpointUnavailableError):
        await archive.resolve_checkpoint_payload(SCOPE, KEY, manifest)


@pytest.mark.asyncio
async def test_native_null_and_empty_bytea_remain_distinct():
    archive = CheckpointArchive(MemoryBackend())
    null, empty = TypedPayload("empty", None), TypedPayload("empty", b"")
    assert null.sha256 != empty.sha256
    for payload in (null, empty):
        manifest = await archive.copy_verified(SCOPE, KEY, payload, CREATION)
        assert await archive.resolve_checkpoint_payload(SCOPE, KEY, manifest) == payload


def test_creation_age_is_immutable_and_unknown_legacy_age_is_never_eligible():
    assert CREATION.eligible(NOW)
    assert not CREATION.eligible(NOW, 21)
    assert not CreationProvenance().eligible(NOW)
    assert not CreationProvenance(NOW + timedelta(days=1), "insert").eligible(NOW)
    with pytest.raises(FrozenInstanceError):
        CREATION.created_at = NOW
    with pytest.raises(CheckpointAgeUnprovenError):
        PayloadManifest.for_payload(SCOPE, KEY, PAYLOAD, CreationProvenance())


@pytest.mark.parametrize("kwargs", [{"created_at": NOW, "source": "unknown"},
                                   {"created_at": NOW.replace(tzinfo=None), "source": "insert"},
                                   {"created_at": NOW, "source": "reviewed_legacy"}])
def test_guessed_naive_or_unreviewed_legacy_age_is_refused(kwargs):
    with pytest.raises(ValueError):
        CreationProvenance(**kwargs)


def test_reviewed_legacy_provenance_roundtrips_without_inventing_read_or_version_age():
    age = CreationProvenance(NOW - timedelta(days=20), "reviewed_legacy", "reviewed-fixture-proof")
    manifest = PayloadManifest.for_payload(SCOPE, KEY, PAYLOAD, age)
    assert PayloadManifest.from_dict(manifest.to_dict()) == manifest
    assert manifest.creation.created_at == age.created_at


def test_cold_key_cannot_address_an_existing_attachment_or_other_object():
    manifest = PayloadManifest.for_payload(SCOPE, KEY, PAYLOAD, CREATION)
    with pytest.raises(CheckpointIntegrityError):
        replace(manifest, cold_key="cb/existing-attachment.json")
    assert manifest.cold_key.startswith("checkpoint-db/v1/2026-09-17/")


def test_ambiguous_delimiter_strings_have_distinct_canonical_scope_keys():
    one = replace(SCOPE, user_id="user:conversation", conversation_id="tail")
    two = replace(SCOPE, user_id="user", conversation_id="conversation:tail")
    assert KEY.digest(one) != KEY.digest(two)


@pytest.mark.parametrize("key", [PayloadKey("checkpoint", "opaque-thread", checkpoint_id="c1"),
                                PayloadKey("write", "opaque-thread", checkpoint_id="c1", task_id="t1", idx=-1)])
def test_family_keys_preserve_native_primary_key_shape(key):
    assert key.digest(SCOPE) != KEY.digest(SCOPE)


def test_manifest_hash_and_length_cannot_accept_changed_bytes():
    manifest = PayloadManifest.for_payload(SCOPE, KEY, PAYLOAD, CREATION)
    with pytest.raises(CheckpointIntegrityError):
        manifest.check_payload(TypedPayload("msgpack", b"same type, different bytes"))
    with pytest.raises(CheckpointIntegrityError):
        manifest.check_payload(TypedPayload("json", PAYLOAD.data))


@pytest.mark.asyncio
async def test_existing_external_object_sentinel_is_never_read_or_modified():
    backend = MemoryBackend()
    backend.objects["cb/existing-body.json"] = b"keep unchanged"
    archive = CheckpointArchive(backend)
    await archive.copy_verified(SCOPE, KEY, PAYLOAD, CREATION)
    assert backend.objects["cb/existing-body.json"] == b"keep unchanged"
    assert "cb/existing-body.json" not in backend.reads + backend.writes

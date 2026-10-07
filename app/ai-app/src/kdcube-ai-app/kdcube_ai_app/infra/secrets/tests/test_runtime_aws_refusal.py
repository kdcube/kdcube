# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Received-refusal closure: real PG and explicit synthetic SDK responses.

Adapts the independently supplied capacity reproduction to a real ClientError
class with received HTTP metadata. Arbitrary code-only exceptions are its
negative twin, not positive AWS non-creation evidence. No live cloud/IAM proof.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import replace
from contextlib import asynccontextmanager

import pytest
from botocore.exceptions import ClientError

from kdcube_ai_app.infra.secrets.runtime_aws import RuntimeAwsStore, RuntimeCloudError, _received_create_refusal
from kdcube_ai_app.infra.secrets.runtime_pg_metadata import PostgresRuntimeCustodyMetadata
from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError, create_runtime_metadata_schema
from kdcube_ai_app.infra.secrets.tests.test_runtime_aws import COMMITMENT_KEY, VALUE, SyntheticAws, CloudFailure
from kdcube_ai_app.infra.secrets.tests.test_runtime_pg_metadata import NS, metadata, cleanup_due, reserve, publish, arn

CANARY = "synthetic-cloud-error-message-do-not-expose"


def received(code="AccessDeniedException", *, status=400, retries=0, operation="CreateSecret"):
    return ClientError(dict(Error=dict(Code=code, Message=CANARY),
                            ResponseMetadata=dict(HTTPStatusCode=status, RetryAttempts=retries)), operation)


class RespondingAws(SyntheticAws):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    async def create_secret(self, **args):
        if self.failure is not None:
            self.calls.append(("create", dict(args)))
            if self.before_create is not None:
                await self.before_create()
            raise self.failure
        return await super().create_secret(**args)


def custody(metadata, cloud):
    bounded = PostgresRuntimeCustodyMetadata(
        metadata._pool, schema=metadata._records.split('"')[1], namespace=NS,
        authorized_namespaces=(NS,), cloud_prefix="test/runtime", max_unresolved=1,
    )
    return RuntimeAwsStore(metadata=bounded, client_factory=cloud.client, account_id="123456789012",
                           region="eu-central-1", commitment_key=COMMITMENT_KEY)


@pytest.mark.parametrize("code", [
    "AccessDeniedException", "AccessDenied", "NotAuthorized", "InvalidClientTokenId",
    "IncompleteSignature", "OptInRequired", "RequestExpired", "ThrottlingException",
    "InvalidParameterException", "InvalidRequestException", "LimitExceededException",
    "ValidationError", "ValidationException",
])
@pytest.mark.asyncio
async def test_received_precreation_refusal_releases_capacity_but_never_reuses_original(metadata, code):
    cloud = RespondingAws(received(code))
    store = custody(metadata, cloud)
    refused_ref, deadline = uuid.uuid4().hex, int(time.time()) + 600
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_create_refused$") as caught:
        await store.create(secret_ref=refused_ref, value=VALUE, expires_at=deadline)
    assert CANARY not in str(caught.value) and cloud.resources == {}
    original = await store._metadata.read_original(secret_ref=refused_ref)
    assert (original.state, original.attempt_state, original.arn) == ("terminal", "refused", None)
    status = await store._metadata.admission_status()
    assert status == dict(unknown=0, legacy_unknown=0, unstarted=0, unresolved=0, capacity=1, saturated=False)
    assert not await store.create(secret_ref=refused_ref, value=VALUE, expires_at=deadline + 10)
    assert await store.get(secret_ref=refused_ref) is None
    await store.delete(secret_ref=refused_ref)
    for _ in range(3):
        await cleanup_due(store._metadata)
        assert await store.drain_cleanup(limit=1000) == 0
    assert len(cloud.calls) == 1, "no redispatch or negative-GET closure of refused original"
    cloud.failure = None
    assert await custody(metadata, cloud).create(secret_ref=uuid.uuid4().hex, value=VALUE, expires_at=deadline)
    assert len(cloud.calls) == 2 and len(cloud.resources) == 1
    assert await store._metadata.read_original(secret_ref=refused_ref) == original


@pytest.mark.parametrize("failure", [
    received(status=500), received(status=503), received(status=None), received(status="400"),
    received(status=True), received(retries=1), received(retries=None), received(retries=False),
    received("InternalServiceError"), received("EncryptionFailure"), received("DecryptionFailure"),
    received("ResourceExistsException"), received("ResourceNotFoundException"), received("unrecognized"),
    received(operation="GetSecretValue"), CloudFailure("AccessDeniedException"),
    TimeoutError(CANARY), OSError(CANARY), asyncio.CancelledError(),
])
@pytest.mark.asyncio
async def test_uncertain_outcome_or_untrusted_error_keeps_capacity_and_never_resends(metadata, failure):
    cloud = RespondingAws(failure)
    store = custody(metadata, cloud)
    ref, deadline = uuid.uuid4().hex, int(time.time()) + 600
    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else RuntimeCloudError
    with pytest.raises(expected) as caught:
        await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert CANARY not in str(caught.value)
    original = await store._metadata.read_original(secret_ref=ref)
    assert original.attempt_state == "unknown"
    cloud.failure = None
    assert not await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    await store.delete(secret_ref=ref)
    await cleanup_due(store._metadata)
    await store.drain_cleanup(limit=1)
    assert (await store._metadata.read_original(secret_ref=ref)).attempt_state == "unknown"
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_capacity_unavailable$"):
        await store.create(secret_ref=uuid.uuid4().hex, value=VALUE, expires_at=deadline)
    status = await store._metadata.admission_status()
    assert status == dict(unknown=1, legacy_unknown=0, unstarted=0, unresolved=1, capacity=1, saturated=True)
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1
    assert CANARY not in json.dumps(status) and ref not in json.dumps(status)


@pytest.mark.asyncio
async def test_late_received_refusal_atomically_closes_claimed_discovery_and_rejects_stale_worker(metadata):
    cloud = RespondingAws(received(status=403))
    store = custody(metadata, cloud)
    ref, deadline, claims = uuid.uuid4().hex, int(time.time()) + 600, []
    async def retire_and_claim():
        await store.delete(secret_ref=ref)
        claims.extend(await store._metadata.claim_cleanup(limit=1))
    cloud.before_create = retire_and_claim
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_create_refused$"):
        await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert len(claims) == 1
    assert not await store._metadata.settle_cleanup(claims[0], outcome="retry")
    assert await store._metadata.claim_cleanup(limit=1) == ()
    async with metadata._pool.acquire() as connection:
        row = await connection.fetchrow(f"SELECT * FROM {metadata._cleanup} WHERE secret_ref = $1", ref)
    assert row["state"] == "reconciled" and row["claim_token"] is None and row["claim_until"] is None
    assert (await store._metadata.admission_status())["unresolved"] == 0
    assert len(cloud.calls) == 1


@pytest.mark.asyncio
async def test_refusal_receipt_commit_failure_stays_unknown_and_cannot_redispatch(metadata, monkeypatch):
    cloud, deadline, ref = RespondingAws(received()), int(time.time()) + 600, uuid.uuid4().hex
    store = custody(metadata, cloud)
    async def unavailable(original):
        raise RuntimeMetadataError("runtime_secret_metadata_unavailable")
    monkeypatch.setattr(store._metadata, "record_create_refusal", unavailable)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert (await store._metadata.read_original(secret_ref=ref)).attempt_state == "unknown"
    assert not await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert len(cloud.calls) == 1 and (await store._metadata.admission_status())["unknown"] == 1


@pytest.mark.parametrize("change", ["incarnation", "request_digest", "creation_token", "expires_at", "secret_name"])
@pytest.mark.asyncio
async def test_refusal_is_exact_original_cas_and_cannot_erase_another_attempt(metadata, change):
    original = await reserve(metadata)
    replacement = "c" * (64 if change == "request_digest" else 32)
    if change == "expires_at": replacement = original.expires_at + 1
    if change == "secret_name": replacement = original.secret_name + "-replacement"
    assert not await metadata.record_create_refusal(replace(original, **{change: replacement}))
    assert (await metadata.read_original(secret_ref=original.secret_ref)).attempt_state == "unknown"
    assert await metadata.record_create_refusal(original)
    assert not await metadata.begin_create(original)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await publish(metadata, original)


@pytest.mark.asyncio
async def test_observed_or_legacy_attempt_cannot_be_reclassified_as_refused(metadata):
    observed = await reserve(metadata)
    assert await publish(metadata, observed)
    assert not await metadata.record_create_refusal(observed)
    assert (await metadata.read_active(secret_ref=observed.secret_ref)).attempt_state == "observed"
    legacy = await reserve(metadata)
    async with metadata._pool.acquire() as connection:
        await connection.execute(f"UPDATE {metadata._records} SET attempt_state = 'legacy_unknown' WHERE secret_ref = $1",
                                 legacy.secret_ref)
    legacy = await metadata.read_original(secret_ref=legacy.secret_ref)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await metadata.record_create_refusal(legacy)
    assert (await metadata.admission_status())["legacy_unknown"] == 1


@pytest.mark.asyncio
async def test_refused_terminal_and_attempt_guards_reject_revival_rearm_or_late_pin(metadata):
    import asyncpg
    original = await reserve(metadata)
    assert await metadata.record_create_refusal(original)
    async with metadata._pool.acquire() as connection:
        for update in ("state = 'reserved'", "attempt_state = 'unknown'", "attempt_state = 'unstarted'",
                       f"arn = '{arn(original)}', version_id = '{original.creation_token}'"):
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(f"UPDATE {metadata._records} SET {update} WHERE secret_ref = $1", original.secret_ref)
    assert (await metadata.read_original(secret_ref=original.secret_ref)).attempt_state == "refused"


@pytest.mark.parametrize("status", [403, 429])
def test_real_received_denial_statuses_are_accepted_but_malformed_codes_are_not(status):
    assert _received_create_refusal(received(status=status))
    response = received()
    response.response["Error"]["Code"] = {"untrusted": "AccessDeniedException"}
    assert not _received_create_refusal(response)


@pytest.mark.parametrize("phase", ["enter", "exit"])
@pytest.mark.asyncio
async def test_client_context_error_is_not_mistaken_for_received_create_refusal(metadata, phase):
    cloud, ref, deadline = SyntheticAws(), uuid.uuid4().hex, int(time.time()) + 600
    store, fail = custody(metadata, cloud), [True]
    @asynccontextmanager
    async def client():
        if phase == "enter" and fail[0]:
            fail[0] = False
            raise received()
        yield cloud
        if phase == "exit" and fail[0]:
            fail[0] = False
            raise received()
    store._client_factory = client
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$"):
        await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    original = await store._metadata.read_original(secret_ref=ref)
    assert original.attempt_state == ("unstarted" if phase == "enter" else "unknown")
    assert not await store.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    if phase == "exit":
        assert len(cloud.resources) == 1 and await store.get(secret_ref=ref) == VALUE
        assert (await store._metadata.read_active(secret_ref=ref)).attempt_state == "observed"
    else:
        assert cloud.resources == {}
    assert len([call for call in cloud.calls if call[0] == "create"]) == (0 if phase == "enter" else 1)


@pytest.mark.asyncio
async def test_explicit_pre_refusal_schema_upgrade_preserves_old_unknown_then_immutable_refusal(metadata):
    schema = metadata._records.split('"')[1]
    original = await reserve(metadata)
    async with metadata._pool.acquire() as connection:
        await connection.execute(f"ALTER TABLE {metadata._records} DROP CONSTRAINT runtime_secret_records_attempt_state_check")
        await connection.execute(f"ALTER TABLE {metadata._records} ADD CONSTRAINT runtime_secret_records_attempt_state_check "
                                 "CHECK (attempt_state IN ('unstarted','unknown','observed','legacy_unknown'))")
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await metadata.record_create_refusal(original)
    assert await metadata.read_original(secret_ref=original.secret_ref) == original
    await create_runtime_metadata_schema(metadata._pool, schema=schema)
    assert await metadata.record_create_refusal(original)
    refused = await metadata.read_original(secret_ref=original.secret_ref)
    await create_runtime_metadata_schema(metadata._pool, schema=schema)
    assert await metadata.read_original(secret_ref=original.secret_ref) == refused
    assert refused.incarnation == original.incarnation and refused.expires_at == original.expires_at

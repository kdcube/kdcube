# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Real PG fences with a synthetic AWS actor, not cloud qualification."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets.runtime_aws import RuntimeAwsStore, RuntimeCloudError, RuntimeAwsClientFactory
from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError
from kdcube_ai_app.infra.secrets.tests.test_runtime_pg_metadata import metadata, cleanup_due

VALUE = "synthetic-aws-original-no-live-credential"
COMMITMENT_KEY = hashlib.sha256(b"synthetic-service-only-test-key").digest()


class CloudFailure(Exception):
    def __init__(self, code):
        super().__init__("synthetic-cloud-canary-do-not-expose")
        self.response = {"Error": {"Code": code, "Message": str(self)}}


class SyntheticAws:
    """Explicit API-shape actor. Its success is not IAM or AWS evidence."""

    def __init__(self):
        self.resources = {}
        self.calls = []
        self.lose_create_response = False
        self.before_create = None
        self.after_get = None
        self.response_change = None
        self.create_failure = None
        self.lose_delete_response = False
        # Explicit test transport input, not an AWS qualification seam.
        self.meta = SimpleNamespace(config=SimpleNamespace(retries={"total_max_attempts": 1}))

    @asynccontextmanager
    async def client(self):
        yield self

    def find(self, secret_id):
        for resource in self.resources.values():
            if secret_id in (resource["Name"], resource["ARN"]):
                return resource
        raise CloudFailure("ResourceNotFoundException")

    async def create_secret(self, **args):
        self.calls.append(("create", dict(args)))
        if self.before_create is not None:
            await self.before_create()
        if self.create_failure is not None:
            raise CloudFailure(self.create_failure)
        name, version = args["Name"], args["ClientRequestToken"]
        value = args["SecretString"] if "SecretString" in args else args["SecretBinary"]
        if name in self.resources:
            original = self.resources[name]
            if original["versions"].get(version) != value:
                raise CloudFailure("ResourceExistsException")
        else:
            original = {"Name": name,
                        "ARN": f"arn:aws:secretsmanager:eu-central-1:123456789012:secret:{name}-Ab1234",
                        "versions": {version: value}, "current": version, "deleting": False}
            self.resources[name] = original
        result = {"Name": name, "ARN": original["ARN"], "VersionId": version}
        if self.lose_create_response:
            self.lose_create_response = False
            raise TimeoutError("synthetic-cloud-canary-do-not-expose")
        return self.response_change("create", result) if self.response_change else result

    async def get_secret_value(self, **args):
        self.calls.append(("get", dict(args)))
        resource = self.find(args["SecretId"])
        # No default-current fallback: every test read requires a VersionId.
        assert set(args) == {"SecretId", "VersionId"}
        version = args["VersionId"]
        if version not in resource["versions"]:
            raise CloudFailure("ResourceNotFoundException")
        result = {"Name": resource["Name"], "ARN": resource["ARN"],
                  "VersionId": version}
        value = resource["versions"][version]
        result["SecretBinary" if type(value) is bytes else "SecretString"] = value
        if self.after_get:
            await self.after_get()
        return self.response_change("get", result) if self.response_change else result

    async def delete_secret(self, **args):
        self.calls.append(("delete", dict(args)))
        assert set(args) == {"SecretId", "ForceDeleteWithoutRecovery"}
        assert args["SecretId"].startswith("arn:") and args["ForceDeleteWithoutRecovery"] is True
        resource = self.find(args["SecretId"])
        resource["deleting"] = True  # Accepted, not physically removed yet.
        if self.lose_delete_response:
            self.lose_delete_response = False
            raise TimeoutError("synthetic-delete-response-lost")
        return {"Name": resource["Name"], "ARN": resource["ARN"]}

    async def describe_secret(self, **args):
        self.calls.append(("describe", dict(args)))
        resource = self.find(args["SecretId"])
        return {"Name": resource["Name"], "ARN": resource["ARN"]}


def store(metadata, cloud):
    return RuntimeAwsStore(metadata=metadata, client_factory=cloud.client,
                           account_id="123456789012", region="eu-central-1", commitment_key=COMMITMENT_KEY)


async def created(metadata, cloud, *, value=VALUE):
    custody = store(metadata, cloud)
    ref, deadline = uuid.uuid4().hex, int(time.time()) + 600
    assert await custody.create(secret_ref=ref, value=value, expires_at=deadline)
    return custody, ref, deadline


@pytest.mark.asyncio
async def test_create_collisions_false_and_changed_current_never_changes_pinned_read(metadata):
    cloud = SyntheticAws()
    custody, ref, deadline = await created(metadata, cloud)
    assert not await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert not await custody.create(secret_ref=ref, value=VALUE + "-different", expires_at=deadline + 600)
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1
    original = await metadata.read_active(secret_ref=ref)
    resource = cloud.resources[original.secret_name]
    resource["versions"]["b" * 32] = "synthetic-new-current"
    resource["current"] = "b" * 32
    assert await store(metadata, cloud).get(secret_ref=ref) == VALUE
    assert cloud.calls[-1] == ("get", {"SecretId": original.arn,
                                     "VersionId": original.creation_token})
    assert original.expires_at == deadline


@pytest.mark.asyncio
async def test_different_value_contention_has_one_original_logical_creation(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    values = [VALUE, VALUE + "-contender"]
    outcomes = await asyncio.gather(*[
        custody.create(secret_ref=ref, value=value, expires_at=deadline) for value in values
    ])
    assert sorted(outcomes) == [False, True]
    assert await custody.get(secret_ref=ref) == values[outcomes.index(True)]
    assert len(cloud.resources) == 1


@pytest.mark.asyncio
async def test_identical_contention_has_one_true_and_one_stable_cloud_version(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    outcomes = await asyncio.gather(*[
        custody.create(secret_ref=ref, value=VALUE, expires_at=deadline) for _ in range(8)
    ])
    assert sum(outcomes) == 1
    original = await metadata.read_active(secret_ref=ref)
    assert cloud.resources[original.secret_name]["versions"] == {original.creation_token: VALUE}


@pytest.mark.parametrize("recovery", ["create", "get"])
@pytest.mark.asyncio
async def test_lost_create_response_recovers_original_version_without_new_identity(metadata, recovery):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    cloud.lose_create_response = True
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$"):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    original = await metadata.read_original(secret_ref=ref)
    assert original.state == "reserved"
    fresh = store(metadata, cloud)
    if recovery == "create":
        assert not await fresh.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert await fresh.get(secret_ref=ref) == VALUE
    active = await metadata.read_active(secret_ref=ref)
    assert active.incarnation == original.incarnation
    assert active.creation_token == original.creation_token and active.expires_at == deadline
    assert len(cloud.resources) == 1
    assert len(cloud.resources[active.secret_name]["versions"]) == 1
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1
    assert active.attempt_state == "observed"


@pytest.mark.asyncio
async def test_unresolved_absence_is_unavailable_not_missing_and_cleanup_remains_durable(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    cloud.create_failure = "InternalServiceError"
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$"):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    original = await metadata.read_original(secret_ref=ref)
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$"):
        await custody.get(secret_ref=ref)
    assert (await metadata.read_original(secret_ref=ref)).state == "reserved"
    await custody.delete(secret_ref=ref)
    assert await custody.get(secret_ref=ref) is None
    assert await custody.drain_cleanup(limit=1) == 1
    assert await metadata.claim_cleanup(limit=1) == ()
    await cleanup_due(metadata)
    claim, = await metadata.claim_cleanup(limit=1)
    assert claim.phase == "reconcile" and claim.incarnation == original.incarnation
    assert claim.arn is None


@pytest.mark.parametrize("move", ["delete", "expire"])
@pytest.mark.asyncio
async def test_late_create_cannot_publish_after_terminal_or_expiry_fence(metadata, move):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600

    async def fence_before_ack():
        if move == "delete":
            await custody.delete(secret_ref=ref)
        else:
            async with metadata._pool.acquire() as connection:
                await connection.execute(f"UPDATE {metadata._records} SET expires_at = 1 WHERE secret_ref = $1", ref)

    cloud.before_create = fence_before_ack
    assert not await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert await custody.get(secret_ref=ref) is None
    assert (await metadata.read_original(secret_ref=ref)).state == "terminal"
    claims = await metadata.claim_cleanup(limit=1000)
    assert {claim.phase for claim in claims} == ({"reconcile", "delete"} if move == "delete" else {"delete"})
    pinned = next(claim for claim in claims if claim.arn is not None)
    assert pinned.arn == next(iter(cloud.resources.values()))["ARN"]


@pytest.mark.parametrize("move", ["delete", "expire"])
@pytest.mark.asyncio
async def test_read_recheck_never_returns_value_after_authority_moves(metadata, move):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)

    async def fence_after_value_fetch():
        if move == "delete":
            await custody.delete(secret_ref=ref)
        else:
            async with metadata._pool.acquire() as connection:
                await connection.execute(f"UPDATE {metadata._records} SET expires_at = 1 WHERE secret_ref = $1", ref)

    cloud.after_get = fence_after_value_fetch
    assert await custody.get(secret_ref=ref) is None


@pytest.mark.parametrize("field,value", [
    ("ARN", "arn:aws:secretsmanager:eu-west-1:123456789012:secret:foreign-Ab1234"),
    ("Name", "foreign-name"), ("VersionId", "b" * 32), ("SecretString", "different-original"),
    ("SecretBinary", b"unexpected-binary"),
])
@pytest.mark.asyncio
async def test_foreign_or_corrupt_value_response_is_not_returned(metadata, field, value):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    cloud.response_change = lambda operation, response: dict(response, **{field: value})
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_metadata_binding_invalid$"):
        await custody.get(secret_ref=ref)


@pytest.mark.asyncio
async def test_foreign_account_create_ack_cannot_pin_or_delete_foreign_resource(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody = store(metadata, cloud)
    cloud.response_change = lambda operation, response: dict(
        response, ARN=response["ARN"].replace(":123456789012:", ":999999999999:"),
    )
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_metadata_binding_invalid$"):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=int(time.time()) + 600)
    assert (await metadata.read_original(secret_ref=ref)).state == "reserved"
    assert not any(call[0] == "delete" for call in cloud.calls)


@pytest.mark.asyncio
async def test_corrupt_active_metadata_arn_is_refused_before_cloud_read(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    async with metadata._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {metadata._records} SET arn = replace(arn, ':123456789012:', ':999999999999:') "
            "WHERE secret_ref = $1", ref,
        )
    before = list(cloud.calls)
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_metadata_binding_invalid$"):
        await custody.get(secret_ref=ref)
    assert cloud.calls == before


@pytest.mark.asyncio
async def test_existing_name_with_wrong_creation_version_is_not_overwritten(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600

    async def preseed_foreign_version():
        original = await metadata.read_original(secret_ref=ref)
        cloud.resources[original.secret_name] = {
            "Name": original.secret_name,
            "ARN": f"arn:aws:secretsmanager:eu-central-1:123456789012:secret:{original.secret_name}-Ab1234",
            "versions": {"b" * 32: "foreign-value"}, "current": "b" * 32,
            "deleting": False,
        }

    cloud.before_create = preseed_foreign_version
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$"):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    assert (await metadata.read_original(secret_ref=ref)).state == "reserved"
    assert next(iter(cloud.resources.values()))["versions"] == {"b" * 32: "foreign-value"}


@pytest.mark.asyncio
async def test_expired_and_missing_reads_do_no_cloud_io_and_purge_is_only_logical(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    async with metadata._pool.acquire() as connection:
        await connection.execute(f"UPDATE {metadata._records} SET expires_at = 1 WHERE secret_ref = $1", ref)
    before = list(cloud.calls)
    assert await custody.get(secret_ref=ref) is None
    assert await custody.get(secret_ref=uuid.uuid4().hex) is None
    await custody.delete(secret_ref=uuid.uuid4().hex)
    assert await custody.purge_expired(now=int(time.time()), limit=1) == 1
    assert cloud.calls == before
    assert len(cloud.resources) == 1
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_purge_invalid$"):
        await custody.purge_expired(now=int(time.time()) + 600, limit=1)


@pytest.mark.asyncio
async def test_cleanup_deletes_full_pins_and_distinguishes_acceptance_from_confirmation(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    original = await metadata.read_active(secret_ref=ref)
    await custody.delete(secret_ref=ref)
    assert await custody.drain_cleanup(limit=2) == 1
    async with metadata._pool.acquire() as connection:
        rows = await connection.fetch(f"SELECT state, phase FROM {metadata._cleanup}")
    assert {(row["state"], row["phase"]) for row in rows} == {
        ("confirm_pending", "confirm"),
    }
    assert cloud.resources[original.secret_name]["deleting"]
    assert ("delete", {"SecretId": original.arn, "ForceDeleteWithoutRecovery": True}) in cloud.calls
    del cloud.resources[original.secret_name]  # Synthetic physical deletion event.
    assert await custody.drain_cleanup(limit=2) == 1
    async with metadata._pool.acquire() as connection:
        rows = await connection.fetch(f"SELECT state, phase FROM {metadata._cleanup}")
    assert {(row["state"], row["phase"]) for row in rows} == {
        ("deleted", "confirm"),
    }
    assert await custody.drain_cleanup(limit=1000) == 0


@pytest.mark.asyncio
async def test_reconciliation_positive_original_read_closes_without_a_second_create_or_name_delete(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    cloud.lose_create_response = True
    with pytest.raises(RuntimeCloudError):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    original = await metadata.read_original(secret_ref=ref)
    await custody.delete(secret_ref=ref)
    assert await custody.drain_cleanup(limit=1) == 1
    assert await custody.drain_cleanup(limit=2) == 1
    resource = cloud.resources[original.secret_name]
    old_arn = resource["ARN"]
    del cloud.resources[original.secret_name]
    assert await custody.drain_cleanup(limit=2) == 1
    assert await custody.drain_cleanup(limit=1000) == 0
    assert not await custody.create(secret_ref=ref, value=VALUE, expires_at=deadline)
    deleted_ids = [args["SecretId"] for op, args in cloud.calls if op == "delete"]
    assert deleted_ids == [old_arn]
    assert original.secret_name not in deleted_ids
    assert await custody.get(secret_ref=ref) is None
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1
    async with metadata._pool.acquire() as connection:
        assert set(await connection.fetchval(
            f"SELECT array_agg(state) FROM {metadata._cleanup}",
        )) == {"reconciled", "deleted"}


@pytest.mark.parametrize("code", ["AccessDeniedException", "synthetic-sensitive-code", "InternalServiceError"])
@pytest.mark.asyncio
async def test_cloud_errors_are_finite_and_do_not_expose_exception_text(metadata, code):
    cloud = SyntheticAws()
    cloud.create_failure = code
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_outcome_unknown$") as error:
        await store(metadata, cloud).create(secret_ref=uuid.uuid4().hex, value=VALUE,
                                            expires_at=int(time.time()) + 600)
    assert "canary" not in str(error.value) and "sensitive" not in error.value.cloud_code
    assert error.value.__suppress_context__


@pytest.mark.asyncio
async def test_qualifier_stays_closed_without_cloud_or_metadata_io(metadata):
    cloud = SyntheticAws()
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        await store(metadata, cloud).qualify()
    assert cloud.calls == []


@pytest.mark.parametrize("value", [None, "\ud800", "x" * 65537],
                         ids=["not-string", "invalid-unicode", "over-byte-limit"])
@pytest.mark.asyncio
async def test_invalid_value_refuses_before_reservation_or_cloud_io(metadata, value):
    cloud = SyntheticAws()
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_value_invalid$"):
        await store(metadata, cloud).create(secret_ref=uuid.uuid4().hex, value=value,
                                            expires_at=int(time.time()) + 600)
    assert cloud.calls == []
    async with metadata._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {metadata._records}") == 0


@pytest.mark.parametrize("value", ["", "\0", "x" * 65536, "é" * 32768],
                         ids=["empty", "nul", "max-ascii", "max-utf8"])
@pytest.mark.asyncio
async def test_empty_and_full_byte_limit_strings_round_trip_without_current_fallback(metadata, value):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud, value=value)
    assert await custody.get(secret_ref=ref) == value
    wire = cloud.calls[0][1]
    if value == "":
        assert wire["SecretBinary"] == b"\0" and "SecretString" not in wire
    else:
        assert wire["SecretString"] == value and "SecretBinary" not in wire


@pytest.mark.asyncio
async def test_arbitrary_binary_cannot_impersonate_the_committed_empty_string(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud, value="")
    cloud.response_change = lambda operation, response: dict(response, SecretBinary=b"unexpected-binary")
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_metadata_binding_invalid$"):
        await custody.get(secret_ref=ref)


def test_installed_sdk_parser_returns_decoded_binary_bytes_without_cloud_io():
    from botocore.parsers import create_parser
    from botocore.session import get_session

    service = get_session().get_service_model("secretsmanager")
    shape = service.operation_model("GetSecretValue").output_shape
    response = create_parser("json").parse({
        "status_code": 200, "headers": {},
        "body": json.dumps({"SecretBinary": "AA=="}).encode(),
    }, shape)
    assert response["SecretBinary"] == b"\0"


@pytest.mark.parametrize("retries", [{}, {"max_attempts": 0}, {"total_max_attempts": 2},
                                    {"total_max_attempts": True}],
                         ids=["missing", "ambiguous-retry-key", "automatic-retry", "boolean"])
@pytest.mark.asyncio
async def test_effective_client_retries_refuse_before_dispatch_not_just_qualification(metadata, retries):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    cloud.meta.config.retries = retries
    custody = store(metadata, cloud)
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        await custody.create(secret_ref=ref, value=VALUE, expires_at=int(time.time()) + 600)
    assert cloud.calls == []
    assert (await metadata.read_original(secret_ref=ref)).attempt_state == "unstarted"
    await custody.delete(secret_ref=ref)
    assert await metadata.claim_cleanup(limit=1000) == ()


@pytest.mark.asyncio
async def test_absence_during_inflight_create_does_not_close_or_authorize_resend(metadata):
    cloud, ref = SyntheticAws(), uuid.uuid4().hex
    custody, deadline = store(metadata, cloud), int(time.time()) + 600
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_wire_attempt():
        entered.set()
        await release.wait()

    cloud.before_create = delayed_wire_attempt
    creator = asyncio.create_task(custody.create(secret_ref=ref, value=VALUE, expires_at=deadline))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await custody.delete(secret_ref=ref)
        assert await custody.drain_cleanup(limit=1) == 1  # Absence, not closure.
        assert not await store(metadata, cloud).create(secret_ref=ref, value=VALUE, expires_at=deadline)
        assert len([call for call in cloud.calls if call[0] == "create"]) == 1
        async with metadata._pool.acquire() as connection:
            assert await connection.fetchval(f"SELECT state FROM {metadata._cleanup}") == "pending"
        release.set()
        assert not await asyncio.wait_for(creator, timeout=2)
    finally:
        release.set()
        await creator
    original = await metadata.read_original(secret_ref=ref)
    assert original.attempt_state == "observed" and original.state == "terminal"
    await cleanup_due(metadata)
    assert await custody.drain_cleanup(limit=1000) == 2
    del cloud.resources[original.secret_name]
    assert await custody.drain_cleanup(limit=1000) == 1
    assert await custody.drain_cleanup(limit=1000) == 0
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1


def test_trusted_client_factory_supplies_actual_no_retry_sdk_config_without_cloud_io():
    calls = []

    class Session:
        def client(self, service, **kwargs):
            calls.append((service, kwargs))
            return "synthetic-context"

    factory = RuntimeAwsClientFactory(Session(), region="eu-central-1")
    assert factory() == "synthetic-context"
    service, options = calls[0]
    assert service == "secretsmanager" and options["region_name"] == "eu-central-1"
    assert options["config"].retries == {"total_max_attempts": 1, "mode": "standard"}


@pytest.mark.asyncio
async def test_installed_async_sdk_effective_config_disables_resends_without_network():
    import aioboto3

    session = aioboto3.Session(aws_access_key_id="synthetic-no-live-key",
                             aws_secret_access_key="synthetic-no-live-secret")
    async with RuntimeAwsClientFactory(session, region="eu-central-1")() as client:
        assert client.meta.config.retries["total_max_attempts"] == 1
        assert client.meta.config.retries["mode"] == "standard"


@pytest.mark.asyncio
async def test_lost_delete_ack_followed_by_full_arn_absence_still_requires_confirmation(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    original = await metadata.read_active(secret_ref=ref)
    await custody.delete(secret_ref=ref)
    cloud.lose_delete_response = True
    assert await custody.drain_cleanup(limit=1000) == 1
    assert await custody.drain_cleanup(limit=1000) == 0  # Backoff, not completion.
    del cloud.resources[original.secret_name]  # Physical synthetic deletion after lost ACK.
    await cleanup_due(metadata)
    assert await custody.drain_cleanup(limit=1000) == 1
    async with metadata._pool.acquire() as connection:
        row = await connection.fetchrow(f"SELECT state, phase FROM {metadata._cleanup}")
        assert (row["state"], row["phase"]) == ("confirm_pending", "confirm")
    assert await custody.drain_cleanup(limit=1000) == 1
    assert cloud.calls[-1] == ("describe", {"SecretId": original.arn})
    assert await custody.drain_cleanup(limit=1000) == 0


@pytest.mark.parametrize("key", [None, b"short", "not-bytes", b"x" * 4097],
                         ids=["missing", "short", "wrong-type", "over-limit"])
@pytest.mark.asyncio
async def test_missing_or_invalid_service_commitment_key_refuses_before_io(metadata, key):
    cloud = SyntheticAws()
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        RuntimeAwsStore(metadata=metadata, client_factory=cloud.client,
                        account_id="123456789012", region="eu-central-1", commitment_key=key)
    assert cloud.calls == []


@pytest.mark.asyncio
async def test_low_entropy_value_commitment_is_keyed_and_wrong_key_never_returns_or_replaces(metadata):
    cloud = SyntheticAws()
    custody, ref, deadline = await created(metadata, cloud, value="0")
    original = await metadata.read_active(secret_ref=ref)
    coordinates = [original.namespace, ref, deadline, hashlib.sha256(b"0").hexdigest()]
    canonical = json.dumps(coordinates, separators=(",", ":")).encode()
    assert original.request_digest == hmac.new(COMMITMENT_KEY, canonical, hashlib.sha256).hexdigest()
    assert original.request_digest != hashlib.sha256(canonical).hexdigest()
    wrong_key = RuntimeAwsStore(metadata=metadata, client_factory=cloud.client,
                                account_id="123456789012", region="eu-central-1",
                                commitment_key=hashlib.sha256(b"synthetic-other-service-key").digest())
    assert not await wrong_key.create(secret_ref=ref, value="0", expires_at=deadline)
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_metadata_binding_invalid$"):
        await wrong_key.get(secret_ref=ref)
    assert await store(metadata, cloud).get(secret_ref=ref) == "0"
    assert len([call for call in cloud.calls if call[0] == "create"]) == 1
    async with metadata._pool.acquire() as connection:
        raw = await connection.fetchval(f"SELECT row_to_json(r)::text FROM {metadata._records} r")
    assert COMMITMENT_KEY.hex() not in raw


@pytest.mark.asyncio
async def test_known_resource_cleanup_cannot_touch_replacement_full_arn(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    original = await metadata.read_active(secret_ref=ref)
    await custody.delete(secret_ref=ref)
    replacement = cloud.resources[original.secret_name]
    replacement["ARN"] = original.arn[:-6] + "Xy9876"
    assert await custody.drain_cleanup(limit=1000) == 1  # Original full ARN absent.
    assert await custody.drain_cleanup(limit=1000) == 1  # Separate full-ARN confirmation.
    assert await custody.drain_cleanup(limit=1000) == 0
    assert not replacement["deleting"]
    assert [args["SecretId"] for op, args in cloud.calls if op == "delete"] == [original.arn]
    assert await custody.get(secret_ref=ref) is None


@pytest.mark.asyncio
async def test_corrupt_cleanup_pin_refuses_before_replacement_cloud_io(metadata):
    cloud = SyntheticAws()
    custody, ref, _ = await created(metadata, cloud)
    original = await metadata.read_active(secret_ref=ref)
    await custody.delete(secret_ref=ref)
    async with metadata._pool.acquire() as connection:
        await connection.execute(f"UPDATE {metadata._cleanup} SET arn = $1", original.arn[:-6] + "Xy9876")
    before = list(cloud.calls)
    assert await custody.drain_cleanup(limit=1000) == 1
    assert cloud.calls == before

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Trusted asynchronous AWS custody operations over value-free PG fences.

This component is not wired into service startup yet. Its qualifier remains
closed; supplying a client or namespace is not deployment/IAM evidence.
Only the trusted SecretsService composition may supply its pool, client,
account and region. Request data never selects those capabilities.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from kdcube_ai_app.infra.secrets.runtime_pg_metadata import PostgresRuntimeCustodyMetadata
from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError

_REF = re.compile(r"[0-9a-f]{32}")
_MAX_VALUE_BYTES = 65536


class RuntimeAwsClientFactory:
    """Trusted service SDK composition, not a caller-provided retry label."""

    def __init__(self, session, *, region: str):
        from botocore.config import Config

        self._session = session
        self._region = region
        self._config = Config(retries={"total_max_attempts": 1, "mode": "standard"})

    def __call__(self):
        return self._session.client("secretsmanager", region_name=self._region, config=self._config)


class RuntimeCloudError(RuntimeError):
    """Fixed public reason; a whitelisted cloud code is internal control flow."""

    def __init__(self, reason: str, *, cloud_code: str = "unavailable"):
        super().__init__(reason)
        self.cloud_code = cloud_code


def _commitment(key: bytes, namespace: str, secret_ref: str, value: str, expires_at: int) -> str:
    coordinates = [namespace, secret_ref, expires_at,
                   hashlib.sha256(value.encode("utf-8")).hexdigest()]
    return hmac.new(key, json.dumps(coordinates, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


class RuntimeAwsStore:
    """One host-enrolled namespace; no overwrite or arbitrary-current read."""

    def __init__(self, *, metadata: PostgresRuntimeCustodyMetadata, client_factory,
                 account_id: str, region: str, partition: str = "aws", commitment_key: bytes | None = None):
        if (type(metadata) is not PostgresRuntimeCustodyMetadata
                or not callable(client_factory) or type(account_id) is not str
                or re.fullmatch(r"[0-9]{12}", account_id) is None
                or type(region) is not str or re.fullmatch(r"[a-z0-9-]{1,64}", region) is None
                or type(partition) is not str or re.fullmatch(r"[a-z][a-z0-9-]{1,31}", partition) is None
                or type(commitment_key) is not bytes or not 32 <= len(commitment_key) <= 4096):
            raise RuntimeCloudError("runtime_secret_storage_unavailable")
        self._metadata = metadata
        self._client_factory = client_factory
        self._arn_prefix = f"arn:{partition}:secretsmanager:{region}:{account_id}:secret:"
        # Trusted persistent service secret, never request JSON or PostgreSQL
        # metadata. No default/derived/namespace-only key is acceptable.
        self._commitment_key = commitment_key

    async def qualify(self) -> None:
        # Do not lift this before actual service/pool/role composition and the
        # complete guarantee matrix exist. No cloud availability probe can
        # establish those guarantees by itself.
        raise RuntimeCloudError("runtime_secret_storage_unavailable")

    @staticmethod
    def _reference(secret_ref):
        if type(secret_ref) is not str or _REF.fullmatch(secret_ref) is None:
            raise RuntimeCloudError("runtime_secret_reference_invalid")

    @staticmethod
    def _value_input(value, expires_at):
        try:
            if (type(value) is not str
                    or len(value.encode("utf-8")) > _MAX_VALUE_BYTES
                    or type(expires_at) is not int or expires_at <= 0):
                raise ValueError
        except (ValueError, UnicodeError):
            raise RuntimeCloudError("runtime_secret_value_invalid") from None

    async def _cloud(self, operation: str, *, dispatch_record=None, **arguments) -> dict | None:
        try:
            async with self._client_factory() as client:
                if operation == "create_secret":
                    # A later automatic retry could recreate the name after
                    # cleanup. Inspect the actual SDK client's effective config,
                    # not a request flag or an availability/qualification label.
                    retries = getattr(getattr(getattr(client, "meta", None), "config", None), "retries", None)
                    if (type(retries) is not dict
                            or type(retries.get("total_max_attempts")) is not int
                            or retries["total_max_attempts"] != 1):
                        raise RuntimeCloudError("runtime_secret_storage_unavailable")
                    if not await self._metadata.begin_create(dispatch_record):
                        return None
                response = await getattr(client, operation)(**arguments)
            if type(response) is not dict:
                raise ValueError
            return response
        except (RuntimeMetadataError, RuntimeCloudError):
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            error = response.get("Error") if type(response) is dict else None
            code = error.get("Code") if type(error) is dict else None
            if type(code) is not str or code not in {
                "ResourceNotFoundException", "ResourceExistsException",
                "AccessDeniedException", "AccessDenied", "InvalidRequestException",
            }:
                code = "unavailable"
            raise RuntimeCloudError("runtime_secret_outcome_unknown", cloud_code=code) from None

    def _pin_arn(self, original, full_arn):
        pattern = re.escape(self._arn_prefix + original.secret_name) + r"-[A-Za-z0-9]{6}"
        if type(full_arn) is not str or re.fullmatch(pattern, full_arn) is None:
            raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")

    def _ack(self, original, response):
        full_arn, version = response.get("ARN"), response.get("VersionId")
        self._pin_arn(original, full_arn)
        if (response.get("Name") != original.secret_name
                or type(version) is not str or version != original.creation_token
                or (original.arn is not None and full_arn != original.arn)):
            raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
        return full_arn, version

    async def _fetch_original(self, original):
        if original.attempt_state == "unstarted":
            raise RuntimeCloudError("runtime_secret_outcome_unknown")
        # Name lookup is only for an unresolved reservation. The creation
        # VersionId is always explicit. Active reads use the pinned full ARN.
        secret_id = original.arn if original.arn is not None else original.secret_name
        if original.arn is not None:
            self._pin_arn(original, original.arn)
            if original.version_id != original.creation_token:
                raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
        response = await self._cloud("get_secret_value", SecretId=secret_id,
                                     VersionId=original.creation_token)
        full_arn, version = self._ack(original, response)
        if set(response) & {"SecretString", "SecretBinary"} == {"SecretBinary"}:
            # AWS SecretString has a nonempty minimum. Only the empty public
            # string uses this one-byte binary marker; its immutable request
            # commitment below prevents it from impersonating another value.
            if type(response["SecretBinary"]) is not bytes or response["SecretBinary"] != b"\0":
                raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
            value = ""
        elif set(response) & {"SecretString", "SecretBinary"} == {"SecretString"}:
            value = response["SecretString"]
        else:
            raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
        self._value_input(value, original.expires_at)
        if not hmac.compare_digest(
                _commitment(self._commitment_key, original.namespace, original.secret_ref, value,
                            original.expires_at), original.request_digest):
            raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
        return value, full_arn, version

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        self._reference(secret_ref)
        self._value_input(value, expires_at)
        digest = _commitment(self._commitment_key, self._metadata.namespace, secret_ref, value, expires_at)
        original = await self._metadata.reserve(secret_ref=secret_ref,
                                                request_digest=digest, expires_at=expires_at)
        if (not original.created or original.state == "terminal" or original.request_digest != digest
                or original.expires_at != expires_at or original.state == "active"):
            return False
        current = await self._metadata.read_original(secret_ref=secret_ref, live_only=True)
        if current is None:
            return False
        if current.state == "active":
            # Another exact contender completed the same reservation; the
            # original insertion owner is still the only True create caller.
            return original.created
        try:
            response = await self._cloud(
                "create_secret", dispatch_record=current, Name=original.secret_name,
                ClientRequestToken=original.creation_token,
                **({"SecretString": value} if value else {"SecretBinary": b"\0"}),
                Tags=[{"Key": "kdcube-runtime-expires-at", "Value": str(original.expires_at)}],
            )
            if response is None:
                return False
            full_arn, version = self._ack(original, response)
        except RuntimeCloudError as exc:
            if exc.cloud_code != "ResourceExistsException":
                raise
            # An existing name is not proof of our original. Recover only its
            # creation version and verify the immutable value commitment.
            _, full_arn, version = await self._fetch_original(original)
        published = await self._metadata.publish_original(
            secret_ref=secret_ref, incarnation=original.incarnation,
            request_digest=digest, arn=full_arn, version_id=version,
        )
        return published and original.created

    async def get(self, *, secret_ref: str) -> str | None:
        self._reference(secret_ref)
        original = await self._metadata.read_original(secret_ref=secret_ref, live_only=True)
        if original is None:
            return None
        value, full_arn, version = await self._fetch_original(original)
        if original.state == "reserved" and not await self._metadata.publish_original(
            secret_ref=secret_ref, incarnation=original.incarnation,
            request_digest=original.request_digest, arn=full_arn, version_id=version,
        ):
            return None
        active = await self._metadata.read_active(secret_ref=secret_ref)
        if active is None:
            return None
        if (active.arn, active.version_id) != (full_arn, version):
            raise RuntimeCloudError("runtime_secret_metadata_binding_invalid")
        return value if await self._metadata.confirms_active(active) else None

    async def delete(self, *, secret_ref: str) -> None:
        self._reference(secret_ref)
        await self._metadata.retire_reference(secret_ref=secret_ref)

    async def purge_expired(self, *, now: int, limit: int) -> int:
        return await self._metadata.retire_expired(now=now, limit=limit)

    async def drain_cleanup(self, *, limit: int) -> int:
        """Process at most limit durable claims; return claimed, not erased.

        A delete ACK advances to confirmation. A sole dispatch can close on
        positive original-version evidence, never one absent-resource read.
        This is a trusted maintenance operation, not a request-supplied job.
        """
        claims = await self._metadata.claim_cleanup(limit=limit)
        for claim in claims:
            outcome = "retry"
            try:
                original = await self._metadata.read_original(secret_ref=claim.secret_ref)
                if (original is None or original.state != "terminal"
                        or original.incarnation != claim.incarnation):
                    raise RuntimeCloudError("runtime_secret_cleanup_binding_invalid")
                if claim.phase == "reconcile":
                    if original.attempt_state != "observed":
                        _, full_arn, version = await self._fetch_original(original)
                        await self._metadata.publish_original(
                            secret_ref=original.secret_ref, incarnation=original.incarnation,
                            request_digest=original.request_digest, arn=full_arn, version_id=version,
                        )
                    observed = await self._metadata.read_original(secret_ref=claim.secret_ref)
                    if observed.attempt_state == "observed":
                        outcome = "reconciled"
                else:
                    self._pin_arn(original, claim.arn)
                    if (claim.version_id != original.creation_token
                            or (claim.arn, claim.version_id) != (original.arn, original.version_id)):
                        raise RuntimeCloudError("runtime_secret_cleanup_binding_invalid")
                    if claim.phase == "delete":
                        try:
                            response = await self._cloud("delete_secret", SecretId=claim.arn,
                                                         ForceDeleteWithoutRecovery=True)
                            if response.get("ARN") != claim.arn or response.get("Name") != original.secret_name:
                                raise RuntimeCloudError("runtime_secret_cleanup_binding_invalid")
                            outcome = "delete_accepted"
                        except RuntimeCloudError as exc:
                            if exc.cloud_code != "ResourceNotFoundException":
                                raise
                            # A lost delete response can leave an already absent
                            # pinned ARN. Advance to a separate confirmation,
                            # without calling absence a delete ACK or erasure.
                            outcome = "delete_absent"
                    elif claim.phase == "confirm":
                        try:
                            response = await self._cloud("describe_secret", SecretId=claim.arn)
                            if response.get("ARN") != claim.arn:
                                raise RuntimeCloudError("runtime_secret_cleanup_binding_invalid")
                        except RuntimeCloudError as exc:
                            if exc.cloud_code != "ResourceNotFoundException":
                                raise
                            outcome = "deleted"
            except RuntimeCloudError:
                # A refusal/unavailable cloud outcome leaves the claim durable.
                # Database errors are not swallowed; the lease will recover.
                pass
            await self._metadata.settle_cleanup(claim, outcome=outcome)
        return len(claims)

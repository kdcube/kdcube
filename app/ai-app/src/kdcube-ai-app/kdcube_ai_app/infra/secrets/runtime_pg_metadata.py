# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Service-owned PostgreSQL fencing for cloud runtime-secret custody.

Only coordinates, commitments and cleanup claims enter these tables. The
trusted service reserves before cloud I/O and pins the returned full ARN and
original creation version. It must separately implement and qualify cloud
operations, pool/role composition and HTTP scope policy. This adapter does
not declare an AWS backend qualified or use Card/session tables.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from kdcube_ai_app.infra.secrets.runtime_contract import valid_namespace
from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError, metadata_tables

_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CLOCK = "floor(extract(epoch FROM clock_timestamp()))::bigint"


@dataclass(frozen=True)
class RuntimeCustodyRecord:
    namespace: str
    secret_ref: str
    incarnation: str
    request_digest: str
    expires_at: int
    creation_token: str
    secret_name: str
    state: str
    arn: str | None
    version_id: str | None
    created: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class RuntimeCleanupClaim:
    namespace: str
    job_id: str
    secret_ref: str
    incarnation: str
    arn: str | None
    version_id: str | None
    phase: str
    claim_token: str


def _hex(value, pattern) -> None:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")


def _bound_integer(value, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")


class PostgresRuntimeCustodyMetadata:
    """One enrolled namespace, immutable reservation and terminal read fence.

    Construction is a trusted service capability, not a request model. The
    caller performs per-operation read/write authorization before using it.
    The supplied role must only access this service-owned schema. Migration
    and deployment persistence/role qualification are separate operations.
    """

    def __init__(self, pool, *, schema: str, namespace: str,
                 authorized_namespaces, cloud_prefix: str) -> None:
        self._records, self._cleanup = metadata_tables(schema)
        if (not valid_namespace(namespace)
                or type(authorized_namespaces) not in (tuple, list, set, frozenset)
                or any(not valid_namespace(n) for n in authorized_namespaces)
                or namespace not in authorized_namespaces):
            raise RuntimeMetadataError("runtime_secret_scope_forbidden")
        if (type(cloud_prefix) is not str
                or re.fullmatch(r"[A-Za-z0-9/_+=.@-]{1,300}", cloud_prefix) is None
                or cloud_prefix.startswith("/") or cloud_prefix.endswith("/")
                or not callable(getattr(pool, "acquire", None))):
            raise RuntimeMetadataError("runtime_secret_metadata_configuration_invalid")
        self._pool = pool
        self._namespace = namespace
        self._prefix = cloud_prefix

    @property
    def namespace(self) -> str:
        return self._namespace

    @asynccontextmanager
    async def _transaction(self):
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    yield connection
        except RuntimeMetadataError:
            raise
        except Exception:
            raise RuntimeMetadataError("runtime_secret_metadata_unavailable") from None

    def _name(self, secret_ref: str, incarnation: str) -> str:
        return f"{self._prefix}/{self._namespace}/{secret_ref}/{incarnation}"

    def _record(self, row, *, created=False) -> RuntimeCustodyRecord:
        result = RuntimeCustodyRecord(**{name: row[name] for name in (
            "namespace", "secret_ref", "incarnation", "request_digest", "expires_at",
            "creation_token", "secret_name", "state", "arn", "version_id",
        )}, created=created)
        if result.secret_name != self._name(result.secret_ref, result.incarnation):
            raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
        return result

    async def _locked(self, connection, secret_ref):
        row = await connection.fetchrow(
            f"SELECT * FROM {self._records} "
            "WHERE namespace = $1 AND secret_ref = $2 FOR UPDATE",
            self._namespace, secret_ref,
        )
        if row is None:
            return None
        # A row-lock wait can cross the deadline. Read the server clock after
        # acquiring the lock, not while evaluating the SELECT's projection.
        return dict(row, live=await connection.fetchval(
            f"SELECT $1 > {_CLOCK}", row["expires_at"],
        ))

    async def reserve(self, *, secret_ref: str, request_digest: str,
                      expires_at: int) -> RuntimeCustodyRecord:
        """Insert before cloud I/O; every collision returns the fixed original.

        A different commitment is never allowed to perform recovery I/O. The
        service maps an existing reference to False, and only replays a still
        reserved original when all its immutable inputs match. Terminal rows
        remain permanent value-free fences.
        """
        _hex(secret_ref, _HEX32)
        _hex(request_digest, _HEX64)
        _bound_integer(expires_at, minimum=1, maximum=253402300799)
        incarnation, token = uuid.uuid4().hex, uuid.uuid4().hex
        async with self._transaction() as connection:
            inserted = await connection.fetchval(
                f"INSERT INTO {self._records} (namespace, secret_ref, incarnation, "
                "request_digest, expires_at, creation_token, secret_name, state) "
                f"SELECT $1, $2, $3, $4, $5, $6, $7, 'reserved' WHERE $5 > {_CLOCK} "
                "ON CONFLICT (namespace, secret_ref) DO NOTHING RETURNING secret_ref",
                self._namespace, secret_ref, incarnation, request_digest, expires_at,
                token, self._name(secret_ref, incarnation),
            )
            row = await self._locked(connection, secret_ref)
            if row is None:
                raise RuntimeMetadataError("runtime_secret_expired")
            return self._record(row, created=inserted is not None)

    def _validate_pin(self, record, *, request_digest, arn, version_id):
        if (type(request_digest) is not str or type(version_id) is not str
                or request_digest != record.request_digest or version_id != record.creation_token):
            raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
        pattern = (r"arn:[a-z][a-z0-9-]{1,31}:secretsmanager:[a-z0-9-]{1,64}:"
                   r"[0-9]{12}:secret:" + re.escape(record.secret_name) + r"-[A-Za-z0-9]{6}")
        if type(arn) is not str or re.fullmatch(pattern, arn) is None:
            raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")

    async def _enqueue(self, connection, record, *, arn=None, version_id=None):
        coordinates = [record.namespace, record.secret_ref, record.incarnation, arn, version_id]
        job_id = hashlib.sha256(json.dumps(coordinates, separators=(",", ":")).encode()).hexdigest()
        await connection.execute(
            f"INSERT INTO {self._cleanup} (namespace, job_id, secret_ref, incarnation, "
            "arn, version_id, phase) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (namespace, job_id) DO NOTHING",
            record.namespace, job_id, record.secret_ref, record.incarnation,
            arn, version_id, "reconcile" if arn is None else "delete",
        )

    async def _retire(self, connection, record):
        await connection.execute(
            f"UPDATE {self._records} SET state = 'terminal', updated_at = clock_timestamp() "
            "WHERE namespace = $1 AND secret_ref = $2 AND incarnation = $3",
            record.namespace, record.secret_ref, record.incarnation,
        )
        # Without a completed cloud-attempt ledger an in-flight/unknown create
        # cannot be disproved by one absence read. Reconciliation stays durable
        # even after a known resource's physical deletion is confirmed.
        await self._enqueue(connection, record)
        if record.arn is not None:
            await self._enqueue(connection, record, arn=record.arn, version_id=record.version_id)

    async def publish_original(self, *, secret_ref: str, incarnation: str,
                               request_digest: str, arn: str, version_id: str) -> bool:
        """Pin a trusted original create acknowledgement, never AWSCURRENT.

        A late result after retirement/expiry cannot publish. Its returned full
        ARN/version becomes cleanup work even if it differs from a previously
        deleted ARN with the same creation name. The cloud service must verify
        the response's account/region against its configured client context.
        """
        _hex(secret_ref, _HEX32)
        _hex(incarnation, _HEX32)
        _hex(request_digest, _HEX64)
        async with self._transaction() as connection:
            row = await self._locked(connection, secret_ref)
            if row is None or row["incarnation"] != incarnation:
                raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
            record = self._record(row)
            self._validate_pin(record, request_digest=request_digest, arn=arn, version_id=version_id)
            if record.state == "terminal" or not row["live"]:
                await self._retire(connection, record)
                await self._enqueue(connection, record, arn=arn, version_id=version_id)
                return False
            if record.state == "active":
                if record.arn != arn or record.version_id != version_id:
                    raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
                return True
            changed = await connection.fetchval(
                f"UPDATE {self._records} SET state = 'active', arn = $4, version_id = $5, "
                "updated_at = clock_timestamp() WHERE namespace = $1 "
                "AND secret_ref = $2 AND incarnation = $3 AND state = 'reserved' RETURNING TRUE",
                self._namespace, secret_ref, incarnation, arn, version_id,
            )
            if changed is not True:
                raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
            return True

    async def read_active(self, *, secret_ref: str) -> RuntimeCustodyRecord | None:
        _hex(secret_ref, _HEX32)
        async with self._transaction() as connection:
            row = await self._locked(connection, secret_ref)
            if row is None or row["state"] == "terminal" or not row["live"]:
                return None
            if row["state"] != "active":
                raise RuntimeMetadataError("runtime_secret_metadata_unavailable")
            return self._record(row)

    async def read_original(self, *, secret_ref: str,
                            live_only: bool = False) -> RuntimeCustodyRecord | None:
        """Trusted cloud recovery/cleanup coordinates, never a value endpoint.

        A live reserved row is unresolved, not cloud absence. Cleanup also
        needs terminal rows; an HTTP reader must request live_only and recheck
        active state/pins after recovering the original creation version.
        """
        _hex(secret_ref, _HEX32)
        if type(live_only) is not bool:
            raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
        async with self._transaction() as connection:
            row = await self._locked(connection, secret_ref)
            if row is None or (live_only and (row["state"] == "terminal" or not row["live"])):
                return None
            return self._record(row)

    async def confirms_active(self, original: RuntimeCustodyRecord) -> bool:
        """Recheck state/deadline and exact pins after an external value read."""
        if type(original) is not RuntimeCustodyRecord or original.namespace != self._namespace:
            raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
        current = await self.read_active(secret_ref=original.secret_ref)
        return current is not None and current == original

    async def retire(self, *, secret_ref: str, incarnation: str) -> None:
        _hex(secret_ref, _HEX32)
        _hex(incarnation, _HEX32)
        async with self._transaction() as connection:
            row = await self._locked(connection, secret_ref)
            if row is None or row["incarnation"] != incarnation:
                raise RuntimeMetadataError("runtime_secret_metadata_binding_invalid")
            await self._retire(connection, self._record(row))

    async def retire_reference(self, *, secret_ref: str) -> None:
        """Retire the current original under its lock; missing delete is a no-op.

        Every cloud create must have committed its reservation first, so a
        missing row cannot hide a legitimate in-flight cloud create. No cloud
        lookup, new incarnation or replacement-target deletion follows it.
        """
        _hex(secret_ref, _HEX32)
        async with self._transaction() as connection:
            row = await self._locked(connection, secret_ref)
            if row is not None:
                await self._retire(connection, self._record(row))

    async def retire_expired(self, *, now: int, limit: int) -> int:
        """Atomically retire at most limit rows and queue value-free cleanup."""
        _bound_integer(now, minimum=1, maximum=253402300799)
        _bound_integer(limit, minimum=1, maximum=1000)
        async with self._transaction() as connection:
            if await connection.fetchval(f"SELECT $1 > {_CLOCK}", now):
                raise RuntimeMetadataError("runtime_secret_purge_invalid")
            rows = await connection.fetch(
                f"SELECT * FROM {self._records} WHERE namespace = $1 "
                "AND state != 'terminal' AND expires_at <= $2 ORDER BY expires_at, secret_ref "
                "LIMIT $3 FOR UPDATE SKIP LOCKED", self._namespace, now, limit,
            )
            for row in rows:
                await self._retire(connection, self._record(row))
            return len(rows)

    async def claim_cleanup(self, *, limit: int, lease_seconds: int = 30) -> tuple[RuntimeCleanupClaim, ...]:
        _bound_integer(limit, minimum=1, maximum=1000)
        _bound_integer(lease_seconds, minimum=1, maximum=300)
        async with self._transaction() as connection:
            rows = await connection.fetch(
                f"SELECT * FROM {self._cleanup} WHERE namespace = $1 AND "
                "(state IN ('pending', 'delete_accepted') OR "
                "(state = 'claimed' AND claim_until <= clock_timestamp())) "
                "ORDER BY updated_at, job_id LIMIT $2 FOR UPDATE SKIP LOCKED",
                self._namespace, limit,
            )
            result = []
            for row in rows:
                token = uuid.uuid4().hex
                await connection.execute(
                    f"UPDATE {self._cleanup} SET state = 'claimed', claim_token = $3, "
                    "claim_until = clock_timestamp() + ($4::int * interval '1 second'), "
                    "updated_at = clock_timestamp() WHERE namespace = $1 AND job_id = $2",
                    self._namespace, row["job_id"], token, lease_seconds,
                )
                result.append(RuntimeCleanupClaim(**{name: row[name] for name in (
                    "namespace", "job_id", "secret_ref", "incarnation", "arn", "version_id", "phase",
                )}, claim_token=token))
            return tuple(result)

    async def settle_cleanup(self, claim: RuntimeCleanupClaim, *, outcome: str) -> bool:
        """CAS a trusted worker result; a delete ACK is not physical erasure.

        'deleted' requires a confirm-phase claim after accepted deletion of
        the exact pinned full ARN. Unknown-create reconciliation only retries;
        one absent-resource read cannot settle it as deleted.
        """
        if (type(claim) is not RuntimeCleanupClaim or claim.namespace != self._namespace
                or type(outcome) is not str or outcome not in {"retry", "delete_accepted", "deleted"}
                or type(claim.phase) is not str or claim.phase not in {"reconcile", "delete", "confirm"}
                or any(type(value) is not str or pattern.fullmatch(value) is None for value, pattern in (
                    (claim.job_id, _HEX64), (claim.secret_ref, _HEX32),
                    (claim.incarnation, _HEX32), (claim.claim_token, _HEX32),
                ))
                or ((claim.arn is None) != (claim.version_id is None))
                or ((claim.phase == "reconcile") != (claim.arn is None))
                or (claim.arn is not None and (type(claim.arn) is not str
                    or type(claim.version_id) is not str or _HEX32.fullmatch(claim.version_id) is None))
                or (outcome == "delete_accepted" and claim.phase != "delete")
                or (outcome == "deleted" and claim.phase != "confirm")):
            raise RuntimeMetadataError("runtime_secret_cleanup_binding_invalid")
        state = {"retry": "delete_accepted" if claim.phase == "confirm" else "pending",
                 "delete_accepted": "delete_accepted", "deleted": "deleted"}[outcome]
        phase = "confirm" if outcome == "delete_accepted" else claim.phase
        async with self._transaction() as connection:
            changed = await connection.fetchval(
                f"UPDATE {self._cleanup} SET state = $8, phase = $9, claim_token = NULL, "
                "claim_until = NULL, updated_at = clock_timestamp() WHERE namespace = $1 "
                "AND job_id = $2 AND incarnation = $3 AND claim_token = $4 "
                "AND arn IS NOT DISTINCT FROM $5 AND version_id IS NOT DISTINCT FROM $6 "
                "AND phase = $7 AND secret_ref = $10 AND state = 'claimed' "
                "AND claim_until > clock_timestamp() "
                "RETURNING job_id", self._namespace, claim.job_id, claim.incarnation,
                claim.claim_token, claim.arn, claim.version_id, claim.phase, state, phase, claim.secret_ref,
            )
            return changed is not None

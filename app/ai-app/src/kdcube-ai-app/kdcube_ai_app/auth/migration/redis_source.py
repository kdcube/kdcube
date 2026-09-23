# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Read-only Redis source for KDCube-owned durable session authority."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from connection_hub.delegated_credentials.authority_cutover import (
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_USERS,
    FAMILY_PLATFORM_SESSIONS,
)
from connection_hub.delegated_credentials.migration.model import (
    AuthorityMigrationInspection,
    AuthorityMigrationRecord,
    AuthorityMigrationSnapshot,
)
from connection_hub.delegated_credentials.migration.redis_source import (
    DurableRedisRecordError,
    ReadOnlyRedisMigrationScanner,
)


KDCUBE_SESSION_MIGRATION_FAMILIES = (
    FAMILY_BUNDLE_AUTHORITY_VERSION,
    FAMILY_BUNDLE_SESSIONS,
    FAMILY_BUNDLE_USERS,
    FAMILY_PLATFORM_SESSIONS,
)

_ANONYMOUS_DURABLE_FIELDS = (
    "email",
    "identity_authority",
    "permissions",
    "rate_limit_subject",
    "roles",
    "user_id",
    "username",
)


def _anonymous_session_requires_preservation(payload: dict[str, Any]) -> bool:
    for field in _ANONYMOUS_DURABLE_FIELDS:
        value = payload.get(field)
        if value not in (None, "", [], {}):
            return True
    return False


class KdcubeRedisSessionMigrationSource:
    """Inventory bundle and platform sessions while ignoring derived indexes."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        scan_count: int = 200,
    ) -> None:
        self.tenant = str(tenant or "").strip() or "default"
        self.project = str(project or "").strip() or "default-project"
        self._scanner = ReadOnlyRedisMigrationScanner(
            redis,
            scan_count=scan_count,
        )
        self._prefix = f"{self.tenant}:{self.project}"

    async def _bundle_users(self) -> list[AuthorityMigrationRecord]:
        base = f"{self._prefix}:kdcube:auth:bundle-session:"
        user_prefix = base + "user:"
        version_prefix = base + "user-version:"
        user_keys = {
            key.removeprefix(user_prefix): key
            for key in await self._scanner.keys(user_prefix + "*")
        }
        version_keys = {
            key.removeprefix(version_prefix): key
            for key in await self._scanner.keys(version_prefix + "*")
        }
        records: list[AuthorityMigrationRecord] = []
        for subject in sorted(set(user_keys).union(version_keys)):
            if not subject:
                raise DurableRedisRecordError("bundle_user_subject_missing")
            user_record = None
            if subject in user_keys:
                user_record, user_expiry = await self._scanner.read_json_object(
                    user_keys[subject],
                    expiry_required=False,
                )
                if user_expiry is not None:
                    raise DurableRedisRecordError(
                        "bundle_user_unexpected_expiry",
                        key=user_keys[subject],
                    )
                if str(user_record.get("sub") or "") != subject:
                    raise DurableRedisRecordError(
                        "bundle_user_identity_mismatch",
                        key=user_keys[subject],
                    )
            version = 1
            if subject in version_keys:
                raw_version, version_expiry = await self._scanner.read_text(
                    version_keys[subject],
                    expiry_required=False,
                )
                if version_expiry is not None:
                    raise DurableRedisRecordError(
                        "bundle_authority_version_unexpected_expiry",
                        key=version_keys[subject],
                    )
                try:
                    version = int(raw_version)
                except ValueError as exc:
                    raise DurableRedisRecordError(
                        "bundle_authority_version_invalid",
                        key=version_keys[subject],
                    ) from exc
                if version < 1:
                    raise DurableRedisRecordError(
                        "bundle_authority_version_invalid",
                        key=version_keys[subject],
                    )
            families = [FAMILY_BUNDLE_AUTHORITY_VERSION]
            if user_record is not None:
                families.append(FAMILY_BUNDLE_USERS)
            records.append(
                AuthorityMigrationRecord(
                    record_type="bundle_user_authority",
                    identity=hashlib.sha256(subject.encode("utf-8")).hexdigest(),
                    families=families,
                    payload={
                        "subject": subject,
                        "record": user_record,
                        "session_version": version,
                    },
                )
            )
        return records

    async def _bundle_sessions(self) -> list[AuthorityMigrationRecord]:
        prefix = f"{self._prefix}:kdcube:auth:bundle-session:session:"
        records: list[AuthorityMigrationRecord] = []
        for key in await self._scanner.keys(prefix + "*"):
            payload, expires_at_ms = await self._scanner.read_json_object(key)
            session_id = key.removeprefix(prefix)
            if str(payload.get("session_id") or "") != session_id:
                raise DurableRedisRecordError(
                    "bundle_session_identity_mismatch",
                    key=key,
                )
            record_expiry_ms = int(payload.get("exp") or 0) * 1000
            if record_expiry_ms <= 0 or abs(int(expires_at_ms) - record_expiry_ms) > 1500:
                raise DurableRedisRecordError(
                    "bundle_session_expiry_mismatch",
                    key=key,
                )
            records.append(
                AuthorityMigrationRecord(
                    record_type="bundle_session",
                    identity=session_id,
                    families=(FAMILY_BUNDLE_SESSIONS,),
                    payload={"record": payload},
                    expires_at_ms=record_expiry_ms,
                )
            )
        return records

    async def _platform_sessions(
        self,
    ) -> tuple[list[AuthorityMigrationRecord], dict[str, int]]:
        prefix = f"{self._prefix}:kdcube:session:"
        records: list[AuthorityMigrationRecord] = []
        classifications = {
            "preserved_anonymous": 0,
            "preserved_authenticated": 0,
            "skipped_reconstructable_anonymous": 0,
        }
        for key in await self._scanner.keys(prefix + "*"):
            authority_key = key.removeprefix(prefix)
            if authority_key.startswith("index:"):
                continue
            payload, expires_at_ms = await self._scanner.read_json_object(key)
            session_id = str(payload.get("session_id") or "").strip()
            if not authority_key or not session_id:
                raise DurableRedisRecordError(
                    "platform_session_identity_missing",
                    key=key,
                )
            user_type = str(payload.get("user_type") or "").strip().lower()
            is_reconstructable_anonymous = (
                user_type == "anonymous"
                and authority_key.startswith("anonymous:")
                and not _anonymous_session_requires_preservation(payload)
            )
            if is_reconstructable_anonymous:
                classifications["skipped_reconstructable_anonymous"] += 1
                continue
            if user_type == "anonymous":
                classifications["preserved_anonymous"] += 1
            else:
                classifications["preserved_authenticated"] += 1
            records.append(
                AuthorityMigrationRecord(
                    record_type="platform_session",
                    identity=session_id,
                    families=(FAMILY_PLATFORM_SESSIONS,),
                    payload={
                        "authority_key": authority_key,
                        "record": payload,
                    },
                    expires_at_ms=expires_at_ms,
                )
            )
        return records, classifications

    async def inspect(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationInspection:
        captured = int(
            captured_at_ms
            if captured_at_ms is not None
            else time.time_ns() // 1_000_000
        )
        users = await self._bundle_users()
        sessions = await self._bundle_sessions()
        platform, platform_classifications = await self._platform_sessions()
        snapshot = AuthorityMigrationSnapshot(
            tenant=self.tenant,
            project=self.project,
            records=(
                *users,
                *sessions,
                *platform,
            ),
            declared_families=KDCUBE_SESSION_MIGRATION_FAMILIES,
            captured_at_ms=captured,
        ).validated()
        record_types: dict[str, int] = {}
        for record in snapshot.records:
            record_types[record.record_type] = record_types.get(record.record_type, 0) + 1
        user_classifications = {
            "active": sum(
                1 for record in users if record.payload.get("record") is not None
            ),
            "revocation_tombstone": sum(
                1 for record in users if record.payload.get("record") is None
            ),
        }
        return AuthorityMigrationInspection(
            snapshot=snapshot,
            source_summary={
                "bundle_user_authority": user_classifications,
                "platform_sessions": platform_classifications,
                "record_types": record_types,
            },
        ).validated()

    async def snapshot(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationSnapshot:
        return (
            await self.inspect(captured_at_ms=captured_at_ms)
        ).snapshot


class KdcubeRedisSessionResetSource(KdcubeRedisSessionMigrationSource):
    """Declare session authority as reset while reporting discarded records.

    Login rebuilds users and their descriptor/identity-derived grants. The new
    generation deliberately starts without prior browser, bundle, or platform
    sessions and without the prior authority-version namespace.
    """

    async def inspect(
        self,
        *,
        captured_at_ms: int | None = None,
    ) -> AuthorityMigrationInspection:
        captured = int(
            captured_at_ms
            if captured_at_ms is not None
            else time.time_ns() // 1_000_000
        )
        bundle_base = f"{self._prefix}:kdcube:auth:bundle-session:"
        platform_prefix = f"{self._prefix}:kdcube:session:"
        platform_keys = await self._scanner.keys(platform_prefix + "*")
        platform_sessions = sum(
            1
            for key in platform_keys
            if not key.removeprefix(platform_prefix).startswith("index:")
        )
        snapshot = AuthorityMigrationSnapshot(
            tenant=self.tenant,
            project=self.project,
            records=(),
            declared_families=KDCUBE_SESSION_MIGRATION_FAMILIES,
            captured_at_ms=captured,
        ).validated()
        return AuthorityMigrationInspection(
            snapshot=snapshot,
            source_summary={
                "reset": {
                    "bundle_authority_versions": len(
                        await self._scanner.keys(bundle_base + "user-version:*")
                    ),
                    "bundle_sessions": len(
                        await self._scanner.keys(bundle_base + "session:*")
                    ),
                    "bundle_users": len(
                        await self._scanner.keys(bundle_base + "user:*")
                    ),
                    "platform_sessions": platform_sessions,
                }
            },
        ).validated()


__all__ = [
    "KDCUBE_SESSION_MIGRATION_FAMILIES",
    "KdcubeRedisSessionMigrationSource",
    "KdcubeRedisSessionResetSource",
]

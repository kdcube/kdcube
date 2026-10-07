"""Immutable age and exact typed-byte integrity for checkpoint DB payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal

from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.errors import (
    CheckpointAgeUnprovenError, CheckpointIntegrityError,
)
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.scope import CheckpointScope, PayloadKey, _text


def aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be an offset-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CreationProvenance:
    created_at: datetime | None = None
    source: Literal["unknown", "insert", "reviewed_legacy"] = "unknown"
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.source not in ("unknown", "insert", "reviewed_legacy"):
            raise ValueError("unknown creation provenance source")
        if self.source == "unknown":
            if self.created_at is not None:
                raise ValueError("unproven age cannot claim a creation timestamp")
        else:
            aware(self.created_at, "created_at")
        if self.source == "reviewed_legacy":
            _text(self.evidence, "reviewed legacy evidence")

    def eligible(self, now: datetime, retention_days: int = 14) -> bool:
        current = aware(now, "now")
        if type(retention_days) is not int or retention_days < 1:
            raise ValueError("retention_days must be a positive integer")
        if self.source == "unknown":
            return False
        created = aware(self.created_at, "created_at")
        return created <= current - timedelta(days=retention_days)


@dataclass(frozen=True)
class TypedPayload:
    serialization_type: str
    data: bytes | None

    def __post_init__(self) -> None:
        _text(self.serialization_type, "serialization_type")
        if self.data is not None and not isinstance(self.data, bytes):
            raise TypeError("payload must be bytes or the native NULL value")
        if self.data is None and self.serialization_type != "empty":
            raise ValueError("only the native empty channel may have NULL bytes")

    @property
    def byte_length(self) -> int:
        return len(self.data) if self.data is not None else 0

    @property
    def sha256(self) -> str:
        # A SQL NULL and zero-length bytea are different typed values.
        return sha256(b"N" if self.data is None else b"B" + self.data).hexdigest()


@dataclass(frozen=True)
class PayloadManifest:
    scope: CheckpointScope
    key: PayloadKey
    serialization_type: str
    byte_length: int
    sha256: str
    creation: CreationProvenance
    cold_key: str

    def __post_init__(self) -> None:
        self.scope.bind(self.key)
        _text(self.serialization_type, "serialization_type")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("invalid payload length")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("invalid payload SHA-256")
        if self.cold_key != self.expected_cold_key():
            raise CheckpointIntegrityError("cold key is not the exact content-addressed DB payload key")

    def expected_cold_key(self) -> str:
        if self.creation.source == "unknown":
            raise CheckpointAgeUnprovenError("creation provenance is required before archival")
        day = aware(self.creation.created_at, "created_at").date().isoformat()
        return f"checkpoint-db/v1/{day}/{self.key.digest(self.scope)}/{self.sha256}.json"

    @classmethod
    def for_payload(cls, scope: CheckpointScope, key: PayloadKey, payload: TypedPayload,
                    creation: CreationProvenance) -> PayloadManifest:
        if creation.source == "unknown":
            raise CheckpointAgeUnprovenError("creation provenance is required before archival")
        day = aware(creation.created_at, "created_at").date().isoformat()
        cold_key = f"checkpoint-db/v1/{day}/{key.digest(scope)}/{payload.sha256}.json"
        return cls(scope, key, payload.serialization_type, payload.byte_length, payload.sha256,
                   creation, cold_key)

    def check_binding(self, scope: CheckpointScope, key: PayloadKey) -> None:
        scope.bind(key)
        if self.scope != scope or self.key != key:
            raise CheckpointIntegrityError("cold manifest scope or composite key mismatch")

    def check_payload(self, payload: TypedPayload) -> None:
        if (payload.serialization_type, payload.byte_length, payload.sha256) != (
                self.serialization_type, self.byte_length, self.sha256):
            raise CheckpointIntegrityError("cold payload type, length or SHA-256 mismatch")

    def to_dict(self) -> dict:
        record = asdict(self)
        record["creation"]["created_at"] = self.creation.created_at.isoformat()
        return record

    @classmethod
    def from_dict(cls, record: dict) -> PayloadManifest:
        creation = dict(record["creation"])
        creation["created_at"] = datetime.fromisoformat(creation["created_at"])
        return cls(CheckpointScope(**record["scope"]), PayloadKey(**record["key"]),
                   record["serialization_type"], record["byte_length"], record["sha256"],
                   CreationProvenance(**creation), record["cold_key"])

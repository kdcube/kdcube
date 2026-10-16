# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-bound, one-use human export transactions for exact secret keys."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    HumanApprovalEvidence,
    assurance_satisfies,
)
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    SecretTarget,
)

SECRET_EXPORT_REQUEST_SCHEMA = "kdcube.management.secret_export.request.v1"
SECRET_EXPORT_TRANSACTION_SCHEMA = "kdcube.management.secret_export.transaction.v1"
SECRET_EXPORT_START_SCHEMA = "kdcube.management.secret_export.start.v1"
SECRET_EXPORT_RESULT_SCHEMA = "kdcube.management.secret_export.result.v1"
SECRET_EXPORT_ERROR_SCHEMA = "kdcube.management.secret_export.error.v1"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,512}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_EVIDENCE_CLOCK_SKEW_SECONDS = 30
_CAS_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
if current ~= ARGV[1] then return -1 end
redis.call('SETEX', KEYS[1], ARGV[2], ARGV[3])
return 1
"""


class SecretExportError(RuntimeError):
    def __init__(self, code: str, *, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _token(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise SecretExportError(code, status_code=400)
    candidate = value.strip()
    if not _TOKEN_RE.fullmatch(candidate):
        raise SecretExportError(code, status_code=400)
    return candidate


def _callback_uri(value: Any) -> str:
    if not isinstance(value, str):
        raise SecretExportError("secret_export_callback_invalid", status_code=400)
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        raise SecretExportError("secret_export_callback_invalid", status_code=400)
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        raise SecretExportError(
            "secret_export_callback_invalid",
            status_code=400,
        ) from None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or port < 1
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/callback"
        or parsed.query
        or parsed.fragment
    ):
        raise SecretExportError("secret_export_callback_invalid", status_code=400)
    return candidate


def _challenge(value: Any) -> str:
    if not isinstance(value, str):
        raise SecretExportError("secret_export_pkce_invalid", status_code=400)
    candidate = value.strip()
    if not _PKCE_CHALLENGE_RE.fullmatch(candidate):
        raise SecretExportError("secret_export_pkce_invalid", status_code=400)
    return candidate


def _verifier(value: Any) -> str:
    if not isinstance(value, str):
        raise SecretExportError("secret_export_pkce_invalid", status_code=400)
    candidate = value.strip()
    if not _PKCE_VERIFIER_RE.fullmatch(candidate):
        raise SecretExportError("secret_export_pkce_invalid", status_code=400)
    return candidate


def _challenge_for(verifier: str) -> str:
    encoded = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _record_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SecretExportError(
            "secret_export_transaction_invalid",
            status_code=503,
        )
    return value


def _record_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate transaction field")
        result[key] = value
    return result


def _reject_record_constant(_value: str) -> None:
    raise ValueError("nonstandard transaction number")


def _targets(
    values: Any,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> tuple[SecretTarget, ...]:
    if allow_empty and values == []:
        return ()
    if not isinstance(values, list) or not values or len(values) > maximum:
        raise SecretExportError("secret_export_targets_invalid", status_code=400)
    targets: list[SecretTarget] = []
    try:
        for value in values:
            if not isinstance(value, Mapping):
                raise TypeError("target must be an object")
            if set(value) - {"scope", "bundle_id", "user_id", "key"}:
                raise ValueError("target has unknown fields")
            targets.append(SecretTarget.from_mapping(value))
    except (TypeError, ValueError):
        raise SecretExportError(
            "secret_export_targets_invalid",
            status_code=400,
        ) from None
    targets.sort(
        key=lambda item: (item.scope, item.user_id, item.bundle_id, item.key)
    )
    identities = [item.provider_key for item in targets]
    if len(identities) != len(set(identities)):
        raise SecretExportError("secret_export_targets_invalid", status_code=400)
    return tuple(targets)


@dataclass(frozen=True)
class SecretExportRequest:
    tenant: str
    project: str
    callback_uri: str
    state: str
    code_challenge: str
    targets: tuple[SecretTarget, ...]
    # W885: a configuration-bootstrap export also returns the user props; bound into the digest.
    user_props: bool = False

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        tenant: str,
        project: str,
        max_targets: int,
    ) -> SecretExportRequest:
        allowed = {
            "schema",
            "callback_uri",
            "state",
            "code_challenge",
            "code_challenge_method",
            "targets",
            "user_props",
        }
        user_props = value.get("user_props", False)
        if (
            set(value) - allowed
            or value.get("schema") != SECRET_EXPORT_REQUEST_SCHEMA
            or value.get("code_challenge_method") != "S256"
            or type(user_props) is not bool
        ):
            raise SecretExportError("secret_export_request_invalid", status_code=400)
        return cls(
            tenant=str(tenant or "").strip(),
            project=str(project or "").strip(),
            callback_uri=_callback_uri(value.get("callback_uri")),
            state=_token(value.get("state"), code="secret_export_state_invalid"),
            code_challenge=_challenge(value.get("code_challenge")),
            targets=_targets(value.get("targets"), maximum=max_targets, allow_empty=user_props),
            user_props=user_props,
        )

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema": SECRET_EXPORT_REQUEST_SCHEMA,
            "tenant": self.tenant,
            "project": self.project,
            "callback_uri": self.callback_uri,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "targets": [target.public_dict() for target in self.targets],
            **({"user_props": True} if self.user_props else {}),
        }

    @property
    def request_digest(self) -> str:
        return _digest(self.canonical_payload)


@dataclass(frozen=True)
class SecretExportTransaction:
    transaction_id: str
    request: SecretExportRequest
    request_digest: str
    csrf_token: str
    status: str
    created_at: int
    expires_at: int
    required_assurance: str
    max_evidence_age_seconds: int
    max_total_value_bytes: int
    code_digest: str = ""
    subject: str = ""
    assurance: str = ""
    approval_method: str = ""
    approval_verified_at: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": SECRET_EXPORT_TRANSACTION_SCHEMA,
            "transaction_id": self.transaction_id,
            "request": self.request.canonical_payload,
            "request_digest": self.request_digest,
            "csrf_token": self.csrf_token,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "required_assurance": self.required_assurance,
            "max_evidence_age_seconds": self.max_evidence_age_seconds,
            "max_total_value_bytes": self.max_total_value_bytes,
            "code_digest": self.code_digest,
            "subject": self.subject,
            "assurance": self.assurance,
            "approval_method": self.approval_method,
            "approval_verified_at": self.approval_verified_at,
        }


@dataclass(frozen=True)
class ApprovedSecretExport:
    transaction_id: str
    code: str
    callback_uri: str
    state: str
    request_digest: str


@dataclass(frozen=True)
class ConsumedSecretExport:
    transaction_id: str
    request: SecretExportRequest
    request_digest: str
    subject: str
    assurance: str
    approval_method: str
    approval_verified_at: int
    max_total_value_bytes: int


class RedisSecretExportStore:
    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        transaction_ttl_seconds: int,
        consumed_tombstone_seconds: int,
        max_targets: int,
        clock: Any = time.time,
    ) -> None:
        self._redis = redis
        self._tenant = str(tenant or "").strip()
        self._project = str(project or "").strip()
        self._ttl = int(transaction_ttl_seconds)
        self._tombstone_ttl = int(consumed_tombstone_seconds)
        self._max_targets = int(max_targets)
        self._clock = clock
        if (
            not self._tenant
            or not self._project
            or self._ttl < 1
            or self._tombstone_ttl < 1
            or self._max_targets < 1
        ):
            raise ValueError("secret export store configuration is invalid")

    def _key(self, transaction_id: str) -> str:
        digest = hashlib.sha256(transaction_id.encode("ascii")).hexdigest()
        return (
            f"{self._tenant}:{self._project}:"
            f"kdcube:management:secret-export:{digest}"
        )

    def _decode(self, raw: Any) -> SecretExportTransaction:
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SecretExportError(
                    "secret_export_transaction_invalid",
                    status_code=503,
                ) from None
        try:
            value = json.loads(
                str(raw),
                object_pairs_hook=_record_without_duplicate_keys,
                parse_constant=_reject_record_constant,
            )
        except (TypeError, ValueError):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            ) from None
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != SECRET_EXPORT_TRANSACTION_SCHEMA
            or set(value)
            != {
                "schema",
                "transaction_id",
                "request",
                "request_digest",
                "csrf_token",
                "status",
                "created_at",
                "expires_at",
                "required_assurance",
                "max_evidence_age_seconds",
                "max_total_value_bytes",
                "code_digest",
                "subject",
                "assurance",
                "approval_method",
                "approval_verified_at",
            }
        ):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        request_value = value.get("request")
        if not isinstance(request_value, Mapping):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        try:
            request = SecretExportRequest.from_mapping(
                {
                    key: item
                    for key, item in request_value.items()
                    if key not in {"tenant", "project"}
                },
                tenant=str(request_value.get("tenant") or ""),
                project=str(request_value.get("project") or ""),
                max_targets=self._max_targets,
            )
        except SecretExportError:
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            ) from None
        transaction = SecretExportTransaction(
            transaction_id=_token(
                value.get("transaction_id"),
                code="secret_export_transaction_invalid",
            ),
            request=request,
            request_digest=str(value.get("request_digest") or ""),
            csrf_token=_token(
                value.get("csrf_token"),
                code="secret_export_transaction_invalid",
            ),
            status=str(value.get("status") or ""),
            created_at=_record_int(value.get("created_at")),
            expires_at=_record_int(value.get("expires_at")),
            required_assurance=str(value.get("required_assurance") or ""),
            max_evidence_age_seconds=_record_int(
                value.get("max_evidence_age_seconds")
            ),
            max_total_value_bytes=_record_int(value.get("max_total_value_bytes")),
            code_digest=str(value.get("code_digest") or ""),
            subject=str(value.get("subject") or ""),
            assurance=str(value.get("assurance") or ""),
            approval_method=str(value.get("approval_method") or ""),
            approval_verified_at=_record_int(value.get("approval_verified_at")),
        )
        if (
            request.tenant != self._tenant
            or request.project != self._project
            or transaction.request_digest != request.request_digest
            or transaction.status not in {"pending", "approved", "denied", "consumed"}
            or transaction.created_at <= 0
            or transaction.expires_at <= transaction.created_at
            or not assurance_satisfies(
                transaction.required_assurance,
                transaction.required_assurance,
            )
            or not 1 <= transaction.max_evidence_age_seconds <= 900
            or not 1 <= transaction.max_total_value_bytes <= 8 * 1024 * 1024
        ):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        has_evidence = False
        if (
            transaction.subject
            or transaction.assurance
            or transaction.approval_method
        ):
            try:
                evidence = HumanApprovalEvidence(
                    subject=transaction.subject,
                    assurance=transaction.assurance,
                    method=transaction.approval_method,
                    request_digest=transaction.request_digest,
                    verified_at=transaction.approval_verified_at,
                )
            except ValueError:
                raise SecretExportError(
                    "secret_export_transaction_invalid",
                    status_code=503,
                ) from None
            transaction = replace(
                transaction,
                subject=evidence.subject,
                assurance=evidence.assurance,
                approval_method=evidence.method,
                approval_verified_at=evidence.verified_at,
            )
            has_evidence = (
                assurance_satisfies(
                    evidence.assurance,
                    transaction.required_assurance,
                )
                and transaction.created_at <= evidence.verified_at
                and evidence.verified_at
                <= transaction.expires_at + _EVIDENCE_CLOCK_SKEW_SECONDS
            )
        if transaction.status == "pending" and (
            transaction.code_digest
            or transaction.subject
            or transaction.assurance
            or transaction.approval_method
            or transaction.approval_verified_at
        ):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        if transaction.status == "approved" and (
            not _SHA256_RE.fullmatch(transaction.code_digest) or not has_evidence
        ):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        if transaction.status in {"denied", "consumed"} and (
            transaction.code_digest or not has_evidence
        ):
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        return transaction

    async def create(
        self,
        request: SecretExportRequest,
        *,
        required_assurance: str,
        max_evidence_age_seconds: int,
        max_total_value_bytes: int,
    ) -> SecretExportTransaction:
        if request.tenant != self._tenant or request.project != self._project:
            raise SecretExportError(
                "secret_export_request_invalid",
                status_code=400,
            )
        if (
            not assurance_satisfies(required_assurance, required_assurance)
            or isinstance(max_evidence_age_seconds, bool)
            or not isinstance(max_evidence_age_seconds, int)
            or not 1 <= max_evidence_age_seconds <= 900
            or isinstance(max_total_value_bytes, bool)
            or not isinstance(max_total_value_bytes, int)
            or not 1 <= max_total_value_bytes <= 8 * 1024 * 1024
        ):
            raise SecretExportError("secret_export_policy_invalid", status_code=503)
        now = int(self._clock())
        for _attempt in range(4):
            transaction = SecretExportTransaction(
                transaction_id=secrets.token_urlsafe(32),
                request=request,
                request_digest=request.request_digest,
                csrf_token=secrets.token_urlsafe(32),
                status="pending",
                created_at=now,
                expires_at=now + self._ttl,
                required_assurance=required_assurance,
                max_evidence_age_seconds=max_evidence_age_seconds,
                max_total_value_bytes=max_total_value_bytes,
            )
            try:
                created = await self._redis.set(
                    self._key(transaction.transaction_id),
                    _json(transaction.to_record()),
                    nx=True,
                    ex=self._ttl,
                )
            except Exception:  # noqa: BLE001
                raise SecretExportError(
                    "secret_export_store_unavailable",
                    status_code=503,
                ) from None
            if created:
                return transaction
        raise SecretExportError("secret_export_store_unavailable", status_code=503)

    async def load(self, transaction_id: str) -> SecretExportTransaction:
        exact_id = _token(
            transaction_id,
            code="secret_export_transaction_invalid",
        )
        try:
            raw = await self._redis.get(self._key(exact_id))
        except Exception:  # noqa: BLE001
            raise SecretExportError(
                "secret_export_store_unavailable",
                status_code=503,
            ) from None
        if raw is None:
            raise SecretExportError("secret_export_transaction_not_found", status_code=404)
        transaction = self._decode(raw)
        if transaction.transaction_id != exact_id:
            raise SecretExportError(
                "secret_export_transaction_invalid",
                status_code=503,
            )
        if transaction.expires_at <= int(self._clock()):
            raise SecretExportError("secret_export_transaction_expired", status_code=410)
        return transaction

    async def _replace(
        self,
        current: SecretExportTransaction,
        replacement: SecretExportTransaction,
        *,
        ttl_seconds: int,
    ) -> None:
        try:
            result = await self._redis.eval(
                _CAS_SCRIPT,
                1,
                self._key(current.transaction_id),
                _json(current.to_record()),
                max(1, int(ttl_seconds)),
                _json(replacement.to_record()),
            )
        except Exception:  # noqa: BLE001
            raise SecretExportError(
                "secret_export_store_unavailable",
                status_code=503,
            ) from None
        if int(result or 0) != 1:
            raise SecretExportError("secret_export_transaction_moved", status_code=409)

    def _validate_evidence(
        self,
        transaction: SecretExportTransaction,
        evidence: HumanApprovalEvidence,
    ) -> None:
        now = int(self._clock())
        if (
            not isinstance(evidence, HumanApprovalEvidence)
            or not secrets.compare_digest(
                evidence.request_digest,
                transaction.request_digest,
            )
            or evidence.verified_at < transaction.created_at
            or evidence.verified_at > now + _EVIDENCE_CLOCK_SKEW_SECONDS
            or now - evidence.verified_at > transaction.max_evidence_age_seconds
            or not assurance_satisfies(
                evidence.assurance,
                transaction.required_assurance,
            )
        ):
            raise SecretExportError(
                "secret_export_approval_invalid",
                status_code=403,
            )

    async def approve(
        self,
        transaction_id: str,
        *,
        csrf_token: str,
        evidence: HumanApprovalEvidence,
    ) -> ApprovedSecretExport:
        transaction = await self.load(transaction_id)
        if transaction.status != "pending":
            raise SecretExportError("secret_export_transaction_moved", status_code=409)
        candidate_csrf = _token(
            csrf_token,
            code="secret_export_csrf_invalid",
        )
        if not secrets.compare_digest(candidate_csrf, transaction.csrf_token):
            raise SecretExportError("secret_export_csrf_invalid", status_code=403)
        self._validate_evidence(transaction, evidence)
        code = secrets.token_urlsafe(32)
        approved = replace(
            transaction,
            status="approved",
            code_digest=hashlib.sha256(code.encode("ascii")).hexdigest(),
            subject=evidence.subject,
            assurance=evidence.assurance,
            approval_method=evidence.method,
            approval_verified_at=evidence.verified_at,
        )
        await self._replace(
            transaction,
            approved,
            ttl_seconds=transaction.expires_at - int(self._clock()),
        )
        return ApprovedSecretExport(
            transaction_id=transaction.transaction_id,
            code=code,
            callback_uri=transaction.request.callback_uri,
            state=transaction.request.state,
            request_digest=transaction.request_digest,
        )

    async def deny(
        self,
        transaction_id: str,
        *,
        csrf_token: str,
        evidence: HumanApprovalEvidence,
    ) -> SecretExportTransaction:
        transaction = await self.load(transaction_id)
        if transaction.status != "pending":
            raise SecretExportError("secret_export_transaction_moved", status_code=409)
        candidate_csrf = _token(
            csrf_token,
            code="secret_export_csrf_invalid",
        )
        if not secrets.compare_digest(candidate_csrf, transaction.csrf_token):
            raise SecretExportError("secret_export_csrf_invalid", status_code=403)
        self._validate_evidence(transaction, evidence)
        denied = replace(
            transaction,
            status="denied",
            subject=evidence.subject,
            assurance=evidence.assurance,
            approval_method=evidence.method,
            approval_verified_at=evidence.verified_at,
        )
        await self._replace(
            transaction,
            denied,
            ttl_seconds=self._tombstone_ttl,
        )
        return denied

    async def consume(
        self,
        transaction_id: str,
        *,
        code: str,
        code_verifier: str,
    ) -> ConsumedSecretExport:
        transaction = await self.load(transaction_id)
        if transaction.status != "approved":
            raise SecretExportError("secret_export_not_approved", status_code=403)
        exact_code = _token(code, code="secret_export_code_invalid")
        verifier = _verifier(code_verifier)
        if not secrets.compare_digest(
            hashlib.sha256(exact_code.encode("ascii")).hexdigest(),
            transaction.code_digest,
        ) or not secrets.compare_digest(
            _challenge_for(verifier),
            transaction.request.code_challenge,
        ):
            raise SecretExportError("secret_export_code_invalid", status_code=403)
        consumed = SecretExportTransaction(
            transaction_id=transaction.transaction_id,
            request=transaction.request,
            request_digest=transaction.request_digest,
            csrf_token=transaction.csrf_token,
            status="consumed",
            created_at=transaction.created_at,
            expires_at=transaction.expires_at,
            required_assurance=transaction.required_assurance,
            max_evidence_age_seconds=transaction.max_evidence_age_seconds,
            max_total_value_bytes=transaction.max_total_value_bytes,
            subject=transaction.subject,
            assurance=transaction.assurance,
            approval_method=transaction.approval_method,
            approval_verified_at=transaction.approval_verified_at,
        )
        await self._replace(
            transaction,
            consumed,
            ttl_seconds=self._tombstone_ttl,
        )
        return ConsumedSecretExport(
            transaction_id=transaction.transaction_id,
            request=transaction.request,
            request_digest=transaction.request_digest,
            subject=transaction.subject,
            assurance=transaction.assurance,
            approval_method=transaction.approval_method,
            approval_verified_at=transaction.approval_verified_at,
            max_total_value_bytes=transaction.max_total_value_bytes,
        )


def secret_export_values_size(values: Sequence[str]) -> int:
    return sum(len(value.encode("utf-8")) for value in values)


__all__ = [
    "SECRET_EXPORT_ERROR_SCHEMA",
    "SECRET_EXPORT_REQUEST_SCHEMA",
    "SECRET_EXPORT_RESULT_SCHEMA",
    "SECRET_EXPORT_START_SCHEMA",
    "SECRET_EXPORT_TRANSACTION_SCHEMA",
    "ApprovedSecretExport",
    "ConsumedSecretExport",
    "RedisSecretExportStore",
    "SecretExportError",
    "SecretExportRequest",
    "SecretExportTransaction",
    "secret_export_values_size",
]

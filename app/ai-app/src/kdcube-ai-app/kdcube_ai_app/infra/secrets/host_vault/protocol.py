# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Versioned host-vault protocol.

The contract is deliberately narrow. Every request carries the authenticated
deployment identity (established by the transport, never by the body), the
canonical tenant/project namespace, the trusted logical application, the
operation, an opaque secret reference, a request id, and replay controls.
Public callers never submit an arbitrary vault path: a ``SecretReference`` is
derived or validated by trusted KDCube code from a bounded grammar.

Responses use fixed error codes. Nothing in a response carries a backend
exception, key material, a storage path, or metadata about other records.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PROTOCOL_VERSION = "kdcube-host-vault/1"
CUSTODY_SCHEMA = "kdcube.host_vault_custody.v1"
CUSTODY_GUARANTEES = frozenset({
    "atomic_generation", "restart_persistent", "encrypted",
    "process_lock", "namespace_authorized",
})

# Bounds. Values are provider credentials (tokens, app passwords, OAuth
# refresh tokens): kilobytes, never files.
MAX_VALUE_BYTES = 64 * 1024
MAX_NAME_CHARS = 1024
MAX_LIST_NAMES = 1024
MAX_REQUEST_SKEW_SECONDS = 300
REFERENCE_PREFIX = "kdv1"

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_NAME_RE = re.compile(
    rf"^[A-Za-z0-9][A-Za-z0-9._:@/-]{{0,{MAX_NAME_CHARS - 1}}}$"
)


class Operation(str, Enum):
    HEALTH = "health"
    QUALIFY = "custody.qualify"
    GET = "secret.get"
    LIST = "secret.list"
    SET = "secret.set"
    DELETE = "secret.delete"
    # Rotate is a distinct operation only because its authorization intent
    # differs from replace (a rotation must supersede an existing value); the
    # storage effect is the same atomic replace with a generation check.
    ROTATE = "secret.rotate"


MUTATING_OPERATIONS = frozenset({Operation.SET, Operation.DELETE, Operation.ROTATE})


class ErrorCode(str, Enum):
    OK = "ok"
    INVALID_REQUEST = "invalid_request"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    REPLAY_REJECTED = "replay_rejected"
    TOO_LARGE = "too_large"
    CORRUPT_RECORD = "corrupt_record"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    INTERNAL = "internal"


class VaultError(Exception):
    """A protocol-level failure with a fixed code and a caller-safe message.

    ``detail`` is for the service's own log only; it is never serialized."""

    def __init__(self, code: ErrorCode, message: str = "", *, detail: str = "") -> None:
        super().__init__(message or code.value)
        self.code = code
        self.message = message or _DEFAULT_MESSAGES[code]
        self.detail = detail


_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.OK: "ok",
    ErrorCode.INVALID_REQUEST: "The request is malformed.",
    ErrorCode.UNAUTHENTICATED: "The deployment identity was not established.",
    ErrorCode.FORBIDDEN: "The identity may not perform this operation on this reference.",
    ErrorCode.NOT_FOUND: "No committed value exists for this reference.",
    ErrorCode.CONFLICT: "The reference changed since the expected generation.",
    ErrorCode.REPLAY_REJECTED: "The request id was already used with a different request.",
    ErrorCode.TOO_LARGE: "The value exceeds the vault's bound.",
    ErrorCode.CORRUPT_RECORD: "The committed record failed integrity checks.",
    ErrorCode.BACKEND_UNAVAILABLE: "The vault storage is unavailable.",
    ErrorCode.INTERNAL: "The vault could not complete the operation.",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class SecretNamespace:
    """Canonical tenant / project / trusted application namespace.

    The application is a trusted logical namespace bound by the platform
    broker, not by the calling agent. KDCube's deployment-wide broker uses
    ``kdcube-runtime``."""

    tenant: str
    project: str
    application: str

    def __post_init__(self) -> None:
        for name, value in (("tenant", self.tenant), ("project", self.project), ("application", self.application)):
            cleaned = _clean(value)
            if not _SEGMENT_RE.fullmatch(cleaned):
                raise VaultError(ErrorCode.INVALID_REQUEST, f"{name} is not a canonical segment.")
            object.__setattr__(self, name, cleaned)

    @property
    def path(self) -> str:
        return f"{self.tenant}/{self.project}/{self.application}"

    def to_dict(self) -> dict[str, str]:
        return {"tenant": self.tenant, "project": self.project, "application": self.application}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SecretNamespace:
        return cls(
            tenant=_clean(data.get("tenant")),
            project=_clean(data.get("project")),
            application=_clean(data.get("application")),
        )


@dataclass(frozen=True)
class SecretReference:
    """An opaque locator: namespace + bounded name. Its wire form is
    ``kdv1:<tenant>/<project>/<application>/<name>``. ``digest`` is what audit
    records and errors may carry; the name itself may be a user-scoped key."""

    namespace: SecretNamespace
    name: str

    def __post_init__(self) -> None:
        name = _clean(self.name)
        if not _NAME_RE.fullmatch(name) or ".." in name:
            raise VaultError(ErrorCode.INVALID_REQUEST, "secret name is not a bounded reference.")
        object.__setattr__(self, "name", name)

    @property
    def wire(self) -> str:
        return f"{REFERENCE_PREFIX}:{self.namespace.path}/{self.name}"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.wire.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def parse(cls, wire: str) -> SecretReference:
        text = _clean(wire)
        prefix = f"{REFERENCE_PREFIX}:"
        if not text.startswith(prefix):
            raise VaultError(ErrorCode.INVALID_REQUEST, "reference prefix is unknown.")
        parts = text[len(prefix):].split("/", 3)
        if len(parts) != 4:
            raise VaultError(ErrorCode.INVALID_REQUEST, "reference is not tenant/project/application/name.")
        return cls(namespace=SecretNamespace(parts[0], parts[1], parts[2]), name=parts[3])

    @classmethod
    def derive(cls, *, namespace: SecretNamespace, internal_key: str) -> SecretReference:
        """Trusted derivation from an internal secrets-manager key. The key
        grammar of ``ISecretsManager`` (dotted paths such as
        ``users.<uid>.bundles.<bid>.secrets.<key>``) fits the name grammar; the
        namespace comes from the platform, never from the key."""
        return cls(namespace=namespace, name=_clean(internal_key))


def custody_qualification(reference: SecretReference) -> dict[str, Any]:
    """Non-secret service assertion, bound to the requested namespace/selector."""
    return {"schema": CUSTODY_SCHEMA, "namespace": reference.namespace.path,
            "reference_digest": reference.digest,
            **{name: True for name in CUSTODY_GUARANTEES}}


def valid_custody_qualification(payload: Any) -> bool:
    if (type(payload) is not dict
            or set(payload) != {"schema", "namespace", "reference_digest"} | CUSTODY_GUARANTEES
            or type(payload["schema"]) is not str or payload["schema"] != CUSTODY_SCHEMA
            or type(payload["namespace"]) is not str
            or type(payload["reference_digest"]) is not str
            or re.fullmatch(r"[a-f0-9]{24}", payload["reference_digest"]) is None
            or any(payload[name] is not True for name in CUSTODY_GUARANTEES)):
        return False
    try:
        parts = payload["namespace"].split("/")
        return len(parts) == 3 and SecretNamespace(*parts).path == payload["namespace"]
    except VaultError:
        return False


@dataclass(frozen=True)
class VaultRequest:
    """One request. ``deployment_id`` is filled by the transport from the
    verified client certificate; a body value for it is ignored."""

    operation: Operation
    reference: SecretReference | None
    request_id: str
    issued_at: float
    value: str | None = None
    expected_generation: int | None = None
    deployment_id: str = ""
    protocol: str = PROTOCOL_VERSION

    @classmethod
    def new(
        cls,
        operation: Operation,
        reference: SecretReference | None = None,
        *,
        value: str | None = None,
        expected_generation: int | None = None,
    ) -> VaultRequest:
        return cls(
            operation=operation,
            reference=reference,
            request_id=uuid.uuid4().hex,
            issued_at=time.time(),
            value=value,
            expected_generation=expected_generation,
        )

    def body_digest(self) -> str:
        """What replay detection compares: everything but request metadata.
        The value is hashed, never kept."""
        material = json.dumps(
            {
                "op": self.operation.value,
                "ref": self.reference.wire if self.reference else "",
                "value": hashlib.sha256((self.value or "").encode("utf-8")).hexdigest(),
                "gen": self.expected_generation,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_wire(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "protocol": self.protocol,
            "operation": self.operation.value,
            "request_id": self.request_id,
            "issued_at": self.issued_at,
        }
        if self.reference is not None:
            data["reference"] = self.reference.wire
        if self.value is not None:
            data["value"] = self.value
        if self.expected_generation is not None:
            data["expected_generation"] = self.expected_generation
        return data

    @classmethod
    def from_wire(cls, data: Mapping[str, Any], *, deployment_id: str) -> VaultRequest:
        if not isinstance(data, Mapping):
            raise VaultError(ErrorCode.INVALID_REQUEST, "request body must be an object.")
        allowed = {
            "protocol",
            "operation",
            "request_id",
            "issued_at",
            "reference",
            "value",
            "expected_generation",
        }
        if set(data) - allowed:
            raise VaultError(ErrorCode.INVALID_REQUEST, "request fields are invalid.")
        if data.get("protocol") != PROTOCOL_VERSION:
            raise VaultError(ErrorCode.INVALID_REQUEST, "unsupported protocol version.")
        operation_value = data.get("operation")
        if not isinstance(operation_value, str):
            raise VaultError(ErrorCode.INVALID_REQUEST, "unknown operation.")
        try:
            operation = Operation(operation_value)
        except ValueError as exc:
            raise VaultError(ErrorCode.INVALID_REQUEST, "unknown operation.") from exc
        request_id_value = data.get("request_id")
        if not isinstance(request_id_value, str):
            raise VaultError(ErrorCode.INVALID_REQUEST, "request_id is required.")
        request_id = request_id_value.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_id):
            raise VaultError(ErrorCode.INVALID_REQUEST, "request_id is required.")
        issued_at_value = data.get("issued_at")
        if isinstance(issued_at_value, bool) or not isinstance(
            issued_at_value,
            (int, float),
        ):
            raise VaultError(ErrorCode.INVALID_REQUEST, "issued_at is required.")
        try:
            issued_at = float(issued_at_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VaultError(ErrorCode.INVALID_REQUEST, "issued_at is required.") from exc
        if not math.isfinite(issued_at) or issued_at <= 0:
            raise VaultError(ErrorCode.INVALID_REQUEST, "issued_at is required.")
        reference = None
        if operation is not Operation.HEALTH:
            reference_value = data.get("reference")
            if not isinstance(reference_value, str):
                raise VaultError(ErrorCode.INVALID_REQUEST, "reference is required.")
            reference = SecretReference.parse(reference_value)
        value = data.get("value")
        if value is not None:
            if not isinstance(value, str):
                raise VaultError(ErrorCode.INVALID_REQUEST, "value must be a string.")
            if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
                raise VaultError(ErrorCode.TOO_LARGE)
        expected = data.get("expected_generation")
        if expected is not None and (
            isinstance(expected, bool)
            or not isinstance(expected, int)
            or expected < 0
        ):
            raise VaultError(
                ErrorCode.INVALID_REQUEST,
                "expected_generation must be a non-negative integer.",
            )
        if operation in (Operation.SET, Operation.ROTATE) and value is None:
            raise VaultError(ErrorCode.INVALID_REQUEST, "value is required.")
        if operation in {Operation.HEALTH, Operation.QUALIFY, Operation.GET, Operation.DELETE} and (
            value is not None
        ):
            raise VaultError(ErrorCode.INVALID_REQUEST, "operation does not accept a value.")
        if operation in {Operation.HEALTH, Operation.QUALIFY, Operation.GET} and expected is not None:
            raise VaultError(
                ErrorCode.INVALID_REQUEST,
                "operation does not accept expected_generation.",
            )
        if operation is Operation.HEALTH and "reference" in data:
            raise VaultError(ErrorCode.INVALID_REQUEST, "health does not accept a reference.")
        if operation is Operation.QUALIFY and ("value" in data or "expected_generation" in data):
            raise VaultError(ErrorCode.INVALID_REQUEST, "qualification does not mutate.")
        if operation in {Operation.LIST, Operation.QUALIFY} and (
            reference is None or not reference.name.endswith(".__keys")
        ):
            raise VaultError(
                ErrorCode.INVALID_REQUEST,
                "operation requires a secret inventory reference.",
            )
        if operation is Operation.LIST and (
            value is not None or expected is not None
        ):
            raise VaultError(ErrorCode.INVALID_REQUEST, "list does not mutate.")
        return cls(
            operation=operation,
            reference=reference,
            request_id=request_id,
            issued_at=issued_at,
            value=value,
            expected_generation=expected,
            deployment_id=_clean(deployment_id),
        )


@dataclass(frozen=True)
class VaultResponse:
    """One response. ``value`` is present only for a successful GET; ``generation``
    is the committed generation after a successful mutation or read."""

    ok: bool
    code: ErrorCode
    message: str = ""
    request_id: str = ""
    value: str | None = None
    generation: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, request: VaultRequest, *, value: str | None = None, generation: int | None = None, **extra: Any) -> VaultResponse:
        return cls(ok=True, code=ErrorCode.OK, message="ok", request_id=request.request_id, value=value, generation=generation, extra=dict(extra))

    @classmethod
    def failure(cls, error: VaultError, *, request_id: str = "") -> VaultResponse:
        return cls(ok=False, code=error.code, message=error.message, request_id=request_id)

    def to_wire(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "protocol": PROTOCOL_VERSION,
            "ok": self.ok,
            "code": self.code.value,
            "message": self.message,
            "request_id": self.request_id,
        }
        if self.value is not None:
            data["value"] = self.value
        if self.generation is not None:
            data["generation"] = self.generation
        if set(self.extra) - {"deployment_id", "names", "custody"}:
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        data.update(self.extra)
        VaultResponse.from_wire(data)
        return data

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> VaultResponse:
        if not isinstance(data, Mapping) or data.get("protocol") != PROTOCOL_VERSION:
            raise VaultError(ErrorCode.INTERNAL, "The vault answered in an unknown protocol.")
        known = {
            "protocol",
            "ok",
            "code",
            "message",
            "request_id",
            "value",
            "generation",
            "deployment_id",
            "names",
            "custody",
        }
        if set(data) - known:
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        ok = data.get("ok")
        if not isinstance(ok, bool):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        code_value = data.get("code")
        if not isinstance(code_value, str):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        try:
            code = ErrorCode(code_value)
        except ValueError as exc:
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.") from exc
        if ok != (code is ErrorCode.OK):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        message = data.get("message")
        request_id = data.get("request_id")
        if (
            not isinstance(message, str)
            or len(message) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in message)
            or not isinstance(request_id, str)
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        if request_id and not re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_id):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        value = data.get("value")
        generation = data.get("generation")
        if (
            value is not None
            and (
                not isinstance(value, str)
                or len(value.encode("utf-8")) > MAX_VALUE_BYTES
            )
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        if generation is not None and (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        if not ok and (value is not None or generation is not None):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        names = data.get("names")
        if names is not None and (
            not ok
            or not isinstance(names, list)
            or len(names) > MAX_LIST_NAMES
            or any(
                not isinstance(name, str)
                or not _NAME_RE.fullmatch(name)
                or ".." in name
                or name.endswith(".__keys")
                for name in names
            )
            or names != sorted(set(names))
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        deployment_id = data.get("deployment_id")
        if deployment_id is not None and (
            not ok
            or not isinstance(deployment_id, str)
            or not _SEGMENT_RE.fullmatch(deployment_id)
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        if "custody" in data and (
            not ok or "value" in data or "generation" in data
            or "deployment_id" in data or "names" in data
            or not valid_custody_qualification(data["custody"])
        ):
            raise VaultError(ErrorCode.INTERNAL, "The vault answer is malformed.")
        return cls(
            ok=ok,
            code=code,
            message=message,
            request_id=request_id,
            value=value,
            generation=generation,
            extra={
                key: data[key]
                for key in ("deployment_id", "names", "custody")
                if key in data
            },
        )


def sanitize_failure(exc: BaseException) -> VaultError:
    """Map any exception to a fixed-code error whose message carries nothing
    from the exception. Backend, TLS, and OS errors may embed paths, hostnames,
    key ids, or canary strings; none of that reaches the caller."""
    if isinstance(exc, VaultError):
        return exc
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return VaultError(ErrorCode.BACKEND_UNAVAILABLE, detail=type(exc).__name__)
    return VaultError(ErrorCode.INTERNAL, detail=type(exc).__name__)


__all__ = [
    "CUSTODY_GUARANTEES",
    "CUSTODY_SCHEMA",
    "MAX_LIST_NAMES",
    "MAX_NAME_CHARS",
    "MAX_REQUEST_SKEW_SECONDS",
    "MAX_VALUE_BYTES",
    "MUTATING_OPERATIONS",
    "PROTOCOL_VERSION",
    "ErrorCode",
    "Operation",
    "SecretNamespace",
    "SecretReference",
    "VaultError",
    "VaultRequest",
    "VaultResponse",
    "custody_qualification",
    "valid_custody_qualification",
    "sanitize_failure",
]

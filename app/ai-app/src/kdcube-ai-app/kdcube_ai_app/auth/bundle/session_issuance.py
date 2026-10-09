# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Immutable identities and public outcomes for recoverable session issuance.

A trusted host derives these bindings from verified actor context and its
committed immutable intent. A binding is data, not an authorization proof;
constructing one from request JSON never authorizes session issuance.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Protocol

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")


class SessionIssuanceRefused(ValueError):
    """A finite, non-secret reason for refusing an issuance operation."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _text(value: object, *, reason: str, ascii_only: bool = False) -> None:
    try:
        encoded = value.encode("utf-8") if type(value) is str else None
    except UnicodeError:
        encoded = None
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or encoded is None
        or len(encoded) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or (ascii_only and not value.isascii())
    ):
        raise SessionIssuanceRefused(reason)


def _digest(value: object, *, reason: str) -> None:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise SessionIssuanceRefused(reason)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class SessionIssuanceBinding:
    """Original trusted actor, effect, target incarnation and absolute expiry.

    Actor and initiator digests are canonical hashes supplied by the trusted
    context reader; the issuer does not reconstruct identities from user JSON.
    No bearer or secret reference belongs in this input binding.
    """

    tenant: str
    project: str
    transaction_id: str
    slot: str
    actor_digest: str
    initiator_digest: str
    effect_digest: str
    receipt_digest: str
    access_id: str
    before_card_revision: int
    before_content_hash: str | None
    after_card_revision: int
    expires_at: int

    def validated(self) -> SessionIssuanceBinding:
        for name in ("tenant", "project", "access_id"):
            _text(getattr(self, name), reason="session_issuance_identity_invalid")
        _text(self.slot, reason="session_issuance_slot_invalid", ascii_only=True)
        for name in (
            "transaction_id", "actor_digest", "initiator_digest",
            "effect_digest", "receipt_digest",
        ):
            _digest(getattr(self, name), reason="session_issuance_digest_invalid")
        if (
            type(self.before_card_revision) is not int
            or self.before_card_revision < 0
            or type(self.after_card_revision) is not int
            or self.after_card_revision <= self.before_card_revision
        ):
            raise SessionIssuanceRefused("session_issuance_incarnation_invalid")
        if self.before_content_hash is None:
            if self.before_card_revision != 0:
                raise SessionIssuanceRefused("session_issuance_incarnation_invalid")
        else:
            _digest(self.before_content_hash, reason="session_issuance_digest_invalid")
        if type(self.expires_at) is not int or self.expires_at <= 0:
            raise SessionIssuanceRefused("session_issuance_expiry_invalid")
        return self

    @property
    def identity(self) -> str:
        """One identity for the original transaction/slot in this namespace."""
        self.validated()
        encoded = _canonical([
            self.tenant, self.project, self.transaction_id, self.slot,
        ])
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()

    def to_record(self) -> dict[str, object]:
        self.validated()
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        """All immutable inputs, compared on every same-identity replay."""
        encoded = _canonical(self.to_record())
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()


class SessionIssuanceContextReader(Protocol):
    """A host-owned, authenticated committed-intent reader.

    Implementations validate decision, actor, enlistment, candidate and target
    before returning the binding. Missing/unavailable context refuses; request
    fields alone and a caller-supplied digest are never a fallback proof.
    """

    async def read(
        self, *, transaction_id: str, slot: str,
    ) -> SessionIssuanceBinding: ...


@dataclass(frozen=True)
class IssuanceContext:
    """A trusted host's validated immutable committed-intent context.

    The host derives this from its authenticated actor, validated effect and
    committed decision. This internal SDK value is not an HTTP request model
    or an authorization proof a caller may manufacture from request JSON.
    """

    tenant: str
    project: str
    transaction_id: str
    slot: str
    actor: str
    effect_digest: str
    receipt_digest: str
    access_id: str
    target_incarnation: int
    expires_at: int

    @classmethod
    def from_context(cls, context: object) -> IssuanceContext:
        if isinstance(context, dict):
            raise SessionIssuanceRefused("issuance_context_invalid")
        try:
            value = cls(**{name: getattr(context, name) for name in cls.__dataclass_fields__})
        except (AttributeError, TypeError):
            raise SessionIssuanceRefused("issuance_context_invalid") from None
        for name in ("tenant", "project", "actor", "access_id"):
            _text(getattr(value, name), reason="issuance_context_invalid")
        _text(value.slot, reason="issuance_context_invalid", ascii_only=True)
        for name in ("transaction_id", "effect_digest", "receipt_digest"):
            _digest(getattr(value, name), reason="issuance_context_invalid")
        if (type(value.target_incarnation) is not int or value.target_incarnation < 1
                or type(value.expires_at) is not int or value.expires_at <= 0):
            raise SessionIssuanceRefused("issuance_context_invalid")
        return value

    @property
    def identity(self) -> str:
        return hashlib.sha256(_canonical([
            self.tenant, self.project, self.transaction_id, self.slot,
        ]).encode("ascii")).hexdigest()

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SessionIssuanceReceipt:
    """Replay-stable public outcome; bearer values are never stored, only re-signed from claims."""

    session_id: str
    secret_ref: str
    bearer_sha256: str
    outcome: str = "issued"

    def validated(self) -> SessionIssuanceReceipt:
        _text(self.session_id, reason="session_issuance_result_invalid", ascii_only=True)
        if type(self.secret_ref) is not str or _HEX32.fullmatch(self.secret_ref) is None:
            raise SessionIssuanceRefused("session_issuance_result_invalid")
        _digest(self.bearer_sha256, reason="session_issuance_result_invalid")
        if type(self.outcome) is not str or self.outcome not in {"issued", "recovered"}:
            raise SessionIssuanceRefused("session_issuance_result_invalid")
        return self

    def to_public_dict(self) -> dict[str, str]:
        self.validated()
        return asdict(self)


# The host-facing contract and the existing internal receipt share one type.
BoundIssuance = SessionIssuanceReceipt

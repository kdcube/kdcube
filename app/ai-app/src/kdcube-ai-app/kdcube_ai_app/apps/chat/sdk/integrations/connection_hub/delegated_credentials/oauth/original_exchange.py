# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Secret-free bindings for delivery of an original authorization-code exchange.

The trusted host consumes a live code once and supplies its server-owned payload.
The binding pins that validation and the candidate input digest before minting.
A retry proof alone cannot start an exchange or authorize a Card/session. The
host reads the original Hub decision and fences its current target separately.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge

_HEX64 = re.compile(r"[0-9a-f]{64}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_PLAN_FIELDS = frozenset((
    "transaction_id", "decision_request_id", "intent_digest", "original_input_digest",
    "tenant", "project", "access_id", "grantor_subject", "client_id",
    "credential_issuer", "credential_subject", "base_revision", "candidate_revision",
    "expires_at", "card_content_hash", "operations", "resource_grants",
    "resource_operations", "delivery_deadline", "reserved_until", "slots", "effect_digests",
))


class OriginalExchangeRefused(ValueError):
    """A finite refusal whose message contains no request or credential value."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise OriginalExchangeRefused("original_exchange_binding_invalid") from None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def text(value: Any, limit: int = 256) -> None:
    if (type(value) is not str or not value or value != value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise OriginalExchangeRefused("original_exchange_binding_invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise OriginalExchangeRefused("original_exchange_binding_invalid") from None


def hex_digest(value: Any) -> None:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise OriginalExchangeRefused("original_exchange_binding_invalid")


def validate_consumed_code_payload(proof: CodeExchangeProof, payload: Mapping[str, Any]) -> str:
    """Check the live consumed code proof before any host candidate callback."""
    if type(proof) is not CodeExchangeProof or not isinstance(payload, Mapping):
        raise OriginalExchangeRefused("original_exchange_binding_invalid")
    proof.validated()
    try:
        redirect, challenge, grantor = payload["redirect_uri"], payload["code_challenge"], payload["sub"]
        text(redirect, 4096)
        text(challenge)
        text(grantor)
        if (payload["client_id"] != proof.client_id
                or not hmac.compare_digest(digest(redirect), proof.redirect_sha256)
                or not hmac.compare_digest(digest(challenge), proof.challenge_sha256)):
            raise OriginalExchangeRefused("original_exchange_validation_failed")
    except (KeyError, OriginalExchangeRefused):
        raise OriginalExchangeRefused("original_exchange_validation_failed") from None
    return grantor


@dataclass(frozen=True)
class CodeExchangeProof:
    tenant: str
    project: str
    code_sha256: str
    client_id: str
    redirect_sha256: str
    challenge_sha256: str

    @classmethod
    def from_request(cls, *, tenant: str, project: str, code: str, client_id: str,
                     redirect_uri: str, verifier: str) -> CodeExchangeProof:
        text(code, 2048)
        text(redirect_uri, 4096)
        if type(verifier) is not str or _VERIFIER.fullmatch(verifier) is None:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        return cls(tenant, project, digest(code), client_id, digest(redirect_uri),
                   digest(make_s256_challenge(verifier))).validated()

    def validated(self) -> CodeExchangeProof:
        for value in (self.tenant, self.project, self.client_id):
            text(value)
        for value in (self.code_sha256, self.redirect_sha256, self.challenge_sha256):
            hex_digest(value)
        return self

    @property
    def identity(self) -> str:
        self.validated()
        return digest(canonical(["kdcube.original-code-exchange.v1", self.tenant, self.project, self.code_sha256]))

    @property
    def fingerprint(self) -> str:
        self.validated()
        return digest(canonical(asdict(self)))


@dataclass(frozen=True)
class ValidatedCodeExchange:
    proof: CodeExchangeProof
    grantor_subject: str
    payload_digest: str
    original_input_digest: str
    decision_scope: str

    @classmethod
    def from_consumed(cls, proof: CodeExchangeProof, payload: Mapping[str, Any], *,
                      original_input_digest: str, decision_scope: str) -> ValidatedCodeExchange:
        """Only for the host's live consumed payload, never client-supplied JSON.

        The host computes original_input_digest from the exact candidate inputs
        passed to Hub begin. Payload bytes are hashed, not stored in this binding.
        """
        grantor = validate_consumed_code_payload(proof, payload)
        return cls(proof, grantor, digest(canonical(dict(payload))), original_input_digest, decision_scope).validated()

    def validated(self) -> ValidatedCodeExchange:
        if type(self.proof) is not CodeExchangeProof:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        self.proof.validated()
        text(self.grantor_subject)
        text(self.decision_scope)
        hex_digest(self.payload_digest)
        hex_digest(self.original_input_digest)
        return self

    @property
    def fingerprint(self) -> str:
        self.validated()
        return digest(canonical(asdict(self)))

    @property
    def decision_request_id(self) -> str:
        """Matches the Hub's field-tagged request identity for original_request_id=proof.identity."""
        self.validated()
        return digest(canonical({"scope": self.decision_scope, "grantor_subject": self.grantor_subject,
                                 "client_id": self.proof.client_id, "original_request_id": self.proof.identity}))


def _string_list(value: Any) -> None:
    if type(value) is not list:
        raise OriginalExchangeRefused("original_exchange_plan_invalid")
    for item in value:
        text(item)
    if len(set(value)) != len(value):
        raise OriginalExchangeRefused("original_exchange_plan_invalid")


def plan_snapshot(binding: ValidatedCodeExchange, plan: object) -> dict[str, Any]:
    """Copy an authenticated original Hub plan, not an authorization proof itself."""
    if isinstance(plan, Mapping):
        raise OriginalExchangeRefused("original_exchange_plan_invalid")
    try:
        value = json.loads(canonical(plan.to_dict()))
        if type(value) is not dict or set(value) != _PLAN_FIELDS:
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        for field in ("transaction_id", "decision_request_id", "intent_digest", "original_input_digest", "card_content_hash"):
            hex_digest(value[field])
        for field in ("tenant", "project", "access_id", "grantor_subject", "client_id", "credential_issuer", "credential_subject"):
            text(value[field])
        if any(type(value[name]) is not int or value[name] < 1 for name in
               ("candidate_revision", "expires_at", "delivery_deadline", "reserved_until")):
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        if (type(value["base_revision"]) is not int or value["base_revision"] < 0
                or value["candidate_revision"] <= value["base_revision"]):
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        if not value["reserved_until"] <= value["delivery_deadline"] <= value["expires_at"]:
            raise OriginalExchangeRefused("original_exchange_deadline_invalid")
        for name in ("operations", "slots"):
            _string_list(value[name])
        if not value["slots"] or set(value["slots"]) != set(value["effect_digests"]):
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        if not set(value["slots"]) <= {"access", "refresh"}:
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        for effect in value["effect_digests"].values():
            hex_digest(effect)
        for name in ("resource_grants", "resource_operations"):
            if type(value[name]) is not dict:
                raise OriginalExchangeRefused("original_exchange_plan_invalid")
            for key, items in value[name].items():
                text(key)
                _string_list(items)
    except (AttributeError, TypeError, KeyError, OriginalExchangeRefused) as exc:
        if isinstance(exc, OriginalExchangeRefused) and exc.reason == "original_exchange_deadline_invalid":
            raise
        raise OriginalExchangeRefused("original_exchange_plan_invalid") from None
    expected = {"tenant": binding.proof.tenant, "project": binding.proof.project,
                "client_id": binding.proof.client_id, "grantor_subject": binding.grantor_subject,
                "decision_request_id": binding.decision_request_id, "original_input_digest": binding.original_input_digest}
    if any(value[name] != original for name, original in expected.items()):
        raise OriginalExchangeRefused("original_exchange_plan_mismatch")
    return value

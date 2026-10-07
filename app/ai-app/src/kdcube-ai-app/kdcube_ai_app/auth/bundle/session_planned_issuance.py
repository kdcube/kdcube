# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Trusted-host plan and applied-result bindings for two-stage issuance.

These internal values carry facts validated by the host's authenticated
decision reader. Constructing them is not an authorization proof. The host
fences the live Card incarnation separately at its delegated boundary.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from kdcube_ai_app.auth.bundle.session_issuance import (
    SessionIssuanceRefused, _canonical, _digest, _text,
)


@dataclass(frozen=True)
class PlannedIssuanceContext:
    tenant: str
    project: str
    transaction_id: str
    slot: str
    actor: str
    client_id: str
    decision_request_id: str
    intent_digest: str
    original_input_digest: str
    effect_digest: str
    access_id: str
    target_incarnation: str
    base_revision: int
    card_revision: int
    credential_issuer: str
    credential_subject: str
    expires_at: int
    cap_expires_at: int
    delivery_deadline: int
    reserved_until: int

    @classmethod
    def from_context(cls, context: object) -> PlannedIssuanceContext:
        if isinstance(context, dict):
            raise SessionIssuanceRefused("issuance_context_invalid")
        try:
            value = cls(**{name: getattr(context, name) for name in cls.__dataclass_fields__})
        except (AttributeError, TypeError):
            raise SessionIssuanceRefused("issuance_context_invalid") from None
        for name in ("tenant", "project", "actor", "client_id", "access_id",
                     "credential_issuer", "credential_subject"):
            _text(getattr(value, name), reason="issuance_context_invalid")
        _text(value.slot, reason="issuance_context_invalid", ascii_only=True)
        for name in ("transaction_id", "decision_request_id", "intent_digest",
                     "original_input_digest", "effect_digest", "target_incarnation"):
            _digest(getattr(value, name), reason="issuance_context_invalid")
        for name in ("card_revision", "expires_at", "cap_expires_at",
                     "delivery_deadline", "reserved_until"):
            if type(getattr(value, name)) is not int or getattr(value, name) < 1:
                raise SessionIssuanceRefused("issuance_context_invalid")
        if (type(value.base_revision) is not int or value.base_revision < 0
                or value.card_revision <= value.base_revision
                or not value.reserved_until <= value.delivery_deadline <= value.cap_expires_at
                or value.expires_at > value.cap_expires_at):
            raise SessionIssuanceRefused("issuance_context_invalid")
        return value

    @classmethod
    def from_oauth_plan(cls, plan: object, *, slot: str, expires_at: int) -> PlannedIssuanceContext:
        """Adapt a host-validated original Hub plan and captured issuer expiry.

        Plan reads and live target eligibility remain the trusted host's job.
        This structural adapter never turns caller JSON into authority.
        """
        if isinstance(plan, dict):
            raise SessionIssuanceRefused("issuance_context_invalid")
        try:
            if slot not in plan.slots:
                raise SessionIssuanceRefused("issuance_context_invalid")
            value = cls(
                tenant=plan.tenant, project=plan.project, transaction_id=plan.transaction_id,
                slot=slot, actor=plan.grantor_subject, client_id=plan.client_id,
                decision_request_id=plan.decision_request_id, intent_digest=plan.intent_digest,
                original_input_digest=plan.original_input_digest, effect_digest=plan.effect_digests[slot],
                access_id=plan.access_id, target_incarnation=plan.card_content_hash,
                base_revision=plan.base_revision, card_revision=plan.candidate_revision,
                credential_issuer=plan.credential_issuer, credential_subject=plan.credential_subject,
                expires_at=expires_at, cap_expires_at=plan.expires_at,
                delivery_deadline=plan.delivery_deadline, reserved_until=plan.reserved_until,
            )
        except (AttributeError, KeyError, TypeError):
            raise SessionIssuanceRefused("issuance_context_invalid") from None
        return cls.from_context(value)

    @property
    def identity(self) -> str:
        return hashlib.sha256(_canonical([
            self.tenant, self.project, self.transaction_id, self.slot,
        ]).encode("ascii")).hexdigest()

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AppliedIssuanceContext:
    plan: PlannedIssuanceContext
    state: str
    slot_outcome: str
    receipt_digest: str
    token_sha256: str

    @classmethod
    def from_oauth_result(cls, plan: object, result: object) -> AppliedIssuanceContext:
        """Bind a host-validated original Hub result to its prepared SDK plan."""
        bound = PlannedIssuanceContext.from_context(plan)
        if isinstance(result, dict):
            raise SessionIssuanceRefused("issuance_result_invalid")
        try:
            slot = result.per_slot[bound.slot]
            if result.state != "committed" or slot.outcome != "applied":
                raise SessionIssuanceRefused("issuance_result_not_applied")
            if (result.transaction_id != bound.transaction_id or result.intent_digest != bound.intent_digest
                    or result.access_id != bound.access_id or result.card_revision != bound.card_revision
                    or result.expires_at != bound.cap_expires_at
                    or result.delivery_deadline != bound.delivery_deadline
                    or slot.effect_digest != bound.effect_digest):
                raise SessionIssuanceRefused("issuance_identity_conflict")
            value = cls(plan=bound, state=result.state, slot_outcome=slot.outcome,
                        receipt_digest=result.receipt_digest, token_sha256=slot.token_sha256)
        except (AttributeError, KeyError, TypeError):
            raise SessionIssuanceRefused("issuance_result_invalid") from None
        return cls.from_context(value)

    @classmethod
    def from_context(cls, context: object) -> AppliedIssuanceContext:
        if isinstance(context, dict):
            raise SessionIssuanceRefused("issuance_result_invalid")
        try:
            value = cls(**{name: getattr(context, name) for name in cls.__dataclass_fields__})
        except (AttributeError, TypeError):
            raise SessionIssuanceRefused("issuance_result_invalid") from None
        if value.state != "committed" or value.slot_outcome != "applied":
            raise SessionIssuanceRefused("issuance_result_not_applied")
        _digest(value.receipt_digest, reason="issuance_result_invalid")
        _digest(value.token_sha256, reason="issuance_result_invalid")
        return cls(
            plan=PlannedIssuanceContext.from_context(value.plan), state=value.state,
            slot_outcome=value.slot_outcome, receipt_digest=value.receipt_digest,
            token_sha256=value.token_sha256,
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(asdict(self)).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class TerminalIssuanceContext:
    """Trusted original terminal result, or the original delivery deadline.

    No current Card edit supplies this authority. A host must authenticate
    the original result; the structural adapter is not request authorization.
    Expiry is checked again using PostgreSQL time under the issuance lock.
    """
    plan: PlannedIssuanceContext
    state: str
    slot_outcome: str
    receipt_digest: str
    token_sha256: str

    @classmethod
    def from_context(cls, context: object) -> TerminalIssuanceContext:
        if isinstance(context, dict):
            raise SessionIssuanceRefused("issuance_result_invalid")
        try:
            value = cls(**{name: getattr(context, name) for name in cls.__dataclass_fields__})
        except (AttributeError, TypeError):
            raise SessionIssuanceRefused("issuance_result_invalid") from None
        if (type(value.state) is not str or type(value.slot_outcome) is not str
                or (value.state, value.slot_outcome) not in {
            ("aborted", "released"), ("committed", "superseded"), ("expired", "expired"),
        }):
            raise SessionIssuanceRefused("issuance_result_not_terminal")
        if value.state == "committed":
            _digest(value.receipt_digest, reason="issuance_result_invalid")
            _digest(value.token_sha256, reason="issuance_result_invalid")
        elif value.receipt_digest != "":
            raise SessionIssuanceRefused("issuance_result_invalid")
        if value.token_sha256 != "":
            _digest(value.token_sha256, reason="issuance_result_invalid")
        return cls(
            plan=PlannedIssuanceContext.from_context(value.plan),
            state=value.state, slot_outcome=value.slot_outcome,
            receipt_digest=value.receipt_digest, token_sha256=value.token_sha256,
        )

    @classmethod
    def from_oauth_result(cls, plan: object, result: object) -> TerminalIssuanceContext:
        bound = PlannedIssuanceContext.from_context(plan)
        if isinstance(result, dict):
            raise SessionIssuanceRefused("issuance_result_invalid")
        try:
            slot = result.per_slot[bound.slot]
            expected_revision = bound.card_revision if result.state == "committed" else bound.base_revision
            if (result.transaction_id != bound.transaction_id
                    or result.intent_digest != bound.intent_digest
                    or result.access_id != bound.access_id
                    or result.card_revision != expected_revision
                    or result.expires_at != bound.cap_expires_at
                    or result.delivery_deadline != bound.delivery_deadline
                    or slot.effect_digest != bound.effect_digest):
                raise SessionIssuanceRefused("issuance_identity_conflict")
            # Hub reports an absent, never-reserved slot as pending even
            # after the ONE decision has irreversibly aborted.
            outcome = ("released" if result.state == "aborted" and slot.outcome == "pending"
                       and slot.token_sha256 == "" else slot.outcome)
            value = cls(plan=bound, state=result.state, slot_outcome=outcome,
                        receipt_digest=result.receipt_digest, token_sha256=slot.token_sha256)
        except (AttributeError, KeyError, TypeError):
            raise SessionIssuanceRefused("issuance_result_invalid") from None
        # An original Hub result never reports our local delivery-window
        # expiry. A caller cannot smuggle that path through this adapter.
        if value.state == "expired":
            raise SessionIssuanceRefused("issuance_result_not_terminal")
        return cls.from_context(value)

    @classmethod
    def expired(cls, plan: object) -> TerminalIssuanceContext:
        return cls.from_context(cls(
            plan=PlannedIssuanceContext.from_context(plan), state="expired",
            slot_outcome="expired", receipt_digest="", token_sha256="",
        ))

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(asdict(self)).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class TerminalIssuanceReceipt:
    """Public retirement coordinates, not a physical provider-erasure proof."""
    identity: str
    secret_ref: str | None

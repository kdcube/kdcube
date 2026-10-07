# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Prepare an inactive original session, then activate its applied result."""
from __future__ import annotations

import hashlib
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence

from kdcube_ai_app.auth.bundle.session_bound_issuer import (
    IssuanceSecretCustody, _custody_call, _prepare_bound_session, _store_call,
)
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, TerminalIssuanceContext, TerminalIssuanceReceipt,
)


async def prepare_bound_session(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    custody: IssuanceSecretCustody,
    sign: Callable[[Mapping[str, Any]], Awaitable[str]],
) -> SessionIssuanceReceipt:
    bound = PlannedIssuanceContext.from_context(context)
    prepared = await _prepare_bound_session(
        bound, tenant=tenant, project=project, store=store, user_id=user_id,
        roles=roles, permissions=permissions, custody=custody, sign=sign, planned=True,
    )
    return SessionIssuanceReceipt(
        session_id=prepared.session_id, secret_ref=prepared.secret_ref,
        bearer_sha256=prepared.record["token_sha256"],
        outcome="issued" if prepared.created else "recovered",
    ).validated()


async def activate_prepared_bound_session(
    context: object, *, tenant: str | None, project: str | None,
    store: Any, custody: IssuanceSecretCustody,
) -> SessionIssuanceReceipt:
    applied = AppliedIssuanceContext.from_context(context)
    bound = applied.plan
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if store is None or not all(callable(getattr(store, name, None)) for name in
                                ("read_issuance", "activate_reserved")):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    if not callable(getattr(custody, "get", None)):
        raise SessionIssuanceRefused("issuance_custody_unavailable")
    original = await _store_call(store.read_issuance, bound.identity)
    if original is None:
        raise SessionIssuanceRefused("issuance_reservation_missing")
    if (original.record.get("issuance_plan") != bound.to_record()
            or original.delivery_deadline != bound.delivery_deadline
            or original.reserved_until != bound.reserved_until
            or original.expires_at != bound.expires_at
            or original.record.get("sub") != bound.credential_subject):
        raise SessionIssuanceRefused("issuance_identity_conflict")
    if original.record["token_sha256"] != applied.token_sha256:
        raise SessionIssuanceRefused("issuance_commitment_mismatch")
    if original.activation_digest is not None and original.activation_digest != applied.digest:
        raise SessionIssuanceRefused("issuance_activation_conflict")
    if bound.delivery_deadline <= int(time.time()):
        raise SessionIssuanceRefused("issuance_delivery_expired")
    # Activation only reads custody. Missing or uncertain original material
    # never authorizes signing or creating a replacement bearer.
    bearer = await _custody_call(custody.get, secret_ref=original.secret_ref)
    if type(bearer) is not str or not bearer:
        raise SessionIssuanceRefused("issuance_custody_missing")
    if hashlib.sha256(bearer.encode("utf-8")).hexdigest() != applied.token_sha256:
        raise SessionIssuanceRefused("issuance_custody_mismatch")
    active = await _store_call(
        store.activate_reserved, bound.identity,
        expected_inputs_digest=original.inputs_digest, activation_digest=applied.digest,
    )
    return SessionIssuanceReceipt(
        session_id=active.session_id, secret_ref=active.secret_ref,
        bearer_sha256=active.record["token_sha256"], outcome="recovered",
    ).validated()


async def retire_prepared_bound_session(
    context: object, *, tenant: str | None, project: str | None,
    store: Any, custody: IssuanceSecretCustody,
) -> TerminalIssuanceReceipt:
    terminal = TerminalIssuanceContext.from_context(context)
    bound = terminal.plan
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not callable(getattr(store, "retire_issuance", None)):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    if not callable(getattr(custody, "delete", None)):
        raise SessionIssuanceRefused("issuance_custody_unavailable")
    # Do not cross the provider boundary on an unknown database outcome.
    # Recovery retries the same tombstone and exact original secret reference.
    original = await _store_call(store.retire_issuance, terminal)
    if original is not None:
        await _custody_call(custody.delete, secret_ref=original.secret_ref)
    return TerminalIssuanceReceipt(
        identity=bound.identity, secret_ref=original.secret_ref if original is not None else None,
    )

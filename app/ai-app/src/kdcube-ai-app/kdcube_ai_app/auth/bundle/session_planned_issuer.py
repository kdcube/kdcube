# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Prepare an inactive original session, then activate its applied result.

No bearer is kept in custody (operator, 2026-10-09): activation and the bearer read re-sign the stored claims
and must match the stored and applied fingerprint; retirement is a PostgreSQL tombstone only.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Mapping, Sequence

from kdcube_ai_app.auth.bundle.session_bound_issuer import (
    IssuanceSecretCustody, _inputs_digest, _prepare_bound_session, _resigned, _store_call,
)
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, PreparedSessionSnapshot,
    TerminalIssuanceContext, TerminalIssuanceReceipt,
)


async def read_prepared_bound_session(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
) -> PreparedSessionSnapshot:
    """Read existing original coordinates without signing or mutation.

    The host authenticates the original plan and result separately. A missing
    reservation is a refusal, not permission to prepare. Reservation deadline
    does not limit a committed replay's read; original delivery/access expiry do.
    """
    bound = PlannedIssuanceContext.from_context(context)
    if (bound.tenant, bound.project) != (tenant, project):
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if user_id != bound.credential_subject:
        raise SessionIssuanceRefused("issuance_user_invalid")
    fingerprint = _inputs_digest(bound, user_id=user_id, roles=roles, permissions=permissions)
    if not callable(getattr(store, "read_issuance", None)):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    original = await _store_call(store.read_issuance, bound.identity)
    if original is None:
        raise SessionIssuanceRefused("issuance_reservation_missing")
    if (original.inputs_digest != fingerprint
            or original.record.get("issuance_plan") != bound.to_record()
            or original.delivery_deadline != bound.delivery_deadline
            or original.reserved_until != bound.reserved_until
            or original.expires_at != bound.expires_at
            or original.record.get("sub") != user_id
            or original.state not in {"reserved", "active"}):
        raise SessionIssuanceRefused("issuance_identity_conflict")
    now = int(time.time())
    if bound.expires_at <= now:
        raise SessionIssuanceRefused("issuance_expired")
    if bound.delivery_deadline <= now:
        raise SessionIssuanceRefused("issuance_delivery_expired")
    issued_at = original.record.get("iat")
    claims = original.record.get("claims")
    if (type(issued_at) is not int or not 0 < issued_at < bound.expires_at
            or not isinstance(claims, Mapping) or claims.get("iat") != issued_at
            or claims.get("sid") != original.session_id or claims.get("sub") != user_id
            or claims.get("exp") != bound.expires_at):
        raise SessionIssuanceRefused("issuance_record_invalid")
    receipt = SessionIssuanceReceipt(
        session_id=original.session_id, secret_ref=original.secret_ref,
        bearer_sha256=original.record.get("token_sha256"), outcome="recovered",
    ).validated()
    return PreparedSessionSnapshot(bound, receipt, issued_at)


async def prepare_bound_session(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    sign: Callable[[Mapping[str, Any]], Awaitable[str]],
    custody: IssuanceSecretCustody | None = None,
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


async def _applied_original(applied: AppliedIssuanceContext, *, tenant: str | None, project: str | None,
                            store: Any) -> Any:
    """The original reservation of one applied plan, checked against it exactly."""
    bound = applied.plan
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not callable(getattr(store, "read_issuance", None)):
        raise SessionIssuanceRefused("issuance_store_unavailable")
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
    return original


async def read_bound_session_bearer(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    sign: Callable[[Mapping[str, Any]], Awaitable[str]],
) -> str:
    """The applied original's bearer, re-signed from its stored claims (never stored, never logged).

    Delivery and replay read it here: the same claims and key give the same bearer; a changed or
    unavailable key refuses instead of yielding another one.
    """
    applied = AppliedIssuanceContext.from_context(context)
    original = await _applied_original(applied, tenant=tenant, project=project, store=store)
    if applied.plan.delivery_deadline <= int(time.time()):
        raise SessionIssuanceRefused("issuance_delivery_expired")
    return await _resigned(sign, original.record["claims"], applied.token_sha256)


async def activate_prepared_bound_session(
    context: object, *, tenant: str | None, project: str | None,
    store: Any, sign: Callable[[Mapping[str, Any]], Awaitable[str]],
    custody: IssuanceSecretCustody | None = None,
) -> SessionIssuanceReceipt:
    applied = AppliedIssuanceContext.from_context(context)
    bound = applied.plan
    if store is None or not all(callable(getattr(store, name, None)) for name in
                                ("read_issuance", "activate_reserved")):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    original = await _applied_original(applied, tenant=tenant, project=project, store=store)
    if original.activation_digest is not None and original.activation_digest != applied.digest:
        raise SessionIssuanceRefused("issuance_activation_conflict")
    if bound.delivery_deadline <= int(time.time()):
        raise SessionIssuanceRefused("issuance_delivery_expired")
    # The applied fingerprint must still be what the stored claims sign to with the current key: a
    # changed or unavailable key refuses here, before activation, and never yields another bearer.
    await _resigned(sign, original.record["claims"], applied.token_sha256)
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
    store: Any, custody: IssuanceSecretCustody | None = None,
) -> TerminalIssuanceReceipt:
    terminal = TerminalIssuanceContext.from_context(context)
    bound = terminal.plan
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not callable(getattr(store, "retire_issuance", None)):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    # The PostgreSQL tombstone is the whole retirement: no bearer was stored anywhere else.
    original = await _store_call(store.retire_issuance, terminal)
    return TerminalIssuanceReceipt(
        identity=bound.identity, secret_ref=original.secret_ref if original is not None else None,
    )

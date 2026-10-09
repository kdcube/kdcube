# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Recover one fixed session through its durable reservation, re-signing the stored claims.

Operator, 2026-10-09: "i need the stronger version now" (no issued bearer is kept in secret custody).
PostgreSQL keeps the bearer's SHA-256 and the signed claims; the bearer is deterministic over those claims
with the bundle session key, so preparation, recovery and replay re-sign exactly the stored claims and must
match the stored fingerprint, else they refuse (a changed or unavailable key never yields another bearer).
Only the signing key is a secret, read through the secrets manager.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from kdcube_ai_app.auth.bundle.session_issuance import (
    IssuanceContext,
    SessionIssuanceReceipt,
    SessionIssuanceRefused,
)


class IssuanceSecretCustody(Protocol):
    """Former bearer custody. Kept as a type for callers that still pass one; it is never used."""

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool: ...
    async def get(self, *, secret_ref: str) -> str | None: ...
    async def delete(self, *, secret_ref: str) -> None: ...


def _valid_text(value: object, *, maximum_bytes: int) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        return (len(value.encode("utf-8")) <= maximum_bytes
                and not any(ord(character) < 32 or ord(character) == 127 for character in value))
    except UnicodeError:
        return False


def _authority_values(values: Sequence[str]) -> list[str]:
    if (not isinstance(values, (list, tuple)) or len(values) > 256
            or any(not _valid_text(value, maximum_bytes=2048) for value in values)):
        raise SessionIssuanceRefused("issuance_authority_invalid")
    return sorted(set(values))


async def _store_call(call: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    try:
        return await call(*args, **kwargs)
    except SessionIssuanceRefused:
        raise
    except Exception:
        # A write may have committed even when its response was lost. A named
        # unavailable outcome does not authorize a fresh identity on retry.
        raise SessionIssuanceRefused("issuance_store_unavailable") from None


async def _signed(sign: Callable[[Mapping[str, Any]], Awaitable[str]], claims: Mapping[str, Any]) -> str:
    try:
        return await sign(claims)
    except Exception:
        raise SessionIssuanceRefused("issuance_signing_unavailable") from None


def _inputs_digest(bound: Any, *, user_id: str, roles: Sequence[str],
                   permissions: Sequence[str]) -> str:
    """One canonical input contract for preparation and read-only recovery."""
    if not _valid_text(user_id, maximum_bytes=1024):
        raise SessionIssuanceRefused("issuance_user_invalid")
    return hashlib.sha256(json.dumps({
        "context": bound.to_record(), "user_id": user_id,
        "roles": _authority_values(roles), "permissions": _authority_values(permissions),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


async def _resigned(sign: Callable[[Mapping[str, Any]], Awaitable[str]], claims: Mapping[str, Any],
                    token_sha256: str) -> str:
    """The original bearer, re-signed from its stored claims; a different result refuses."""
    bearer = await _signed(sign, claims)
    if (type(bearer) is not str or not bearer or type(token_sha256) is not str
            or not hmac.compare_digest(hashlib.sha256(bearer.encode("utf-8")).hexdigest(), token_sha256)):
        raise SessionIssuanceRefused("issuance_signing_mismatch")
    return bearer


async def _custody_call(call: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
    try:
        return await call(**kwargs)
    except SessionIssuanceRefused as exc:
        if exc.reason in {"issuance_custody_invalid", "issuance_custody_expired",
                          "issuance_custody_not_durable"}:
            raise SessionIssuanceRefused(exc.reason) from None
        raise SessionIssuanceRefused("issuance_custody_unavailable") from None
    except Exception:
        raise SessionIssuanceRefused("issuance_custody_unavailable") from None


async def issue_bound_session(
    context: object, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    sign: Callable[[Mapping[str, Any]], Awaitable[str]],
    custody: IssuanceSecretCustody | None = None,
) -> SessionIssuanceReceipt:
    bound = IssuanceContext.from_context(context)
    reservation = await _prepare_bound_session(
        bound, tenant=tenant, project=project, store=store, user_id=user_id,
        roles=roles, permissions=permissions, custody=custody, sign=sign,
    )
    active = await _store_call(store.activate_reserved, bound.identity)
    return SessionIssuanceReceipt(
        session_id=active.session_id, secret_ref=active.secret_ref,
        bearer_sha256=active.record["token_sha256"],
        outcome="issued" if reservation.created else "recovered",
    ).validated()


async def _prepare_bound_session(
    bound: Any, *, tenant: str | None, project: str | None, store: Any,
    user_id: str, roles: Sequence[str], permissions: Sequence[str],
    sign: Callable[[Mapping[str, Any]], Awaitable[str]], planned: bool = False,
    custody: IssuanceSecretCustody | None = None,
) -> Any:
    if bound.tenant != tenant or bound.project != project:
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not _valid_text(user_id, maximum_bytes=1024):
        raise SessionIssuanceRefused("issuance_user_invalid")
    granted_roles = _authority_values(roles)
    granted_permissions = _authority_values(permissions)
    fingerprint = _inputs_digest(bound, user_id=user_id, roles=granted_roles,
                                 permissions=granted_permissions)
    if (store is None or not all(callable(getattr(store, name, None)) for name in
            ("read_issuance", "reserve_issuance", "activate_reserved", "get_login_state"))):
        raise SessionIssuanceRefused("issuance_store_unavailable")

    reservation = await _store_call(store.read_issuance, bound.identity)
    if reservation is not None and reservation.inputs_digest != fingerprint:
        # This comes before user/key access and before any mutation.
        raise SessionIssuanceRefused("issuance_identity_conflict")
    if bound.expires_at <= int(time.time()):
        raise SessionIssuanceRefused("issuance_expired")
    if planned:
        if reservation is not None and (
            reservation.delivery_deadline != bound.delivery_deadline
            or reservation.reserved_until != bound.reserved_until
            or reservation.expires_at != bound.expires_at
            or reservation.record.get("issuance_plan") != bound.to_record()
        ):
            raise SessionIssuanceRefused("issuance_identity_conflict")
        if bound.delivery_deadline <= int(time.time()) or bound.reserved_until <= int(time.time()):
            raise SessionIssuanceRefused("issuance_delivery_expired")
        if user_id != bound.credential_subject:
            raise SessionIssuanceRefused("issuance_user_invalid")
        if reservation is not None:
            # Reuse the original reservation under its identity lock, checking
            # PostgreSQL time even when no new reservation is needed.
            reservation = await _store_call(
                store.reserve_issuance, bound.identity, fingerprint,
                reservation.session_id, reservation.secret_ref, reservation.expires_at,
                session_record=reservation.record, expected_version=reservation.expected_version,
                user_record=reservation.user_record,
                delivery_deadline=bound.delivery_deadline, reserved_until=bound.reserved_until,
            )
    candidate = None
    if reservation is None:
        login = await _store_call(store.get_login_state, user_id)
        version = login.version if login is not None else 1
        user_revision = login.user_revision if login is not None else 0
        if login is not None and login.user.get("disabled"):
            raise SessionIssuanceRefused("issuance_authority_moved")
        now = int(time.time())
        sid = "bsn_" + uuid.uuid4().hex
        secret_ref = uuid.uuid4().hex
        provider = "integration" if user_id.startswith("integration:") else None
        claims = {
            "schema": "kdcube.session_token.v1", "iss": "kdcube-bundle-session",
            "sid": sid, "sub": user_id, "provider": provider,
            "provider_subject": None, "ver": version, "iat": now, "exp": bound.expires_at,
        }
        candidate = await _signed(sign, claims)
        record = {
            "schema": claims["schema"], "session_id": sid, "sub": user_id,
            "provider": provider, "provider_subject": None,
            "token_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            "version": version, "active": True, "metadata": {}, "claims": claims,
            "iat": now, "exp": bound.expires_at, "max_exp": bound.expires_at,
            "last_seen": now,
        }
        if planned:
            record["issuance_plan"] = bound.to_record()
        reservation = await _store_call(
            store.reserve_issuance,
            bound.identity, fingerprint, sid, secret_ref, bound.expires_at,
            session_record=record, expected_version=version,
            expected_user_revision=user_revision,
            user_record={
                "sub": user_id, "username": user_id, "provider": provider,
                "provider_subject": None, "roles": granted_roles,
                "permissions": granted_permissions, "disabled": False,
                "created_at": now, "updated_at": now,
            },
            **({"delivery_deadline": bound.delivery_deadline,
                "reserved_until": bound.reserved_until} if planned else {}),
        )
        if reservation.session_id != sid:
            candidate = None  # a concurrent reservation owns the original

    # No custody: the original bearer is the deterministic signature over the stored claims. A restart, a
    # concurrent winner or a later read re-signs exactly that input, never new claims or an id, and must
    # match the stored fingerprint.
    if candidate is None or hashlib.sha256(candidate.encode("utf-8")).hexdigest() != reservation.record["token_sha256"]:
        candidate = await _resigned(sign, reservation.record["claims"], reservation.record["token_sha256"])

    if planned:
        # Retirement can commit concurrently. Recheck the durable no-mint record before returning
        # preparation; a terminal identity refuses, and nothing outside PostgreSQL needs cleaning up.
        await _store_call(store.read_issuance, bound.identity)
    return reservation
